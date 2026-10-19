# -*- coding: utf-8 -*-
"""「我已登录」到盘上那一段，每一步都得讲真话。

线上取证（2026-10-18）：
    11:58:09 [账号2] 导航后URL: https://www.zhipin.com/web/passport/zp/security.html?seed=…
    11:58:09 [账号2] [WARN] 访问岗位详情时被重定向到登录页
    11:58:09 [账号2] [INFO] 请重新登录，登录后点击「确认登录」
    12:03:09 [账号2] [ERROR] 登录超时
那页是 BOSS 的风控验证页，不是登录页（`login_state_of` 那两路证据早就把它算成
uncertain，只有 greet_engine 里裸着的一句 `"passport" in current_url` 会定罪）。
账号2 的两份登录项当时明明在盘上：

    zhipin_cookies_1.json  10-18 10:07:03  wt2/zp_at/bst/wbg 全在

他去点「我已登录」，从 11:58 到 12:03 后端一条痕迹都没有——既查不出请求到没到，
也查不出 Cookie 到底写没写。把三条写盘路径摊开看，没有一处会把"我没写成"说出口：

    page_handler._save_cookies   不判有没有登录项，读完是空就再兜底盲写一次，写完不回读
    greet_engine._save_cookies   拿 tab 自己那份 instance.cookies() 无条件覆盖
    main_loop._save_cookies_if_logged_in  有闸门，但同样不回读，且只管自动那条腿

这一轮把写盘收到一处：闸门（有未过期登录项）+ 备份 + 临时文件 + 回读验证，
任何一步不成都保持原文件不动，并把原因一路带到界面上。
"""

import io
import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.browser_launcher import (
    保存登录Cookie, 页面被踢到哪, 是风控验证页, has_live_auth_cookie, login_state_of)

SECURITY_URL = ("https://www.zhipin.com/web/passport/zp/security.html"
                "?seed=BdYsUlfnn&name=f45bbe80&ts=1792295878995"
                "&callbackUrl=%2Fjob_detail%2Fcc247514ee1e7a8b.html")
LOGIN_URL = "https://www.zhipin.com/web/user/?ka=header-login"
# 2026-10-18 13:20 只读 /json 取到的线上原样：账号2 两张标签页都停在这一页
VERIFY_URL = ("https://www.zhipin.com/web/passport/zp/verify.html"
              "?callbackUrl=https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fgeek%2Fjob-recommend")


def _登录项(**over):
    c = {"name": "zp_at", "value": "会话值", "expires": time.time() + 86400,
         "domain": ".zhipin.com"}
    c.update(over)
    return c


def _只写着录项(path, cookies):
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False)


def _读(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


class Test唯一写入口:
    """落盘这件事只许一处做，且只有两件事都成立才动用户的文件。"""

    def test_没有未过期登录项时一个字都不写(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            _只写着录项(target, [_登录项(value="上一轮的好会话")])
            成功, 说明 = 保存登录Cookie(
                target, [{"name": "abtest", "value": "1", "expires": -1},
                         {"name": "zp_at", "value": "", "expires": time.time() + 5},
                         {"name": "wt2", "value": "x", "expires": time.time() - 5}])
            assert 成功 is False
            assert _读(target)[0]["value"] == "上一轮的好会话", "闸门没拦住，好会话被顶掉了"
            assert "登录" in 说明, f"原因里要说清缺哪个登录项: {说明}"

    def test_回读不到登录项就不替换原文件(self):
        """写成功了不算成功——盘上那份能读出登录项才算（防止半截文件/编码坑）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            _只写着录项(target, [_登录项(value="上一轮的好会话")])
            with patch("boss_bot.browser_launcher._读回Cookie", return_value=[]):
                成功, 说明 = 保存登录Cookie(target, [_登录项()])
            assert 成功 is False
            assert _读(target)[0]["value"] == "上一轮的好会话"
            assert "回读" in 说明, f"要说清是回读这一步没过: {说明}"
            assert not os.path.exists(target + ".tmp"), "临时文件没清掉"

    def test_有登录项时写入并回读确认(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            _只写着录项(target, [_登录项(value="上一轮的好会话")])
            成功, 说明 = 保存登录Cookie(target, [_登录项(value="这一轮读到的")])
            assert 成功 is True, 说明
            assert _读(target)[0]["value"] == "这一轮读到的"
            assert "1 个" in 说明, f"说明要带上写了几条: {说明}"

    def test_会话级Cookie算活的(self):
        """BOSS 盘上那四份的 expires 是空的：不能因为读不到到期时间就判成过期。"""
        assert has_live_auth_cookie([_登录项(expires=-1)]) is True
        assert has_live_auth_cookie([_登录项(expires=None)]) is True

    def test_原文件不存在时也要闸门(self):
        """第一次登录那一下如果浏览器里根本没有登录项，也不能留一份废文件。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_9.json")
            成功, _ = 保存登录Cookie(target, [{"name": "abtest", "value": "1"}])
            assert 成功 is False
            assert not os.path.exists(target)


class Test三条腿都走这一处:
    """确认登录、自动登录、打招呼侧补存 —— 三条都得经过闸门。"""

    def _handler(self, cookies, tmp):
        from boss_bot.page_handler import BossChatHandler
        target = os.path.join(tmp, "zhipin_cookies_1.json")
        _只写着录项(target, [_登录项(value="上一轮的好会话")])
        with patch("boss_bot.page_handler.launch_browser"):
            h = BossChatHandler(cookie_file=target)
        h.browser = MagicMock()
        h._get_all_cookies = lambda: list(cookies)
        return h, target

    def test_浏览器里没有登录项时不覆盖也不兜底盲写(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            h, target = self._handler([{"name": "abtest", "value": "1", "expires": -1}], td)
            成功, 说明 = h._save_cookies()
            assert 成功 is False
            assert _读(target)[0]["value"] == "上一轮的好会话"
            assert h.browser.save_cookies.call_count == 0, \
                "读不到登录项还走 browser.save_cookies 兜底，等于闭眼覆盖"
            assert "登录" in 说明

    def test_确认存盘时写自己那份并带回说明(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            h, target = self._handler([_登录项(value="这一轮读到的")], td)
            成功, 说明 = h._save_cookies()
            assert 成功 is True, 说明
            assert _读(target)[0]["value"] == "这一轮读到的"
            assert "zhipin_cookies_1" in 说明

    def test_存盘没成时confirm_and_save不认已登录(self):
        """这条就是用户这次踩到的：点了「我已登录」，界面说成功了，盘上什么都没发生。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            h, _ = self._handler([{"name": "abtest", "value": "1", "expires": -1}], td)
            h.page = MagicMock()
            h._logged_in = False
            h._login_event.clear()
            assert h.confirm_and_save() is False
            assert h._logged_in is False, "Cookie 没落盘却把登录态认领了"
            assert not h._login_event.is_set(), "没写成还去叫醒等登录的那条线程"

    def test_存盘成了才认已登录(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            h, _ = self._handler([_登录项()], td)
            h.page = MagicMock()
            assert h.confirm_and_save() is True
            assert h._logged_in is True
            assert h._login_event.is_set()

    def test_打招呼侧补存也走闸门(self):
        """greet_engine 等完人工登录会自己补存一次，那份是 tab 级的，缺登录项。"""
        import tempfile
        from boss_bot.greet_engine import GreetEngine
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            _只写着录项(target, [_登录项(value="上一轮的好会话")])
            eng = GreetEngine.__new__(GreetEngine)
            eng._cookie_file = "zhipin_cookies_1.json"
            eng.browser_manager = MagicMock()
            inst = MagicMock()
            inst.cookies.return_value = [{"name": "abtest", "value": "1", "expires": -1}]
            inst._get_all_cookies.return_value = [{"name": "abtest", "value": "1"}]
            eng.browser_manager.get_instance.return_value = inst
            eng._log = lambda *a, **k: None
            with patch("boss_bot.greet_engine.resolve_path", return_value=target):
                eng._save_cookies()
            assert _读(target)[0]["value"] == "上一轮的好会话", "打招呼侧把好会话顶掉了"

    def test_三条腿调的是同一个写入口(self):
        import inspect
        from boss_bot.page_handler import BossChatHandler
        from boss_bot.greet_engine import GreetEngine
        from boss_bot.main_loop import UnifiedBotLoop
        for 名, fn in (("page_handler", BossChatHandler._save_cookies),
                       ("greet_engine", GreetEngine._save_cookies),
                       ("main_loop", UnifiedBotLoop._save_cookies_if_logged_in)):
            段 = inspect.getsource(fn)
            assert "保存登录Cookie" in 段 or "save_cookies" in 段, \
                f"{名} 那条腿没走统一的写入口，它自己判断没有的意义"


class Test风控页不算掉登录:
    """security.html 要人工滑一次，Cookie 好着；把它判成掉登录会让人白等 5 分钟。"""

    def test_风控页判成risk(self):
        assert 是风控验证页(SECURITY_URL) is True
        assert 页面被踢到哪(SECURITY_URL, [_登录项()]) == "risk"
        assert 页面被踢到哪(SECURITY_URL, []) == "risk"

    def test_真登录墙仍然判成login_wall(self):
        assert 页面被踢到哪(LOGIN_URL, []) == "login_wall"

    def test_登录页但浏览器里还有登录项时不定罪(self):
        """SPA 跳登录页那几秒里 Cookie 常常还在——两路不一致就不许动会话。"""
        assert 页面被踢到哪(LOGIN_URL, [_登录项()]) == "other"

    def test_正常页面判other(self):
        assert 页面被踢到哪("https://www.zhipin.com/job_detail/abc.html",
                            [_登录项()]) == "other"

    def test_打招呼腿用同一份判据不再裸拼passport(self):
        src = io.open(os.path.join(ROOT, "boss_bot", "greet_engine.py"),
                      encoding="utf-8").read()
        assert 'if "passport" in current_url or "login" in current_url' not in src, \
            "还在拿一眼 URL 定罪"
        assert 'any(k in landed for k in ("login", "passport", "/web/user"))' not in src
        assert "页面被踢到哪" in src, "这一腿没接上统一的判据"


class Test判据只有一份:
    def test_全仓库只有一处把cookie写盘(self):
        命中 = []
        for 文件 in ("boss_bot/browser_launcher.py", "boss_bot/greet_engine.py",
                     "boss_bot/page_handler.py", "boss_bot/main_loop.py"):
            src = io.open(os.path.join(ROOT, 文件), encoding="utf-8").read()
            if "json.dump(cookies" in src:
                命中.append(文件)
        assert 命中 == ["boss_bot/browser_launcher.py"], f"还有别处在直接写 Cookie 文件: {命中}"

    def test_登录态两路证据只定义一次(self):
        for 函数 in ("has_live_auth_cookie", "login_state_of"):
            处 = []
            for 文件 in ("boss_bot/browser_launcher.py", "boss_bot/main_loop.py",
                         "boss_bot/greet_engine.py", "boss_bot/page_handler.py"):
                src = io.open(os.path.join(ROOT, 文件), encoding="utf-8").read()
                if ("def %s(" % 函数) in src:
                    处.append(文件)
            assert 处 == ["boss_bot/browser_launcher.py"], f"{函数} 出现了多处: {处}"

    def test_main_loop还能按老名字用(self):
        """挪走之后 main_loop 里那些调用点不能一起废掉。"""
        import boss_bot.main_loop as ML
        assert ML.login_state_of is login_state_of
        assert ML.has_live_auth_cookie is has_live_auth_cookie

    def test_浏览器没起来时不写盘(self):
        import tempfile
        from boss_bot.greet_engine import GreetEngine
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "zhipin_cookies_1.json")
            _只写着录项(target, [_登录项(value="上一轮的好会话")])
            eng = GreetEngine.__new__(GreetEngine)
            eng._cookie_file = "zhipin_cookies_1.json"
            eng.browser_manager = MagicMock()
            eng.browser_manager.get_instance.return_value = None
            eng._log = lambda *a, **k: None
            with patch("boss_bot.greet_engine.resolve_path", return_value=target):
                eng._save_cookies()
            assert _读(target)[0]["value"] == "上一轮的好会话"


class Test面板讲真话:
    """点了按钮之后，日志里必须查得到这一句到过哪儿、结果是什么。"""

    def test_两个确认登录端点都留来源(self):
        src = io.open(os.path.join(ROOT, "flask-version", "app.py"), encoding="utf-8").read()
        i = src.index("def api_account_confirm_login")
        j = src.index("def api_account_login")
        段 = src[i:j]
        全局 = src[src.index("def api_confirm_login"):src.index("def api_confirm_login") + 600]
        assert "_control_trace()" in 段, "账号级确认登录没有来源留痕"
        assert "_control_trace()" in 全局, "全局确认登录没有来源留痕"
        assert "logged_in" in 段, "端点没把后端的真实结果带给前端"

    def test_失败时返回的status不是ok(self):
        src = io.open(os.path.join(ROOT, "flask-version", "app.py"), encoding="utf-8").read()
        i = src.index("def api_account_confirm_login")
        段 = src[i:i + 1200]
        assert "logged_in" in 段, "端点把结果写死了，不问 loop.confirm_login 认没认领"

    def test_前端先看HTTP状态再看status(self):
        src = io.open(os.path.join(ROOT, "flask-version", "templates", "index.html"),
                      encoding="utf-8").read()
        i = src.index("function confirmLoginAs")
        段 = src[i:i + 1400]
        assert "!r.ok" in 段, "后端 404/500 时前端照报成功"
        assert "data.status" in 段, "后端报了 error 也照样弹绿色成功"


class Test主循环确认登录:
    def _loop(self):
        import boss_bot.unified_config as UC
        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop.account_index = 1
        loop._log = lambda *a, **k: None
        loop._popup_login_window = lambda: None
        loop._login_window_shown = True
        return loop

    def test_Cookie没落盘就不清needs_login(self):
        loop = self._loop()
        loop._needs_login = True
        loop._logged_in = False
        loop._chat_handler = MagicMock()
        loop._chat_handler.confirm_and_save.return_value = False
        loop._greet_engine = None
        assert loop.confirm_login() is False
        assert loop._needs_login is True, "没存成就撤掉「要登录」的红点"
        assert loop._logged_in is False
        assert not loop._login_event.is_set(), "等登录的那条线程不该被叫醒"

    def test_落盘成功才认领已登录(self):
        loop = self._loop()
        loop._needs_login = True
        loop._chat_handler = MagicMock()
        loop._chat_handler.confirm_and_save.return_value = True
        loop._greet_engine = None
        assert loop.confirm_login() is True
        assert loop._needs_login is False
        assert loop._logged_in is True
        assert loop._login_event.is_set()


class Test验证页那一类:
    """线上现在卡的就是这一页：它是"要人滑一下"，不是"要人重新扫码"。

    2026-10-18 13:20 只读 9223：两张标签页都是「安全验证 - BOSS直聘」
    /web/passport/zp/verify.html。三处代码里两处把它说成掉登录
    （greet_engine 的 `"passport" in url`、classify_health 的同一句），
    于是一边喊"请重新登录"一边每次 park 5 分钟，而他根本看不到这张页
    ——那个号是无头跑的，桌面上没有窗口。
    """

    def test_线上verify页归进风控这一类(self):
        assert 是风控验证页(VERIFY_URL) is True
        assert 页面被踢到哪(VERIFY_URL, [_登录项()]) == "risk"
        assert 页面被踢到哪(VERIFY_URL, []) == "risk"

    def test_健康检查把verify页判成captcha而不是need_login(self):
        from boss_bot.page_handler import classify_health
        探针没证据 = json.dumps({"boxes": [], "words": []})
        assert classify_health(VERIFY_URL, 探针没证据) == "captcha"
        assert classify_health(SECURITY_URL, 探针没证据) == "captcha"

    def test_真登录页还是need_login(self):
        from boss_bot.page_handler import classify_health
        探针没证据 = json.dumps({"boxes": [], "words": []})
        assert classify_health(LOGIN_URL, 探针没证据) == "need_login"
        assert classify_health("https://passport.zhipin.com/login", 探针没证据) == \
            "need_login"

    def test_落地判性真的读浏览器里的登录项(self):
        from boss_bot.greet_engine import GreetEngine
        eng = GreetEngine.__new__(GreetEngine)
        eng._log = lambda *a, **k: None
        inst = MagicMock()
        inst.url = VERIFY_URL
        inst._get_all_cookies.return_value = [_登录项()]
        assert eng._落地后判性(inst) == "risk"
        inst.url = LOGIN_URL
        assert eng._落地后判性(inst) == "other", "Cookie 还在就不许定罪"
        inst._get_all_cookies.return_value = []
        assert eng._落地后判性(inst) == "login_wall"

    def test_风控页不许叫重新登录(self):
        """这条锁的是现象本身：那句话会让人白点「我已登录」。"""
        src = io.open(os.path.join(ROOT, "boss_bot", "greet_engine.py"),
                      encoding="utf-8").read()
        i = src.index("_落地后判性(instance)")
        段 = src[i:i + 1200]
        assert 段.index("risk") < 段.index("login_wall"), "风控页要先判掉"
        assert "_login_required_cb" in 段[段.index("login_wall"):]
        assert "_login_required_cb" not in 段[:段.index("login_wall")], \
            "risk 那一支不该叫人工登录"

    def test_验证码闸门要把窗口摆出来(self):
        """无头那侧桌面上没有这个号，「请在浏览器窗口里手动完成」是句空话。"""
        import boss_bot.unified_config as UC
        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        摆过 = []
        loop._ensure_login_window = lambda 侧="", 导航=True: 摆过.append((侧, 导航)) or True
        loop._log = lambda *a, **k: None
        loop._emit_wind = lambda *a, **k: None
        loop._check_health = lambda side="reply", 安静=False: "captcha"
        loop._captcha_strikes = 0
        loop._running = True
        loop._验证码窗口已摆 = False
        loop._stop_event.set()          # 立刻结束等待，别真睡 60 秒
        assert loop._captcha_gate("greet") is False
        assert 摆过 and 摆过[0][1] is False, \
            "验证页要留在原地给他滑，导航=True 会把他踢回登录页"

    def test_摆了窗口之后等待要够他走过来(self):
        from boss_bot.main_loop import UnifiedBotLoop, CAPTCHA_WAIT_SECONDS
        assert UnifiedBotLoop._验证码等待秒(False) == CAPTCHA_WAIT_SECONDS
        assert UnifiedBotLoop._验证码等待秒(True) >= 180, \
            "窗口刚摆出来，60 秒不够他从座位上走到机器前"

    def test_窗口只摆一次(self):
        import boss_bot.unified_config as UC
        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        摆过 = []
        loop._ensure_login_window = lambda 侧="", 导航=True: 摆过.append(侧)
        loop._log = lambda *a, **k: None
        loop._emit_wind = lambda *a, **k: None
        loop._check_health = lambda side="reply", 安静=False: "ok"
        loop._captcha_strikes = 0
        loop._running = True
        loop._验证码窗口已摆 = True
        loop._finish_login_window = lambda: None
        assert loop._captcha_gate("greet") is True
        assert 摆过 == [], "已经摆过就不该再重开一次浏览器"


if __name__ == "__main__":
    unittest.main(verbosity=2)
