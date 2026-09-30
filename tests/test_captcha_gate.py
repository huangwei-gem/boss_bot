# -*- coding: utf-8 -*-
"""风控/验证码：人工验证等 60 秒，超时跳过当前任务。

用户要的语义很具体："碰到验证就让我手动过，超过 1 分钟我没反应就跳过"。

2026-09-30 12:20 的两个号被打招呼停了 3 次，而 BOSS 窗口里根本没有验证图。
逐标签页只读取证（tools/captcha_false_positive_check.py）+ 当日日志给出的结论：

1. `?_security_check=1_…` 是 BOSS 的**静默风控参数**，页面照样渲染、照样能发招呼语
   （12:08:24 带这个参数落地，12:08:25 读到 JD、12:08:27 点中"立即沟通"、
   12:08:31 招呼语已发出）。以前 URL 一命中就定罪，而这个标记不会自己消失，
   于是 60 秒永远等不到"已恢复"→ 三次 → 打招呼暂停。
2. 打招呼侧 `_check_health` 调 `run_js(CAPTCHA_PROBE_JS)` 没带 `as_expr=True`，
   实测返回 `undefined`（同一份 JS 带上就有值）——后端从来拿不到页面证据，
   只有第 1 条那一个信号，所以误判无法自证清白。
3. 探针正文匹配裸"验证码"三个字：岗位 JD、HR 消息、登录框的"获取验证码"
   按钮里都有这三个字，命中就定罪。

所以判据收敛成一句：页面上真有一张要人操作的东西（可见挑战容器 / 强文案），
才算验证码。URL 标记只能当证据记进日志。
"""
import json
import threading
import time
from unittest.mock import MagicMock, patch

JOBS = "https://www.zhipin.com/web/geek/jobs"

# 挑战容器实测：(选择器, 宽, 高, display, visibility)
VISIBLE_BOX = (".nc-container", 320, 120, "block", "visible")
HIDDEN_BOX = (".verify-box", 0, 0, "none", "visible")


def _ev(boxes=(), words=()):
    """拼一份探针返回值——探针只报证据，定罪在 python 侧。"""
    return json.dumps({
        "boxes": [{"sel": s, "w": w, "h": h, "display": d, "visibility": v}
                  for s, w, h, d, v in boxes],
        "words": list(words),
    })


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
    """验证码定罪本身。"""

    def _cls(self):
        from boss_bot.page_handler import classify_health
        return classify_health

    def test_可见挑战容器就是验证码(self):
        assert self._cls()(JOBS, _ev(boxes=[VISIBLE_BOX])) == "captcha"

    def test_强文案就是验证码(self):
        assert self._cls()(JOBS, _ev(words=["安全验证"])) == "captcha"

    def test_隐藏的容器不算验证码(self):
        """BOSS 正常页面上也挂着 .verify-box 这类零尺寸空壳"""
        assert self._cls()(JOBS, _ev(boxes=[HIDDEN_BOX])) == "ok"

    def test_URL带security_check但页面可用_不算验证码(self):
        """这条就是 12:20 停打招呼的那个误判"""
        assert self._cls()(JOBS + "?_security_check=1_1789877299570", _ev()) == "ok"

    def test_security_check页上真有验证_仍然算验证码(self):
        """不能因为URL不可靠就整个丢掉：页面自己承认在验证就要停"""
        assert self._cls()(JOBS + "?_security_check=1_1789877299570",
                           _ev(boxes=[VISIBLE_BOX])) == "captcha"

    def test_登录页算need_login不算验证码(self):
        assert self._cls()("https://www.zhipin.com/web/user/?ka=header-login",
                           _ev()) == "need_login"

    def test_登录页上盖了验证图_先判验证码(self):
        """判成 need_login 会去归档 Cookie 让人重扫，而实情只差过一次滑块"""
        assert self._cls()("https://www.zhipin.com/web/user/",
                           _ev(words=["请完成验证"])) == "captcha"

    def test_探针读不到不能兜底成健康(self):
        """旧代码把 None/未知一律当 ok，图形验证页就是这么漏过去的"""
        assert self._cls()(JOBS, None) == "unknown"

    def test_正常页面算ok(self):
        assert self._cls()(JOBS, _ev()) == "ok"


class OneSourceOfTruthTest:
    """判据只能有一处：以前三套各判各的，后端定罪、页面没影、前端提示验证码。"""

    def test_打招呼侧探针要带as_expr(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._check_health)
        assert "run_js(CAPTCHA_PROBE_JS, as_expr=True)" in src, \
            "不带 as_expr 实测返回 undefined，页面证据一条都拿不到"

    def test_greet侧不许再用URL单独定罪(self):
        from pathlib import Path
        src = Path("boss_bot/greet_engine.py").read_text(encoding="utf-8")
        assert "_security_check" not in src, \
            "打招呼侧两处拿 URL 标记定罪，落地页明明可用也会被说成验证码"

    def test_裸验证码不再写进关键词表(self):
        from boss_bot import greet_engine as GE
        assert not hasattr(GE, "WIND_CONTROL_CAPTCHA_KEYWORDS"), \
            "这张表里裸着「验证码」三个字，而且从来没被调用过"

    def test_强文案表里没有裸验证码(self):
        from boss_bot.page_handler import CAPTCHA_STRONG_WORDS
        assert "验证码" not in CAPTCHA_STRONG_WORDS
        assert "安全验证" in CAPTCHA_STRONG_WORDS

    def test_提示要带上判据(self):
        """只说"触发人机验证"，用户没法判断是不是又误判了"""
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        assert "self._health_why" in inspect.getsource(UnifiedBotLoop._captcha_gate)

    def test_探针的选择器与文案由同一份常量拼出(self):
        from boss_bot import page_handler as PH
        for w in PH.CAPTCHA_STRONG_WORDS:
            assert w in PH.CAPTCHA_PROBE_JS
        for s in PH.CAPTCHA_BOX_SELECTORS:
            assert s in PH.CAPTCHA_PROBE_JS


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


class StickySecurityCheckTest:
    """整件事的复现：地址带风控参数、页面完全可用，却把打招呼停了 3 次。"""

    STICKY = "https://www.zhipin.com/job_detail/x.html?_security_check=1_1789877299570"

    def _loop_on(self, probe, url=STICKY):
        lp = make_loop()
        inst = MagicMock()
        inst.url = url
        inst.run_js.return_value = probe
        lp.browser_manager.get_search_page.return_value = inst
        return lp

    def test_风控参数但页面可用_不算验证码(self):
        lp = self._loop_on(_ev())
        assert lp._check_health(side="greet") == "ok"

    def test_风控参数且页面真有挑战_算验证码(self):
        lp = self._loop_on(_ev(boxes=[VISIBLE_BOX]))
        assert lp._check_health(side="greet") == "captcha"

    def test_误判不会累计strikers(self):
        """strikers 只能由真验证码攒出来，否则三次误判就等于停了打招呼"""
        from boss_bot import main_loop as ML
        lp = self._loop_on(_ev())
        lp._check_health = lambda side="greet": "ok"
        assert lp._check_health(side="greet") != "captcha"
        assert lp._captcha_strikes == 0
        assert ML.CAPTCHA_STRIKES_TO_PAUSE == 3


class CaptchaGateTest:
    """等人工 60 秒，超时跳过。"""

    def test_人工在时限内解掉就继续(self, monkeypatch):
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 5)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.1)
        lp = make_loop()
        seq = ["captcha", "captcha", "ok"]
        lp._check_health = lambda side="reply": seq.pop(0) if seq else "ok"
        assert lp._captcha_gate() is True
        assert lp._greet_paused is False

    def test_超时未解返回假但不永久暂停(self, monkeypatch):
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 0.3)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.1)
        lp = make_loop()
        lp._check_health = lambda side="reply": "captcha"
        assert lp._captcha_gate() is False
        assert lp._greet_paused is False, "一次超时就永久暂停，等于回到老 bug"

    def test_人工解掉后不等满上限(self, monkeypatch):
        """上限是"最多等多久"，不是固定 sleep：页面一恢复就要立刻走"""
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 60)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.1)
        lp = make_loop()
        seq = ["captcha", "captcha", "captcha", "ok"]
        lp._check_health = lambda side="reply": seq.pop(0)
        t0 = time.time()
        assert lp._captcha_gate() is True
        assert time.time() - t0 < 5

    def test_点停止要立刻结束等待(self, monkeypatch):
        from boss_bot import main_loop as ML
        monkeypatch.setattr(ML, "CAPTCHA_WAIT_SECONDS", 60)
        monkeypatch.setattr(ML, "CAPTCHA_POLL_SECONDS", 0.5)
        lp = make_loop()
        lp._check_health = lambda side="reply": "captcha"

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
        lp._check_health = lambda side="reply": "captcha"
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
        lp._check_health = lambda side="reply": "captcha"
        lp._captcha_gate()
        assert lp._captcha_strikes == 1
        lp._check_health = lambda side="reply": "ok"
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
