# -*- coding: utf-8 -*-
"""情绪星球 · 界面回归测试（无头 Chrome + CDP）。

覆盖：登录页、发送与逐字流式、反馈、阶段回顾、切页、AI 设置、话题抽屉（新建/切换/删除）、
XSS 不执行、四档分辨率无溢出、控制台错误。结束时注销测试账号。
"""
import asyncio
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9410
BASE = "http://127.0.0.1:5001"
import os
import tempfile
PROFILE = os.path.join(tempfile.gettempdir(), "emotion_planet_cdp_ui_profile")
SHOT_DIR = tempfile.gettempdir()
USER = "_uitest"
PWD = "uitest123456"

results = []


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  -> " + str(detail)[:150]) if detail else ""))


proc = subprocess.Popen([
    CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-proxy-server",
    f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}", "--no-first-run",
    "--window-size=1440,900", "about:blank",
], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def main():
    data = None
    for _ in range(80):
        try:
            data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1))
            if data:
                break
        except Exception:
            time.sleep(0.3)
    ws_url = [t for t in data if t["type"] == "page"][0]["webSocketDebuggerUrl"]

    async with websockets.connect(ws_url, max_size=200 * 1024 * 1024) as ws:
        counter = 0
        events = []
        dialog_log = []

        async def send(method, params=None):
            nonlocal counter
            counter += 1
            await ws.send(json.dumps({"id": counter, "method": method, "params": params or {}}))
            return counter

        async def cmd(method, params=None):
            ident = await send(method, params)
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("method") == "Page.javascriptDialogOpening":
                    dialog_log.append(msg["params"].get("message", "")[:40])
                    await send("Page.handleJavaScriptDialog", {"accept": True})
                    continue
                if msg.get("method"):
                    events.append(msg)
                    continue
                if msg.get("id") == ident:
                    return msg

        async def js(expr, await_promise=False):
            r = await cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                               "awaitPromise": await_promise})
            res = r.get("result", {})
            if res.get("exceptionDetails"):
                return {"__error": str(res["exceptionDetails"].get("text"))[:80]}
            return res.get("result", {}).get("value")

        async def shot(path):
            r = await cmd("Page.captureScreenshot", {"format": "png"})
            open(path, "wb").write(base64.b64decode(r["result"]["data"]))

        async def resize(width, height):
            await cmd("Emulation.setDeviceMetricsOverride",
                      {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
            await asyncio.sleep(0.8)

        async def overflow_state():
            return await js("""(()=>{const d=document.documentElement;
                return JSON.stringify({vw:innerWidth,vh:innerHeight,
                  sw:d.scrollWidth,sh:d.scrollHeight,
                  bodyScroll:d.scrollWidth>innerWidth+1,
                  track:document.getElementById('track')?getComputedStyle(document.getElementById('track')).transform:''})})()""")

        await cmd("Runtime.enable")
        await cmd("Log.enable")
        await cmd("Page.enable")
        await resize(1440, 900)

        # ---------- 登录页 ----------
        await cmd("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(2.0)
        check("登录页可打开", await js("document.title.indexOf('情绪星球')>=0"), await js("document.title"))
        check("默认是登录模式", await js("document.getElementById('submitBtn').textContent") == "进入情绪星球")
        await js("document.getElementById('registerTab').click()")
        await asyncio.sleep(0.2)
        check("切到注册模式", await js("document.getElementById('submitBtn').textContent") == "创建我的星球")
        await js("document.getElementById('username').value='x'")
        await js("document.getElementById('password').value='123'")
        await js("document.getElementById('authForm').requestSubmit()")
        await asyncio.sleep(1.2)
        status_text = await js("document.getElementById('status').textContent")
        check("前端校验/后端报错有提示", bool(status_text), status_text)

        await js(f"document.getElementById('username').value='{USER}'")
        await js(f"document.getElementById('password').value='{PWD}'")
        await js("document.getElementById('authForm').requestSubmit()")
        await asyncio.sleep(3.0)
        check("注册后进入主界面", await js("location.pathname") == "/" and await js("!!document.getElementById('messages')"),
              await js("location.pathname"))

        # ---------- 主界面初始态 ----------
        check("用户名显示", await js("document.getElementById('username').textContent") == USER,
              await js("document.getElementById('username').textContent"))
        check("模型徽标显示默认模型", "默认模型" in (await js("document.getElementById('modelBadge').textContent") or ""),
              await js("document.getElementById('modelBadge').textContent"))
        check("空状态文案", "等待你的分享" in (await js("document.getElementById('moodName').textContent") or ""))
        check("语音按钮可用", await js("document.getElementById('voice').disabled") is False,
              await js("document.getElementById('voiceStatus').textContent"))

        # ---------- 快捷短语 ----------
        await js("document.querySelectorAll('.quick button')[0].click()")
        filled = await js("document.getElementById('input').value")
        check("快捷短语填入输入框", bool(filled), filled[:20])

        # ---------- 发送 + 逐字流式 ----------
        text1 = "明天要上台做课程展示，我一直在担心忘词，晚上睡不好"
        await js(f"document.getElementById('input').value='{text1}'")
        await js("""(()=>{const el=document.getElementById('input');
            el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))})()""")
        lengths = []
        # 默认模型走 Agent 取数，正文要等取数结束才开始推，等待窗口放宽到两分钟。
        for _ in range(600):
            await asyncio.sleep(0.2)
            value = await js("""(()=>{const b=document.querySelectorAll('.message.ai');
                if(!b.length)return -1;const l=b[b.length-1];
                return l.classList.contains('loading')?-2:l.textContent.length})()""")
            if isinstance(value, int) and value > 0:
                lengths.append(value)
            if isinstance(value, int) and value > 40:
                break
        growing = len(set(lengths)) >= 2
        check("回复逐字增长（流式）", growing, lengths[:6])
        # 正文超过 40 字时流式往往还没结束，先等它收尾，否则后面的断言全在半路执行。
        for _ in range(400):
            await asyncio.sleep(0.3)
            finished = await js("""(()=>{const b=document.querySelectorAll('.message.ai');
                if(!b.length)return false;const l=b[b.length-1];
                return !l.classList.contains('loading') && !document.getElementById('send').disabled
                  && document.getElementById('send').textContent==='发送给 AI'})()""")
            if finished:
                break
        check("发送后按钮恢复", await js("document.getElementById('send').textContent") == "发送给 AI")
        check("发送后输入框清空", await js("document.getElementById('input').value") == "")
        check("左侧星球卡更新", "强度" in (await js("document.getElementById('moodName').textContent") or ""),
              await js("document.getElementById('moodName').textContent"))
        check("反馈按钮存在", (await js("document.querySelectorAll('[data-feedback]').length")) >= 2)

        # Shift+Enter 换行
        await js("document.getElementById('input').focus()")
        await js("document.getElementById('input').value='第一行'")
        await js("""(()=>{const el=document.getElementById('input');
            el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true}))})()""")
        await asyncio.sleep(0.4)
        check("Shift+Enter 不发送", await js("document.getElementById('input').value") == "第一行")

        # ---------- 再发两条，凑够阶段回顾 ----------
        for text in ["今天和室友聊了聊，感觉轻松一些", "晚上去操场走了两圈，脑子清醒了"]:
            before_count = await js("document.querySelectorAll('#messages .message').length")
            await js(f"document.getElementById('input').value='{text}'")
            await js("document.getElementById('send').click()")
            for _ in range(400):
                await asyncio.sleep(0.3)
                done = await js(f"""(()=>{{const n=document.querySelectorAll('#messages .message').length;
                    const b=document.querySelectorAll('.message.ai');
                    const last=b.length?b[b.length-1]:null;
                    return n>={before_count}+2 && last && !last.classList.contains('loading')
                      && last.textContent.length>20 && !document.getElementById('send').disabled}})()""")
                if done:
                    break
        check("三条记录都已渲染", (await js("document.querySelectorAll('#messages .message').length")) >= 6,
              await js("document.querySelectorAll('#messages .message').length"))

        # ---------- 反馈 ----------
        await js("document.querySelectorAll('[data-feedback]')[0].click()")
        await asyncio.sleep(1.2)
        check("反馈后文案替换", "反馈已记住" in (await js("document.querySelector('.feedback').textContent") or ""),
              await js("document.querySelector('.feedback').textContent"))

        # ---------- XSS ----------
        await js("""document.getElementById('input').value='<img src=x onerror="window.__xss=1">这条不该执行'""")
        await js("document.getElementById('send').click()")
        for _ in range(400):
            await asyncio.sleep(0.3)
            done = await js("""(()=>{const b=document.querySelectorAll('.message.ai');
                if(!b.length)return false;const l=b[b.length-1];
                return !l.classList.contains('loading')&&l.textContent.length>20
                  && !document.getElementById('send').disabled})()""")
            if done:
                break
        check("XSS 未被执行", await js("typeof window.__xss") == "undefined", await js("typeof window.__xss"))
        last_user = await js("""(()=>{const u=document.querySelectorAll('#messages .message.user');
            return u.length?u[u.length-1].textContent:''})()""")
        check("XSS 文本按字面显示", (last_user or "").startswith("<img"), (last_user or "")[:24])

        # ---------- 阶段回顾 ----------
        await js("document.querySelectorAll('.nav-tab')[1].click()")
        await asyncio.sleep(1.0)
        check("切到情绪轨迹", "to-journey" in (await js("document.getElementById('track').className") or ""))
        check("柱状图 7 根", await js("document.querySelectorAll('#bars .bar').length") == 7)
        check("统计总数正确", int(await js("document.getElementById('total').textContent") or 0) >= 4,
              await js("document.getElementById('total').textContent"))
        await js("document.getElementById('generateReport').click()")
        # 阶段回顾也要走一次大模型，两秒远远不够。
        for _ in range(400):
            await asyncio.sleep(0.3)
            if await js("document.getElementById('reportModal').classList.contains('open')"):
                break
        check("阶段回顾弹窗打开", await js("document.getElementById('reportModal').classList.contains('open')"))
        check("回顾有标题与正文", bool(await js("document.getElementById('reportTitle').textContent"))
              and len(await js("document.getElementById('reportOverview').textContent") or "") > 10,
              await js("document.getElementById('reportTitle').textContent"))
        await js("document.getElementById('closeReport').click()")
        await asyncio.sleep(0.4)
        check("回顾弹窗可关闭", await js("document.getElementById('reportModal').classList.contains('open')") is False)

        # ---------- 历史片段点击 ----------
        await js("document.querySelectorAll('[data-history]')[0].click()")
        await asyncio.sleep(1.0)
        check("点历史片段回到对话页", "to-journey" not in (await js("document.getElementById('track').className") or ""))

        # ---------- AI 设置 ----------
        await js("document.getElementById('openAiSettings').click()")
        await asyncio.sleep(1.2)
        check("AI 设置弹窗打开", await js("document.getElementById('aiSettingsModal').classList.contains('open')"))
        check("设置回填默认地址", (await js("document.getElementById('aiBaseUrl').value") or "").startswith("https://"),
              await js("document.getElementById('aiBaseUrl').value"))
        await js("document.getElementById('closeAiSettings').click()")
        await asyncio.sleep(0.3)

        # ---------- 话题抽屉 ----------
        await js("document.getElementById('topicToggle').click()")
        await asyncio.sleep(0.4)
        check("话题抽屉打开", await js("document.getElementById('conversationDrawer').classList.contains('open')"))
        before = await js("document.querySelectorAll('.conversation-item').length")
        await js("document.getElementById('newConversation').click()")
        await asyncio.sleep(1.2)
        after = await js("document.querySelectorAll('.conversation-item').length")
        check("新建话题后列表 +1", after == before + 1, (before, after))
        check("新话题为空", await js("document.querySelectorAll('#messages .message').length") == 1,
              await js("document.querySelectorAll('#messages .message').length"))
        check("标题切到新话题", await js("document.getElementById('currentTopic').textContent") == "新话题",
              await js("document.getElementById('currentTopic').textContent"))

        await js("document.getElementById('topicToggle').click()")
        await asyncio.sleep(0.3)
        await js("document.querySelectorAll('[data-conversation]')[1].click()")
        await asyncio.sleep(1.2)
        check("切回旧话题恢复消息", (await js("document.querySelectorAll('#messages .message').length")) >= 6,
              await js("document.querySelectorAll('#messages .message').length"))

        await js("document.getElementById('topicToggle').click()")
        await asyncio.sleep(0.3)
        await js("document.querySelectorAll('[data-delete-conversation]')[0].click()")
        await asyncio.sleep(1.5)
        check("删除话题有二次确认", len(dialog_log) >= 1, dialog_log)
        after_delete = await js("document.querySelectorAll('.conversation-item').length")
        check("删除后列表 -1", after_delete == after - 1, (after, after_delete))

        # ---------- 四档分辨率 ----------
        for width, height in [(1920, 1080), (1440, 900), (1366, 768), (1024, 720)]:
            await resize(width, height)
            state = json.loads(await overflow_state() or "{}")
            ok = (state.get("sh", 0) <= height + 1) and (state.get("sw", 0) <= width + 1)
            check(f"{width}x{height} 无滚动条/无横向溢出", ok,
                  f"scroll={state.get('sw')}x{state.get('sh')} viewport={state.get('vw')}x{state.get('vh')}")

        await resize(1440, 900)
        await shot(os.path.join(SHOT_DIR, "emotion_planet_ui_main.png"))
        await js("document.querySelectorAll('.nav-tab')[1].click()")
        await asyncio.sleep(0.8)
        await shot(os.path.join(SHOT_DIR, "emotion_planet_ui_journey.png"))

        # ---------- 控制台 ----------
        errors = []
        for ev in events:
            if ev["method"] == "Runtime.exceptionThrown":
                errors.append("EXC " + str(ev["params"]["exceptionDetails"].get("text"))[:100])
            elif ev["method"] == "Runtime.consoleAPICalled" and ev["params"]["type"] == "error":
                errors.append("CONSOLE " + str([a.get("value") for a in ev["params"]["args"]])[:100])
            elif ev["method"] == "Log.entryAdded" and ev["params"]["entry"]["level"] == "error":
                text = ev["params"]["entry"]["text"]
                if "favicon" in text or "400 (BAD REQUEST)" in text:
                    continue  # favicon 已有内联图标；400 是本轮故意提交的非法注册
                errors.append("LOG " + text[:100])
        check("控制台无脚本错误", not errors, errors)

        # ---------- 清理 ----------
        status = await js(f"""fetch('/api/auth/account',{{method:'DELETE',
            headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{password:'{PWD}'}})}}).then(r=>r.status)""", await_promise=True)
        check("测试账号已注销", status == 200, status)

    failed = [x for x in results if not x[0]]
    print("\n==== 汇总 ====")
    print(f"共 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for ok, name, detail in failed:
        print("  FAIL:", name, "->", detail)
    return 1 if failed else 0


try:
    code = asyncio.run(main())
finally:
    proc.terminate()
sys.exit(code)
