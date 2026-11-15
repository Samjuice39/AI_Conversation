# -*- coding: utf-8 -*-
"""默认模型额度：成功扣减、失败返还、超限、危机兜底、自有 API 与并发。"""
import concurrent.futures
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ASR_MODE"] = "off"
os.environ["ASR_AUTO_INSTALL"] = "0"

import app as service

results = []


def check(name, ok, detail=""):
    results.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + (("  -> " + str(detail)[:180]) if detail else ""))


def result(source="llm", model="test-default", risk="low"):
    return {
        "emotion": "焦虑", "emotionCode": 2, "intensity": 3, "confidence": 0.8,
        "summary": "测试", "response": "测试回应", "suggestion": "测试建议",
        "followUpQuestion": "后来呢？", "triggers": [], "riskLevel": risk,
        "source": source, "model": model,
    }


def chat(client, text="今天有些紧张，想聊一聊"):
    return client.post("/api/chat", json={
        "text": text, "context": "daily", "persona": "gentle",
        "customPersona": "", "preferredName": "", "sleepQuality": None,
    })


def used(client):
    return client.get("/api/ai/status").get_json()["llm"].get("quota", {}).get("used")


fd, db_path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    service.DB_PATH = db_path
    service.AGENT_MODE = False
    service.SERVER_LLM_DAILY_LIMIT = 3
    service.init_db()
    service.app.config.update(TESTING=True)
    client = service.app.test_client()
    r = client.post("/api/auth/register", json={"username": "quota-user", "password": "test123456"})
    check("注册测试账号", r.status_code in (200, 201), r.status_code)

    with patch.object(service, "get_server_llm_config", return_value={
        "provider": "openai", "apiKey": "secret", "model": "test-default",
        "baseUrl": "http://127.0.0.1:9/v1", "timeout": 1, "source": "server",
    }):
        data = client.get("/api/ai/status").get_json()["llm"]
        check("初始额度完整", data.get("quota") == {"limit": 3, "used": 0, "remaining": 3}, data)
        settings = client.get("/api/ai/settings").get_json()["settings"]
        check("设置面板返回额度", settings.get("quota", {}).get("remaining") == 3, settings)

        with patch.object(service, "run_analysis", return_value=result()):
            r = chat(client)
        check("成功对话返回正常", r.status_code == 200, r.get_json())
        check("成功对话扣一次", used(client) == 1, used(client))

        with patch.object(service, "analyze_stream", return_value=iter([("result", result("fallback", None))])):
            r = client.post("/api/chat/stream", json={
                "text": "这次模型不可用", "context": "daily", "persona": "gentle",
                "customPersona": "", "preferredName": "", "sleepQuality": None,
            })
            list(r.response)
        check("降级对话仍正常完成", r.status_code == 200)
        check("规则降级返还额度", used(client) == 1, used(client))

        with patch.object(service, "run_analysis", side_effect=RuntimeError("mock failure")):
            r = chat(client)
        check("模型异常返回 503", r.status_code == 503, r.status_code)
        check("模型异常返还额度", used(client) == 1, used(client))

        with patch.object(service, "run_analysis", return_value=result()):
            chat(client)
            chat(client)
        check("额度可累计到上限", used(client) == 3, used(client))
        r = chat(client)
        check("超限返回 429", r.status_code == 429, r.status_code)
        check("超限返回机器码与额度", r.get_json().get("code") == "QUOTA_EXCEEDED" and r.get_json().get("quota", {}).get("remaining") == 0, r.get_json())

        r = client.put("/api/ai/settings", json={
            "baseUrl": "http://127.0.0.1:9/v1", "model": "own-model", "apiKey": "own-key",
        })
        check("可切换到自己的 API", r.status_code == 200, r.get_json())
        data = client.get("/api/ai/status").get_json()["llm"]
        check("自有 API 不展示共享额度", data.get("source") == "own" and "quota" not in data, data)
        with patch.object(service, "run_analysis", return_value=result(model="own-model")):
            r = chat(client)
        check("自有 API 超限后仍可对话", r.status_code == 200, r.status_code)
        client.delete("/api/ai/settings")
        check("自有 API 不消耗共享额度", used(client) == 3, used(client))

        with patch.object(service, "run_analysis", return_value=result("fallback", None, "high")) as mocked:
            r = chat(client, "我不想活了，觉得没有希望")
        check("危机表达不被额度阻挡", r.status_code == 200, r.get_json())
        check("危机表达改用本地规则", mocked.call_args.kwargs.get("llm_config") is None, mocked.call_args)
        check("危机兜底不扣额度", used(client) == 3, used(client))

        r = client.post("/api/insights/ai")
        check("阶段回顾也受额度限制", r.status_code == 429 and r.get_json().get("code") == "QUOTA_EXCEEDED", r.get_json())

        with service.get_db() as conn:
            conn.execute("UPDATE llm_daily_usage SET used = 0")
        with patch.object(service, "generate_insights", return_value={"title": "回顾", "model": "test-default"}):
            r = client.post("/api/insights/ai")
        check("AI 回顾成功扣一次", r.status_code == 200 and used(client) == 1, (r.status_code, used(client)))
        with patch.object(service, "generate_insights", side_effect=[RuntimeError("mock failure"), {"title": "本地回顾", "model": None}]):
            r = client.post("/api/insights/ai")
        check("回顾降级返还额度", r.status_code == 200 and used(client) == 1, (r.status_code, used(client)))

        with service.get_db() as conn:
            conn.execute("UPDATE llm_daily_usage SET used = 0")
        service.SERVER_LLM_DAILY_LIMIT = 10
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            reservations = list(pool.map(service.reserve_server_quota, [1] * 20))
        check("并发预留不会突破上限", sum(reservations) == 10, sum(reservations))
        check("并发后数据库计数准确", used(client) == 10, used(client))

        with service.get_db() as conn:
            conn.execute("UPDATE llm_daily_usage SET usage_date = date('now', 'localtime', '-1 day')")
        check("跨天后自动恢复额度", client.get("/api/ai/status").get_json()["llm"]["quota"]["remaining"] == 10)
finally:
    try:
        os.remove(db_path)
    except OSError:
        pass

failed = [name for ok, name in results if not ok]
print("\n合计 %d 项，失败 %d 项" % (len(results), len(failed)))
for name in failed:
    print("  FAILED:", name)
raise SystemExit(1 if failed else 0)
