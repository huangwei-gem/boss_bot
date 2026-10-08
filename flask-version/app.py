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
import tempfile
import logging
import warnings
from logging.handlers import TimedRotatingFileHandler

from boss_bot.safe_log import SafeTimedRotatingFileHandler
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
    account_file,
    UnifiedConfig, BASE_DIR, BOT_CONFIG_FILE, USER_PROFILE_FILE,
    OVERRIDES_FILE,
    load_config, save_config, save_overrides, validate_config,
    ACCOUNT_OVERLAY_LOCKED, ACCOUNT_OVERLAY_SECTIONS, diff_against,
)
from boss_bot.judgement_review import apply_suggestion, build_review
from boss_bot.greet_engine import account_greeting_ready, account_greeting_mode
from boss_bot.greeting import compose_account_default, ensure_account_default
from boss_bot.main_loop import UnifiedBotLoop, MultiAccountManager
from boss_bot.self_evolve import SelfEvolveEngine
from boss_bot.browser_launcher import browser_mode
from boss_bot.reply_record import (
    ReplyRecord, ReplyRecordStore, GreetRecordStore,
    export_reply_records, export_greet_records,
    _get_reply_store, _get_greet_store,
)
from boss_bot.message_store import MessageStore
from boss_bot.contact_ledger import contact_rows

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

# 文件日志处理器 — 记录 DEBUG 及以上级别，按日期轮转，保留7天。
# 用 SafeTimedRotatingFileHandler 而不是标准库那个：Windows 上午夜 rename 撞开着的
# 文件句柄会失败，标准库打完 traceback 就把流关掉，此后当天日志一个字都不落
# （2026-10-07 23:59:50 之后 logs/boss_bot.log 停住，两个号却照常投了一夜）。
file_handler = SafeTimedRotatingFileHandler(
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
# 关键修复：避免重复添加 handler 导致日志重复输出。
# 检查是否已存在相同类型的 handler，存在则不重复添加。
_has_web_handler = any(isinstance(h, WebLogHandler) for h in root_logger.handlers)
if not _has_web_handler:
    root_logger.addHandler(web_handler)
_has_file_handler = any(
    isinstance(h, TimedRotatingFileHandler) for h in root_logger.handlers
)
if not _has_file_handler:
    root_logger.addHandler(file_handler)

logger = logging.getLogger("boss-web")
# propagate 必须留着：这个 logger 自己没有 handler，文件与界面两个处理器都挂在
# root logger 上。以前在这儿写了 propagate=False（当时的理由是"防重复"），
# 结果是面板自己的 logger.info 全被丢掉——日志文件里连一条 boss-web 都没有，
# 想查"谁把投递停了"根本查不到。
logger.setLevel(logging.INFO)

# ===================== Flask 应用 =====================

app = Flask(__name__, template_folder="templates")
app.config["SECRET_KEY"] = os.urandom(24).hex()
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# 自动选择最佳 async_mode：gevent > threading（eventlet已弃用，不再使用）
# WebSocket 稳定性参数：
#   ping_timeout=60   : 客户端必须在 60s 内回复心跳，否则判定断连
#   ping_interval=25  : 服务端每 25s 发送一次心跳包
#   max_http_buffer_size=10MB : 单帧最大缓冲区，避免大消息触发 "Invalid transport"
_socketio_kwargs = {
    "cors_allowed_origins": "*",
    "ping_timeout": 60,
    "ping_interval": 25,
    "max_http_buffer_size": 10 * 1024 * 1024,
}
try:
    import gevent  # noqa: F401
    from gevent import monkey
    monkey.patch_all()  # 协程化标准库，支持 WebSocket
    _socketio_kwargs["async_mode"] = "gevent"
    # gevent 下显式声明允许 polling+websocket 双传输，
    # 客户端可按网络环境自动降级，避免 "Invalid transport for session" 错误
    _socketio_kwargs["transports"] = ["polling", "websocket"]
except Exception:
    # gevent 不可用或 monkey.patch_all() 失败时，降级到 threading
    _socketio_kwargs["async_mode"] = "threading"
    _socketio_kwargs["transports"] = ["polling", "websocket"]

socketio = SocketIO(app, **_socketio_kwargs)

# 抑制 engineio "Invalid transport for session" 错误日志（降级为 DEBUG）
# 该错误通常由客户端在网络抖动后从 websocket 降级到 polling 引起，
# 属于预期行为，不需要以 ERROR 级别刷屏。
for _noisy_logger in ("engineio.server", "socketio.server"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

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
# 已加载那份配置对应的文件指纹，用来发现"文件被别处改了"
_config_stamp_cached: Optional[tuple] = None
_status_thread: Optional[threading.Thread] = None
_status_stop = threading.Event()
_self_evolve: Optional[SelfEvolveEngine] = None
_UNSET = object()
# 「全部账号」时弹窗按号展示：账号 -> 未运行状态下的面板引擎（跑着的账号直接用它的引擎）
_panel_evolve_cache: dict = {}

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


def _add_notification(ntype: str, message: str,
                     account_index: Optional[int] = None,
                     account_name: str = ""):
    """添加一条通知并持久化。

    Args:
        ntype: 通知类型，如 "wind_control"、"error"、"info"
        message: 通知内容
        account_index: 触发通知的账号索引；风控/封号是账号级事件，
            不带账号看不出是哪个号中招
        account_name: 账号显示名
    """
    global _notifications
    notif = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now().isoformat(),
        "type": ntype,
        "message": message,
        "read": False,
    }
    if account_index is not None:
        notif["account_index"] = account_index
    if account_name:
        notif["account_name"] = account_name
    with _notifications_lock:
        _notifications.insert(0, notif)  # 最新的在前
        if len(_notifications) > _MAX_NOTIFICATIONS:
            _notifications = _notifications[:_MAX_NOTIFICATIONS]
        _save_notifications()


def _config_stamp():
    """bot_config.json 的 (修改时间, 大小)，读不到返回 None。"""
    import boss_bot.unified_config as uc
    try:
        st = os.stat(uc.BOT_CONFIG_FILE)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _config_file_parses() -> bool:
    """文件现在能不能解析。UnifiedConfig.load() 读不动时会静默退回默认值，
    直接拿它覆盖内存里那份好配置，界面就会突然变成出厂设置。"""
    import boss_bot.unified_config as uc
    try:
        with open(uc.BOT_CONFIG_FILE, "r", encoding="utf-8") as f:
            json.load(f)
        return True
    except Exception:
        return False


def _ensure_config() -> UnifiedConfig:
    """返回配置；文件被外部改过时重新读盘。

    界面保存配置写的都是内存里这一份。外部改动（脚本清理接口列表、手改文件、
    引擎热重载）如果不重读，下一次点保存就把旧内容整份写回磁盘，
    表现为"删掉的 AI 接口又复活了"。
    """
    global _config, _config_stamp_cached
    stamp = _config_stamp()
    if _config is None:
        _config = UnifiedConfig.load()
        _config_stamp_cached = stamp
    elif stamp is not None and stamp != _config_stamp_cached:
        if _config_file_parses():
            _config = UnifiedConfig.load()
            logger.info("检测到 bot_config.json 被外部改动，已重新加载")
        else:
            logger.warning("bot_config.json 被改动但当前内容解析不了，"
                           "暂继续用内存里那份配置")
        _config_stamp_cached = stamp
    if _ensure_greeting_defaults(_config):
        # 刚自己写过盘：不把这次改动当成"外部改动"，否则下一次进来又重载一遍
        _config_stamp_cached = _config_stamp()
    return _config


def _ensure_greeting_defaults(cfg: UnifiedConfig) -> bool:
    """账号没写招呼语时，按这个账号自己的信息先落一条默认。

    口径 2026-10-03：留空不再等于"这一轮一条都不发"（实测过 218 条全跳过、0 投递，
    用户只觉得"日志和记录对不上"）。现在生成一条能发的账号默认写进配置，界面里
    看到的就是将要发出去的那句话，改了立刻生效；发送时 AI 再按岗位+公司+JD 现编。
    """
    changed = False
    for acc in cfg.greet.accounts:
        if ensure_account_default(acc, cfg.resume, cfg.user_profile):
            changed = True
            logger.info(f"账号「{acc.name}」没写招呼语，已按本账号信息生成一条默认")
    if changed:
        cfg.save()
    return changed


def _ensure_manager() -> MultiAccountManager:
    """确保 _multi_manager 已初始化。"""
    global _multi_manager
    if _multi_manager is None:
        cfg = _ensure_config()

        def greet_event_callback(event_data: dict):
            """投递事件回调 — 推送结构化投递记录到前端表格。"""
            try:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                event_data["time"] = now[11:]
                # 前端按日期筛选/人性化时间都读 timestamp，只给 HH:MM:SS 的
                # 行会被日期条件直接滤空，看起来就像"推送到了但没显示"
                event_data.setdefault("timestamp", now)
                # 确保 ai_reason 字段传到前端（防御性编程）
                event_data["ai_reason"] = event_data.get("ai_reason", "")
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

        def wind_control_callback(message: str, wtype: str,
                                  account_index: Optional[int] = None,
                                  account_name: str = ""):
            """风控事件回调 — 推送风控警告到前端，并持久化通知。

            两个号各自跑各自的循环，风控必须标上账号，否则"账号被封了"
            这种事件在界面上根本分不清是谁的事。
            """
            try:
                payload = {
                    "time": datetime.now().strftime("%H:%M:%S"),
                    "message": message,
                    "type": wtype,
                }
                if account_index is not None:
                    payload["account_index"] = account_index
                if account_name:
                    payload["account_name"] = account_name
                    payload["message"] = f"[{account_name}] {message}"
                socketio.emit("wind_control", payload)
            except Exception:
                pass
            # 持久化通知到列表和文件
            try:
                _add_notification(wtype, message, account_index, account_name)
            except Exception:
                pass

        def log_callback(msg: str):
            """日志回调 — 推送 SocketIO 到前端。

            关键修复：不再重复写入 log_buffer，因为 root logger 的 WebLogHandler
            已经将日志写入 log_buffer。此处只负责推送 SocketIO 实时事件到前端。
            DEBUG 级别日志不推送前端。

            日志流向：
              main_loop._log(msg)
                → log_cb(msg) → log_callback → 推 socket（实时）
                → logger.info(msg) → root → web_handler 写 log_buffer（HTTP 拉取）
                                       → file_handler 写文件（持久化）
            这样 log_buffer 只被 web_handler 写一次，避免重复。
            """
            # 解析日志级别
            level = msg.split("]")[0].strip("[") if "]" in msg else "INFO"
            clean_msg = msg.split("]", 1)[1].strip() if "]" in msg else msg

            # DEBUG 级别不推送前端，只通过 logging 写入文件
            if level.upper() == "DEBUG":
                return

            # 只推送 SocketIO 实时事件，不写 log_buffer（由 WebLogHandler 统一维护）
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
                status = _enrich_status(manager.get_status())
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
    """获取机器人运行状态（多账号汇总，含各账号 Cookie 状态）。"""
    manager = _multi_manager
    if manager is None:
        return jsonify({
            "running": False,
            "accounts": [],
            "stats": {},
        })
    return jsonify(_enrich_status(manager.get_status()))


def _enrich_status(data: dict) -> dict:
    """给每个账号补 cookie 字段，前端状态点因此能随轮询自己更新。

    browser 那一栏补的是**此刻真跑着的是什么**（在不在、是不是无头、哪个端口）：
    "无头"开关说的只是配置，浏览器是启动那一刻定型的，两者可以不一致
    （改完没重启、或者那个端口上根本是别人的浏览器）。
    """
    try:
        accounts = _ensure_config().greet.accounts
        for acc in data.get("accounts", []):
            idx = acc.get("index", 0)
            if 0 <= idx < len(accounts):
                acc["cookie"] = _account_cookie_state(idx, account=accounts[idx])
            try:
                acc["browser"] = browser_mode(9222 + int(idx))
            except Exception as e:
                logger.debug(f"读账号{idx}浏览器形态失败: {e}")
    except Exception:
        pass
    return data


# ===================== 启动/停止 API =====================

def _control_trace():
    """是谁按的启动/停止：来源 IP、UA、数据范围。

    2026-10-04 20:44 两个号的投递轮被一次停止打断，事后查不到是谁发的
    （werkzeug 的访问日志不进 boss_bot.log），只能靠这条留痕。
    """
    ua = (request.headers.get("User-Agent") or "")[:90]
    return (f"来源={request.remote_addr} scope={request.args.get('scope')}"
            f" referer={(request.headers.get('Referer') or '')[-40:]}"
            f" ua={ua}")


def _ensure_status_pusher():
    """状态推送线程只由 /api/start 拉起过；单独启动某个账号时也要有，
    否则界面只能靠自己轮询，账号点的状态半天不动。"""
    global _status_thread
    if _status_thread is not None and _status_thread.is_alive():
        return
    _status_stop.clear()
    _status_thread = threading.Thread(target=_status_pusher, daemon=True)
    _status_thread.start()


def _control_trace():
    """启停这种"会把投递打断"的动作，必须能从日志里回溯到是谁发的。

    2026-10-04 20:44 两个号的轮次被一次停止请求打断，事后查不到来源，
    只能靠 werkzeug 的访问日志（默认没进 boss_bot.log）。这里补一条。
    """
    return "%s ua=%s referer=%s" % (
        request.remote_addr,
        (request.headers.get("User-Agent") or "-")[:60],
        (request.headers.get("Referer") or "-")[:60])


@app.route("/api/start", methods=["POST"])
def api_start():
    """启动所有启用的账号。"""
    logger.info("[/api/start] 来源 %s", _control_trace())
    manager = _ensure_manager()
    status = manager.get_status()
    if status.get("running"):
        return jsonify({"status": "ok", "message": "机器人已在运行中"})
    manager.start()

    # 启动状态推送线程
    _ensure_status_pusher()

    return jsonify({"status": "ok", "message": "机器人已启动"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """停止所有账号。

    为避免前端 fetch 请求超时（"Failed to fetch"）：
    1. 先设置运行停止标志（非阻塞）
    2. 线程 join 使用较短的超时，避免长时间等待
    3. 任何异常都吞掉并记录日志，始终返回 200 响应
    """
    global _status_thread
    logger.info("[/api/stop] 来源 %s", _control_trace())
    _status_stop.set()
    stop_errors = []
    try:
        manager = _multi_manager
        if manager is not None:
            try:
                manager.stop()
            except Exception as e:
                logger.warning("manager.stop() 异常: %s", e)
                stop_errors.append(str(e))
    except Exception as e:
        logger.warning("停止流程异常: %s", e)
        stop_errors.append(str(e))
    # 等待状态线程结束（短超时，避免阻塞请求）
    try:
        if _status_thread is not None and _status_thread.is_alive():
            _status_thread.join(timeout=2)
    except Exception as e:
        logger.warning("状态线程 join 异常: %s", e)
        stop_errors.append(str(e))
    # 即使停止过程出现异常，也返回 200，避免前端报 "Failed to fetch"
    if stop_errors:
        return jsonify({"status": "ok", "message": "机器人已停止（部分异常已忽略）", "errors": stop_errors})
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
    socketio.emit("status_update", _enrich_status(manager.get_status()))
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
    _ensure_status_pusher()
    return jsonify({"status": "ok", "message": f"账号 {idx} 已启动"})


@app.route("/api/accounts/<int:idx>/stop", methods=["POST"])
def api_account_stop(idx: int):
    """停止指定账号。

    增加异常容错：即使停止失败也返回 200，避免前端 "Failed to fetch"。
    """
    manager, error = _validate_account_index(idx)
    if error:
        return error
    stop_error = None
    try:
        manager.stop_account(idx)
    except Exception as e:
        logger.warning("账号 %s 停止异常: %s", idx, e)
        stop_error = str(e)
    if stop_error:
        return jsonify({"status": "ok", "message": f"账号 {idx} 已停止（异常已忽略）", "error": stop_error})
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
    socketio.emit("status_update", _enrich_status(manager.get_status()))
    return jsonify({"status": "ok", "message": f"账号 {idx} 登录已确认"})


@app.route("/api/accounts/<int:idx>/login", methods=["POST"])
def api_account_login(idx: int):
    """只把该账号的浏览器停在 BOSS 登录页等人工登录，不启动投递/回复。

    左侧账号列表的"登录"按钮走这里。以前想登录只能点启动，一下去整条
    流水线就跑起来了，新增的账号没法先登录再投。
    """
    manager, error = _validate_account_index(idx)
    if error:
        return error
    result = manager._loops[idx].open_login_page()
    socketio.emit("status_update", _enrich_status(manager.get_status()))
    return jsonify(result)


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

        # 启动浏览器做完整检测。
        # 端口和 profile 都必须另开一份：留空会让 DrissionPage 退回默认的 9222，
        # 于是"点一下检测账号2的Cookie"其实是连进主账号正在投递的浏览器里翻页面，
        # 顺手把 Cookie 文件里的旧会话注进去。检测环境还要跟生产一致（headless
        # 照抄配置），否则无头被 BOSS 拦出来的假"失效"会把好 Cookie 判死。
        browser_cfg = cfg.browser
        probe_profile = tempfile.mkdtemp(prefix="boss_cookie_check_")
        try:
            result = check_cookie_valid(
                cookie_file=cookie_file_path,
                headless=browser_cfg.headless,
                chrome_path=browser_cfg.chrome_path,
                browser_type=browser_cfg.browser_type,
                port=9500 + idx,
                user_data_dir=probe_profile,
                background=browser_cfg.background,
            )
        finally:
            shutil.rmtree(probe_profile, ignore_errors=True)

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

_cookie_state_cache: dict = {}   # account_index -> (时间戳, payload)
_COOKIE_STATE_TTL = 5            # 秒


def _live_login_state(idx: int):
    """这个账号的浏览器正在跑时，从它身上实测登录态；没在跑返回 None。

    只问已经在跑的循环，绝不另起浏览器：老那条 check_cookie 会起一个不带端口与
    profile 的实例，跟正在跑的会话抢同一个用户目录还 quit() 它 —— 检测一次等于
    把登录态踢一次，这是本项目不能碰的红线。
    """
    if _multi_manager is None:
        return None
    for lp in getattr(_multi_manager, "_loops", []) or []:
        if getattr(lp, "account_index", None) == idx:
            try:
                return lp.live_login_state()
            except Exception as e:
                logger.debug(f"实测登录态失败(账号{idx}): {e}")
                return None
    return None


def _account_cookie_state(idx: int, account=None, force: bool = False) -> dict:
    """按账号读登录态：优先用正在跑的那个浏览器实测，没在跑才读 Cookie 文件。

    带 5 秒缓存，所以可以跟着状态轮询跑：以前红点只在手动点一下后更新，
    账号2 登录成功之后点还是红的，看起来像"多账号没跑起来"。
    """
    now = time.time()
    hit = _cookie_state_cache.get(idx)
    if not force and hit and (now - hit[0]) < _COOKIE_STATE_TTL:
        return hit[1]
    try:
        if account is None:
            acc = _ensure_config().greet.accounts[idx]
        else:
            acc = account
        cookie_file_name = acc.cookie_file or "zhipin_cookies.json"
        from boss_bot.browser_launcher import check_cookie_valid_simple
        result = check_cookie_valid_simple(str(PROJECT_ROOT / cookie_file_name))
        payload = {
            "valid": bool(result.get("valid")),
            "logged_in": bool(result.get("logged_in")),
            "reason": result.get("reason", ""),
            "cookie_file": cookie_file_name,
            "account_name": getattr(acc, "name", f"账号{idx}"),
            "checked_at": int(now),
        }
    except Exception as e:
        payload = {"valid": False, "logged_in": False, "reason": f"检测异常: {e}",
                   "cookie_file": "", "account_name": f"账号{idx}",
                   "checked_at": int(now)}

    # 文件结论只说明"那几条登录项没过期"，界面不能把它说成"登录着"
    payload["source"] = "file"
    payload["reason"] = f"本地 Cookie 文件（未联网核对）：{payload.get('reason') or ''}".strip()

    live = _live_login_state(idx)
    if live == "logged_in":
        payload.update(valid=True, logged_in=True, source="live",
                       reason="浏览器实测：页面在 BOSS 内页且登录项未过期")
    elif live == "need_login":
        payload.update(valid=False, logged_in=False, source="live",
                       reason="浏览器实测：这个浏览器现在停在登录页，登录态已失效")
    elif live == "uncertain":
        # 两路证据对不上时给灰点（valid=None）：蒙红或蒙绿都会把人带偏
        payload.update(valid=None, source="uncertain",
                       reason="浏览器实测：地址与登录项两路证据对不上，看不准，"
                              "请在浏览器窗口里看一眼")
    _cookie_state_cache[idx] = (now, payload)
    return payload


@app.route("/api/accounts/cookies")
def api_accounts_cookies():
    """一次拿全部账号的 Cookie 状态（前端账号点的自动刷新走这里）。"""
    try:
        cfg = _ensure_config()
        out = []
        force = request.args.get("force") == "1"
        only = request.args.get("index", type=int)
        for i, acc in enumerate(cfg.greet.accounts):
            if only is not None and i != only:
                continue
            out.append(dict(_account_cookie_state(i, account=acc, force=force), index=i))
        return jsonify({"status": "ok", "accounts": out})
    except Exception as e:
        logger.exception("批量检测账号 Cookie 失败")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/config", methods=["GET"])
def api_get_config():
    """获取当前配置。

    ?account=N 返回该账号的生效值（全局基准 + accounts[N].settings 覆盖）。
    界面选中某个账号时展示/编辑的就是这份，否则"给账号2 改了阈值"要用户自己
    心算哪个值最终生效。不带 account 就是全局基准。
    """
    cfg = _ensure_config()
    account = request.args.get("account", type=int)
    if account is not None:
        cfg = cfg.apply_account(account)
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


def _fold_into_account_overlay(new_cfg: dict, idx: int) -> None:
    """把提交里的账号差异折进 accounts[idx].settings，全局段退回基准值。

    界面选中某个账号时读写的是"基准 + 覆盖"的生效值，整份回写会把生效值写成
    全局基准（改一个号 = 改所有号）。基准取 to_dict() 而不是 dataclass 直接
    序列化：那正是 GET 发给前端的那份形状，两边字段对得上才比得出差异。
    改回和基准一样就得到空覆盖，等于取消这个账号的独立设置。
    """
    base_cfg = _ensure_config().to_dict()
    overlay = {}
    for section in ACCOUNT_OVERLAY_SECTIONS:
        submitted = new_cfg.get(section)
        base = base_cfg.get(section)
        if isinstance(submitted, dict) and isinstance(base, dict):
            diff = diff_against(submitted, base)
            # 锁住的键不写进覆盖：浏览器端口与用户目录是账号索引算出来的
            # （9222+idx、account_{idx}），界面改了它等于让两个号抢同一个浏览器
            locked = ACCOUNT_OVERLAY_LOCKED.get(section, set())
            diff = {k: v for k, v in diff.items() if k not in locked}
            if diff:
                overlay[section] = diff
        if isinstance(base, dict):
            new_cfg[section] = base
    new_cfg["accounts"][idx]["settings"] = overlay


@app.route("/api/config", methods=["POST", "PUT"])
def api_save_config():
    """保存配置。

    保存策略：
      - reply_rules、templates、importance_keywords、user_profile 同时写入
        bot_config.json 和 config_overrides.json，确保两个文件一致。
      - AI 配置等其他字段只写入 bot_config.json。
      - 请求带 account 时，AI/回复段按账号写进 accounts[N].settings 覆盖，
        全局基准保持磁盘上的原值。
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

        # 界面选中某个账号时，GET 给的是"基准+覆盖"的生效值，整份回传就会把
        # 生效值当成全局基准写进磁盘——改账号2 的阈值顺手改了所有号。
        # 带 account 时先把它折回 accounts[N].settings，全局段再用基准原样补回。
        account = data.get("account") if isinstance(data.get("config"), dict) else None
        if isinstance(account, int) and 0 <= account < len(new_cfg.get("accounts") or []):
            _fold_into_account_overlay(new_cfg, account)

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
            # 界面点"删除"也不许真删：Cookie 是用户唯一的登录会话凭据，
            # 删错了连"当时里面是什么"都查不回来（2026-09-29 就是这么丢的）
            from boss_bot.main_loop import archive_cookie_file
            dest = archive_cookie_file(str(cookie_path),
                                       str(resolve_path(Path("data") / "stale_cookies")),
                                       "界面删除_" + Path(safe_name).stem)
            return jsonify({"status": "ok",
                            "message": f"{safe_name} 已归档到 "
                                       f"{Path(dest).name}（没有真删，需要时可拿回来）"})
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


# ===================== 漏斗指标 =====================

@app.route("/api/metrics")
def api_metrics():
    """累计投递 / 今日已投递 / 接收简历 / 面试数（按会话去重）。

    Query: account=全部时省略，或 account=0 指定单个账号。
    """
    try:
        from boss_bot.metrics import MetricsStore
        cfg = _ensure_config()
        acc = request.args.get("account")
        scope = None
        if acc is not None and acc not in ("", "all"):
            try:
                scope = [int(acc)]
            except ValueError:
                return jsonify({"status": "error", "message": "account 参数非法"}), 400
        # 每次重新读盘：写的人在机器人线程里，单例不会重读文件。
        # backfill 只在第一次执行，把历史记录换算成累计值
        snap = MetricsStore(backfill=True).snapshot(scope)
        rl = cfg.greet.rate_limit
        snap["daily_limit"] = rl.max_per_day if (rl and rl.enabled) else None
        snap["scope"] = "all" if scope is None else scope[0]
        snap["accounts"] = [
            {"index": i, "name": a.name, "enabled": a.enabled,
             "greeting_ready": account_greeting_ready(a, cfg.resume, cfg.user_profile),
             # 这句话是谁的话：自己写的，还是系统按本账号信息生成的默认
             "greeting_mode": account_greeting_mode(a, cfg.resume, cfg.user_profile)}
            for i, a in enumerate(cfg.greet.accounts)
        ]
        return jsonify({"status": "ok", **snap})
    except Exception as e:
        logger.exception("读取指标失败")
        return jsonify({"status": "error", "message": str(e)}), 500


# ===================== AI 接口体检 =====================

_ai_health_lock = threading.Lock()
_ai_health_running = False


def _provider_dicts(cfg):
    return [{"name": p.name, "api_key": p.api_key, "api_base": p.api_base,
             "model": p.model, "timeout": getattr(p, "timeout", 30)}
            for p in (cfg.ai.providers or [])]


def _ai_health_payload(cfg):
    """把体检结果和当前配置对齐，未测过的标 untested。"""
    from boss_bot.ai_health import STATUS_UNTESTED, provider_key, load_health, summarize
    providers = _provider_dicts(cfg)
    health = load_health()
    stored = health.get("results") or {}
    rows = []
    for i, p in enumerate(providers):
        hit = stored.get(provider_key(p)) or {}
        key = p.get("api_key") or ""
        row = {
            "index": i, "name": p["name"], "model": p["model"],
            "api_base": p["api_base"], "api_key_masked": key[:6] + "..." if key else "",
            "configured": bool(key),
            "status": hit.get("status", STATUS_UNTESTED),
            "latency_ms": hit.get("latency_ms"),
            "reply": hit.get("reply", ""),
            "reason": hit.get("reason", ""),
            "checked_at": hit.get("checked_at", ""),
        }
        rows.append(row)
    configured_keys = {provider_key(p) for p in providers}
    stale = [r for k, r in stored.items()
             if k not in configured_keys and r.get("status") == "available"]
    return {
        "status": "ok",
        "running": _ai_health_running,
        "updated_at": health.get("updated_at", ""),
        "providers": rows,
        # 按下标算，重复配置的两条都要计入，否则 22 个接口只显示 20 个
        "summary": summarize({str(r["index"]): r for r in rows}),
        "incomplete": sum(1 for r in rows if not r["configured"]),
        "duplicated": len(providers) - len({
            provider_key(p) for p in providers}),
        "stale_available": len(stale),
    }


@app.route("/api/ai/health")
def api_ai_health():
    """读取 22 个接口的体检结果（不发起请求）。"""
    try:
        return jsonify(_ai_health_payload(_ensure_config()))
    except Exception as e:
        logger.exception("读取 AI 体检结果失败")
        return jsonify({"status": "error", "message": str(e)}), 500


def _start_ai_health(indexes=None):
    """后台逐个真实发一条测试消息探测接口。返回 (是否启动, 说明)。"""
    global _ai_health_running
    from boss_bot.ai_health import (load_health, merge_results, probe_one,
                                    save_health, summarize)
    cfg = _ensure_config()
    providers = _provider_dicts(cfg)
    if not providers:
        return False, "未配置任何 AI 接口"
    idx = [i for i in (list(indexes) if indexes else range(len(providers)))
           if 0 <= i < len(providers)]
    with _ai_health_lock:
        if _ai_health_running:
            return False, "体检正在进行中，请等待本轮完成"
        _ai_health_running = True

    def _worker():
        global _ai_health_running
        results = []
        try:
            for pos, i in enumerate(idx):
                res = probe_one(providers[i],
                                timeout=providers[i].get("timeout") or 45)
                res["index"] = i
                results.append(res)
                socketio.emit("ai_health_progress", {
                    "index": i, "name": providers[i]["name"],
                    "status": res["status"], "latency_ms": res["latency_ms"],
                    "reason": res["reason"], "reply": res["reply"],
                    "done": pos + 1, "total": len(idx),
                })
            merged = merge_results(load_health(), results, providers)
            save_health(merged)
            socketio.emit("ai_health_done", {
                "summary": summarize(merged["results"]),
                "updated_at": merged["updated_at"],
                "available": sum(1 for r in results if r["status"] == "available"),
                "checked": len(results),
            })
            logger.info(f"AI 体检完成：{summarize(merged['results'])}")
        except Exception as e:
            logger.exception("AI 体检异常")
            socketio.emit("ai_health_done", {"error": str(e)})
        finally:
            with _ai_health_lock:
                _ai_health_running = False

    threading.Thread(target=_worker, daemon=True).start()
    return True, f"开始检测 {len(idx)} 个接口"


@app.route("/api/ai/health", methods=["POST"])
def api_ai_health_run():
    """Body: {"indexes":[0,3]} 只测指定接口；省略则全部。

    探测在后台线程跑，每个接口测完通过 socketio 推 ai_health_progress，
    全部结束推 ai_health_done —— 22 个接口最坏要几分钟，不能让请求挂着。
    """
    try:
        body = request.get_json(silent=True) or {}
        indexes = body.get("indexes")
        indexes = [int(i) for i in indexes] if indexes else None
        started, message = _start_ai_health(indexes)
        code = 200 if started else 409
        return jsonify({"status": "ok" if started else "error",
                        "message": message}), code
    except Exception as e:
        logger.exception("启动 AI 体检失败")
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


# AI 提示词默认值直接取引擎真正在用的那份（boss_bot/prompts.py）。
# 以前这里抄了一份只有 10 条的副本，而 prompts.py 的默认值有 20 条 ——
# 界面点一次「恢复默认」就把削弱版写进 config_overrides.json，
# 之后每条 AI 回复都丢了"先读完整上下文""被拒绝后别再推销"这些约束。
from boss_bot.prompts import (_SYSTEM_RULES_DEFAULT as _DEFAULT_SYSTEM_RULES,
                              _USER_PROMPT_DEFAULT as _DEFAULT_USER_PROMPT_TEMPLATE)


@app.route("/api/ai/prompts", methods=["GET"])
def api_get_ai_prompts():
    """获取当前 AI 提示词配置（system_rules, user_prompt_template）。

    未配置时返回 prompts.py 的默认值；defaults 一并返回，
    「恢复默认」按钮用它，前端不必再抄一份可能过期的副本。
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
        return jsonify({"status": "ok", "prompts": prompts,
                        "defaults": {"system_rules": _DEFAULT_SYSTEM_RULES,
                                     "user_prompt_template": _DEFAULT_USER_PROMPT_TEMPLATE}})
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
        per_account = []
        if manager is not None:
            status = manager.get_status()
            stats = status.get("stats", {})
            per_account = status.get("accounts", [])

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

        # 分账号明细：只有汇总的话，两个号看不出谁在干活
        if len(per_account) > 1:
            ws.append([])
            ws.append(["账号", "阶段", "已投递", "已跳过", "回复发送", "回复跳过", "简历"])
            for acc in per_account:
                s = acc.get("stats", {})
                ws.append([
                    acc.get("name") or f"账号{acc.get('index', 0)}",
                    acc.get("phase", ""),
                    s.get("greet_applied", 0),
                    s.get("greet_skipped", 0),
                    s.get("reply_sent", 0),
                    s.get("reply_skipped", 0),
                    s.get("resume_sent", 0),
                ])

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
    """确保回复记录存储已初始化（使用全局单例，与引擎层共享同一实例）。"""
    return _get_reply_store()


def _ensure_greet_store() -> GreetRecordStore:
    """确保打招呼记录存储已初始化（使用全局单例，与引擎层共享同一实例）。"""
    return _get_greet_store()


def _account_arg() -> Optional[int]:
    """记录/聊天类接口统一的账号过滤参数：?account=1。

    缺省或 account=all 返回 None（不过滤，看全部）；前端切到某个账号时带上索引，
    记录、聊天、导出、清空就都只作用于该账号。
    """
    raw = request.args.get("account")
    if raw is None or raw == "" or str(raw).lower() == "all":
        return None
    try:
        idx = int(raw)
    except (TypeError, ValueError):
        return None
    return idx if idx >= 0 else None


def _msg_store(account_index: int = 0) -> MessageStore:
    """按账号取消息存储：记录里的会话可能属于账号2，读详情必须用对应前缀。"""
    return MessageStore(account_index=account_index if account_index and account_index > 0 else 0)


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
    account = _account_arg()

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
            account_index=account,
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
                download_name=(f"reply_records_a{account}_export.json"
                               if account is not None else "reply_records_export.json"),
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
    account = _account_arg()

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
            account_index=account,
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
                download_name=(f"greet_records_a{account}_export.json"
                               if account is not None else "greet_records_export.json"),
                mimetype="application/json",
            )
    except Exception as e:
        logger.exception("导出打招呼记录失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/reply_records")
def api_reply_records():
    """获取回复记录列表（前端展示用，不分页）。

    返回全部记录（按 timestamp 降序），确保前端能看到完整数据。
    之前限制 200 条会导致文件有 358 条但前端只显示 200 条的不一致问题。
    ?account=N 只看某个账号的记录，不带则全部账号。
    """
    try:
        store = _ensure_reply_store()
        account = _account_arg()
        records = store.filter(account_index=account) if account is not None else store.get_all()
        # 按 timestamp 降序排列（最新的在前）
        result = sorted(
            (r.to_dict() for r in records),
            key=lambda x: x.get("timestamp", "") or "",
            reverse=True,
        )
        return jsonify({
            "status": "ok",
            "total": len(records),
            "account": account,
            "records": result,
        })
    except Exception as e:
        logger.exception("获取回复记录列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


_ARCHIVE_GREETS_CACHE = {"key": None, "rows": []}


def _greet_rows_with_archive() -> list:
    """当前打招呼记录 + 已归档的历史记录，给台账补岗位链接用。

    岗位链接只存在打招呼记录里，而它按天归档：只看当前文件的话，
    134 行台账里只有 4 行能点开岗位，翻几天前的线索就断了。
    归档文件写完不再变，所以按 (日期, mtime, 大小) 缓存，不每次重解析 27MB；
    当天的那份从内存里的 store 取，投递写一条台账就能看到。
    """
    archive_dir = _get_archive_dir()
    paths = sorted(archive_dir.glob("*/greet_records.json"))
    key = tuple((p.parent.name, p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
    if _ARCHIVE_GREETS_CACHE["key"] != key:
        rows = []
        for p in paths:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            rows.extend(data.get("records") or [])
        _ARCHIVE_GREETS_CACHE.update({"key": key, "rows": rows})
    live = [r.to_dict() for r in _get_greet_store().get_all()]
    return live + _ARCHIVE_GREETS_CACHE["rows"]


@app.route("/api/contact_ledger")
def api_contact_ledger():
    """「联系与简历」台账：HR 给了电话/微信、简历已发或对方要了没发的会话。

    每次现算，不再另存一份：会话存档是唯一真源，另存就会和 BOSS 上对不齐
    （回复记录已经为此返过一次工）。?account=N 只看某个号。
    """
    try:
        account = _account_arg()
        chats = MessageStore().get_all_chats_detail()
        if account is not None:
            chats = [c for c in chats if c.get("account_index") == account]
        rows = contact_rows(chats, _greet_rows_with_archive())
        return jsonify({"status": "ok", "total": len(rows),
                        "account": account, "rows": rows})
    except Exception as e:
        logger.exception("获取联系与简历台账失败")
        return jsonify({"status": "error", "message": str(e)}), 500


def _normalize_ts(ts: str) -> str:
    """将各种时间戳格式统一为 YYYY-MM-DD HH:MM:SS 格式，用于排序比较。

    支持的输入格式：
    - "2026-09-23 14:03:17"（标准格式，直接返回）
    - "2026-09-23T14:03:17.189728"（ISO格式，转换为标准格式）
    - "2026-09-23T14:03:17"（ISO格式无毫秒）
    - "14:03"（仅时间，返回空串无法排序）
    - "昨天 22:50"（人类可读，返回空串无法排序）
    """
    if not ts:
        return ""
    s = ts.strip()
    # ISO格式: 2026-09-23T14:03:17.189728 → 2026-09-23 14:03:17
    if "T" in s and len(s) >= 19:
        try:
            return s[:10] + " " + s[11:19]
        except Exception:
            return ""
    # 标准格式: 2026-09-23 14:03:17
    if len(s) >= 19 and s[4] == "-" and s[7] == "-":
        return s[:19]
    # 无法解析的格式（如"14:03"、"昨天 22:50"），返回空串
    return ""


def _chat_id_for_record(chat_index, msg_store, record) -> str:
    """把一条回复记录归到它真正所属的那一路会话。

    记录本身只带 姓名+岗位，而会话身份是 姓名+公司，所以要拿记录的岗位去同名的
    几路会话里对：实测两个"陈女士"的岗位完全不同（数据分析师 / 运营实习生），
    岗位能唯一对上；对不上时（同名多路且岗位都不匹配）只能退回按岗位自己拼一个
    身份，绝不能塞进同名会话里 —— 那正是界面顺序和内容对不上 BOSS 的来历。

    chat_index 由调用方一次建好（(姓名, 账号) -> 会话列表），逐条记录查表，
    免得每条记录都重扫一遍 messages 目录。
    """
    name = record.chat_name or "(未知)"
    job = (record.job_name or "").strip()
    cands = chat_index.get((name, int(record.account_index or 0)), [])
    if len(cands) == 1:
        return cands[0]["chat_id"]
    if job:
        hits = [c for c in cands
                if job in (c.get("job_name") or "") or (c.get("job_name") or "") in job]
        if len(hits) == 1:
            return hits[0]["chat_id"]
    return msg_store.chat_id(name, "", job)


@app.route("/api/reply_records", methods=["POST"])
def api_append_reply_record():
    """让在线面板自己追加一条回复记录（工具/脚本用）。

    为什么非要走接口：面板进程把 reply_records 整份写回磁盘。工具在另一个进程里
    append 再 save，面板下一次落盘就把它覆盖掉了——2026-10-07 清剿 68 单拒绝，
    会话气泡（分文件合并写）活下来了，回复记录全没了，前端筛不到。
    单写者只能是进程内那个 store，所以外部要记账就得走这里。
    """
    允许的来源 = {"family_filter", "reject_contact", "policy", "manual", "backfill"}
    try:
        data = request.get_json(force=True, silent=True) or {}
        src = str(data.get("reply_source") or "")
        if src not in 允许的来源:
            return jsonify({"status": "error",
                            "message": f"reply_source 只认 {sorted(允许的来源)}，收到 {src!r}"}), 400
        if not (data.get("chat_name") or "").strip():
            return jsonify({"status": "error", "message": "chat_name 不能空"}), 400
        store = _ensure_reply_store()
        record = ReplyRecord(
            timestamp=str(data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            chat_name=str(data.get("chat_name")),
            job_name=str(data.get("job_name") or ""),
            received_message=str(data.get("received_message") or ""),
            reply_content=str(data.get("reply_content") or ""),
            reply_source=src,
            reply_intent=str(data.get("reply_intent") or "other"),
            reply_reason=str(data.get("reply_reason") or ""),
            account_index=int(data.get("account_index") or 0),
        )
        store.add(record)
        socketio.emit("reply_record", record.to_dict())
        return jsonify({"status": "ok", "chat_name": record.chat_name,
                        "reply_source": src})
    except Exception as e:
        logger.exception("追加回复记录失败")
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
    ?account=N 只看某个账号的会话（记录 + message_store 里的完整对话）。
    """
    try:
        store = _ensure_reply_store()
        account = _account_arg()
        records = store.filter(account_index=account) if account is not None else store.get_all()

        # 加载完整对话消息（来自 message_store，全目录扫描，含各账号）
        msg_store = MessageStore()
        all_chats = [c for c in msg_store.get_all_chats_detail()
                     if account is None or int(c.get("account_index") or 0) == account]
        # 分组键必须是会话身份而不是昵称：实测侧栏 34 行里 4 组重名昵称
        # （两个陈女士分属小智时代科技/艾秒广告），按昵称分组会把两段对话并成一组。
        # 身份还要再带账号：两个号聊到同一家公司的同一个人时 chat_id 完全相同
        # （实测 李女士 @ 深圳市极客星球电…，账号1 存 9 条、账号2 存 7 条），
        # 只按 chat_id 建字典会让后读的盖掉先读的——界面上少一整段对话
        full_chats = {(c["chat_id"], int(c.get("account_index") or 0)): c for c in all_chats}
        chat_index = {}
        for c in all_chats:
            chat_index.setdefault(
                (c.get("chat_name", ""), int(c.get("account_index") or 0)), []).append(c)

        # 按会话身份分组
        groups = {}
        for r in records:
            cid = _chat_id_for_record(chat_index, msg_store, r)
            gkey = (cid, int(r.account_index or 0))
            if gkey not in groups:
                full = full_chats.get(gkey) or {}
                groups[gkey] = {
                    "chat_name": r.chat_name or "(未知)",
                    "chat_id": cid,
                    "company": full.get("company", ""),
                    "account_index": int(r.account_index or 0),
                    "account_name": r.account_name or "",
                    "message_count": 0,
                    "last_time": "",
                    "last_message": "",
                    "last_reply": "",
                    "records": [],
                    "messages": full.get("messages", []) or [],
                    "job_name": full.get("job_name", ""),
                    "full_message_count": full.get("message_count", 0),
                    "unread_count": full.get("unread_count", 0),
                }
            d = r.to_dict()
            groups[gkey]["records"].append(d)
            groups[gkey]["message_count"] += 1
            # 更新最新消息（按 timestamp 字符串比较）
            timestamp = d.get("timestamp", "") or ""
            if timestamp > groups[gkey]["last_time"]:
                groups[gkey]["last_time"] = timestamp
                groups[gkey]["last_message"] = d.get("received_message", "") or ""
                groups[gkey]["last_reply"] = d.get("reply_content", "") or ""

        # 补上只有 HR 消息、还没回复过的会话（reply_records 里没有，message_store 里有）
        for (cid, _acct), full in full_chats.items():
            g = groups.get((cid, _acct))
            if g is None:
                groups[(cid, _acct)] = {
                    "chat_name": full.get("chat_name", ""),
                    "chat_id": cid,
                    "company": full.get("company", ""),
                    "account_index": int(full.get("account_index") or 0),
                    "account_name": full.get("account_name", ""),
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
                continue
            g["messages"] = full.get("messages", []) or g["messages"]
            g["job_name"] = full.get("job_name", "") or g.get("job_name", "")
            g["company"] = full.get("company", "") or g.get("company", "")
            # message_store 的时间更新就用它，消息数以 message_store 为准（更准确）
            full_last_time = full.get("last_time", "") or full.get("updated_at", "")
            if full_last_time and full_last_time > g["last_time"]:
                g["last_time"] = full_last_time
                last_msg = full.get("last_message", "")
                if last_msg:
                    g["last_message"] = last_msg
            g["full_message_count"] = full.get("message_count", 0)
            g["unread_count"] = full.get("unread_count", 0)

        # 转为列表，按最后消息时间倒序排列（统一时间戳格式后排序）
        result = sorted(groups.values(), key=lambda x: _normalize_ts(x.get("last_time", "")), reverse=True)
        # 统一 last_time 格式为 YYYY-MM-DD HH:MM:SS，方便前端排序和显示
        for g in result:
            normalized = _normalize_ts(g.get("last_time", ""))
            if normalized:
                g["last_time"] = normalized
        return jsonify({
            "status": "ok",
            "groups": result,
            "total_groups": len(result),
            "total_records": len(records),
            "account": account,
        })
    except Exception as e:
        logger.exception("获取分组回复记录失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chats")
def api_chats():
    """获取所有聊天会话列表（完整对话消息，用于前端聊天界面）。

    ?account=N 只列该账号的会话；不带则全部账号（每条带 account_index 标注）。

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
        account = _account_arg()
        msg_store = MessageStore()
        chats = [c for c in msg_store.get_all_chats_detail()
                 if account is None or int(c.get("account_index") or 0) == account]
        return jsonify({
            "status": "ok",
            "chats": chats,
            "total": len(chats),
            "account": account,
        })
    except Exception as e:
        logger.exception("获取聊天列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chats/<path:chat_name>")
def api_chat_detail(chat_name: str):
    """获取指定聊天会话的完整消息列表。

    Args:
        chat_name: 聊天对象称呼（URL 路径参数）
    Query:
        account: 账号索引。BOSS 只显示"杨女士"这类称呼，两个账号聊到同名 HR
            时落在不同文件（aN_前缀），不带账号就会读到另一个账号的会话。
        company: 公司名。实测侧栏 34 行只用姓名有 4 组重名，加上公司后全唯一，
            所以定位会话靠 姓名+公司（它就挂在侧栏行上，不依赖"点开的是谁"）。
        job: 岗位名称。公司取不到时的兜底身份（详情头部读得到）。
    """
    try:
        account = _account_arg()
        company = (request.args.get("company") or "").strip()
        job = (request.args.get("job") or "").strip()
        msg_store = _msg_store(0 if account is None else account)
        detail = msg_store.get_chat_detail(chat_name, job, company)
        return jsonify({
            "status": "ok",
            "chat": detail,
        })
    except Exception as e:
        logger.exception("获取聊天详情失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/chats/<path:chat_name>/mark_read", methods=["POST"])
def api_chat_mark_read(chat_name: str):
    """标记指定会话的所有 HR 消息为已读（?account=N&company=公司[&job=岗位]）。"""
    try:
        account = _account_arg()
        company = (request.args.get("company") or "").strip()
        job = (request.args.get("job") or "").strip()
        msg_store = _msg_store(0 if account is None else account)
        msg_store.mark_chat_read(chat_name, job, company)
        return jsonify({"status": "ok", "message": "已标记为已读"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/quality")
def api_ai_quality():
    """AI 判分质量：真判分多少、兜底多少、平均多久。

    「AI 筛岗有没有生效」以前只能翻日志。兜底率（ai_error 占比）才是关键：
    兜底=这个岗位没被 AI 看过，按默认话术直接打招呼了。
    """
    try:
        account = _account_arg()
        stats = _get_greet_store().quality_stats(account_index=account)
        stats["scope"] = account
        return jsonify({"status": "ok", **stats})
    except Exception as e:
        logger.exception("读取 AI 判分质量失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/judgement_review")
def api_judgement_review():
    """判分复盘：AI 判"不符合"后追问到的原因，聚合成排行与建议。

    建议一律 auto_applied:false —— 只有点「采纳」才写配置，见
    docs/superpowers/specs/2026-09-29-reject-reason-followup-design.md。
    """
    try:
        account = _account_arg()
        try:
            days = int(request.args.get("days") or 7)
        except (TypeError, ValueError):
            days = 7
        rows = _get_greet_store().get_all()
        data = build_review(rows, account_index=account, days=max(1, min(90, days)))
        return jsonify({"status": "ok", **data})
    except Exception as e:
        logger.exception("读取判分复盘失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/ai/judgement_review/adopt", methods=["POST"])
def api_judgement_review_adopt():
    """采纳一条复盘建议：只把它写进对应的那一个配置字段。

    带 account 时 AI 段折进该号覆盖，改账号 2 的建议不会顺手改掉所有号的基准。
    简历段（add_resume_evidence）不在账号可覆盖清单里，永远写全局——两个号对同一个
    HR 说出互相矛盾的自我介绍更糟。
    """
    global _config
    try:
        body = request.get_json() or {}
        suggestion = body.get("suggestion") or {}
        account = body.get("account")
        cfg = UnifiedConfig.load()
        # 在这个号"基准 + 已有覆盖"的生效值上追加，再折回覆盖。
        # 直接在全局段上追加的话，下一次采纳读不到上一次写进覆盖的内容，
        # 折出来的差异是空的，settings 被整个替换 —— 连点两条建议只剩最后一条
        if isinstance(account, int) and 0 <= account < len(cfg.greet.accounts):
            cfg = cfg.apply_account(account)
        ok, msg = apply_suggestion(cfg, suggestion)
        if not ok:
            return jsonify({"status": "error", "message": msg}), 400

        new_cfg = cfg.to_dict()
        if isinstance(account, int) and 0 <= account < len(new_cfg.get("accounts") or []):
            _fold_into_account_overlay(new_cfg, account)
        save_config(new_cfg)
        save_overrides(new_cfg)
        _config = UnifiedConfig.load()
        scope = f"账号「{(new_cfg.get('accounts') or [{}])[account].get('name', account)}」" \
            if isinstance(account, int) and 0 <= account < len(new_cfg.get("accounts") or []) \
            else "全局"
        return jsonify({"status": "ok", "message": f"{msg}（已写入 {scope}）",
                        "scope": scope})
    except Exception as e:
        logger.exception("采纳复盘建议失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/greet_records")
def api_greet_records():
    """获取打招呼记录列表（前端展示用，不分页）。

    返回全部记录（按 timestamp 降序），确保前端能看到完整数据。
    之前限制 200 条会导致文件有 225 条但前端只显示 200 条的不一致问题。
    ?account=N 只看某个账号的记录，不带则全部账号。
    """
    try:
        store = _ensure_greet_store()
        account = _account_arg()
        records = store.filter(account_index=account) if account is not None else store.get_all()
        # 按 timestamp 降序排列（最新的在前）
        result = sorted(
            (r.to_dict() for r in records),
            key=lambda x: x.get("timestamp", "") or "",
            reverse=True,
        )
        return jsonify({
            "status": "ok",
            "total": len(records),
            "account": account,
            "records": result,
        })
    except Exception as e:
        logger.exception("获取打招呼记录列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/greet_records/clear", methods=["POST", "DELETE"])
def api_clear_greet_records():
    """清空打招呼记录。

    同时清空内存单例和磁盘文件，确保刷新页面后记录不再出现。
    ?account=N 只清该账号的记录——两个号共用一份记录文件，不带账号
    在看账号2 时点"清空"会把主账号的历史一起删掉。
    """
    try:
        store = _ensure_greet_store()
        account = _account_arg()
        if account is None:
            store.clear_all()
            deleted = None
        else:
            deleted = store.delete_account(account)
        return jsonify({"status": "ok", "deleted": deleted, "account": account,
                        "message": f"账号{account} 的打招呼记录已清空" if account is not None
                                   else "打招呼记录已清空"})
    except Exception as e:
        logger.exception("清空打招呼记录失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/reply_records/clear", methods=["POST", "DELETE"])
def api_clear_reply_records():
    """清空回复记录。

    同时清空内存单例和磁盘文件，确保刷新页面后记录不再出现。
    ?account=N 只清该账号的记录，理由同打招呼记录。
    """
    try:
        store = _ensure_reply_store()
        account = _account_arg()
        if account is None:
            store.clear_all()
            deleted = None
        else:
            deleted = store.delete_account(account)
        return jsonify({"status": "ok", "deleted": deleted, "account": account,
                        "message": f"账号{account} 的回复记录已清空" if account is not None
                                   else "回复记录已清空"})
    except Exception as e:
        logger.exception("清空回复记录失败")
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


@app.route("/api/accounts/greeting_suggest", methods=["POST"])
def api_greeting_suggest():
    """按这个账号自己的信息重生成默认招呼语，写回这个账号的配置。

    已经自己写过的号必须 force=1 才覆盖：用户写的话不能被一次"重新生成"顺手抹掉
    （改了必须生效，反过来也一样——他没说要改就不能改）。
    """
    try:
        data = request.get_json() or {}
        idx = int(data.get("index", 0))
        cfg = _ensure_config()
        if not 0 <= idx < len(cfg.greet.accounts):
            return jsonify({"status": "error", "message": f"账号 {idx} 不存在"}), 400
        acc = cfg.greet.accounts[idx]
        generated = compose_account_default(acc, cfg.resume, cfg.user_profile)
        if not generated.strip():
            return jsonify({"status": "error",
                            "message": "这个账号的城市/方向/简历画像都是空的，拼不出默认招呼语"}), 400
        if account_greeting_mode(acc, cfg.resume, cfg.user_profile) == "账号自写" \
                and not data.get("force"):
            return jsonify({"status": "error", "need_confirm": True,
                            "message": "这个账号已经写过招呼语，要用生成的默认覆盖吗？"}), 409
        acc.greeting_message = generated
        cfg.save()
        logger.info(f"账号「{acc.name}」的默认招呼语已按本账号信息重新生成")
        return jsonify({"status": "ok", "account_index": idx,
                        "greeting_message": generated, "mode": "自动生成的默认"})
    except Exception as e:
        logger.exception("生成默认招呼语失败")
        return jsonify({"status": "error", "message": str(e)}), 500


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
        # 新账号一进来就该带一条按它自己信息生成的默认招呼语，
        # 不能等到下一次请求才补——否则用户刚加完号点"启动"就是一轮空跳
        _ensure_greeting_defaults(cfg)
        cfg.save()

        # 循环是按账号建的：不重建管理器，新账号就没有 loop，
        # 于是"登录/启动"都会报"账号未启用或不存在"
        note = ""
        global _multi_manager
        if _multi_manager is not None:
            if _multi_manager.get_status().get("running"):
                note = "（有账号正在运行，新账号要等下次全部启动后才有独立循环）"
            else:
                _multi_manager = None

        return jsonify({"status": "ok",
                        "message": f"账号「{name}」已添加，Cookie文件: {cookie_file}" + note})
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
                "job_type": job.job_type,
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
            job_type=str(data.get("job_type", "") or ""),
            scroll_pages=data.get("scroll_pages", 5),
            greeting_message=data.get("greeting_message", ""),
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
        if "job_type" in data:
            job.job_type = str(data["job_type"] or "").strip()
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

def _resolve_evolve_account(account=_UNSET) -> int:
    if account is _UNSET:
        account = _account_arg()
    return 0 if account is None else int(account)


def _ensure_self_evolve(account=_UNSET) -> SelfEvolveEngine:
    """拿到「当前数据范围那个账号」的自进化引擎。

    自进化数据（回复效果、经验、快照）本来就按号分文件写：运行中的账号用自己
    那份，面板必须读同一份。以前这里无条件 new 一个挂在旧全局文件上的引擎，
    弹窗看到的统计跟两个号实际记录的是两套数，而且和右侧数据范围完全无关。

    account 不传时按请求里的 ?account= 解析；「全部账号」看账号1（主号）——
    经验与快照是按号存的，没有可合并的单一口径，所以响应里会带 account_name，
    弹窗顶部标明现在看的是谁。
    """
    global _self_evolve
    idx = _resolve_evolve_account(account)

    manager = globals().get("_multi_manager")
    loop = None
    if manager is not None:
        loop = (getattr(manager, "_loops", {}) or {}).get(idx)
    live = getattr(loop, "_self_evolve", None) if loop else None
    if live is not None:
        # 跑着就用它自己那份：引擎内存态比文件新，读文件会少最近几条
        return live

    cached = _panel_evolve_cache.get(idx)
    if cached is None:
        cached = SelfEvolveEngine(
            config={"enabled": True},
            log_callback=lambda msg: logger.info(msg),
            data_file=str(account_file(BASE_DIR / "data" / "evolution_data.json", idx)),
        )
        _panel_evolve_cache[idx] = cached
    _self_evolve = cached
    return cached


def _all_evolve_engines() -> list:
    """当前存在的自进化引擎：跑着的账号各一份 + 面板为没跑的号建的那几份。

    开关是全局配置，改一次要同步到所有已建好的引擎，否则弹窗显示"已启用"
    而正在跑的账号还在用旧引擎的关闭状态。
    """
    out = []
    manager = globals().get("_multi_manager")
    if manager is not None:
        for loop in (getattr(manager, "_loops", {}) or {}).values():
            eng = getattr(loop, "_self_evolve", None)
            if eng is not None:
                out.append(eng)
    out.extend(_panel_evolve_cache.values())
    return out


def _evolve_account_name(idx: int) -> str:
    """给弹窗标注当前看的是哪个账号（取不到配置就说索引，不编名字）"""
    try:
        cfg = load_config()
        accs = cfg.greet.accounts or []
        if 0 <= idx < len(accs):
            return accs[idx].name or f"账号{idx + 1}"
    except Exception:
        pass
    return f"账号{idx + 1}"


@app.route("/api/evolution/report", methods=["GET"])
def api_evolution_report():
    """获取自进化报告 — 回复效果统计、模板效果分析、策略调整历史。"""
    try:
        engine = _ensure_self_evolve()
        report = engine.get_evolution_report()
        idx = _resolve_evolve_account()
        return jsonify({"status": "ok", "report": report,
                        "account": idx, "account_name": _evolve_account_name(idx)})
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
    """启用或禁用自进化：写进配置，再同步到运行中的引擎。

    以前只 engine.set_enabled() 改内存：下一轮热重载就被 config 的值冲回去，
    而右侧「运行时开关」里那个同名开关改的是配置——同一个功能两处两套数。
    self_evolve_enabled 是全局字段（两个号是同一个人，经验口径不该分叉）。
    """
    global _config
    try:
        data = request.get_json() or {}
        enabled = bool(data.get("enabled", True))
        cfg = UnifiedConfig.load()
        cfg.self_evolve_enabled = enabled
        new_cfg = cfg.to_dict()
        save_config(new_cfg)
        save_overrides(new_cfg)
        _config = UnifiedConfig.load()
        for eng in _all_evolve_engines():
            eng.set_enabled(enabled)
        return jsonify({"status": "ok",
                        "message": f"自进化功能已{'启用' if enabled else '禁用'}（全局，已写入配置）",
                        "enabled": enabled})
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


@app.route("/api/evolution/lessons", methods=["GET"])
def api_evolution_lessons():
    """在库经验列表（active + 最近退役）。"""
    try:
        engine = _ensure_self_evolve()
        report = engine.get_evolution_report()
        return jsonify({"status": "ok", "lessons": report["lessons"]})
    except Exception as e:
        logger.exception("获取经验列表失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/lessons/refine", methods=["POST"])
def api_evolution_lessons_refine():
    """复盘执行轨迹，把反复出现的闸门拦截沉淀成经验（沉淀前自动留快照）。"""
    try:
        engine = _ensure_self_evolve()
        result = engine.refine_lessons()
        return jsonify({"status": "ok", "result": result})
    except Exception as e:
        logger.exception("经验复盘失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/lessons/<int:lesson_id>/confirm", methods=["POST"])
def api_evolution_lessons_confirm(lesson_id):
    """确认一条经验的效果：positive/negative；连负两次自动退役。"""
    try:
        data = request.get_json() or {}
        engine = _ensure_self_evolve()
        lesson = engine.confirm_lesson(lesson_id, data.get("effect", ""))
        return jsonify({"status": "ok", "lesson": lesson})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.exception("确认经验效果失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/lessons/<int:lesson_id>/retire", methods=["POST"])
def api_evolution_lessons_retire(lesson_id):
    """人工退役一条经验。"""
    try:
        data = request.get_json() or {}
        engine = _ensure_self_evolve()
        lesson = engine.retire_lesson(lesson_id, data.get("reason", ""))
        return jsonify({"status": "ok", "lesson": lesson})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    except Exception as e:
        logger.exception("退役经验失败")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/evolution/rollback", methods=["POST"])
def api_evolution_rollback():
    """回滚进化数据到最近一份快照。"""
    try:
        engine = _ensure_self_evolve()
        if engine.rollback_evolution_data():
            return jsonify({"status": "ok", "message": "已回滚到最近一份快照"})
        return jsonify({"status": "error", "message": "没有可用的快照"}), 400
    except Exception as e:
        logger.exception("回滚进化数据失败")
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
        emit("status_update", _enrich_status(manager.get_status()))
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
            # 必须走 _ensure_manager()：它才会把 greet/reply/wind 三个回调接上。
            # 以前这里自己 new 了一个不带回调的 MultiAccountManager，
            # 投递事件根本不会 emit，界面只能靠刷新看记录；而且它调的
            # start_all() 这个方法压根不存在，一进线程就抛"启动失败"。
            manager = _ensure_manager()
            global _multi_manager
            _multi_manager = manager
            manager.start()
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

    # 由服务自己打开默认浏览器，start.bat 就不必再额外开一个 cmd 窗口
    threading.Timer(1.5, _open_dashboard).start()
    # 体检结果缺失或超过 24 小时时后台补一轮，前端一进来看接口就知道哪些能用
    threading.Timer(10.0, _auto_ai_health).start()
    socketio.run(app, **_run_kwargs)


def _auto_ai_health():
    """启动时自动做一轮 AI 接口体检（仅当结果过期时）。"""
    from datetime import datetime, timedelta
    try:
        from boss_bot.ai_health import load_health
        health = load_health()
        updated = health.get("updated_at") or ""
        try:
            dt = datetime.strptime(updated, "%Y-%m-%d %H:%M:%S")
            if datetime.now() - dt < timedelta(hours=24):
                return
        except ValueError:
            pass
        started, message = _start_ai_health()
        logger.info(f"启动自动 AI 体检：{message}" if started else f"跳过自动体检：{message}")
    except Exception as e:
        logger.warning(f"自动 AI 体检失败（不影响使用）: {e}")


def _open_dashboard():
    """打开管理界面。失败不影响服务运行。"""
    import webbrowser
    try:
        webbrowser.open("http://localhost:5000")
    except Exception as e:
        logger.warning(f"自动打开浏览器失败，请手动访问 http://localhost:5000（{e}）")


if __name__ == "__main__":
    main()
