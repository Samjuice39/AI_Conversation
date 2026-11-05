# coding: utf-8
"""Agent 取数：判断是否需要查询，执行工具并返回结果。"""

import json
import re
import sqlite3
from datetime import datetime, timedelta

import rag_retriever
from ai_coach import _STYLE_EXAMPLES, _STYLE_GUIDE, _SYSTEM_PROMPT
from llm_client import chat_completion

# 工具定义
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_recent_records",
            "description": (
                "查询用户最近几天的情绪记录明细，用于了解情绪变化过程、"
                "回顾之前发生过什么、或确认某个困扰是否反复出现。"
                "当用户说「最近总是」「又来了」这类暗示重复的表述时应当调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "回溯天数，默认 7"},
                    "limit": {"type": "integer", "description": "最多返回条数，默认 10"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_emotion_summary",
            "description": (
                "统计最近一段时间的情绪分布、平均强度、高频触发因素和采纳反馈。"
                "当用户问「我最近怎么样」「有没有好转」这类需要整体判断的问题时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "统计天数，默认 7"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_interventions",
            "description": (
                "从干预策略库检索具体做法（如呼吸法、担忧分流、行为激活）。"
                "当用户需要可执行的建议、或当前困扰有明确对应技术时调用。"
                "只想要陪伴倾诉时不需要调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索用的关键词或原话"},
                    "emotion_code": {
                        "type": "integer",
                        "description": "情绪码 0平静 1焦虑 2愉悦 3疲惫 4低落 5烦躁，可选",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

LABELS = {0: "平静", 1: "焦虑", 2: "愉悦", 3: "疲惫", 4: "低落", 5: "烦躁"}

# 中文键名兜底。
_CN_KEYS = {
    "情绪": "emotionCode", "情绪码": "emotionCode", "情绪类别": "emotionCode",
    "强度": "intensity", "置信度": "confidence", "可信度": "confidence",
    "摘要": "summary", "概括": "summary",
    "回复": "response", "回应": "response", "回应内容": "response",
    "依据": "evidence", "风险依据": "evidence", "判断依据": "evidence",
    "触发因素": "triggers", "诱因": "triggers",
    "建议": "suggestion", "干预建议": "suggestion",
    "追问": "followUpQuestion", "追问问题": "followUpQuestion",
    "风险等级": "riskLevel", "风险": "riskLevel", "风险级别": "riskLevel",
}


# 情绪码别名，统一转成 0-5。
_EMOTION_ALIAS = {
    "calm": 0, "neutral": 0, "平静": 0,
    "anxiety": 1, "anxious": 1, "nervous": 1, "焦虑": 1, "紧张": 1,
    "joy": 2, "happy": 2, "happiness": 2, "愉悦": 2, "开心": 2,
    "fatigue": 3, "tired": 3, "exhausted": 3, "疲惫": 3, "累": 3,
    "low": 4, "sad": 4, "sadness": 4, "低落": 4, "难过": 4,
    "irritable": 5, "irritated": 5, "angry": 5, "烦躁": 5, "烦": 5,
}


def _normalize(raw):
    """清理围栏、映射键名并修正字段类型。"""
    text = (raw or "").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text

    fixed = {_CN_KEYS.get(k, k): v for k, v in data.items()}

    # 情绪码 0-5
    code = fixed.get("emotionCode")
    if isinstance(code, str):
        key = code.strip().lower()
        fixed["emotionCode"] = _EMOTION_ALIAS.get(key, _EMOTION_ALIAS.get(code.strip(), 0))
    elif isinstance(code, (int, float)):
        fixed["emotionCode"] = int(code)
    else:
        fixed["emotionCode"] = 0

    # 强度 1-5
    try:
        intensity = int(float(fixed.get("intensity", 3)))
    except (TypeError, ValueError):
        intensity = 3
    fixed["intensity"] = max(1, min(5, intensity))

    # 置信度 0-1，兼容十分制与百分数
    try:
        confidence = float(fixed.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    if confidence > 1:
        if confidence <= 10:
            confidence = confidence / 10
        elif confidence <= 100:
            confidence = confidence / 100
        else:
            confidence = 0.7
    fixed["confidence"] = round(max(0.0, min(1.0, confidence)), 2)

    # 风险等级
    risk = str(fixed.get("riskLevel", "low")).strip().lower()
    fixed["riskLevel"] = risk if risk in ("low", "medium", "high") else "low"

    return json.dumps(fixed, ensure_ascii=False)


# 工具实现
def _connect(db_path):
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _query_recent_records(db_path, user_id, days=7, limit=10):
    """按时间倒序返回最近记录的关键字段。"""
    limit = max(1, min(int(limit or 10), 30))
    days = max(1, min(int(days or 7), 90))
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT created_at, emotion, intensity, summary, triggers_json,
                      risk_level, feedback
               FROM checkins
               WHERE user_id = ? AND created_at >= ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, since, limit),
        ).fetchall()

    records = []
    for r in rows:
        try:
            triggers = json.loads(r["triggers_json"] or "[]")
        except (TypeError, ValueError):
            triggers = []
        records.append({
            "时间": r["created_at"],
            "情绪": r["emotion"],
            "强度": r["intensity"],
            "摘要": r["summary"],
            "触发因素": triggers,
            "风险": r["risk_level"],
            "用户反馈": r["feedback"],
        })
    return {"天数": days, "条数": len(records), "记录": records}


def _get_emotion_summary(db_path, user_id, days=7):
    """聚合情绪分布、平均强度、高频触发因素与反馈采纳情况。"""
    days = max(1, min(int(days or 7), 90))
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT emotion, intensity, triggers_json, feedback, risk_level
               FROM checkins WHERE user_id = ? AND created_at >= ?""",
            (user_id, since),
        ).fetchall()

    if not rows:
        return {"天数": days, "条数": 0, "说明": "该时间段内没有记录"}

    dist = {}
    intensities = []
    triggers = {}
    helpful = 0
    not_helpful = 0
    high_risk = 0

    for r in rows:
        dist[r["emotion"]] = dist.get(r["emotion"], 0) + 1
        if r["intensity"] is not None:
            intensities.append(r["intensity"])
        if r["risk_level"] == "high":
            high_risk += 1
        if r["feedback"] == "helpful":
            helpful += 1
        elif r["feedback"] == "unhelpful":
            not_helpful += 1
        try:
            for t in json.loads(r["triggers_json"] or "[]"):
                triggers[t] = triggers.get(t, 0) + 1
        except (TypeError, ValueError):
            pass

    top_triggers = sorted(triggers.items(), key=lambda kv: kv[1], reverse=True)[:5]
    total = len(rows)
    return {
        "天数": days,
        "条数": total,
        "情绪分布": dist,
        "平均强度": round(sum(intensities) / len(intensities), 2) if intensities else None,
        "高频触发因素": [{"因素": k, "次数": v} for k, v in top_triggers],
        "反馈": {"有帮助": helpful, "没帮助": not_helpful},
        "高风险记录数": high_risk,
        "采纳率": round(helpful / (helpful + not_helpful), 2) if (helpful + not_helpful) else None,
    }


def _search_interventions(query, emotion_code=None):
    """检索干预策略，返回精简结果。"""
    items = rag_retriever.retrieve(query, emotion_code=emotion_code, top_k=3)
    return {
        "检索词": query,
        "命中": len(items),
        "使用提示": "以下是策略素材，请用自己的话结合对方处境表达，只取一条，不要逐条罗列，不要照抄步骤",
        "策略": [
            {"标题": it["title"], "步骤": it["content"], "适用": it["when"]}
            for it in items
        ],
    }


_DISPATCH = {
    "query_recent_records": lambda args, db, uid: _query_recent_records(
        db, uid, args.get("days", 7), args.get("limit", 10)),
    "get_emotion_summary": lambda args, db, uid: _get_emotion_summary(
        db, uid, args.get("days", 7)),
    "search_interventions": lambda args, db, uid: _search_interventions(
        args.get("query", ""), args.get("emotion_code")),
}


# 工作流主体
_AGENT_SYSTEM = """你可以调用工具获取信息后再回应，工具不是必须用的。

判断顺序：
1. 先看当前消息本身。如果用户只是想说说、分享心情、或者情绪很清楚，不需要调用任何工具，直接回应。
2. 如果用户提到「最近总是」「又」「一直」这类重复性表述，或需要了解变化趋势，调用 query_recent_records 或 get_emotion_summary。
3. 如果用户需要具体可执行的做法，调用 search_interventions 取策略参考。
4. 为了用工具而用工具，会让回应变得机械。宁可不用。

若调用了工具，evidence 里要体现你依据的具体事实（例如某天记录的情绪、某个高频触发因素），不要凭空断言。
工具返回的参考策略是素材，不是台词。只在对方明确需要做法时自然融入其中一条，不要逐条列举、不要照搬措辞、不要为了用上而硬塞。
response 绝不能写成步骤清单或待办列表。对方只是在倾诉时，不给建议、不安排下一步；明确求助时也只给一个做法，用商量语气，不用命令式。

以下是本产品一贯的表达要求，与是否调用工具无关：
""" + _SYSTEM_PROMPT + "\n" + _STYLE_GUIDE + "\n下面是表达风格示例，不要复述示例事件：\n" + "\n".join(
    f"示例输入：{item[1]}\n示例回应：{item[2]}" for item in _STYLE_EXAMPLES
)


_COLLECT_SYSTEM = """你要判断：为了恰当地回应这个人，需不需要先查一些信息。

可用工具：
- query_recent_records：查最近几天的情绪记录明细。当用户提到「最近总是」「又」「一直」这类重复性表述时用。
- get_emotion_summary：情绪分布与趋势统计。当用户问「我最近怎么样」「有没有好转」时用。
- search_interventions：检索具体干预策略。当用户明确需要可执行做法时用。

原则：
- 用户只是想说说、分享心情、或情绪已经很清楚时，不要调用任何工具，直接结束。
- 最多调用 2 个工具，不要为了显得勤快而查。
- 只调工具，不要写回应文字。"""


def collect(context, db_path, user_id, max_steps=2, llm_config=None):
    """只收集工具结果，不生成用户可见文字。"""
    messages = [
        {"role": "system", "content": _COLLECT_SYSTEM},
        {
            "role": "user",
            "content": json.dumps({
                "当前消息": context["text"],
                "当前情境": context.get("taskContext") or "未选择",
                "昨夜睡眠质量（1差到5好）": context.get("sleepQuality") or "未提供",
            }, ensure_ascii=False),
        },
    ]

    facts = []
    trace = []
    model = None

    for step in range(max_steps):
        message, active = chat_completion(messages, tools=TOOLS, temperature=0.3, config=llm_config)
        model = (active or {}).get("model") or model

        calls = message.get("tool_calls") or []
        if not calls:
            trace.append({"step": step + 1, "action": "无需更多信息"})
            break

        messages.append(message)
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            handler = _DISPATCH.get(name)
            if handler is None:
                result = {"错误": f"未知工具 {name}"}
            else:
                try:
                    result = handler(args, db_path, user_id)
                except Exception as exc:  # noqa: BLE001 - 工具失败不应中断
                    result = {"错误": f"{type(exc).__name__}: {exc}"}
            trace.append({"step": step + 1, "action": "调用工具", "tool": name, "args": args})
            facts.append({"tool": name, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(result, ensure_ascii=False),
            })

    return {"facts": facts, "trace": trace, "model": model}


def run(context, db_path, user_id, max_steps=3, llm_config=None):
    """执行完整的工具调用流程，返回原始 message 与轨迹。"""
    messages = [
        {"role": "system", "content": _AGENT_SYSTEM},
        {
            "role": "user",
            "content": json.dumps({
                "当前消息": context["text"],
                "当前情境": context.get("taskContext") or "未选择",
                "昨夜睡眠质量（1差到5好）": context.get("sleepQuality") or "未提供",
                "近期对话与反馈": context.get("history") or [],
            }, ensure_ascii=False),
        },
    ]

    trace = []
    active = None

    for step in range(max_steps):
        message, active = chat_completion(messages, tools=TOOLS, temperature=0.4, config=llm_config)

        calls = message.get("tool_calls") or []
        if not calls:
            # 工具轮草稿不写回上下文，避免污染 JSON 收尾。
            trace.append({
                "step": step + 1,
                "action": "信息已足够",
                "tools": [],
                "draft": (message.get("content") or "")[:60],
            })
            break

        # 保留 tool_calls 消息，tool 结果才能对应。
        messages.append(message)

        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = {}
            handler = _DISPATCH.get(name)
            if handler is None:
                result = {"错误": f"未知工具 {name}"}
            else:
                try:
                    result = handler(args, db_path, user_id)
                except Exception as exc:  # noqa: BLE001 - 工具失败不应中断对话
                    result = {"错误": f"{type(exc).__name__}: {exc}"}
            trace.append({"step": step + 1, "action": "调用工具", "tools": [name], "args": args})
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(result, ensure_ascii=False),
            })

    # 收尾只输出 JSON。
    messages.append({
        "role": "user",
        "content": (
            "现在输出最终 JSON。必须严格使用以下英文键名，一个不多一个不少："
            "emotionCode, intensity, confidence, summary, response, evidence, "
            "triggers, suggestion, followUpQuestion, riskLevel。"
            "其中 emotionCode 必须是 0-5 的整数（0平静 1焦虑 2愉悦 3疲惫 4低落 5烦躁），"
            "不要写 anxiety 这类英文单词；intensity 必须是 1-5 的整数。"
            "不要用中文键名，不要包 markdown 代码块，不要输出任何解释文字。"
        ),
    })
    final, active = chat_completion(messages, tools=None, temperature=0.2,
                                    max_tokens=2000, json_mode=True, config=llm_config)
    if isinstance(final, dict):
        final = dict(final)
        final["content"] = _normalize(final.get("content"))
    return final, {"trace": trace, "model": (active or {}).get("model")}
