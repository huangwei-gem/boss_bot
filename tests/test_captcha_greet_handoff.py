# -*- coding: utf-8 -*-
"""人机验证：打招呼侧到底有没有把闸门叫起来。

用户口径（2026-09-30 选定）：遇人机验证 → 暂停交人工；人工超过 1 分钟无响应 →
自动跳过当前任务；连续 3 个岗位都卡验证 → 停轮交人工。
`_captcha_gate` 早就按这个语义写了（CAPTCHA_WAIT_SECONDS=60、CAPTCHA_STRIKES_TO_PAUSE=3），
但两条路把它架空了：
① 打招呼侧认出验证页只 return 一句原因，从不调闸门（greet_engine 里 _wind_control_cb
   赋值后全文件未调用）；
② `_check_health()` 只要回复引擎一建好就永远读会话页，验证页弹在另一张标签上，
   闸门等到超时也等不到"已恢复"。
所以现场看起来就是"程序卡在那张验证图不动"。全程离线，不起真浏览器、不点任何发送。
"""
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))

from boss_bot.greet_engine import CAPTCHA_REASON, GreetEngine


def _engine(gate=None):
    eng = GreetEngine.__new__(GreetEngine)
    eng._log = lambda *a, **k: None
    eng._captcha_gate_cb = gate
    return eng


class HandoffTest:
    def test_没接闸门时不许假装恢复(self):
        """CLI/测试里 gate 可能为 None —— 这时候必须老实按"没恢复"走"""
        assert _engine(None)._captcha_handoff() is False

    def test_闸门说人工过了才算过(self):
        seen = []

        def gate():
            seen.append(1)
            return True

        assert _engine(gate)._captcha_handoff() is True
        assert seen == [1], "闸门没被调用，验证页就等于没人管"

    def test_闸门抛异常按未恢复处理(self):
        def boom():
            raise RuntimeError("浏览器断了")

        assert _engine(boom)._captcha_handoff() is False

    def test_未找到输入框那条路也要叫闸门(self):
        """这条是源码锁：那个分支嵌在几十行的发送流程里，没法单测，就只能锁它在不在。

        认出验证页却不叫闸门 = 用户看到的"卡在验证图"，别再改回只写日志。
        """
        src = (ROOT / "boss_bot" / "greet_engine.py").read_text(encoding="utf-8")
        start = src.index("reason = chat_failure_reason(snap)")
        seg = src[start:src.index('if "登录" in reason', start)]
        assert "self._captcha_handoff()" in seg, \
            "未找到输入框那条路认出验证页却没等人工"


class MissingChatButtonAttributionTest:
    """点完沟通却没有按钮：先归因，是验证页就交人工，人工过了再重试一次按钮"""

    def _eng(self, handoff_result, retry_results):
        """retry_results = 人工过完验证之后再一次找按钮的返回序列（第一次不算：
        找按钮是调用方先做的，helper 里只在人工过了之后补试一次）"""
        eng = _engine(lambda: handoff_result)
        eng._chat_snapshot = lambda instance: {"url": "", "captcha": True}
        eng._find_chat_button = MagicMock(side_effect=retry_results)
        eng._mark_chatted = MagicMock()
        return eng

    def test_验证页会叫起闸门并且人工过了就重试按钮(self):
        btn = MagicMock()
        eng = self._eng(True, [btn])
        with patch("boss_bot.greet_engine.chat_button_failure_reason",
                   return_value=(CAPTCHA_REASON, False)):
            reason, got = eng._explain_missing_chat_button(MagicMock(), "u", {})
        assert got is btn, "人工已完成验证却没重试找按钮，这个岗位就白丢了"
        assert reason == ""

    def test_到点没人处理就带着原因跳过这个岗位(self):
        eng = self._eng(False, [])
        with patch("boss_bot.greet_engine.chat_button_failure_reason",
                   return_value=(CAPTCHA_REASON, False)):
            reason, got = eng._explain_missing_chat_button(MagicMock(), "u", {})
        assert reason == CAPTCHA_REASON and got is None
        assert eng._find_chat_button.call_count == 0, "没人处理还再等 8 秒找按钮，纯拖时间"

    def test_人工过了但按钮还是没回来不无限重试(self):
        """只重试一次：再没有就按原原因跳过，不然一个岗位能把整轮吊死"""
        eng = self._eng(True, [None])
        with patch("boss_bot.greet_engine.chat_button_failure_reason",
                   return_value=(CAPTCHA_REASON, False)):
            reason, got = eng._explain_missing_chat_button(MagicMock(), "u", {})
        assert reason == CAPTCHA_REASON and got is None
        assert eng._find_chat_button.call_count == 1, eng._find_chat_button.call_count

    def test_不是验证页就不叫闸门(self):
        eng = _engine(lambda: pytest.fail("不该调闸门"))
        eng._chat_snapshot = lambda instance: {"url": "https://x/job_detail/123.html"}
        eng._find_chat_button = MagicMock(return_value=None)
        eng._mark_chatted = MagicMock()
        with patch("boss_bot.greet_engine.chat_button_failure_reason",
                   return_value=("岗位已下线", False)):
            reason, got = eng._explain_missing_chat_button(MagicMock(), "u", {})
        assert reason == "岗位已下线" and got is None

    def test_会话页那种要标已沟通而不是留着重撞(self):
        """这是上一轮修的分档：BOSS 把已沟通岗位跳成会话页，不标就是下一轮再投一次"""
        eng = _engine(lambda: pytest.fail("不该调闸门"))
        eng._chat_snapshot = lambda instance: {"url": "https://x/web/geek/chat"}
        eng._find_chat_button = MagicMock(return_value=None)
        eng._mark_chatted = MagicMock()
        with patch("boss_bot.greet_engine.chat_button_failure_reason",
                   return_value=("已跳会话页", True)):
            reason, got = eng._explain_missing_chat_button(MagicMock(), "u", {"url": "u"})
        assert reason == "已跳会话页" and got is None
        assert eng._mark_chatted.called


class SideAwareHealthTest:
    # 一张真的要人操作的验证页：URL 带着风控参数（这本身不足以定罪），
    # 页面上有个 320x120 的可见挑战容器（这才是判据）
    CHALLENGE = ('{"boxes":[{"sel":".nc-container","w":320,"h":120,'
                 '"display":"block","visibility":"visible"}],"words":[]}')

    def _loop(self):
        from boss_bot.unified_config import UnifiedConfig, AccountConfig, JobConfig
        cfg = UnifiedConfig()
        cfg.greet.accounts = [
            AccountConfig(name="主账号", enabled=True, cookie_file="a0.json",
                          jobs=[JobConfig(query="数据分析", city="长沙", enabled=True)]),
            AccountConfig(name="账号2", enabled=True, cookie_file="a1.json",
                          jobs=[JobConfig(query="数据分析", city="上海", enabled=True)]),
        ]
        cfg.browser.debug_port = 9222

        class FakeManager:
            def __init__(self, config=None, account_index=0, port=None, user_data_dir=None):
                self.instance = MagicMock()
                self.instance.url = "https://www.zhipin.com/web/geek/jobs"
                self.instance.run_js.return_value = '{"boxes":[],"words":[]}'
                self.search = MagicMock()
                self.search.url = "https://www.zhipin.com/_security_check"
                self.search.run_js.return_value = SideAwareHealthTest.CHALLENGE

            def get_instance(self):
                return self.instance

            def get_search_page(self):
                return self.search

            def get_chat_page(self):
                return self.instance

            def get_reply_tab_id(self):
                return None

        with patch("boss_bot.main_loop.BrowserManager", FakeManager):
            from boss_bot.main_loop import MultiAccountManager
            mgr = MultiAccountManager(config=cfg)
        loop = mgr._loops[0]
        loop._log = lambda *a, **k: None
        # 回复侧的会话页很健康，验证页在另一张标签上 —— 这正是以前读错的地方
        loop._chat_handler = MagicMock()
        loop._chat_handler.check_health.return_value = "ok"
        return loop

    def test_打招呼侧健康检查看的是搜索页(self):
        loop = self._loop()
        assert loop._check_health(side="greet") == "captcha", \
            "打招呼侧还在读会话页，验证页就永远等不到恢复"

    def test_回复侧健康检查仍然看会话页(self):
        loop = self._loop()
        assert loop._check_health(side="reply") == "ok"
        assert loop._chat_handler.check_health.called

    def test_闸门复核时带上自己那一侧(self):
        loop = self._loop()
        seen = []

        def fake_health(side="reply"):
            seen.append(side)
            return "ok"

        loop._check_health = fake_health
        loop._emit_wind = lambda *a, **k: None
        loop._running = True
        assert loop._captcha_gate("greet") is True
        assert seen and seen[0] == "greet", f"闸门复核读错侧：{seen}"


class InterruptibleWaitTest:
    def _eng(self):
        eng = GreetEngine.__new__(GreetEngine)
        eng.running = True
        eng._log = lambda *a, **k: None
        return eng

    def test_随机延时会被停止打断(self):
        """裸 time.sleep 让"停止"要点满重试循环才生效，最长几分钟；卡验证页时最明显"""
        eng = self._eng()
        threading.Timer(0.2, lambda: setattr(eng, "running", False)).start()
        started = time.monotonic()
        eng._random_delay(4, 6)
        assert time.monotonic() - started < 1.5, "停不下来：等待没看 running"

    def test_等人工登录会被停止打断(self):
        """_login_event 只有"我已登录"会 set，stop() 不打断它就是 300 秒空转"""
        eng = self._eng()
        eng._login_event = threading.Event()
        eng._login_wait_timeout = 5
        eng.check_login = lambda: True
        threading.Timer(0.3, lambda: setattr(eng, "running", False)).start()
        started = time.monotonic()
        assert eng._wait_for_login() is False
        assert time.monotonic() - started < 2.0, "停止没被听见"

    def test_人工登录完仍然照常返回成功(self):
        eng = self._eng()
        eng._login_event = threading.Event()
        eng._login_wait_timeout = 5
        eng._random_delay = lambda *a: None      # 登录后那段 settle 等待不占测试时间
        eng.check_login = lambda: True
        threading.Timer(0.2, lambda: eng._login_event.set()).start()
        assert eng._wait_for_login() is True
