"""账号"登录"入口（task #34）：新增完账号后要能只把 BOSS 登录页唤起来。

以前只有"启动账号"这一条路，点下去就跑整条投递流水线；用户想先登录再投递
就找不到入口。这里要的是：登录按钮只开浏览器+登录页+等人工登录+存 Cookie。
"""

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

INDEX_HTML = Path("flask-version/templates/index.html")
APP_PY = Path("flask-version/app.py")


def make_loop(account_index=1):
    from boss_bot.main_loop import UnifiedBotLoop
    with patch('boss_bot.main_loop.BrowserManager'):
        lp = UnifiedBotLoop(account_index=account_index)
    lp._log = lambda *a: None
    lp._stop_event = threading.Event()
    lp._running = False
    lp._emit_wind = lambda *a, **k: None
    return lp


class OpenLoginPageTest:
    def test_开的是BOSS登录页(self):
        lp = make_loop()
        inst = MagicMock()
        lp.browser_manager.get_instance.return_value = inst
        out = lp.open_login_page()
        assert out["status"] == "ok", out
        urls = [c.args[0] for c in inst.get.call_args_list]
        assert any("/web/user" in u for u in urls), f"没打开登录页: {urls}"

    def test_不启动投递线程(self):
        """点登录只是登录，不能顺手把打招呼/回复跑起来"""
        lp = make_loop()
        inst = MagicMock()
        lp.browser_manager.get_instance.return_value = inst
        lp.open_login_page()
        assert lp._running is False
        assert getattr(lp, "_greet_thread", None) is None

    def test_没浏览器时先把浏览器拉起来(self):
        lp = make_loop()
        lp.browser_manager.get_instance.return_value = None
        called = {}

        def fake_init():
            called["init"] = True
            lp.browser_manager.get_instance.return_value = MagicMock()
            return True

        lp._init_browser = fake_init
        out = lp.open_login_page()
        assert called.get("init"), "实例为空时必须先启动浏览器"
        assert out["status"] == "ok", out

    def test_浏览器起不来要如实报错(self):
        lp = make_loop()
        lp.browser_manager.get_instance.return_value = None
        lp._init_browser = lambda: False
        lp._login_reason = "browser_failed"
        out = lp.open_login_page()
        assert out["status"] == "error"
        assert out["message"], "错误必须带能照着修的文字"

    def test_正在运行时不抢占浏览器(self):
        """跑着的时候跳登录页会把流水线打断，这种情况要拒绝而不是照做"""
        lp = make_loop()
        lp._running = True
        inst = MagicMock()
        lp.browser_manager.get_instance.return_value = inst
        out = lp.open_login_page()
        assert out["status"] == "running"
        assert inst.get.call_count == 0

    def test_登录成功后按本账号存Cookie(self):
        lp = make_loop(account_index=1)
        lp._cookie_file = lambda: "/abs/zhipin_cookies_1.json"
        lp._login_event.set()
        inst = MagicMock()
        inst._get_all_cookies.return_value = [
            {"name": "wt2", "value": "x", "expires": time.time() + 86400}]
        lp._finish_manual_login(inst)
        lp.browser_manager.save_cookies.assert_called_once_with("/abs/zhipin_cookies_1.json")
        assert lp._logged_in is True
        assert lp._needs_login is False

    def test_停在登录页时不许顶掉本账号Cookie(self):
        """今天真实踩到的：假判登录成功后把登录页那份 cookie 存成文件，好会话没了"""
        lp = make_loop(account_index=1)
        lp._cookie_file = lambda: "/abs/zhipin_cookies_1.json"
        lp._login_event.set()
        inst = MagicMock()
        inst._get_all_cookies.return_value = [
            {"name": "abtest", "value": "1", "expires": -1}]
        lp._finish_manual_login(inst)
        lp.browser_manager.save_cookies.assert_not_called()

    def test_等不到登录就标超时不清Cookie(self):
        lp = make_loop()
        lp.config.login.wait_timeout = 0
        lp._cookie_file = lambda: "/abs/zhipin_cookies_1.json"
        lp._finish_manual_login(MagicMock())
        assert lp._login_reason == "login_timeout"
        lp.browser_manager.save_cookies.assert_not_called()


class LoginWaitTest:
    """登录等待的两个退出条件——都是别人依赖的行为，不能因为加了登录按钮就松掉。"""

    def _loop(self):
        import boss_bot.main_loop as ml
        lp = make_loop()
        lp.config.login.wait_timeout = 600
        return lp

    def test_停止信号打断等待(self):
        import boss_bot.main_loop as ml
        lp = self._loop()
        lp._stop_event.set()
        with patch.object(ml, "LOGIN_POLL_INTERVAL", 0.1):
            assert lp._wait_for_login(MagicMock(), "c.json") is False

    def test_运行循环停了就退出等待(self):
        """没在跑、也没人显式等登录时不能空转到超时"""
        import boss_bot.main_loop as ml
        lp = self._loop()
        lp._running = False
        with patch.object(ml, "LOGIN_POLL_INTERVAL", 0.1):
            assert lp._wait_for_login(MagicMock(), "c.json") is False

    def test_登录按钮的等待不受运行状态影响(self):
        """open_login_page 不启动流水线，_running 是假的，等待照样得进行"""
        import boss_bot.main_loop as ml
        lp = self._loop()
        lp._running = False
        lp._login_wait_active = True
        instance = MagicMock()
        instance._get_all_cookies.return_value = [
            {"name": "wt2", "value": "x", "expires": time.time() + 86400}]
        instance.url = "https://www.zhipin.com/web/geek/chat"
        with patch.object(ml, "LOGIN_POLL_INTERVAL", 0.1):
            assert lp._wait_for_login(instance, "c.json") is True

    def test_等待结束后标记复位(self):
        lp = make_loop()
        lp._login_event.set()
        lp._finish_manual_login(MagicMock())
        assert lp._login_wait_active is False


class LoginApiTest:
    def test_端点存在(self):
        src = APP_PY.read_text(encoding="utf-8")
        assert '@app.route("/api/accounts/<int:idx>/login"' in src

    def test_索引越界要挡掉(self):
        src = APP_PY.read_text(encoding="utf-8")
        body = src[src.index('/api/accounts/<int:idx>/login"'):]
        body = body[:body.index("\n@app.route", 10)] if "\n@app.route" in body[10:] else body
        assert "_validate_account_index" in body, "登录端点没校验账号索引"


class AccountPanelUiTest:
    """左侧列表只做新增+登录，切数据仍然只在右侧数据范围"""

    def test_账号行不再兼职切数据(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        block = html[html.index("function renderAccounts"):]
        block = block[:block.index("\nfunction ")]
        assert "switchAccount(" not in block, "左侧账号行仍在切数据范围，和右边重复"

    def test_账号行有登录按钮(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "loginAccount(" in html, "左侧没有登录入口"

    def test_登录按钮打的是新端点(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        block = html[html.index("function loginAccount"):]
        block = block[:block.index("\nfunction ")]
        assert "/login" in block

    def test_登录态有自动检测(self):
        """新增的账号默认红点，检测要能自己变绿，不能只靠手点"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert "refreshCookieStates(false)" in html, "没有周期刷新登录态"
