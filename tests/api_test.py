# -*- coding: utf-8 -*-
"""情绪星球 · 全接口回归测试。

覆盖：鉴权、注册登录、对话（流式/非流式/危机/非法参数）、话题 CRUD 与越权、
反馈、统计、AI 设置、语音识别、账号注销、XSS 与输入边界。
测试账号在结束时自行注销，不污染真实数据。
"""
import json
import os
import sys
import time

import requests

BASE = "http://127.0.0.1:5001"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_A = "_autotest_a"
USER_B = "_autotest_b"
PWD = "test123456"

results = []


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  -> " + str(detail)[:160]) if detail else ""))


def new_session():
    s = requests.Session()
    s.trust_env = False
    return s


def post(s, path, **kw):
    kw.setdefault("timeout", 90)
    return s.post(BASE + path, **kw)


def put(s, path, **kw):
    kw.setdefault("timeout", 60)
    return s.put(BASE + path, **kw)


def delete(s, path, **kw):
    kw.setdefault("timeout", 60)
    return s.delete(BASE + path, **kw)


def get(s, path, **kw):
    kw.setdefault("timeout", 60)
    return s.get(BASE + path, **kw)


def read_sse(response):
    events = []
    buffer = ""
    for chunk in response.iter_content(chunk_size=None, decode_unicode=True):
        if chunk is None:
            continue
        buffer += chunk
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            for line in block.split("\n"):
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
    return events


def chat_stream(session, text, **extra):
    payload = {"text": text, "context": "daily", "persona": "gentle",
               "customPersona": "", "preferredName": "", "sleepQuality": None}
    payload.update(extra)
    # 默认模型开启后，未配置账号也会走 Agent 多轮调用，单次可能超过一分钟。
    response = post(session, "/api/chat/stream", json=payload, stream=True, timeout=600)
    if response.headers.get("Content-Type", "").startswith("application/json"):
        return response.status_code, None, response.json()
    events = read_sse(response)
    return response.status_code, events, None


def purge_old_accounts():
    """上一次中断的测试可能留下账号，先清掉。"""
    for name in (USER_A, USER_B):
        s = new_session()
        if post(s, "/api/auth/login", json={"username": name, "password": PWD}).status_code == 200:
            delete(s, "/api/auth/account", json={"password": PWD})


def main():
    purge_old_accounts()

    # ---------- 0. 匿名访问 ----------
    anon = new_session()
    r = get(anon, "/")
    check("匿名访问首页返回登录页", r.status_code == 200 and "登录" in r.text, r.status_code)
    check("匿名访问 /api/history 返回 401", get(anon, "/api/history").status_code == 401)
    check("匿名发消息返回 401", post(anon, "/api/chat/stream", json={"text": "你好"}).status_code == 401)
    check("匿名读 AI 设置返回 401", get(anon, "/api/ai/settings").status_code == 401)
    r = get(anon, "/web/dist/main.js")
    check("编译产物可被页面加载", r.status_code == 200 and "fetch" in r.text, r.status_code)

    # ---------- 1. 注册与登录 ----------
    s = new_session()
    r = post(s, "/api/auth/register", json={"username": "x", "password": PWD})
    check("用户名过短被拒", r.status_code == 400, r.json().get("error"))
    r = post(s, "/api/auth/register", json={"username": USER_A, "password": "12345"})
    check("密码过短被拒", r.status_code == 400, r.json().get("error"))
    r = post(s, "/api/auth/register", json={"username": USER_A, "password": PWD})
    check("注册成功", r.status_code == 200 and r.json().get("ok"), r.text[:80])
    r = post(s, "/api/auth/register", json={"username": USER_A, "password": PWD})
    check("重复注册被拒（409）", r.status_code == 409, r.json().get("error"))
    r = post(s, "/api/auth/login", json={"username": USER_A, "password": "wrong-pass"})
    check("错误密码登录被拒", r.status_code in (400, 401), r.status_code)
    r = post(s, "/api/auth/login", json={"username": USER_A, "password": PWD})
    check("登录成功", r.status_code == 200 and r.json().get("ok"))
    r = get(s, "/api/auth/me")
    check("/api/auth/me 返回当前账号", r.status_code == 200 and r.json().get("username") == USER_A,
          r.json().get("username"))

    # ---------- 2. 对话 ----------
    code, events, body = chat_stream(s, "啊")
    check("1 字文本被拒", code == 400 and body and "2～1000" in body.get("error", ""), code)
    code, events, body = chat_stream(s, "啊" * 1001)
    check("超长文本被拒", code == 400, code)
    code, events, body = chat_stream(s, "明天要上台做课程展示，我一直在担心忘词，晚上睡不好")
    check("流式对话返回 200", code == 200, code)
    kinds = [e.get("type") for e in (events or [])]
    check("事件类型合法", set(kinds) <= {"status", "trace", "token", "final", "error"}, set(kinds))
    check("含 status 事件", "status" in kinds)
    check("含多个 token（逐字推流）", kinds.count("token") >= 5, kinds.count("token"))
    check("含 final 事件", "final" in kinds)
    final = next((e for e in events if e.get("type") == "final"), None)
    result = (final or {}).get("result", {})
    required = ["id", "emotion", "emotionCode", "intensity", "confidence", "summary",
                "response", "suggestion", "riskLevel", "source", "conversationId", "trace"]
    missing = [k for k in required if k not in result]
    check("final 结果字段齐全", not missing, missing)
    tokens = "".join(e["text"] for e in events if e.get("type") == "token")
    check("token 拼接与最终回应一致", tokens.strip() == (result.get("response") or "").strip(),
          (len(tokens), len(result.get("response") or "")))
    check("情绪码在 0-5", result.get("emotionCode") in range(6), result.get("emotionCode"))
    check("强度在 1-5", result.get("intensity") in range(1, 6), result.get("intensity"))
    # 配置了服务端默认模型时，未填写 API 的账号走大模型（llm/agent），否则是规则模式。
    check("来源标记与实际路径一致", result.get("source") in ("rule", "fallback", "llm", "agent"),
          result.get("source"))
    conv_id = result.get("conversationId")

    code, events, body = chat_stream(s, "我真的撑不下去了，不想活了")
    final = next((e for e in events or [] if e.get("type") == "final"), None) or {}
    crisis = final.get("result", {})
    check("危机文本提升风险等级", crisis.get("riskLevel") == "high", crisis.get("riskLevel"))
    check("危机文本给出安全建议", any(k in (crisis.get("suggestion") or "") for k in ("110", "120", "监护")),
          (crisis.get("suggestion") or "")[:60])

    r = post(s, "/api/chat", json={"text": "今天有点累，但还算平静", "context": "daily",
                                  "persona": "gentle", "customPersona": "",
                                  "preferredName": "", "sleepQuality": 3})
    check("非流式接口可用", r.status_code == 200 and r.json().get("ok"), r.status_code)

    # ---------- 3. 话题 ----------
    r = get(s, "/api/conversations")
    convs = r.json().get("conversations", [])
    check("话题列表非空", r.status_code == 200 and len(convs) >= 1, len(convs))
    check("话题含预览文本", all("preview" in c for c in convs))

    r = post(s, "/api/conversations")
    new_conv = r.json().get("conversation", {})
    check("新建话题成功", r.status_code == 200 and new_conv.get("title") == "新话题", new_conv.get("title"))

    code, events, body = chat_stream(s, "换个话题聊聊今天的午饭", conversationId=new_conv["id"])
    final = next((e for e in events or [] if e.get("type") == "final"), None) or {}
    check("指定话题发消息落到该话题", final.get("result", {}).get("conversationId") == new_conv["id"],
          final.get("result", {}).get("conversationId"))
    r = get(s, f"/api/conversations/{new_conv['id']}/messages")
    check("该话题消息可读", r.status_code == 200 and len(r.json().get("records", [])) == 1)

    r = get(s, "/api/conversations/999999/messages")
    check("不存在的话题返回 404", r.status_code == 404, r.status_code)
    r = delete(s, "/api/conversations/999999")
    check("删除不存在的话题返回 404", r.status_code == 404, r.status_code)

    # 越权：B 访问 A 的话题
    sb = new_session()
    post(sb, "/api/auth/register", json={"username": USER_B, "password": PWD})
    post(sb, "/api/auth/login", json={"username": USER_B, "password": PWD})
    r = get(sb, f"/api/conversations/{conv_id}/messages")
    check("跨用户读话题被拒（404）", r.status_code == 404, r.status_code)
    code, events, body = chat_stream(sb, "越权测试消息", conversationId=conv_id)
    check("跨用户写话题被拒（404）", code == 404, code)
    r = get(sb, "/api/history")
    check("跨用户看不到他人记录", r.status_code == 200 and r.json().get("records") == [])

    r = delete(s, f"/api/conversations/{new_conv['id']}")
    check("删除话题成功", r.status_code == 200)
    check("话题删除后消息一并清除", get(s, f"/api/conversations/{new_conv['id']}/messages").status_code == 404)

    # ---------- 4. 反馈 ----------
    r = get(s, "/api/history")
    records = r.json().get("records", [])
    target = records[0]["id"] if records else None
    r = post(s, f"/api/checkins/{target}/feedback", json={"feedback": "helpful"})
    check("提交反馈成功", r.status_code == 200 and r.json().get("ok"), r.text[:80])
    r = post(s, f"/api/checkins/{target}/feedback", json={"feedback": "maybe"})
    check("非法反馈值被拒", r.status_code == 400, r.status_code)
    r = post(sb, f"/api/checkins/{target}/feedback", json={"feedback": "helpful"})
    check("对他人记录反馈被拒（404）", r.status_code == 404, r.status_code)
    r = post(s, "/api/checkins/999999/feedback", json={"feedback": "helpful"})
    check("不存在的记录反馈返回 404", r.status_code == 404, r.status_code)

    # ---------- 5. 统计 ----------
    r = get(s, "/api/insights")
    stats = r.json().get("stats", {})
    check("统计接口可用", r.status_code == 200 and stats.get("total", 0) >= 3, stats.get("total"))
    check("统计含分布与 7 天序列", "distribution" in stats and "daily" in stats)
    check("有帮助率已计算", isinstance(stats.get("helpfulRate"), float), stats.get("helpfulRate"))
    r = post(sb, "/api/insights/ai")
    check("记录不足时拒绝生成回顾", r.status_code == 400 and "3" in r.json().get("error", ""),
          r.json().get("error"))

    r = post(s, "/api/insights/ai")
    report = r.json().get("report", {}) if r.status_code == 200 else {}
    check("记录足够时可生成阶段回顾", r.status_code == 200 and all(
        report.get(k) for k in ("title", "overview", "patterns", "nextStep")), r.text[:80])

    # ---------- 6. AI 设置 ----------
    r = get(s, "/api/ai/settings")
    check("默认未配置 API", r.json().get("settings", {}).get("enabled") is False)
    r = put(s, "/api/ai/settings",
            json={"baseUrl": "http://api.example.com/v1", "model": "x", "apiKey": "sk-1"})
    check("远程地址必须 HTTPS", r.status_code == 400, r.json().get("error"))
    r = put(s, "/api/ai/settings",
            json={"baseUrl": "https://api.deepseek.com/v1", "model": "deepseek-chat", "apiKey": ""})
    check("首次配置必须有 Key", r.status_code == 400, r.json().get("error"))
    r = put(s, "/api/ai/settings",
            json={"baseUrl": "http://127.0.0.1:9/v1", "model": "test-model", "apiKey": "sk-test"})
    check("本地地址允许 HTTP 并保存成功", r.status_code == 200 and r.json().get("settings", {}).get("enabled"),
          r.text[:80])
    r = get(s, "/api/ai/status")
    check("状态反映已配置", r.json().get("llm", {}).get("enabled") is True
          and r.json().get("llm", {}).get("model") == "test-model", r.json().get("llm"))
    code, events, body = chat_stream(s, "模型连不上时会怎样")
    final = next((e for e in events or [] if e.get("type") == "final"), None) or {}
    degraded = final.get("result", {})
    check("模型不可达时优雅降级", code == 200 and degraded.get("source") == "fallback",
          (code, degraded.get("source")))
    check("降级说明指向真实原因", "没能连上你配置的模型" in (degraded.get("response") or ""),
          (degraded.get("response") or "")[:40])
    r = delete(s, "/api/ai/settings")
    check("清除 API 配置成功", r.status_code == 200 and get(s, "/api/ai/settings").json()["settings"]["enabled"] is False)

    # ---------- 7. 语音 ----------
    r = get(s, "/api/asr/status")
    asr = r.json().get("asr", {})
    check("语音状态可读且引擎正确", r.status_code == 200 and asr.get("engine") == "SenseVoice-Small",
          (asr.get("engine"), asr.get("reason")))
    check("本地语音可用", asr.get("available") is True, asr.get("reason"))
    wav_path = os.path.join(ROOT, "samples", "asr_example_zh.wav")
    t0 = time.time()
    with open(wav_path, "rb") as fh:
        r = post(s, "/api/asr", files={"audio": ("sample.wav", fh, "audio/wav")}, data={"language": "auto"})
    elapsed = time.time() - t0
    text = (r.json() or {}).get("text", "")
    check("语音识别返回文本", r.status_code == 200 and bool(text.strip()), f"{elapsed:.1f}s {text[:30]}")
    r = post(s, "/api/asr", data={"language": "auto"})
    check("无音频文件被拒", r.status_code == 400, r.status_code)
    r = post(s, "/api/asr", files={"audio": ("x.wav", b"not-audio", "audio/wav")})
    check("非音频内容被拒且不崩溃", r.status_code >= 400 and r.headers.get("Content-Type", "").startswith("application/json"),
          r.status_code)

    # ---------- 8. XSS 与输入边界 ----------
    payload = "<img src=x onerror=alert(1)>测试转义"
    code, events, body = chat_stream(s, payload)
    final = next((e for e in events or [] if e.get("type") == "final"), None) or {}
    r = get(s, "/api/history")
    stored = next((x for x in r.json().get("records", []) if x["id"] == final.get("result", {}).get("id")), {})
    check("原文按字面保存（前端负责转义）", stored.get("text") == payload, stored.get("text"))
    r = post(s, "/api/chat/stream", json={"text": "正常文本", "context": "bad", "persona": "gentle"})
    check("未知情境被拒", r.status_code == 400, r.json().get("error"))
    r = post(s, "/api/chat/stream", json={"text": "正常文本", "context": "daily", "persona": "bad"})
    check("未知人设被拒", r.status_code == 400, r.json().get("error"))
    r = post(s, "/api/chat/stream", json={"text": "正常文本", "context": "daily",
                                          "persona": "gentle", "sleepQuality": 9})
    check("非法睡眠质量被拒", r.status_code == 400, r.json().get("error"))

    # ---------- 9. 注销账号 ----------
    r = delete(s, "/api/auth/account", json={"password": "wrong-pass"})
    check("注销需要正确密码（403）", r.status_code == 403, r.status_code)
    r = delete(s, "/api/auth/account", json={"password": PWD})
    check("注销成功", r.status_code == 200 and r.json().get("ok"), r.text[:80])
    r = get(s, "/api/auth/me")
    check("注销后 /me 返回空账号", r.status_code == 200 and r.json().get("username") is None, r.text[:60])
    check("注销后业务接口返回 401", get(s, "/api/history").status_code == 401)
    r = post(s, "/api/auth/login", json={"username": USER_A, "password": PWD})
    check("注销后账号不可登录", r.status_code in (400, 401), r.status_code)

    # 清理 B
    delete(sb, "/api/auth/account", json={"password": PWD})

    # ---------- 汇总 ----------
    failed = [x for x in results if not x[0]]
    print("\n==== 汇总 ====")
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for ok, name, detail in failed:
        print("  FAIL:", name, "->", detail)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
