# coding: utf-8
"""根据反馈统计表达偏好，不推断人格。"""
from statistics import mean
import re


_NICKNAME_CANDIDATES = ("宝宝", "宝贝", "亲爱的", "小可爱", "小朋友", "小助手", "同学")
_NAME_PATTERN = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_]{1,12}$")


def clean_preferred_name(value):
    """清理用户填写的称呼；不合法就返回空字符串。"""
    name = re.sub(r"\s+", "", str(value or "").strip())
    name = re.sub(r"[吧呀哦啊哈]$", "", name)
    return name if _NAME_PATTERN.fullmatch(name) else ""


def infer_nickname(text):
    """从“宝宝，……”“叫我小明”这类表达中推断称呼。"""
    source = str(text or "").strip()
    called = re.search(r"(?:叫我|称呼我|以后叫我)[：:\s]*([\u4e00-\u9fffA-Za-z0-9]{1,12})", source)
    if called:
        return clean_preferred_name(called.group(1))
    for nickname in _NICKNAME_CANDIDATES:
        if re.match(rf"^{re.escape(nickname)}(?=[，,。！？!?\s])", source):
            return nickname
        if re.search(rf"(?:谢谢|感谢|麻烦|辛苦)你?[，, ]*{re.escape(nickname)}", source):
            return nickname
    return ""


def _connect(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _rate(rows):
    if not rows:
        return None
    helpful = sum(1 for row in rows if row["feedback"] == "helpful")
    return helpful / len(rows)


def _avg_response_len(rows):
    return mean(len(row["response"] or "") for row in rows) if rows else 0


_NEGATIVE_CODES = {1, 3, 4, 5}


def _negative_load(rows):
    """负面情绪的强度和，用来比较对话开头和结尾。"""
    return sum((row["intensity"] or 0) for row in rows
               if row["emotion_code"] in _NEGATIVE_CODES)


def _trajectory_stats(conn, user_id):
    """按对话比较开头与结尾，找“负面明显→趋于平稳”的片段。"""
    try:
        rows = conn.execute(
            """SELECT conversation_id, id, persona, emotion_code, intensity,
                      response, suggestion, follow_up, feedback
               FROM checkins
               WHERE user_id = ? AND conversation_id IS NOT NULL
               ORDER BY conversation_id, id""",
            (user_id,),
        ).fetchall()
    except Exception:
        # 旧库无 conversation_id 时跳过。
        return None

    groups = {}
    for row in rows:
        groups.setdefault(row["conversation_id"], []).append(row)

    improved = []
    worked_rows = []
    for items in groups.values():
        if len(items) < 4:
            continue
        first, last = items[:2], items[-2:]
        first_neg = sum(1 for row in first if row["emotion_code"] in _NEGATIVE_CODES)
        last_neg = sum(1 for row in last if row["emotion_code"] in _NEGATIVE_CODES)
        first_load, last_load = _negative_load(first), _negative_load(last)
        if first_neg and (last_neg == 0 or last_load <= first_load - 2):
            improved.append(items)
            worked_rows.extend(last)

    if not improved:
        return {
            "conversations": len([g for g in groups.values() if len(g) >= 4]),
            "improved": 0,
            "improvementRate": 0.0,
        }

    analyzed = len([g for g in groups.values() if len(g) >= 4])
    return {
        "conversations": analyzed,
        "improved": len(improved),
        "improvementRate": round(len(improved) / analyzed, 2) if analyzed else 0.0,
        "workedPersona": _dominant_persona(worked_rows),
        "workedWithoutSuggestion": _majority_empty(worked_rows, "suggestion"),
        "workedWithFollowup": _majority_nonempty(worked_rows, "follow_up"),
    }


def _dominant_persona(rows):
    counts = {}
    for row in rows:
        persona = row["persona"] or ""
        if persona:
            counts[persona] = counts.get(persona, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _majority_empty(rows, field):
    return bool(rows) and sum(1 for row in rows if not (row[field] or "").strip()) / len(rows) >= 0.6


def _majority_nonempty(rows, field):
    return bool(rows) and sum(1 for row in rows if (row[field] or "").strip()) / len(rows) >= 0.6


def _trajectory_hints(trajectory):
    if not trajectory or trajectory.get("improved", 0) < 1:
        return []
    hints = [
        f"有 {trajectory['improved']}/{trajectory['conversations']} 段对话从负面转向平稳，可延续当时的回应节奏"
    ]
    if trajectory.get("workedWithoutSuggestion"):
        hints.append("这些对话更多发生在先陪伴、少给方案时")
    elif trajectory.get("workedPersona") == "gentle":
        hints.append("这些对话更多使用温柔陪伴的表达")
    elif trajectory.get("workedPersona") == "cool":
        hints.append("这些对话更多使用酷酷解决的表达")
    elif trajectory.get("workedWithFollowup"):
        hints.append("这些对话里更多出现追问")
    return hints


def build_profile(db_path, user_id, limit=60):
    """返回最近反馈形成的轻量偏好。

    只统计用户主动点过 helpful / not_helpful 的记录，样本不足时明确返回
    “暂不调整”，避免用一两次点击就给人下结论。
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT persona, response, suggestion, follow_up, feedback, emotion
               FROM checkins
               WHERE user_id = ? AND feedback IN ('helpful', 'not_helpful')
               ORDER BY id DESC LIMIT ?""",
            (user_id, max(1, min(int(limit or 60), 200))),
        ).fetchall()
        trajectory = _trajectory_stats(conn, user_id)
    finally:
        conn.close()

    trajectory_hints = _trajectory_hints(trajectory)
    if not rows:
        return {
            "feedbackCount": 0,
            "helpfulRate": None,
            "hints": trajectory_hints,
            "personaStats": {},
            "preferredPersona": None,
            "trajectory": trajectory,
            "summary": "；".join(trajectory_hints[:2]) or "还没有足够的反馈，先按当前人设自然回应。",
        }

    helpful_rows = [row for row in rows if row["feedback"] == "helpful"]
    unhelpful_rows = [row for row in rows if row["feedback"] == "not_helpful"]
    helpful_rate = _rate(rows)
    hints = list(trajectory_hints)

    # 回复长度
    if len(helpful_rows) >= 2 and len(unhelpful_rows) >= 2:
        helpful_len = _avg_response_len(helpful_rows)
        unhelpful_len = _avg_response_len(unhelpful_rows)
        if helpful_len - unhelpful_len >= 25:
            hints.append("更接受展开一些的回应，不要只回一句")
        elif unhelpful_len - helpful_len >= 25:
            hints.append("更接受简短回应，先别铺陈")

    # 建议密度
    with_suggestion = [row for row in rows if (row["suggestion"] or "").strip()]
    without_suggestion = [row for row in rows if not (row["suggestion"] or "").strip()]
    if len(with_suggestion) >= 1 and len(without_suggestion) >= 1 and len(rows) >= 3:
        with_rate = _rate(with_suggestion)
        without_rate = _rate(without_suggestion)
        if without_rate - with_rate >= 0.25:
            hints.append("更接受先陪伴、少给方案")
        elif with_rate - without_rate >= 0.25:
            hints.append("更接受具体可执行的建议")

    # 追问方式
    with_followup = [row for row in rows if (row["follow_up"] or "").strip()]
    without_followup = [row for row in rows if not (row["follow_up"] or "").strip()]
    if len(with_followup) >= 1 and len(without_followup) >= 1 and len(rows) >= 3:
        with_rate = _rate(with_followup)
        without_rate = _rate(without_followup)
        if with_rate - without_rate >= 0.25:
            hints.append("更愿意被追问一个具体细节")
        elif without_rate - with_rate >= 0.25:
            hints.append("不太喜欢被连续追问")

    # 人设偏好，不自动切换
    persona_stats = {}
    for persona in ("gentle", "cool"):
        group = [row for row in rows if (row["persona"] or "") == persona]
        if len(group) >= 3:
            persona_stats[persona] = {
                "count": len(group),
                "helpfulRate": round(_rate(group), 2),
            }
    preferred_persona = None
    if len(persona_stats) == 2:
        gentle_rate = persona_stats["gentle"]["helpfulRate"]
        cool_rate = persona_stats["cool"]["helpfulRate"]
        if cool_rate - gentle_rate >= 0.15:
            preferred_persona = "cool"
            hints.append("对酷酷解决型的反馈更好")
        elif gentle_rate - cool_rate >= 0.15:
            preferred_persona = "gentle"
            hints.append("对温柔陪伴型的反馈更好")

    summary = "；".join(hints[:2]) if hints else "反馈还不够稳定，继续按当前人设自然回应。"
    return {
        "feedbackCount": len(rows),
        "helpfulRate": round(helpful_rate, 2),
        "hints": hints,
        "personaStats": persona_stats,
        "preferredPersona": preferred_persona,
        "trajectory": trajectory,
        "summary": summary,
    }


def format_for_prompt(profile):
    """把统计结果压缩成给模型的软约束；没有足够样本就返回 None。"""
    if not profile or not profile.get("hints"):
        return None
    result = {
        "反馈条数": profile["feedbackCount"],
        "整体有帮助率": profile["helpfulRate"],
        "表达偏好": profile.get("hints") or ["暂无明显偏好"],
        "说明": (
            "这是用户主动反馈形成的表达偏好，不是心理诊断。"
            "只调整语气、长度、建议密度和追问方式；不要向用户提及画像或统计，"
            "不要据此改变用户当前选择的人设，也不要为了迎合而编造或过度承诺。"
        ),
    }
    if profile.get("trajectory"):
        result["对话轨迹"] = profile["trajectory"]
    return result
