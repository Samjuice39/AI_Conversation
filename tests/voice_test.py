# -*- coding: utf-8 -*-
"""语音链路端到端测试：用 Chrome 的假麦克风播放样例 WAV，验证「按住说话 → 识别 → 填词」。"""
import asyncio
import base64
import json
import subprocess
import sys
import time
import urllib.request

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9411
BASE = "http://127.0.0.1:5001"
import os
import tempfile
PROFILE = os.path.join(tempfile.gettempdir(), "emotion_planet_cdp_voice_profile")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAV = os.path.join(ROOT, "samples", "asr_example_zh.wav")
SHOT_DIR = tempfile.gettempdir()
USER = "_voicetest"
PWD = "voicetest123456"

results = []


def check(name, ok, detail=""):
    results.append((ok, name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  -> " + str(detail)[:150]) if detail else ""))


proc = subprocess.Popen([
    CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-proxy-server",
    "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    f"--use-file-for-fake-audio-capture={WAV}",
    "--autoplay-policy=no-user-gesture-required",
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

        async def cmd(method, params=None):
            nonlocal counter
            counter += 1
            await ws.send(json.dumps({"id": counter, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("method"):
                    events.append(msg)
                    continue
                if msg.get("id") == counter:
                    return msg

        async def js(expr, await_promise=False):
            r = await cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                               "awaitPromise": await_promise})
            res = r.get("result", {})
            if res.get("exceptionDetails"):
                return {"__error": str(res["exceptionDetails"].get("text"))[:80]}
            return res.get("result", {}).get("value")

        await cmd("Runtime.enable")
        await cmd("Page.enable")
        await cmd("Emulation.setDeviceMetricsOverride",
                  {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})

        # 登录（先清理残留账号）
        await cmd("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(2.0)
        await js(f"""fetch('/api/auth/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{username:'{USER}',password:'{PWD}'}})}}).then(r=>r.status)""",
                 await_promise=True)
        await js(f"""fetch('/api/auth/account',{{method:'DELETE',headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{password:'{PWD}'}})}}).then(r=>r.status)""", await_promise=True)
        await cmd("Page.navigate", {"url": BASE + "/"})
        await asyncio.sleep(1.5)
        await js(f"document.getElementById('username').value='{USER}'")
        await js(f"document.getElementById('password').value='{PWD}'")
        await js("document.getElementById('registerTab').click()")
        await asyncio.sleep(0.2)
        await js("document.getElementById('authForm').requestSubmit()")
        await asyncio.sleep(3.0)
        check("测试账号进入主界面", await js("!!document.getElementById('voice')"),
              await js("location.pathname"))

        # 等本地语音状态就绪
        status = ""
        for _ in range(30):
            await asyncio.sleep(0.5)
            status = await js("document.getElementById('voiceStatus').textContent") or ""
            if "就绪" in status:
                break
        check("语音状态提示本地就绪", "本地识别已就绪" in status, status)
        check("语音按钮文案为按住说话", await js("document.getElementById('voice').textContent") == "按住说话",
              await js("document.getElementById('voice').textContent"))

        # 按住 4 秒再松开
        await js("""(()=>{const b=document.getElementById('voice');
            b.dispatchEvent(new PointerEvent('pointerdown',{pointerId:1,pointerType:'mouse',button:0,bubbles:true,cancelable:true}))})()""")
        await asyncio.sleep(1.0)
        during = await js("document.getElementById('voiceStatus').textContent")
        check("按下后进入录音状态", "录音" in (during or ""), during)
        await asyncio.sleep(3.5)
        await js("""(()=>{const b=document.getElementById('voice');
            b.dispatchEvent(new PointerEvent('pointerup',{pointerId:1,pointerType:'mouse',button:0,bubbles:true,cancelable:true}))})()""")

        text = ""
        final_status = ""
        for _ in range(80):
            await asyncio.sleep(0.5)
            text = await js("document.getElementById('input').value") or ""
            final_status = await js("document.getElementById('voiceStatus').textContent") or ""
            if text.strip() or "失败" in final_status:
                break
        check("松开后完成识别并填入输入框", bool(text.strip()), text[:40])
        check("识别结果与样例一致", "欢迎" in text or "语音识别" in text or "达摩院" in text, text[:40])
        check("状态提示识别完成", "识别完成" in final_status, final_status)

        # 录音按钮在识别后恢复
        check("按钮恢复为按住说话", await js("document.getElementById('voice').textContent") == "按住说话",
              await js("document.getElementById('voice').textContent"))
        check("按钮无 recording 残留", await js("document.getElementById('voice').classList.contains('recording')") is False)

        # 用识别出的文字直接发送
        await js("document.getElementById('send').click()")
        for _ in range(40):
            await asyncio.sleep(0.4)
            done = await js("""(()=>{const b=document.querySelectorAll('.message.ai');
                if(!b.length)return false;const l=b[b.length-1];
                return !l.classList.contains('loading')&&l.textContent.length>20})()""")
            if done:
                break
        check("语音转写内容可正常发送并收到回应",
              (await js("document.querySelectorAll('#messages .message').length")) >= 3,
              await js("document.querySelectorAll('#messages .message').length"))

        r = await cmd("Page.captureScreenshot", {"format": "png"})
        open(os.path.join(SHOT_DIR, "emotion_planet_ui_voice.png"), "wb").write(base64.b64decode(r["result"]["data"]))

        errors = []
        for ev in events:
            if ev["method"] == "Runtime.exceptionThrown":
                errors.append(str(ev["params"]["exceptionDetails"].get("text"))[:120])
            elif ev["method"] == "Runtime.consoleAPICalled" and ev["params"]["type"] == "error":
                errors.append(str([a.get("value") for a in ev["params"]["args"]])[:120])
        check("语音流程无脚本错误", not errors, errors)

        await js(f"""fetch('/api/auth/account',{{method:'DELETE',headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{password:'{PWD}'}})}}).then(r=>r.status)""", await_promise=True)

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
