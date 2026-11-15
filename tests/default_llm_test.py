# -*- coding: utf-8 -*-
"""验证服务端默认模型：未配置账号直接用，自己的 API 仍优先，清除后回到默认。"""
import json
import requests

BASE = "http://127.0.0.1:5001"
USER = "_defaultllm"
PWD = "test123456"
results = []


def check(name, ok, detail=""):
    results.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + (("  -> " + str(detail)[:200]) if detail else ""))


s = requests.Session()
s.trust_env = False

# 清掉上次中断的账号
if s.post(BASE + "/api/auth/login", json={"username": USER, "password": PWD}).status_code == 200:
    s.delete(BASE + "/api/auth/account", json={"password": PWD})
    s = requests.Session()
    s.trust_env = False

r = s.post(BASE + "/api/auth/register", json={"username": USER, "password": PWD})
check("注册临时账号", r.status_code in (200, 201), r.status_code)

r = s.get(BASE + "/api/ai/status").json()["llm"]
check("未配置账号也启用了模型", r.get("enabled") is True, r)
check("来源标记为 server", r.get("source") == "server", r.get("source"))
check("默认模型名正确", r.get("model") == "cn:hy3", r.get("model"))

r = s.get(BASE + "/api/ai/settings").json()["settings"]
check("自己的配置仍为空", r.get("enabled") is False and r.get("hasApiKey") is False, r)
check("面板能看到默认模型", r.get("serverDefault") is True and r.get("serverModel") == "cn:hy3", r)

payload = {"text": "今天有点累，但还算平静", "context": "daily", "persona": "gentle",
           "customPersona": "", "preferredName": "", "sleepQuality": None}
r = s.post(BASE + "/api/chat/stream", json=payload, stream=True, timeout=180)
events, buf = [], ""
for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
    if not chunk:
        continue
    buf += chunk
    while "\n\n" in buf:
        block, buf = buf.split("\n\n", 1)
        for line in block.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
final = next((e for e in events if e.get("type") == "final"), {})
result = final.get("result", {})
check("对话真正走了大模型而非规则兜底", result.get("source") in ("llm", "agent"),
      (result.get("source"), (result.get("response") or "")[:60]))
check("模型名写入结果", result.get("model") == "cn:hy3", result.get("model"))

# 自己的 API 优先：填一个连不上的本地地址
r = s.put(BASE + "/api/ai/settings",
          json={"baseUrl": "http://127.0.0.1:9/v1", "model": "test-model", "apiKey": "sk-test"})
check("保存自己的 API", r.status_code == 200, r.status_code)
r = s.get(BASE + "/api/ai/status").json()["llm"]
check("自己的 API 覆盖默认模型", r.get("source") == "own" and r.get("model") == "test-model", r)

r = s.post(BASE + "/api/chat/stream", json=payload, stream=True, timeout=180)
events, buf = [], ""
for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
    if not chunk:
        continue
    buf += chunk
    while "\n\n" in buf:
        block, buf = buf.split("\n\n", 1)
        for line in block.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
final = next((e for e in events if e.get("type") == "final"), {})
result = final.get("result", {})
check("自己的模型连不上时降级", result.get("source") == "fallback", result.get("source"))
check("降级文案仍指向自己的配置", "你配置的模型" in (result.get("response") or ""),
      (result.get("response") or "")[:60])

# 留空 key 不应把默认模型的 key 写进用户表
r = s.put(BASE + "/api/ai/settings",
          json={"baseUrl": "http://127.0.0.1:9/v1", "model": "test-model", "apiKey": ""})
check("留空 key 表示不修改", r.status_code == 200, r.text[:120])

r = s.delete(BASE + "/api/ai/settings")
check("清除自己的配置", r.status_code == 200, r.status_code)
r = s.get(BASE + "/api/ai/status").json()["llm"]
check("清除后回到默认模型", r.get("source") == "server" and r.get("model") == "cn:hy3", r)

r = s.delete(BASE + "/api/auth/account", json={"password": PWD})
check("注销临时账号", r.status_code == 200, r.status_code)

failed = [n for ok, n in results if not ok]
print("\n合计 %d 项，失败 %d 项" % (len(results), len(failed)))
for n in failed:
    print("  FAILED:", n)
