"""纯软件 AI 情绪 Agent：多轮对话、情绪理解、长期记忆与干预规划。"""
import json
import re

import rag_retriever
from llm_client import describe, request_completion

LABELS = {0: "平静", 1: "焦虑", 2: "愉悦", 3: "疲惫", 4: "低落", 5: "烦躁"}
CRISIS_PATTERN = re.compile(r"自杀|不想活|结束生命|伤害自己|自残|轻生")

# 粗判情绪码，只用于检索时给知识库加权，不参与最终输出。
# 真正的情绪判断仍由模型给出，这里只是一个检索线索。
_EMOTION_KEYWORDS = {
    1: ("紧张", "焦虑", "担心", "慌", "害怕", "压力", "怕", "不安", "睡不着"),
    5: ("烦", "生气", "火", "吵", "受不了", "怒", "暴躁", "崩溃"),
    3: ("累", "困", "没精神", "疲惫", "睡不好", "没睡好", "睡不够",
        "没劲", "转不动", "提不起", "没力气"),
    4: ("难过", "失望", "委屈", "低落", "想哭", "孤独", "空虚", "麻木", "没意思"),
    2: ("开心", "高兴", "完成", "好消息", "兴奋", "棒", "顺利", "终于"),
}


def _guess_emotion(text):
    """按关键词粗判情绪码，命中最多者优先；判不出返回 None。"""
    hits = {}
    for code, words in _EMOTION_KEYWORDS.items():
        n = sum(1 for w in words if w in text)
        if n:
            hits[code] = n
    return max(hits, key=hits.get) if hits else None

_SYSTEM_PROMPT = """你是情绪支持 Agent。结合当前消息、情境、睡眠和近期对话，理解情绪并给出支持。
只输出一个完整合法 JSON，禁止 Markdown 和任何额外文字。字段：
emotionCode(0平静/1焦虑/2愉悦/3疲惫/4低落/5烦躁)、intensity(1-5整数)、confidence(0-1)、summary、response、evidence(数组)、triggers(数组)、suggestion、followUpQuestion、riskLevel(low/medium/high)。
response 像一位可信赖、有耐心的陪伴者，说自然口语，不像客服或心理咨询话术。不要机械复述，不说教，不灌鸡汤，不夸大理解，不滥用“我理解”“听起来”“这很正常”“你愿意吗”等套话，不用表情符号。
不要固定使用“共情+建议+提问”结构。用户只是想倾诉或分享开心时，先自然接住话题；没有合适建议时 suggestion 留空，没有值得追问时 followUpQuestion 留空。确需建议时只给一个简单动作，确需了解时再问一个自然的问题。
不诊断，不编造经历，不提及未提供的硬件数据。明确自伤或轻生风险时 riskLevel=high。
若输入里带「可参考的干预思路」，只在合适时自然融入其中一条的做法，不要逐条列举、不要照搬措辞、不要为了用上而硬塞；与当前处境不符就忽略。
若输入里带「已知事实」，那是工具查到的真实记录。可以引用其中的具体时间、情绪或触发因素来支撑你的判断，但不要罗列成清单，也不要把它当成必须提起的内容。"""

_STYLE_GUIDE = """表达风格：像亲近、热烈、可靠的陪伴者，坚定站在用户一边。先接住感受，多肯定用户付出的努力和已有的能力，让对方感到“有人陪我、有人相信我”。
语气自然活泼，可适量使用“呀、啦、哒、哈、~”，偶尔用“我一直在”“辛苦了”“我相信你”。可以适度撒娇式安慰，但不要每句都卖萌。
建议融进陪伴里，用“可以、如果你想”提供选择，不用命令式条目。重要情绪下可以写2—5句，别只回一句客套话。
不要变成客服，不要说教，不要冷冰冰地复述。不要每次都用相同结尾或固定句式。只能依据提供的历史肯定具体努力，不编造共同经历，不绝对保证未来结果。爱心最多一个且只在合适时。"""

_STYLE_EXAMPLES = [
    (r"只想.*说说|不是.*办法|找个人.*说", "今天事情全堆在一起了，我有点撑不住。我不是要你给办法，就想找个人说说。",
     "好，我就在这里陪着你聊。太多事压着肯定会喘不过气的吧，辛苦了。我们不用急着解决，想从哪件说起都可以，一件件慢慢来，我一直陪着你。", 1),
    (r"开心|完成.*作品|特别棒|好消息", "我终于把准备很久的作品做完了，特别开心！",
     "这中间一定做了很多努力吧，实在是太棒了！接下来就静候佳音啦，现在可以稍微放松一下啦~", 2),
    (r"紧张|忘词|上台|展示", "明天要上台展示，我现在很紧张，总担心忘词。",
     "如果准备还不够充分，可以先把每个模块串起来，理解它们分别在讲什么。如果你已经很努力了，只是担心发挥，那先别急着否定自己呀。你的努力不是白费的，我也愿意相信你。复习一会儿就早点休息，太紧张的话我一直在这里陪着你。", 1),
    (r"结果.*不好|努力.*结果|没劲|失望|难过", "我已经很努力了，但结果还是不好，感觉有点没劲。",
     "结果配不上努力的时候，那种无力感真的很难受。但这一次结果不会把你之前的努力抹掉，你在我这里依然是很棒的。如果你想再完善一点，可以告诉我你做到了哪一步；如果现在只想难过一会儿，也没关系，我陪着你。", 4),
    (r"噪音|很烦|烦躁|生气", "旁边一直有人说话，我已经忍很久了，现在特别烦。",
     "我懂！一直被打扰真的很烦，忍了这么久辛苦你了。可以的话我们先换个安静点的位置；不方便的话，也可以想一种你觉得舒服的提醒方式。我们不气不气哈。", 5),
    (r"没睡好|很累|疲惫|转不动", "昨晚没睡好，今天什么都不想做，脑子也转不动。",
     "哎哟心疼你，现在肯定很累吧。不用着急逼自己做什么，今天可以先给自己一点休息，散散步、晒晒太阳，恢复一点力气再说哦。", 3),
    (r"心情不好|不开心", "我今天心情不太好。",
     "是发生什么了吗？如果你现在不想说也没关系，我会在这里陪着你。需要我陪你聊一会儿，还是帮你做点什么，都可以直接告诉我哦~", 0),
]

_REPORT_PROMPT = """你是情绪支持产品的 AI 总结助手。根据给定的匿名统计和近期记录生成阶段回顾。
只输出合法 JSON：{"title":"简短标题","overview":"不超过120字的趋势解释","patterns":["最多3条有价值的模式"],"nextStep":"一个可执行的下一步"}。
不得诊断，不得把相关关系说成因果关系，不得编造记录中不存在的事件。样本少时必须明确局限性。"""


def _extract_json(text):
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.I)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", candidate)
        if not match:
            raise ValueError("模型没有返回 JSON")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("模型返回的不是 JSON 对象")
    return value


def _string_list(value, limit=3):
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:100] for item in value[:limit] if str(item).strip()]


def _crisis_suggestion():
    return "请立即陪伴当事人并联系可信赖的监护人和学校心理支持人员；若有即时危险，请拨打 110 或 120。不要让当事人独处。"


def _validate(value, local_crisis=False):
    code = int(value.get("emotionCode"))
    if code not in LABELS:
        raise ValueError("emotionCode 超出范围")
    intensity = int(value.get("intensity"))
    confidence = float(value.get("confidence"))
    if not 1 <= intensity <= 5 or not 0 <= confidence <= 1:
        raise ValueError("强度或置信度超出范围")
    risk = str(value.get("riskLevel", "low")).lower()
    if risk not in ("low", "medium", "high"):
        raise ValueError("riskLevel 超出范围")
    result = {
        "emotionCode": code, "emotion": LABELS[code], "intensity": intensity,
        "confidence": round(confidence, 3),
        "summary": str(value.get("summary") or "").strip()[:240],
        "response": str(value.get("response") or "").strip()[:300],
        "evidence": _string_list(value.get("evidence")),
        "triggers": _string_list(value.get("triggers")),
        "suggestion": str(value.get("suggestion") or "").strip()[:180],
        "followUpQuestion": str(value.get("followUpQuestion") or "").strip()[:120],
        "riskLevel": risk,
        "arousal": {0: 22, 1: 86, 2: 62, 3: 18, 4: 28, 5: 92}[code],
    }
    if not result["summary"] or not result["response"]:
        raise ValueError("模型结果缺少必要字段")
    if local_crisis or risk == "high":
        result["riskLevel"] = "high"
        result["suggestion"] = _crisis_suggestion()
    return result


def _fallback(text):
    if CRISIS_PATTERN.search(text):
        code, risk = 1, "high"
    elif re.search(r"难过|委屈|低落|伤心|孤独|失望", text):
        code, risk = 4, "medium"
    elif re.search(r"累|困|疲劳|走神|睡不够|疲惫", text):
        code, risk = 3, "low"
    elif re.search(r"开心|高兴|愉悦|喜欢|期待", text):
        code, risk = 2, "low"
    elif re.search(r"生气|烦|愤怒|讨厌|烦躁", text):
        code, risk = 5, "medium"
    elif re.search(r"紧张|害怕|焦虑|担心|抗拒|哭", text):
        code, risk = 1, "medium"
    else:
        code, risk = 0, "low"
    result = {
        "emotionCode": code, "emotion": LABELS[code], "intensity": 3, "confidence": 0.3,
        "summary": "本地演示模式仅进行关键词粗分类，不能替代大模型语义理解。",
        "response": "我听见你的描述了。配置大模型后，我会结合上下文和历史记录给出更完整的回应。",
        "evidence": ["本次文字输入"], "triggers": [],
        "suggestion": "先暂停片刻，找一个相对安静的位置，观察接下来几分钟的感受变化。",
        "followUpQuestion": "发生这件事之前，你正在经历什么？",
        "riskLevel": risk, "arousal": {0: 22, 1: 86, 2: 62, 3: 18, 4: 28, 5: 92}[code],
    }
    if risk == "high":
        result["suggestion"] = _crisis_suggestion()
    return result


def validate_result(value, local_crisis=False):
    """对外暴露的结果校验入口。

    Agent 工作流拿到的是模型原始 JSON，需要走同一套字段校验，
    避免两条路径产出结构不一致的结果。
    """
    return _validate(value, local_crisis)


def _retrieve_knowledge(text, top_k=3):
    """检索与当前消息相关的干预策略，作为 prompt 的参考素材。

    检索失败不阻断主流程——RAG 是增强项，不是必需项。
    """
    try:
        items = rag_retriever.retrieve(
            text, emotion_code=_guess_emotion(text), top_k=top_k
        )
        return rag_retriever.format_for_prompt(items)
    except Exception as exc:  # noqa: BLE001 - 检索异常不应影响对话
        print(f"[RAG] 检索失败，跳过知识增强：{exc}")
        return []


def analyze(context, facts=None):
    text = context["text"]
    local_crisis = bool(CRISIS_PATTERN.search(text))
    if not describe().get("enabled"):
        result = _fallback(text)
        result.update({"source": "fallback", "model": None})
        return result
    payload = {
        "当前消息": text,
        "当前情境": context.get("taskContext") or "未选择",
        "昨夜睡眠质量（1差到5好）": context.get("sleepQuality") or "未提供",
        "近期对话与反馈": context.get("history") or [],
        "可参考的干预思路": _retrieve_knowledge(text),
    }
    # facts 来自 Agent 工作流的工具调用结果（历史记录、统计等）。
    # 只在确实查到内容时加入，避免给模型一个空的「已知事实」造成干扰。
    if facts:
        payload["已知事实"] = facts
    user_prompt = json.dumps(payload, ensure_ascii=False)
    pattern, sample_input, sample_response, sample_code = next(
        (item for item in _STYLE_EXAMPLES if re.search(item[0], text)),
        _STYLE_EXAMPLES[-1],
    )
    sample = {
        "emotionCode": sample_code, "intensity": 3, "confidence": 0.8,
        "summary": "对示例情绪的简短概括", "response": sample_response,
        "evidence": ["来自示例输入的依据"], "triggers": [], "suggestion": "",
        "followUpQuestion": "", "riskLevel": "low",
    }
    example = (
        "\n" + _STYLE_GUIDE + "\n下面是表达风格和 JSON 格式示例，不要复述示例事件：\n示例输入："
        + sample_input + "\n示例输出：" + json.dumps(sample, ensure_ascii=False)
        + "\n现在分析下面的实际输入，只输出完整 JSON：\n"
    )
    # 部分国产兼容网关不处理 system 角色，合并到 user 并提供示例更稳定。
    last_error = None
    for attempt in range(2):
        correction = "\n上一次输出不是合法 JSON。这次必须检查引号、逗号和括号，只输出完整 JSON。\n" if attempt else ""
        raw, active = request_completion("", _SYSTEM_PROMPT + correction + example + user_prompt)
        try:
            result = _validate(_extract_json(raw), local_crisis)
            result.update({"source": "llm", "model": active["model"]})
            return result
        except ValueError as exc:
            last_error = exc
            print(f"[AI] 第 {attempt + 1} 次结构化输出校验失败：{exc}")
    raise ValueError(f"模型连续两次未返回有效结构化结果：{last_error}")


def generate_insights(payload):
    if not describe().get("enabled"):
        raise RuntimeError("尚未配置大模型，无法生成 AI 阶段回顾")
    raw, active = request_completion("", _REPORT_PROMPT + "\n实际统计数据：\n" + json.dumps(payload, ensure_ascii=False))
    value = _extract_json(raw)
    result = {
        "title": str(value.get("title") or "阶段情绪回顾")[:60],
        "overview": str(value.get("overview") or "")[:500],
        "patterns": _string_list(value.get("patterns")),
        "nextStep": str(value.get("nextStep") or "")[:240],
        "model": active["model"],
    }
    if not result["overview"] or not result["nextStep"]:
        raise ValueError("AI 回顾缺少必要内容")
    return result
