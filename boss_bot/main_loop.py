"""
统一主循环模块 — 双标签页并行架构

整合打招呼引擎（GreetEngine）和回复引擎（ReplyEngine），
共享同一个浏览器实例（BrowserManager），通过两个标签页并行执行：

- 搜索/打招呼标签页：自动搜索岗位、发送打招呼消息
- 聊天/回复标签页：监控未读消息、自动回复

核心设计：
- 双标签页并行：打招呼和回复在各自标签页中同时运行，互不干扰
- 共享浏览器：两个标签页属于同一个浏览器实例，共享 Cookie 和登录态
- 独立暂停/恢复：打招呼和回复可各自暂停/恢复
- 人工接管模式：检测到重要事件时自动暂停回复
- 错误恢复：浏览器断连自动重连，登录失效等待重新登录，验证码暂停通知
- Flask 兼容：log_callback 将日志传递到 Web 界面，状态字典可共享

使用方式：
    loop = UnifiedBotLoop(config=cfg, log_callback=print)
    loop.start()          # 启动主循环（非阻塞，后台双线程）
    loop.pause_greet()    # 暂停打招呼
    loop.resume_reply()   # 恢复回复
    loop.stop()           # 停止主循环
"""

import logging
import threading
import time
import random
import os
import json
import shutil
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional, Callable

from boss_bot.unified_config import (UnifiedConfig, BASE_DIR, resolve_path,
                                     account_file, write_json_atomic)
from boss_bot.browser_launcher import BrowserManager, BOSS_AUTH_COOKIES
from boss_bot.greet_engine import (GreetEngine, GREETING_MISSING_REASON,
                                   in_probe_band)
from boss_bot.reply_engine import ReplyEngine, conversation_rejected
from boss_bot.intent import veto_hit_anywhere
from boss_bot.contact_ledger import CONTACT_DECLINED_MARK
from boss_bot.page_handler import BossChatHandler
from boss_bot.pending_resume import pending_resume_asks, resume_already_sent
from boss_bot.state_store import StateStore
from boss_bot.stats import Stats
from boss_bot.notify import Notifier
from boss_bot.message_store import MessageStore
from boss_bot.reply_queue import (chat_state, inbound_body, is_pinned, owed_replies,
                                  followup_due, mark_followed,
                                  worth_following_up)
from boss_bot.self_evolve import SelfEvolveEngine
from boss_bot.metrics import get_metrics

# DrissionPage 断连异常
try:
    from DrissionPage.errors import PageDisconnectedError
except ImportError:
    PageDisconnectedError = None

logger = logging.getLogger(__name__)

# 打招呼两轮搜索之间的间隔秒数
GREET_ROUND_INTERVAL = 30

# 主动跟进的记账文件：谁追过几次、最后一次什么时候，重启不能重置
FOLLOWUP_STATE_FILE = BASE_DIR / "data" / "followup_state.json"

# 追到面试的话术：第 1 次问岗位还在不在并约面试，第 2 次换个说法，别复读。
# 每条都必须自己把"只找线上"讲明白：2026-10-07 16:59 真发出去的一条写的是
# "我随时能到岗，想约个时间当面聊聊"——模板不判据，跟进轮就成了另一个漏承诺的口子。
FOLLOWUP_LINES = (
    "{boss}您好，之前聊的{job}我很有兴趣，我时间灵活，方便线上远程沟通或视频面试吗？",
    "{boss}您好，{company}这个岗位还在招吗？我这边只找线上远程就能做的，"
    "方便的话咱们线上细聊。",
)

# 等待人工登录时的登录态轮询间隔（秒）
LOGIN_POLL_INTERVAL = 5

# BOSS 的登录页：界面"登录"按钮和运行循环缺会话时都停在这里
BOSS_LOGIN_URL = "https://www.zhipin.com/web/user/?ka=header-login"

# 碰到人机验证：提示人工处理，最多等这么多秒，超时跳过当前任务
CAPTCHA_WAIT_SECONDS = 60
CAPTCHA_POLL_SECONDS = 2
# 连续这么多次都没人应答才真暂停，避免对着验证页无限空转
CAPTCHA_STRIKES_TO_PAUSE = 3
# 机器自己判出来的暂停（空搜索、验证码连击）过这么久要自己再试一次。
# 实测 10-06 两个号 07:09 被"连续3轮搜索结果为0"暂停后一直挂到中午，
# 那段时间 BOSS 的当日沟通额度还没重置、搜索页也只是暂时空白，
# 永久暂停等于把"投递中"变成了"没人点就永远停着"。
GREET_PAUSE_RETRY_SEC = 900

# 现场/线下面试邀请：优先点平台上的「拒绝」，点不到才发这句话（同一口径的两种落法）。
# 用户 2026-10-06："我只要线上的兼职不要线下的…他发面试邀请，你直接拒绝就行了"。
OFFLINE_INTERVIEW_DECLINE = (
    "不好意思，我这边只找线上远程就能完成的兼职，线下面试就不占用您的时间了，"
    "这个岗位我先放弃，感谢您的邀请。")

# 按天归档的进程内互斥：记录文件所有账号共用，一天只能归一次
_ARCHIVE_LOCK = threading.Lock()

# 登录态的两路证据：页面落在哪 + 浏览器里有没有未过期的登录 Cookie。
# 单看 URL 会误判——/web/geek/chat 是 SPA，未登录时它先渲染一下才跳 /web/user，
# 抓早了就是"看着像登录了"或者"看着像过期了"，两种都出过事。
LOGIN_WALL_MARKS = ("/web/user", "/login", "passport.")
CHAT_PAGE_MARKS = ("/web/geek/chat",)

# 回复侧连续判这么多次登录失效就停这个号，等人工——不再每 30 秒空刷
REPLY_LOGIN_FAIL_LIMIT = 3
# 等页面跳转稳定：最多这么多秒，连续两次 URL 一样就算定了
URL_SETTLE_SECONDS = 8


def login_state_of(url: str, has_auth_cookie: bool) -> str:
    """logged_in / login_wall / uncertain —— 两路证据一致才给结论。

    uncertain 不等于失效：它只表示"这一眼看不准"，调用方要么重看要么留痕，
    绝不能拿它当"Cookie 过期"去动用户的会话文件。
    """
    u = (url or "").lower()
    wall = any(k in u for k in LOGIN_WALL_MARKS)
    chat = any(k in u for k in CHAT_PAGE_MARKS)
    if wall and not has_auth_cookie:
        return "login_wall"
    if chat and has_auth_cookie:
        return "logged_in"
    return "uncertain"


def has_live_auth_cookie(cookies) -> bool:
    """浏览器里有没有还没过期的 BOSS 登录项。"""
    for c in cookies or []:
        if not isinstance(c, dict) or c.get("name") not in BOSS_AUTH_COOKIES:
            continue
        if not c.get("value"):
            continue
        try:
            expires = float(c.get("expires") or -1)
        except (TypeError, ValueError):
            expires = -1
        if expires < 0 or expires > time.time():
            return True
    return False


def archive_cookie_file(path: str, archive_dir: str, label: str = "") -> str:
    """把一份 Cookie 挪进归档目录，返回归档后的路径（原文件不存在则返回空串）。

    以前这里是 unlink() 直接删：判错一次登录态就永久丢掉用户唯一的会话文件，
    人工也没法拿它排查为什么"突然要重新登录"。归档后原路径为空，下一轮照样
    走完整登录，但内容还在。
    """
    src = Path(path)
    if not src.is_file():
        return ""
    dest_dir = Path(archive_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = "".join(ch for ch in str(label) if ch.isalnum() or ch in "-_") or src.stem
    dest = dest_dir / f"{safe}_{stamp}{src.suffix}"
    if dest.exists():                      # 同一秒归档两次（多账号并行）不能互相覆盖
        n = 1
        while dest_dir / f"{safe}_{stamp}_{n}{src.suffix}" in (dest,) or \
                (dest_dir / f"{safe}_{stamp}_{n}{src.suffix}").exists():
            n += 1
        dest = dest_dir / f"{safe}_{stamp}_{n}{src.suffix}"
    shutil.move(str(src), str(dest))
    return str(dest)


def _should_show_frontend(level: str, msg: str) -> bool:
    """判断日志是否应该推送到前端。
    
    前端显示所有 INFO/WARN/ERROR/SUCCESS/CRITICAL 级别日志，不过滤。
    DEBUG 级别不推送到前端（后台日志文件保留）。
    """
    level_upper = level.upper()
    if level_upper == "DEBUG":
        return False
    return True


class UnifiedBotLoop:
    """统一主循环 — 双标签页并行架构

    通过 BrowserManager 共享浏览器，两个标签页分别执行打招呼和回复任务。
    打招呼线程使用搜索标签页，回复线程使用聊天标签页，两者并行运行。

    Attributes:
        config: UnifiedConfig 实例，包含所有配置
        log_callback: 日志回调函数，用于将日志传递到 Web 界面
    """

    # ─────────────────────────────────────────────
    # 初始化
    # ─────────────────────────────────────────────

    def __init__(self, config: Optional[UnifiedConfig] = None,
                 log_callback: Optional[Callable] = None,
                 account_index: int = 0,
                 greet_event_cb: Optional[Callable] = None,
                 reply_event_cb: Optional[Callable] = None,
                 wind_control_cb: Optional[Callable] = None):
        # 每个循环各持一份"基准 + 本账号覆盖"：直接存传进来的对象的话，
        # 账号 0 的判分阈值会盖到账号 1 身上（两个循环共用同一个 config）
        self.config = (config or UnifiedConfig.load()).apply_account(account_index)
        self.log_cb = log_callback
        self.account_index = account_index
        self._greet_event_cb = greet_event_cb
        self._reply_event_cb = reply_event_cb
        self._wind_control_cb = wind_control_cb

        # 获取账号名称，用于日志前缀
        if account_index < len(self.config.greet.accounts):
            self.account_name = self.config.greet.accounts[account_index].name
        else:
            self.account_name = f"账号{account_index}"

        # 运行状态
        self._running = False
        self._logged_in = False
        self._needs_login = False
        # 需要登录的原因，供前端状态点说明"为什么是黄的"
        self._login_reason = ""
        self._greet_paused = False
        # 只有"因为到达每日上限而暂停"才允许跨零点自动恢复；人工暂停不动
        self._greet_paused_by_cap = False
        self._greet_cap_paused_on = ""
        # 机器自判暂停的重试时刻（0 = 没有，人工暂停一律留 0）
        self._greet_auto_resume_at = 0.0
        self._reply_paused = False
        # 连续多少次"等了 60 秒没人过验证"之后才真暂停
        self._captcha_strikes = 0
        # 最近一次健康检查的页面判据：提示里要说清"凭什么说这是验证码"
        self._health_why = ""
        # 回复侧连续判了几次登录失效：到 REPLY_LOGIN_FAIL_LIMIT 就停下等人工
        self._login_fail_streak = 0
        # 判分复盘：本轮已经用掉几次"追问不匹配原因"的配额（每号各算，每轮清零）
        self._probe_used_this_round = 0
        # 采集口径：启动后全量同步一次侧栏，之后回复轮只看未读
        self._full_sync_done = False
        # 存档里欠回复的会话：一条处理失败后多久才再试（侧栏对不上行时会反复空跑）
        self._owed_attempt_at = {}
        self._owed_last_scan = 0.0
        # 红点深扫（滚到侧栏底部）上次时刻：0 = 启动后第一轮就深扫一次
        self._deep_sweep_at = 0.0
        # 主动跟进：上次扫描的时刻 + 各会话已追了几次（落盘，重启不重追）
        self._followup_last_scan = None
        self._followup_state = self._load_followup_state()
        # 自进化引擎在 _init_engines 里构造（先置空，热重载/状态查询要能安全引用）
        self._self_evolve = None
        self._current_mode = "idle"
        self._current_chat = None
        self._last_check = ""

        # 线程控制
        self._init_thread: Optional[threading.Thread] = None
        self._greet_thread: Optional[threading.Thread] = None
        self._reply_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._login_event = threading.Event()
        # 界面"登录"按钮拉起的后台等待线程，以及它是否在等
        self._login_thread: Optional[threading.Thread] = None
        self._login_wait_active = False

        # 统计数据
        self._stats_dict = {
            "greet_applied": 0,
            "greet_skipped": 0,
            "greet_total": 0,
            "reply_sent": 0,
            "reply_skipped": 0,
            "resume_sent": 0,
            "important_events": 0,
            "greet_rounds": 0,
            "reply_rounds": 0,
        }
        # 累计/当日漏斗指标 — 持久化，重启和记录截断都不影响
        self._metrics = get_metrics()

        # 共享浏览器管理器 — 使用指定账号的调试端口和独立用户数据目录
        browser_cfg = self.config.browser
        user_data_dir = str(resolve_path(
            browser_cfg.user_data_dir
            or Path("browser_data") / f"account_{account_index}"
        ))
        self.browser_manager = BrowserManager(
            config=browser_cfg,
            account_index=account_index,
            port=browser_cfg.debug_port + account_index,
            user_data_dir=user_data_dir,
        )

        # 回复相关组件（延迟初始化，登录后创建）
        self._chat_handler: Optional[BossChatHandler] = None
        self._reply_engine: Optional[ReplyEngine] = None
        self._state_store: Optional[StateStore] = None
        self._stats: Optional[Stats] = None
        self._notifier: Optional[Notifier] = None
        self._msg_store: Optional[MessageStore] = None

        # 打招呼引擎（延迟初始化，登录后创建）
        self._greet_engine: Optional[GreetEngine] = None

        # 浏览器重连计数 — 指数退避策略
        self._reconnect_lock = threading.Lock()
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10  # 允许更多次重连
        self._reconnect_base_delay = 2.0   # 基础延迟2秒
        self._reconnect_backoff_factor = 2.0  # 退避因子
        self._reconnect_max_delay = 60.0   # 最大延迟60秒

        # 打招呼/回复总开关 — 由 _hot_reload_config() 每轮刷新，线程内实时判断
        self._greet_enabled = self.config.greet.enabled
        self._reply_enabled = self.config.reply.enabled

        # 数据按天归档配置
        # data/archive/YYYY-MM-DD/ 存放每天的 greet_records + reply_records
        # 保留最近 10 天的归档数据，超过自动清理
        self._archive_dir = Path(BASE_DIR) / "data" / "archive"
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        self._archive_retention_days = 10
        # 记录已归档的日期，避免同一天多次归档
        self._last_archived_date: Optional[str] = None
        # 启动时自动检查并执行归档（如果是新的一天）
        self._check_and_archive_daily_data()

    # ─────────────────────────────────────────────
    # 日志
    # ─────────────────────────────────────────────

    def _log(self, level: str, msg: str):
        """统一日志输出 — 回调 + logging，包含账号名称前缀。

        前端日志精简：只有关键事件（启动/停止/AI匹配/投递结果/错误等）推送到前端。
        后台日志完整：所有级别（含DEBUG）都写入日志文件，方便定位问题。
        """
        prefix = f"[{self.account_name}] " if self.account_name else ""
        full_msg = f"{prefix}{msg}"
        # 前端过滤：只有关键日志推送到前端
        if _should_show_frontend(level, msg) and self.log_cb:
            try:
                self.log_cb(f"[{level}] {full_msg}")
            except Exception:
                pass
        # 后台完整：所有日志都写入文件
        log_method = getattr(logger, level.lower(), None)
        if log_method is None:
            log_method = logger.info
        if level.lower() == "warn":
            log_method = logger.warning
        log_method(full_msg)

    def _emit_reply_event(self, contact_name: str, job_name: str,
                          message_received: str, reply_sent: str,
                          ai_model: str = "", intent: str = "",
                          status: str = "replied"):
        """推送回复事件到前端（通过 reply_record socket 事件）。

        Args:
            contact_name: 聊天对象名称（BOSS 名）
            job_name: 岗位名称
            message_received: 收到的对方消息
            reply_sent: 实际发送的回复内容
            ai_model: 使用的 AI 模型名称（若由 AI 生成）
            intent: 意图标签
            status: replied（已回复）/ skipped（跳过）/ error（错误）
        """
        if not self._reply_event_cb:
            return
        try:
            # 时间戳取自这一轮的动作（与 _add_record 落库那份同一个值）：
            # 各取一次 now() 会差一秒，前端就认不出推送行和库行是同一条。
            # 引擎还没建起来（没连上浏览器）时没有落库方，现取一个即可。
            engine = getattr(self, "_reply_engine", None)
            if engine is not None:
                ts = engine.event_ts()
            else:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 同时发送两套字段名，确保前端 addReplyRecord 能正确映射：
            # 前端期望 chat_name/boss_name, received_message, reply_content
            # 后端原有 contact_name, message_received, reply_sent
            self._reply_event_cb({
                "time": ts[11:19],
                "timestamp": ts,
                "contact_name": contact_name or "",
                "chat_name": contact_name or "",
                "boss_name": contact_name or "",
                "job_name": job_name or "",
                "message_received": message_received or "",
                "received_message": message_received or "",
                "reply_sent": reply_sent or "",
                "reply_content": reply_sent or "",
                "ai_model": ai_model or "",
                "intent": intent or "",
                "status": status,
                "is_skipped": status in ("skipped", "skip", "error"),
                "account_index": self.account_index,
                "account_name": self.account_name,
            })
        except Exception:
            pass

    def _emit_wind(self, message: str, wtype: str):
        """风控事件上报 — 带上账号，免得两个号的风控事件分不清是谁的。"""
        if not self._wind_control_cb:
            return
        try:
            self._wind_control_cb(message, wtype,
                                  account_index=self.account_index,
                                  account_name=self.account_name)
        except Exception:
            pass

    # ─────────────────────────────────────────────
    # 对外接口
    # ─────────────────────────────────────────────

    def start(self):
        """启动主循环（在后台线程中运行，非阻塞）。

        启动流程：
        1. 启动共享浏览器
        2. 检查/等待登录
        3. 登录成功后创建两个标签页
        4. 并行启动打招呼线程和回复线程
        """
        if self._running:
            self._log("WARN", "主循环已在运行中")
            return

        self._running = True
        self._stop_event.clear()

        # 启动初始化线程（负责登录+创建标签页+启动双线程）
        self._init_thread = threading.Thread(target=self._init_and_run, daemon=True)
        self._init_thread.start()
        self._log("INFO", "统一主循环已启动")

    def stop(self):
        """停止主循环，关闭浏览器。"""
        if not self._running and self._current_mode == "idle":
            return  # 已经停止，不重复执行
        self._log("INFO", "正在停止主循环...")
        self._running = False
        self._stop_event.set()

        # 停止打招呼引擎
        if self._greet_engine is not None:
            self._greet_engine.stop()

        # 等待线程结束
        for t in [self._greet_thread, self._reply_thread, getattr(self, '_init_thread', None)]:
            if t is not None and t.is_alive():
                t.join(timeout=10)

        # 关闭浏览器
        try:
            self.browser_manager.close()
        except Exception as e:
            self._log("WARN", f"关闭浏览器异常: {e}")

        self._current_mode = "idle"
        self._log("INFO", "主循环已停止")

    def pause_greet(self):
        """暂停打招呼功能。回复功能不受影响。

        人工暂停不带自动恢复：清掉上一轮机器设的重试时刻，
        否则用户刚点完暂停，十几分钟后机器人自己跑回去投递。
        """
        self._greet_paused = True
        self._greet_paused_by_cap = False
        self._greet_auto_resume_at = 0.0
        if self._greet_engine is not None:
            self._greet_engine.stop()
        self._log("INFO", "打招呼已暂停")

    def resume_greet(self):
        """恢复打招呼功能。"""
        self._greet_paused = False
        self._greet_paused_by_cap = False
        self._greet_auto_resume_at = 0.0
        self._log("INFO", "打招呼已恢复")

    def pause_reply(self):
        """暂停回复功能（人工接管模式）。打招呼功能不受影响。"""
        self._reply_paused = True
        if self._state_store is not None:
            self._state_store.pause(reason="手动暂停回复（人工接管）")
        self._log("INFO", "回复已暂停（人工接管模式）")

    def resume_reply(self):
        """恢复回复功能。"""
        self._reply_paused = False
        if self._state_store is not None:
            self._state_store.resume()
        self._log("INFO", "回复已恢复")

    @property
    def phase(self) -> str:
        """账号当前阶段：stopped / starting / waiting_login / running。

        _running 在 start() 第一时间就置真，浏览器还没起来时前端已经显示"运行中"，
        绿点骗人；这里把初始化中、等登录、真在跑分开。
        """
        if not self._running:
            return "stopped"
        if self._needs_login:
            return "waiting_login"
        if not self._logged_in or self._current_mode == "idle":
            return "starting"
        return "running"

    def get_status(self) -> dict:
        """获取当前运行状态。"""
        return {
            "index": self.account_index,
            "name": self.account_name,
            "running": self._running,
            # 等登录时不能报已登录：_logged_in 可能是上一轮会话留下的真值，
            # 直接吐出去会让界面的红点和"正在等待登录"自相矛盾
            "logged_in": bool(self._logged_in and not self._needs_login),
            "needs_login": self._needs_login,
            # login_reason: cookie_expired / no_cookie / cookie_error /
            #               login_timeout / session_lost / "" — 红黄点要能说明原因
            "login_reason": self._login_reason,
            "phase": self.phase,
            "greet_paused": self._greet_paused,
            "reply_paused": self._reply_paused,
            "current_mode": self._current_mode,
            "current_chat": self._current_chat,
            "last_check": self._last_check,
            "stats": dict(self._stats_dict),
        }

    def confirm_login(self):
        """确认登录完成（外部调用，通知主循环用户已手动登录）。"""
        self._log("INFO", "收到登录确认信号")
        self._login_event.set()

        if self._greet_engine is not None:
            self._greet_engine.confirm_login()
        if self._chat_handler is not None:
            self._chat_handler.confirm_and_save()

        self._needs_login = False
        self._logged_in = True
        self._login_reason = ""

    # ─────────────────────────────────────────────
    # 数据按天归档
    # ─────────────────────────────────────────────

    def _check_and_archive_daily_data(self):
        """检查是否是新的一天，如果是则归档当前数据并清空。

        归档逻辑：
        1. 获取当前日期 YYYY-MM-DD
        2. 如果与 _last_archived_date 不同（新的一天）：
           a. 将 data/greet_records.json 和 data/reply_records.json 复制到 data/archive/YYYY-MM-DD/
           b. 清空当前数据文件（开始新一天的数据）
           c. 删除超过 10 天的归档数据
           d. 更新 _last_archived_date

        记录文件是所有账号共用的，归档必须全天只做一次：两个账号的循环各自
        归档时，后跑的那个会拷走已被清空的空文件，等于丢掉一整天数据。
        用 data/archive/.last_archived 标记文件 + 进程内锁来保证只跑一次
        （标记落盘，CLI 和 Web 同时开着也不会各归一次）。
        """
        today_str = date.today().isoformat()  # YYYY-MM-DD
        if self._last_archived_date == today_str:
            return
        with _ARCHIVE_LOCK:
            marker = self._archive_dir / ".last_archived"
            try:
                prev = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
            except Exception:
                prev = ""
            if prev == today_str:
                # 别的账号循环（或另一个进程）今天已经归过档了
                self._last_archived_date = today_str
                return
            self._do_archive(today_str, prev, marker)

    def _do_archive(self, today_str: str, prev: str, marker: Path):
        """真正执行归档，调用方须持有 _ARCHIVE_LOCK。"""
        try:
            # 首次启动：只记录日期，不归档（避免把今天已有的数据归档掉）
            if prev == "" and self._last_archived_date is None:
                self._last_archived_date = today_str
                try:
                    self._archive_dir.mkdir(parents=True, exist_ok=True)
                    marker.write_text(today_str, encoding="utf-8")
                except Exception as e:
                    self._log("WARN", f"写入归档标记失败: {e}")
                # 启动时清理过期归档
                self._clean_old_archives()
                return

            # 新的一天：归档昨天的数据（标记里是上次归档日，缺省退回内存值）
            archive_for = prev or self._last_archived_date or today_str
            self._log("INFO", f"检测到新的一天（{today_str}），开始归档 {archive_for} 的数据...")

            # 归档目录：data/archive/YYYY-MM-DD/
            archive_subdir = self._archive_dir / archive_for
            archive_subdir.mkdir(parents=True, exist_ok=True)

            # 复制 greet_records.json 和 reply_records.json 到归档目录
            data_dir = Path(BASE_DIR) / "data"
            for filename in ("greet_records.json", "reply_records.json"):
                src = data_dir / filename
                if src.exists():
                    dst = archive_subdir / filename
                    try:
                        shutil.copy2(str(src), str(dst))
                        self._log("DEBUG", f"已归档 {filename} 到 {archive_subdir}")
                    except Exception as e:
                        self._log("WARN", f"归档 {filename} 失败: {e}")

            # 归档成功后先落标记：中途崩溃也不会把清空后的空文件再归一次
            try:
                marker.write_text(today_str, encoding="utf-8")
            except Exception as e:
                self._log("WARN", f"写入归档标记失败: {e}")

            # 清空当前数据文件（开始新一天的数据）
            # 必须走 store 单例：引擎内存里还留着整份记录，只改磁盘文件的话
            # 下一次 add() 会把旧记录整团写回去，"按天归档"根本清不干净
            self._reset_record_stores()

            # 更新归档日期
            self._last_archived_date = today_str

            # 清理过期归档
            self._clean_old_archives()

            self._log("INFO", f"数据归档完成，新一天数据已清空")
        except Exception as e:
            self._log("WARN", f"数据归档检查异常: {e}")

    def _reset_record_stores(self):
        """把回复/打招呼记录存储清空（内存 + 磁盘），供归档后开新一天用。"""
        from boss_bot.reply_record import _get_reply_store, _get_greet_store
        for label, getter in (("reply_records.json", _get_reply_store),
                              ("greet_records.json", _get_greet_store)):
            try:
                getter().clear()
                self._log("DEBUG", f"已清空 {label}")
            except Exception as e:
                self._log("WARN", f"清空 {label} 失败: {e}")

    def _clean_old_archives(self):
        """清理超过保留天数的归档数据。"""
        try:
            if not self._archive_dir.exists():
                return
            today = date.today()
            retention = self._archive_retention_days

            for subdir in self._archive_dir.iterdir():
                if not subdir.is_dir():
                    continue
                try:
                    # 目录名应为 YYYY-MM-DD
                    archive_date = date.fromisoformat(subdir.name)
                    age_days = (today - archive_date).days
                    if age_days > retention:
                        shutil.rmtree(str(subdir))
                        self._log("INFO", f"已清理过期归档: {subdir.name}（{age_days}天前）")
                except ValueError:
                    # 目录名不是有效日期，跳过
                    continue
                except Exception as e:
                    self._log("WARN", f"清理归档 {subdir.name} 失败: {e}")
        except Exception as e:
            self._log("WARN", f"清理过期归档异常: {e}")

    def get_archive_list(self) -> list:
        """获取所有归档日期列表（用于前端展示）。

        Returns:
            归档日期字典列表，每个含 date, greet_count, reply_count, size_kb
        """
        result = []
        try:
            if not self._archive_dir.exists():
                return result
            for subdir in sorted(self._archive_dir.iterdir(), reverse=True):
                if not subdir.is_dir():
                    continue
                try:
                    archive_date = date.fromisoformat(subdir.name)
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
        except Exception as e:
            self._log("WARN", f"获取归档列表异常: {e}")
        return result

    def get_archive_data(self, archive_date: str) -> dict:
        """获取指定日期的归档数据。

        Args:
            archive_date: 日期字符串 YYYY-MM-DD

        Returns:
            {"greet_records": [...], "reply_records": [...], "date": ...}
        """
        result = {"date": archive_date, "greet_records": [], "reply_records": []}
        try:
            subdir = self._archive_dir / archive_date
            if not subdir.exists():
                return result
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
        except Exception as e:
            self._log("WARN", f"获取归档数据异常: {e}")
        return result

    # ─────────────────────────────────────────────
    # 初始化与登录
    # ─────────────────────────────────────────────

    def _init_and_run(self):
        """初始化线程：登录 → 创建标签页 → 启动双线程。"""
        try:
            self._log("INFO", "=" * 50)
            self._log("INFO", "BOSS 统一机器人启动（双标签页并行架构）")
            self._log("INFO", "=" * 50)

            # 1. 启动浏览器
            if not self._init_browser():
                self._log("ERROR", "浏览器启动失败，主循环退出")
                self._running = False
                return

            # 2. 登录
            if not self._handle_login():
                self._log("ERROR", "登录失败，主循环退出")
                self._running = False
                return

            # 3. 创建两个标签页
            self._log("INFO", "正在创建双标签页...")
            search_page = self.browser_manager.get_search_page()
            chat_page = self.browser_manager.get_chat_page()
            self._log("DEBUG", f"搜索标签页: {search_page}, 聊天标签页: {chat_page}")
            self._log("INFO", "✅ 双标签页已创建：搜索标签页 + 聊天标签页")

            # 4. 初始化引擎
            self._init_engines()

            # 5. 并行启动打招呼线程和回复线程
            self._greet_thread = threading.Thread(
                target=self._greet_loop, daemon=True, name="greet_loop"
            )
            self._reply_thread = threading.Thread(
                target=self._reply_loop, daemon=True, name="reply_loop"
            )

            self._greet_thread.start()
            self._reply_thread.start()

            self._log("INFO", "✅ 打招呼线程和回复线程已并行启动")

            # 等待停止信号
            while self._running and not self._stop_event.is_set():
                self._stop_event.wait(timeout=5)

        except Exception as e:
            self._log("ERROR", f"初始化异常: {e}")
            import traceback
            self._log("ERROR", traceback.format_exc())
        finally:
            self._running = False
            self._current_mode = "idle"
            self._log("INFO", "初始化线程结束")

    def _init_browser(self) -> bool:
        """启动共享浏览器实例。"""
        try:
            self._log("INFO", "正在启动共享浏览器...")
            self._log("DEBUG", f"浏览器配置: type={self.config.browser.browser_type}, "
                              f"chrome_path={self.config.browser.chrome_path}, "
                              f"debug_port={self.config.browser.debug_port}")
            self.browser_manager.launch()
            self._log("DEBUG", "浏览器进程已启动，等待连接...")
            self._log("INFO", "浏览器已启动")
            return True
        except Exception as e:
            self._log("ERROR", f"浏览器启动失败: {e}")
            self._log("DEBUG", f"浏览器启动异常详情: {e!r}")
            # 原因要留在状态里：不然界面只剩一个 stopped，
            # 用户分不清是"没启动"还是"端口/用户目录被占"
            self._login_reason = "browser_failed"
            return False

    def _interruptible_sleep(self, seconds: float):
        """可被停止打断的等待：停止请求一到就返回，不等满。

        登录复核这类等待原来用裸 time.sleep，点"停止"要等它睡完才生效。
        """
        deadline = time.time() + max(0.0, seconds)
        while self._running and time.time() < deadline:
            if self._stop_event.wait(timeout=min(0.2, deadline - time.time())):
                break

    def _handle_login(self) -> bool:
        """处理登录流程。先尝试 Cookie 自动登录，失败则等待用户手动登录。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            self._log("ERROR", "浏览器实例不存在")
            return False

        self._log("INFO", "正在检查登录状态...")

        # 先访问主站
        try:
            self._log("DEBUG", "正在访问主站 https://www.zhipin.com")
            instance.get("https://www.zhipin.com")
            time.sleep(2)
            self._log("DEBUG", f"主站访问完成，当前URL: {instance.url}")
        except Exception as e:
            self._log("WARN", f"访问主站异常: {e}")

        # 使用账号特定的 Cookie 文件（优先账号配置，其次全局配置）
        cookie_file = self._cookie_file()
        self._log("DEBUG", f"Cookie 文件路径: {cookie_file}")
        if cookie_file:
            try:
                if self.browser_manager.load_cookies(cookie_file):
                    self._log("DEBUG", "正在访问聊天页面验证登录态...")
                    state = self._login_state_now(instance)
                    if state == "uncertain":
                        # 两路矛盾多半是 SPA 还在跳转。先复核一次再决定，别急着把
                        # 一个本来就登录着的号推去登录页（23:01 实测白等 80 秒）
                        self._log("DEBUG", "页面与 Cookie 两路证据矛盾，等页面稳定后复核一次...")
                        self._interruptible_sleep(4)
                        state = self._login_state_now(instance)
                    if state == "logged_in":
                        self._logged_in = True
                        self._login_reason = ""
                        self._log("SUCCESS", "Cookie 有效，已自动登录")
                        return True
                    if state == "login_wall":
                        self._login_reason = "cookie_expired"
                        self._log("WARN", "确认是登录墙（页面被踢回登录页且浏览器里没有"
                                          "登录 Cookie），需要重新登录")
                        self._discard_stale_cookies("启动时确认登录墙")
                    else:
                        # 看不准 ≠ 失效：它只表示"这一眼不够"，绝不能拿它当
                        # "Cookie 过期"去动用户的会话文件
                        self._login_reason = "login_uncertain"
                        self._log("WARN", "复核后仍看不准（页面与 Cookie 两路证据矛盾），"
                                          "按需要人工登录处理，Cookie 文件原样保留")
                else:
                    self._login_reason = "no_cookie"
                    self._log("INFO", "无 Cookie 文件或加载失败")
            except Exception as e:
                self._login_reason = "cookie_error"
                self._log("WARN", f"Cookie 加载异常: {e}")
        else:
            self._login_reason = "no_cookie"

        # 只有确认是登录墙才把页面导航到登录页：本来就登录着的时候跳过去，
        # 会把会话页弄脏，回复侧随后就读到"登录墙"，自己把自己绊停
        self._needs_login = True
        if self._login_reason == "cookie_expired":
            self._log("WARN", "需要手动登录，已跳转到登录页面")
            try:
                instance.get(BOSS_LOGIN_URL)
            except Exception:
                pass
        else:
            self._log("WARN", f"登录态判不准（{self._login_reason}），"
                              f"不去动当前页面，等浏览器里出现登录 Cookie")

        self._log("INFO", "请在浏览器中登录 BOSS 直聘（扫码或手机号+验证码），登录成功后会自动继续")

        if not self._wait_for_login(instance, cookie_file):
            # 界面这一列靠它定位问题：前面已经判出 cookie_expired / login_uncertain
            # 时不许用笼统的 login_timeout 盖掉
            self._login_reason = self._login_reason or "login_timeout"
            self._log("ERROR", "登录超时，主循环退出")
            return False

        # 登录确认后保存 Cookie（没确认就不许顶掉原文件）
        self._save_cookies_if_logged_in(instance)

        self._logged_in = True
        self._needs_login = False
        self._login_reason = ""
        return True

    def open_login_page(self) -> dict:
        """界面左侧"登录"按钮：只把该账号的浏览器停在 BOSS 登录页等人工登录。

        新增的账号在跑投递流水线之前必须先能单独登录，所以这条路径不启动任何
        打招呼/回复线程；登录结果由后台线程检测并保存本账号自己的 Cookie。
        """
        if self._running:
            return {"status": "running",
                    "message": "该账号正在运行，跳登录页会打断投递；要重新登录请先停止"}

        instance = self.browser_manager.get_instance()
        if instance is None:
            if not self._init_browser():
                return {"status": "error",
                        "message": "浏览器没起来（端口或用户目录被别的 Chrome 占了），详见日志"}
            instance = self.browser_manager.get_instance()
            if instance is None:
                return {"status": "error", "message": "浏览器起来了但拿不到实例，详见日志"}

        self._login_event.clear()
        self._needs_login = True
        self._logged_in = False
        self._login_reason = "manual_login"
        try:
            instance.get(BOSS_LOGIN_URL)
        except Exception as e:
            self._needs_login = False
            self._login_reason = ""
            return {"status": "error", "message": f"打开登录页失败: {e}"}

        self._log("INFO", f"账号 {self.account_index}：已打开 BOSS 登录页，"
                          f"请在该账号的浏览器窗口完成登录")
        self._login_thread = threading.Thread(
            target=self._finish_manual_login, args=(instance,),
            name=f"boss-login-{self.account_index}", daemon=True)
        self._login_thread.start()
        return {"status": "ok",
                "message": "已打开 BOSS 登录页，登录成功后会自动保存该账号的 Cookie"}

    def _finish_manual_login(self, instance):
        """后台等人工登录完成：成功就按本账号存 Cookie，等不到就如实标超时。"""
        cookie_file = self._cookie_file()
        self._login_wait_active = True
        try:
            ok = self._wait_for_login(instance, cookie_file)
        finally:
            self._login_wait_active = False
        if ok:
            if self._save_cookies_if_logged_in(instance):
                self._logged_in = True
                self._needs_login = False
                self._login_reason = ""
                self._log("SUCCESS", f"账号 {self.account_index} 登录完成")
            else:
                self._login_reason = "cookie_save_failed"
                self._log("ERROR", "登录判定通过但 Cookie 没存下来，界面上的登录态以这里为准")
        else:
            self._login_reason = "login_timeout"
            self._log("WARN", f"账号 {self.account_index} 等待登录超时，未保存 Cookie")

    def _dry_run(self, what: str, detail: str = "") -> bool:
        """演练模式：搜索、AI 判分、决策照常做，最后一下点击不发。

        Returns:
            True 表示处于演练模式，调用方必须跳过真实操作
        """
        if not self.config.dry_run:
            return False
        self._log("INFO", f"🧪 演练模式 | {what}"
                          + (f": {detail[:60]}" if detail else "") + "（未真实操作）")
        return True

    def _cookie_file(self) -> str:
        """本账号实际使用的 Cookie 文件：账号配置优先，全局配置兜底。

        多账号下每个浏览器实例必须读自己那份，否则重连后会串号。
        返回绝对路径：配置里存的是 "zhipin_cookies_1.json" 这种相对名，
        而界面从 flask-version/ 目录启动，不锚定就会按 CWD 写到别处去。
        """
        accounts = self.config.greet.accounts
        acc_cookie = (accounts[self.account_index].cookie_file
                      if self.account_index < len(accounts) else "")
        return str(resolve_path(acc_cookie or self.config.login.cookie_file))

    def _stale_cookie_dir(self) -> str:
        """失效 Cookie 的归档目录（可整份搬走排查，绝不留在原地当有效会话）。"""
        return str(resolve_path(Path("data") / "stale_cookies"))

    def _discard_stale_cookies(self, reason: str):
        """确认会话失效时把本地 Cookie 归档，避免下一轮又拿死会话去撞风控。

        由 login.clear_cookies_on_failure 控制（前端高级设置里的开关），关掉时只记日志。
        开的时候也不再 unlink：今天误判一次就把用户唯一的 zhipin_cookies.json 删没了，
        而那份文件正是人工排查"为什么突然要重新登录"的依据。归档后原路径为空，
        下一轮照样走完整登录，内容还留着。
        """
        if not self.config.login.clear_cookies_on_failure:
            self._log("DEBUG", f"{reason}：按配置保留 Cookie 文件")
            return
        path = self._cookie_file()
        if not path:
            return
        try:
            dest = archive_cookie_file(str(resolve_path(path)), self._stale_cookie_dir(),
                                       self.account_name or f"账号{self.account_index}")
            if dest:
                self._log("INFO", f"{reason}：失效 Cookie 已归档到 "
                                  f"{Path(dest).name}（原文件不再充当有效会话）")
        except OSError as e:
            self._log("WARN", f"{reason}：归档 Cookie 文件失败: {e}")

    def _wait_for_login(self, instance, cookie_file: str) -> bool:
        """等待登录完成 — 自动检测为主，前端「我已登录」按钮只作兜底。

        判"登录成功"必须两路证据一致（浏览器里有未过期登录 Cookie + 会话页没被踢回
        登录页）。今天就是只等 2 秒看 URL，把停在登录页的现场报成了登录成功，随后
        那份登录页 Cookie 被存成文件，顶掉了原来的好会话。

        轮询时先只看 Cookie：还没出现登录项就说明人压根没扫完，此时不去动页面，
        免得把二维码刷没。出现登录项后才真访问会话页确认一次。

        轮次结束的条件是"没人再等这个登录"：运行循环被停掉（``_running`` 变假）
        或收到停止信号就立刻退出；界面"登录"按钮那条独立路径靠
        ``_login_wait_active`` 顶位，它本来就不在跑流水线。
        """
        deadline = time.time() + self.config.login.wait_timeout
        while (time.time() < deadline and not self._stop_event.is_set()
               and (self._running or self._login_wait_active)):
            if self._login_event.wait(timeout=LOGIN_POLL_INTERVAL):
                self._log("INFO", "收到手动确认，按已登录继续")
                return True
            if not self._has_live_auth_cookie(instance):
                continue
            if self._login_state_now(instance) == "logged_in":
                self._log("SUCCESS", "检测到登录成功，自动继续（无需手动点击）")
                return True
        return False

    def _settle_url(self, instance, timeout: float = URL_SETTLE_SECONDS) -> str:
        """等 SPA 把跳转走完再读 URL。

        /web/geek/chat 未登录时会先渲染再跳 /web/user，抓早了两种误判都会发生：
        看成"还停在会话页"（假已登录）或看成"中间态"（假过期）。连续两次读到同一个
        URL 才算稳定。
        """
        deadline = time.time() + timeout
        last = ""
        same = 0
        while time.time() < deadline:
            try:
                cur = instance.url or ""
            except Exception:
                cur = ""
            if cur and cur == last:
                same += 1
                if same >= 1:
                    return cur
            else:
                same = 0
            last = cur
            time.sleep(0.5)
        return last

    def _login_state_read(self, instance) -> str:
        """只读当前标签页：等 URL 稳定 + 看浏览器里的登录项，不主动导航。

        两个线程各占一个标签页，复核时把对方的页面拽走比误判本身更糟。
        """
        return login_state_of(self._settle_url(instance), self._has_live_auth_cookie(instance))

    def _login_state_now(self, instance) -> str:
        """访问会话页并等跳转稳定，返回 logged_in / login_wall / uncertain。"""
        try:
            instance.get("https://www.zhipin.com/web/geek/chat")
        except Exception as e:
            # 有些 DrissionPage 异常 str() 是空串：只打 `{e}` 会留下一条
            # 「访问会话页异常: 」，看不出是标签页没了还是导航超时，
            # 而这一句正是"页面与 Cookie 矛盾→判需人工登录"那条链唯一的现场证据。
            self._log("DEBUG", ("访问会话页异常: %s: %s"
                                % (type(e).__name__, str(e) or repr(e)))[:200])
        return self._login_state_read(instance)

    def _save_cookies_if_logged_in(self, instance=None) -> bool:
        """只在浏览器里确实有未过期登录项时才落盘。

        无条件 save_cookies 等于允许"登录页那一份"覆盖用户原有的会话文件，
        而那份文件是人工排查登录态的唯一依据。
        """
        path = self._cookie_file()
        if not path:
            return False
        if instance is None:
            try:
                instance = self.browser_manager.get_instance()
            except Exception:
                instance = None
        cookies = []
        try:
            cookies = instance._get_all_cookies() or []
        except Exception as e:
            self._log("WARN", f"读浏览器 Cookie 失败，本次不保存: {e}")
            return False
        if not has_live_auth_cookie(cookies):
            self._log("WARN", f"浏览器里没有未过期的登录 Cookie，不覆盖 "
                              f"{Path(path).name}（避免顶掉原有会话）")
            return False
        try:
            self.browser_manager.save_cookies(path)
            self._log("SUCCESS", f"Cookie 已保存到 {Path(path).name}")
            return True
        except Exception as e:
            self._log("WARN", f"Cookie 保存失败: {e}")
            return False

    def _has_live_auth_cookie(self, instance) -> bool:
        """浏览器中是否存在未过期的 BOSS 登录 Cookie。"""
        try:
            return has_live_auth_cookie(instance._get_all_cookies())
        except Exception:
            return False

    def _recheck_before_archive(self, instance, side: str) -> bool:
        """报"要登录"时先自己复核两路证据，确认是登录墙才允许动 Cookie 文件。

        两个线程共用一个浏览器：打招呼侧正在搜索/岗位页跳转时，回复侧读到的是
        半截页面；反过来也一样。23:01 实测两边都凭第一眼判过"失效"。
        """
        state = self._login_state_read(instance)
        if state == "login_wall":
            return True
        self._log("WARN", f"{side}报登录失效，但复核是 {state}（另一个线程正在动这个"
                          f"浏览器）——不动 Cookie 文件、不计失败，本轮先跳过")
        return False

    def _reply_login_guard(self, chat_instance) -> str:
        """回复侧健康检查报"要登录"时先自己复核，确认了才动 Cookie 和计数。

        并发时打招呼侧正在同一个浏览器里跑搜索/岗位页，会话页可能只是在跳转中；
        23:01 实测第一眼误判就把 7 秒前刚存好的 Cookie 归档走了，随后连判三次把
        这个号的回复轮停掉。复核用同一套两路证据（页面 + 浏览器内登录项）。
        """
        if not self._recheck_before_archive(chat_instance, "回复侧"):
            return "waiting"
        self._needs_login = True
        self._login_reason = "session_lost"
        if self._login_fail_streak == 0:
            # 只在第一次确认时归档，后面几次不再反复搬文件
            self._discard_stale_cookies("回复侧确认登录失效")
        if self._reply_login_lost_once():
            return "stop"
        self._log("WARN", "回复侧确认登录态失效，等待重新登录...")
        return "waiting"

    def _reply_login_lost_once(self) -> bool:
        """回复侧判了一次登录失效。返回 True 表示该停下等人工了。"""
        self._login_fail_streak += 1
        if self._login_fail_streak >= REPLY_LOGIN_FAIL_LIMIT:
            self._needs_login = True
            self._login_reason = "session_lost"
            self._log("ERROR", f"回复侧连续 {self._login_fail_streak} 次判登录态失效，"
                               f"这个号的回复轮不再空转，等人工登录")
            return True
        return False

    def _reply_login_ok(self):
        """登录态恢复正常，计数清零。"""
        self._login_fail_streak = 0

    def _init_engines(self):
        """初始化打招呼引擎和回复相关组件。"""
        self._log("INFO", "正在初始化引擎...")

        # 初始化回复相关组件 — 使用聊天标签页
        chat_page = self.browser_manager.get_chat_page()
        self._log("DEBUG", "正在初始化回复组件（BossChatHandler, ReplyEngine, StateStore...）")
        self._chat_handler = BossChatHandler(
            browser_manager=self.browser_manager,
            browser_instance=chat_page,
            cookie_file=self._cookie_file(),
        )

        self._msg_store = MessageStore(account_index=self.account_index)

        # 自进化引擎：只做"回复效果记录 + 评估"，不会自动改你写的话术和规则。
        # 以前生产链路根本没构造它，ReplyEngine._self_evolve 永远是 None，
        # 2687 行代码加 5 个 /api/self_evolve 端点全都读到空数据。
        self._self_evolve = SelfEvolveEngine(
            config={"enabled": self.config.self_evolve_enabled},
            log_callback=lambda msg: self._log("DEBUG", msg),
            data_file=str(account_file(BASE_DIR / "data" / "evolution_data.json",
                                       self.account_index)),
        )
        # 启动即做一次性纠正（幂等）：拒绝重判 negative、泄漏思考过程剔除、超时未答补 ignored
        try:
            purify = self._self_evolve.purify_records()
            if any(purify.values()):
                self._log("INFO", f"自进化数据已纠正: {purify}")
        except Exception as e:
            self._log("WARN", f"自进化数据纠正失败（不影响运行）: {e}")
        self._reply_engine = ReplyEngine(
            self_evolve=self._self_evolve,
            account_name=self.account_name,
            account_index=self.account_index,
            message_store=self._msg_store,
        )
        # 每个账号一份 state/stats：共用的话账号2 会把账号1 处理过的消息当成
        # "已处理"而不回复，人工接管和每日上限也会互相串
        from boss_bot.config import STATE_FILE, STATS_FILE
        self._state_store = StateStore(
            path=account_file(STATE_FILE, self.account_index))
        # 启动时自动清除之前的人工接管暂停状态
        if self._state_store.is_paused():
            self._log("INFO", "检测到之前的人工接管暂停状态，自动恢复")
            self._state_store.resume()
        self._stats = Stats(path=account_file(STATS_FILE, self.account_index))
        self._metrics = get_metrics()
        self._notifier = Notifier()


        # 初始化打招呼引擎 — 使用搜索标签页
        self._log("DEBUG", "正在初始化打招呼引擎（GreetEngine）...")
        self._greet_engine = GreetEngine(
            browser_manager=self.browser_manager,
            config=self.config,
            log_callback=lambda msg: self._log("INFO", msg),
            progress_callback=self._on_greet_progress,
            greet_event_cb=self._greet_event_cb,
            account_index=self.account_index,
            # 验证页要交人工等结果：闸门负责 60 秒时限、到点跳过当前任务、
            # 连续三次没人处理停轮 —— 以前打招呼侧只 return 原因，从不叫它
            captcha_gate_cb=lambda: self._captcha_gate("greet"),
        )
        # 关键：设置 running=True，否则 send_greeting 会直接返回 False
        self._greet_engine.running = True

        self._log("INFO", "引擎初始化完成")

    # ─────────────────────────────────────────────
    # 打招呼线程（使用搜索标签页）
    # ─────────────────────────────────────────────

    def _greet_loop(self):
        """打招呼线程主循环 — 在搜索标签页中执行。"""
        self._log("INFO", "打招呼线程启动")

        if not self.config.greet.enabled:
            self._log("INFO", "打招呼功能未启用，线程退出")
            return

        consecutive_empty_rounds = 0  # 连续空搜索轮次计数

        while self._running and not self._stop_event.is_set():
            try:
                # 总开关在每轮重新判断（前端可在不重启的情况下启用/停用）
                if not self._greet_enabled:
                    # 停用期间也要刷新配置，否则前端重新勾选后永远读不到
                    self._hot_reload_config()
                    self._stop_event.wait(timeout=10)
                    continue

                if self._greet_paused:
                    # 因到达每日上限而暂停时，跨过零点要在暂停分支里自己醒过来：
                    # 这一分支不会走到 _run_greet_round，放在轮次里永远执行不到
                    self._maybe_auto_resume_greet()
                    self._stop_event.wait(timeout=10)
                    continue

                # ── 健康检查：检测验证码/风控/登录态 ──
                health = self._check_health(side="greet")
                if health == "captcha":
                    self._log("ERROR", "⚠️ 检测到验证码/风控拦截，等人工处理（最多 60 秒）")
                    # 人工解掉就继续本轮；超时则跳过这一轮，不再永久暂停
                    self._captcha_gate()
                    continue
                elif health == "need_login":
                    if not self._recheck_before_archive(
                            self.browser_manager.get_instance(), "打招呼侧"):
                        self._stop_event.wait(timeout=30)
                        continue
                    self._log("WARN", "登录态失效，等待重新登录...")
                    self._needs_login = True
                    self._login_reason = "session_lost"
                    self._discard_stale_cookies("运行中确认登录态失效")
                    self._stop_event.wait(timeout=30)
                    continue
                elif health == "browser_disconnected":
                    self._log("WARN", "浏览器断开，尝试重连...")
                    self._try_reconnect_browser()
                    self._stop_event.wait(timeout=10)
                    continue
                elif health == "unknown":
                    # 页面读不到时不盲发：可能是验证码页/标签页被关，先等下一轮再看
                    self._log("WARN", "健康检查无法判断页面状态，本轮跳过打招呼")
                    self._stop_event.wait(timeout=15)
                    continue

                self._current_mode = "greet"
                round_had_jobs = self._run_greet_round()

                # ── 连续空搜索检测 ──
                if round_had_jobs is False:
                    consecutive_empty_rounds += 1
                    if consecutive_empty_rounds >= 3:
                        self._log("ERROR", "⚠️ 连续3轮搜索结果为0，可能触发风控或岗位已投完。"
                                          f"打招呼暂停 {GREET_PAUSE_RETRY_SEC // 60} 分钟后自动再试，"
                                          "也可以现在就去 BOSS 直聘页面看看")
                        self._emit_wind("连续3轮搜索结果为0，请检查BOSS直聘页面", "limit")
                        self._greet_paused = True
                        self._greet_auto_resume_at = time.time() + GREET_PAUSE_RETRY_SEC
                        consecutive_empty_rounds = 0
                else:
                    consecutive_empty_rounds = 0

                # 两轮搜索之间的固定间隔（配置里没有这一项，别再假装可读）
                self._stop_event.wait(timeout=GREET_ROUND_INTERVAL)

            except Exception as e:
                self._log("ERROR", f"打招呼线程异常: {e}")
                import traceback
                self._log("ERROR", traceback.format_exc())
                # 检测浏览器断连，自动重连
                if self._is_connection_error(e):
                    self._log("WARN", "检测到浏览器连接断开，尝试重连...")
                    self._try_reconnect_browser()
                self._stop_event.wait(timeout=30)

        self._log("INFO", "打招呼线程结束")

    def _maybe_probe_rejection(self, job: dict, ai_result: dict) -> bool:
        """AI 判"不符合"之后追问它到底卡在哪一条，结果挂回 job 上给记录和推送用。

        只问 AI，不给 HR 发任何消息——主动问 HR"哪里不合适"是骚扰，撞红线。
        返回 True 表示这一轮又用掉一次配额（问没问到都算，问砸了同样烧了一次接口）。
        """
        chain = getattr(self._greet_engine, "_ai_analyzer", None) if self._greet_engine else None
        if chain is None:
            return False
        try:
            limit = int(self.config.ai.probe_max_per_round)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0 or self._probe_used_this_round >= limit:
            return False
        if not in_probe_band((ai_result or {}).get("score"),
                             self.config.ai.match_threshold):
            return False
        self._probe_used_this_round += 1
        probe = chain.probe_rejection(job, ai_result or {})
        if probe:
            job["_ai_probe"] = probe
            self._log("INFO", f"🔎 追问不匹配原因: "
                              f"{str(probe.get('blocking_requirement', ''))[:60]}")
        return True

    def _record_greet_skip(self, job: dict, ui_status: str, reason: str):
        """跳过结论落库并推送前端；今天本号在这个岗位上记过同类别就不再重复。

        不去重的话一轮就是几百条：招呼语没配那条更是每轮每个岗位都记一遍，
        真发出去的那几条被埋在中间，记录与 BOSS 端根本对不上。
        失败类（可重试）不受这里影响，仍每轮记。
        """
        self._stats_dict["greet_skipped"] += 1
        if self._greet_engine._already_recorded_today(job, reason):
            return
        self._greet_engine._emit_greet_event(job, ui_status, skip_reason=reason)
        self._greet_engine._record_greet(job, is_skipped=True, skip_reason=reason)

    def _run_greet_round(self) -> bool:
        """执行一轮打招呼任务。返回 True 如果有岗位被处理，False 如果所有搜索都为空。"""
        self._log("INFO", "━━━ 开始打招呼轮次 ━━━")
        self._stats_dict["greet_rounds"] += 1
        # 追问配额按"每号每轮"算，不按天：不重置的话一天只问得到 5 条
        self._probe_used_this_round = 0
        any_jobs_found = False

        # 配置每轮读一次即可：原来放在岗位循环里，每个岗位都要重读
        # .env + bot_config + user_profile + overrides 四个文件
        self._hot_reload_config()

        # 额度见底时整轮直接不开。闸门放在点「沟通」那一步的话，
        # 每 30 秒的一轮仍会把列表里几十个岗位逐个走一遍、每个都记一条"已冷却"跳过，
        # 记录表被同一句话灌满，岗位却在这一轮被过完、冷却结束也不回头补投。
        # 返回 True：这既不是"搜索为空"也不是异常，别让空轮计数把它当成投完而永久暂停
        cooldown_left = self._greet_engine.greet_cooldown_left()
        if cooldown_left > 0:
            self._log("INFO", f"⏸️ 打招呼冷却中，本轮不搜索不建记录"
                              f"（剩 {int(cooldown_left // 60) + 1} 分钟，"
                              f"疑似本号当日沟通额度用完）")
            return True

        try:
            tasks = self._build_greet_tasks()
            self._log("DEBUG", f"构建打招呼任务数: {len(tasks)}")
            if not tasks:
                self._log("WARN", "无打招呼任务可执行")
                return False

            for task in tasks:
                if not self._running or self._greet_paused:
                    break

                query = task.get("query", "")
                city = task.get("city", "上海")
                scroll_pages = task.get("scroll_pages", 5)

                self._log("INFO", f"搜索岗位: {city} · {query}")
                self._log("DEBUG", f"搜索参数: scroll_pages={scroll_pages}, "
                                  f"interval_min={task.get('message_interval_min', 3)}, "
                                  f"interval_max={task.get('message_interval_max', 8)}")

                jobs = self._greet_engine.search_jobs(
                    query, city, scroll_pages, task.get("job_type", ""))
                self._stats_dict["greet_total"] += len(jobs)

                if not jobs:
                    self._log("WARN", f"未找到岗位: {city} · {query}")
                    continue

                any_jobs_found = True
                self._log("INFO", f"找到 {len(jobs)} 个岗位，开始打招呼...")
                self._log("DEBUG", f"岗位列表前5个: {[j.get('title', j.get('job_name', '未知')) for j in jobs[:5]]}")

                for job in jobs:
                    if not self._running or self._greet_paused:
                        break
                    cooldown_now = self._greet_engine.greet_cooldown_left()
                    if cooldown_now > 0:
                        # 冷却多半是这一轮里刚挂上的（额度见底）。这时候剩下的岗位
                        # 每一个都会先花 20~40 秒点开再被闸门弹回去，一晚上能刷出
                        # 几百条一模一样的"已冷却"记录，所以整轮直接收手
                        self._log("WARN", f"⏸️ 本轮到此为止：打招呼进入冷却（剩 "
                                          f"{int(cooldown_now // 60) + 1} 分钟），不再逐岗位空跑")
                        return True

                    # 这一次岗位的落库与实时推送共用一个时间戳（见 GreetEngine.begin_event_ts）
                    self._greet_engine.begin_event_ts()

                    # 频率限制检查 — 带时间窗口重置
                    if self._greet_engine._rate_limit_enabled:
                        # 检查是否需要重置计数器（每小时重置）
                        now = time.time()
                        if not hasattr(self, '_rate_window_start'):
                            self._rate_window_start = now
                        if now - self._rate_window_start >= 3600:
                            self._greet_engine.applied_count = 0
                            self._rate_window_start = now
                            self._log("INFO", "每小时计数器已重置")

                        if self._greet_engine.applied_count >= self._greet_engine._max_per_hour:
                            wait_sec = int(3600 - (now - self._rate_window_start))
                            self._log("WARN", f"已达到每小时打招呼上限 {self._greet_engine._max_per_hour}，等待 {wait_sec} 秒后重置")
                            # 真正等待，而不是立即重试
                            self._stop_event.wait(timeout=max(wait_sec, 60))
                            if not self._running:
                                break
                            # 重置计数器
                            self._greet_engine.applied_count = 0
                            self._rate_window_start = time.time()
                            self._log("INFO", "计数器已重置，继续打招呼")
                            break

                        # 每天上限 — 以持久化记录为准，重启进程不会重置（风控相关）
                        max_per_day = self._greet_engine._max_per_day
                        if max_per_day:
                            # 上限是按 BOSS 账号算的，两个账号各 150，不能合计
                            today_applied = sum(
                                1 for r in self._greet_engine._greet_store.filter(
                                    date=date.today().isoformat())
                                if r.is_greeted and r.account_index == self.account_index
                            )
                            if today_applied >= max_per_day:
                                self._log("WARN", f"已达到每日打招呼上限 {max_per_day}，本轮停止，明天继续")
                                self._greet_paused = True
                                self._greet_paused_by_cap = True
                                self._greet_cap_paused_on = date.today().isoformat()
                                break

                    # 去重检查：已沟通过的岗位跳过
                    if self._greet_engine._is_already_chatted(job):
                        self._log("INFO", f"⏭️ 已沟通过: {job.get('job_name', '')}")
                        self._record_greet_skip(job, "already", "已沟通过")
                        continue

                    # 已沟通过的岗位由 _is_already_chatted（按岗位 URL 去重）拦下：
                    # 一条 URL 只属于一个 HR 发的一个岗位，这就是"岗位+HR 都是同一个"
                    # 的唯一可靠依据。此处不再按昵称/岗位名扫描其他会话的聊天记录——
                    # BOSS 只显示"杨女士""胡女士"，同名不同人，扫出来的拒绝属于别人。

                    # 招呼语取不到先判，别排在 AI 之后：
                    # 一是每条岗位每轮白付一次判分预算，
                    # 二是 send_greeting 里那道闸门要到点「沟通」之后才响。
                    # 账号没写话术时 _greeting_for 会按本账号信息给一条默认（2026-10-03），
                    # 所以走到这里只剩"账号配置都取不到"这一种真没救的情况
                    if not self._greet_engine._greeting_for(job)[0]:
                        self._log("WARN", f"⏭️ 跳过: {job.get('job_name', '')}"
                                          f"（原因: {GREETING_MISSING_REASON}）")
                        self._record_greet_skip(job, "skip", GREETING_MISSING_REASON)
                        continue

                    # AI 智能匹配分析 — 配置已在每轮开头热重载过
                    has_ai = self._greet_engine._ai_enabled and bool(self._greet_engine._ai_providers)
                    if has_ai:
                        ai_result, ai_duration = self._greet_engine._analyze_job_with_ai(job)
                        if ai_result is None and job.get("_ai_no_candidate"):
                            # 体检判死 + 冷却把可试的接口清零了。这既不是"AI 说不匹配"
                            # 也不是"AI 答不出"，投出去就是盲投；写 ai_skip 记录又会
                            # 每个岗位烧一条（同 冷却期不烧记录 那条）。留给下一轮真判。
                            self._log("WARN", f"⏸️ 暂无可用 AI 接口，本岗位留到下一轮: "
                                              f"{job.get('job_name', '')}")
                            continue
                        if ai_result is None and self._greet_engine._init_ai() is not None:
                            # AI 判"不匹配"（或按 fail_action 跳过）：原因取自这条岗位自己的
                            # 判分结果，传给 skip_reason 让前端完整显示
                            last_ai = job.get("_ai_result") or {}
                            ai_reason = last_ai.get("reason", "")
                            ai_skip_reason = (
                                f"AI判定不匹配: {ai_reason}" if ai_reason else "AI判定不匹配"
                            )
                            self._log("WARN", f"🤖 AI 判定不匹配，跳过: {job.get('job_name', '')}（{ai_reason[:80] if ai_reason else ''}）")
                            # 边界带上再追问一句"到底卡在哪条硬性要求"，结果写进这条
                            # 记录（ai_probe），复盘界面据此出建议——只问 AI，不问 HR
                            self._maybe_probe_rejection(job, last_ai)
                            self._record_greet_skip(job, "ai_skip", ai_skip_reason)
                            continue
                        if ai_result and ai_result.get("suggested_greeting"):
                            job["_ai_suggested_greeting"] = ai_result["suggested_greeting"]

                    # 随机间隔 — 账号级配置（前端「消息间隔」），每轮任务重建时已是最新值
                    min_interval = task.get("message_interval_min", 3)
                    max_interval = task.get("message_interval_max", 8)
                    delay = random.uniform(min_interval, max_interval)
                    self._stop_event.wait(timeout=delay)
                    if not self._running:
                        break

                    if self._dry_run("本应打招呼",
                                     f"{job.get('job_name', '')} @ {job.get('company', '')}"):
                        continue
                    success = self._greet_engine.send_greeting(job)
                    if success:
                        self._stats_dict["greet_applied"] += 1
                        self._metrics.bump(self.account_index, "greet_sent")
                    else:
                        self._stats_dict["greet_skipped"] += 1

        except Exception as e:
            self._log("ERROR", f"打招呼轮次异常: {e}")
            import traceback
            self._log("ERROR", traceback.format_exc())
            # 检测浏览器断连，自动重连
            if self._is_connection_error(e):
                self._log("WARN", "检测到浏览器连接断开，尝试重连...")
                self._try_reconnect_browser()

        self._log("INFO", "━━━ 打招呼轮次结束 ━━━")
        return any_jobs_found

    def _build_greet_tasks(self) -> list:
        """从配置构建打招呼任务列表（仅当前账号）。"""
        tasks = []
        if self.account_index >= len(self.config.greet.accounts):
            return tasks
        acc = self.config.greet.accounts[self.account_index]
        if not acc.enabled:
            return tasks
        for job in acc.jobs:
            if not job.enabled:
                continue
            tasks.append({
                "query": job.query,
                "city": job.city,
                "job_type": job.job_type,
                "scroll_pages": job.scroll_pages,
                "greeting_message": job.greeting_message,
                "image_files": job.image_files or acc.image_files,
                "message_interval_min": acc.message_interval_min,
                "message_interval_max": acc.message_interval_max,
                "cookie_file": acc.cookie_file,
            })
        return tasks

    def _on_greet_progress(self, progress: dict):
        """打招呼进度回调。"""
        self._stats_dict["greet_applied"] = progress.get("applied", 0)
        self._stats_dict["greet_skipped"] = progress.get("skipped", 0)
        self._stats_dict["greet_total"] = progress.get("total", 0)

    # ─────────────────────────────────────────────
    # 回复线程（使用聊天标签页）
    # ─────────────────────────────────────────────

    def _reply_loop(self):
        """回复线程主循环 — 在聊天标签页中执行。"""
        self._log("INFO", "回复线程启动")

        if not self.config.reply.enabled:
            self._log("INFO", "回复功能未启用，线程退出")
            return

        while self._running and not self._stop_event.is_set():
            try:
                if not self._reply_enabled:
                    # 停用期间也要刷新配置，否则前端重新勾选后永远读不到
                    self._hot_reload_config()
                    self._stop_event.wait(timeout=10)
                    continue

                if self._reply_paused:
                    # 自动恢复的判断在 _hot_reload_config() 里面，而这条分支以前
                    # 直接 continue，永远走不到 → 验证码造成的暂停解不开
                    self._maybe_auto_resume_reply()
                    self._stop_event.wait(timeout=10)
                    continue

                # 热重载所有运行时可变配置（AI/频率/间隔/开关等）
                self._hot_reload_config()

                # 登录态/风控检查：打招呼侧有，回复侧此前完全裸奔，
                # 会话被踢或弹验证码时会空转一整轮才被发现
                # 先对齐聊天标签页：否则健康检查读到的是打招呼侧正在跳转的页面，
                # 搜索页上偶尔出现的风控提示会被误判成"回复侧被验证码拦截"
                self._sync_chat_tab()
                health = self._check_health(side="reply")
                if health == "captcha":
                    self._log("ERROR", "⚠️ 回复侧检测到验证码/风控拦截，等人工处理（最多 60 秒）")
                    self._captcha_gate(pause_field="reply")
                    continue
                if health == "need_login":
                    # 今天实测：这里每 30 秒判一次失效、每 30 秒归档一次 Cookie，
                    # 一路刷到我手动停。现在先复核，确认了才动文件并计数。
                    verdict = self._reply_login_guard(
                        self.browser_manager.get_chat_page())
                    if verdict == "stop":
                        self._log("ERROR", "回复侧已停止本轮，请在左侧该账号上完成登录后再启动")
                        return
                    self._stop_event.wait(timeout=30)
                    continue
                if health == "browser_disconnected":
                    self._try_reconnect_browser()
                    self._stop_event.wait(timeout=10)
                    continue

                self._reply_login_ok()                # 检查是否是新的一天，如果是则归档数据
                self._check_and_archive_daily_data()

                # 采集口径：启动后先全量同步一次最新消息（只存档不回复），
                # 之后回复轮只看未读，不再每轮全量点开。
                # 但同步要把 270 多个会话逐个点开，实测要 25~50 分钟——上一轮存档里
                # 明明压着欠着的提问，却被这段同步挡在后面（用户 2026-10-04：
                # "现在好多的回复没有回"）。所以先把已知欠的回完，再做全量同步。
                if not self._full_sync_done:
                    self._current_mode = "reply"
                    self._run_reply_round()
                    self._run_followup_round()
                    self._full_sync_done = True
                    try:
                        self._sync_chat_tab()
                        self._chat_handler.go_to_chat()
                        self._full_sync_chats()
                        self._backfill_pending_resumes()
                    except Exception as e:
                        self._log("WARN", f"启动全量同步失败（不影响后续未读轮次）: {e}")

                self._current_mode = "reply"
                self._run_reply_round()
                # 追到面试：回复轮只管"对方说了什么"，这一句管"我们说完对方没回"
                self._run_followup_round()

                # 回复检查间隔
                check_interval = self.config.reply.check_interval
                self._stop_event.wait(timeout=check_interval)

            except Exception as e:
                self._log("ERROR", f"回复线程异常: {e}")
                import traceback
                self._log("ERROR", traceback.format_exc())
                # 检测浏览器断连，自动重连
                if self._is_connection_error(e):
                    self._log("WARN", "检测到浏览器连接断开，尝试重连...")
                    self._try_reconnect_browser()
                self._stop_event.wait(timeout=30)

        self._log("INFO", "回复线程结束")

    def _sync_chat_tab(self):
        """把 chat_handler 指回回复侧专用的聊天标签页。

        打招呼引擎会临时开自己的会话标签页，两边抢同一个 handler 时，
        读到的是搜索/岗位页，健康检查和消息读取都会错位。
        """
        chat_page = self.browser_manager.get_chat_page()
        if self._chat_handler is not None and chat_page is not None:
            self._chat_handler.browser = chat_page
            self._chat_handler.page = chat_page.page

    def _run_reply_round(self):
        """执行一轮回复任务：检查未读消息并回复。"""
        self._log("INFO", "━━━ 开始回复轮次 ━━━")
        round_started = time.time()
        self._stats_dict["reply_rounds"] += 1
        self._last_check = datetime.now().strftime("%H:%M:%S")

        try:
            # 人工接管模式提示
            if self._state_store.is_paused():
                info = self._state_store.pause_info()
                self._log("INFO", f"人工接管模式中（{info.get('reason', '')}），仅监控不回复")

            # 关键修复：每次回复轮次都重新获取回复引擎专用的聊天标签页，
            # 每次轮次都指回专用聊天标签页，避免被打招呼侧临时标签页抢占
            self._sync_chat_tab()

            # 导航到聊天页面（使用聊天标签页）
            self._log("DEBUG", "正在导航到聊天页面...")
            self._chat_handler.go_to_chat()
            self._log("DEBUG", "聊天页面导航完成")

            # 获取未读聊天列表：默认只滚到列表靠前那 ~120 行（BOSS 按活跃时间排序，
            # 新消息基本都在前面）；每 5 分钟另有一次滚到底的深扫，捞那些排在后面的红点
            deep = time.time() >= self._deep_sweep_at
            if deep:
                self._deep_sweep_at = time.time() + self.DEEP_SWEEP_SECONDS
            self._log("DEBUG", "正在获取未读聊天列表...（深扫=%s）" % ("是" if deep else "否"))
            unread_chats = self._chat_handler.get_unread_chats(max_rounds=30 if deep else 12)
            self._log("DEBUG", f"获取到未读会话数: {len(unread_chats)}")

            # 置顶标记先落盘（他取消置顶的那一路也会跟着清掉）：欠回复、跟进、
            # 补发简历和两个批量清剿工具读的是存档、不看侧栏，不落盘它们不知道谁被置顶
            for chat_info in unread_chats:
                self._msg_store.set_pinned(
                    chat_info.get("name", ""), bool(chat_info.get("pinned")),
                    company=chat_info.get("company", ""))

            # 红点只是其中一条腿，另外一条腿在本地存档：
            # 启动全量同步把每个会话点开读过一遍，红点是我们自己清掉的，
            # 侧栏又是虚拟列表（一屏只渲染 ~20 行），真提问经常根本进不了候选。
            candidates = list(unread_chats) + self._archive_owed_chats(unread_chats)
            owed_n = len(candidates) - len(unread_chats)
            if owed_n:
                self._log("INFO", f"另外从存档补了 {owed_n} 个欠着的会话"
                                  f"（红点已被启动全量同步清掉）")

            # 「我置顶的岗位你就不要动了，因为那我打算单独聊」（2026-10-08）：
            # 置顶是他亲手标的"这一路我自己谈"，机器连点开都不必
            pinned_rows = [c for c in candidates if is_pinned(c)]
            if pinned_rows:
                candidates = [c for c in candidates if not is_pinned(c)]
                self._log("INFO", "📌 %d 个置顶会话不动（你自己聊）：%s" % (
                    len(pinned_rows),
                    "、".join("%s|%s" % (c.get("name"), c.get("company") or "")
                             for c in pinned_rows[:6])))

            # 先排空"待评估"再处理本轮：要评估的会话多半根本不在这一轮候选里
            # ——HR 回过话之后我们那轮常走跳过分支，就再也不会进 get_reply，
            # 效果统计与 lessons 的支撑量一直饿着（2026-10-07 挂着 48 条）。
            if self._self_evolve and self._self_evolve.enabled:
                try:
                    self._self_evolve.drain_pending_evaluations(
                        self._msg_store.get_all_chats_detail())
                except Exception as e:
                    self._log("WARN", f"排空待评估失败（不影响本轮回复）: {e}")

            if not candidates:
                self._log("DEBUG", "无未读消息")
            else:
                self._log("INFO", f"发现 {len(candidates)} 个待处理会话"
                                  f"（未读 {len(unread_chats)} + 存档补 {owed_n}）")
                # 把 BOSS 自己报的未读数记下来：界面的红点只认这个数，
                # 不再用"本地存了几条 HR 消息"去猜（猜出来的 53/118 和线上 13/1 对不上）
                for chat_info in unread_chats:
                    self._msg_store.set_boss_unread(
                        chat_info.get("name", "未知"),
                        chat_info.get("unread_count") or 1,
                        chat_info.get("job_name", ""),
                        chat_info.get("company", ""))

                for _ci, chat_info in enumerate(candidates):
                    if not self._running or self._reply_paused:
                        break

                    # 一轮要有时间预算：候选按"新红点在前、存档欠账在后"排，
                    # 一轮跑到五六分钟的话，中途刚来的 HR 消息要等整轮做完才轮得上。
                    # 到点收工不影响吞吐——没处理的不会被标已处理，下一轮接着做。
                    spent = time.time() - round_started
                    if spent >= self.REPLY_ROUND_BUDGET_SECONDS:
                        self._log("INFO", f"本轮 {int(spent)}s 到预算，收工；"
                                          f"剩 {len(candidates) - _ci} 个下一轮接着做")
                        break

                    # 每小时主动发送额度是面板上那个框管的东西，回复轮和跟进轮共用。
                    # 到点就收工，剩下的下一小时接着做：一路点进去只读不回更糟——
                    # 红点被自己清掉，存档却一条没回，界面看上去"处理过了"。
                    if not self._reply_engine.can_reply():
                        self._log("INFO", f"本轮回复收工：已到每小时上限"
                                          f"（{self._reply_engine._max_replies_per_hour} 条/小时）")
                        break

                    name = chat_info.get("name", "未知")
                    self._current_chat = name

                    if self._state_store.is_paused():
                        self._log("INFO", f"人工接管模式中，跳过 [{name}]")
                        break

                    self._process_single_chat(chat_info)
                    # 点进去看过 = BOSS 那边这一行的红点没了，本地记的数要跟着归零，
                    # 否则面板会一直挂着"上次扫到时有 5 条未读"
                    self._msg_store.set_boss_unread(
                        chat_info.get("name", "未知"), 0,
                        chat_info.get("job_name", ""), chat_info.get("company", ""))
                    self._reply_engine.wait_human_delay()

            self._current_chat = None

        except Exception as e:
            self._log("ERROR", f"回复轮次异常: {e}")
            import traceback
            self._log("ERROR", traceback.format_exc())
            # 检测浏览器断连，自动重连
            if self._is_connection_error(e):
                self._log("WARN", "检测到浏览器连接断开，尝试重连...")
                self._try_reconnect_browser()

        self._log("INFO", "━━━ 回复轮次结束 ━━━")

    # 一条欠着的会话处理失败后多久才再试：侧栏对不上行时会一直空跑
    OWED_RETRY_SECONDS = 20 * 60
    # 存档扫描节流：回复轮不到 10 秒一轮，每轮都把 600 多个会话文件读一遍太浪费，
    # 而且 8 条回复挤在 9 秒里发出去对 BOSS 也太像机器
    OWED_SCAN_SECONDS = 60
    # 红点深扫（滚到侧栏底）的间隔：平时只滚靠前那 ~120 行就够了
    DEEP_SWEEP_SECONDS = 300
    # 一轮回复的时间预算：超了就收工，下一轮接着做，别让新来的红点等整轮
    REPLY_ROUND_BUDGET_SECONDS = 120
    # 全量同步每读这么多个会话就插一轮回复：整轮同步要逐个点开 270 多个会话
    # （实测 25~50 分钟），一路挡住回复轮就等于这段时间里没人回话
    SYNC_REPLY_EVERY = 20

    def _archive_owed_chats(self, unread_chats) -> list:
        """从本地存档里补出"HR 最后说话、我们没接"的会话，当作本轮候选。

        侧栏红点不可靠（全量同步读过就清掉 + 虚拟列表只渲染 ~20 行），
        2026-10-04 实测连 289 轮"0 个未读"，同期存档里压着「大四还有课吗」
        「通勤距离怎么样」这类真提问没人接。
        只补 姓名+公司 能对上的：实测 34 行里有 4 组重名昵称，公司空着就是猜人。
        """
        cfg = self.config.reply
        now = time.time()
        if now - self._owed_last_scan < self.OWED_SCAN_SECONDS:
            return []
        self._owed_last_scan = now
        convs = [c for c in self._msg_store.get_all_chats_detail()
                 if c.get("account_index") == self.account_index]
        owed = owed_replies(convs, max_age_hours=cfg.owed_max_age_hours,
                            limit=max(cfg.owed_per_round, 0) + len(unread_chats))
        if not owed:
            return []

        taken = {(c.get("name", ""), (c.get("company") or "").strip())
                 for c in unread_chats}
        out = []
        for item in owed:
            key = "%s|%s" % (item["name"], item["company"])
            if (item["name"], item["company"]) in taken:
                continue
            if not item["company"]:
                self._log("INFO", f"⏭️ 欠回复 [{item['name']}]：存档里没有公司名，"
                                  f"重名对不上是谁，不猜")
                continue
            if now - self._owed_attempt_at.get(key, 0) < self.OWED_RETRY_SECONDS:
                continue
            self._owed_attempt_at[key] = now
            # index=-1：enter_chat 万一没按 姓名+公司 对上，会退回按索引点第一行，
            # 那等于点进别人的会话；给个不可能的行号，让它老实回报 not_found
            out.append({"name": item["name"], "company": item["company"],
                        "job_name": item["job_name"], "index": -1,
                        "unread_count": 0, "preview": item["ask"], "owed": True})
            self._log("INFO", f"📌 存档欠回复 [{item['name']}|{item['company']}] "
                              f"已等 {item['age_hours']}h：{item['ask'][:40]}")
        return out[:max(cfg.owed_per_round, 0)]

    @staticmethod
    def _load_followup_state() -> dict:
        """谁追过几次、最后一次追是什么时候——重启不能把次数忘掉，否则每次都从第 1 次重追。"""
        try:
            with open(resolve_path(FOLLOWUP_STATE_FILE), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_followup_state(self):
        try:
            write_json_atomic(resolve_path(FOLLOWUP_STATE_FILE), self._followup_state)
        except Exception as e:
            self._log("WARN", f"跟进次数没记进 {FOLLOWUP_STATE_FILE.name}（本次仍会追）: {e}")

    def _followup_text(self, item: dict, boss: str, job_name: str) -> str:
        """固定话术而不是让 AI 现编：要追的就是"能不能约个面试"这一句，
        一次扫出几十个会话时 AI 又慢又贵，模板反而稳。"""
        tpl = FOLLOWUP_LINES[min(int(item.get("times") or 0), len(FOLLOWUP_LINES) - 1)]
        return tpl.format(boss=boss,
                          job=(job_name or "").strip() or "这个岗位",
                          company=(item.get("company") or "").strip() or "贵公司")

    @staticmethod
    def _in_quiet_hours(now=None) -> bool:
        """23:00–07:00 不主动追。

        被动回复随时都行（对方半夜发消息，第二天看到回复很正常）；
        半夜三点冒出一句"方便约个面试吗"最像机器人，白天被 HR 翻到反而掉好感。
        """
        h = (now or datetime.now()).hour
        return h >= 23 or h < 7

    def _run_followup_round(self):
        """我们说完、对方沉默够久的会话，主动追一句把话头接到面试上。

        漏斗不会自己往前走：只打招呼不追，转化率永远停在"已沟通"。
        够久 / 次数上限 / 两次间隔三条闸门都在 reply_queue.followup_due 里，
        防的是骚扰，也是 BOSS 的反爬。
        """
        cfg = self.config.reply
        if not cfg.followup_enabled:
            return
        now = datetime.now()
        if self._in_quiet_hours(now):
            return
        if (self._followup_last_scan
                and now - self._followup_last_scan < timedelta(minutes=cfg.followup_every_minutes)):
            return
        self._followup_last_scan = now

        convs = [c for c in self._msg_store.get_all_chats_detail()
                 if c.get("account_index") == self.account_index]

        # 置顶的那几路他自己聊（2026-10-08）：机器连"还在招人吗"这一句都不追
        pinned_n = sum(1 for c in convs if is_pinned(c))
        if pinned_n:
            self._log("INFO", f"📌 {pinned_n} 个置顶会话不主动追（你标了自己聊）")
        convs = [c for c in convs if not is_pinned(c)]

        # 只对真聊上过话、而且这一类岗位我们还没判死的会话追：
        # 孤儿存档（没公司名、HR 从没开口）追过去是骚扰；普工/主播那一类
        # 前面刚拒完，后脚问"还在招人吗"就是自己打自己脸。
        convs = [c for c in convs
                 if worth_following_up(c, self.config.ai.title_veto_keywords,
                                       self.config.ai.custom_filter_keywords)]
        due = followup_due(convs, self._followup_state, now=now,
                           after_hours=cfg.followup_after_hours,
                           gap_hours=cfg.followup_gap_hours,
                           max_times=cfg.followup_max_times, limit=6)
        if not due:
            return
        self._log("INFO", f"主动跟进：{len(due)} 个会话对方已沉默 —— "
                          + "、".join(f"{d['name']}({d['idle_hours']}h)" for d in due))
        for item in due:
            if not self._running or self._reply_paused or self._state_store.is_paused():
                break
            # 跟进也吃"每小时最多回复"这个额度：面板上那个框管的是所有主动发送，
            # 只让回复轮计数的话，跟进就能在额度之外另发一串
            if not self._reply_engine.can_reply():
                self._log("INFO", "本轮跟进收工：已到每小时回复上限")
                break
            self._current_chat = item["name"]
            self._send_followup(item)
            self._reply_engine.wait_human_delay()
        self._current_chat = None

    def _send_followup(self, item: dict) -> bool:
        """点开会话、按页面上的实时消息再判一次，才决定追不追。

        存档会滞后（发出去的那句、HR 后来补的话都可能还没读进来），
        只看存档就发等于凭旧账说话。
        """
        name, company = item["name"], item["company"]
        if not self._chat_handler.enter_chat({"name": name, "company": company, "index": -1}):
            self._log("WARN", f"⏭️ 跟进跳过 [{name}]：会话切换校验失败")
            return False
        sel = self._chat_handler.read_selected_row() or {}
        if sel.get("name") and sel.get("name") != name:
            self._log("WARN", f"⏭️ 跟进跳过 [{name}]：选中的是 [{sel.get('name')}]，不猜人")
            return False

        live = self._chat_handler.read_all_messages() or []
        if not live:
            self._log("INFO", f"⏭️ 跟进跳过 [{name}]：页面上没读到消息")
            return False
        job_name = self._chat_handler.get_job_name() or item.get("job_name", "")
        try:
            self._msg_store.merge_messages(chat_name=name, new_messages=live,
                                           job_name=job_name, company=company)
        except Exception as e:
            self._log("WARN", f"[{name}] 跟进前同步存档失败（不影响本次判断）: {e}")

        kind, info = chat_state(live)
        if kind != "follow":
            self._log("INFO", f"⏭️ 跟进跳过 [{name}]：页面上现在不是我们话说完了（{kind}）")
            return False
        if conversation_rejected(live):
            self._log("INFO", f"⏭️ 跟进跳过 [{name}]：HR 已拒绝，再追是骚扰")
            return False

        text = self._followup_text(item, name, job_name)
        self._reply_engine.begin_event_ts()
        if self._dry_run("本应跟进", f"[{name}] {text}"):
            return False
        self._reply_engine.wait_human_delay()
        if not self._chat_handler.send_text(text):
            self._log("WARN", f"跟进 [{name}] 文字发送失败")
            return False

        mark_followed(self._followup_state, item["key"], now=datetime.now())
        self._save_followup_state()
        self._reply_engine.record_reply()
        self._stats.record_reply(source="followup", action="text")
        self._stats_dict["reply_sent"] += 1
        self._msg_store.append_bot_message(
            name, text, job_name, reply_source="followup", action="text", company=company)
        self._reply_engine._add_record(
            chat_name=name, job_name=job_name,
            received_message=item.get("last_we_said", ""), reply_content=text,
            reply_source="followup", reply_intent="followup",
            reply_reason=f"对方静默 {item['idle_hours']}h，主动追一次把话头接到面试",
        )
        self._emit_reply_event(
            contact_name=name, job_name=job_name,
            message_received=item.get("last_we_said", ""), reply_sent=text,
            ai_model="", intent="followup", status="replied",
        )
        self._log("INFO", f"📞 已跟进 [{name}]（第 {item.get('times', 0) + 1} 次）: {text}")
        return True

    def _full_sync_chats(self):
        """启动后全量同步：侧栏每个会话点开读一遍最新消息，只存档不回复。"""
        chats = self._chat_handler.get_all_chats()
        self._log("INFO", f"启动全量同步：侧栏共 {len(chats)} 个会话，逐个点开读最新消息（不回复）")
        synced = 0
        for chat_info in chats:
            if not self._running:
                break
            # 验证码判据走会话页自己那份探针：_on_captcha_page 是 GreetEngine 的方法，
            # 在这里调用会 AttributeError 把整轮全量同步打断（2026-10-04 实测）
            if self._chat_handler.check_health() == "captcha":
                self._log("WARN", "全量同步途中出现验证页，中止本次同步")
                break
            name = chat_info.get("name", "未知")
            # 置顶标记在这一趟全量登记：置顶行常常一条未读都没有（他一直自己聊），
            # 回复轮根本看不见它；顺便把他取消置顶的会话清回 False
            self._msg_store.set_pinned(
                name, bool(chat_info.get("pinned")),
                company=(chat_info.get("company") or "").strip())
            try:
                if not self._chat_handler.enter_chat(chat_info):
                    self._log("DEBUG", f"[{name}] 切换校验失败，同步跳过")
                    continue
                messages = self._chat_handler.read_all_messages()
                if not messages:
                    continue
                sel = self._chat_handler.read_selected_row() or {}
                company = (sel.get("company") or chat_info.get("company") or "").strip()
                self._msg_store.merge_messages(
                    chat_name=name, new_messages=messages,
                    job_name=self._chat_handler.get_job_name(), company=company)
                synced += 1
                self._reply_engine.wait_human_delay()
                if synced % self.SYNC_REPLY_EVERY == 0:
                    # 同步要点开 270 多个会话（实测 25~50 分钟），一路挡住回复轮就等于
                    # 这段时间没人回话（用户 2026-10-04："现在好多的回复没有回"）
                    self._log("INFO", f"全量同步已读 {synced}/{len(chats)}，先插一轮回复")
                    self._run_reply_round()
                    self._run_followup_round()
            except Exception as e:
                self._log("WARN", f"[{name}] 全量同步失败（继续下一个）: {e}")
        self._log("INFO", f"启动全量同步完成：{synced}/{len(chats)} 个会话已同步，之后只采未读")

    @staticmethod
    def _sidebar_row_for(rows, name: str, company: str):
        """在侧栏行里找到这一条会话，找不到或多解都返回 None（宁可跳过也不猜人）"""
        def same(a, b):
            # BOSS 会自己把公司名截成"招商银行股份有限..."，两边都可能是截断后的
            a = (a or "").strip().rstrip(".")
            b = (b or "").strip().rstrip(".")
            if not a or not b:
                return False
            n = min(len(a), len(b))
            return a[:n] == b[:n]

        hits = [r for r in rows or []
                if r.get("name") == name and (not company or same(r.get("company"), company))]
        return hits[0] if len(hits) == 1 else None

    def _backfill_pending_resumes(self):
        """补发"HR 要过简历、但我们没发出去"的会话。

        回复轮只点未读会话，而这些会话早被点开过，红点没了；更要命的是那句
        索要常常是张卡片，页面上读不出正文，"对方最新消息"于是退成卡片前面那句
        我们已经文字回过的话，was_handled 按那句判"已处理过" —— 索要没被满足，
        却永远不会再被处理（2026-10-02 江女士 | 孤波 就是这么挂着的）。
        所以这里直接下发动作；但下发前先照页面上的实时消息再判两道：
        已经发过的不再发，HR 后来拒绝了的也不再发。
        """
        convs = [c for c in self._msg_store.get_all_chats_detail()
                 if c.get("account_index") == self.account_index]
        not_pinned = [c for c in convs if not is_pinned(c)]
        if len(not_pinned) != len(convs):
            self._log("INFO", f"📌 {len(convs) - len(not_pinned)} 个置顶会话不补发简历"
                              f"（你标了自己聊）")
        convs = not_pinned
        # 认账：这一单真发出去过的不再进，否则每次重启都会给同一个 HR 再发一遍。
        # 但认的是"验过的标记"（带时间戳）或存档里的卡片，不是盘上那批旧假判据
        # 留下的标记——那批挂着，欠简历的会话连会话都进不去（2026-10-08 数到 6 个）。
        pending = [p for p in pending_resume_asks(convs)
                   if not self._resume_delivered(p["chat_name"],
                                                 p.get("job_name", ""),
                                                 p.get("company", ""))]
        if not pending:
            self._log("INFO", "补扫欠简历的会话：没有")
            return
        self._log("INFO", f"补扫欠简历的会话：{len(pending)} 个 —— "
                          + "、".join(p["chat_name"] for p in pending))
        rows = self._chat_handler.get_all_chats()
        for item in pending:
            name = item["chat_name"]
            row = self._sidebar_row_for(rows, name, item.get("company", ""))
            if row is None:
                self._log("INFO", f"⏭️ 跳过 [{name}]：侧栏按 姓名+公司 对不上这一行"
                                  f"（存档公司={item.get('company', '')!r}），不猜人")
                continue
            if is_pinned(row):
                self._log("INFO", f"⏭️ 跳过 [{name}]：这一路你置顶了自己聊，机器不动")
                continue
            if not self._chat_handler.enter_chat(row):
                self._log("WARN", f"⏭️ 跳过 [{name}]：会话切换校验失败")
                continue
            live = self._chat_handler.read_all_messages() or []
            if resume_already_sent(live):
                self._log("INFO", f"[{name}] 页面上已显示简历发送给对方，不再重复发")
                continue
            if conversation_rejected(live):
                self._log("INFO", f"⏭️ 跳过 [{name}]：HR 后来已拒绝，再塞简历是骚扰")
                continue
            job_name = self._chat_handler.get_job_name() or item.get("job_name", "")
            self._log("INFO", f"[{name}] 欠着的索要: {item['ask'][:40]}")
            # 每条一个时间戳：不然这一轮 8 条回复记录全挤在同一秒，
            # 面板上看不出先后，也没法跟 BOSS 端逐条对齐
            self._reply_engine.begin_event_ts()
            self._handle_reply_action("resume", None, {"source": "backfill"},
                                      name, job_name, item["ask"],
                                      item.get("company", ""))
            self._reply_engine.wait_human_delay()

    def _process_single_chat(self, chat_info: dict):
        """处理一个会话：候选来自未读红点，或存档里欠着的回复（chat_info["owed"]）。"""
        name = chat_info.get("name", "未知")
        # 这一轮的回复记录与实时推送共用一个时间戳（见 ReplyEngine.begin_event_ts）
        self._reply_engine.begin_event_ts()
        self._log("INFO", f"--- 正在处理与 [{name}] 的聊天 ---")
        self._log("DEBUG", f"聊天会话信息: {chat_info}")

        # 拒绝与否只能由"这段对话自己"判定，所以放到进入会话、读到页面实时消息之后再判。
        # 这里不再按昵称预扫 message_store：昵称相同的两个人会被并进同一个文件，
        # 提前扫出来的"已拒绝"其实属于另一个 HR，会造成误跳过。

        if not self._chat_handler.enter_chat(chat_info):
            self._log("WARN", f"会话 [{name}] 切换校验失败，本次跳过")
            skip_reason = "会话切换校验失败"
            self._emit_reply_event(
                contact_name=name, job_name="",
                message_received="", reply_sent="",
                ai_model="", intent="",
                status="skipped",
            )
            self._reply_engine._add_record(
                chat_name=name, job_name="",
                received_message="", reply_content=None,
                reply_source="skip", reply_intent="",
                reply_reason="会话切换校验失败，本次跳过",
                is_skipped=True, skip_reason=skip_reason,
            )
            # 关键修复：不再把跳过原因存到 message_store，避免前端聊天界面被
            # [跳过] 系统消息污染。跳过原因仍通过 reply_records 记录。
            return

        # 切换校验通过后，身份以"当前 selected 那一行"为准重新取一次公司名：
        # 侧栏会重排，进来之前快照里的行号可能已经不是同一个人了
        sel = self._chat_handler.read_selected_row() or {}
        chat_company = (sel.get("company") or chat_info.get("company") or "").strip()
        if sel.get("name") and sel.get("name") != name:
            self._log("WARN", f"选中行是 [{sel.get('name')}]，不是目标 [{name}]，本次跳过")
            return

        context_count = self.config.reply.context_message_count
        self._log("DEBUG", f"正在读取所有可见消息（完整聊天记录同步）...")
        messages = self._chat_handler.read_all_messages()
        self._log("DEBUG", f"读取到完整消息数: {len(messages)}")
        if not messages:
            self._log("INFO", "未读取到消息，跳过")
            boss_name = self._chat_handler.get_boss_name()
            job_name = self._chat_handler.get_job_name()
            skip_reason = "未读取到消息"
            self._emit_reply_event(
                contact_name=name, job_name=job_name,
                message_received="", reply_sent="",
                ai_model="", intent="",
                status="skipped",
            )
            self._reply_engine._add_record(
                chat_name=name, job_name=job_name,
                received_message="", reply_content=None,
                reply_source="skip", reply_intent="",
                reply_reason="未读取到消息，跳过",
                is_skipped=True, skip_reason=skip_reason,
            )
            # 关键修复：不再把跳过原因存到 message_store，避免前端聊天界面被
            # [跳过] 系统消息污染。跳过原因仍通过 reply_records 记录。
            return

        # 将完整消息列表合并到 message_store（去重保存完整对话历史）
        # 身份用 姓名+公司：公司就在侧栏那一行上，实测 34 行里 (姓名,公司) 全唯一，
        # 而只用姓名有 4 组重名（两个陈女士分属小智时代科技/艾秒广告）。
        try:
            job_name_for_merge = self._chat_handler.get_job_name()
            merged_total = self._msg_store.merge_messages(
                chat_name=name, new_messages=messages,
                job_name=job_name_for_merge, company=chat_company,
            )
            self._log("DEBUG", f"合并后完整对话消息总数: {merged_total}")
        except Exception as e:
            self._log("WARN", f"合并完整消息失败（不影响后续流程）: {e}")

        latest_other_msg = None
        latest_other_msg_time = ""
        for msg in reversed(messages):
            # 卡片的 .text-content 是空的，正文在 card_text（HR 点"求附件简历"就是张卡片），
            # 但同一套读法也会把平台自己塞的"竞争者PK/已发送"卡片读成 HR 在说话——
            # 那种回一句就是对着空气讲话，过滤规则收在 reply_queue.inbound_body 里
            body = inbound_body(msg)
            if body:
                latest_other_msg = body
                latest_other_msg_time = msg.get("time", "")
                break

        if not latest_other_msg:
            self._log("INFO", "最新消息是自己发的，无需回复")
            boss_name = self._chat_handler.get_boss_name()
            job_name = self._chat_handler.get_job_name()
            skip_reason = "最新消息是自己发的，无需回复"
            self._emit_reply_event(
                contact_name=name, job_name=job_name,
                message_received="", reply_sent="",
                ai_model="", intent="",
                status="skipped",
            )
            self._reply_engine._add_record(
                chat_name=name, job_name=job_name,
                received_message="", reply_content=None,
                reply_source="skip", reply_intent="",
                reply_reason=skip_reason,
                is_skipped=True, skip_reason=skip_reason,
            )
            # 关键修复：不再把跳过原因存到 message_store，避免前端聊天界面被
            # [跳过] 系统消息污染。跳过原因仍通过 reply_records 记录。
            return

        self._log("INFO", f"对方最新消息: {latest_other_msg[:80]}")

        # ── 防骚扰判定：依据只有当前这一段对话（同一 HR、同一岗位，页面实时读到什么算什么）──
        # 只有当 HR 的拒绝是这段对话里对方最后一条消息、而且我们已经回过一句时，才跳过。
        # 还没回过就不跳过 —— 交给回复引擎礼貌收尾一次，那是人的正常反应。
        if conversation_rejected(messages) and messages[-1].get("is_mine"):
            job_name = self._chat_handler.get_job_name()
            self._log("INFO",
                      f"⏭️ [{name} | {job_name}] 该会话HR已拒绝且已回复过，跳过避免骚扰")
            skip_reason = "该会话HR已拒绝且已礼貌回复，不再骚扰"
            self._emit_reply_event(
                contact_name=name, job_name=job_name,
                message_received=latest_other_msg, reply_sent="",
                ai_model="", intent="rejection",
                status="skipped",
            )
            self._reply_engine._add_record(
                chat_name=name, job_name=job_name,
                received_message=latest_other_msg, reply_content=None,
                reply_source="skip", reply_intent="rejection",
                reply_reason=skip_reason,
                is_skipped=True, skip_reason=skip_reason,
            )
            return

        # 完整消息已通过 merge_messages 合并保存到 message_store，
        # 不再单独调用 append_hr_message 保存最新一条 HR 消息（避免重复）

        # 存档补出来的（owed）不看这条：was_handled 只说明"这条消息我们看过"，
        # 而欠回复的判据是"HR 的话排在最后、我们没接"。拿前者当闸门，当年那次
        # "看过但没回"就永远翻不了账（2026-10-04 实测：50 分钟里挡掉 20 个，只回了 8 个）
        if not chat_info.get("owed") and self._state_store.was_handled(name, latest_other_msg):
            self._log("INFO", "该消息已处理过，跳过（防重复回复）")
            self._stats.record_skip()
            boss_name = self._chat_handler.get_boss_name()
            job_name = self._chat_handler.get_job_name()
            skip_reason = "该消息已处理过，防重复回复"
            self._emit_reply_event(
                contact_name=name, job_name=job_name,
                message_received=latest_other_msg, reply_sent="",
                ai_model="", intent="",
                status="skipped",
            )
            self._reply_engine._add_record(
                chat_name=name, job_name=job_name,
                received_message=latest_other_msg, reply_content=None,
                reply_source="skip", reply_intent="",
                reply_reason=skip_reason,
                is_skipped=True, skip_reason=skip_reason,
            )
            # 关键修复：不再把跳过原因存到 message_store，避免前端聊天界面被
            # [跳过] 系统消息污染。跳过原因仍通过 reply_records 记录。
            return

        boss_name = self._chat_handler.get_boss_name()
        job_name = self._chat_handler.get_job_name()
        self._log("DEBUG", f"聊天对象: boss_name={boss_name}, job_name={job_name}")

        # 从 message_store 获取完整对话历史（所有HR消息+所有我的回复+时间顺序），
        # 而不只传页面读取的最新消息，让AI能看到完整上下文
        try:
            # 会话按 姓名+公司 存，同名的两个 HR 已经是两个文件，
            # 不需要再靠"岗位名对不对得上"去怀疑这份历史是不是别人的
            full_dialog = self._msg_store.get_full_dialog(
                name, company=chat_company)
            if full_dialog:
                messages_for_reply = full_dialog
                self._log("DEBUG", f"使用完整对话历史: {len(full_dialog)} 条消息")
            else:
                messages_for_reply = messages
                self._log("DEBUG", f"回退使用页面消息: {len(messages)} 条")
        except Exception as e:
            self._log("WARN", f"获取完整对话历史失败，回退使用页面消息: {e}")
            messages_for_reply = messages

        action, content, meta = self._reply_engine.get_reply(
            messages_for_reply, boss_name, job_name, chat_name=name
        )
        self._log("DEBUG", f"回复引擎决策: action={action}, meta={meta}")

        # 现场/线下的面试邀请是去点「拒绝」的，下面两件事都不能再照原样办：
        # ①计入面试数——那是一条刚被我拒掉的单，界面上多一个"面试"就是虚报；
        # ②按"重要消息"挂成人工接管——用户要的是它自己拒掉，不是一停就等他点恢复。
        if action != "reject_interview":
            # 面试数：HR 这条消息的意图是邀约/敲定面试，同一个会话只算一次
            if meta.get("intent") in ("invite_interview", "ask_interview"):
                if self._metrics.add_interview(self.account_index, name):
                    self._log("SUCCESS", f"🎯 新增面试会话：[{name}]（{job_name or '未知岗位'}）")

            # 重要事件检测
            if self._notifier.notify_if_important(
                latest_other_msg, chat_name=name, job_name=job_name,
                intent=meta.get("intent", "")
            ):
                self._stats.record_important()
                self._stats_dict["important_events"] += 1
                if self.config.reply.pause_on_important:
                    self._state_store.pause(
                        reason=f"收到重要消息: {latest_other_msg[:50]}",
                        chat_name=name,
                    )
                    self._reply_paused = True
                    self._notifier.send_notification(
                        title="机器人已暂停，转人工模式",
                        content="检测到重要消息，自动回复已暂停。在 Web 界面点击「恢复」可恢复。",
                        level="info",
                    )
                    self._log("WARN", "已切换为人工接管模式，自动回复暂停")

        return self._handle_reply_action(action, content, meta, name, job_name,
                                         latest_other_msg, chat_company,
                                         hr_recent=" ".join(
                                             inbound_body(m) for m in
                                             [x for x in messages if inbound_body(x)][-4:]))

    def _send_offline_interview_decline(self, name, job_name, chat_company,
                                        latest_other_msg, meta, verdict) -> bool:
        """点不到「拒绝」按钮时，退回发一句文字拒绝。

        单独一个方法是为了让演练闸门贴着发送点：这一路也是真的往 HR 那边发消息，
        闸门写在调用它的分支顶上，隔着一整段拒绝逻辑看不出来。
        """
        if self._dry_run("本应发文字拒绝现场面试邀请", f"[{name}] {OFFLINE_INTERVIEW_DECLINE}"):
            return False
        if not self._chat_handler.send_text(OFFLINE_INTERVIEW_DECLINE):
            # 和文字回复那条路同一个约定：记一条跳过、算处理过，
            # 返回 False 会让调用方以为没动过它，下一轮又进来发一遍
            self._stats.record_reply(source="policy", action="skip")
            reason = f"拒绝话术没发出去（{verdict}）"
            self._reply_engine._add_record(
                chat_name=name, job_name=job_name,
                received_message=latest_other_msg, reply_content=None,
                reply_source="skip", reply_intent=meta.get("intent", ""),
                reply_reason=reason, is_skipped=True, skip_reason=reason)
            self._emit_reply_event(
                contact_name=name, job_name=job_name,
                message_received=latest_other_msg, reply_sent="",
                ai_model="", intent=meta.get("intent", ""), status="error")
            return True
        self._stats.record_reply(source="policy", action="text")
        self._reply_engine.record_reply()
        self._stats_dict["reply_sent"] += 1
        self._msg_store.append_bot_message(
            name, OFFLINE_INTERVIEW_DECLINE, job_name,
            reply_source="policy", action="text", company=chat_company)
        self._reply_engine._add_record(
            chat_name=name, job_name=job_name,
            received_message=latest_other_msg, reply_content=OFFLINE_INTERVIEW_DECLINE,
            reply_source="policy", reply_intent=meta.get("intent", ""),
            reply_reason="现场面试邀请，页面上没有可点的「拒绝」，改发文字拒绝",
        )
        self._emit_reply_event(
            contact_name=name, job_name=job_name,
            message_received=latest_other_msg, reply_sent=OFFLINE_INTERVIEW_DECLINE,
            ai_model="", intent=meta.get("intent", ""), status="replied")
        return True

    def _decline_contact_card(self, name, job_name, chat_company,
                              latest_other_msg, meta, hit) -> bool:
        """命中岗位类型的那张交换卡片：点「拒绝」，绝不点「同意」。

        只点按钮、不再追一句话：卡片本身已经把"我不同意"报给平台了，紧接着再发
        一条文字，两个号各发一遍就成了追着人说话。点不到（已经处理过）只记跳过。
        """
        if self._dry_run("本应拒绝交换联系方式卡片",
                         f"[{name}]（{job_name or '未知岗位'}）命中「{hit}」"):
            return False
        self._reply_engine.wait_human_delay()
        if self._chat_handler.decline_contact_exchange():
            note = CONTACT_DECLINED_MARK
            self._stats.record_reply(source=meta.get("source", "intent"),
                                     action="reject_contact")
            self._msg_store.append_bot_message(
                name, note, job_name,
                reply_source="reject_contact", action="reject_contact",
                company=chat_company,
            )
            self._reply_engine._add_record(
                chat_name=name, job_name=job_name,
                received_message=latest_other_msg, reply_content=note,
                reply_source="reject_contact", reply_intent=meta.get("intent", ""),
                reply_reason=f"岗位类型命中「{hit}」，交换联系方式的卡片已点拒绝",
            )
            self._emit_reply_event(
                contact_name=name, job_name=job_name,
                message_received=latest_other_msg, reply_sent=note,
                ai_model="", intent=meta.get("intent", ""),
                status="replied",
            )
            self._log("INFO", f"⛔ 命中岗位类型「{hit}」，已拒绝交换联系方式")
        else:
            reason = f"卡片上没找到可点的「拒绝」（可能已经处理过），没有真的拒绝"
            self._log("WARN", f"⏭️ [{name}] {reason}")
            self._reply_engine._add_record(
                chat_name=name, job_name=job_name,
                received_message=latest_other_msg, reply_content=None,
                reply_source="skip", reply_intent=meta.get("intent", ""),
                reply_reason=reason, is_skipped=True, skip_reason=reason,
            )
            self._emit_reply_event(
                contact_name=name, job_name=job_name,
                message_received=latest_other_msg, reply_sent="",
                ai_model="", intent=meta.get("intent", ""),
                status="skipped",
            )
        return True

    def _resume_delivered(self, name, job_name="", company="") -> bool:
        """这一单的简历到底发没发：先看存档证据，再看状态位是什么时候落的。

        状态位过去是假判据写的（2026-10-08 按号数：标了已发 82 个，同一账号存档里
        真有卡片的 76 个，剩 6 个查无卡片），照它去重欠的就补不回来。但也不能
        反过来只看存档——刚发成功、卡片还没进存档那几秒，只看存档会对同一个 HR
        发两遍。所以按来历分：带时间戳的是"点确认前数一遍、点完卡片多出一条"
        验过的，信它；没时间戳的旧标记只配按证据重判，证据不在就撤回。
        """
        msgs = []
        try:
            msgs = self._msg_store.get_messages(
                name, job_name=job_name, company=company) or []
        except Exception as e:
            self._log("DEBUG", f"读会话存档失败，简历去重回落到状态位: {e}")
        if resume_already_sent(msgs):
            return True
        if not self._state_store.resume_sent(name):
            return False
        if self._state_store.resume_sent_at(name):
            return True
        self._state_store.clear_resume_sent(name)
        self._log("WARN", f"↩️ [{name}] 状态里写着简历已发（旧判据落的，没留时间），"
                          f"但存档里没有简历卡片，按没发过处理")
        return False

    def _handle_reply_action(self, action, content, meta, name, job_name,
                             latest_other_msg, chat_company="",
                             hr_recent: str = "") -> bool:
        """执行本次回复并落回复记录。

        返回 False = 演练模式提前收工，这条消息没有真被回复，不能标成已处理。
        发送成功与发送失败两条路都要落记录：以前只有 socket 实时推送，刷新一次
        界面那条回复就没了，回复记录和 BOSS 端消息列表对不上。
        """
        # 简历去重降级：判据是"存档里有没有 BOSS 的简历卡片"，不是那个会自己骗自己的标记
        if action == "resume" and self.config.reply.resume_send_once and \
           self._resume_delivered(name, job_name, chat_company):
            from boss_bot.config import RESUME_DUPLICATE_REPLY
            self._log("INFO", "该会话已发送过简历，降级为文字提醒")
            action, content = "text", RESUME_DUPLICATE_REPLY
            meta["source"] = "intent"

        # 执行回复
        if action == "resume":
            if self._dry_run("本应发送简历", f"[{name}]（{job_name or '未知岗位'}）"):
                return False
            self._reply_engine.wait_human_delay()
            if self._chat_handler.send_resume():
                self._state_store.mark_resume_sent(name)
                self._stats.record_reply(source=meta.get("source", "rule"), action="resume")
                self._stats_dict["resume_sent"] += 1
                self._metrics.bump(self.account_index, "resume_sent")
                self._msg_store.append_bot_message(
                    name, "[简历已发送]", job_name,
                    reply_source=meta.get("source", ""), action="resume",
                    company=chat_company,
                )
                self._log("INFO", "已发送简历")
                # 落到 reply_records：接收简历数靠这条统计，重启才不会丢
                self._reply_engine._add_record(
                    chat_name=name, job_name=job_name,
                    received_message=latest_other_msg,
                    reply_content="[简历已发送]",
                    reply_source=meta.get("source", ""),
                    reply_intent=meta.get("intent", ""),
                    reply_reason="HR索要简历，已发送附件简历",
                )
                self._emit_reply_event(
                    contact_name=name, job_name=job_name,
                    message_received=latest_other_msg, reply_sent="[简历已发送]",
                    ai_model=self._reply_engine._last_ai_model,
                    intent=meta.get("intent", ""),
                    status="replied",
                )
            else:
                from boss_bot.config import RESUME_UNAVAILABLE_REPLY
                self._log("WARN", "简历发送失败，降级为文字告知")
                if self._dry_run("本应降级为文字告知", f"[{name}]"):
                    return False
                self._reply_engine.wait_human_delay()
                self._chat_handler.send_text(RESUME_UNAVAILABLE_REPLY)
                self._msg_store.append_bot_message(
                    name, RESUME_UNAVAILABLE_REPLY, job_name,
                    reply_source=meta.get("source", ""), action="text_fallback",
                    company=chat_company,
                )
                self._reply_engine._add_record(
                    chat_name=name, job_name=job_name,
                    received_message=latest_other_msg,
                    reply_content=RESUME_UNAVAILABLE_REPLY,
                    reply_source=meta.get("source", ""),
                    reply_intent=meta.get("intent", ""),
                    reply_reason="附件简历发送失败，已回复降级话术告知",
                )
                self._notifier.send_notification(
                    title="简历发送失败",
                    content=f"[{name}]（{job_name or '未知岗位'}）请求简历但发送失败，已回复降级话术。",
                    level="warning",
                )
                self._stats.record_reply(source=meta.get("source", "rule"), action="skip")
                self._emit_reply_event(
                    contact_name=name, job_name=job_name,
                    message_received=latest_other_msg, reply_sent=RESUME_UNAVAILABLE_REPLY,
                    ai_model=self._reply_engine._last_ai_model,
                    intent=meta.get("intent", ""),
                    status="replied",
                )

        elif action == "contact":
            # HR 点的是平台那张"交换微信/电话号码，您是否同意"卡片：能点掉就别回话，
            # 回一段"还是在平台上聊吧"等于把送上门的联系方式推回去
            if not self.config.reply.accept_contact_exchange:
                reason = "交换联系方式的开关是关的，这张卡片留给人工处理"
                self._log("INFO", f"⏭️ [{name}] {reason}")
                self._reply_engine._add_record(
                    chat_name=name, job_name=job_name,
                    received_message=latest_other_msg, reply_content=None,
                    reply_source="skip", reply_intent=meta.get("intent", ""),
                    reply_reason=reason, is_skipped=True, skip_reason=reason,
                )
                return True
            # 普工/主播/快递/保洁这一类：号码交出去就收不回来，卡片只准点「拒绝」。
            # 这些会话大多不是我们投的，是 HR 主动找上门的，所以闸门只能设在发送点，
            # 指望投递侧拦不住（2026-10-07 盘上 6 张这一类的卡片全被点了同意）。
            # 标题两道表都过（岗位类型词 + 否决词），消息正文只过否决词那一遍：
            # 「【白班坐岗】28/H包吃住」这种类型就写在标题里，光过岗位类型词会漏。
            # 闸门排在演练判断之前，演练日志才不会报反。
            hit = veto_hit_anywhere(self.config.ai.title_veto_keywords,
                                    self.config.ai.custom_filter_keywords,
                                    title=job_name,
                                    text=hr_recent or latest_other_msg)
            if hit:
                return self._decline_contact_card(name, job_name, chat_company,
                                                  latest_other_msg, meta, hit)
            if self._dry_run("本应同意交换联系方式", f"[{name}]（{job_name or '未知岗位'}）"):
                return False
            self._reply_engine.wait_human_delay()
            if self._chat_handler.accept_contact_exchange():
                note = "[已同意交换联系方式]"
                self._stats.record_reply(source=meta.get("source", "intent"),
                                         action="contact")
                self._msg_store.append_bot_message(
                    name, note, job_name,
                    reply_source=meta.get("source", ""), action="contact",
                    company=chat_company,
                )
                self._reply_engine._add_record(
                    chat_name=name, job_name=job_name,
                    received_message=latest_other_msg, reply_content=note,
                    reply_source="contact", reply_intent=meta.get("intent", ""),
                    reply_reason="HR 发来交换微信/电话的卡片，已点同意",
                )
                self._emit_reply_event(
                    contact_name=name, job_name=job_name,
                    message_received=latest_other_msg, reply_sent=note,
                    ai_model="", intent=meta.get("intent", ""),
                    status="replied",
                )
                self._log("INFO", "已同意交换联系方式")
            else:
                # 卡片上没有可点的「同意」= 这张已经被处理过或不是那张卡片。
                # 记一条留痕，别再拿文字去追一遍，两个号都回一次就是骚扰
                reason = "卡片上没找到可点的「同意」（可能已经处理过），没有真的交换"
                self._log("WARN", f"⏭️ [{name}] {reason}")
                self._reply_engine._add_record(
                    chat_name=name, job_name=job_name,
                    received_message=latest_other_msg, reply_content=None,
                    reply_source="skip", reply_intent=meta.get("intent", ""),
                    reply_reason=reason, is_skipped=True, skip_reason=reason,
                )
                self._emit_reply_event(
                    contact_name=name, job_name=job_name,
                    message_received=latest_other_msg, reply_sent="",
                    ai_model="", intent=meta.get("intent", ""),
                    status="skipped",
                )
            return True

        elif action == "reject_interview":
            # 现场/线下面试的邀请：用户只要线上（2026-10-06 原话"他发面试邀请，
            # 你直接拒绝就行了"）。优先点平台上那张邀请的「拒绝」——那才是真的把
            # 面试撤掉；按钮找不到（邀请过期/面板没展开）才退回发一句拒绝话术。
            if self._dry_run("本应拒绝现场面试邀请", f"[{name}]（{job_name or '未知岗位'}）"):
                return False
            self._reply_engine.wait_human_delay()
            verdict = self._chat_handler.reject_interview_invite(expect=chat_company)
            if verdict == "no-btn" and self._chat_handler.open_interview_invite():
                # 展开是异步的，实测 2~5 秒才渲染出 拒绝/接受，固定 sleep 会漏单
                verdict = self._chat_handler.reject_interview_invite(
                    wait_sec=8, expect=chat_company)
            if verdict in ("clicked", "clicked-confirmed"):
                note = "[已拒绝现场面试邀请]"
                self._stats.record_reply(source="policy", action="reject_interview")
                self._msg_store.append_bot_message(
                    name, note, job_name, reply_source="policy", action="text",
                    company=chat_company)
                self._reply_engine._add_record(
                    chat_name=name, job_name=job_name,
                    received_message=latest_other_msg, reply_content=note,
                    reply_source="policy", reply_intent=meta.get("intent", ""),
                    reply_reason="邀请写的是现场/线下面试，只找线上的，已点平台上的「拒绝」",
                )
                self._emit_reply_event(
                    contact_name=name, job_name=job_name,
                    message_received=latest_other_msg, reply_sent=note,
                    ai_model="", intent=meta.get("intent", ""), status="replied")
                self._log("INFO", f"已拒绝现场面试邀请 [{name}]（{verdict}）")
                return True

            # 点不到按钮：至少把话说明白，别让 HR 一直等一个不会到的人
            self._log("WARN", f"️ [{name}] 面试邀请没找到可点的「拒绝」（{verdict}），改发文字拒绝")
            return self._send_offline_interview_decline(
                name, job_name, chat_company, latest_other_msg, meta, verdict)

        elif action == "text" and content:
            if self._dry_run("本应回复", f"[{name}] {content}"):
                return False
            self._reply_engine.wait_human_delay()
            if self._chat_handler.send_text(content):
                self._stats.record_reply(source=meta.get("source", "rule"), action="text")
                # 只有真发出去的一句才占每小时额度：跳过、人工接管、侧栏对不上行
                # 都点在函数结尾计数的话，一小时点过 30 个没回成的会话
                # 就把后面的真回复全挡在门外了
                self._reply_engine.record_reply()
                self._stats_dict["reply_sent"] += 1
                self._msg_store.append_bot_message(
                    name, content, job_name,
                    reply_source=meta.get("source", ""), action="text",
                    company=chat_company,
                )
                self._log("INFO", f"已回复: {content[:30]}...")
                # 发出去的那句要落 reply_records：只有 socket 推送的话，
                # 刷新一次界面这条回复就没了，和 BOSS 端消息列表对不上
                self._reply_engine._add_record(
                    chat_name=name, job_name=job_name,
                    received_message=latest_other_msg, reply_content=content,
                    reply_source=meta.get("source", ""),
                    reply_intent=meta.get("intent", ""),
                    reply_reason=f"按 {meta.get('source') or '默认'} 来源生成的回复已发送",
                    ai_model=self._reply_engine._last_ai_model,
                )
                self._emit_reply_event(
                    contact_name=name, job_name=job_name,
                    message_received=latest_other_msg, reply_sent=content,
                    ai_model=self._reply_engine._last_ai_model,
                    intent=meta.get("intent", ""),
                    status="replied",
                )
            else:
                self._stats.record_reply(source=meta.get("source", "rule"), action="skip")
                self._log("WARN", "发送文字失败")
                skip_reason = "发送文字失败"
                self._reply_engine._add_record(
                    chat_name=name, job_name=job_name,
                    received_message=latest_other_msg, reply_content=None,
                    reply_source="skip", reply_intent=meta.get("intent", ""),
                    reply_reason=skip_reason,
                    is_skipped=True, skip_reason=skip_reason,
                )
                self._emit_reply_event(
                    contact_name=name, job_name=job_name,
                    message_received=latest_other_msg, reply_sent=content or "",
                    ai_model=self._reply_engine._last_ai_model,
                    intent=meta.get("intent", ""),
                    status="error",
                )
                # 关键修复：不再把发送失败原因存到 message_store，避免前端聊天界面被
                # [跳过] 系统消息污染。失败原因仍通过 reply_records 记录。

        else:
            self._log("INFO", "无合适回复，跳过")
            self._stats.record_reply(source=meta.get("source", "default"), action="skip")
            self._stats_dict["reply_skipped"] += 1
            self._emit_reply_event(
                contact_name=name, job_name=job_name,
                message_received=latest_other_msg, reply_sent="",
                ai_model=self._reply_engine._last_ai_model,
                intent=meta.get("intent", ""),
                status="skipped",
            )
            # 记录跳过原因，便于后续分析
            skip_reason = "无合适回复，跳过"
            if action == "none":
                if meta.get("source") == "default":
                    skip_reason = "规则/意图/AI均未命中，跳过回复"
                elif meta.get("source") == "":
                    skip_reason = "未匹配任何回复来源，跳过回复"
                else:
                    skip_reason = f"action=none source={meta.get('source', '')}，跳过回复"
            elif not content:
                skip_reason = "回复内容为空，跳过"
            self._reply_engine._add_record(
                chat_name=name, job_name=job_name,
                received_message=latest_other_msg, reply_content=None,
                reply_source="skip", reply_intent=meta.get("intent", ""),
                reply_reason=skip_reason,
                is_skipped=True, skip_reason=skip_reason,
            )
            # 关键修复：不再把跳过原因存到 message_store，避免前端聊天界面被
            # [跳过] 系统消息污染。跳过原因仍通过 reply_records 记录。

        self._state_store.mark_handled(name, latest_other_msg, action or "none")
        return True

    # ─────────────────────────────────────────────
    # 配置热重载
    # ─────────────────────────────────────────────

    def _hot_reload_config(self):
        """热重载所有运行时可变配置。

        从配置文件重新加载，并同步到各运行引擎和主循环属性。
        覆盖范围（只写引擎真正读取的属性，避免"改了没生效"）：
          1. 打招呼引擎的账号级参数（话术/简历图片/间隔/频率限制/重试次数）
          2. AI 配置（打招呼侧受 ai.enabled 控制，回复侧始终启用）
          3. 回复侧：延迟区间、每小时回复上限、max_tokens、失败兜底、限流等待
          4. 打招呼、回复总开关
          5. 人工接管暂停的自动恢复
        整个过程只做日志与赋值，任何异常都不允许打断调用线程。
        """
        try:
            # 读盘之后必须再套本账号的覆盖：这里以前直接 self.config = load()，
            # 于是运行 10 秒后账号级阈值就被全局基准冲掉，表现为"独立配置改了没生效"
            self.config = UnifiedConfig.load().apply_account(self.account_index)

            # 0. 引擎与主循环共用同一份新 config，并按新 config 重读
            #    频率限制/话术/简历图片/账号参数（否则前端改了要重启才生效）
            if self._greet_engine:
                self._greet_engine.config = self.config
                self._greet_engine.reload_runtime_settings()

            # 1. AI 配置热重载
            # 注意：ai.enabled 仅控制打招呼的AI岗位解析，不控制自动回复AI
            # 自动回复AI始终开启（只要有API key），确保句句有回应
            _ai_providers_as_dict = [
                {
                    "name": p.name,
                    "api_key": p.api_key,
                    "api_base": p.api_base,
                    "model": p.model,
                    "timeout": p.timeout,
                }
                for p in self.config.ai.providers
            ]

            if self._greet_engine:
                self._greet_engine._ai_enabled = self.config.ai.enabled
                self._greet_engine._ai_providers = _ai_providers_as_dict
                # 只有 providers 列表真的变了才重建分析器：分析器里带着"坏接口冷却表"，
                # 无条件置 None 会让每个岗位都把已确认失败的接口再踩一遍
                fp = json.dumps([(p["name"], p["api_key"][-6:], p["api_base"], p["model"])
                                 for p in _ai_providers_as_dict], ensure_ascii=False)
                if getattr(self, "_ai_providers_fp", None) != fp:
                    self._ai_providers_fp = fp
                    self._greet_engine._ai_analyzer = None
                self._greet_engine._ai_custom_filter_keywords = self.config.ai.custom_filter_keywords
                self._greet_engine._ai_title_veto_keywords = list(
                    self.config.ai.title_veto_keywords)
                self._greet_engine._ai_custom_scoring_prompt = self.config.ai.custom_scoring_prompt

            if self._reply_engine:
                # 回复引擎AI始终开启，不受 ai.enabled 开关控制
                self._reply_engine._ai_providers = _ai_providers_as_dict
                self._reply_engine._min_delay = self.config.reply.min_delay
                self._reply_engine._max_delay = self.config.reply.max_delay
                # 这几项以前只在进程启动时读一次快照，前端改了要重启才生效
                self._reply_engine._max_replies_per_hour = self.config.reply.max_replies_per_hour
                self._reply_engine._ai_max_tokens = self.config.ai.max_tokens
                self._reply_engine._ai_fail_action = self.config.ai.fail_action
                self._reply_engine._ai_rate_limit_wait = self.config.ai.rate_limit_wait
                # 岗位类型词/否决词也要跟着热重载：面板改了词框，运行中的回复轮
                # 下一句就得按新表判，不能等重启
                self._reply_engine._title_veto_keywords = list(
                    self.config.ai.title_veto_keywords)
                self._reply_engine._body_veto_keywords = list(
                    self.config.ai.custom_filter_keywords)
            if self._self_evolve:
                self._self_evolve.enabled = self.config.self_evolve_enabled

            # 2. 打招呼 / 回复总开关
            self._greet_enabled = self.config.greet.enabled
            self._reply_enabled = self.config.reply.enabled

            # 3. 人工接管状态自动恢复
            self._maybe_auto_resume_reply()
        except Exception as e:
            self._log("WARN", f"热重载配置失败，保留旧配置: {e}")

    def _maybe_auto_resume_greet(self):
        """机器自己判出来的暂停要能自己醒；人工点的永远挂着。

        两条路：
        - 空搜索/验证码连击：设了 _greet_auto_resume_at，到点醒过来再试一轮；
        - 投满每日上限：BOSS 的额度按天回，跨过零点才醒（_greet_paused_by_cap）。
        以前只有第二条，第一条就是"悄悄停机"的来源：07:09 暂停，中午还没投。
        """
        if not self._greet_paused:
            return
        if self._greet_auto_resume_at and time.time() >= self._greet_auto_resume_at:
            self._greet_auto_resume_at = 0.0
            self._greet_paused = False
            self._log("SUCCESS", "空搜索/风控暂停到点，打招呼自动恢复，再试一轮")
            return
        if not self._greet_paused_by_cap:
            return
        if self._greet_cap_paused_on == date.today().isoformat():
            return  # 还是同一天，不必每 10 秒翻一遍记录文件
        try:
            eng = self._greet_engine
            max_per_day = getattr(eng, "_max_per_day", 0) if eng else 0
            if not max_per_day:
                return
            today_applied = sum(
                1 for r in eng._greet_store.filter(date=date.today().isoformat())
                if r.is_greeted and r.account_index == self.account_index)
            if today_applied < max_per_day:
                self._greet_paused = False
                self._greet_paused_by_cap = False
                self._log("SUCCESS",
                          f"新的一天，今日已投 {today_applied}/{max_per_day}，打招呼自动恢复")
        except Exception as e:
            self._log("DEBUG", f"打招呼自动恢复检查失败（不影响流程）: {e}")

    def _maybe_auto_resume_reply(self):
        """重要事件驱动的暂停，在事件不再匹配关键词时自动恢复。

        人工接管（前端「暂停回复」按钮）产生的暂停永不自动解除，
        必须由用户点「恢复回复」——否则接管到一半机器人会把消息发出去。
        """
        if self._state_store is None:
            return
        if not (self._reply_paused or self._state_store.is_paused()):
            return

        pause_info = self._state_store.pause_info() if hasattr(self._state_store, "pause_info") else {}
        pause_reason = str(pause_info.get("reason", ""))
        if "人工接管" in pause_reason or "手动" in pause_reason:
            return

        # 事件暂停要会自己过期。IMPORTANCE_KEYWORDS 命中的是暂停原因里那句 HR 原文，
        # 而那句话永远含关键词——所以"不再匹配"这条路几乎不会走到，一条推销消息
        # 就能把整个号的回复轮一直挂着（实测挂着之后 9 个真提问没人点）。
        minutes = int(getattr(self.config.reply, "pause_auto_resume_minutes", 0) or 0)
        at = str(pause_info.get("at") or "")
        if minutes > 0 and at:
            try:
                paused_at = datetime.strptime(at[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                paused_at = None
            if paused_at and datetime.now() - paused_at >= timedelta(minutes=minutes):
                self._reply_paused = False
                self._state_store.resume()
                self._log("INFO", f"人工接管已超 {minutes} 分钟，自动恢复回复"
                                  f"（当初触发的那条：{pause_reason[:40]}）")
                return

        # 检查暂停原因中的消息是否还匹配当前的重要关键词
        from boss_bot.config import IMPORTANCE_KEYWORDS
        reason_text = pause_reason.lower()
        still_important = any(kw.lower() in reason_text for kw in IMPORTANCE_KEYWORDS)
        if not still_important:
            self._reply_paused = False
            self._state_store.resume()
            self._log("INFO", "热重载检测到暂停原因已不再匹配重要关键词，自动恢复回复")

    # ─────────────────────────────────────────────
    # 健康检查与错误恢复
    # ─────────────────────────────────────────────

    def _is_connection_error(self, e: Exception) -> bool:
        """判断异常是否为浏览器连接断开错误。"""
        if e is None:
            return False
        # PageDisconnectedError
        if PageDisconnectedError is not None and isinstance(e, PageDisconnectedError):
            return True
        # 字符串匹配（兼容不同版本）
        err_str = str(e).lower()
        keywords = ["断开", "disconnected", "connection", "target closed", "session deleted"]
        return any(k in err_str for k in keywords)

    def live_login_state(self):
        """在**已经开着**的那个浏览器里实测登录态；没在跑就返回 None。

        为什么单独给界面用：界面原本只能读 Cookie 文件，"检测登录状态"其实是
        "文件里那几条没过期"。也不让界面去调 /api/accounts/<idx>/check_cookie ——
        那条会另起一个不带端口与 profile 的浏览器，跟正在跑的会话抢用户目录并
        quit() 掉它，检测一次等于把登录态踢一次（本项目红线）。
        这里只读：等 URL 稳定 + 看浏览器里的登录项，绝不导航。
        """
        instance = (self.browser_manager.get_instance()
                    if self.browser_manager is not None else None)
        if instance is None:
            return None
        try:
            return self._login_state_read(instance)
        except Exception as e:
            self._log("DEBUG", f"实测登录态读不到: {e}")
            return None

    def _check_health(self, side: str = "reply") -> str:
        """健康检查：检测登录态和验证码拦截。side 决定读哪张标签页。

        打招呼侧必须读搜索/详情那一页 —— 人机验证就弹在那儿。以前只要聊天处理器
        建好了就永远读会话页，验证页在另一张标签上等着，闸门等到超时也等不到
        "已恢复"，用户看到的还是"卡在那张验证图不动"。
        """
        instance = (self.browser_manager.get_search_page() if side == "greet"
                    else self.browser_manager.get_instance())
        if instance is None:
            return "browser_disconnected"

        try:
            _ = instance.url
        except Exception:
            return "browser_disconnected"

        if side != "greet" and self._chat_handler is not None:
            try:
                health = self._chat_handler.check_health()
                self._health_why = "会话页检查器判定"
                return health if health in ("ok", "need_login", "captcha") else "unknown"
            except Exception as e:
                self._log("DEBUG", f"健康检查异常: {e}")
                return "unknown"

        # 回复引擎还没建时也要能认出验证码页：以前这里直接回 "ok"，
        # 于是风控页被当成健康，接着掉进打招呼侧几分钟不可中断的重试循环
        try:
            from boss_bot.page_handler import (CAPTCHA_PROBE_JS, classify_health,
                                               captcha_evidence)
            url = instance.url or ""
            # as_expr 必须带：这份 JS 是 IIFE 表达式，不带的话 DrissionPage 把它
            # 当函数体包一层，没有 return → 恒为 undefined → 页面证据一条拿不到，
            # 于是只能靠 URL 定罪（2026-09-30 就是这条把打招呼停了 3 次）
            probe = instance.run_js(CAPTCHA_PROBE_JS, as_expr=True)
            verdict = classify_health(url, probe)
            _is_c, why = captcha_evidence(probe)
            self._health_why = why or "无挑战证据"
            # 判据写进日志：以后再说"页面没有验证码"，能当场看出是哪条判据响的
            self._log("DEBUG" if verdict != "captcha" else "WARN",
                      f"页面判据={verdict}（{self._health_why}）URL={url[:90]}")
            return verdict
        except Exception as e:
            self._log("DEBUG", f"健康检查（无聊天处理器）失败: {e}")
            return "unknown"

    def _captcha_gate(self, pause_field: str = "greet") -> bool:
        """碰到人机验证：提示人工处理，最多等 CAPTCHA_WAIT_SECONDS 秒。

        True = 页面已恢复可以继续；False = 超时，调用方跳过当前任务继续跑。
        旧逻辑是一遇验证码就 `_greet_paused = True` 永久暂停，而自动恢复只认
        "每日上限"那个标记，验证码造成的暂停永远解不开——用户看到的就是
        "程序卡在那张验证图不动"。连续 CAPTCHA_STRIKES_TO_PAUSE 次没人应答
        才真暂停，止损还是要有的，但不能第一次就停。
        pause_field 决定耗尽重试后停哪一侧（打招呼 / 回复）。
        """
        self._emit_wind(
            f"BOSS 触发人机验证（{self._health_why or '页面判据未取到'}），"
            f"请在浏览器窗口里手动完成；"
            f"{CAPTCHA_WAIT_SECONDS} 秒内没操作会自动跳过当前任务", "captcha")
        deadline = time.time() + CAPTCHA_WAIT_SECONDS
        while time.time() < deadline:
            if self._stop_event.is_set() or not self._running:
                return False
            if self._check_health(side=pause_field) != "captcha":
                self._captcha_strikes = 0
                self._log("INFO", "人工已完成验证，继续")
                return True
            self._stop_event.wait(timeout=min(CAPTCHA_POLL_SECONDS,
                                              max(0.1, deadline - time.time())))
        self._captcha_strikes += 1
        self._log("WARN", f"验证码等待 {CAPTCHA_WAIT_SECONDS} 秒无人应答，"
                          f"跳过当前任务（连续 {self._captcha_strikes}/"
                          f"{CAPTCHA_STRIKES_TO_PAUSE} 次）")
        if self._captcha_strikes >= CAPTCHA_STRIKES_TO_PAUSE:
            if pause_field == "reply":
                self._reply_paused = True
            else:
                self._greet_paused = True
                # 打招呼这边要自己再试：验证页常常是几分钟的临时风控，
                # 挂到人回来点按钮，等于这一天的额度白扔
                self._greet_auto_resume_at = time.time() + GREET_PAUSE_RETRY_SEC
            self._emit_wind("连续多次验证码都没人处理，"
                            f"{'自动回复' if pause_field == 'reply' else '打招呼'}已暂停；"
                            "手动过完验证后点恢复", "captcha")
        return False

    def _try_reconnect_browser(self):
        """尝试重新连接浏览器（指数退避策略）。

        打招呼和回复两个线程都可能在断连时调用，重连会关闭并重建浏览器实例
        以及全部引擎，因此必须串行：后到的线程等前一个重连做完再继续。

        退避序列：2s → 4s → 8s → 16s → 32s → 60s → 60s → ...
        每次重连失败后等待时间按指数增长，上限为 _reconnect_max_delay。
        """
        with self._reconnect_lock:
            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnect_attempts:
                self._log("ERROR", f"已达到最大重连次数 {self._max_reconnect_attempts}，停止重连")
                self._running = False
                return

            # 指数退避：base_delay * (backoff_factor ^ (attempts - 1))，上限 max_delay
            delay = min(
                self._reconnect_base_delay * (self._reconnect_backoff_factor ** (self._reconnect_attempts - 1)),
                self._reconnect_max_delay
            )

            self._log("INFO", f"尝试重连浏览器（第 {self._reconnect_attempts} 次，等待 {delay:.1f}s）...")
            # 在重连前先等待退避时间，避免短时间内频繁重连加重服务端压力
            self._stop_event.wait(timeout=delay)
            if not self._running:
                return

            self._log("DEBUG", "正在关闭旧浏览器实例...")

            try:
                self.browser_manager.close()
                time.sleep(2)
                self.browser_manager.launch()
                self._log("INFO", "浏览器重连成功")

                cookie_file = self._cookie_file()
                if cookie_file:
                    try:
                        if not self.browser_manager.load_cookies(cookie_file):
                            self._log("WARN", "重连后 Cookie 加载未成功，可能需要重新登录")
                    except Exception as e:
                        self._log("WARN", f"重连后 Cookie 加载异常: {e}")

                # 搜索标签页必须在 _init_engines() 之前建回来。启动路径里那句
                # get_search_page()（见 _initialize_and_run）不会再来第二次，而 close()
                # 已经把 _search_tab 置空；不重建的话打招呼侧就退回浏览器级别名，
                # 别名此刻绑的是刚起来的空白初始页 —— 用户看到的"第二个浏览器打开
                # 后无内容"就是这么来的（2026-09-30 真机取证 + 11:03 重连现场）。
                # 顺序也有讲究：先搜索页再聊天页，new_tab 之后别名才不会被会话页占住。
                search_page = self.browser_manager.get_search_page()
                self._log("INFO", f"重连后已重建搜索标签页: "
                                  f"{getattr(search_page, 'url', '') or '（读不到 URL）'}")

                # 重新初始化所有引擎（回复 + 打招呼）
                self._init_engines()
                self._reconnect_attempts = 0
                self._log("INFO", "引擎重新初始化完成，恢复运行")

            except Exception as e:
                self._log("ERROR", f"浏览器重连失败: {e}")
                # 不再固定等待10秒，由下次调用的指数退避决定等待时长

    # ─────────────────────────────────────────────
    # 上下文管理器支持
    # ─────────────────────────────────────────────

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False

# ─────────────────────────────────────────────────────────
# 多账号管理器
# ─────────────────────────────────────────────────────────

class MultiAccountManager:
    """多账号并行运行管理器

    管理多个 UnifiedBotLoop 实例，每个账号一个独立的浏览器实例和运行线程。
    支持并行启动/停止/暂停/恢复每个账号，汇总所有账号的状态和统计数据。

    使用方式：
        manager = MultiAccountManager(config=cfg, log_callback=print)
        manager.start()                # 启动所有启用的账号
        manager.start_account(1)       # 启动指定账号
        manager.pause_greet(0)         # 暂停指定账号的打招呼
        manager.pause_greet()          # 暂停所有账号的打招呼
        status = manager.get_status()  # 获取汇总状态
        manager.stop()                 # 停止所有账号
    """

    def __init__(self, config: Optional[UnifiedConfig] = None,
                 log_callback: Optional[Callable] = None,
                 greet_event_cb: Optional[Callable] = None,
                 reply_event_cb: Optional[Callable] = None,
                 wind_control_cb: Optional[Callable] = None):
        self.config = config or UnifiedConfig.load()
        self.log_cb = log_callback
        self._greet_event_cb = greet_event_cb
        self._reply_event_cb = reply_event_cb
        self._wind_control_cb = wind_control_cb
        self._loops: dict = {}  # account_index -> UnifiedBotLoop
        self._lock = threading.Lock()

        # 为每个启用的账号创建独立的 UnifiedBotLoop
        for i, acc in enumerate(self.config.greet.accounts):
            if acc.enabled:
                self._loops[i] = UnifiedBotLoop(
                    config=self.config,
                    log_callback=log_callback,
                    account_index=i,
                    greet_event_cb=greet_event_cb,
                    reply_event_cb=reply_event_cb,
                    wind_control_cb=wind_control_cb,
                )

    def _log(self, level: str, msg: str):
        """统一日志输出 — 前端精简，后台完整。"""
        if _should_show_frontend(level, msg) and self.log_cb:
            try:
                self.log_cb(f"[{level}] {msg}")
            except Exception:
                pass
        getattr(logger, level.lower(), logger.info)(msg)

    # ─────────────────────────────────────────────
    # 启动/停止
    # ─────────────────────────────────────────────

    def start(self):
        """启动所有启用的账号。"""
        with self._lock:
            for idx, loop in self._loops.items():
                try:
                    loop.start()
                except Exception as e:
                    self._log("ERROR", f"账号 {idx} 启动失败: {e}")
        self._log("INFO", f"多账号管理器已启动，共 {len(self._loops)} 个账号")

    def stop(self):
        """停止所有账号。"""
        with self._lock:
            for idx, loop in self._loops.items():
                try:
                    loop.stop()
                except Exception as e:
                    self._log("ERROR", f"账号 {idx} 停止失败: {e}")
        # 只在有账号在运行时才输出停止日志
        if any(loop._current_mode != "idle" for loop in self._loops.values()):
            self._log("INFO", "多账号管理器已停止")

    def start_account(self, account_index: int):
        """启动指定账号。"""
        with self._lock:
            loop = self._loops.get(account_index)
            if loop is None:
                self._log("WARN", f"账号 {account_index} 不存在或未启用")
                return
            try:
                loop.start()
            except Exception as e:
                self._log("ERROR", f"账号 {account_index} 启动失败: {e}")

    def stop_account(self, account_index: int):
        """停止指定账号。"""
        with self._lock:
            loop = self._loops.get(account_index)
            if loop is None:
                self._log("WARN", f"账号 {account_index} 不存在或未启用")
                return
            try:
                loop.stop()
            except Exception as e:
                self._log("ERROR", f"账号 {account_index} 停止失败: {e}")

    # ─────────────────────────────────────────────
    # 暂停/恢复
    # ─────────────────────────────────────────────

    def pause_greet(self, account_index: Optional[int] = None):
        """暂停打招呼（指定账号或全部）。"""
        with self._lock:
            if account_index is not None:
                loop = self._loops.get(account_index)
                if loop is not None:
                    loop.pause_greet()
            else:
                for loop in self._loops.values():
                    loop.pause_greet()

    def resume_greet(self, account_index: Optional[int] = None):
        """恢复打招呼（指定账号或全部）。"""
        with self._lock:
            if account_index is not None:
                loop = self._loops.get(account_index)
                if loop is not None:
                    loop.resume_greet()
            else:
                for loop in self._loops.values():
                    loop.resume_greet()

    def pause_reply(self, account_index: Optional[int] = None):
        """暂停回复（指定账号或全部）。"""
        with self._lock:
            if account_index is not None:
                loop = self._loops.get(account_index)
                if loop is not None:
                    loop.pause_reply()
            else:
                for loop in self._loops.values():
                    loop.pause_reply()

    def resume_reply(self, account_index: Optional[int] = None):
        """恢复回复（指定账号或全部）。"""
        with self._lock:
            if account_index is not None:
                loop = self._loops.get(account_index)
                if loop is not None:
                    loop.resume_reply()
            else:
                for loop in self._loops.values():
                    loop.resume_reply()

    # ─────────────────────────────────────────────
    # 登录确认
    # ─────────────────────────────────────────────

    def confirm_login(self, account_index: Optional[int] = None):
        """确认登录（指定账号或全部）。"""
        with self._lock:
            if account_index is not None:
                loop = self._loops.get(account_index)
                if loop is not None:
                    loop.confirm_login()
            else:
                for loop in self._loops.values():
                    loop.confirm_login()

    # ─────────────────────────────────────────────
    # 状态查询
    # ─────────────────────────────────────────────

    def get_status(self) -> dict:
        """返回汇总状态（所有账号）。"""
        accounts_status = []
        aggregated_stats = {
            "greet_applied": 0,
            "greet_skipped": 0,
            "greet_total": 0,
            "reply_sent": 0,
            "reply_skipped": 0,
            "resume_sent": 0,
            "important_events": 0,
            "greet_rounds": 0,
            "reply_rounds": 0,
        }

        any_running = False

        with self._lock:
            for idx in sorted(self._loops.keys()):
                loop = self._loops[idx]
                status = loop.get_status()
                accounts_status.append(status)

                if status["running"]:
                    any_running = True

                # 汇总统计
                for key in aggregated_stats:
                    if key in status["stats"]:
                        aggregated_stats[key] += status["stats"][key]

        return {
            "running": any_running,
            "accounts": accounts_status,
            "stats": aggregated_stats,
        }

    def get_account_status(self, account_index: int) -> Optional[dict]:
        """返回单个账号状态。"""
        with self._lock:
            loop = self._loops.get(account_index)
            if loop is None:
                return None
            return loop.get_status()

    # ─────────────────────────────────────────────
    # 上下文管理器支持
    # ─────────────────────────────────────────────

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
