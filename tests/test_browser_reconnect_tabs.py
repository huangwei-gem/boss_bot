# -*- coding: utf-8 -*-
"""断连/重连之后，那两个标签页还在不在。

为什么单独测这个：2026-09-30 用户报"双浏览器实例运行异常，第二个浏览器打开后无内容"。
真机取证（tools/tab_handle_drift_probe.py --concurrent）证明端口、profile、CDP 归属都是
对的，两个号同时跑各自守着自己的标签页 —— 所以不是并发抢端口那一类。现场日志里唯一的
异常是 `11:03:31 [账号2] 引擎重新初始化完成，恢复运行`，也就是只有账号2 走过重连。
而重连路径 close()+launch() 之后只 _init_engines()（那里建聊天标签页），启动路径里那句
get_search_page()（main_loop.py:719）不会再来第二次 —— 打招呼侧于是退回"浏览器级别名"，
别名此刻绑的是刚起来的空白初始页，窗口看着就是空的。

全程离线：不起真浏览器、不点任何沟通/发送/继续沟通。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.unified_config import UnifiedConfig


def _cfg():
    cfg = UnifiedConfig()
    from boss_bot.unified_config import AccountConfig, JobConfig
    cfg.greet.accounts = [
        AccountConfig(name="主账号", enabled=True, cookie_file="zhipin_cookies.json",
                      jobs=[JobConfig(query="数据分析", city="长沙", enabled=True)]),
        AccountConfig(name="账号2", enabled=True, cookie_file="zhipin_cookies_1.json",
                      jobs=[JobConfig(query="数据分析", city="上海", enabled=True)]),
    ]
    cfg.browser.debug_port = 9222
    return cfg


def _recording_loop():
    """造一个把 BrowserManager 全打桩的循环，记录它对标签页做了什麼"""
    calls = []

    class RecordingManager:
        def __init__(self, config=None, account_index=0, port=None, user_data_dir=None):
            self.account_index = account_index
            self._instance = MagicMock()
            # URL 要给真串：断连恢复与重连日志都靠它判断"这一页到底是不是岗位页"
            self._instance.url = ("https://www.zhipin.com/web/geek/jobs"
                                  "?query=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90&city=101250100")

        def get_instance(self):
            return self._instance

        def get_reply_tab_id(self):
            return None

        def close(self):
            calls.append("close")

        def launch(self):
            calls.append("launch")
            return self._instance

        def load_cookies(self, filepath=""):
            calls.append("load_cookies")
            return True

        def get_search_page(self):
            calls.append("search")
            return self._instance

        def get_chat_page(self):
            calls.append("chat")
            return self._instance

    with patch("boss_bot.main_loop.BrowserManager", RecordingManager):
        from boss_bot.main_loop import MultiAccountManager
        mgr = MultiAccountManager(config=_cfg())
    del calls[:]                      # 构造期不算，只看重连这一次
    loop = mgr._loops[1]
    loop._reconnect_base_delay = 0
    loop._running = True              # 重连开头就检查这个，False 会直接 return
    # 真 _init_engines 会去建聊天标签页（main_loop.py:1128），这里用 side_effect 记账，
    # 既不打桩掉真实顺序，也不让引擎构造把测试拖成集成测试
    loop._init_engines = MagicMock(side_effect=lambda: calls.append("chat"))
    return loop, calls


def reconnect(loop):
    """跑一次重连，把 close() 之后的 time.sleep(2) 吃掉，测试不该为它等"""
    with patch("boss_bot.main_loop.time.sleep"):
        loop._try_reconnect_browser()


class ReconnectRebuildsTabsTest:
    def test_重连后必须重建搜索标签页(self):
        """这是"第二个浏览器打开后无内容"的正面锁：重连只补聊天页不补搜索页就是漏"""
        loop, calls = _recording_loop()
        reconnect(loop)
        assert "search" in calls, f"重连后没有重建搜索标签页：{calls}"
        assert calls.index("launch") < calls.index("search"), \
            f"搜索页要在 launch 之后建：{calls}"

    def test_搜索页建在聊天页之前(self):
        """new_tab 会把浏览器级别名带到会话页；先建搜索页才不会一启动就读错 tab"""
        loop, calls = _recording_loop()
        reconnect(loop)
        assert "search" in calls and "chat" in calls, calls
        assert calls.index("search") < calls.index("chat"), \
            f"顺序反了：{calls} —— 别名会先被会话页占住"

    def test_重连日志说得出落在哪一页(self):
        """现场要能自证：下次再报"打开后无内容"，日志里就有当时那一页的 URL"""
        loop, calls = _recording_loop()
        logged = []
        loop._log = lambda level, msg: logged.append(f"{level}|{msg}")
        reconnect(loop)
        assert any("搜索" in m and "zhipin" in m.lower() for m in logged), \
            f"重连日志没交代搜索页落在哪：{logged}"


class _DisconnectHarness:
    """只测 GreetEngine._handle_disconnect 这一个方法，绕开它重型 __init__"""

    def build(self):
        from boss_bot.greet_engine import GreetEngine
        eng = GreetEngine.__new__(GreetEngine)
        inst = MagicMock()
        inst.url = "https://www.zhipin.com/web/geek/jobs?query=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90&city=101250100"
        bm = MagicMock()
        bm.get_instance.return_value = inst
        eng.browser_manager = bm
        eng._log = lambda *a, **k: None
        eng._random_delay = lambda a, b: None
        eng._query = "数据分析"
        eng._city = "长沙"
        eng._job_type = ""            # __init__ 里会建，harness 绕过 __init__ 要自己补
        eng._build_search_url = lambda q, c, jt="": ("https://www.zhipin.com/web/geek/jobs"
                                                     "?query=%E6%95%B0%E6%8D%AE%E5%88%86%E6%9E%90"
                                                     "&city=101250100")
        eng.check_login = lambda *a, **k: True
        return eng, inst, bm


class DisconnectRecoveryTest:
    def test_恢复后的页面是岗位列表而不是空白页(self):
        """旧实现导航到 about:blank 就 return True —— 窗口就此停在空白"""
        eng, inst, _ = _DisconnectHarness().build()
        assert eng._handle_disconnect() is True
        urls = [c.args[0] for c in inst.get.call_args_list]
        assert urls, "恢复过程一次都没导航"
        assert urls[-1] != "about:blank", "恢复完停在 about:blank，用户看到的就是无内容窗口"
        assert "zhipin.com" in urls[-1] and "geek" in urls[-1], urls

    def test_恢复不许关掉共享浏览器(self):
        """close() 会把回复侧正握着的 _chat_tab 一起带走；重建浏览器归 _try_reconnect_browser 管"""
        eng, _, bm = _DisconnectHarness().build()
        eng._handle_disconnect()
        assert not bm.close.called, "打招呼侧不该从自己内部关掉两个线程共享的浏览器"

    def test_真起不来时如实返回失败(self):
        """返回 False 上层才会去走带锁的串行重连，而不是拿着死对象继续跑"""
        eng, inst, _ = _DisconnectHarness().build()
        inst.get.side_effect = Exception("connection is closed")
        assert eng._handle_disconnect() is False
