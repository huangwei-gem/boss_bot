# -*- coding: utf-8 -*-
"""风控/验证码：人工验证等 60 秒，超时跳过当前任务。

用户要的语义很具体："碰到验证就让我手动过，超过 1 分钟我没反应就跳过"。
现在代码里卡死在那张图上有三个原因（2026-09-29 逐行定位）：

1. `page_handler.check_health()` 不认 `_security_check` 这种 URL，而且 JS 返回
   不是 ok/captcha 时**兜底成 ok**（page_handler.py:1352）——纯图形验证页被判健康。
2. `main_loop._check_health()` 在 `_chat_handler is None` 时**直接返回 "ok"**
   （main_loop.py:1817）——只有回复侧初始化了才可能认出验证码。
3. 就算认出来了，旧逻辑是 `self._greet_paused = True` 永久暂停，而
   `_maybe_auto_resume_greet()` 要求 `_greet_paused_by_cap` 标记（只有每日上限会设），
   验证码造成的暂停**永远解不开**；回复侧更糟：`_reply_paused` 分支在
   `_hot_reload_config()` 之前就 continue，自动恢复函数根本走不到。
"""
import threading
import time
from unittest.mock import MagicMock, patch


def make_loop():
    from boss_bot.main_loop import UnifiedBotLoop
    with patch('boss_bot.main_loop.BrowserManager'):
        lp = UnifiedBotLoop()
    lp._stop_event = threading.Event()
    lp._running = True
    lp._log = lambda *a: None
    lp._emit_wind = lambda *a, **k: None
    return lp


class ClassifyHealthTest:
    """验证码判据本身。"""

    def _cls(self):
        from boss_bot.page_handler import classify_health
        return classify_health

    def test_安全校验URL就是验证码页(self):
        # BOSS 触发风控时跳的这种地址，正文里不一定有"验证码"三个字
        assert self._cls()(
            "https://www.zhipin.com/web/geek/jobs?_security_check=1_1790639686255",
            "ok") == "captcha"

    def test_登录页算need_login不算验证码(self):
        assert self._cls()("https://www.zhipin.com/web/user/?ka=header-login",
                           "ok") == "need_login"

    def test_页面探针说captcha就是captcha(self):
        assert self._cls()("https://www.zhipin.com/web/geek/jobs", "captcha") == "captcha"

    def test_探针读不到不能兜底成健康(self):
        """旧代码把 None/未知一律当 ok，图形验证页就是这么漏过去的"""
        assert self._cls()("https://www.zhipin.com/web/geek/jobs", None) == "unknown"

    def test_正常页面算ok(self):
        assert self._cls()("https://www.zhipin.com/web/geek/jobs", "ok") == "ok"


class HealthWithoutChatHandlerTest:
    def test_没有聊天处理器也要能认出验证码(self):
        """回复引擎还没建时 _chat_handler 是 None，旧代码直接回 'ok'"""
        from boss_bot import page_handler as PH
        lp = make_loop()
        lp._chat_handler = None
        inst = MagicMock()
        inst.url = "https://www.zhipin.com/web/geek/jobs?_security_check=1_123"
        inst.run_js.return_value = "ok"
        lp.browser_manager.get_instance.return_value = inst
        with patch.object(PH, "classify_health",
                          side_effect=lambda url, probe: "captcha"):
            assert lp._check_health() == "captcha"


class CaptchaGateTest:
    """等人工 60 秒，超时跳过。"""

    def test_人工在时限内解掉就继续(self, monkeypatch):
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 5)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.1)
        lp = make_loop()
        seq = ["captcha", "captcha", "ok"]
        lp._check_health = lambda: seq.pop(0) if seq else "ok"
        assert lp._captcha_gate() is True
        assert lp._greet_paused is False

    def test_超时未解返回假但不永久暂停(self, monkeypatch):
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 0.3)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.1)
        lp = make_loop()
        lp._check_health = lambda: "captcha"
        assert lp._captcha_gate() is False
        assert lp._greet_paused is False, "一次超时就永久暂停，等于回到老 bug"

    def test_人工解掉后不等满上限(self, monkeypatch):
        """上限是"最多等多久"，不是固定 sleep：页面一恢复就要立刻走"""
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 60)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.1)
        lp = make_loop()
        seq = ["captcha", "captcha", "captcha", "ok"]
        lp._check_health = lambda: seq.pop(0)
        t0 = time.time()
        assert lp._captcha_gate() is True
        assert time.time() - t0 < 5

    def test_点停止要立刻结束等待(self, monkeypatch):
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 60)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.5)
        lp = make_loop()
        lp._check_health = lambda: "captcha"

        def stop_soon():
            time.sleep(0.6)
            lp._stop_event.set()
        threading.Thread(target=stop_soon, daemon=True).start()
        t0 = time.time()
        assert lp._captcha_gate() is False
        assert time.time() - t0 < 5, "停止按钮解不了等待，就是现在卡死的样子"

    def test_连续三次超时才暂停并说明原因(self, monkeypatch):
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 0.2)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.1)
        lp = make_loop()
        notices = []
        lp._emit_wind = lambda msg, wtype: notices.append((msg, wtype))
        lp._check_health = lambda: "captcha"
        for _ in range(ML.CAPTCHA_STRIKES_TO_PAUSE - 1):
            assert lp._captcha_gate() is False
        assert lp._greet_paused is False
        assert lp._captcha_gate() is False
        assert lp._greet_paused is True, "一直卡在验证页也该有个止损，但不能第一次就停"
        assert any("验证" in n[0] for n in notices), "必须告诉用户为什么停了"

    def test_人工解掉后清零连续计数(self, monkeypatch):
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 0.2)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.1)
        lp = make_loop()
        lp._check_health = lambda: "captcha"
        lp._captcha_gate()
        assert lp._captcha_strikes == 1
        lp._check_health = lambda: "ok"
        assert lp._captcha_gate() is True
        assert lp._captcha_strikes == 0


class RunJsAsExprTest:
    """BrowserInstance.run_js 吞掉了 as_expr，验证码探针其实从来没跑过。

    page_handler.check_health 是这么调的：run_js(JS, as_expr=True)，
    而 BrowserInstance.run_js(self, script, *args) 不收关键字参数
    → TypeError → 被 except 兜成 "unknown"。
    DrissionPage 那边 as_expr 是关键字-only，位置参数会被当成 JS 的入参。
    """

    def test_as_expr要转发给底层(self):
        from boss_bot.browser_launcher import BrowserInstance
        seen = {}

        class _Fake:
            def run_js(self, script, *args, as_expr=False):
                seen['as_expr'] = as_expr
                seen['args'] = args
                return "captcha"

        bi = BrowserInstance.__new__(BrowserInstance)
        bi._get_active = lambda: _Fake()
        assert bi.run_js("SOME_JS", as_expr=True) == "captcha"
        assert seen['as_expr'] is True
        assert seen['args'] == (), "as_expr 不能被当成 JS 位置参数传下去"

    def test_探针JS在真DrissionPage语义下要能返回字符串(self):
        from boss_bot.page_handler import CAPTCHA_PROBE_JS
        assert CAPTCHA_PROBE_JS.lstrip().startswith("("), \
            "探针是表达式，必须配 as_expr=True 才有返回值"
