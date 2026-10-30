# -*- coding: utf-8 -*-
"""整页加载这一族：地板、加载模式、以及"改快之后不许变空"。

现场依据（都是本轮从日志与探针里数出来的，不是推测）：
- 10-23~10-25 三天日志里能数出来的整页加载共 792 次，其中 11 个"同一秒两号各发一发"
  ——两个号走同一个出口 IP，风控按 IP 记，所以地板必须按进程算，不按号算。
- 2026-10-24 11:01:20,164「正在访问主站」→ 11:03:28,293「主站访问完成」= 128 秒；
  同一趟四次整页加载各 126~135 秒，启动 527 秒里 508 秒都花在"等图片和统计脚本加载完"。
- 探针实测（一次性无头 9602 + 临时档案，不带账号 Cookie）：load_mode=eager 之后
  主站 4.8 秒、搜索页 1.6 秒，readyState 仍是 complete、HTML 读得出 50 万/1.3 万字。
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import (          # noqa: E402
    BrowserInstance, _加载模式, 加载模式取值, 整页加载地板秒, 整页加载要等秒,
    准整页加载, 设置整页加载地板, reset_page_load_throttle)
from tests.test_config_hot_reload import flask_app  # noqa: F401  跨文件复用面板夹具


@pytest.fixture(autouse=True)
def _地板复位():
    """每条用例都从"没装地板、没加载过"开始，跑完也恢复原样。"""
    原地板, 原上次 = None, None
    from boss_bot import browser_launcher as BL
    原地板 = BL._SHARED_PAGE_LOAD_STATE["地板"]
    原上次 = BL._SHARED_PAGE_LOAD_STATE["上次"]
    reset_page_load_throttle()
    设置整页加载地板(0)
    yield
    BL._SHARED_PAGE_LOAD_STATE["地板"] = 原地板
    BL._SHARED_PAGE_LOAD_STATE["上次"] = 原上次


class Test地板判据:
    def test_第一趟绝不等(self):
        """上次=0 是"这个进程还没加载过"。真按 0+地板-现在 算，在任何一个小钟面上
        都会等出一个天文数字，两条腿当场冻死——这条哨兵和 打开要等秒() 同一个理由。
        小钟面那句才是这条门的牙：拿 time.time() 当"现在"时，负数被 max 折成 0，
        把哨兵删掉门照样绿（变异实测过一轮，那是一条摆设）。"""
        assert 整页加载要等秒(0.0, 5.0, 30.0) == 0.0
        assert 整页加载要等秒(0.0, time.time(), 30.0) == 0.0

    def test_地板为0就是不节流(self):
        assert 整页加载要等秒(time.time(), time.time(), 0) == 0.0
        assert 整页加载要等秒(100.0, 110.0, -5) == 0.0

    def test_窗口内还差几秒就说几秒(self):
        assert 整页加载要等秒(100.0, 103.0, 5.0) == 2.0

    def test_等够了归零(self):
        assert 整页加载要等秒(100.0, 105.0, 5.0) == 0.0

    def test_装完要能读回来且夹到0到60(self):
        """"改了必须生效"：装一个值读回来不是那个值，就等于界面写了后端不认。"""
        assert 设置整页加载地板(3) == 3.0
        assert 整页加载地板秒() == 3.0
        assert 设置整页加载地板(9999) == 60.0     # 再大就不是摊平而是停腿
        assert 设置整页加载地板(-7) == 0.0
        assert 设置整页加载地板("不是数") == 0.0   # 坏值不许把地板变成 NaN


class Test真会等:
    def test_不够地板真的要睡(self):
        """门要能真的失败：只比函数返回值不睡，测不出"两条腿同一秒各发一发"没了。"""
        设置整页加载地板(0.4)
        reset_page_load_throttle()
        准整页加载("https://www.zhipin.com/a")          # 第一趟不等
        开 = time.time()
        等成 = 准整页加载("https://www.zhipin.com/b")
        实际 = time.time() - 开
        assert 等成 > 0, "第二发没被地板挡住"
        assert 实际 >= min(0.4, 等成) * 0.5, (实际, 等成)

    def test_地板为0时一秒都不多等(self):
        """反向哨兵：关掉节流就必须真的不睡，否则这条开关是个摆设。"""
        设置整页加载地板(0)
        准整页加载("https://www.zhipin.com/a")
        开 = time.time()
        for _ in range(5):
            准整页加载("https://www.zhipin.com/b")
        assert time.time() - 开 < 0.2


class Test走对出口:
    def _实例(self, 假页):
        return BrowserInstance(chrome_page=假页)

    def test_BrowserInstance的get与new_tab都踩地板(self, monkeypatch):
        from boss_bot import browser_launcher as BL
        踩 = []
        monkeypatch.setattr(BL, "准整页加载",
                            lambda url, 记=None: 踩.append(url) or 0.0)

        class 假页:
            def get(self, url):
                return True

            def new_tab(self, url=""):
                return self

        for 方法, 参数 in (("get", "https://www.zhipin.com/x"),
                          ("new_tab", "https://www.zhipin.com/y")):
            踩.clear()
            实例 = self._实例(假页())
            getattr(实例, 方法)(参数)
            assert 踩 == [参数], "%s 没走地板" % 方法

    def test_new_tab开空白页不冒充一次加载(self, monkeypatch):
        """没有 url 就没发整页加载，不该占掉地板。"""
        from boss_bot import browser_launcher as BL
        踩 = []
        monkeypatch.setattr(BL, "准整页加载", lambda url, 记=None: 踩.append(url) or 0.0)

        class 假页:
            def new_tab(self, url=""):
                return self

        self._实例(假页()).new_tab()
        assert 踩 == []

    def test_页面处理器的导航走同一个地板(self, monkeypatch):
        """page_handler 是 `from ... import 准整页加载` 绑进自己命名空间的，
        所以要打在它自己的名字上——打错地方这条门会假绿。"""
        from boss_bot import page_handler as PH
        踩 = []
        monkeypatch.setattr(PH, "准整页加载",
                            lambda url, 记=None: 踩.append(url) or 0.0)
        h = PH.BossChatHandler.__new__(PH.BossChatHandler)

        class 假页:
            url = "https://www.zhipin.com/web/geek/chat"

            def get(self, u):
                踩.append("真加载:" + u)

        h.page = 假页()
        h._导航("https://www.zhipin.com/aaa")
        assert 踩[:1] == ["https://www.zhipin.com/aaa"], "地板先于加载"
        assert 踩[1] == "真加载:https://www.zhipin.com/aaa"


class Test加载模式:
    def test_空值按新默认eager且不念警(self, caplog):
        assert _加载模式("") == "eager"
        assert _加载模式(None) == "eager"

    def test_认得的三个词照收(self):
        for v in 加载模式取值:
            assert _加载模式(v) == v
            assert _加载模式(v.upper()) == v

    def test_认不了的退回eager并且说一句(self, caplog):
        """填错一个字母不该悄悄变成 none（发了导航立刻读空 DOM）。"""
        with caplog.at_level("WARNING"):
            assert _加载模式("eggar") == "eager"
        assert "加载模式" in caplog.text

    def test_两条启动路径都把模式装进浏览器参数(self):
        """mac 与 Windows 两条启动路径以前是手抄的两份参数，漏一条就等于那台机器
        还按 127 秒加载——判据打在"两边都真的调了 set_load_mode"上。"""
        源 = (ROOT / "boss_bot" / "browser_launcher.py").read_text(encoding="utf-8")
        Windows段 = 源.split("def _launch_windows")[1].split("class BrowserManager")[0]
        mac段 = 源.split("def _launch_macos")[1].split("def _launch_windows")[0]
        for 名, 段 in (("Windows", Windows段), ("macOS", mac段)):
            assert "co.set_load_mode(_加载模式(load_mode))" in 段, 名 + " 没装加载模式"
            assert "load_mode: str" in 段, 名 + " 的签名没接这个参数"

    def test_启动器把配置里的模式转给底层(self):
        源 = (ROOT / "boss_bot" / "browser_launcher.py").read_text(encoding="utf-8")
        assert "load_mode=self._load_mode," in 源.split("self._instance = launch_browser(")[1]
        assert "self._load_mode = _加载模式(" in 源


class Test等卡片挂出来:
    """eager 把 126 秒压到 1.6 秒，代价是脚本渲染的列表可能还没挂出来。
    这一族就是补那句"再给它几秒"，而且等不到不许谎称等到了。"""

    def _引擎(self):
        from boss_bot.greet_engine import GreetEngine
        e = GreetEngine.__new__(GreetEngine)
        e._log = lambda *a, **k: None
        return e

    def test_第二拍挂出来就算等到(self):
        拍 = [0]

        class 假实例:
            def eles(self, sel):
                拍[0] += 1
                return ["一张卡"] if 拍[0] >= 2 else []

        assert self._引擎()._等岗位卡片挂出来(假实例(), 上限秒=3) is True
        assert 拍[0] == 2

    def test_等不到要说一句并且回假(self):
        喊 = []

        class 假实例:
            def eles(self, sel):
                return []

        e = self._引擎()
        e._log = lambda 级, 话="": 喊.append(话)
        开 = time.time()
        assert e._等岗位卡片挂出来(假实例(), 上限秒=1) is False
        assert 0.9 <= time.time() - 开 < 2.5, "等待必须有界，也不许一拍都不等"
        assert any("岗位卡片" in 话 for 话 in 喊)

    def test_页面断开不陪它等满(self):
        """多等也不会自己好，交给上层重连；这里是"不许把断开咽成超时"的门。"""
        喊 = []

        class 假实例:
            def eles(self, sel):
                raise RuntimeError("页面断开")

        e = self._引擎()
        e._log = lambda 级, 话="": 喊.append(话)
        开 = time.time()
        assert e._等岗位卡片挂出来(假实例(), 上限秒=5) is False
        assert time.time() - 开 < 1.0
        assert 喊 == []

    def test_解析列表真的调用它(self):
        """判据打在调用处，而且要打在"没被注释掉的那一行"上：光比字符串，
        `pass  # self._等岗位卡片挂出来(instance)` 这种写法照样能骗过去（变异实测过）。"""
        源 = (ROOT / "boss_bot" / "greet_engine.py").read_text(encoding="utf-8")
        行 = 源.split("def _parse_job_list")[1].split("\n")
        找 = 下一行 = None
        for i, 文 in enumerate(行):
            if "instance.get(search_url)" in 文 and not 文.strip().startswith("#"):
                找 = i
                break
        assert 找 is not None, "抓取腿导航搜索页那一行找不到了（判据该跟着改）"
        for 文 in 行[找:找 + 12]:
            if 文.strip().startswith("self._等岗位卡片挂出来(instance)"):
                下一行 = 文
                break
        assert 下一行 is not None, "eager 之后没人等渲染：整页加载快了，读到的却是空列表"


class Test岗位详情也踩地板:
    def test_走JS的那一发自己踩(self):
        """打开岗位详情用的是 window.location.href，不经过 BrowserInstance.get，
        不自己踩一下，另一条腿就还在同一秒各发一发。"""
        源 = (ROOT / "boss_bot" / "greet_engine.py").read_text(encoding="utf-8")
        块 = 源.split('self._log("INFO", f"导航到: {url}")')[1][:600]
        assert '准整页加载(url' in 块, "JS 导航没踩地板"
        assert "window.location.href" in 块, "这一发确实是 JS 导航（判据还对不上别改）"


class Test配置三端:
    def _配置(self):
        from boss_bot.unified_config import UnifiedConfig
        return UnifiedConfig()

    def test_默认值与代码里写死的一致(self):
        c = self._配置()
        assert c.greet.rate_limit.page_load_min_interval == 3
        assert c.browser.load_mode == "eager"

    def test_写进去读得出来且坏值不照收(self):
        c = self._配置()
        c._apply_bot_config({"rate_limit": {"page_load_min_interval": 9999},
                            "browser": {"load_mode": "EGGAR"}})
        assert c.greet.rate_limit.page_load_min_interval == 60
        assert c.browser.load_mode == "eager", "认不了的词不许改加载模式"
        c._apply_bot_config({"browser": {"load_mode": "normal"}})
        assert c.browser.load_mode == "normal"

    def test_序列化要带出这两个键(self):
        d = self._配置().to_dict()
        assert d["rate_limit"]["page_load_min_interval"] == 3
        assert d["browser"]["load_mode"] == "eager"

    def test_界面有输入框且写的键后端读得到(self):
        html = (ROOT / "flask-version" / "templates" / "index.html").read_text(
            encoding="utf-8")
        assert 'id="pageLoadMinInterval"' in html
        assert 'id="advLoadMode"' in html
        assert "page_load_min_interval" in html
        assert "load_mode" in html
        assert "page_timeout_min_interval" not in html      # 拼错的键写了也没人读

    def test_一致性脚本认识这两个键(self):
        源 = (ROOT / "tools" / "consistency_test" / "run_consistency_test.py").read_text(
            encoding="utf-8")
        assert "page_load_min_interval" in 源
        assert "load_mode" in 源


class Test检测Cookie不抢页面:
    """浏览器正在跑的时候，"检测登录态"只问它，不再另起一台多撞两次整页加载。"""

    def _模块(self, flask_app):
        return flask_app[0]

    def test_号在跑时不再起探测浏览器(self, flask_app, monkeypatch):
        APP = self._模块(flask_app)
        monkeypatch.setattr(APP, "_live_login_state", lambda idx: "logged_in")
        起了 = []
        import boss_bot.browser_launcher as BL
        monkeypatch.setattr(BL, "check_cookie_valid",
                            lambda **kw: 起了.append(kw) or {})
        with APP.app.test_client() as c:
            r = c.get("/api/accounts/0/check_cookie")
            assert r.status_code == 200, (r.status_code, r.get_data()[:200])
            据 = r.get_json()
        assert 起了 == [], "浏览器在跑还另起一台探测=白撞两次整页加载"
        assert 据["valid"] is True and 据["source"] == "logged_in"

    def test_看不准就说不准不蒙绿(self, flask_app, monkeypatch):
        APP = self._模块(flask_app)
        monkeypatch.setattr(APP, "_live_login_state", lambda idx: "uncertain")
        with APP.app.test_client() as c:
            据 = c.get("/api/accounts/0/check_cookie").get_json()
        assert 据["valid"] is None, "两路证据对不上时不许蒙一个颜色"
        assert "看不准" in 据["reason"]

    def test_号没在跑才走文件探测(self, flask_app, monkeypatch):
        """反向哨兵：一律短路会把"这个号还没启动，想验一下 Cookie"那条路也堵死。"""
        APP = self._模块(flask_app)
        monkeypatch.setattr(APP, "_live_login_state", lambda idx: None)
        起了 = []

        def 假探测(**kw):
            起了.append(kw)
            return {"valid": False, "logged_in": False, "reason": "没登录",
                    "current_url": "", "checks": {}}

        import boss_bot.browser_launcher as BL
        monkeypatch.setattr(BL, "check_cookie_valid", 假探测)
        with APP.app.test_client() as c:
            c.get("/api/accounts/0/check_cookie")
        assert 起了, "号没在跑时探测那条路还在"
