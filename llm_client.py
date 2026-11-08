# coding: utf-8
import json
import urllib.error
import urllib.request

# 默认模型
ANTHROPIC_DEFAULT_MODEL = "claude-opus-5"


def _resolve(cfg):
    # 补全配置
    provider = str(cfg.get("provider") or "none").strip().lower()
    api_key = str(cfg.get("apiKey") or "").strip()
    if provider in ("", "none") or not api_key:
        return None
    if provider == "anthropic":
        return {
            "provider": "anthropic",
            "apiKey": api_key,
            "model": str(cfg.get("model") or "").strip() or ANTHROPIC_DEFAULT_MODEL,
            "baseUrl": str(cfg.get("baseUrl") or "").strip(),
            "timeout": cfg["timeout"],
        }
    if provider in ("openai", "openai-compatible", "compatible"):
        model = str(cfg.get("model") or "").strip()
        if not model:
            print("[AI] provider=openai 但没写 model，已降级为内置提示")
            return None
        return {
            "provider": "openai",
            "apiKey": api_key,
            "model": model,
            "baseUrl": str(cfg.get("baseUrl") or "").strip()
                       or "https://api.openai.com/v1",
            "timeout": cfg["timeout"],
        }
    print(f"[AI] 不认识的 provider: {provider!r}，已降级为内置提示")
    return None

def describe(config=None):
    active = _resolve(config or {})
    if not active:
        return {
            "enabled": False,
            "provider": "fallback",
            "model": None,
            "note": "未配置大模型，使用内置提示库",
        }
    return {
        "enabled": True,
        "provider": active["provider"],
        "model": active["model"],
        "note": "已接入大模型",
    }

# 各服务商调用
def _call_anthropic(active, system_prompt, user_prompt):
    try:
        import anthropic
    except ImportError:
        raise RuntimeError(
            "未安装 anthropic 包，无法使用 Claude。请先执行：pip install anthropic"
        )
    client_kwargs = {"api_key": active["apiKey"], "timeout": active["timeout"]}
    if active["baseUrl"]:
        client_kwargs["base_url"] = active["baseUrl"]
    client = anthropic.Anthropic(**client_kwargs)
    try:
        resp = client.messages.create(
            model=active["model"],
            max_tokens=1024,
            system=system_prompt,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic.RateLimitError:
        raise RuntimeError("触发限流，稍后再试")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"接口返回 {e.status_code}")
    except anthropic.APIConnectionError:
        raise RuntimeError("网络不通")
    if getattr(resp, "stop_reason", None) == "refusal":
        raise RuntimeError("模型拒绝作答")
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        raise RuntimeError("模型返回空内容")
    return text

def _call_openai(active, system_prompt, user_prompt, json_mode=False):
    url = active["baseUrl"].rstrip("/") + "/chat/completions"
    payload = {
        "model": active["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 1200 if json_mode else 200,
        "temperature": 0.3 if json_mode else 0.7,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {active['apiKey']}",
    })
    try:
        with urllib.request.urlopen(req, timeout=active["timeout"]) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        raise RuntimeError(f"接口返回 {e.code} {detail}".strip())
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络不通: {e.reason}")
    try:
        text = (payload["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("返回格式不符合预期")
    if not text:
        raise RuntimeError("模型返回空内容")
    return text

def request_completion(system_prompt, user_prompt, config=None):
    """供结构化 AI 功能复用的统一模型入口；不在此处伪装降级结果。"""
    active = _resolve(config or {})
    if not active:
        raise RuntimeError("尚未配置大模型，请先在页面的「AI 设置」中填写接口")
    if active["provider"] == "anthropic":
        text = _call_anthropic(active, system_prompt, user_prompt)
    else:
        text = _call_openai(active, system_prompt, user_prompt, json_mode=True)
    return text, active


def chat_completion(messages, tools=None, temperature=0.3, max_tokens=1200,
                    json_mode=False, config=None):
    """返回完整 message，保留 tool_calls；仅支持 OpenAI 兼容接口。"""
    active = _resolve(config or {})
    if not active:
        raise RuntimeError("尚未配置大模型，请先在页面的「AI 设置」中填写接口")
    if active["provider"] == "anthropic":
        raise RuntimeError("工具调用仅支持 OpenAI 兼容接口，请切换 provider")

    url = active["baseUrl"].rstrip("/") + "/chat/completions"
    payload = {
        "model": active["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {active['apiKey']}",
    })
    try:
        with urllib.request.urlopen(req, timeout=active["timeout"]) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        raise RuntimeError(f"接口返回 {e.code} {detail}".strip())
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络不通: {e.reason}")

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("返回格式不符合预期")
    return message, active


def chat_completion_stream(messages, temperature=0.3, max_tokens=1200,
                           json_mode=False, config=None):
    """流式调用，逐块产出文本增量。仅支持 OpenAI 兼容接口。

    调用方负责累积文本并在流结束后做结构化校验；本函数不吞异常，
    网络或接口错误直接抛出，由上层决定降级方式。
    """
    active = _resolve(config or {})
    if not active:
        raise RuntimeError("尚未配置大模型，请先在页面的「AI 设置」中填写接口")
    if active["provider"] == "anthropic":
        raise RuntimeError("流式输出仅支持 OpenAI 兼容接口，请切换 provider")

    url = active["baseUrl"].rstrip("/") + "/chat/completions"
    payload = {
        "model": active["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Authorization": f"Bearer {active['apiKey']}",
    })
    try:
        resp = urllib.request.urlopen(req, timeout=active["timeout"])
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        raise RuntimeError(f"接口返回 {e.code} {detail}".strip())
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络不通: {e.reason}")

    try:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece is None:
                # 少数网关在流式响应里仍返回完整 message，兼容一下。
                piece = (choice.get("message") or {}).get("content")
            if piece:
                yield piece
    finally:
        resp.close()
