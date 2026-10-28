import datetime
import os
import sqlite3
from contextlib import contextmanager

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from ai_coach import analyze as ai_analyze, generate_insights
from llm_client import describe as llm_describe

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "emotion_planet.db")
app = Flask(__name__)
app.secret_key = os.environ.get("EMOTION_SECRET_KEY", "emotion-planet-pure-web-dev-key")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

CONTEXTS = {"study", "family", "peers", "sensory", "daily", "other"}


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                input_text TEXT NOT NULL,
                context TEXT NOT NULL,
                sleep_quality INTEGER,
                emotion TEXT NOT NULL,
                emotion_code INTEGER NOT NULL,
                intensity INTEGER NOT NULL,
                confidence REAL NOT NULL,
                summary TEXT NOT NULL,
                response TEXT NOT NULL,
                suggestion TEXT NOT NULL,
                follow_up TEXT,
                triggers_json TEXT NOT NULL DEFAULT '[]',
                risk_level TEXT NOT NULL,
                source TEXT NOT NULL,
                model TEXT,
                feedback TEXT,
                feedback_at TEXT
            )
        """)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(checkins)")}
        if "conversation_id" not in columns:
            conn.execute("ALTER TABLE checkins ADD COLUMN conversation_id INTEGER")
        # 老版本数据归入每个用户自己的“往期对话”，不跨账号串话题。
        users = conn.execute("SELECT DISTINCT user_id FROM checkins").fetchall()
        for (uid,) in users:
            exists = conn.execute("SELECT id FROM conversations WHERE user_id = ? LIMIT 1", (uid,)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (?, '往期对话', datetime('now','localtime'), datetime('now','localtime'))", (uid,)
                )
        conn.execute("""
            UPDATE checkins SET conversation_id = (
                SELECT id FROM conversations WHERE user_id = checkins.user_id ORDER BY id LIMIT 1
            ) WHERE conversation_id IS NULL OR conversation_id NOT IN (
                SELECT id FROM conversations WHERE user_id = checkins.user_id
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkins_user_time ON checkins(user_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checkins_conversation ON checkins(conversation_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at)")


init_db()


def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    with get_db() as conn:
        row = conn.execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
    return (row["id"], row["username"]) if row else None


def require_user():
    user = current_user()
    if not user:
        raise PermissionError("请先登录")
    return user


@app.errorhandler(PermissionError)
def unauthorized(error):
    return jsonify({"ok": False, "error": str(error)}), 401


def checkin_dict(row):
    import json
    return {
        "id": row["id"], "conversationId": row["conversation_id"],
        "createdAt": row["created_at"], "text": row["input_text"],
        "context": row["context"], "sleepQuality": row["sleep_quality"],
        "emotion": row["emotion"], "emotionCode": row["emotion_code"],
        "intensity": row["intensity"], "confidence": row["confidence"],
        "summary": row["summary"], "response": row["response"],
        "suggestion": row["suggestion"], "followUpQuestion": row["follow_up"],
        "triggers": json.loads(row["triggers_json"] or "[]"),
        "riskLevel": row["risk_level"], "source": row["source"], "model": row["model"],
        "feedback": row["feedback"], "feedbackAt": row["feedback_at"],
    }


@app.route("/api/auth/register", methods=["POST"])
def register():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    if not 2 <= len(username) <= 20:
        return jsonify({"ok": False, "error": "用户名需 2～20 个字符"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "密码至少 6 位"}), 400
    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, datetime('now','localtime'))",
                (username, generate_password_hash(password)),
            )
            uid = cursor.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "用户名已存在"}), 409
    session.clear()
    session["user_id"] = uid
    return jsonify({"ok": True, "username": username})


@app.route("/api/auth/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (body.get("username"),)).fetchone()
    if not row or not check_password_hash(row["password_hash"], str(body.get("password") or "")):
        return jsonify({"ok": False, "error": "用户名或密码错误"}), 401
    session.clear()
    session["user_id"] = row["id"]
    return jsonify({"ok": True, "username": row["username"]})


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/account", methods=["DELETE"])
def delete_account():
    user = require_user()
    body = request.get_json(silent=True) or {}
    password = str(body.get("password") or "")
    with get_db() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user[0],)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "账号不存在"}), 404
        if not password or not check_password_hash(row["password_hash"], password):
            return jsonify({"ok": False, "error": "密码不正确，账号未注销"}), 403
        conn.execute("DELETE FROM checkins WHERE user_id = ?", (user[0],))
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (user[0],))
        conn.execute("DELETE FROM users WHERE id = ?", (user[0],))
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def me():
    user = current_user()
    return jsonify({"ok": True, "username": user[1] if user else None})


@app.route("/api/ai/status")
def ai_status():
    return jsonify({"ok": True, "llm": llm_describe()})


def conversation_dict(row):
    return {
        "id": row["id"], "title": row["title"], "createdAt": row["created_at"],
        "updatedAt": row["updated_at"], "preview": row["preview"] or "还没有消息",
    }


@app.route("/api/conversations")
def conversations():
    user = require_user()
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.*, (SELECT input_text FROM checkins WHERE conversation_id = c.id
                         ORDER BY id DESC LIMIT 1) AS preview
            FROM conversations c WHERE c.user_id = ? ORDER BY c.updated_at DESC, c.id DESC
        """, (user[0],)).fetchall()
    return jsonify({"ok": True, "conversations": [conversation_dict(row) for row in rows]})


@app.route("/api/conversations", methods=["POST"])
def create_conversation():
    user = require_user()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO conversations (user_id, title, created_at, updated_at) "
            "VALUES (?, '新话题', datetime('now','localtime'), datetime('now','localtime'))", (user[0],)
        )
        row = conn.execute("SELECT id, title, created_at, updated_at, NULL AS preview FROM conversations WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify({"ok": True, "conversation": conversation_dict(row)})


@app.route("/api/conversations/<int:conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    user = require_user()
    with get_db() as conn:
        owner = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user[0])
        ).fetchone()
        if not owner:
            return jsonify({"ok": False, "error": "话题不存在"}), 404
        conn.execute(
            "DELETE FROM checkins WHERE conversation_id = ? AND user_id = ?", (conversation_id, user[0])
        )
        conn.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user[0]))
    return jsonify({"ok": True})


@app.route("/api/conversations/<int:conversation_id>/messages")
def conversation_messages(conversation_id):
    user = require_user()
    with get_db() as conn:
        owner = conn.execute("SELECT id FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user[0])).fetchone()
        if not owner:
            return jsonify({"ok": False, "error": "话题不存在"}), 404
        rows = conn.execute("SELECT * FROM checkins WHERE conversation_id = ? ORDER BY id", (conversation_id,)).fetchall()
    return jsonify({"ok": True, "records": [checkin_dict(row) for row in rows]})


@app.route("/api/chat", methods=["POST"])
def chat():
    user = require_user()
    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "").strip()
    context = str(body.get("context") or "daily")
    sleep = body.get("sleepQuality")
    if not 2 <= len(text) <= 1000:
        return jsonify({"ok": False, "error": "请输入 2～1000 字"}), 400
    if context not in CONTEXTS:
        return jsonify({"ok": False, "error": "未知情境"}), 400
    if sleep not in (None, "", 1, 2, 3, 4, 5):
        return jsonify({"ok": False, "error": "睡眠质量应为 1～5"}), 400
    sleep = int(sleep) if sleep not in (None, "") else None
    with get_db() as conn:
        raw_conversation_id = body.get("conversationId")
        if raw_conversation_id not in (None, ""):
            try:
                conversation_id = int(raw_conversation_id)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "话题编号无效"}), 400
            conversation = conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user[0])
            ).fetchone()
            if not conversation:
                return jsonify({"ok": False, "error": "话题不存在"}), 404
        else:
            conversation = conn.execute(
                "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC, id DESC LIMIT 1", (user[0],)
            ).fetchone()
            if not conversation:
                cursor = conn.execute(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (?, ?, datetime('now','localtime'), datetime('now','localtime'))",
                    (user[0], text.replace("\n", " ")[:20]),
                )
                conversation_id = cursor.lastrowid
            else:
                conversation_id = conversation["id"]
        rows = conn.execute(
            "SELECT input_text, response, emotion, feedback FROM checkins "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT 8", (conversation_id,),
        ).fetchall()
    history = [{
        "用户": row["input_text"], "AI": row["response"],
        "情绪": row["emotion"], "建议反馈": row["feedback"] or "未反馈",
    } for row in reversed(rows)]
    try:
        result = ai_analyze({"text": text, "taskContext": context,
                             "sleepQuality": sleep, "history": history})
    except Exception as exc:
        print(f"[AI] 分析失败：{exc}")
        return jsonify({"ok": False, "error": f"AI 分析失败：{exc}"}), 503
    import json
    with get_db() as conn:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user[0])
        ).fetchone()
        if not conversation:
            return jsonify({"ok": False, "error": "话题已被删除，本次回复未保存"}), 404
        cursor = conn.execute("""
            INSERT INTO checkins (
                user_id, conversation_id, created_at, input_text, context, sleep_quality, emotion,
                emotion_code, intensity, confidence, summary, response, suggestion,
                follow_up, triggers_json, risk_level, source, model
            ) VALUES (?, ?, datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user[0], conversation_id, text, context, sleep, result["emotion"], result["emotionCode"],
              result["intensity"], result["confidence"], result["summary"], result["response"],
              result["suggestion"], result["followUpQuestion"], json.dumps(result["triggers"], ensure_ascii=False),
              result["riskLevel"], result["source"], result.get("model")))
        checkin_id = cursor.lastrowid
        conn.execute(
            "UPDATE conversations SET title = CASE WHEN title = '新话题' THEN ? ELSE title END, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (text.replace("\n", " ")[:20], conversation_id),
        )
        row = conn.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
    return jsonify({"ok": True, "result": checkin_dict(row)})


@app.route("/api/checkins/<int:checkin_id>/feedback", methods=["POST"])
def feedback(checkin_id):
    user = require_user()
    body = request.get_json(silent=True) or {}
    value = body.get("feedback")
    if value not in ("helpful", "not_helpful"):
        return jsonify({"ok": False, "error": "反馈值无效"}), 400
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE checkins SET feedback = ?, feedback_at = datetime('now','localtime') "
            "WHERE id = ? AND user_id = ?", (value, checkin_id, user[0]),
        )
    if not cursor.rowcount:
        return jsonify({"ok": False, "error": "记录不存在"}), 404
    return jsonify({"ok": True, "feedback": value})


@app.route("/api/history")
def history():
    user = require_user()
    try:
        limit = max(1, min(int(request.args.get("limit", 30)), 100))
    except ValueError:
        limit = 30
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM checkins WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user[0], limit),
        ).fetchall()
    return jsonify({"ok": True, "records": [checkin_dict(row) for row in rows]})


def build_stats(user_id):
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM checkins WHERE user_id = ?", (user_id,)).fetchone()[0]
        distribution = conn.execute(
            "SELECT emotion, COUNT(*) AS count FROM checkins WHERE user_id = ? "
            "AND created_at >= datetime('now','localtime','-30 days') GROUP BY emotion",
            (user_id,),
        ).fetchall()
        daily = conn.execute(
            "SELECT date(created_at) AS day, emotion_code, COUNT(*) AS count FROM checkins "
            "WHERE user_id = ? AND created_at >= datetime('now','localtime','-6 days') "
            "GROUP BY day, emotion_code ORDER BY day", (user_id,),
        ).fetchall()
        contexts = conn.execute(
            "SELECT context, COUNT(*) AS count FROM checkins WHERE user_id = ? "
            "GROUP BY context ORDER BY count DESC", (user_id,),
        ).fetchall()
        feedback_rows = conn.execute(
            "SELECT feedback, COUNT(*) AS count FROM checkins WHERE user_id = ? "
            "AND feedback IS NOT NULL GROUP BY feedback", (user_id,),
        ).fetchall()
        recent = conn.execute(
            "SELECT summary, emotion, triggers_json, feedback, created_at FROM checkins "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,),
        ).fetchall()
    feedback_map = {row["feedback"]: row["count"] for row in feedback_rows}
    feedback_total = sum(feedback_map.values())
    return {
        "total": total,
        "distribution": {row["emotion"]: row["count"] for row in distribution},
        "daily": [dict(row) for row in daily],
        "contexts": {row["context"]: row["count"] for row in contexts},
        "feedbackTotal": feedback_total,
        "helpfulRate": round(feedback_map.get("helpful", 0) / feedback_total, 3) if feedback_total else None,
        "recent": [dict(row) for row in recent],
    }


@app.route("/api/insights")
def insights():
    user = require_user()
    stats = build_stats(user[0])
    stats.pop("recent", None)
    return jsonify({"ok": True, "stats": stats})


@app.route("/api/insights/ai", methods=["POST"])
def ai_insights():
    user = require_user()
    stats = build_stats(user[0])
    if stats["total"] < 3:
        return jsonify({"ok": False, "error": "至少完成 3 次记录后，才能生成有边界的阶段回顾"}), 400
    try:
        result = generate_insights(stats)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"AI 回顾失败：{exc}"}), 503
    return jsonify({"ok": True, "report": result})


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "emotion-planet.html" if current_user() else "login.html")


@app.route("/<path:filename>")
def static_files(filename):
    if not filename.lower().endswith((".html", ".js", ".css", ".png", ".jpg", ".svg", ".glb")):
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    print("情绪星球 · 纯软件 AI Agent：http://127.0.0.1:5001")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5001")), debug=False)
