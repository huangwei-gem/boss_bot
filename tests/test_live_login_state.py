# -*- coding: utf-8 -*-
"""左侧账号的登录状态检测：能用真浏览器就用，不能就明说只读了文件。

为什么不做成"点一下就调 /api/accounts/<idx>/check_cookie"：那条会另起一个不带
端口与 profile 的浏览器（browser_launcher.py 里 `check_cookie_valid` 的老路径），
跟正在跑的会话抢同一个用户目录，还会 quit() 掉它 —— 等于检测一次就把登录态踢掉，
而"不许丢登录态"是这个项目的红线。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.test_config_hot_reload import flask_app  # noqa: F401  跨文件复用夹具


class LiveLoginStateTest:
    def _loop(self):
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp.account_index = 1
        lp._log = lambda *a, **k: None
        lp.browser_manager = MagicMock()
        return lp

    def test_浏览器没起来就返回无结论(self):
        lp = self._loop()
        lp.browser_manager.get_instance.return_value = None
        assert lp.live_login_state() is None

    def test_起来过就只读不导航(self):
        """读 URL + 浏览器里的登录项，绝不 get() —— 动了就是抢用户的页面"""
        lp = self._loop()
        inst = MagicMock()
        lp.browser_manager.get_instance.return_value = inst
        lp._login_state_read = lambda instance: "logged_in"
        assert lp.live_login_state() == "logged_in"
        assert not inst.get.called

    def test_读不到就当没结论而不是判失效(self):
        lp = self._loop()
        lp.browser_manager.get_instance.return_value = MagicMock()
        lp._login_state_read = MagicMock(side_effect=RuntimeError("断开"))
        assert lp.live_login_state() is None


class CookieStateEndpointTest:
    def _call(self, flask_app, monkeypatch, live):
        APP, _tmp = flask_app          # 夹具 return 的就是 flask-version/app.py 那个模块
        monkeypatch.setattr(APP, "_live_login_state", lambda idx: live)
        with APP.app.test_client() as c:
            r = c.get("/api/accounts/cookies?index=0&force=1")
            assert r.status_code == 200, (r.status_code, r.get_data()[:300])
            return r.get_json()["accounts"][0]

    def test_实测说已登录就不被文件结论压住(self, flask_app, monkeypatch):
        got = self._call(flask_app, monkeypatch, "logged_in")
        assert got["valid"] is True and got["logged_in"] is True
        assert got["source"] == "live", got
        assert "实测" in got["reason"], got["reason"]

    def test_实测说停在登录页就报失效(self, flask_app, monkeypatch):
        got = self._call(flask_app, monkeypatch, "need_login")
        assert got["valid"] is False and got["source"] == "live"

    def test_两路证据对不上时不硬判颜色(self, flask_app, monkeypatch):
        """valid=None → 界面是灰点，正好表达"看不准"，比蒙一个红/绿诚实"""
        got = self._call(flask_app, monkeypatch, "uncertain")
        assert got["valid"] is None, got
        assert "看不准" in got["reason"] or "对不上" in got["reason"], got["reason"]

    def test_没在跑就明说是读文件(self, flask_app, monkeypatch):
        got = self._call(flask_app, monkeypatch, None)
        assert got["source"] == "file", got
        assert "未联网核对" in got["reason"], got["reason"]
