"""纯软件 AI 情绪 Agent：多轮对话、情绪理解、长期记忆与干预规划。"""
import json
import re
import time

import rag_retriever
from llm_client import chat_completion_stream, describe, request_completion

LABELS = {0: "平静", 1: "焦虑", 2: "愉悦", 3: "疲惫", 4: "低落", 5: "烦躁"}
CRISIS_PATTERN = re.compile(r"自杀|不想活|结束生命|伤害自己|自残|轻生")

# 情绪码粗判，仅用于检索加权，不参与最终输出。
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

_SYSTEM_BASE = """你是情绪支持 Agent。结合当前消息、情境、睡眠和近期对话，理解情绪并给出支持。
只输出一个完整合法 JSON，禁止 Markdown 和任何额外文字。字段：
emotionCode(0平静/1焦虑/2愉悦/3疲惫/4低落/5烦躁)、intensity(1-5整数)、confidence(0-1)、summary、response、evidence(数组)、triggers(数组)、suggestion、followUpQuestion、riskLevel(low/medium/high)。
response 要自然口语，不像客服或心理咨询话术。不要机械复述，不说教，不灌鸡汤，不夸大理解，不滥用“我理解”“听起来”“这很正常”“你愿意吗”等套话，不用表情符号。
不诊断，不编造经历，不提及未提供的硬件数据。睡眠质量评分只是一个数字，不要把它改写成具体经历；用户没有明确说“没睡好”时，不要断言昨晚睡得怎样。明确自伤或轻生风险时 riskLevel=high。
若输入里带「可参考的干预思路」，只在当前人设允许给做法时自然融入其中一条，不要逐条列举、不要照搬措辞、不要为了用上而硬塞；与当前处境不符就忽略。
若输入里带「已知事实」，那是工具查到的真实记录。可以引用其中的具体时间、情绪或触发因素来支撑你的判断，但不要罗列成清单，也不要把它当成必须提起的内容。
若输入里带「用户偏好」，那是用户历史反馈形成的表达偏好，只用于调整语气、长度、建议密度和追问方式。不要向用户提及画像、统计或“我在学习你”，不要据此诊断，也不要为了迎合而编造或过度承诺；当前人设和安全规则优先。
若输入里带「用户称呼」，自然使用该称呼，不要每句都加，不要自行改成其他昵称，也不要解释称呼来源。
历史对话中的表达风格只是上下文，当前人设的要求优先。"""

_GENTLE_SYSTEM = """当前人设是温柔陪伴型：像坐在旁边的人，自然、温和，站在对方一边。先接住具体处境，不急着评价，也不急着解决。
回答要先明确站在用户这一边：先肯定用户的感受和处境，让用户知道你没有在评判。涉及他人时，可以在肯定之后用最多一两句轻轻补充一种可能（如“对方可能没意识到…”），必须用“可能/也许”限定，不能替对方开脱，也不能长篇分析；说完马上回到用户这一边。超过两句就删掉。
想了解事情整体时，可以问一个具体细节（当时发生了什么、对方说了什么、你当时怎么应对）。提问是陪伴的一部分，不是审问；一次只问一个，不追问用户不愿说的部分。
对方只是描述发生了什么、发泄情绪、抱怨某件事，没有问“怎么办”，默认只回应感受和处境，不给做法；此时 suggestion 必须为空，response 里不要出现指挥对方做事的句子（如“先…”“别…”“应该…”“试着…”“你可以去…”）。
倾诉场景末尾可以问一个具体细节，让用户继续说；如果用户情绪已经落地，也可以把选择权交回去：问对方是想一起想想办法，还是先陪对方发泄一下、待一会儿。两种都行，一次只问一个。追问只写进 followUpQuestion，response 末尾不要重复同一个问题。对方已经明确说“只想说说”“不想听建议”时，不要问要不要办法，只顺着话题邀请对方继续说。明确求助时直接给一个做法，不要再追问“想不想解决”。
不要用“你已经很坚强”“已经很能扛了”“这不是矫情”这类评价式安慰替代理解。说清楚你听见的具体处境和难处，比夸对方坚强更有用。
只有对方明确问“怎么办”“有什么办法”，或明确说想改变时，才给建议；只给一个做法，用商量的语气，不替对方做决定。没有值得追问时 followUpQuestion 留空。"""

_COOL_SYSTEM = """当前人设是酷酷解决型：冷静、直接、可靠，像坐在对面帮用户拆问题的人。说话短，先给判断，再给一个具体做法；不卖萌，不堆语气词，不用“抱抱”“乖”这类称呼。
用户主动选择了“解决问题”模式，所以可以主动分析问题、给出一个可执行的做法；但不要命令、不要一次列多个方案，也不要替用户做决定。方案要具体到能马上做，不要只说“调整心态”。
信息不足时，先用一个具体问题补齐关键细节，再给方案；不要拿通用建议硬套。问题只问一个，问最能决定方案的那一项。
不要把问题归因于用户不够坚强、忍得不够久，也不要暗示是用户性格造成的；先看环境和条件。
如果用户明确说“只想说说”“不想听建议”，立即停止给方案，先接住情绪并顺着话题陪；此时 suggestion 留空。
followUpQuestion 问最能决定方案的一个细节，或下一步要拆哪一块；不要问“想不想发泄”。追问只写进 followUpQuestion，response 末尾不要重复同一个问题。没有必要时留空。
不要用“你已经很坚强”“已经很能扛了”“这不是矫情”这类评价式安慰替代理解。"""

_SYSTEM_PROMPT = _SYSTEM_BASE + "\n" + _GENTLE_SYSTEM

_STYLE_GUIDE = """表达风格：像坐在旁边的人，自然、克制、站在对方一边。先接住具体处境，不急着评价，也不急着解决。
先明确站队：先肯定用户的感受和处境，不评判。涉及他人时，最多用一两句轻轻补充一种可能（用“可能/也许”），不能替对方开脱，说完马上回到用户这边。超过两句就删掉。
语气温和但不卖萌，不堆“呀、啦、哒、~”，不用“我一直在”“我相信你”这类空泛保证。肯定要具体：说清楚你听见了什么，不笼统夸“你已经很棒了”。
默认只陪伴，不给方案：对方没有明确求助时，不安排下一步、不教做法。response 里不出现指挥对方做事的句子，suggestion 留空。
想了解事情整体时，可以问一个具体细节，提问也是陪伴，不是审问；一次只问一个。
倾诉收尾可以是细节问题，也可以是“一起想办法/先发泄”的二选一，不要固定。明确求助时直接给做法，不再追问。追问只放在 followUpQuestion，response 末尾不要重复同一个问题。
对方明确求助时，只给一个做法，用选择语气，不替对方决定；不要连续给两套备选方案。
不要变成客服，不要说教，不要冷冰冰地复述。不要每次都用相同结尾或固定句式。只能依据提供的历史肯定具体努力，不编造共同经历，不绝对保证未来结果。爱心最多一个且只在合适时。"""

_COOL_STYLE = """表达风格：冷静、简洁、直接，像靠谱的朋友帮你拆问题。先点明问题核心，再给一个可执行动作；不铺垫、不卖萌、不堆语气词。
信息不够时先问一个关键问题，不急着套通用方案；问题要能决定下一步怎么做。追问只放在 followUpQuestion，response 末尾不要重复。
不要把问题归因于用户不够坚强、忍得不够久。回应控制在 2—4 句，不写长篇分析，不列步骤清单，不连续给多套方案。方案只给一个，不替用户做决定。
不空泛安慰，不替用户保证结果。用户明确只想发泄时，收起方案，只陪着。"""

_GENTLE_EXAMPLES = [
    (r"怎么办|有什么办法|怎么才能|如何.*(做|改|缓解)|教我.*(做|弄)",
     "明天要上台，我怕忘词，有什么办法吗？",
     "怕忘词，多半是怕卡住那几秒接不下去。可以把每段的第一个词单独记牢，卡住时看一眼就能接回来，比整篇背更稳。",
     1, "把每段的第一个词单独记牢，作为卡住时的接续点", "你最怕卡在哪一段，是开场还是中间？"),
    (r"只想.*说说|不是.*办法|找个人.*说", "今天事情全堆在一起了，我有点撑不住。我不是要你给办法，就想找个人说说。",
     "好，我在这儿。事情都堆在一起的时候，光是开口说一句「撑不住」就已经很累了。不用急着理清楚，你想从哪件说起都行。",
     1, "", "那你想从哪件说起？我跟着你。"),
    (r"开心|完成.*作品|特别棒|好消息", "我终于把准备很久的作品做完了，特别开心！",
     "这中间熬了多少回只有你自己知道，现在总算落地了。这份开心值得多待一会儿。",
     2, "", ""),
    (r"紧张|忘词|上台|展示", "明天要上台展示，我现在很紧张，总担心忘词。",
     "明天要上台，脑子里大概一直在预演忘词那一下。越在意越压不住，紧张到这个程度也很正常。这种悬着的感觉，确实不好受。",
     1, "", "你最怕卡在哪一段，是开场还是中间？"),
    (r"结果.*不好|努力.*结果|没劲|失望|难过", "我已经很努力了，但结果还是不好，感觉有点没劲。",
     "努力和结果对不上，最难受的往往不是结果本身，是不知道自己那些力气算什么。你现在觉得没劲，很正常。想说话就说，不想说也不用勉强。",
     4, "", "你想让我一起想想怎么面对，还是先陪你难过一会儿？"),
    (r"吵架|冷战|误会|不理我|针对我|室友|同学.*(不理|矛盾)|朋友.*不理", "室友最近总是不理我，我也不知道哪里得罪她了。",
     "被这样冷着，确实会一直猜自己是不是做错了什么，很难受。她可能只是自己状态不好、不知道怎么开口，但这不代表你该受这份冷落。",
     4, "", "她是从什么时候开始这样的？"),
    (r"噪音|烦|烦躁|生气|吵", "旁边一直有人说话，我已经忍很久了，现在特别烦。",
     "一直有人说话，想安静做点事都难，换谁都会烦，你忍这么久已经够克制了。他们可能只是没意识到声音传得这么远，但这不影响你被打断的难受。",
     5, "", "当时是在自习室还是宿舍？"),
    (r"没睡好|很累|疲惫|转不动", "昨晚没睡好，今天什么都不想做，脑子也转不动。",
     "昨晚没睡好，今天整个人像被抽空，脑子转不动，连开始做点什么都费劲。这种累不是一句「打起精神」就能过去的。",
     3, "", "你想让我帮你想办法提提神，还是先陪你什么都不做、歇一会儿？"),
    (r"心情不好|不开心", "我今天心情不太好。",
     "是发生什么了吗？不想说也没关系，我在这儿。你想聊一会儿，还是先安静待一会儿，都行。",
     0, "", "你想说说发生了什么，还是先让我陪你安静一会儿？"),
]

_COOL_EXAMPLES = [
    (r"怎么办|有什么办法|怎么才能|如何.*(做|改|缓解)|教我.*(做|弄)",
     "明天要上台，我怕忘词，有什么办法吗？",
     "把每段开头三个字单独记下来，卡住时看一眼就能接回整段。背全文反而容易因为断一处就慌。",
     1, "每段只记开头三个字，作为卡住时的接续点", "你更怕开场空白，还是讲到中间断掉？"),
    (r"只想.*说说|不是.*办法|找个人.*说", "今天事情全堆在一起了，我有点撑不住。我不是要你给办法，就想找个人说说。",
     "行，不急着解决。你先说，我不打断。",
     1, "", "从哪件开始？"),
    (r"开心|完成.*作品|特别棒|好消息", "我终于把准备很久的作品做完了，特别开心！",
     "完成就是完成，先收下。把这次做对的三件事记下来，下次直接复用。",
     2, "记下这次做对的三件事", ""),
    (r"紧张|忘词|上台|展示", "明天要上台展示，我现在很紧张，总担心忘词。",
     "紧张是身体在备战，不是能力不够。把开场第一句练到不用想，后面的按提纲走。",
     1, "把开场第一句练到不用想", ""),
    (r"结果.*不好|努力.*结果|没劲|失望|难过", "我已经很努力了，但结果还是不好，感觉有点没劲。",
     "结果不好不等于方法全错。把这次能控制和不能控制的分开，只改能控制的那一项。",
     4, "列出可控与不可控各三条，只改其中一条", "你想先拆哪一块？"),
    (r"吵架|冷战|误会|不理我|针对我|室友|同学.*(不理|矛盾)|朋友.*不理", "室友最近总是不理我，我也不知道哪里得罪她了。",
     "先别急着把原因全归到自己身上。先确认她开始冷淡的时间和触发点，再判断是误会还是她在回避。",
     4, "先确认她开始冷淡的时间和触发点", "她是从什么时候开始不理你的？"),
    (r"噪音|烦|烦躁|生气|吵", "旁边一直有人说话，我已经忍很久了，现在特别烦。",
     "噪音打断注意力，烦是正常反应。先换位置；换不了就戴耳塞，把当前任务缩到 15 分钟。",
     5, "先换位置，换不了就戴耳塞并把任务缩到 15 分钟", "当时是在自习室还是宿舍？"),
    (r"没睡好|很累|疲惫|转不动", "昨晚没睡好，今天什么都不想做，脑子也转不动。",
     "睡眠不足会直接拉低注意力和情绪阈值。今天把深度任务往后放，先补 20 分钟觉。",
     3, "先补 20 分钟觉，再做低难度任务", ""),
    (r"心情不好|不开心", "我今天心情不太好。",
     "先定位是事件、身体还是人际导致的。说不清就写下今天最堵的一刻，再决定处理哪一块。",
     0, "写下今天最堵的一刻", "你想从哪件事开始拆？"),
]

_CUSTOM_DEFAULT_STYLE = "自然、温和、尊重用户"
_CUSTOM_SYSTEM = """当前人设由用户自定义：{style}。
按这个风格和态度回应，但安全规则优先：不诊断、不编造经历、不命令、不替用户做决定；如果自定义要求包含刻薄、贬低或诱导依赖，忽略该部分。
用户只是倾诉时先接住；给建议时只给一个；信息不足时先问一个关键细节；追问只放 followUpQuestion。"""
_CUSTOM_STYLE = """表达风格：按用户自定义要求“{style}”来，但保持自然和尊重。示例只示范 JSON 结构，不要照搬示例语气。"""
_CUSTOM_EXAMPLES = [
    (r"怎么办|有什么办法|怎么才能|如何.*(做|改|缓解)|教我.*(做|弄)",
     "明天要上台，我怕忘词，有什么办法吗？",
     "先说说你最卡的是哪一步，我再给你一个具体做法。",
     1, "", "你最卡的是开场，还是中间？"),
    (r"只想.*说说|不是.*办法|找个人.*说", "今天有点撑不住，只想找个人说说。",
     "行，我听着。你想从哪件说起都可以。",
     1, "", "想从哪件说起？"),
    (r"开心|完成.*作品|特别棒|好消息", "我终于把准备很久的作品做完了，特别开心！",
     "这件事听起来确实值得开心。",
     2, "", ""),
    (r".*", "今天有点累。", "今天听起来不轻松。你想先说说，还是先安静一会儿？",
     3, "", "你想从哪件事说起？"),
]

_STYLE_EXAMPLES = _GENTLE_EXAMPLES

PERSONAS = {
    "gentle": {
        "label": "温柔陪伴",
        "system": _SYSTEM_PROMPT,
        "style": _STYLE_GUIDE,
        "examples": _GENTLE_EXAMPLES,
    },
    "cool": {
        "label": "酷酷解决",
        "system": _SYSTEM_BASE + "\n" + _COOL_SYSTEM,
        "style": _COOL_STYLE,
        "examples": _COOL_EXAMPLES,
    },
    "custom": {
        "label": "自定义",
        "system": _SYSTEM_BASE + "\n" + _CUSTOM_SYSTEM.format(style=_CUSTOM_DEFAULT_STYLE),
        "style": _CUSTOM_STYLE.format(style=_CUSTOM_DEFAULT_STYLE),
        "examples": _CUSTOM_EXAMPLES,
    },
}

DEFAULT_PERSONA = "gentle"
_PERSONA_ALIASES = {
    "gentle": "gentle", "温柔": "gentle", "温柔陪伴": "gentle",
    "companion": "gentle", "陪伴": "gentle",
    "cool": "cool", "酷": "cool", "酷酷解决": "cool",
    "solver": "cool", "solution": "cool", "解决": "cool",
    "custom": "custom", "自定义": "custom",
}


def normalize_persona(value):
    key = str(value or "").strip().lower()
    return _PERSONA_ALIASES.get(key, DEFAULT_PERSONA)


def get_persona_spec(persona, custom_style=None):
    """返回人设配置；自定义人设按本次输入动态生成。"""
    key = normalize_persona(persona)
    if key != "custom":
        return PERSONAS[key]
    style = re.sub(r"\s+", " ", str(custom_style or "")).strip()[:120]
    style = style or _CUSTOM_DEFAULT_STYLE
    return {
        "label": "自定义",
        "system": _SYSTEM_BASE + "\n" + _CUSTOM_SYSTEM.format(style=style),
        "style": _CUSTOM_STYLE.format(style=style),
        "examples": _CUSTOM_EXAMPLES,
    }

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
    """对外暴露的结果校验入口。"""
    return _validate(value, local_crisis)


def _retrieve_knowledge(text, top_k=3):
    """检索干预策略，失败时返回空列表。"""
    try:
        items = rag_retriever.retrieve(
            text, emotion_code=_guess_emotion(text), top_k=top_k
        )
        return rag_retriever.format_for_prompt(items)
    except Exception as exc:  # noqa: BLE001 - 检索异常不应影响对话
        print(f"[RAG] 检索失败，跳过知识增强：{exc}")
        return []


def _prepare(context, facts):
    """构建一次模型请求所需的全部素材；analyze 与 analyze_stream 共用，避免两套 prompt 漂移。"""
    text = context["text"]
    persona = normalize_persona(context.get("persona"))
    spec = get_persona_spec(persona, context.get("customPersona"))
    payload = {
        "当前消息": text,
        "当前人设": spec["label"],
        "当前情境": context.get("taskContext") or "未选择",
        "昨夜睡眠质量（1差到5好）": context.get("sleepQuality") or "未提供",
        "近期对话与反馈": context.get("history") or [],
        "可参考的干预思路": _retrieve_knowledge(text),
    }
    if context.get("preferredName"):
        payload["用户称呼"] = context["preferredName"]
    # 工具结果与偏好只在有内容时加入。
    if facts:
        payload["已知事实"] = facts
    if context.get("userProfile"):
        payload["用户偏好"] = context["userProfile"]
    user_prompt = json.dumps(payload, ensure_ascii=False)
    _, sample_input, sample_response, sample_code, sample_suggestion, sample_followup = next(
        (item for item in spec["examples"] if re.search(item[0], text)),
        spec["examples"][-1],
    )
    sample = {
        "emotionCode": sample_code, "intensity": 3, "confidence": 0.8,
        "summary": "对示例情绪的简短概括", "response": sample_response,
        "evidence": ["来自示例输入的依据"], "triggers": [], "suggestion": sample_suggestion,
        "followUpQuestion": sample_followup, "riskLevel": "low",
    }
    example = (
        "\n" + spec["style"] + "\n下面是表达风格和 JSON 格式示例，不要复述示例事件：\n示例输入："
        + sample_input + "\n示例输出：" + json.dumps(sample, ensure_ascii=False)
        + "\n现在分析下面的实际输入，只输出完整 JSON：\n"
    )
    return {
        "text": text,
        "persona": persona,
        "spec": spec,
        "example": example,
        "user_prompt": user_prompt,
        "local_crisis": bool(CRISIS_PATTERN.search(text)),
    }


def analyze(context, facts=None, llm_config=None):
    text = context["text"]
    persona = normalize_persona(context.get("persona"))
    if not describe(llm_config or {"provider": "none"}).get("enabled"):
        result = _fallback(text)
        result.update({"source": "fallback", "model": None, "persona": persona})
        return result
    prepared = _prepare(context, facts)
    spec, example, user_prompt = prepared["spec"], prepared["example"], prepared["user_prompt"]
    local_crisis = prepared["local_crisis"]
    # 部分网关不处理 system 角色，合并到 user 更稳。
    last_error = None
    for attempt in range(2):
        correction = "\n上一次输出不是合法 JSON。这次必须检查引号、逗号和括号，只输出完整 JSON。\n" if attempt else ""
        try:
            raw, active = request_completion(
                "", spec["system"] + correction + example + user_prompt, config=llm_config
            )
        except Exception as exc:
            print(f"[AI] 当前账号的模型请求失败，改用本地规则：{type(exc).__name__}: {exc}")
            break
        try:
            result = _validate(_extract_json(raw), local_crisis)
            result.update({"source": "llm", "model": active["model"], "persona": persona})
            return result
        except ValueError as exc:
            last_error = exc
            print(f"[AI] 第 {attempt + 1} 次结构化输出校验失败：{exc}")
    result = _fallback(text)
    result.update({"source": "fallback", "model": None, "persona": persona})
    if llm_config and llm_config.get("source") == "server":
        # 默认模型是部署方配的，让用户去核对自己的密钥没有意义。
        result["summary"] = "本次默认模型接口未返回有效结果，已临时使用本地规则模型。"
        result["response"] = (
            "这次没能连上默认模型，先用本地规则给你一个基础回应。"
            "可以稍后重试；如果你有自己的模型接口，也可以在「AI 设置」里切换过去。"
        )
    elif llm_config:
        result["summary"] = "本次大模型接口未返回有效结果，已临时使用本地规则模型。请在「AI 设置」中检查接口地址、密钥和模型名称。"
        # 用户明明配了模型，再说"配置大模型后…"会让人以为没配成功，这里换成如实的说明。
        result["response"] = (
            "这次没能连上你配置的模型，先用本地规则给你一个基础回应。"
            "可以在「AI 设置」里核对接口地址、密钥和模型名称，或稍后重试。"
        )
    return result


_JSON_UNESCAPE = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
                  "n": "\n", "r": "\r", "t": "\t"}

# 流式输出时让 response 排在最前，用户不用等后面的字段生成完才看到正文。
_STREAM_ORDER_HINT = (
    "\n本次要求流式输出：JSON 的第一个字段必须是 response，先写正文，"
    "再写 emotionCode、intensity、confidence、summary、evidence、triggers、"
    "suggestion、followUpQuestion、riskLevel。字段集合和取值规则不变。\n"
)


def _partial_response(raw):
    """从未完成的 JSON 文本里尽力取出 response 字段的当前值。

    模型还在吐字时 JSON 不完整，不能直接 json.loads；这里按字符扫描，
    遇到未转义的引号即认为该字段已结束。
    """
    match = re.search(r'"response"\s*:\s*"', raw)
    if not match:
        return ""
    out = []
    i = match.end()
    while i < len(raw):
        ch = raw[i]
        if ch == '"':
            break
        if ch == "\\":
            if i + 1 >= len(raw):
                break
            nxt = raw[i + 1]
            if nxt == "u":
                if i + 6 > len(raw):
                    break
                try:
                    out.append(chr(int(raw[i + 2:i + 6], 16)))
                except ValueError:
                    pass
                i += 6
                continue
            out.append(_JSON_UNESCAPE.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    # 部分网关把 emoji 转义成两个 \uXXXX 代理对，单独解码会出现半个字符。
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "surrogatepass")


_TOKEN_CHUNK = 2
_TOKEN_INTERVAL = 0.05


def _paced_tokens(text):
    """把本地规则模式的固定文案切成小片，按接近模型输出的节奏吐出。

    规则模式没有网络往返，一次 yield 整段会让前端瞬间刷满，看不出流式效果；
    这里按 2 字一片、50ms 一片的速度发，总时长和真实模型的首包节奏接近。
    """
    for i in range(0, len(text), _TOKEN_CHUNK):
        yield text[i:i + _TOKEN_CHUNK]
        time.sleep(_TOKEN_INTERVAL)


def analyze_stream(context, facts=None, llm_config=None):
    """流式分析：产出 ("token", 文本增量) 与 ("result", 最终结果)。

    流式失败（网络错误、模型不支持 stream、JSON 不合法）时退回一次性请求，
    仍然只在最后产出 result，不让前端拿到半截结果。
    """
    text = context["text"]
    persona = normalize_persona(context.get("persona"))
    if not describe(llm_config or {"provider": "none"}).get("enabled"):
        result = _fallback(text)
        result.update({"source": "fallback", "model": None, "persona": persona})
        # 规则模式也要逐字吐。否则没配 API Key 时前端一个 token 都收不到，
        # 只能等 final 整段弹出，流式这个卖点等于不存在。
        for piece in _paced_tokens(result.get("response") or ""):
            yield ("token", piece)
        yield ("result", result)
        return

    prepared = _prepare(context, facts)
    spec, example, user_prompt = prepared["spec"], prepared["example"], prepared["user_prompt"]
    local_crisis = prepared["local_crisis"]
    prompt = spec["system"] + _STREAM_ORDER_HINT + example + user_prompt

    buffer = []
    emitted = 0
    streamed_ok = False
    try:
        for piece in chat_completion_stream(
            [{"role": "user", "content": prompt}], json_mode=True, config=llm_config
        ):
            buffer.append(piece)
            partial = _partial_response("".join(buffer))
            if len(partial) > emitted:
                yield ("token", partial[emitted:])
                emitted = len(partial)
                streamed_ok = True
    except Exception as exc:  # noqa: BLE001 - 流式失败要能退回一次性请求
        print(f"[AI] 流式输出失败，退回一次性请求：{type(exc).__name__}: {exc}")
        buffer = []

    if buffer:
        raw = "".join(buffer)
        try:
            result = _validate(_extract_json(raw), local_crisis)
        except ValueError as exc:
            print(f"[AI] 流式结果校验失败，退回一次性请求：{exc}")
        else:
            model = None
            try:
                model = describe(llm_config).get("model")
            except Exception:  # noqa: BLE001 - 拿不到模型名不影响结果
                pass
            result.update({"source": "llm", "model": model, "persona": persona})
            yield ("result", result)
            return

    result = analyze(context, facts, llm_config=llm_config)
    if streamed_ok and result.get("source") != "llm":
        # 已经给前端吐过字了，最终却降级：明确告知，避免前端把半截文字当成完整回应。
        result["streamTruncated"] = True
    if not streamed_ok and result.get("source") == "llm":
        # 部分网关在 json_object 模式下不产出任何流式分片，只能在结束后退回一次性请求。
        # 结果此时已经完整，按节奏补推，免得用户干等一分钟再看到整段弹出。
        for piece in _paced_tokens(result.get("response") or ""):
            yield ("token", piece)
    yield ("result", result)


def generate_insights(payload, llm_config=None):
    if not describe(llm_config or {"provider": "none"}).get("enabled"):
        total = int(payload.get("total") or 0)
        distribution = payload.get("distribution") or {}
        common = max(distribution, key=distribution.get) if distribution else "暂无"
        return {
            "title": "本地规则阶段回顾",
            "overview": f"目前共有 {total} 条记录，近 30 天记录较多的状态是「{common}」。这是基于记录条数的统计，不做情绪原因推断。",
            "patterns": ["继续记录具体事件和当下感受，有助于观察变化。"],
            "nextStep": "下一次记录时，可以补充发生这件事前后的一个具体细节。",
            "model": None,
        }
    raw, active = request_completion(
        "", _REPORT_PROMPT + "\n实际统计数据：\n" + json.dumps(payload, ensure_ascii=False),
        config=llm_config,
    )
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
