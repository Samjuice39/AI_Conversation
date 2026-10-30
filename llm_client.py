# coding: utf-8
import json
import os
import random
import urllib.error
import urllib.request

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_BASE_DIR, "llm_config.json")

# 默认模型
ANTHROPIC_DEFAULT_MODEL = "claude-opus-5"

_KNOWN_KEYS = {"provider", "apiKey", "model", "baseUrl", "timeout"}
_config_cache = None

def _load_config():
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    cfg = {
        "provider": "none",
        "apiKey": "",
        "model": "",
        "baseUrl": "",
        "timeout": 10.0,
    }
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            file_cfg = json.load(f)
        if isinstance(file_cfg, dict):
            for key, value in file_cfg.items():
                if key in _KNOWN_KEYS and value not in (None, ""):
                    cfg[key] = value
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[AI] 读取 llm_config.json 失败，忽略该文件: {e}")
    for env_key, cfg_key in (
        ("EMOTION_LLM_PROVIDER", "provider"),
        ("EMOTION_LLM_API_KEY", "apiKey"),
        ("EMOTION_LLM_MODEL", "model"),
        ("EMOTION_LLM_BASE_URL", "baseUrl"),
        ("EMOTION_LLM_TIMEOUT", "timeout"),
    ):
        value = os.environ.get(env_key)
        if value:
            cfg[cfg_key] = value
    cfg["provider"] = str(cfg.get("provider") or "none").strip().lower()
    try:
        cfg["timeout"] = float(cfg.get("timeout") or 10.0)
    except (TypeError, ValueError):
        cfg["timeout"] = 10.0
    _config_cache = cfg
    return cfg

def _resolve(cfg):
    # 补全配置
    provider = cfg["provider"]
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

def is_enabled():
    # 是否已配置
    return _resolve(_load_config()) is not None

def describe():
    active = _resolve(_load_config())
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

# 内置提示库
_FALLBACK_LINES = {
    1: [
        "孩子身体绷得比较紧，头部晃动也比平时多，可能是在硬撑。可以先暂停手上的事，让他站起来活动一下。",
        "孩子现在的身体状态偏紧张。不妨先把任务放一放，陪他做几轮慢呼吸，等动作缓下来再继续。",
        "孩子的晃动频率在上升。可以先把他带离当前任务，换个安静的位置待一会儿，再决定是否回到座位。",
    ],
    2: [
        "孩子现在动得停不下来，节奏偏快。可以先带他做几次深呼吸，把速度放慢，再进入下一项任务。",
        "孩子的活动量明显偏高。不妨先安排一个安静环节过渡，等他自己缓下来再继续。",
    ],
    3: [
        "孩子的注意力开始飘了，动作也变少了。可以让他站起来伸展一下、喝口水，再回来接着做。",
        "孩子像是有点累了。不妨先把任务拆小一点，中间插一个短休息，别让他硬撑。",
    ],
}

def _fallback(context):
    code = int(context.get("emotionCode") or 1)
    pool = _FALLBACK_LINES.get(code) or _FALLBACK_LINES[1]
    return random.choice(pool)

# 输出过滤
_BANNED_WORDS = (
    "医生", "就医", "诊断", "治疗", "病症", "生病", "病情", "症状", "医院",
    "心率", "心跳", "bpm", "BPM", "唤醒度", "传感器", "算法", "设备", "数据",
)
MAX_REPLY_CHARS = 90

def _sanitize(text):
    if not text:
        return None
    flat = " ".join(str(text).split())
    for mark in ("**", "__", "##", "`", "*", "#"):
        flat = flat.replace(mark, "")
    flat = flat.strip()
    for prefix in ("教师提示：", "教师提示", "干预提示：", "干预建议：", "干预建议",
                   "状态描述：", "状态描述", "当前状态：", "当前状态",
                   "建议教师", "建议：", "提示：", "提示教师：", "提示教师",
                   "教师可以这样干预：", "教师可以：", "可以这样干预：", "干预：",
                   "教师可以这样提示：", "可以这样提示："):
        if flat.startswith(prefix):
            flat = flat[len(prefix):].lstrip(" ：:")
            break
    # 模型反复在提示里塞台词
    quote_idx = -1
    for q in ("\u201c", "\u300c", '"'):
        pos = flat.find(q)
        if pos >= 0 and (quote_idx < 0 or pos < quote_idx):
            quote_idx = pos
    if quote_idx >= 0:
        if quote_idx < 15:
            return None
        flat = flat[:quote_idx].rstrip("，、, ：:。；;")
        if flat and flat[-1] not in "。！？":
            flat += "。"
    if not flat:
        return None
    for word in _BANNED_WORDS:
        if word in flat:
            return None
    if len(flat) > MAX_REPLY_CHARS:
        head = flat[:MAX_REPLY_CHARS]
        for mark in ("。", "！", "？", "；"):
            cut = head.rfind(mark)
            if cut >= 15:
                head = head[:cut + 1]
                break
        else:
            head = head.rstrip("，、, ") + "。"
        flat = head
    if flat and flat[-1] not in "。！？…":
        for mark in ("。", "！", "？"):
            cut = flat.rfind(mark)
            if cut >= 10:
                flat = flat[:cut + 1]
                break
        else:
            return None
    return flat or None

# Prompt
_SYSTEM_PROMPT = (
    "你为一款特殊教育辅助系统写干预提示，读者是特教教师。"
    "教师比你更懂这名孩子，也比他更会和孩子说话，所以你不写台词、不写孩子该说什么。"
    "你只写两句：孩子此刻的身体状态，和一个教师可以尝试的做法。\n"
    "示例一\n"
    "观察：该儿童当前有点紧张，身体绷得紧紧的。\n"
    "提示：孩子身体绷得紧，晃动也比平时多，可能是在硬撑。可以先暂停任务，让他站起来活动一下。\n"
    "示例二\n"
    "观察：该儿童当前累了，注意力飘走了。\n"
    "提示：孩子注意力开始飘，动作也变少了。可以让他站起来伸展一下、喝口水，再接着做。\n"
    "要求：按示例的句式和长度写，40 到 70 字，两句话。"
    "语气谦和，用「可以」「不妨」，不要用「必须」「应该」。"
    "不要加标题，不要加粗，不要解释原因。"
    "绝对不要给医学建议，不要提医生、就医、治疗、诊断、病症、生病、医院。"
)

def _user_prompt(context):
    code = int(context.get("emotionCode") or 1)
    label = {
        1: "有点紧张，身体绷得紧紧的",
        2: "太兴奋了，停不下来",
        3: "累了，注意力飘走了",
    }.get(code, "有点紧张")
    return (
        f"把下面这条观察改写成 40 到 70 字的干预提示，先写孩子此刻的状态，再写一个可以尝试的做法。"
        f"只输出提示本身，不要解释，不要写台词：\n"
        f"观察：该儿童当前{label}。"
    )

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

def request_completion(system_prompt, user_prompt):
    """供结构化 AI 功能复用的统一模型入口；不在此处伪装降级结果。"""
    active = _resolve(_load_config())
    if not active:
        raise RuntimeError("尚未配置大模型，请填写 llm_config.json")
    if active["provider"] == "anthropic":
        text = _call_anthropic(active, system_prompt, user_prompt)
    else:
        text = _call_openai(active, system_prompt, user_prompt, json_mode=True)
    return text, active


def chat_completion(messages, tools=None, temperature=0.3, max_tokens=1200,
                    json_mode=False):
    """底层对话接口，返回完整 message 对象（含 content 与 tool_calls）。

    与 request_completion 的区别：这里不做「只取 content」的裁剪，
    因为工具调用时模型返回的是 tool_calls 而不是文本，调用方需要拿到原始结构。

    仅支持 OpenAI 兼容协议；Anthropic 的工具调用协议格式不同，
    当前不在此实现，调用方应自行降级。
    """
    active = _resolve(_load_config())
    if not active:
        raise RuntimeError("尚未配置大模型，请填写 llm_config.json")
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


# 陪护话术入口
def generate_comfort(context):
    active = _resolve(_load_config())
    if not active:
        return _fallback(context), "fallback"
    system_prompt = _SYSTEM_PROMPT
    user_prompt = _user_prompt(context)
    try:
        if active["provider"] == "anthropic":
            text = _call_anthropic(active, system_prompt, user_prompt)
        else:
            text = _call_openai(active, system_prompt, user_prompt)
        text = _sanitize(text)
        if not text:
            print("[AI] 模型输出不合格（超长/台词/禁用表述），改用内置提示")
            return _fallback(context), "fallback"
        print(f"[AI] {active['provider']}/{active['model']} 生成提示：{text[:24]}…")
        return text, "llm"
    except Exception as e:
        print(f"[AI] 大模型调用失败，改用内置提示：{e}")
        return _fallback(context), "fallback"
