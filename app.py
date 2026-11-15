import datetime
import importlib.util
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager

from flask import (Flask, Response, jsonify, request, send_from_directory,
                   session, stream_with_context)
from werkzeug.security import check_password_hash, generate_password_hash

import agent_workflow
from ai_coach import (CRISIS_PATTERN, analyze as ai_analyze, analyze_stream,
                      generate_insights, validate_result)
from llm_client import describe as llm_describe
from urllib.parse import urlparse
from user_profile import (build_profile, clean_preferred_name, format_for_prompt,
                          infer_nickname)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "emotion_planet.db")
app = Flask(__name__)


def _load_env_file(path):
    """读取 .env；已存在的环境变量优先。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(os.path.join(BASE_DIR, ".env"))

# Agent 工作流开关；设为 0 时退回单轮分析。
AGENT_MODE = os.environ.get("AGENT_MODE", "1") not in ("0", "false", "False")


def run_analysis(text, context, sleep, history, user_id, persona="gentle",
                 custom_persona="", preferred_name=""):
    """先取数，再生成；取数失败退回单轮。"""
    payload = {"text": text, "taskContext": context, "persona": persona,
               "customPersona": custom_persona, "preferredName": preferred_name,
               "sleepQuality": sleep, "history": history}
    profile = build_profile(DB_PATH, user_id)
    profile_hint = format_for_prompt(profile)
    if profile_hint:
        payload["userProfile"] = profile_hint
    llm_config = get_user_llm_config(user_id)

    facts, trace = [], None
    if AGENT_MODE and llm_config:
        try:
            info = agent_workflow.collect(payload, DB_PATH, user_id, llm_config=llm_config)
            facts, trace = info.get("facts") or [], info.get("trace")
        except Exception as exc:  # noqa: BLE001 - 取数失败就退回纯单轮
            print(f"[Agent] 信息收集失败，按单轮处理：{exc}")

    result = ai_analyze(payload, facts, llm_config=llm_config)
    result["trace"] = trace
    result["personalization"] = profile
    if facts:
        result["source"] = "agent"
    return result

def _load_secret_key():
    """优先用环境变量；否则读本地 .secret_key，没有就生成并落盘。

    不设固定兜底值：硬编码密钥意味着任何人拿到源码就能伪造会话。
    """
    env_key = os.environ.get("EMOTION_SECRET_KEY", "").strip()
    if env_key:
        return env_key
    key_path = os.path.join(BASE_DIR, ".secret_key")
    if os.path.exists(key_path):
        try:
            with open(key_path, "r", encoding="utf-8") as fh:
                stored = fh.read().strip()
        except OSError as exc:
            print(f"[Auth] 读取 .secret_key 失败：{exc}")
            stored = ""
        if stored:
            return stored
    key = secrets.token_hex(32)
    try:
        with open(key_path, "w", encoding="utf-8") as fh:
            fh.write(key)
        os.chmod(key_path, 0o600)
        print("[Auth] 已生成随机会话密钥并写入 .secret_key")
    except OSError as exc:
        print(f"[Auth] 无法写入 .secret_key，本次运行使用临时密钥（重启后需重新登录）：{exc}")
    return key


app.secret_key = _load_secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

CONTEXTS = {"study", "family", "peers", "sensory", "daily", "other"}
PERSONAS = {"gentle", "cool", "custom"}


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
                preferred_name TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_ai_settings (
                user_id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL DEFAULT 'openai',
                api_key TEXT NOT NULL,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL,
                timeout REAL NOT NULL DEFAULT 60,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
        if "persona" not in columns:
            conn.execute("ALTER TABLE checkins ADD COLUMN persona TEXT")
        if "trace_json" not in columns:
            conn.execute("ALTER TABLE checkins ADD COLUMN trace_json TEXT")
        user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "preferred_name" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN preferred_name TEXT")
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
    return {
        "id": row["id"], "conversationId": row["conversation_id"],
        "createdAt": row["created_at"], "text": row["input_text"],
        "context": row["context"], "sleepQuality": row["sleep_quality"],
        "emotion": row["emotion"], "emotionCode": row["emotion_code"],
        "intensity": row["intensity"], "confidence": row["confidence"],
        "summary": row["summary"], "response": row["response"],
        "suggestion": row["suggestion"], "followUpQuestion": row["follow_up"],
        "persona": row["persona"],
        "trace": json.loads(row["trace_json"] or "[]"),
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
        conn.execute("DELETE FROM user_ai_settings WHERE user_id = ?", (user[0],))
        conn.execute("DELETE FROM users WHERE id = ?", (user[0],))
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def me():
    user = current_user()
    preferred_name = ""
    if user:
        with get_db() as conn:
            row = conn.execute("SELECT preferred_name FROM users WHERE id = ?", (user[0],)).fetchone()
            preferred_name = row["preferred_name"] if row and row["preferred_name"] else ""
    return jsonify({"ok": True, "username": user[1] if user else None,
                    "preferredName": preferred_name})


def get_server_llm_config():
    """部署方在 .env 里配一次，没填自己 API 的账号共用这份默认配置。"""
    api_key = os.environ.get("EMOTION_LLM_API_KEY", "").strip()
    model = os.environ.get("EMOTION_LLM_MODEL", "").strip()
    if not api_key or not model:
        return None
    try:
        timeout = float(os.environ.get("EMOTION_LLM_TIMEOUT", "60") or 60)
    except ValueError:
        timeout = 60.0
    return {
        "provider": os.environ.get("EMOTION_LLM_PROVIDER", "openai").strip() or "openai",
        "apiKey": api_key, "model": model,
        "baseUrl": os.environ.get("EMOTION_LLM_BASE_URL", "").strip().rstrip("/"),
        "timeout": timeout, "source": "server",
    }


def get_own_llm_config(user_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT provider, api_key, model, base_url, timeout FROM user_ai_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row or not row["api_key"] or not row["model"]:
        return None
    return {
        "provider": row["provider"], "apiKey": row["api_key"], "model": row["model"],
        "baseUrl": row["base_url"], "timeout": row["timeout"], "source": "own",
    }


def get_user_llm_config(user_id):
    """账号自己的配置优先；没配就走部署方的默认模型，不要求用户自带 key。"""
    return get_own_llm_config(user_id) or get_server_llm_config()


def public_ai_settings(user_id):
    # enabled 只表示「这个账号有没有自己的配置」，默认模型不影响它。
    config = get_own_llm_config(user_id)
    default = get_server_llm_config()
    return {
        "enabled": bool(config), "provider": "openai",
        "baseUrl": config["baseUrl"] if config else "https://api.deepseek.com/v1",
        "model": config["model"] if config else "deepseek-chat",
        "hasApiKey": bool(config),
        "serverDefault": bool(default),
        "serverModel": default["model"] if default else "",
    }


@app.route("/api/ai/status")
def ai_status():
    user = require_user()
    own = get_own_llm_config(user[0])
    config = own or get_server_llm_config()
    description = llm_describe(config or {"provider": "none"})
    if own:
        description["note"] = "使用当前账号的大模型配置"
        description["source"] = "own"
    elif config:
        description["note"] = "使用本站默认模型，无需自行配置"
        description["source"] = "server"
    else:
        description["note"] = "未配置模型，使用本地规则"
        description["source"] = "fallback"
    return jsonify({"ok": True, "llm": description})


@app.route("/api/ai/settings", methods=["GET", "PUT", "DELETE"])
def ai_settings():
    user = require_user()
    if request.method == "GET":
        return jsonify({"ok": True, "settings": public_ai_settings(user[0])})
    if request.method == "DELETE":
        with get_db() as conn:
            conn.execute("DELETE FROM user_ai_settings WHERE user_id = ?", (user[0],))
        return jsonify({"ok": True})

    body = request.get_json(silent=True) or {}
    api_key = str(body.get("apiKey") or "").strip()
    model = str(body.get("model") or "").strip()
    base_url = str(body.get("baseUrl") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    local_host = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        return jsonify({"ok": False, "error": "请填写完整的 API 地址，例如 https://api.deepseek.com/v1"}), 400
    if parsed.scheme != "https" and not local_host:
        return jsonify({"ok": False, "error": "远程 API 地址必须使用 HTTPS；本地服务可使用 HTTP"}), 400
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return jsonify({"ok": False, "error": "API 地址不能包含账号、密码、查询参数或锚点"}), 400
    if len(base_url) > 300 or not 1 <= len(model) <= 100 or len(api_key) > 500:
        return jsonify({"ok": False, "error": "模型名称或 API 地址格式不正确"}), 400
    # 这里只能读账号自己的配置：读到默认模型会把部署方的 key 写进用户表。
    existing = get_own_llm_config(user[0])
    if not api_key and not existing:
        return jsonify({"ok": False, "error": "要换用自己的模型时请填写 API Key；也可以直接取消，继续使用默认模型"}), 400
    api_key = api_key or existing["apiKey"]
    with get_db() as conn:
        conn.execute("""
            INSERT INTO user_ai_settings (user_id, provider, api_key, model, base_url, timeout, updated_at)
            VALUES (?, 'openai', ?, ?, ?, 60, datetime('now','localtime'))
            ON CONFLICT(user_id) DO UPDATE SET
                provider='openai', api_key=excluded.api_key, model=excluded.model,
                base_url=excluded.base_url, timeout=60, updated_at=excluded.updated_at
        """, (user[0], api_key, model, base_url))
    return jsonify({"ok": True, "settings": public_ai_settings(user[0])})


# ---------------- 本地语音依赖自检（缺什么自动装什么） ----------------
# 目标：别人拿到项目只要 python app.py，缺 funasr/torch 时自动补装；
# 装不上也不影响文字对话，语音自动退回浏览器在线识别。
ASR_AUTO_INSTALL = os.environ.get("ASR_AUTO_INSTALL", "1").lower() not in ("0", "false", "no", "off")
# 顺序有讲究：先 torch，再 torchaudio，最后 funasr（funasr 依赖前两者）。
_ASR_DEPS_MODULES = (("torch", "torch"), ("torchaudio", "torchaudio"), ("funasr", "funasr"))
_asr_deps_lock = threading.Lock()
_ASR_DEPS_STARTED = False
_asr_deps = {
    "state": "unknown",  # unknown / present / installing / installed / failed / missing / disabled
    "missing": [], "message": "", "updatedAt": None, "autoInstall": ASR_AUTO_INSTALL,
}


def asr_deps_missing():
    """只查包是否存在，不真正导入，调用很快。"""
    missing = []
    for module, package in _ASR_DEPS_MODULES:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(package)
    return missing


def asr_deps_state():
    with _asr_deps_lock:
        return dict(_asr_deps)


def _set_asr_deps(**kwargs):
    with _asr_deps_lock:
        _asr_deps.update(kwargs)
        _asr_deps["updatedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pip_install(packages):
    """用当前解释器装包，避免装到另一个 Python 上。"""
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *packages]
    print(f"[ASR] 开始安装缺少的依赖：{' '.join(packages)}")
    print(f"[ASR] 执行：{' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 - 起不了 pip 就是装不上
        return False, f"无法执行 pip：{exc}"
    if proc.returncode == 0:
        return True, "安装成功"
    tail = ((proc.stderr or "") + (proc.stdout or "")).strip()[-600:]
    return False, tail or f"pip 退出码 {proc.returncode}"


def _asr_deps_worker(missing):
    ok, detail = _pip_install(missing)
    if not ok:
        _set_asr_deps(state="failed",
                      message=f"自动安装失败，语音继续使用浏览器在线识别。{detail}")
        print(f"[ASR] 自动安装失败：{detail}")
        return
    importlib.invalidate_caches()  # 让当前进程能发现刚装好的包
    still = asr_deps_missing()
    if still:
        _set_asr_deps(state="failed", missing=still,
                      message="部分依赖仍未就绪：" + " ".join(still))
        print(f"[ASR] 装完仍缺少：{still}")
        return
    _set_asr_deps(state="installed", missing=[], message="依赖已就绪")
    print("[ASR] 依赖安装完成，本地语音可用")


def start_asr_deps_check():
    """启动时自检；缺依赖就后台补装，不阻塞 Web 服务启动。重复调用无副作用。"""
    global _ASR_DEPS_STARTED
    with _asr_deps_lock:
        if _ASR_DEPS_STARTED:
            return
        _ASR_DEPS_STARTED = True
    if not ASR_ENABLED:
        _set_asr_deps(state="disabled", message="ASR_MODE 已关闭本地语音")
        return
    missing = asr_deps_missing()
    if not missing:
        _set_asr_deps(state="present", missing=[], message="")
        return
    if not ASR_AUTO_INSTALL:
        _set_asr_deps(state="missing", missing=missing,
                      message="缺少本地语音依赖：" + " ".join(missing)
                              + "（ASR_AUTO_INSTALL=0，未自动安装）")
        return
    _set_asr_deps(state="installing", missing=missing,
                  message="正在后台安装：" + " ".join(missing) + "（首次约几分钟，不影响文字对话）")
    threading.Thread(target=_asr_deps_worker, args=(missing,), daemon=True).start()


def wait_asr_deps(timeout=900):
    """等依赖状态脱离 installing，预热模型前用。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = asr_deps_state()["state"]
        if state != "installing":
            return state
        time.sleep(3)
    return "timeout"


# ---------------- 本地语音识别（SenseVoice-Small，离线推理） ----------------
# 完全跑在本机，不经过任何在线语音服务。前端把麦克风录音转成 16k 单声道 WAV
# 传上来，这里落临时文件后交给 sensevoice_asr 识别，识别完就删。
ASR_MAX_BYTES = int(os.environ.get("ASR_MAX_BYTES", 20 * 1024 * 1024))
ASR_DEVICE = os.environ.get("ASR_DEVICE", "cpu")
# ASR_MODE=off 可以整体关掉，前端会自动退回浏览器在线识别。
ASR_ENABLED = os.environ.get("ASR_MODE", "local").lower() not in ("off", "0", "false", "no")
# SenseVoice 是 CPU 推理，几个请求同时进来只会互相抢核心，串行更稳。
_asr_lock = threading.Lock()


def _asr_module():
    """按需导入：没装 funasr 时不影响应用启动，只是语音功能不可用。"""
    import sensevoice_asr
    return sensevoice_asr


@app.route("/api/asr/status")
def asr_status():
    require_user()
    deps = asr_deps_state()
    info = {"engine": "SenseVoice-Small", "available": False, "loaded": False,
            "cached": False, "source": "unknown", "device": ASR_DEVICE,
            "reason": "", "deps": deps}
    if not ASR_ENABLED:
        info["reason"] = "已通过环境变量 ASR_MODE 关闭"
        return jsonify({"ok": True, "asr": info})
    try:
        module = _asr_module()
    except Exception as exc:  # noqa: BLE001 - 缺依赖时给出可读原因
        if deps["state"] == "installing":
            info["reason"] = "正在后台安装本地语音依赖，装好后刷新页面即可使用"
        else:
            info["reason"] = deps.get("message") or f"本地识别依赖未就绪（{exc}）"
        return jsonify({"ok": True, "asr": info})
    info["available"] = True
    info["cached"] = bool(module.local_weight_path())
    info["source"] = module.model_source()
    info["loaded"] = module.is_loaded()
    if info["source"] == "cache":
        info["reason"] = "使用本机 modelscope 缓存，权重未随项目打包"
    elif info["source"] == "download":
        info["reason"] = "未发现本地权重，首次识别会联网下载约 900MB"
    return jsonify({"ok": True, "asr": info})


@app.route("/api/asr", methods=["POST"])
def asr_transcribe():
    require_user()
    if not ASR_ENABLED:
        return jsonify({"ok": False, "error": "本地语音识别已关闭"}), 503

    upload = request.files.get("audio")
    if upload is None:
        return jsonify({"ok": False, "error": "没有收到音频数据"}), 400
    data = upload.read(ASR_MAX_BYTES + 1)
    if len(data) > ASR_MAX_BYTES:
        return jsonify({"ok": False, "error": "录音文件过大，请控制在 20MB 以内"}), 413
    if len(data) < 3200:  # 16k/16bit 单声道，约 0.1 秒
        return jsonify({"ok": False, "error": "录音太短，请说完再停止"}), 400

    try:
        module = _asr_module()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"本地识别不可用：{exc}"}), 503

    language = str(request.form.get("language") or "auto").strip()
    if language not in module.LANGUAGES:
        language = "auto"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        with _asr_lock:
            result = module.transcribe(tmp_path, language=language, device=ASR_DEVICE)
    except Exception as exc:  # noqa: BLE001
        print(f"[ASR] 识别失败：{type(exc).__name__}: {exc}")
        return jsonify({"ok": False, "error": f"识别失败：{exc}"}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return jsonify({
        "ok": True, "engine": "sensevoice-local",
        "text": result["text"], "language": result["language"],
        "languageText": result["language_text"], "emotion": result["emotion"],
        "emotionText": result["emotion_text"], "events": result["events"],
        "eventsText": result["events_text"], "elapsed": result["elapsed"],
    })


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


def _parse_chat_request(body):
    """校验并归一化对话请求体；失败返回 (None, (payload, status))。"""
    text = str(body.get("text") or "").strip()
    context = str(body.get("context") or "daily")
    persona = str(body.get("persona") or "gentle")
    custom_persona = re.sub(r"\s+", " ", str(body.get("customPersona") or "")).strip()[:120]
    preferred_name = clean_preferred_name(body.get("preferredName"))
    sleep = body.get("sleepQuality")
    if not 2 <= len(text) <= 1000:
        return None, ({"ok": False, "error": "请输入 2～1000 字"}, 400)
    if context not in CONTEXTS:
        return None, ({"ok": False, "error": "未知情境"}, 400)
    if persona not in PERSONAS:
        return None, ({"ok": False, "error": "未知人设"}, 400)
    if sleep not in (None, "", 1, 2, 3, 4, 5):
        return None, ({"ok": False, "error": "睡眠质量应为 1～5"}, 400)
    return {
        "text": text, "context": context, "persona": persona,
        "customPersona": custom_persona, "preferredName": preferred_name,
        "sleep": int(sleep) if sleep not in (None, "") else None,
        "rawConversationId": body.get("conversationId"),
    }, None


def _prepare_chat_context(user_id, params):
    """解析话题、更新称呼并读取近期历史，返回 (conversation_id, history, preferred_name)。

    话题编号非法抛 ValueError（400），话题不存在抛 LookupError（404）。
    """
    text = params["text"]
    preferred_name = params["preferredName"]
    raw_conversation_id = params["rawConversationId"]
    with get_db() as conn:
        user_row = conn.execute("SELECT preferred_name FROM users WHERE id = ?", (user_id,)).fetchone()
        stored_name = user_row["preferred_name"] if user_row and user_row["preferred_name"] else ""
        if preferred_name:
            if preferred_name != stored_name:
                conn.execute("UPDATE users SET preferred_name = ? WHERE id = ?", (preferred_name, user_id))
        elif not stored_name:
            inferred_name = infer_nickname(text)
            if inferred_name:
                preferred_name = inferred_name
                conn.execute("UPDATE users SET preferred_name = ? WHERE id = ?", (inferred_name, user_id))
        else:
            preferred_name = stored_name
        if raw_conversation_id not in (None, ""):
            try:
                conversation_id = int(raw_conversation_id)
            except (TypeError, ValueError):
                raise ValueError("话题编号无效")
            conversation = conn.execute(
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
            ).fetchone()
            if not conversation:
                raise LookupError("话题不存在")
        else:
            conversation = conn.execute(
                "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC, id DESC LIMIT 1",
                (user_id,)
            ).fetchone()
            if not conversation:
                cursor = conn.execute(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) "
                    "VALUES (?, ?, datetime('now','localtime'), datetime('now','localtime'))",
                    (user_id, text.replace("\n", " ")[:20]),
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
    return conversation_id, history, preferred_name


def _save_checkin(user_id, conversation_id, params, result):
    """写入一条记录并返回前端要的格式；话题已被删除时返回 None。"""
    text = params["text"]
    with get_db() as conn:
        conversation = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?", (conversation_id, user_id)
        ).fetchone()
        if not conversation:
            return None
        cursor = conn.execute("""
            INSERT INTO checkins (
                user_id, conversation_id, created_at, input_text, context, sleep_quality, emotion,
                emotion_code, intensity, confidence, summary, response, suggestion,
                follow_up, triggers_json, risk_level, source, model, persona, trace_json
            ) VALUES (?, ?, datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, conversation_id, text, params["context"], params["sleep"], result["emotion"],
              result["emotionCode"], result["intensity"], result["confidence"], result["summary"],
              result["response"], result["suggestion"], result["followUpQuestion"],
              json.dumps(result["triggers"], ensure_ascii=False), result["riskLevel"],
              result["source"], result.get("model"), result.get("persona") or params["persona"],
              json.dumps(result.get("trace") or [], ensure_ascii=False)))
        checkin_id = cursor.lastrowid
        conn.execute(
            "UPDATE conversations SET title = CASE WHEN title = '新话题' THEN ? ELSE title END, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (text.replace("\n", " ")[:20], conversation_id),
        )
        row = conn.execute("SELECT * FROM checkins WHERE id = ?", (checkin_id,)).fetchone()
    payload = checkin_dict(row)
    payload["personalization"] = result.get("personalization")
    return payload


def _chat_params_or_error():
    """解析请求并准备话题；出错时返回 (None, None, None, (payload, status))。"""
    user = require_user()
    body = request.get_json(silent=True) or {}
    params, error = _parse_chat_request(body)
    if error:
        return None, None, None, error
    try:
        conversation_id, history, preferred_name = _prepare_chat_context(user[0], params)
    except ValueError as exc:
        return None, None, None, ({"ok": False, "error": str(exc)}, 400)
    except LookupError as exc:
        return None, None, None, ({"ok": False, "error": str(exc)}, 404)
    return user, params, (conversation_id, history, preferred_name), None


@app.route("/api/chat", methods=["POST"])
def chat():
    """一次性返回完整结果；流式端点不可用时的兜底路径。"""
    user, params, prepared, error = _chat_params_or_error()
    if error:
        return jsonify(error[0]), error[1]
    conversation_id, history, preferred_name = prepared
    try:
        result = run_analysis(params["text"], params["context"], params["sleep"], history,
                              user[0], params["persona"], params["customPersona"], preferred_name)
    except Exception as exc:
        print(f"[AI] 分析失败：{exc}")
        return jsonify({"ok": False, "error": f"AI 分析失败：{exc}"}), 503
    payload = _save_checkin(user[0], conversation_id, params, result)
    if payload is None:
        return jsonify({"ok": False, "error": "话题已被删除，本次回复未保存"}), 404
    return jsonify({"ok": True, "result": payload})


@app.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    """SSE 流式对话：status / trace / token / final / error 五类事件。"""
    user, params, prepared, error = _chat_params_or_error()
    if error:
        return jsonify(error[0]), error[1]
    conversation_id, history, preferred_name = prepared
    user_id = user[0]

    def event(payload):
        return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    def generate():
        try:
            payload = {
                "text": params["text"], "taskContext": params["context"],
                "persona": params["persona"], "customPersona": params["customPersona"],
                "preferredName": preferred_name, "sleepQuality": params["sleep"],
                "history": history,
            }
            profile = build_profile(DB_PATH, user_id)
            profile_hint = format_for_prompt(profile)
            if profile_hint:
                payload["userProfile"] = profile_hint
            llm_config = get_user_llm_config(user_id)

            facts, trace = [], []
            if AGENT_MODE and llm_config:
                yield event({"type": "status", "stage": "agent", "message": "正在查看近期记录"})
                try:
                    info = agent_workflow.collect(payload, DB_PATH, user_id, llm_config=llm_config)
                    facts = info.get("facts") or []
                    trace = info.get("trace") or []
                except Exception as exc:  # noqa: BLE001 - 取数失败退回单轮
                    print(f"[Agent] 信息收集失败，按单轮处理：{exc}")
                for step in trace:
                    yield event({"type": "trace", "step": step})

            yield event({"type": "status", "stage": "generate", "message": "正在组织回应"})
            result = None
            for kind, value in analyze_stream(payload, facts, llm_config=llm_config):
                if kind == "token":
                    yield event({"type": "token", "text": value})
                else:
                    result = value
            if result is None:
                raise RuntimeError("模型没有返回结果")

            result["trace"] = trace
            result["personalization"] = profile
            if facts:
                result["source"] = "agent"
            saved = _save_checkin(user_id, conversation_id, params, result)
            if saved is None:
                yield event({"type": "error", "message": "话题已被删除，本次回复未保存"})
                return
            yield event({"type": "final", "result": saved})
        except Exception as exc:  # noqa: BLE001 - 响应已开始，只能以事件形式报错
            print(f"[AI] 流式对话失败：{type(exc).__name__}: {exc}")
            yield event({"type": "error", "message": f"AI 分析失败：{exc}"})

    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })

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
        result = generate_insights(stats, llm_config=get_user_llm_config(user[0]))
    except Exception as exc:
        print(f"[AI] 阶段回顾请求失败，改用本地规则：{type(exc).__name__}: {exc}")
        result = generate_insights(stats)
    return jsonify({"ok": True, "report": result})


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "emotion-planet.html" if current_user() else "login.html")


@app.route("/<path:filename>")
def static_files(filename):
    if not filename.lower().endswith((".html", ".js", ".css", ".png", ".jpg", ".svg", ".glb")):
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_from_directory(BASE_DIR, filename)


# 导入即自检：这样 python app.py、flask run、gunicorn 都能触发自动补装。
start_asr_deps_check()


if __name__ == "__main__":
    deps_now = asr_deps_state()
    if deps_now["state"] == "installing":
        print(f"[ASR] {deps_now['message']}")

    # 首次识别要加载 900MB 权重（约十几秒）。依赖与权重都就位时后台先热起来，
    # 用户第一次点语音就不用干等；缺依赖或没权重就跳过，不在启动时被动联网下载。
    if ASR_ENABLED and os.environ.get("ASR_PRELOAD", "1").lower() not in ("0", "false", "no", "off"):
        def _warmup():
            state = wait_asr_deps()
            if state not in ("present", "installed"):
                print(f"[ASR] 跳过预热（依赖状态：{state}）")
                return
            try:
                module = _asr_module()
                if module.model_source() == "download":
                    print("[ASR] 本地没有模型权重，跳过预热（首次识别时会自动下载）")
                    return
                with _asr_lock:
                    module.get_model(device=ASR_DEVICE)
                print("[ASR] 本地语音模型已就绪")
            except Exception as exc:  # noqa: BLE001 - 预热失败不影响其他功能
                print(f"[ASR] 预热失败，不影响文字对话：{exc}")

        threading.Thread(target=_warmup, daemon=True).start()

    print("情绪星球 · 纯软件 AI Agent：http://127.0.0.1:5001")
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5001")), debug=False)
