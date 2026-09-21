"""
统一 Flask Web 管理界面

整合打招呼引擎和回复引擎的统一控制面板，提供：
- REST API 管理机器人状态、配置、日志、统计
- SocketIO 实时推送日志、进度、登录提示、状态更新
- 后台线程运行 UnifiedBotLoop
- Cookie 管理、浏览器选择、图片上传

使用方式：
    python app.py
    # 访问 http://localhost:5000
"""

import os
import sys
import io
import json
import uuid
import shutil
import logging
import warnings
from logging.handlers import TimedRotatingFileHandler
import threading


import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# 设置标准输出编码为 UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from flask import Flask, render_template, jsonify, request, send_file, abort
from flask_socketio import SocketIO, emit

# 抑制警告
warnings.filterwarnings("ignore", category=UserWarning, module="urllib3")

# 关闭 Flask 默认请求日志
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from boss_bot.unified_config import (
    UnifiedConfig, BASE_DIR, BOT_CONFIG_FILE, USER_PROFILE_FILE,
    OVERRIDES_FILE,
    load_config, save_config, save_overrides, validate_config, DEFAULT_GREETING,
)
from boss_bot.main_loop import UnifiedBotLoop, MultiAccountManager
from boss_bot.self_evolve import SelfEvolveEngine
from boss_bot.reply_record import (
    ReplyRecordStore, GreetRecordStore,
    export_reply_records, export_greet_records,
)
from boss_bot.message_store import MessageStore

# ===================== 日志缓冲区 =====================

MAX_LOGS = 200
log_buffer = []
log_buffer_lock = threading.Lock()


class WebLogHandler(logging.Handler):
    """将日志写入内存缓冲区，供前端显示。"""

    def emit(self, record):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        with log_buffer_lock:
            log_buffer.append(entry)
            if len(log_buffer) > MAX_LOGS:
                log_buffer.pop(0)


# 配置日志
# 确保 logs 目录存在
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 前端日志处理器 — 只记录 INFO 及以上级别
web_handler = WebLogHandler()
web_handler.setLevel(logging.INFO)

# 文件日志处理器 — 记录 DEBUG 及以上级别，按日期轮转，保留7天
file_handler = TimedRotatingFileHandler(
    str(LOG_DIR / "boss_bot.log"),
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8",
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
))

# 根日志级别设为 DEBUG，让文件处理器能收到所有日志
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(web_handler)
root_logger.addHandler(file_handler)

logger = logging.getLogger("boss-web")

# ===================== Flask 应用 =====================

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = os.urandom(24).hex()
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# 自动选择最佳 async_mode：gevent > threading（eventlet已弃用，不再使用）
_socketio_kwargs = {"cors_allowed_origins": "*"}
try:
    import gevent  # noqa: F401
    from gevent import monkey
    monkey.patch_all()  # 协程化标准库，支持 WebSocket
    _socketio_kwargs["async_mode"] = "gevent"
except Exception:
    # gevent 不可用或 monkey.patch_all() 失败时，降级到 threading
    _socketio_kwargs["async_mode"] = "threading"

socketio = SocketIO(app, **_socketio_kwargs)

# ===================== API 认证 =====================

# 允许访问的本地 IP 白名单
_ALLOWED_LOCAL_IPS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _get_api_token() -> str:
    """从 bot_config.json 读取 api_token（可选字段，默认为空字符串）。

    api_token 为空时，仅允许本地 IP 访问；
    api_token 非空时，非本地 IP 请求需携带匹配的 X-API-Token 头。
    """
    try:
        config_dict = load_config()
        return str(config_dict.get("api_token", "") or "")
    except Exception:
        return ""


@app.before_request
def _check_api_auth():
    """API 认证：本地 IP 白名单 + 可选 token。

    - 首页（/）和静态文件（/static/）不需要认证
    - 本地 IP（127.0.0.1, ::1, ::ffff:127.0.0.1）直接放行
    - 非本地 IP：若配置了 api_token，则校验 X-API-Token 头；否则拒绝
    """
    # 首页和静态文件不需要认证
    if request.path == "/" or request.path.startswith("/static/"):
        return

    # 检查请求来源 IP
    remote_ip = request.remote_addr or ""
    if remote_ip in _ALLOWED_LOCAL_IPS:
        return

    # 非本地 IP，检查 token
    api_token = _get_api_token()
    if api_token:
        provided_token = request.headers.get("X-API-Token", "")
        if provided_token != api_token:
            abort(403, description="认证失败：无效的 API Token")
    else:
        abort(403, description="认证失败：仅限本地访问")

# ===================== 全局状态 =====================

_multi_manager: Optional[MultiAccountManager] = None
_config: Optional[UnifiedConfig] = None
_status_thread: Optional[threading.Thread] = None
_status_stop = threading.Event()
_self_evolve: Optional[SelfEvolveEngine] = None

# 数据目录
DATA_DIR = PROJECT_ROOT / "data"
DASHBOARD_DIR = PROJECT_ROOT / "static" / "dashboard"
COOKIE_DIR = PROJECT_ROOT / "data"

# 确保目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

# ===================== 风控通知持久化 =====================

# 全局通知列表（最新的在前），每条通知结构：
# {id, timestamp, type, message, read}
_notifications: list[dict] = []
# 通知列表最大容量，超过自动删除最旧的
_MAX_NOTIFICATIONS = 200
# 通知持久化文件路径
_NOTIFICATIONS_FILE = os.path.join(os.path.dirname(__file__), "..", "notifications.json")
# 通知列表读写锁，避免并发写入冲突
_notifications_lock = threading.Lock()


def _load_notifications():
    """启动时从 notifications.json 加载持久化通知。"""
    global _notifications
    try:
        if os.path.exists(_NOTIFICATIONS_FILE):
            with open(_NOTIFICATIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _notifications = data
    except Exception:
        _notifications = []


def _save_notifications():
    """将当前通知列表持久化到 notifications.json。"""
    try:
        with open(_NOTIFICATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(_notifications, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _add_notification(ntype: str, message: str):
    """添加一条通知并持久化。

    Args:
        ntype: 通知类型，如 "wind_control"、"error"、"info"
        message: 通知内容
    """
    global _notifications
    notif = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now().isoformat(),
        "type": ntype,
        "message": message,
        "read": False,
    }
    with _notifications_lock:
        _notifications.insert(0, notif)  # 最新的在前
        if len(_notifications) > _MAX_NOTIFICATIONS:
            _notifications = _notifications[:_MAX_NOTIFICATIONS]
        _save_notifications()


def _ensure_config() -> UnifiedConfig:
    """确保 _config 已初始化。"""
    global _config
    if _config is None:
        _config = UnifiedConfig.load()
    return _config


def _ensure_manager() -> MultiAccountManager:
    """确保 _multi_manager 已初始化。"""
    global _multi_manager
    if _multi_manager is None:
        cfg = _ensure_config()

        def greet_event_callback(event_data: dict):
            """投递事件回调 — 推送结构化投递记录到前端表格。"""
            try:
                event_data["time"] = datetime.now().strftime("%H:%M:%S")
                socketio.emit("greet_record", event_data)
            except Exception:
                pass

        def reply_event_callback(event_data: dict):
            """回复事件回调 — 推送结构化回复记录到前端表格。"""
            try:
                event_data["time"] = datetime.now().strftime("%H:%M:%S")
                socketio.emit("reply_record", event_data)
            except Exception:
                pass

        def wind_control_callback(message: str, wtype: str):
            """风控事件回调 — 推送风控警告到前端，并持久化通知。"""
            try:
                socketio.emit("wind_control", {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "message": message,
                    "type": wtype,
                })
            except Exception:
                pass
            # 持久化通知到列表和文件
            try:
                _add_notification(wtype, message)
            except Exception:
                pass

        def log_callback(msg: str):
            """日志回调 — 同时写入缓冲区和推送 SocketIO。

            DEBUG 级别日志只写入文件（由 logging 处理），不推送前端。
            INFO/WARN/ERROR/CRITICAL 级别日志推送前端 + 写入文件。
            """
            # 解析日志级别
            level = msg.split("]")[0].strip("[") if "]" in msg else "INFO"
            clean_msg = msg.split("]", 1)[1].strip() if "]" in msg else msg

            # DEBUG 级别不推送前端，只通过 logging 写入文件
            if level.upper() == "DEBUG":
                return

            with log_buffer_lock:
                log_buffer.append({
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "level": level,
                    "message": clean_msg,
                })
                if len(log_buffer) > MAX_LOGS:
                    log_buffer.pop(0)
            try:
                socketio.emit("bot_log", {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "level": level,
                    "message": clean_msg,
                })
            except Exception:
                pass

        _multi_manager = MultiAccountManager(config=cfg, log_callback=log_callback, greet_event_cb=greet_event_callback, reply_event_cb=reply_event_callback, wind_control_cb=wind_control_callback)
    return _multi_manager


def _status_pusher():
    """后台线程：定期推送状态更新到前端。

    推送多账号汇总状态（status_update），检测每个账号的登录需求并推送
    login_required 事件（包含 account_index 和 account_name），
    以及推送每个账号的打招呼/回复进度数据（bot_progress，包含 account_index）。
    """
    _last_needs_login = {}  # account_index -> bool
    while not _status_stop.is_set():
        try:
            manager = _multi_manager
            if manager is not None:
                status = manager.get_status()
                socketio.emit("status_update", status)

                # 遍历每个账号，检测登录状态变化 → 推送 login_required
                for acc_status in status.get("accounts", []):
                    idx = acc_status.get("index", 0)
                    name = acc_status.get("name", f"账号{idx}")
                    needs_login = acc_status.get("needs_login", False)
                    if needs_login and not _last_needs_login.get(idx, False):
                        socketio.emit("login_required", {
                            "message": f"账号「{name}」登录已过期或需要手动登录，请在浏览器中登录后点击「我已登录」",
                            "account_index": idx,
                            "account_name": name,
                        })
                    _last_needs_login[idx] = needs_login

                # 推送每个账号的进度数据（bot_progress）
                for acc_status in status.get("accounts", []):
                    idx = acc_status.get("index", 0)
                    name = acc_status.get("name", f"账号{idx}")
                    stats = acc_status.get("stats", {})
                    if stats:
                        socketio.emit("bot_progress", {
                            "account_index": idx,
                            "account_name": name,
                            "applied": stats.get("greet_applied", 0),
                            "skipped": stats.get("greet_skipped", 0),
                            "total": stats.get("greet_total", 0),
                            "reply_sent": stats.get("reply_sent", 0),
                            "reply_skipped": stats.get("reply_skipped", 0),
                            "resume_sent": stats.get("resume_sent", 0),
                            "important_events": stats.get("important_events", 0),
                        })
        except Exception:
            pass
        _status_stop.wait(timeout=3)


# ===================== 主页 =====================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard/<path:filename>")
def serve_dashboard(filename):
    """提供看板图片静态文件。"""
    safe_name = filename.replace("..", "").strip("/")
    filepath = DASHBOARD_DIR / safe_name
    if filepath.exists() and filepath.is_file():
        return send_file(str(filepath))
    return jsonify({"error": "File not found"}), 404


# ===================== 状态 API =====================

@app.route("/api/status")
def api_status():
    """获取机器人运行状态（多账号汇总）。"""
    manager = _multi_manager
    if manager is None:
        return jsonify({
            "running": False,
            "accounts": [],
            "stats": {},
        })
    return jsonify(manager.get_status())


# ===================== 启动/停止 API =====================

@app.route("/api/start", methods=["POST"])
def api_start():
    """启动所有启用的账号。"""
    global _status_thread
    manager = _ensure_manager()
    status = manager.get_status()
    if status.get("running"):
        return jsonify({"status": "ok", "message": "机器人已在运行中"})
    manager.start()

    # 启动状态推送线程
    _status_stop.clear()
    _status_thread = threading.Thread(target=_status_pusher, daemon=True)
    _status_thread.start()

    return jsonify({"status": "ok", "message": "机器人已启动"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """停止所有账号。"""
    global _status_thread
    _status_stop.set()
    manager = _multi_manager
    if manager is not None:
        manager.stop()
    if _status_thread is not None and _status_thread.is_alive():
        _status_thread.join(timeout=5)
    return jsonify({"status": "ok", "message": "机器人已停止"})


# ===================== 打招呼控制 API =====================

@app.route("/api/pause_greet", methods=["POST"])
def api_pause_greet():
    """暂停所有账号的打招呼功能。"""
    manager = _ensure_manager()
    manager.pause_greet()
    return jsonify({"status": "ok", "message": "打招呼已暂停"})


@app.route("/api/resume_greet", methods=["POST"])
def api_resume_greet():
    """恢复所有账号的打招呼功能。"""
    manager = _ensure_manager()
    manager.resume_greet()
    return jsonify({"status": "ok", "message": "打招呼已恢复"})


# ===================== 回复控制 API =====================

@app.route("/api/pause_reply", methods=["POST"])
def api_pause_reply():
    """暂停所有账号的回复功能（人工接管模式）。"""
    manager = _ensure_manager()
    manager.pause_reply()
    return jsonify({"status": "ok", "message": "回复已暂停（人工接管模式）"})


@app.route("/api/resume_reply", methods=["POST"])
def api_resume_reply():
    """恢复所有账号的回复功能。"""
    manager = _ensure_manager()
    manager.resume_reply()
    return jsonify({"status": "ok", "message": "回复已恢复"})


# ===================== 登录 API =====================

@app.route("/api/confirm_login", methods=["POST"])
def api_confirm_login():
    """确认所有账号的登录完成。"""
    manager = _ensure_manager()
    manager.confirm_login()
    socketio.emit("status_update", manager.get_status())
    return jsonify({"status": "ok", "message": "登录已确认"})


# ===================== 账号级别控制 API =====================

def _validate_account_index(idx: int):
    """验证账号索引是否有效，返回 (manager, error_response)。"""
    manager = _ensure_manager()
    cfg = _ensure_config()
    if idx < 0 or idx >= len(cfg.greet.accounts):
        return None, (jsonify({"status": "error", "message": "账号索引超出范围"}), 404)
    if idx not in manager._loops:
        return None, (jsonify({"status": "error", "message": "账号未启用或不存在"}), 404)
    return manager, None


@app.route("/api/accounts/<int:idx>/start", methods=["POST"])
def api_account_start(idx: int):
    """启动指定账号。"""
    manager, error = _validate_account_index(idx)
    if error:
        return error
    manager.start_account(idx)
    return jsonify({"status": "ok", "message": f"账号 {idx} 已启动"})


@app.route("/api/accounts/<int:idx>/stop", methods=["POST"])
def api_account_stop(idx: int):
    """停止指定账号。"""
    manager, error = _validate_account_index(idx)
    if error:
        return error
    manager.stop_account(idx)
    return jsonify({"status": "ok", "message": f"账号 {idx} 已停止"})


@app.route("/api/accounts/<int:idx>/pause_greet", methods=["POST"])
def api_account_pause_greet(idx: int):
    """暂停指定账号的打招呼功能。"""
    manager, error = _validate_account_index(idx)
    if error:
        return error
    manager.pause_greet(idx)
    return jsonify({"status": "ok", "message": f"账号 {idx} 打招呼已暂停"})


@app.route("/api/accounts/<int:idx>/resume_greet", methods=["POST"])
def api_account_resume_greet(idx: int):
    """恢复指定账号的打招呼功能。"""
    manager, error = _validate_account_index(idx)
    if error:
        return error
    manager.resume_greet(idx)
    return jsonify({"status": "ok", "message": f"账号 {idx} 打招呼已恢复"})


@app.route("/api/accounts/<int:idx>/pause_reply", methods=["POST"])
def api_account_pause_reply(idx: int):
    """暂停指定账号的回复功能（人工接管模式）。"""
    manager, error = _validate_account_index(idx)
    if error:
        return error
    manager.pause_reply(idx)
    return jsonify({"status": "ok", "message": f"账号 {idx} 回复已暂停（人工接管模式）"})


@app.route("/api/accounts/<int:idx>/resume_reply", methods=["POST"])
def api_account_resume_reply(idx: int):
    """恢复指定账号的回复功能。"""
    manager, error = _validate_account_index(idx)
    if error:
        return error
    manager.resume_reply(idx)
    return jsonify({"status": "ok", "message": f"账号 {idx} 回复已恢复"})


@app.route("/api/accounts/<int:idx>/confirm_login", methods=["POST"])
def api_account_confirm_login(idx: int):
    """确认指定账号的登录完成。"""
    manager, error = _validate_account_index(idx)
    if error:
        return error
    manager.confirm_login(idx)
    acc_status = manager.get_account_status(idx)
    socketio.emit("status_update", manager.get_status())
    return jsonify({"status": "ok", "message": f"账号 {idx} 登录已确认"})


@app.route("/api/accounts/<int:idx>/status", methods=["GET"])
def api_account_status(idx: int):
    """获取指定账号的运行状态。"""
    manager, error = _validate_account_index(idx)
    if error:
        return error
    acc_status = manager.get_account_status(idx)
    if acc_status is None:
        return jsonify({"status": "error", "message": "账号状态获取失败"}), 404
    return jsonify(acc_status)


@app.route("/api/accounts/<int:idx>/check_cookie", methods=["POST", "GET"])
def api_account_check_cookie(idx: int):
    """检测指定账号的 Cookie 是否有效。

    使用 DrissionPage 启动浏览器，加载 Cookie 后访问 BOSS 直聘首页，
    检查登录状态。参考 auto_boss 项目的 check_login_status 方法。

    Returns:
        {
            "status": "ok",
            "valid": bool,           # Cookie 是否有效
            "logged_in": bool,       # 是否已登录
            "reason": str,           # 失效原因
            "current_url": str,      # 检测时的页面 URL
            "checks": dict,          # 各项检查结果
            "cookie_file": str,      # Cookie 文件路径
        }
    """
    try:
        cfg = _ensure_config()
        if idx < 0 or idx >= len(cfg.greet.accounts):
            return jsonify({"status": "error", "message": "账号索引超出范围"}), 404

        acc = cfg.greet.accounts[idx]
        cookie_file_name = acc.cookie_file or "zhipin_cookies.json"
        # Cookie 文件路径相对于项目根目录
        cookie_file_path = str(PROJECT_ROOT / cookie_file_name)

        # 先做简单检测（不启动浏览器）
        from boss_bot.browser_launcher import check_cookie_valid_simple, check_cookie_valid
        simple_result = check_cookie_valid_simple(cookie_file_path)
        if not simple_result["checks"].get("file_exists", False):
            return jsonify({
                "status": "ok",
                "valid": False,
                "logged_in": False,
                "reason": simple_result["reason"],
                "current_url": "",
                "checks": simple_result["checks"],
                "cookie_file": cookie_file_name,
            })

        # 启动浏览器做完整检测
        browser_cfg = cfg.browser
        result = check_cookie_valid(
            cookie_file=cookie_file_path,
            headless=True,
            chrome_path=browser_cfg.chrome_path,
            browser_type=browser_cfg.browser_type,
        )

        return jsonify({
            "status": "ok",
            "valid": result["valid"],
            "logged_in": result["logged_in"],
            "reason": result["reason"],
            "current_url": result["current_url"],
            "checks": result["checks"],
            "cookie_file": cookie_file_name,
        })
    except Exception as e:
        logger.exception("检测 Cookie 有效性失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/accounts/<int:idx>/check_cookie_simple", methods=["POST", "GET"])
def api_account_check_cookie_simple(idx: int):
    """快速检测指定账号的 Cookie 是否有效（不启动浏览器，仅检查文件）。

    Returns:
        {
            "status": "ok",
            "valid": bool,
            "reason": str,
            "checks": dict,
            "cookie_file": str,
        }
    """
    try:
        cfg = _ensure_config()
        if idx < 0 or idx >= len(cfg.greet.accounts):
            return jsonify({"status": "error", "message": "账号索引超出范围"}), 404

        acc = cfg.greet.accounts[idx]
        cookie_file_name = acc.cookie_file or "zhipin_cookies.json"
        cookie_file_path = str(PROJECT_ROOT / cookie_file_name)

        from boss_bot.browser_launcher import check_cookie_valid_simple
        result = check_cookie_valid_simple(cookie_file_path)

        return jsonify({
            "status": "ok",
            "valid": result["valid"],
            "logged_in": result["logged_in"],
            "reason": result["reason"],
            "checks": result["checks"],
            "cookie_file": cookie_file_name,
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 配置 API =====================

@app.route("/api/config", methods=["GET"])
def api_get_config():
    """获取当前配置。"""
    cfg = _ensure_config()
    config_dict = cfg.to_dict()
    # 添加 user_profile 信息
    config_dict["user_profile"] = cfg._profile_dict()
    # 添加 reply_rules 和 importance_keywords
    config_dict["reply_rules"] = dict(cfg.rules.reply_rules)
    config_dict["importance_keywords"] = list(cfg.rules.importance_keywords)
    # 添加 templates
    config_dict["templates"] = {
        "salary_reply": cfg.templates.salary_reply,
        "interview_time_reply": cfg.templates.interview_time_reply,
        "job_content_reply": cfg.templates.job_content_reply,
        "greeting_reply": cfg.templates.greeting_reply,
        "default_reply": cfg.templates.default_reply,
        "resume_duplicate_reply": cfg.templates.resume_duplicate_reply,
        "resume_unavailable_reply": cfg.templates.resume_unavailable_reply,
    }
    # 添加 greet.rate_limit 字段，兼容前端 config.greet.rate_limit 访问路径
    # （to_dict() 顶层已有 rate_limit，但前端部分代码会读写 config.greet.rate_limit）
    if "greet" not in config_dict:
        config_dict["greet"] = {}
    config_dict["greet"]["rate_limit"] = {
        "enabled": cfg.greet.rate_limit.enabled,
        "max_per_hour": cfg.greet.rate_limit.max_per_hour,
        "max_per_day": cfg.greet.rate_limit.max_per_day,
    }
    config_dict["greet"]["enabled"] = cfg.greet.enabled
    return jsonify({"status": "ok", "config": config_dict})


@app.route("/api/config", methods=["POST", "PUT"])
def api_save_config():
    """保存配置。

    保存策略：
      - reply_rules、templates、importance_keywords、user_profile 同时写入
        bot_config.json 和 config_overrides.json，确保两个文件一致。
      - AI 配置等其他字段只写入 bot_config.json。
      - 保存后重新加载配置，确保后端读到正确的值。
    """
    global _config, _multi_manager
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "请求体为空"}), 400

        new_cfg = data.get("config") if isinstance(data.get("config"), dict) else data
        if not isinstance(new_cfg, dict):
            return jsonify({"status": "error", "message": "config 字段必须是对象"}), 400

        # 使用兼容层校验
        errors = validate_config(new_cfg)
        if errors:
            return jsonify({"status": "error", "message": "；".join(errors)}), 400

        # 保存到 bot_config.json（主配置源）
        save_config(new_cfg)

        # 同步 reply_rules、templates、importance_keywords、user_profile 到 config_overrides.json
        # 确保 config_overrides.json 中的覆盖配置与 bot_config.json 一致
        save_overrides(new_cfg)

        # 重新加载配置
        _config = UnifiedConfig.load()

        # 如果机器人正在运行，更新配置（需要停止后重启才能生效）
        if _multi_manager is not None and _multi_manager.get_status().get("running"):
            return jsonify({
                "status": "ok",
                "message": "配置已保存，需重启机器人才能生效",
                "need_restart": True,
            })

        return jsonify({"status": "ok", "message": "配置已保存"})
    except Exception as e:
        logger.exception("保存配置失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 日志 API =====================

@app.route("/api/logs")
def api_logs():
    """获取日志列表。"""
    with log_buffer_lock:
        logs = list(log_buffer)
    return jsonify({"status": "ok", "logs": logs})


@app.route("/api/logs/file")
def api_logs_file():
    """查看文件日志内容，支持按日期和行数筛选。

    查询参数：
        date: 指定日期（YYYY-MM-DD），默认今天
        lines: 返回最后 N 行，默认 200
    """
    try:
        date_str = request.args.get("date", datetime.now().strftime("%Y-%m-%d"))
        lines = request.args.get("lines", 200, type=int)

        # 构造日志文件路径
        if date_str == datetime.now().strftime("%Y-%m-%d"):
            log_file = LOG_DIR / "boss_bot.log"
        else:
            log_file = LOG_DIR / f"boss_bot.log.{date_str}"

        if not log_file.exists():
            return jsonify({
                "status": "ok",
                "date": date_str,
                "lines": [],
                "message": f"日志文件不存在: {log_file.name}",
            })

        # 读取最后 N 行
        all_lines = log_file.read_text(encoding="utf-8").splitlines()
        recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return jsonify({
            "status": "ok",
            "date": date_str,
            "lines": recent_lines,
            "total": len(all_lines),
        })
    except Exception as e:
        logger.exception("读取文件日志失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/logs/clear", methods=["POST"])
def api_logs_clear():
    """清空日志文件和前端日志缓冲。"""
    try:
        # 清空前端日志缓冲
        with log_buffer_lock:
            log_buffer.clear()
        # 清空日志文件内容（不删除文件本身）
        for log_file in LOG_DIR.iterdir():
            if log_file.is_file() and log_file.name.endswith(".log"):
                try:
                    log_file.write_text("", encoding="utf-8")
                except Exception:
                    pass
        logger.info("日志已清空")
        return jsonify({"status": "ok", "message": "日志已清空"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 统计 API =====================

@app.route("/api/stats")
def api_stats():
    """获取统计数据（多账号汇总）。"""
    manager = _multi_manager
    if manager is None:
        return jsonify({
            "status": "ok",
            "stats": {
                "greet_applied": 0,
                "greet_skipped": 0,
                "greet_total": 0,
                "reply_sent": 0,
                "reply_skipped": 0,
                "resume_sent": 0,
                "important_events": 0,
                "greet_rounds": 0,
                "reply_rounds": 0,
            },
        })
    status = manager.get_status()
    return jsonify({"status": "ok", "stats": status.get("stats", {})})


# ===================== 消息 API =====================

@app.route("/api/messages")
def api_messages():
    """获取消息列表（汇总所有账号）。"""
    manager = _multi_manager
    if manager is None:
        return jsonify({"status": "ok", "messages": []})
    try:
        all_messages = []
        with manager._lock:
            for idx in sorted(manager._loops.keys()):
                loop = manager._loops[idx]
                if loop._msg_store is not None:
                    msgs = loop._msg_store.get_recent_messages(limit=50)
                    for msg in msgs:
                        msg["account_index"] = idx
                        msg["account_name"] = loop.account_name
                    all_messages.extend(msgs)
        # 按时间排序，最多返回 50 条
        all_messages.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return jsonify({"status": "ok", "messages": all_messages[:50]})
    except Exception as e:
        return jsonify({"status": "ok", "messages": [], "error": str(e)})


# ===================== Cookie 上传 API =====================

@app.route("/api/upload/cookie", methods=["POST"])
def api_upload_cookie():
    """上传 Cookie 文件。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"status": "error", "message": "未选择文件"}), 400
    safe_name = os.path.basename(f.filename)
    if not safe_name.endswith(".json"):
        safe_name += ".json"
    save_path = str(COOKIE_DIR / safe_name)
    f.save(save_path)
    return jsonify({"status": "ok", "filename": safe_name})


@app.route("/api/cookies/upload", methods=["POST"])
def api_cookies_upload():
    """上传 Cookie 文件（别名路由，兼容前端路径）。"""
    return api_upload_cookie()


@app.route("/api/cookies/delete", methods=["POST"])
def api_cookies_delete():
    """删除 Cookie 文件。"""
    try:
        data = request.get_json() or {}
        filename = data.get("filename", "")
        if not filename:
            return jsonify({"status": "error", "message": "未指定文件名"}), 400
        safe_name = os.path.basename(filename)
        if not safe_name.endswith(".json"):
            safe_name += ".json"
        cookie_path = COOKIE_DIR / safe_name
        if cookie_path.exists():
            cookie_path.unlink()
            return jsonify({"status": "ok", "message": f"已删除 {safe_name}"})
        else:
            return jsonify({"status": "error", "message": f"文件不存在: {safe_name}"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 图片上传 API =====================

@app.route("/api/upload/images", methods=["POST"])
def api_upload_images():
    """上传作品图片。"""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"status": "error", "message": "未选择文件"}), 400
    uploaded = []
    for f in files:
        if f and f.filename:
            filename = uuid.uuid4().hex[:8] + "_" + f.filename
            save_path = str(DASHBOARD_DIR / filename)
            f.save(save_path)
            uploaded.append(f"dashboard/{filename}")
    return jsonify({"status": "ok", "files": uploaded})


# ===================== 浏览器检测 API =====================

@app.route("/api/browser/list")
def api_browser_list():
    """检测可用浏览器。"""
    browsers = []

    # Windows 常见路径
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    edge_paths = [
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]

    for p in chrome_paths:
        if os.path.isfile(p):
            browsers.append({"name": "Chrome", "path": p, "type": "chrome"})
            break

    for p in edge_paths:
        if os.path.isfile(p):
            browsers.append({"name": "Edge", "path": p, "type": "edge"})
            break

    # 检查 PATH 中的浏览器
    for name in ["chrome", "chromium", "msedge"]:
        path = shutil.which(name)
        if path:
            browsers.append({
                "name": name.capitalize(),
                "path": path,
                "type": "chrome" if name != "msedge" else "edge",
            })

    return jsonify({"status": "ok", "browsers": browsers})


@app.route("/api/browser/set", methods=["POST"])
def api_browser_set():
    """设置偏好浏览器。"""
    data = request.get_json() or {}
    browser_path = data.get("path", "")
    browser_type = data.get("type", "chrome")

    if not browser_path:
        return jsonify({"status": "error", "message": "path 不能为空"}), 400

    cfg = _ensure_config()
    cfg.browser.chrome_path = browser_path
    cfg.browser.browser_type = browser_type
    cfg.save()

    return jsonify({"status": "ok", "message": f"浏览器已设置为 {browser_type}"})


# ===================== 个人画像 API =====================

@app.route("/api/user_profile", methods=["GET"])
def api_get_profile():
    """获取个人画像配置。"""
    cfg = _ensure_config()
    return jsonify({"status": "ok", "profile": cfg._profile_dict()})


@app.route("/api/user_profile", methods=["POST"])
def api_save_profile():
    """保存个人画像配置。"""
    global _config
    try:
        data = request.get_json()
        if not data or "profile" not in data:
            return jsonify({"status": "error", "message": "缺少 profile 字段"}), 400

        profile_data = data["profile"]

        # 保存到 user_profile.json
        with open(USER_PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, ensure_ascii=False, indent=2)

        # 重新加载配置
        _config = UnifiedConfig.load()

        return jsonify({"status": "ok", "message": "个人画像已保存"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== AI 相关 API =====================

@app.route("/api/ai/test", methods=["POST"])
def api_ai_test():
    """测试 AI 连接，接收 {provider_index} 参数，返回测试结果。"""
    try:
        data = request.get_json() or {}
        provider_index = data.get("provider_index", 0)
        cfg = _ensure_config()

        if not cfg.ai.providers:
            return jsonify({"status": "error", "message": "未配置任何 AI 提供商"}), 400

        if provider_index < 0 or provider_index >= len(cfg.ai.providers):
            return jsonify({"status": "error", "message": "提供商索引超出范围"}), 400

        provider = cfg.ai.providers[provider_index]
        if not provider.api_key:
            return jsonify({"status": "error", "message": f"提供商「{provider.name}」未配置 API Key"}), 400

        try:
            from openai import OpenAI
            client = OpenAI(api_key=provider.api_key, base_url=provider.api_base)
            response = client.chat.completions.create(
                model=provider.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "请回复：连接成功"}],
            )
            reply_text = response.choices[0].message.content if response.choices else ""
            return jsonify({
                "status": "ok",
                "message": f"提供商「{provider.name}」连接成功",
                "reply": reply_text,
            })
        except ImportError:
            return jsonify({"status": "error", "message": "未安装 openai 库"}), 500
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"提供商「{provider.name}」连接失败: {str(e)[:200]}",
            }), 500
    except Exception as e:
        logger.exception("AI 测试失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/analyze", methods=["POST"])
def api_ai_analyze():
    """分析岗位描述，接收 {job_desc, provider_index} 参数，返回分析结果。"""
    try:
        data = request.get_json() or {}
        job_desc = data.get("job_desc", "")
        provider_index = data.get("provider_index", 0)

        if not job_desc:
            return jsonify({"status": "error", "message": "岗位描述不能为空"}), 400

        cfg = _ensure_config()
        if not cfg.ai.providers:
            return jsonify({"status": "error", "message": "未配置任何 AI 提供商"}), 400

        if provider_index < 0 or provider_index >= len(cfg.ai.providers):
            return jsonify({"status": "error", "message": "提供商索引超出范围"}), 400

        provider = cfg.ai.providers[provider_index]
        if not provider.api_key:
            return jsonify({"status": "error", "message": f"提供商「{provider.name}」未配置 API Key"}), 400

        try:
            from openai import OpenAI
            client = OpenAI(api_key=provider.api_key, base_url=provider.api_base)
            profile = cfg._profile_dict()
            skills = "、".join(str(s) for s in profile.get("skills", [])) if isinstance(profile.get("skills"), list) else profile.get("skills", "")
            system_prompt = (
                f"你是一个求职分析助手。求职者背景：学历{profile.get('education', '')}，"
                f"求职方向{profile.get('position', '')}，技能{skills}。"
                f"请分析岗位描述与求职者的匹配度，给出匹配分数(0-100)和简要理由。"
            )
            response = client.chat.completions.create(
                model=provider.model,
                max_tokens=cfg.ai.max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请分析以下岗位描述：\n{job_desc}"},
                ],
            )
            analysis = response.choices[0].message.content if response.choices else ""
            return jsonify({
                "status": "ok",
                "analysis": analysis,
                "provider": provider.name,
            })
        except ImportError:
            return jsonify({"status": "error", "message": "未安装 openai 库"}), 500
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"AI 分析失败: {str(e)[:200]}",
            }), 500
    except Exception as e:
        logger.exception("AI 分析失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# AI 提示词默认值（与 boss_bot/prompts.py 保持一致）
_DEFAULT_SYSTEM_RULES = """你的回复要求：
1. 语气专业、礼貌、真诚，不要过于机械
2. 简洁明了，控制在 1-2 句话，不要长篇大论
3. 展现积极态度和学习能力
4. 不要编造不存在的工作经历或技能
5. 如果对方问了你不知道的问题，诚实说可以面谈详细了解
6. 不要使用 emoji，保持专业
7. 只输出回复内容本身，不要加引号或任何前缀
8. 结合上面的对话历史自然接续话题，不要重复已经说过的内容
9. 严格禁止声称已经完成了无法确认的事情（如"已投递简历""已发送材料""已经报名"），除非对话历史中确实发生过；对方要求你做某事时，回复"稍后完成/马上处理"即可
10. 如果对方的岗位与你的求职方向明显不符，礼貌说明求职方向并询问是否有相关岗位，不要强行迎合"""

_DEFAULT_USER_PROMPT_TEMPLATE = """当前聊天上下文：
- 招聘方称呼：{boss_name}
- 招聘岗位：{job_name}
- 最近对话记录：
{history}
- 对方最新消息：{message}

请根据对话历史和最新消息，给出合适的回复。只输出回复内容，不要解释。"""


@app.route("/api/ai/prompts", methods=["GET"])
def api_get_ai_prompts():
    """获取当前 AI 提示词配置（system_rules, user_prompt_template）。

    当 config_overrides.json 中未配置时，返回 prompts.py 中的默认值。
    """
    try:
        overrides = {}
        if OVERRIDES_FILE.exists():
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                overrides = json.load(f)
        prompts = {
            "system_rules": overrides.get("system_rules", "") or _DEFAULT_SYSTEM_RULES,
            "user_prompt_template": overrides.get("user_prompt_template", "") or _DEFAULT_USER_PROMPT_TEMPLATE,
        }
        return jsonify({"status": "ok", "prompts": prompts})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/prompts", methods=["POST"])
def api_save_ai_prompts():
    """保存 AI 提示词配置。"""
    try:
        try:
            data = request.get_json() or {}
        except Exception:
            # 编码问题降级：手动解析请求体
            raw = request.get_data()
            data = json.loads(raw.decode('utf-8', errors='replace')) if raw else {}
        prompts = data.get("prompts", data)

        overrides = {}
        if OVERRIDES_FILE.exists():
            with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                overrides = json.load(f)

        if "system_rules" in prompts:
            overrides["system_rules"] = prompts["system_rules"]
        if "user_prompt_template" in prompts:
            overrides["user_prompt_template"] = prompts["user_prompt_template"]

        with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)

        return jsonify({"status": "ok", "message": "AI 提示词已保存"})
    except Exception as e:
        logger.exception("保存 AI 提示词失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/providers", methods=["GET"])
def api_get_ai_providers():
    """获取 AI 提供商列表。"""
    cfg = _ensure_config()
    providers = [
        {
            "name": p.name,
            "api_key": p.api_key,
            "api_base": p.api_base,
            "model": p.model,
            "timeout": p.timeout,
        }
        for p in cfg.ai.providers
    ]
    return jsonify({"status": "ok", "providers": providers})


@app.route("/api/ai/providers/add", methods=["POST"])
def api_add_ai_provider():
    """添加 AI 提供商，接收 {name, api_key, model, api_base}。"""
    try:
        data = request.get_json() or {}
        name = data.get("name", "")
        api_key = data.get("api_key", "")
        model = data.get("model", "")
        api_base = data.get("api_base", "")

        if not name:
            return jsonify({"status": "error", "message": "提供商名称不能为空"}), 400
        if not api_key:
            return jsonify({"status": "error", "message": "API Key 不能为空"}), 400
        if not model:
            return jsonify({"status": "error", "message": "模型名称不能为空"}), 400

        cfg = _ensure_config()
        from boss_bot.unified_config import AIProvider
        new_provider = AIProvider(
            name=name,
            api_key=api_key,
            api_base=api_base or "https://apihub.agnes-ai.com/v1",
            model=model,
            timeout=30,
        )
        cfg.ai.providers.append(new_provider)
        cfg.save()

        return jsonify({"status": "ok", "message": f"提供商「{name}」已添加"})
    except Exception as e:
        logger.exception("添加 AI 提供商失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/providers/delete", methods=["POST"])
def api_delete_ai_provider():
    """删除 AI 提供商，接收 {index}。"""
    try:
        data = request.get_json() or {}
        index = data.get("index", -1)

        cfg = _ensure_config()
        if index < 0 or index >= len(cfg.ai.providers):
            return jsonify({"status": "error", "message": "提供商索引超出范围"}), 400

        removed = cfg.ai.providers.pop(index)
        cfg.save()

        return jsonify({"status": "ok", "message": f"提供商「{removed.name}」已删除"})
    except Exception as e:
        logger.exception("删除 AI 提供商失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 简历 API =====================

@app.route("/api/resume", methods=["GET"])
def api_get_resume():
    """获取简历内容（从配置中读取 resume 相关字段）。"""
    cfg = _ensure_config()
    resume = {
        "school": cfg.resume.school,
        "major": cfg.resume.major,
        "degree": cfg.resume.degree,
        "skills": list(cfg.resume.skills),
        "experience": cfg.resume.experience,
        "target_position": cfg.resume.target_position,
        "self_intro": cfg.resume.self_intro,
    }
    return jsonify({"status": "ok", "resume": resume})


@app.route("/api/resume", methods=["POST"])
def api_save_resume():
    """保存简历内容。"""
    global _config
    try:
        data = request.get_json() or {}
        resume_data = data.get("resume", data)

        cfg = _ensure_config()
        if "school" in resume_data:
            cfg.resume.school = str(resume_data["school"])
        if "major" in resume_data:
            cfg.resume.major = str(resume_data["major"])
        if "degree" in resume_data:
            cfg.resume.degree = str(resume_data["degree"])
        if "skills" in resume_data and isinstance(resume_data["skills"], list):
            cfg.resume.skills = list(resume_data["skills"])
        if "experience" in resume_data:
            cfg.resume.experience = str(resume_data["experience"])
        if "target_position" in resume_data:
            cfg.resume.target_position = str(resume_data["target_position"])
        if "self_intro" in resume_data:
            cfg.resume.self_intro = str(resume_data["self_intro"])

        cfg.save()
        _config = UnifiedConfig.load()

        return jsonify({"status": "ok", "message": "简历已保存"})
    except Exception as e:
        logger.exception("保存简历失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== Excel 导出 API =====================

@app.route("/api/excel/files", methods=["GET"])
def api_excel_files():
    """获取已导出的 Excel 文件列表（扫描 data/ 目录下的 .xlsx 文件）。"""
    try:
        files = []
        if DATA_DIR.exists():
            for f in DATA_DIR.iterdir():
                if f.is_file() and f.suffix == ".xlsx":
                    stat = f.stat()
                    files.append({
                        "name": f.name,
                        "size": stat.st_size,
                        "created": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
                    })
        files.sort(key=lambda x: x["created"], reverse=True)
        return jsonify({"status": "ok", "files": files})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/excel/download/<filename>", methods=["GET"])
def api_excel_download(filename):
    """下载指定的 Excel 文件。"""
    try:
        safe_name = os.path.basename(filename)
        if not safe_name.endswith(".xlsx"):
            return jsonify({"status": "error", "message": "只能下载 .xlsx 文件"}), 400

        file_path = DATA_DIR / safe_name
        if not file_path.exists() or not file_path.is_file():
            return jsonify({"status": "error", "message": "文件不存在"}), 404

        return send_file(
            str(file_path),
            as_attachment=True,
            download_name=safe_name,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/excel/export", methods=["POST"])
def api_excel_export():
    """导出当前统计数据为 Excel 文件。"""
    try:
        from openpyxl import Workbook

        manager = _multi_manager
        stats = {}
        if manager is not None:
            status = manager.get_status()
            stats = status.get("stats", {})

        wb = Workbook()
        ws = wb.active
        ws.title = "统计数据"

        headers = ["指标", "数值"]
        ws.append(headers)

        rows = [
            ("打招呼-已投递", stats.get("greet_applied", 0)),
            ("打招呼-已跳过", stats.get("greet_skipped", 0)),
            ("打招呼-总数", stats.get("greet_total", 0)),
            ("回复-已发送", stats.get("reply_sent", 0)),
            ("回复-已跳过", stats.get("reply_skipped", 0)),
            ("简历-已发送", stats.get("resume_sent", 0)),
            ("重要事件数", stats.get("important_events", 0)),
            ("打招呼轮次", stats.get("greet_rounds", 0)),
            ("回复轮次", stats.get("reply_rounds", 0)),
        ]
        for row in rows:
            ws.append(row)

        # 导出时间
        ws.append([])
        ws.append(["导出时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])

        filename = f"stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        file_path = DATA_DIR / filename
        wb.save(str(file_path))

        return jsonify({
            "status": "ok",
            "message": "数据已导出",
            "filename": filename,
        })
    except ImportError:
        return jsonify({"status": "error", "message": "未安装 openpyxl 库"}), 500
    except Exception as e:
        logger.exception("Excel 导出失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 记录导出 API =====================

_reply_record_store: Optional[ReplyRecordStore] = None
_greet_record_store: Optional[GreetRecordStore] = None


def _ensure_reply_store() -> ReplyRecordStore:
    """确保回复记录存储已初始化。"""
    global _reply_record_store
    if _reply_record_store is None:
        _reply_record_store = ReplyRecordStore()
    return _reply_record_store


def _ensure_greet_store() -> GreetRecordStore:
    """确保打招呼记录存储已初始化。"""
    global _greet_record_store
    if _greet_record_store is None:
        _greet_record_store = GreetRecordStore()
    return _greet_record_store


@app.route("/api/export/reply_records")
def api_export_reply_records():
    """导出回复记录，支持 JSON/Excel 格式下载。

    查询参数：
        format: json 或 excel（默认 json）
        date: 按日期筛选（YYYY-MM-DD）
        chat_name: 按聊天对象筛选
    """
    fmt = request.args.get("format", "json")
    date = request.args.get("date")
    chat_name = request.args.get("chat_name")

    # Excel 格式但 openpyxl 未安装时，fallback 到 JSON
    if fmt == "excel":
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            fmt = "json"

    try:
        file_path = export_reply_records(
            format=fmt,
            date=date,
            chat_name=chat_name,
        )

        if fmt == "excel":
            return send_file(
                file_path,
                as_attachment=True,
                download_name=os.path.basename(file_path),
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            return send_file(
                file_path,
                as_attachment=True,
                download_name="reply_records_export.json",
                mimetype="application/json",
            )
    except Exception as e:
        logger.exception("导出回复记录失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/export/greet_records")
def api_export_greet_records():
    """导出打招呼记录，支持 JSON/Excel 格式下载。

    查询参数：
        format: json 或 excel（默认 json）
        date: 按日期筛选（YYYY-MM-DD）
    """
    fmt = request.args.get("format", "json")
    date = request.args.get("date")

    # Excel 格式但 openpyxl 未安装时，fallback 到 JSON
    if fmt == "excel":
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            fmt = "json"

    try:
        file_path = export_greet_records(
            format=fmt,
            date=date,
        )

        if fmt == "excel":
            return send_file(
                file_path,
                as_attachment=True,
                download_name=os.path.basename(file_path),
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            return send_file(
                file_path,
                as_attachment=True,
                download_name="greet_records_export.json",
                mimetype="application/json",
            )
    except Exception as e:
        logger.exception("导出打招呼记录失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/reply_records")
def api_reply_records():
    """获取回复记录列表（前端展示用，不分页）。

    返回全部记录（倒序），确保前端能看到完整数据。
    之前限制 200 条会导致文件有 358 条但前端只显示 200 条的不一致问题。
    """
    try:
        store = _ensure_reply_store()
        records = store.get_all()
        # 返回全部记录（倒序），不再限制 200 条
        result = [r.to_dict() for r in reversed(records)]
        return jsonify({
            "status": "ok",
            "total": len(records),
            "records": result,
        })
    except Exception as e:
        logger.exception("获取回复记录列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/reply_records/grouped")
def api_reply_records_grouped():
    """获取按聊天对象分组的回复记录。

    将所有回复记录按 chat_name 分组，每组包含：
    - chat_name: 聊天对象名称
    - message_count: 该聊天对象的消息总数
    - last_time: 最新消息的时间戳
    - last_message: 最新收到的消息
    - last_reply: 最新回复的内容
    - records: 该聊天对象的所有记录列表（按时间正序）
    - messages: 该聊天对象的完整对话消息列表（来自 message_store，含HR消息和bot回复）

    返回的分组列表按 last_time 倒序排列（最新的在前）。
    """
    try:
        store = _ensure_reply_store()
        records = store.get_all()

        # 加载完整对话消息（来自 message_store）
        msg_store = MessageStore()
        full_chats = {c["chat_name"]: c for c in msg_store.get_all_chats_detail()}

        # 按 chat_name 分组
        groups = {}
        for r in records:
            chat_name = r.chat_name or "(未知)"
            if chat_name not in groups:
                groups[chat_name] = {
                    "chat_name": chat_name,
                    "message_count": 0,
                    "last_time": "",
                    "last_message": "",
                    "last_reply": "",
                    "records": [],
                    "messages": [],
                }
            d = r.to_dict()
            groups[chat_name]["records"].append(d)
            groups[chat_name]["message_count"] += 1
            # 更新最新消息（按 timestamp 字符串比较）
            timestamp = d.get("timestamp", "") or ""
            if timestamp > groups[chat_name]["last_time"]:
                groups[chat_name]["last_time"] = timestamp
                groups[chat_name]["last_message"] = d.get("received_message", "") or ""
                groups[chat_name]["last_reply"] = d.get("reply_content", "") or ""

        # 合并完整对话消息（来自 message_store）
        for chat_name, full in full_chats.items():
            if chat_name in groups:
                groups[chat_name]["messages"] = full.get("messages", [])
                groups[chat_name]["job_name"] = full.get("job_name", "")
                # 如果 message_store 的时间更新，则更新 last_time
                full_last_time = full.get("last_time", "") or full.get("updated_at", "")
                if full_last_time and full_last_time > groups[chat_name]["last_time"]:
                    groups[chat_name]["last_time"] = full_last_time
                    last_msg = full.get("last_message", "")
                    if last_msg:
                        groups[chat_name]["last_message"] = last_msg
                # 用 message_store 的消息数为准（更准确）
                groups[chat_name]["full_message_count"] = full.get("message_count", 0)
                groups[chat_name]["unread_count"] = full.get("unread_count", 0)
            else:
                # message_store 中有但 reply_records 中没有（例如只有 HR 消息未回复）
                groups[chat_name] = {
                    "chat_name": chat_name,
                    "message_count": 0,
                    "last_time": full.get("last_time", "") or full.get("updated_at", ""),
                    "last_message": full.get("last_message", ""),
                    "last_reply": "",
                    "records": [],
                    "messages": full.get("messages", []),
                    "job_name": full.get("job_name", ""),
                    "full_message_count": full.get("message_count", 0),
                    "unread_count": full.get("unread_count", 0),
                }

        # 转为列表，按最后消息时间倒序排列
        result = sorted(groups.values(), key=lambda x: x.get("last_time", ""), reverse=True)
        return jsonify({
            "status": "ok",
            "groups": result,
            "total_groups": len(result),
            "total_records": len(records),
        })
    except Exception as e:
        logger.exception("获取分组回复记录失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chats")
def api_chats():
    """获取所有聊天会话列表（完整对话消息，用于前端聊天界面）。

    返回每个会话的：
    - chat_name: 聊天对象名称
    - job_name: 岗位名称
    - updated_at: 最后更新时间
    - message_count: 消息总数
    - unread_count: 未读数
    - last_message: 最新消息内容
    - last_time: 最新消息时间
    - messages: 完整消息列表
    """
    try:
        msg_store = MessageStore()
        chats = msg_store.get_all_chats_detail()
        return jsonify({
            "status": "ok",
            "chats": chats,
            "total": len(chats),
        })
    except Exception as e:
        logger.exception("获取聊天列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chats/<path:chat_name>")
def api_chat_detail(chat_name: str):
    """获取指定聊天会话的完整消息列表。

    Args:
        chat_name: 聊天对象名称（URL 路径参数）
    """
    try:
        msg_store = MessageStore()
        detail = msg_store.get_chat_detail(chat_name)
        return jsonify({
            "status": "ok",
            "chat": detail,
        })
    except Exception as e:
        logger.exception("获取聊天详情失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chats/<path:chat_name>/mark_read", methods=["POST"])
def api_chat_mark_read(chat_name: str):
    """标记指定聊天会话的所有 HR 消息为已读。"""
    try:
        msg_store = MessageStore()
        msg_store.mark_chat_read(chat_name)
        return jsonify({"status": "ok", "message": "已标记为已读"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/greet_records")
def api_greet_records():
    """获取打招呼记录列表（前端展示用，不分页）。

    返回全部记录（倒序），确保前端能看到完整数据。
    之前限制 200 条会导致文件有 225 条但前端只显示 200 条的不一致问题。
    """
    try:
        store = _ensure_greet_store()
        records = store.get_all()
        # 返回全部记录（倒序），不再限制 200 条
        result = [r.to_dict() for r in reversed(records)]
        return jsonify({
            "status": "ok",
            "total": len(records),
            "records": result,
        })
    except Exception as e:
        logger.exception("获取打招呼记录列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 数据按天归档 API =====================

def _get_archive_dir():
    """获取归档目录路径，确保目录存在。"""
    archive_dir = PROJECT_ROOT / "data" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir


def _get_active_bot_loop():
    """获取当前活动的 UnifiedBotLoop 实例（用于调用归档方法）。"""
    global _multi_manager
    if _multi_manager is not None:
        # MultiAccountManager._loops 是 dict: account_index -> UnifiedBotLoop
        loops = getattr(_multi_manager, '_loops', {})
        if loops:
            # 取第一个活动的 loop
            for loop in loops.values():
                return loop
    return None


@app.route("/api/archive/list")
def api_archive_list():
    """列出所有归档日期。

    Returns:
        {
            "status": "ok",
            "archives": [
                {"date": "2026-09-20", "greet_count": 50, "reply_count": 30, "size_kb": 12.5},
                ...
            ]
        }
    """
    try:
        archive_dir = _get_archive_dir()
        result = []
        for subdir in sorted(archive_dir.iterdir(), reverse=True):
            if not subdir.is_dir():
                continue
            try:
                from datetime import date as _date
                _date.fromisoformat(subdir.name)  # 验证目录名是有效日期
            except ValueError:
                continue
            # 统计该日期的记录数和大小
            greet_count = 0
            reply_count = 0
            total_size = 0
            for f in subdir.glob("*.json"):
                try:
                    total_size += f.stat().st_size
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    if f.name == "greet_records.json":
                        greet_count = len(data.get("records", []))
                    elif f.name == "reply_records.json":
                        reply_count = len(data.get("records", []))
                except Exception:
                    pass
            result.append({
                "date": subdir.name,
                "greet_count": greet_count,
                "reply_count": reply_count,
                "size_kb": round(total_size / 1024, 1),
            })
        return jsonify({"status": "ok", "archives": result})
    except Exception as e:
        logger.exception("获取归档列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/archive/<date>")
def api_archive_download(date: str):
    """下载指定日期的归档数据。

    Args:
        date: 日期字符串 YYYY-MM-DD

    Returns:
        JSON 文件下载（包含 greet_records + reply_records）
    """
    try:
        archive_dir = _get_archive_dir()
        subdir = archive_dir / date
        if not subdir.exists():
            return jsonify({"status": "error", "message": f"归档日期 {date} 不存在"}), 404

        result = {"date": date, "greet_records": [], "reply_records": []}
        for filename, key in (("greet_records.json", "greet_records"),
                              ("reply_records.json", "reply_records")):
            f = subdir / filename
            if f.exists():
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    result[key] = data.get("records", [])
                except Exception:
                    pass

        # 写入临时文件并返回下载
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                         encoding="utf-8") as tmp:
            json.dump(result, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name

        return send_file(
            tmp_path,
            as_attachment=True,
            download_name=f"archive_{date}.json",
            mimetype="application/json",
        )
    except Exception as e:
        logger.exception("下载归档数据失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/archive/<date>/view")
def api_archive_view(date: str):
    """查看指定日期的归档数据（JSON 返回，不下载）。

    Args:
        date: 日期字符串 YYYY-MM-DD

    Returns:
        {"status": "ok", "date": ..., "greet_records": [...], "reply_records": [...]}
    """
    try:
        archive_dir = _get_archive_dir()
        subdir = archive_dir / date
        if not subdir.exists():
            return jsonify({"status": "error", "message": f"归档日期 {date} 不存在"}), 404

        result = {"date": date, "greet_records": [], "reply_records": []}
        for filename, key in (("greet_records.json", "greet_records"),
                              ("reply_records.json", "reply_records")):
            f = subdir / filename
            if f.exists():
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        data = json.load(fp)
                    result[key] = data.get("records", [])
                except Exception:
                    pass
        return jsonify({"status": "ok", **result})
    except Exception as e:
        logger.exception("查看归档数据失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/archive/auto_clean", methods=["POST"])
def api_archive_auto_clean():
    """手动触发清理超过10天的归档数据。"""
    try:
        archive_dir = _get_archive_dir()
        from datetime import date as _date, timedelta
        retention_days = 10
        today = _date.today()
        cleaned = []
        for subdir in archive_dir.iterdir():
            if not subdir.is_dir():
                continue
            try:
                archive_date = _date.fromisoformat(subdir.name)
                age_days = (today - archive_date).days
                if age_days > retention_days:
                    shutil.rmtree(str(subdir))
                    cleaned.append({"date": subdir.name, "age_days": age_days})
            except ValueError:
                continue
            except Exception as e:
                logger.warning(f"清理归档 {subdir.name} 失败: {e}")
        return jsonify({
            "status": "ok",
            "message": f"已清理 {len(cleaned)} 个过期归档",
            "cleaned": cleaned,
            "retention_days": retention_days,
        })
    except Exception as e:
        logger.exception("清理归档失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/archive/trigger", methods=["POST"])
def api_archive_trigger():
    """手动触发数据归档（将当前数据归档到今天，并清空当前数据）。

    用于跨天时手动触发归档，或在运行中需要归档时调用。
    """
    try:
        loop = _get_active_bot_loop()
        if loop is None:
            return jsonify({"status": "error", "message": "机器人未运行，无法归档"}), 400
        # 强制归档：重置 _last_archived_date 触发归档
        loop._last_archived_date = None
        loop._check_and_archive_daily_data()
        return jsonify({"status": "ok", "message": "数据归档已触发"})
    except Exception as e:
        logger.exception("触发归档失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 账号管理 API =====================

@app.route("/api/accounts", methods=["GET"])
def api_get_accounts():
    """获取账号列表。"""
    cfg = _ensure_config()
    accounts = [
        {
            "name": acc.name,
            "enabled": acc.enabled,
            "cookie_file": acc.cookie_file,
            "image_files": list(acc.image_files),
            "message_interval_min": acc.message_interval_min,
            "message_interval_max": acc.message_interval_max,
            "jobs_count": len(acc.jobs),
        }
        for acc in cfg.greet.accounts
    ]
    return jsonify({"status": "ok", "accounts": accounts})


@app.route("/api/accounts/add", methods=["POST"])
def api_add_account():
    """添加新账号，接收 {name, cookie_file, enabled}。"""
    try:
        data = request.get_json() or {}
        name = data.get("name", "")
        enabled = data.get("enabled", True)

        if not name:
            return jsonify({"status": "error", "message": "账号名称不能为空"}), 400

        cfg = _ensure_config()
        from boss_bot.unified_config import AccountConfig, JobConfig

        # 自动生成独立的 cookie 文件名 — 每个账号必须独立
        # 主账号用 zhipin_cookies.json，后续账号用 zhipin_cookies_1.json, _2.json ...
        new_idx = len(cfg.greet.accounts)
        if new_idx == 0:
            cookie_file = data.get("cookie_file", "zhipin_cookies.json")
        else:
            # 优先使用前端传入的，否则自动生成
            cookie_file = data.get("cookie_file", "") or f"zhipin_cookies_{new_idx}.json"

        new_account = AccountConfig(
            name=name,
            enabled=enabled,
            cookie_file=cookie_file,
            jobs=[JobConfig()],
        )
        cfg.greet.accounts.append(new_account)
        cfg.save()

        return jsonify({"status": "ok", "message": f"账号「{name}」已添加，Cookie文件: {cookie_file}"})
    except Exception as e:
        logger.exception("添加账号失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/accounts/delete", methods=["POST"])
def api_delete_account():
    """删除账号，接收 {index}。"""
    try:
        data = request.get_json() or {}
        index = data.get("index", -1)

        cfg = _ensure_config()
        if index < 0 or index >= len(cfg.greet.accounts):
            return jsonify({"status": "error", "message": "账号索引超出范围"}), 400

        if len(cfg.greet.accounts) <= 1:
            return jsonify({"status": "error", "message": "至少需要保留一个账号"}), 400

        removed = cfg.greet.accounts.pop(index)
        cfg.save()

        return jsonify({"status": "ok", "message": f"账号「{removed.name}」已删除"})
    except Exception as e:
        logger.exception("删除账号失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/accounts/update", methods=["POST"])
def api_update_account():
    """更新账号信息，接收 {index, name, cookie_file, enabled, message_interval_min, message_interval_max}。"""
    try:
        data = request.get_json() or {}
        index = data.get("index", -1)

        cfg = _ensure_config()
        if index < 0 or index >= len(cfg.greet.accounts):
            return jsonify({"status": "error", "message": "账号索引超出范围"}), 400

        acc = cfg.greet.accounts[index]
        if "name" in data:
            acc.name = str(data["name"])
        if "cookie_file" in data:
            acc.cookie_file = str(data["cookie_file"])
        if "enabled" in data:
            acc.enabled = bool(data["enabled"])
        if "message_interval_min" in data:
            acc.message_interval_min = int(data["message_interval_min"])
        if "message_interval_max" in data:
            acc.message_interval_max = int(data["message_interval_max"])

        cfg.save()

        return jsonify({"status": "ok", "message": f"账号「{acc.name}」已更新"})
    except Exception as e:
        logger.exception("更新账号失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 岗位管理 API =====================

@app.route("/api/jobs", methods=["GET"])
def api_get_jobs():
    """获取岗位列表，支持按账号筛选。"""
    cfg = _ensure_config()
    account_index = request.args.get("account_index", type=int)

    result = []
    for acc_idx, acc in enumerate(cfg.greet.accounts):
        if account_index is not None and acc_idx != account_index:
            continue
        for job_idx, job in enumerate(acc.jobs):
            result.append({
                "account_index": acc_idx,
                "account_name": acc.name,
                "job_index": job_idx,
                "query": job.query,
                "city": job.city,
                "scroll_pages": job.scroll_pages,
                "greeting_message": job.greeting_message,
                "enabled": job.enabled,
                "image_files": list(job.image_files),
            })

    return jsonify({"status": "ok", "jobs": result})


@app.route("/api/jobs/add", methods=["POST"])
def api_add_job():
    """添加岗位，接收 {account_index, query, city, scroll_pages, greeting_message, enabled, images}。"""
    try:
        data = request.get_json() or {}
        account_index = data.get("account_index", 0)

        cfg = _ensure_config()
        if account_index < 0 or account_index >= len(cfg.greet.accounts):
            return jsonify({"status": "error", "message": "账号索引超出范围"}), 400

        from boss_bot.unified_config import JobConfig
        new_job = JobConfig(
            query=data.get("query", "数据分析"),
            city=data.get("city", "上海"),
            scroll_pages=data.get("scroll_pages", 5),
            greeting_message=data.get("greeting_message", DEFAULT_GREETING),
            enabled=data.get("enabled", True),
            image_files=data.get("images", []),
        )
        cfg.greet.accounts[account_index].jobs.append(new_job)
        cfg.save()

        return jsonify({"status": "ok", "message": "岗位已添加"})
    except Exception as e:
        logger.exception("添加岗位失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/jobs/delete", methods=["POST"])
def api_delete_job():
    """删除岗位，接收 {account_index, job_index}。"""
    try:
        data = request.get_json() or {}
        account_index = data.get("account_index", -1)
        job_index = data.get("job_index", -1)

        cfg = _ensure_config()
        if account_index < 0 or account_index >= len(cfg.greet.accounts):
            return jsonify({"status": "error", "message": "账号索引超出范围"}), 400

        acc = cfg.greet.accounts[account_index]
        if job_index < 0 or job_index >= len(acc.jobs):
            return jsonify({"status": "error", "message": "岗位索引超出范围"}), 400

        if len(acc.jobs) <= 1:
            return jsonify({"status": "error", "message": "每个账号至少需要保留一个岗位"}), 400

        acc.jobs.pop(job_index)
        cfg.save()

        return jsonify({"status": "ok", "message": "岗位已删除"})
    except Exception as e:
        logger.exception("删除岗位失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/jobs/update", methods=["POST"])
def api_update_job():
    """更新岗位信息，接收 {account_index, job_index, query, city, scroll_pages, greeting_message, enabled, images}。"""
    try:
        data = request.get_json() or {}
        account_index = data.get("account_index", -1)
        job_index = data.get("job_index", -1)

        cfg = _ensure_config()
        if account_index < 0 or account_index >= len(cfg.greet.accounts):
            return jsonify({"status": "error", "message": "账号索引超出范围"}), 400

        acc = cfg.greet.accounts[account_index]
        if job_index < 0 or job_index >= len(acc.jobs):
            return jsonify({"status": "error", "message": "岗位索引超出范围"}), 400

        job = acc.jobs[job_index]
        if "query" in data:
            job.query = str(data["query"])
        if "city" in data:
            job.city = str(data["city"])
        if "scroll_pages" in data:
            job.scroll_pages = int(data["scroll_pages"])
        if "greeting_message" in data:
            job.greeting_message = str(data["greeting_message"])
        if "enabled" in data:
            job.enabled = bool(data["enabled"])
        if "images" in data and isinstance(data["images"], list):
            job.image_files = list(data["images"])

        cfg.save()

        return jsonify({"status": "ok", "message": "岗位已更新"})
    except Exception as e:
        logger.exception("更新岗位失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 图片管理 API =====================

@app.route("/api/images", methods=["GET"])
def api_get_images():
    """获取已上传的图片列表（扫描 static/dashboard/ 目录）。"""
    try:
        images = []
        if DASHBOARD_DIR.exists():
            for f in DASHBOARD_DIR.iterdir():
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    stat = f.stat()
                    images.append({
                        "filename": f.name,
                        "url": f"dashboard/{f.name}",
                        "size": stat.st_size,
                        "created": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
                    })
        images.sort(key=lambda x: x["created"], reverse=True)
        return jsonify({"status": "ok", "images": images})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/images/delete", methods=["POST"])
def api_delete_image():
    """删除指定图片，接收 {filename}。"""
    try:
        data = request.get_json() or {}
        filename = data.get("filename", "")

        safe_name = os.path.basename(filename)
        if not safe_name:
            return jsonify({"status": "error", "message": "文件名不能为空"}), 400

        file_path = DASHBOARD_DIR / safe_name
        if not file_path.exists() or not file_path.is_file():
            return jsonify({"status": "error", "message": "文件不存在"}), 404

        file_path.unlink()
        return jsonify({"status": "ok", "message": f"图片「{safe_name}」已删除"})
    except Exception as e:
        logger.exception("删除图片失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 主题设置 API =====================

@app.route("/api/theme", methods=["GET"])
def api_get_theme():
    """获取当前主题设置（light/dark）。"""
    try:
        config_dict = load_config()
        theme = config_dict.get("theme", "light")
        return jsonify({"status": "ok", "theme": theme})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/theme", methods=["POST"])
def api_save_theme():
    """保存主题设置。"""
    try:
        data = request.get_json() or {}
        theme = data.get("theme", "light")

        if theme not in ("light", "dark"):
            return jsonify({"status": "error", "message": "主题只能是 light 或 dark"}), 400

        config_dict = load_config()
        config_dict["theme"] = theme
        save_config(config_dict)

        return jsonify({"status": "ok", "message": f"主题已设置为 {theme}"})
    except Exception as e:
        logger.exception("保存主题失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 规则管理 API =====================

@app.route("/api/rules", methods=["GET"])
def api_get_rules():
    """获取回复规则和重要关键词。"""
    cfg = _ensure_config()
    rules = {
        "reply_rules": dict(cfg.rules.reply_rules),
        "importance_keywords": list(cfg.rules.importance_keywords),
    }
    return jsonify({"status": "ok", "rules": rules})


@app.route("/api/rules", methods=["POST"])
def api_save_rules():
    """保存回复规则和重要关键词。

    三端一致策略：同时写入 bot_config.json 和 config_overrides.json，
    确保两个文件中的 reply_rules 和 importance_keywords 完全一致。
    后端 UnifiedConfig.load() 会先加载 bot_config.json，再用 config_overrides.json 覆盖，
    因此两个文件保持一致可避免任何不一致问题。
    """
    global _config
    try:
        data = request.get_json() or {}
        rules_data = data.get("rules", data)

        # 1. 更新内存中的配置对象
        cfg = _ensure_config()
        if "reply_rules" in rules_data and isinstance(rules_data["reply_rules"], dict):
            cfg.rules.reply_rules = dict(rules_data["reply_rules"])
        if "importance_keywords" in rules_data and isinstance(rules_data["importance_keywords"], list):
            cfg.rules.importance_keywords = list(rules_data["importance_keywords"])

        # 2. 持久化到 config_overrides.json
        overrides = {}
        if OVERRIDES_FILE.exists():
            try:
                with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
            except Exception:
                overrides = {}
        if "reply_rules" in rules_data and isinstance(rules_data["reply_rules"], dict):
            overrides["reply_rules"] = dict(rules_data["reply_rules"])
        if "importance_keywords" in rules_data and isinstance(rules_data["importance_keywords"], list):
            overrides["importance_keywords"] = list(rules_data["importance_keywords"])
        with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)

        # 3. 同步写入 bot_config.json，保持三端一致
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                bot_cfg = json.load(f)
            if not isinstance(bot_cfg, dict):
                bot_cfg = {}
        except Exception:
            bot_cfg = {}
        if "reply_rules" in rules_data and isinstance(rules_data["reply_rules"], dict):
            bot_cfg["reply_rules"] = dict(rules_data["reply_rules"])
        if "importance_keywords" in rules_data and isinstance(rules_data["importance_keywords"], list):
            bot_cfg["importance_keywords"] = list(rules_data["importance_keywords"])
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_cfg, f, ensure_ascii=False, indent=2)

        # 4. 重新加载配置以保持一致性
        _config = UnifiedConfig.load()

        return jsonify({"status": "ok", "message": "规则已保存"})
    except Exception as e:
        logger.exception("保存规则失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 模板管理 API =====================

@app.route("/api/templates", methods=["GET"])
def api_get_templates():
    """获取回复模板（salary_reply, interview_time_reply 等）。"""
    cfg = _ensure_config()
    templates = {
        "salary_reply": cfg.templates.salary_reply,
        "interview_time_reply": cfg.templates.interview_time_reply,
        "job_content_reply": cfg.templates.job_content_reply,
        "greeting_reply": cfg.templates.greeting_reply,
        "default_reply": cfg.templates.default_reply,
        "resume_duplicate_reply": cfg.templates.resume_duplicate_reply,
        "resume_unavailable_reply": cfg.templates.resume_unavailable_reply,
    }
    return jsonify({"status": "ok", "templates": templates})


@app.route("/api/templates", methods=["POST"])
def api_save_templates():
    """保存回复模板。

    三端一致策略：同时写入 bot_config.json（templates 小写键）和
    config_overrides.json（reply_templates 大写键），确保两个文件完全一致。
    后端 UnifiedConfig.load() 会先加载 bot_config.json 的 templates，
    再用 config_overrides.json 的 reply_templates 覆盖，因此两个文件保持一致
    可避免任何不一致问题。
    """
    global _config
    try:
        try:
            data = request.get_json() or {}
        except Exception:
            # 编码问题降级：手动解析请求体
            raw = request.get_data()
            data = json.loads(raw.decode('utf-8', errors='replace')) if raw else {}
        templates_data = data.get("templates", data)

        # 1. 更新内存中的配置对象
        cfg = _ensure_config()
        if "salary_reply" in templates_data:
            cfg.templates.salary_reply = str(templates_data["salary_reply"])
        if "interview_time_reply" in templates_data:
            cfg.templates.interview_time_reply = str(templates_data["interview_time_reply"])
        if "job_content_reply" in templates_data:
            cfg.templates.job_content_reply = str(templates_data["job_content_reply"])
        if "greeting_reply" in templates_data:
            cfg.templates.greeting_reply = str(templates_data["greeting_reply"])
        if "default_reply" in templates_data:
            cfg.templates.default_reply = str(templates_data["default_reply"])
        if "resume_duplicate_reply" in templates_data:
            cfg.templates.resume_duplicate_reply = str(templates_data["resume_duplicate_reply"])
        if "resume_unavailable_reply" in templates_data:
            cfg.templates.resume_unavailable_reply = str(templates_data["resume_unavailable_reply"])

        # 2. 持久化到 config_overrides.json（reply_templates 大写键）
        overrides = {}
        if OVERRIDES_FILE.exists():
            try:
                with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
            except Exception:
                overrides = {}
        # _apply_overrides 使用大写键名读取 reply_templates
        rt = overrides.get("reply_templates", {})
        if not isinstance(rt, dict):
            rt = {}
        if "salary_reply" in templates_data:
            rt["SALARY_REPLY"] = str(templates_data["salary_reply"])
        if "interview_time_reply" in templates_data:
            rt["INTERVIEW_TIME_REPLY"] = str(templates_data["interview_time_reply"])
        if "job_content_reply" in templates_data:
            rt["JOB_CONTENT_REPLY"] = str(templates_data["job_content_reply"])
        if "greeting_reply" in templates_data:
            rt["GREETING_REPLY"] = str(templates_data["greeting_reply"])
        if "default_reply" in templates_data:
            rt["DEFAULT_REPLY"] = str(templates_data["default_reply"])
        if "resume_duplicate_reply" in templates_data:
            rt["RESUME_DUPLICATE_REPLY"] = str(templates_data["resume_duplicate_reply"])
        if "resume_unavailable_reply" in templates_data:
            rt["RESUME_UNAVAILABLE_REPLY"] = str(templates_data["resume_unavailable_reply"])
        overrides["reply_templates"] = rt
        with open(OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump(overrides, f, ensure_ascii=False, indent=2)

        # 3. 同步写入 bot_config.json（templates 小写键），保持三端一致
        try:
            with open(BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
                bot_cfg = json.load(f)
            if not isinstance(bot_cfg, dict):
                bot_cfg = {}
        except Exception:
            bot_cfg = {}
        bt = bot_cfg.get("templates", {})
        if not isinstance(bt, dict):
            bt = {}
        if "salary_reply" in templates_data:
            bt["salary_reply"] = str(templates_data["salary_reply"])
        if "interview_time_reply" in templates_data:
            bt["interview_time_reply"] = str(templates_data["interview_time_reply"])
        if "job_content_reply" in templates_data:
            bt["job_content_reply"] = str(templates_data["job_content_reply"])
        if "greeting_reply" in templates_data:
            bt["greeting_reply"] = str(templates_data["greeting_reply"])
        if "default_reply" in templates_data:
            bt["default_reply"] = str(templates_data["default_reply"])
        if "resume_duplicate_reply" in templates_data:
            bt["resume_duplicate_reply"] = str(templates_data["resume_duplicate_reply"])
        if "resume_unavailable_reply" in templates_data:
            bt["resume_unavailable_reply"] = str(templates_data["resume_unavailable_reply"])
        bot_cfg["templates"] = bt
        with open(BOT_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(bot_cfg, f, ensure_ascii=False, indent=2)

        # 4. 重新加载配置以保持一致性
        _config = UnifiedConfig.load()

        return jsonify({"status": "ok", "message": "模板已保存"})
    except Exception as e:
        logger.exception("保存模板失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 风控通知 API =====================

@app.route("/api/notifications", methods=["GET"])
def api_get_notifications():
    """获取所有通知列表。

    查询参数：
        unread_only: 为 true 时仅返回未读通知
    """
    try:
        unread_only = request.args.get("unread_only", "").lower() in ("true", "1", "yes")
        with _notifications_lock:
            if unread_only:
                result = [n for n in _notifications if not n.get("read", False)]
            else:
                result = list(_notifications)
        return jsonify({
            "status": "ok",
            "total": len(result),
            "notifications": result,
        })
    except Exception as e:
        logger.exception("获取通知列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/notifications/unread_count", methods=["GET"])
def api_notifications_unread_count():
    """获取未读通知数量。"""
    try:
        with _notifications_lock:
            count = sum(1 for n in _notifications if not n.get("read", False))
        return jsonify({"status": "ok", "unread_count": count})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/notifications/<notif_id>/read", methods=["POST"])
def api_notification_mark_read(notif_id: str):
    """标记指定通知为已读。"""
    try:
        with _notifications_lock:
            for n in _notifications:
                if n.get("id") == notif_id:
                    n["read"] = True
                    _save_notifications()
                    return jsonify({"status": "ok", "message": "通知已标记为已读"})
        return jsonify({"status": "error", "message": "通知不存在"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/notifications/read_all", methods=["POST"])
def api_notifications_mark_all_read():
    """标记所有通知为已读。"""
    try:
        with _notifications_lock:
            for n in _notifications:
                n["read"] = True
            _save_notifications()
        return jsonify({"status": "ok", "message": "所有通知已标记为已读"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/notifications/<notif_id>", methods=["DELETE"])
def api_notification_delete(notif_id: str):
    """删除指定通知。"""
    try:
        with _notifications_lock:
            for i, n in enumerate(_notifications):
                if n.get("id") == notif_id:
                    _notifications.pop(i)
                    _save_notifications()
                    return jsonify({"status": "ok", "message": "通知已删除"})
        return jsonify({"status": "error", "message": "通知不存在"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/notifications", methods=["DELETE"])
def api_notifications_clear():
    """清空所有通知。"""
    try:
        with _notifications_lock:
            _notifications.clear()
            _save_notifications()
        return jsonify({"status": "ok", "message": "所有通知已清空"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 自进化 API =====================

def _ensure_self_evolve() -> SelfEvolveEngine:
    """确保 _self_evolve 已初始化。"""
    global _self_evolve
    if _self_evolve is None:
        _self_evolve = SelfEvolveEngine(
            config={"enabled": True},
            log_callback=lambda msg: logger.info(msg),
        )
    return _self_evolve


@app.route("/api/evolution/report", methods=["GET"])
def api_evolution_report():
    """获取自进化报告 — 回复效果统计、模板效果分析、策略调整历史。"""
    try:
        engine = _ensure_self_evolve()
        report = engine.get_evolution_report()
        return jsonify({"status": "ok", "report": report})
    except Exception as e:
        logger.exception("获取进化报告失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/optimize", methods=["POST"])
def api_evolution_optimize():
    """手动触发策略优化。"""
    try:
        engine = _ensure_self_evolve()
        result = engine.auto_optimize_strategy()
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        logger.exception("触发策略优化失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/history", methods=["GET"])
def api_evolution_history():
    """获取进化历史 — 策略调整记录和最近回复记录。"""
    try:
        engine = _ensure_self_evolve()
        report = engine.get_evolution_report()
        history = {
            "strategy_adjustments": report.get("strategy_adjustments", []),
            "recent_records": report.get("recent_records", []),
        }
        return jsonify({"status": "ok", "history": history})
    except Exception as e:
        logger.exception("获取进化历史失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/status", methods=["GET"])
def api_evolution_status():
    """获取自进化功能状态。"""
    try:
        engine = _ensure_self_evolve()
        return jsonify({
            "status": "ok",
            "enabled": engine.enabled,
            "pending_evaluations": engine.get_pending_evaluations(),
            "total_replies": engine._reply_stats.get("total_replies", 0),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/toggle", methods=["POST"])
def api_evolution_toggle():
    """启用或禁用自进化功能。"""
    try:
        data = request.get_json() or {}
        enabled = data.get("enabled", True)
        engine = _ensure_self_evolve()
        engine.set_enabled(bool(enabled))
        return jsonify({"status": "ok", "message": f"自进化功能已{'启用' if enabled else '禁用'}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/reset", methods=["POST"])
def api_evolution_reset():
    """重置进化统计数据。"""
    try:
        engine = _ensure_self_evolve()
        engine.reset_stats()
        return jsonify({"status": "ok", "message": "进化统计数据已重置"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== 自进化质量评分 API =====================

@app.route("/api/self_evolve/report", methods=["GET"])
def api_self_evolve_report():
    """获取自进化质量评分报告。

    返回回复质量评分、规则调整建议、模板优化建议等完整报告。
    可选 query 参数 limit 控制评估的回复数量（默认 50）。
    """
    try:
        engine = _ensure_self_evolve()
        limit = request.args.get("limit", 50, type=int)

        # 获取评分数据
        evaluation = engine.evaluate_reply_quality(limit=limit)

        # 获取最近的规则调整和模板优化记录
        with engine._lock:
            rule_adjustments = list(engine._rule_adjustments[-20:])
            template_optimizations = list(engine._template_optimizations[-20:])
            template_usage_stats = {
                k: dict(v) for k, v in engine._template_usage_stats.items()
            }

        # 生成人类可读报告
        report_text = engine.generate_report(
            evaluation=evaluation,
            rule_adjustments=rule_adjustments,
            template_optimizations=template_optimizations,
        )

        return jsonify({
            "status": "ok",
            "evaluation": evaluation,
            "rule_adjustments": rule_adjustments,
            "template_optimizations": template_optimizations,
            "template_usage_stats": template_usage_stats,
            "report": report_text,
        })
    except Exception as e:
        logger.exception("获取自进化报告失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/self_evolve/run", methods=["POST"])
def api_self_evolve_run():
    """执行一次完整的自进化周期。

    流程：评估回复质量 → 自动调整规则 → 优化模板 → 生成报告 → 持久化。
    可选 body 参数 limit 控制评估的回复数量（默认 50）。
    """
    try:
        engine = _ensure_self_evolve()
        data = request.get_json() or {}
        limit = data.get("limit", 50)

        # 如果指定了 limit，先评估指定数量的回复
        if limit != 50:
            evaluation = engine.evaluate_reply_quality(limit=int(limit))
            rule_adjustments = engine.auto_adjust_rules(evaluation)
            template_optimizations = engine.optimize_templates()
            report_text = engine.generate_report(
                evaluation=evaluation,
                rule_adjustments=rule_adjustments,
                template_optimizations=template_optimizations,
            )
            result = {
                "evaluation": evaluation,
                "rule_adjustments": rule_adjustments,
                "template_optimizations": template_optimizations,
                "report": report_text,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            # 使用默认的自进化周期
            result = engine.run_evolution_cycle()

        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        logger.exception("执行自进化周期失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/self_evolve/evaluate", methods=["POST"])
def api_self_evolve_evaluate():
    """仅评估回复质量（不执行完整周期）。

    可选 body 参数 limit 控制评估的回复数量（默认 50）。
    """
    try:
        engine = _ensure_self_evolve()
        data = request.get_json() or {}
        limit = data.get("limit", 50)
        result = engine.evaluate_reply_quality(limit=int(limit))
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        logger.exception("评估回复质量失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/self_evolve/adjust_rules", methods=["POST"])
def api_self_evolve_adjust_rules():
    """根据评估结果自动调整回复规则。

    可选 body 参数 evaluation 传入评估结果，不传则自动评估。
    """
    try:
        engine = _ensure_self_evolve()
        data = request.get_json() or {}
        evaluation = data.get("evaluation")

        if not evaluation:
            evaluation = engine.evaluate_reply_quality()

        adjustments = engine.auto_adjust_rules(evaluation)
        return jsonify({"status": "ok", "adjustments": adjustments})
    except Exception as e:
        logger.exception("自动调整规则失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/self_evolve/optimize_templates", methods=["POST"])
def api_self_evolve_optimize_templates():
    """分析并优化回复模板。"""
    try:
        engine = _ensure_self_evolve()
        optimizations = engine.optimize_templates()
        return jsonify({"status": "ok", "optimizations": optimizations})
    except Exception as e:
        logger.exception("优化模板失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== SocketIO 事件 =====================

@socketio.on("connect")
def on_connect():
    """客户端连接时推送当前状态。"""
    emit("connected", {"data": "BOSS Bot 统一管理面板已连接"})
    manager = _multi_manager
    if manager is not None:
        emit("status_update", manager.get_status())
    # 推送最近的日志
    with log_buffer_lock:
        recent_logs = list(log_buffer[-50:])
    for log_entry in recent_logs:
        emit("bot_log", {
            "time": log_entry["time"],
            "message": f"[{log_entry['level']}] {log_entry['message']}",
            "level": log_entry["level"],
        })


@socketio.on("disconnect")
def on_disconnect():
    logger.info("客户端已断开连接")


@socketio.on("start_all")
def on_start_all():
    """启动所有账号的机器人。"""
    import threading
    logger.info("收到 start_all 事件，正在启动所有账号...")
    emit("bot_log", {"time": _now(), "message": "正在启动所有账号...", "level": "INFO"})
    emit("bot_status", {"running": True})
    
    def _start_thread():
        try:
            cfg = _ensure_config()
            global _multi_manager
            from boss_bot.main_loop import MultiAccountManager
            if _multi_manager is None:
                _multi_manager = MultiAccountManager(cfg)
            _multi_manager.start_all()
            socketio.emit("bot_log", {"time": _now(), "message": "所有账号已启动", "level": "SUCCESS"})
            socketio.emit("scheduler_status", {"running": True, "current": None})
        except Exception as e:
            logger.error(f"启动失败: {e}", exc_info=True)
            socketio.emit("bot_log", {"time": _now(), "message": f"启动失败: {e}", "level": "ERROR"})
            socketio.emit("bot_status", {"running": False})
    
    threading.Thread(target=_start_thread, daemon=True).start()


@socketio.on("stop_all")
def on_stop_all():
    """停止所有账号的机器人。"""
    logger.info("收到 stop_all 事件，正在停止所有账号...")
    emit("bot_log", {"time": _now(), "message": "正在停止所有账号...", "level": "INFO"})
    emit("bot_status", {"running": False})
    emit("scheduler_status", {"running": False, "current": None})
    
    def _stop_thread():
        try:
            global _multi_manager
            if _multi_manager is not None:
                _multi_manager.stop_all()
            socketio.emit("bot_log", {"time": _now(), "message": "所有账号已停止", "level": "SUCCESS"})
        except Exception as e:
            logger.error(f"停止失败: {e}", exc_info=True)
            socketio.emit("bot_log", {"time": _now(), "message": f"停止失败: {e}", "level": "ERROR"})
    
    import threading
    threading.Thread(target=_stop_thread, daemon=True).start()


@socketio.on("confirm_login")
def on_confirm_login():
    """确认登录。"""
    logger.info("收到 confirm_login 事件")
    emit("bot_log", {"time": _now(), "message": "登录已确认", "level": "SUCCESS"})
    global _multi_manager
    if _multi_manager is not None:
        _multi_manager.confirm_login()


@socketio.on("check_login")
def on_check_login():
    """检查登录状态。"""
    logger.info("收到 check_login 事件")
    emit("login_result", {"success": True})


@socketio.on("stop_login_modal")
def on_stop_login_modal():
    """关闭登录弹窗。"""
    logger.info("收到 stop_login_modal 事件")


# ===================== 日志清理 =====================

def cleanup_old_logs():
    """清理旧日志文件 — 删除超过7天的日志和临时flask日志。"""
    import time
    from pathlib import Path

    log_dir = PROJECT_ROOT / "logs"
    if not log_dir.exists():
        return

    now = time.time()
    max_age_seconds = 7 * 24 * 3600  # 7天
    cleaned = 0

    for f in log_dir.iterdir():
        if not f.is_file():
            continue
        # 删除超过7天的日志文件
        try:
            file_age = now - f.stat().st_mtime
            if file_age > max_age_seconds:
                f.unlink()
                cleaned += 1
                continue
        except Exception:
            pass

        # 删除临时 flask 日志文件（flask_e2e*.log, flask_output*.log）
        if f.name.startswith("flask_e2e") or f.name.startswith("flask_output"):
            try:
                f.unlink()
                cleaned += 1
            except Exception:
                pass

        # 大文件截断：超过20MB的日志文件清空内容
        try:
            if f.stat().st_size > 20 * 1024 * 1024:
                f.write_text("", encoding="utf-8")
                cleaned += 1
        except Exception:
            pass

    if cleaned > 0:
        logger.info(f"日志清理: 已清理 {cleaned} 个文件")


def _log_cleanup_loop():
    """后台线程：每天清理一次旧日志。"""
    import time
    while True:
        try:
            cleanup_old_logs()
        except Exception:
            pass
        time.sleep(24 * 3600)  # 24小时执行一次


# ===================== 主入口 =====================

def main():
    """启动 Flask 应用。"""
    logger.info("=" * 50)
    logger.info("BOSS 统一机器人 Web 管理界面")
    logger.info(f"项目根目录: {PROJECT_ROOT}")
    logger.info(f"配置文件: {BOT_CONFIG_FILE}")
    logger.info("=" * 50)

    # 预加载配置
    _ensure_config()
    logger.info("配置已加载")

    # 加载持久化通知
    _load_notifications()
    logger.info(f"已加载 {len(_notifications)} 条通知")

    # 启动日志清理线程
    import threading
    threading.Thread(target=_log_cleanup_loop, daemon=True).start()
    cleanup_old_logs()  # 启动时立即清理一次
    logger.info("日志清理已启动")

    # 启动 Flask-SocketIO
    _run_kwargs = {"host": "0.0.0.0", "port": 5000, "debug": False, "use_reloader": False}
    if _socketio_kwargs.get("async_mode") == "threading":
        _run_kwargs["allow_unsafe_werkzeug"] = True
    socketio.run(app, **_run_kwargs)


if __name__ == "__main__":
    main()
