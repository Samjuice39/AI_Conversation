# coding: utf-8
"""从用户的历史反馈中提取表达偏好。

这里只做行为统计，不推断人格、不诊断心理状态。统计结果用于调整
回复长度、建议密度和追问方式，不能覆盖用户当前显式选择的人设。
"""
from statistics import mean


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
    finally:
        conn.close()

    if not rows:
        return {
            "feedbackCount": 0,
            "helpfulRate": None,
            "hints": [],
            "personaStats": {},
            "preferredPersona": None,
            "summary": "还没有足够的反馈，先按当前人设自然回应。",
        }

    helpful_rows = [row for row in rows if row["feedback"] == "helpful"]
    unhelpful_rows = [row for row in rows if row["feedback"] == "not_helpful"]
    helpful_rate = _rate(rows)
    hints = []

    # 1. 回复长度：只有两类样本都够时才比较，避免小样本噪声。
    if len(helpful_rows) >= 2 and len(unhelpful_rows) >= 2:
        helpful_len = _avg_response_len(helpful_rows)
        unhelpful_len = _avg_response_len(unhelpful_rows)
        if helpful_len - unhelpful_len >= 25:
            hints.append("更接受展开一些的回应，不要只回一句")
        elif unhelpful_len - helpful_len >= 25:
            hints.append("更接受简短回应，先别铺陈")

    # 2. 建议密度：有建议 vs 没建议的满意度差异。
    # 至少各一条、总共三条才比较，避免单次点击造成误判。
    with_suggestion = [row for row in rows if (row["suggestion"] or "").strip()]
    without_suggestion = [row for row in rows if not (row["suggestion"] or "").strip()]
    if len(with_suggestion) >= 1 and len(without_suggestion) >= 1 and len(rows) >= 3:
        with_rate = _rate(with_suggestion)
        without_rate = _rate(without_suggestion)
        if without_rate - with_rate >= 0.25:
            hints.append("更接受先陪伴、少给方案")
        elif with_rate - without_rate >= 0.25:
            hints.append("更接受具体可执行的建议")

    # 3. 追问方式：有追问 vs 没追问的满意度差异。
    with_followup = [row for row in rows if (row["follow_up"] or "").strip()]
    without_followup = [row for row in rows if not (row["follow_up"] or "").strip()]
    if len(with_followup) >= 1 and len(without_followup) >= 1 and len(rows) >= 3:
        with_rate = _rate(with_followup)
        without_rate = _rate(without_followup)
        if with_rate - without_rate >= 0.25:
            hints.append("更愿意被追问一个具体细节")
        elif without_rate - with_rate >= 0.25:
            hints.append("不太喜欢被连续追问")

    # 4. 人设偏好：只作为提示，不自动替用户切换。
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
        "summary": summary,
    }


def format_for_prompt(profile):
    """把统计结果压缩成给模型的软约束；没有足够样本就返回 None。"""
    if not profile or profile.get("feedbackCount", 0) < 2 or not profile.get("hints"):
        return None
    return {
        "反馈条数": profile["feedbackCount"],
        "整体有帮助率": profile["helpfulRate"],
        "表达偏好": profile.get("hints") or ["暂无明显偏好"],
        "说明": (
            "这是用户主动反馈形成的表达偏好，不是心理诊断。"
            "只调整语气、长度、建议密度和追问方式；不要向用户提及画像或统计，"
            "不要据此改变用户当前选择的人设，也不要为了迎合而编造或过度承诺。"
        ),
    }
