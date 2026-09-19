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
from datetime import datetime
from typing import Optional, Callable

from boss_bot.unified_config import UnifiedConfig
from boss_bot.browser_launcher import BrowserManager
from boss_bot.greet_engine import GreetEngine
from boss_bot.reply_engine import ReplyEngine
from boss_bot.page_handler import BossChatHandler
from boss_bot.state_store import StateStore
from boss_bot.stats import Stats
from boss_bot.notify import Notifier
from boss_bot.message_store import MessageStore

# DrissionPage 断连异常
try:
    from DrissionPage.errors import PageDisconnectedError
except ImportError:
    PageDisconnectedError = None

logger = logging.getLogger(__name__)


# 前端可见的关键日志关键词 — INFO 级别日志只有包含这些关键词才推送前端
# WARN/ERROR/SUCCESS/CRITICAL 始终推送前端，DEBUG 始终不推送
_FRONTEND_LOG_KEYWORDS = (
    "启动", "停止", "已启动", "已停止", "已终止", "已退出",
    "浏览器", "登录", "登出", "重连",
    "配置已保存",
)


def _should_show_frontend(level: str, msg: str) -> bool:
    """判断日志是否应该推送到前端。"""
    level_upper = level.upper()
    if level_upper == "DEBUG":
        return False
    if level_upper in ("WARN", "ERROR", "SUCCESS", "CRITICAL"):
        return True
    # INFO 级别：根据关键词过滤
    return any(kw in msg for kw in _FRONTEND_LOG_KEYWORDS)


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
        self.config = config or UnifiedConfig.load()
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
        self._greet_paused = False
        self._reply_paused = False
        self._current_mode = "idle"
        self._current_chat = None
        self._last_check = ""

        # 线程控制
        self._init_thread: Optional[threading.Thread] = None
        self._greet_thread: Optional[threading.Thread] = None
        self._reply_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._login_event = threading.Event()

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

        # 共享浏览器管理器 — 使用指定账号的调试端口和独立用户数据目录
        browser_cfg = self.config.browser
        user_data_dir = os.path.join("browser_data", f"account_{account_index}")
        self.browser_manager = BrowserManager(
            config=browser_cfg,
            account_index=account_index,
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
        self._reconnect_attempts = 0
        self._max_reconnect_attempts = 10  # 允许更多次重连
        self._reconnect_base_delay = 2.0   # 基础延迟2秒
        self._reconnect_backoff_factor = 2.0  # 退避因子
        self._reconnect_max_delay = 60.0   # 最大延迟60秒

        # 热重载配置默认值 — 运行时可被 _hot_reload_config() 覆盖
        self._message_interval_min = 3
        self._message_interval_max = 8
        self._greet_enabled = True
        self._reply_enabled = True
        self._page_timeout = 30

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
            self._reply_event_cb({
                "time": datetime.now().strftime("%H:%M:%S"),
                "contact_name": contact_name or "",
                "job_name": job_name or "",
                "message_received": message_received or "",
                "reply_sent": reply_sent or "",
                "ai_model": ai_model or "",
                "intent": intent or "",
                "status": status,
            })
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
        """暂停打招呼功能。回复功能不受影响。"""
        self._greet_paused = True
        if self._greet_engine is not None:
            self._greet_engine.stop()
        self._log("INFO", "打招呼已暂停")

    def resume_greet(self):
        """恢复打招呼功能。"""
        self._greet_paused = False
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

    def get_status(self) -> dict:
        """获取当前运行状态。"""
        return {
            "index": self.account_index,
            "name": self.account_name,
            "running": self._running,
            "logged_in": self._logged_in,
            "needs_login": self._needs_login,
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
            return False

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
        if self.account_index < len(self.config.greet.accounts):
            acc_cookie = self.config.greet.accounts[self.account_index].cookie_file
        else:
            acc_cookie = ""
        cookie_file = acc_cookie or self.config.login.cookie_file
        self._log("DEBUG", f"Cookie 文件路径: {cookie_file}")
        if cookie_file:
            try:
                if self.browser_manager.load_cookies(cookie_file):
                    self._log("INFO", "已加载 Cookie，验证登录状态...")
                    self._log("DEBUG", "正在访问聊天页面验证登录态...")
                    instance.get("https://www.zhipin.com/web/geek/chat")
                    time.sleep(3)

                    current_url = instance.url or ""
                    self._log("DEBUG", f"验证页面URL: {current_url}")
                    if "login" not in current_url and "user" not in current_url and "passport" not in current_url:
                        self._logged_in = True
                        self._log("SUCCESS", "Cookie 有效，已自动登录")
                        return True
                    else:
                        self._log("WARN", "Cookie 已过期，需要重新登录")
                else:
                    self._log("INFO", "无 Cookie 文件或加载失败")
            except Exception as e:
                self._log("WARN", f"Cookie 加载异常: {e}")

        # 需要手动登录
        self._needs_login = True
        self._log("WARN", "需要手动登录，已跳转到登录页面")

        try:
            instance.get("https://www.zhipin.com/web/user/?ka=header-login")
        except Exception:
            pass

        self._log("INFO", "请在浏览器中手动登录 BOSS 直聘，登录完成后点击「我已登录」按钮")

        # 等待用户确认登录
        if not self._login_event.wait(timeout=self.config.login.wait_timeout):
            self._log("ERROR", "登录超时，主循环退出")
            return False

        # 登录确认后保存 Cookie
        try:
            self.browser_manager.save_cookies(cookie_file)
            self._log("SUCCESS", "Cookie 已保存")
        except Exception as e:
            self._log("WARN", f"Cookie 保存失败: {e}")

        self._logged_in = True
        self._needs_login = False
        return True

    def _init_engines(self):
        """初始化打招呼引擎和回复相关组件。"""
        self._log("INFO", "正在初始化引擎...")

        # 初始化回复相关组件 — 使用聊天标签页
        chat_page = self.browser_manager.get_chat_page()
        self._log("DEBUG", "正在初始化回复组件（BossChatHandler, ReplyEngine, StateStore...）")
        self._chat_handler = BossChatHandler(
            browser_manager=self.browser_manager,
            browser_instance=chat_page,
        )

        self._reply_engine = ReplyEngine()
        self._state_store = StateStore()
        self._stats = Stats()
        self._notifier = Notifier()
        self._msg_store = MessageStore()

        # 初始化打招呼引擎 — 使用搜索标签页
        self._log("DEBUG", "正在初始化打招呼引擎（GreetEngine）...")
        self._greet_engine = GreetEngine(
            browser_manager=self.browser_manager,
            config=self.config,
            log_callback=lambda msg: self._log("INFO", msg),
            progress_callback=self._on_greet_progress,
            greet_event_cb=self._greet_event_cb,
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

        greet_enabled = self.config.greet.enabled
        if not greet_enabled:
            self._log("INFO", "打招呼功能未启用，线程退出")
            return

        consecutive_empty_rounds = 0  # 连续空搜索轮次计数

        while self._running and not self._stop_event.is_set():
            try:
                if self._greet_paused:
                    self._stop_event.wait(timeout=10)
                    continue

                # ── 健康检查：检测验证码/风控/登录态 ──
                health = self._check_health()
                if health == "captcha":
                    self._log("ERROR", "⚠️ 检测到验证码/风控拦截！打招呼已暂停，请手动解除风控后恢复")
                    if self._wind_control_cb:
                        self._wind_control_cb("检测到验证码/风控拦截，请手动解除", "captcha")
                    self._greet_paused = True
                    continue
                elif health == "need_login":
                    self._log("WARN", "登录态失效，等待重新登录...")
                    self._needs_login = True
                    self._stop_event.wait(timeout=30)
                    continue
                elif health == "browser_disconnected":
                    self._log("WARN", "浏览器断开，尝试重连...")
                    self._try_reconnect_browser()
                    self._stop_event.wait(timeout=10)
                    continue

                self._current_mode = "greet"
                round_had_jobs = self._run_greet_round()

                # ── 连续空搜索检测 ──
                if round_had_jobs is False:
                    consecutive_empty_rounds += 1
                    if consecutive_empty_rounds >= 3:
                        self._log("ERROR", "⚠️ 连续3轮搜索结果为0，可能触发风控或岗位已投完。打招呼已暂停，请检查BOSS直聘页面")
                        if self._wind_control_cb:
                            self._wind_control_cb("连续3轮搜索结果为0，请检查BOSS直聘页面", "limit")
                        self._greet_paused = True
                        consecutive_empty_rounds = 0
                else:
                    consecutive_empty_rounds = 0

                # 打招呼间隔
                greet_interval = self.config.greet.rate_limit.min_interval if hasattr(self.config.greet.rate_limit, 'min_interval') else 30
                self._stop_event.wait(timeout=greet_interval)

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

    def _run_greet_round(self) -> bool:
        """执行一轮打招呼任务。返回 True 如果有岗位被处理，False 如果所有搜索都为空。"""
        self._log("INFO", "━━━ 开始打招呼轮次 ━━━")
        self._stats_dict["greet_rounds"] += 1
        any_jobs_found = False

        try:
            tasks = self._build_greet_tasks()
            self._log("DEBUG", f"构建打招呼任务数: {len(tasks)}")
            if not tasks:
                self._log("WARN", "无打招呼任务可执行")
                return

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

                jobs = self._greet_engine.search_jobs(query, city, scroll_pages)
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

                    # 去重检查：已沟通过的岗位跳过
                    if self._greet_engine._is_already_chatted(job):
                        self._log("INFO", f"⏭️ 已沟通过: {job.get('job_name', '')}")
                        self._stats_dict["greet_skipped"] += 1
                        continue

                    # AI 智能匹配分析 — 热重载所有运行时可变配置
                    self._hot_reload_config()
                    has_ai = self._greet_engine._ai_enabled and bool(self._greet_engine._ai_providers)
                    if has_ai:
                        ai_result, ai_duration = self._greet_engine._analyze_job_with_ai(job)
                        if ai_result is None and self._greet_engine._init_ai() is not None:
                            self._log("WARN", f"🤖 AI 判定不匹配，跳过: {job.get('job_name', '')}")
                            self._stats_dict["greet_skipped"] += 1
                            continue
                        if ai_result and ai_result.get("suggested_greeting"):
                            job["_ai_suggested_greeting"] = ai_result["suggested_greeting"]

                    # 随机间隔 — 优先使用热重载的配置值，回退到任务级配置
                    min_interval = getattr(self, '_message_interval_min', task.get("message_interval_min", 3))
                    max_interval = getattr(self, '_message_interval_max', task.get("message_interval_max", 8))
                    delay = random.uniform(min_interval, max_interval)
                    self._stop_event.wait(timeout=delay)
                    if not self._running:
                        break

                    success = self._greet_engine.send_greeting(job)
                    if success:
                        self._stats_dict["greet_applied"] += 1
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

        reply_enabled = self.config.reply.enabled
        if not reply_enabled:
            self._log("INFO", "回复功能未启用，线程退出")
            return

        while self._running and not self._stop_event.is_set():
            try:
                if self._reply_paused:
                    self._stop_event.wait(timeout=10)
                    continue

                # 热重载所有运行时可变配置（AI/频率/间隔/开关等）
                self._hot_reload_config()

                self._current_mode = "reply"
                self._run_reply_round()

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

    def _run_reply_round(self):
        """执行一轮回复任务：检查未读消息并回复。"""
        self._log("INFO", "━━━ 开始回复轮次 ━━━")
        self._stats_dict["reply_rounds"] += 1
        self._last_check = datetime.now().strftime("%H:%M:%S")

        try:
            # 人工接管模式提示
            if self._state_store.is_paused():
                info = self._state_store.pause_info()
                self._log("INFO", f"人工接管模式中（{info.get('reason', '')}），仅监控不回复")

            # 导航到聊天页面（使用聊天标签页）
            self._log("DEBUG", "正在导航到聊天页面...")
            self._chat_handler.go_to_chat()
            self._log("DEBUG", "聊天页面导航完成")

            # 获取未读聊天列表
            self._log("DEBUG", "正在获取未读聊天列表...")
            unread_chats = self._chat_handler.get_unread_chats()
            self._log("DEBUG", f"获取到未读会话数: {len(unread_chats)}")

            if not unread_chats:
                self._log("DEBUG", "无未读消息")
            else:
                self._log("INFO", f"发现 {len(unread_chats)} 个未读会话")

                for chat_info in unread_chats:
                    if not self._running or self._reply_paused:
                        break

                    name = chat_info.get("name", "未知")
                    self._current_chat = name

                    if self._state_store.is_paused():
                        self._log("INFO", f"人工接管模式中，跳过 [{name}]")
                        break

                    self._process_single_chat(chat_info)
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

    def _process_single_chat(self, chat_info: dict):
        """处理单个未读聊天会话。"""
        name = chat_info.get("name", "未知")
        self._log("INFO", f"--- 正在处理与 [{name}] 的聊天 ---")
        self._log("DEBUG", f"聊天会话信息: {chat_info}")

        if not self._chat_handler.enter_chat(chat_info):
            self._log("WARN", f"会话 [{name}] 切换校验失败，本次跳过")
            self._emit_reply_event(
                contact_name=name, job_name="",
                message_received="", reply_sent="",
                ai_model="", intent="",
                status="skipped",
            )
            return

        context_count = self.config.reply.context_message_count
        self._log("DEBUG", f"正在读取最近 {context_count} 条消息...")
        messages = self._chat_handler.read_latest_messages(count=context_count)
        self._log("DEBUG", f"读取到消息数: {len(messages)}")
        if not messages:
            self._log("INFO", "未读取到消息，跳过")
            self._emit_reply_event(
                contact_name=name, job_name="",
                message_received="", reply_sent="",
                ai_model="", intent="",
                status="skipped",
            )
            return

        latest_other_msg = None
        for msg in reversed(messages):
            if not msg.get("is_mine"):
                latest_other_msg = msg.get("text", "")
                break

        if not latest_other_msg:
            self._log("INFO", "最新消息是自己发的，无需回复")
            self._emit_reply_event(
                contact_name=name, job_name="",
                message_received="", reply_sent="",
                ai_model="", intent="",
                status="skipped",
            )
            return

        self._log("INFO", f"对方最新消息: {latest_other_msg[:80]}")

        if self._state_store.was_handled(name, latest_other_msg):
            self._log("INFO", "该消息已处理过，跳过（防重复回复）")
            self._stats.record_skip()
            self._emit_reply_event(
                contact_name=name, job_name="",
                message_received=latest_other_msg, reply_sent="",
                ai_model="", intent="",
                status="skipped",
            )
            return

        boss_name = self._chat_handler.get_boss_name()
        job_name = self._chat_handler.get_job_name()
        self._log("DEBUG", f"聊天对象: boss_name={boss_name}, job_name={job_name}")

        action, content, meta = self._reply_engine.get_reply(
            messages, boss_name, job_name, chat_name=name
        )
        self._log("DEBUG", f"回复引擎决策: action={action}, meta={meta}")

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

        # 简历去重降级
        if action == "resume" and self.config.reply.resume_send_once and \
           self._state_store.resume_sent(name):
            from boss_bot.config import RESUME_DUPLICATE_REPLY
            self._log("INFO", "该会话已发送过简历，降级为文字提醒")
            action, content = "text", RESUME_DUPLICATE_REPLY
            meta["source"] = "intent"

        # 执行回复
        if action == "resume":
            self._reply_engine.wait_human_delay()
            if self._chat_handler.send_resume():
                self._state_store.mark_resume_sent(name)
                self._stats.record_reply(source=meta.get("source", "rule"), action="resume")
                self._stats_dict["resume_sent"] += 1
                self._msg_store.append_message(name, {
                    "text": "[简历已发送]",
                    "is_mine": True,
                    "source": "bot",
                    "action": "resume",
                    "time": datetime.now().strftime("%H:%M"),
                }, job_name)
                self._log("INFO", "已发送简历")
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
                self._reply_engine.wait_human_delay()
                self._chat_handler.send_text(RESUME_UNAVAILABLE_REPLY)
                self._msg_store.append_message(name, {
                    "text": RESUME_UNAVAILABLE_REPLY,
                    "is_mine": True,
                    "source": "bot",
                    "action": "text_fallback",
                    "time": datetime.now().strftime("%H:%M"),
                }, job_name)
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

        elif action == "text" and content:
            self._reply_engine.wait_human_delay()
            if self._chat_handler.send_text(content):
                self._stats.record_reply(source=meta.get("source", "rule"), action="text")
                self._stats_dict["reply_sent"] += 1
                self._msg_store.append_message(name, {
                    "text": content,
                    "is_mine": True,
                    "source": "bot",
                    "action": "text",
                    "reply_source": meta.get("source", ""),
                    "time": datetime.now().strftime("%H:%M"),
                }, job_name)
                self._log("INFO", f"已回复: {content[:30]}...")
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
                self._emit_reply_event(
                    contact_name=name, job_name=job_name,
                    message_received=latest_other_msg, reply_sent=content or "",
                    ai_model=self._reply_engine._last_ai_model,
                    intent=meta.get("intent", ""),
                    status="error",
                )

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

        self._state_store.mark_handled(name, latest_other_msg, action or "none")
        self._reply_engine.record_reply()

    # ─────────────────────────────────────────────
    # 配置热重载
    # ─────────────────────────────────────────────

    def _hot_reload_config(self):
        """热重载所有运行时可变配置。

        从配置文件重新加载，并同步到各运行引擎和主循环属性。
        覆盖范围：
          1. AI 配置（开关 + providers）
          2. 频率限制（每小时/每天上限）
          3. 消息间隔（打招呼最小/最大间隔）
          4. 回复配置（回复间隔、每会话最大回复数）
          5. 浏览器配置（页面超时）
          6. 打招呼开关
          7. 回复开关
        任何配置加载异常都只记录日志并返回，不影响主循环运行。
        """
        try:
            self.config = UnifiedConfig.load()
        except Exception as e:
            self._log("WARN", f"热重载配置失败，保留旧配置: {e}")
            return

        # 1. AI 配置热重载 — 打招呼和回复引擎共享同一份 AI 配置
        if self._greet_engine:
            self._greet_engine._ai_enabled = self.config.ai.enabled
            self._greet_engine._ai_providers = self.config.ai.providers

        if self._reply_engine:
            self._reply_engine._ai_enabled = self.config.ai.enabled
            self._reply_engine._ai_providers = self.config.ai.providers

        # 2. 频率限制热重载 — 每小时/每天打招呼上限
        if hasattr(self.config, 'greet') and self._greet_engine:
            self._greet_engine._rate_per_hour = getattr(self.config.greet, 'rate_per_hour', 30)
            self._greet_engine._rate_per_day = getattr(self.config.greet, 'rate_per_day', 100)

        # 3. 消息间隔热重载 — 打招呼消息发送间隔
        if hasattr(self.config, 'greet'):
            self._message_interval_min = getattr(self.config.greet, 'message_interval_min', 3)
            self._message_interval_max = getattr(self.config.greet, 'message_interval_max', 8)

        # 4. 回复配置热重载 — 回复间隔和每会话最大回复数
        if hasattr(self.config, 'reply') and self._reply_engine:
            self._reply_engine._reply_interval_min = getattr(self.config.reply, 'reply_interval_min', 5)
            self._reply_engine._reply_interval_max = getattr(self.config.reply, 'reply_interval_max', 15)
            self._reply_engine._max_reply_per_conversation = getattr(self.config.reply, 'max_reply_per_conversation', 10)

        # 5. 浏览器配置热重载 — 页面超时时间
        if hasattr(self.config, 'browser'):
            self._page_timeout = getattr(self.config.browser, 'page_timeout', 30)

        # 6. 打招呼开关热重载
        if hasattr(self.config, 'greet'):
            self._greet_enabled = getattr(self.config.greet, 'enabled', True)

        # 7. 回复开关热重载
        if hasattr(self.config, 'reply'):
            self._reply_enabled = getattr(self.config.reply, 'enabled', True)

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

    def _check_health(self) -> str:
        """健康检查：检测登录态和验证码拦截。"""
        instance = self.browser_manager.get_instance()
        if instance is None:
            return "browser_disconnected"

        try:
            _ = instance.url
        except Exception:
            return "browser_disconnected"

        if self._chat_handler is not None:
            try:
                return self._chat_handler.check_health()
            except Exception:
                pass

        return "ok"

    def _try_reconnect_browser(self):
        """尝试重新连接浏览器（指数退避策略）。

        退避序列：2s → 4s → 8s → 16s → 32s → 60s → 60s → ...
        每次重连失败后等待时间按指数增长，上限为 _reconnect_max_delay。
        """
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

            cookie_file = self.config.login.cookie_file
            if cookie_file:
                try:
                    self.browser_manager.load_cookies(cookie_file)
                except Exception:
                    pass

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
