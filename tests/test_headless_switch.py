# -*- coding: utf-8 -*-
"""浏览器形态（无头/有头）开关要一点就换掉在跑的浏览器。

起因：界面上那个「无头」开关只写配置，自己的 toast 都写着"下一次启动浏览器才生效"。
于是用户既看不出此刻跑的是哪种形态，也没有"现在就切过去"的入口（2026-10-10 提出）。
按这个项目的红线——改了必须真生效——开关落点必须是：写配置 + 把在跑的号按新形态重启。
"""
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import boss_bot.main_loop as ML
from boss_bot.main_loop import MultiAccountManager, apply_headless_config
from boss_bot.unified_config import UnifiedConfig


def _配置(基准无头=False, 按号覆盖=None):
    cfg = UnifiedConfig()
    cfg.browser.headless = 基准无头
    while len(cfg.greet.accounts) < 2:
        from boss_bot.unified_config import AccountConfig
        cfg.greet.accounts.append(AccountConfig(name="账号%d" % (len(cfg.greet.accounts) + 1)))
    if 按号覆盖 is not None:
        cfg.greet.accounts[1].settings = 按号覆盖
    return cfg


class 假线程:
    """把后台线程就地跑完，好让测试断言"重启真的发生了"。"""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **kw):
        self._target = target
        self._args = tuple(args)
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)

    def is_alive(self):
        return False


class 假浏览器管理器:
    def __init__(self):
        self.adopted = 0
        self.收到的形态 = None

    def adopt_mode(self, headless):
        self.收到的形态 = bool(headless)
        self.adopted += 1


class 假循环:
    def __init__(self, config=None, account_index=0, **kw):
        self.index = account_index
        self.running = False
        self.calls = []
        self.adopted = None
        self.browser_manager = 假浏览器管理器()
        self.config = _配置()          # 每个号自己那份深拷贝副本

    def adopt_headless(self, headless):
        self.adopted = bool(headless)
        self.config.browser.headless = bool(headless)
        self.browser_manager.adopt_mode(bool(headless))

    def get_status(self):
        return {"index": self.index, "running": self.running,
                "logged_in": self.running, "needs_login": False, "login_reason": ""}

    def start(self):
        self.calls.append("start")
        self.running = True

    def stop(self):
        self.calls.append("stop")
        self.running = False


@pytest.fixture
def 落盘(tmp_path, monkeypatch):
    import boss_bot.unified_config as UC
    monkeypatch.setattr(UC, "BOT_CONFIG_FILE", tmp_path / "bot_config.json")
    monkeypatch.setattr(UC, "OVERRIDES_FILE", tmp_path / "config_overrides.json")
    monkeypatch.setattr(UC, "USER_PROFILE_FILE", tmp_path / "user_profile.json")
    return tmp_path


@pytest.fixture
def 管理器(monkeypatch, 落盘):
    monkeypatch.setattr(ML.threading, "Thread", 假线程)
    monkeypatch.setattr(ML.time, "sleep", lambda s: None)
    monkeypatch.setattr(ML, "UnifiedBotLoop", 假循环)
    cfg = _配置()
    mgr = MultiAccountManager(cfg)
    assert len(mgr._loops) == 2, "两个号都要建出来，切换才谈得上"
    return mgr, cfg


class Test写配置:
    def test_全局基准被改写并且落盘(self, 落盘):
        cfg = _配置(基准无头=False)
        assert apply_headless_config(cfg, True) == []
        assert cfg.browser.headless is True
        落盘后 = json.loads((落盘 / "bot_config.json").read_text(encoding="utf-8"))
        assert 落盘后["browser"]["headless"] is True

    def test_按号覆盖会悄悄赢过全局所以要拆掉(self, 落盘):
        cfg = _配置(基准无头=True, 按号覆盖={"browser": {"headless": False}})
        拆了 = apply_headless_config(cfg, True)
        assert 拆了 == [1], "被拆掉的号要报出来，界面才知道这次动了几个号"
        assert "headless" not in (cfg.greet.accounts[1].settings.get("browser") or {})

    def test_没设过覆盖的号不会被凭空造出一段空配置(self, 落盘):
        cfg = _配置()
        apply_headless_config(cfg, False)
        assert cfg.greet.accounts[0].settings == {}


class Test切形态:
    def test_只重启原本在跑的号(self, 管理器):
        mgr, cfg = 管理器
        mgr._loops[0].running = True      # 号0 在跑；号1 是用户手动停的
        结果 = mgr.switch_headless(True)
        assert 结果["restarting"] == [0]
        assert mgr._loops[0].calls == ["stop", "start"], "先停再起，才能用新形态重开浏览器"
        assert mgr._loops[0].browser_manager.adopted == 1, "起之前对象要先跟上"
        assert mgr._loops[0].browser_manager.收到的形态 is True, \
            "传给对象的必须是这次的新值，读旧配置对象等于没切"
        assert mgr._loops[1].calls == [], "手动停着的号不许被这次切换顺手起回来"

    def test_停着的号也要记住新形态(self, 管理器):
        """循环拿的是 apply_account() 的深拷贝：只改基准，下次启动还是旧形态。"""
        mgr, cfg = 管理器
        mgr.switch_headless(True)
        assert [loop.adopted for loop in mgr._loops.values()] == [True, True]
        assert all(loop.config.browser.headless for loop in mgr._loops.values())

    def test_配置先落盘再谈重启(self, 管理器):
        mgr, cfg = 管理器
        mgr.switch_headless(True)
        assert cfg.browser.headless is True

    def test_没有在跑的号时不开线程也能生效(self, 管理器):
        mgr, cfg = 管理器
        结果 = mgr.switch_headless(False)
        assert 结果["restarting"] == []
        assert cfg.browser.headless is False


class Test循环副本:
    def test_adopt_headless同时改掉副本和浏览器管理器(self):
        import types
        对象 = types.SimpleNamespace(config=_配置(基准无头=True),
                                     browser_manager=假浏览器管理器())
        ML.UnifiedBotLoop.adopt_headless(对象, False)
        assert 对象.config.browser.headless is False
        assert 对象.browser_manager.收到的形态 is False

    def test_管理器不在的时候不要炸(self):
        import types
        对象 = types.SimpleNamespace(config=_配置(基准无头=False), browser_manager=None)
        ML.UnifiedBotLoop.adopt_headless(对象, True)
        assert 对象.config.browser.headless is True


class Test对象跟上配置:
    """重启这条链上最容易丢的一环：BrowserManager 构造时把形态抄成自己的字段。"""

    def test_切完形态对象上也要是新值(self, 落盘):
        from boss_bot.browser_launcher import BrowserManager
        cfg = _配置(基准无头=False)
        bm = BrowserManager(config=cfg.browser, account_index=0, port=9399)
        assert bm._headless is False
        assert bm.adopt_mode(True) is True, "变了要报出来，重启才有意义"
        assert bm._headless is True
        assert bm.adopt_mode(True) is False, "本来就一致时别谎报"

    def test_热重载换过副本之后切形态也要生效(self, 落盘):
        """真机踩过：_hot_reload_config 每轮把 loop.config 换成一个全新的深拷贝，
        BrowserManager._config 还指着构造时那一份孤儿。形态若从 _config 里读，
        配置翻过来了、号也重开了，浏览器还是老样子起来（2026-10-10 10:48 日志）。"""
        import types
        from boss_bot.browser_launcher import BrowserManager
        构造时 = _配置(基准无头=False)
        管 = BrowserManager(config=构造时.browser, account_index=0, port=9399)
        对象 = types.SimpleNamespace(config=_配置(基准无头=False), browser_manager=管)
        对象.config = _配置(基准无头=True)      # 模拟热重载：换了另一个对象
        ML.UnifiedBotLoop.adopt_headless(对象, True)
        assert 管._headless is True, "起浏览器读的是这个字段，它必须跟上这次切换"


class Test端口上的旧形态浏览器:
    """切形态的第二种失败方式：端口上还挂着一个旧形态的浏览器，新起的直接连上去。"""

    def _装好(self, monkeypatch, 挂着, 记录):
        import boss_bot.browser_launcher as BL
        cfg = _配置(基准无头=True)
        管 = BL.BrowserManager(config=cfg.browser, account_index=0, port=9399,
                              user_data_dir=str(Path("browser_data") / "account_0"))
        monkeypatch.setattr(BL, "_端口上的自家浏览器", lambda 端口, 目录: 挂着)
        monkeypatch.setattr(BL.time, "sleep", lambda s: None)
        monkeypatch.setattr(BL.platform_compat, "终止进程",
                            lambda pid, **kw: 记录.append(("杀", pid)))
        monkeypatch.setattr(BL, "launch_browser",
                            lambda **kw: 记录.append(("起", kw["headless"])) or object())
        return 管

    def test_形态不对的自家浏览器要先让位再按新形态起(self, monkeypatch):
        """DrissionPage 见端口有人应答就直接连上去，这次的 --headless 压根没送进进程；
        偏偏启动记录在连之前就写了这次要的形态，界面于是报"无头"而真跑的是有头
        （2026-10-10 11:50 实测：9223 上那个进程没有 --headless 旗标）。"""
        记录 = []
        管 = self._装好(monkeypatch, {"pid": 4242, "headless": False}, 记录)
        管.launch()
        assert ("杀", 4242) in 记录, "旧形态的浏览器得先收掉，不然连上去还是老样子"
        assert 记录[-1] == ("起", True), "最后一次动作必须是按新形态真起一个"

    def test_形态一致时一个进程都不杀(self, monkeypatch):
        记录 = []
        self._装好(monkeypatch, {"pid": 4242, "headless": True}, 记录).launch()
        assert [r for r in 记录 if r[0] == "杀"] == [], "一致就复用，别白白重开一次浏览器"
        assert 记录[-1] == ("起", True)

    def test_端口空着时不杀任何东西(self, monkeypatch):
        记录 = []
        self._装好(monkeypatch, {}, 记录).launch()
        assert [r for r in 记录 if r[0] == "杀"] == []

    def test_进程号认不出来就不动手(self, monkeypatch):
        记录 = []
        self._装好(monkeypatch, {"pid": 0, "headless": False}, 记录).launch()
        assert [r for r in 记录 if r[0] == "杀"] == [], "宁可形态没切成，也不盲杀进程"

    def test_只有自家profile的那份浏览器才算自家(self, monkeypatch):
        """9223 被兄弟项目占过一次：不是本账号 profile 的浏览器交给守卫去拒，
        轮不到我们动它的进程。"""
        import boss_bot.browser_launcher as BL
        monkeypatch.setattr(BL, "_is_port_open", lambda h, p, timeout=1.0: True)
        monkeypatch.setattr(BL, "_browser_owners", lambda: [
            {"pid": 11, "port": 9399, "user_data_dir": r"C:\兄弟项目\browser_data\account_0",
             "headless": False},
        ])
        自家目录 = r"C:\Users\35796\orca\boss_bot\browser_data\account_0"
        assert BL._端口上的自家浏览器(9399, 自家目录) == {}

    def test_端口没人应答就不去翻进程表(self, monkeypatch):
        """每次起浏览器都要问一遍，端口空着的时候别顺手起一发 PowerShell。"""
        import boss_bot.browser_launcher as BL
        monkeypatch.setattr(BL, "_is_port_open", lambda h, p, timeout=1.0: False)
        monkeypatch.setattr(BL, "_browser_owners",
                            lambda: (_ for _ in ()).throw(AssertionError("不该翻进程表")))
        assert BL._端口上的自家浏览器(9399, r"C:\x\account_0") == {}

    def test_自家profile那份要报出它真实的形态(self, monkeypatch):
        """让位判定全靠这一手：读回来的 headless 是进程自己的，不是启动记录里的愿望。"""
        import boss_bot.browser_launcher as BL
        monkeypatch.setattr(BL, "_is_port_open", lambda h, p, timeout=1.0: True)
        monkeypatch.setattr(BL, "_browser_owners", lambda: [
            {"pid": 77, "port": 9399, "user_data_dir": r"C:\x\browser_data\account_0",
             "headless": False},
        ])
        assert BL._端口上的自家浏览器(9399, r"C:\x\browser_data\account_0") == {
            "pid": 77, "headless": False}

    def test_命令行里的无头旗标要读出来(self):
        """判形态的唯一硬依据就是进程命令行（cloakbrowser 把 UA 里的 Headless 抹平了）。"""
        from boss_bot.browser_launcher import parse_browser_cmdline
        有头 = parse_browser_cmdline(r'C:\cloakbrowser\chrome.exe --remote-debugging-port=9223 '
                                     r'--user-data-dir=C:\x\account_1')
        无头 = parse_browser_cmdline(r'C:\cloakbrowser\chrome.exe --remote-debugging-port=9223 '
                                     r'--user-data-dir=C:\x\account_1 --headless=new')
        assert 有头["headless"] is False and 无头["headless"] is True


class Test启动要能自己再来一次:
    """切换会同时重开两个号的浏览器，负载下 DrissionPage 会连不上端口。"""

    def _对象(self, 抛错次数):
        import types

        class 会抛的管理器:
            def __init__(self):
                self.n = 0

            def launch(self):
                self.n += 1
                if self.n <= 抛错次数:
                    raise RuntimeError("浏览器连接失败，请确认浏览器已启动。")

            def 启动现场(self):
                # _init_browser 的日志要问这个号真正用的端口/目录（替身也得答得出）
                return "端口=9223 用户目录=C:\\x\\account_1 形态=无头"

            def close(self):
                pass

        记 = []
        对象 = types.SimpleNamespace(
            config=_配置(基准无头=True), browser_manager=会抛的管理器(),
            _login_reason="", _log=lambda lvl, msg: 记.append((lvl, msg)),
            _interruptible_sleep=lambda s: None)
        return 对象, 记

    def test_第一次连不上要再试一次而不是把号停在stopped(self):
        """11:41:37 /json 还回 200，11:41:39 就抛 BrowserConnectError——
        浏览器明明活着，号却悄悄停了，用户点的是"换形态"，代价不该是一个号下线。"""
        对象, 记 = self._对象(1)
        assert ML.UnifiedBotLoop._init_browser(对象) is True
        assert 对象.browser_manager.n == 2

    def test_两次都连不上才认失败并留下原因(self):
        对象, 记 = self._对象(2)
        assert ML.UnifiedBotLoop._init_browser(对象) is False
        assert 对象._login_reason == "browser_failed"
        assert [lvl for lvl, _ in 记 if lvl == "ERROR"] == ["ERROR", "ERROR"]


class Test换形态要排队:
    """start() 是非阻塞的：连着发两次就是两个浏览器同时抢启动。"""

    def _管理器(self, monkeypatch):
        monkeypatch.setattr(ML.time, "sleep", lambda s: None)
        mgr = MultiAccountManager(_配置())
        return mgr

    def _号(self, mgr, idx, **状态):
        import types
        底 = {"running": True, "logged_in": False, "needs_login": False, "login_reason": ""}
        底.update(状态)
        mgr._loops[idx] = types.SimpleNamespace(get_status=lambda: dict(底))

    def test_号真跑起来了才放行(self, monkeypatch):
        mgr = self._管理器(monkeypatch)
        self._号(mgr, 0, logged_in=True)
        assert mgr._等在跑起来(0) is True

    def test_要人工登录说明浏览器已经起来了(self, monkeypatch):
        """别把这个当"没起来"干等：扫码那一类号本来就要人管，浏览器是活的。"""
        mgr = self._管理器(monkeypatch)
        self._号(mgr, 0, needs_login=True, login_reason="no_cookie")
        assert mgr._等在跑起来(0) is True

    def test_浏览器起不来就认定没起来(self, monkeypatch):
        """12:22 那一次：两个号一起停在 stopped，等下去也不会自己好。"""
        mgr = self._管理器(monkeypatch)
        self._号(mgr, 0, running=False, login_reason="browser_failed")
        assert mgr._等在跑起来(0) is False

    def test_还在启动中就轮到超时(self, monkeypatch):
        mgr = self._管理器(monkeypatch)
        self._号(mgr, 0)
        assert mgr._等在跑起来(0, 最长秒=0.01) is False

    def test_排队是一个号一个号起而不是同时抢(self, monkeypatch):
        """start() 非阻塞，连着发两次就是两个浏览器同时抢启动
        （12:21–12:22 实测：两个号都停在 stopped，一个都没起来）。"""
        monkeypatch.setattr(ML, "UnifiedBotLoop", 假循环)
        monkeypatch.setattr(ML.threading, "Thread", 假线程)
        mgr = MultiAccountManager(_配置())
        次序 = []
        for loop in mgr._loops.values():
            loop.running = True
            loop.calls = 次序              # stop/start 都往同一个列表里按顺序记
        等过 = []
        mgr._等在跑起来 = lambda idx, **kw: 等过.append(idx) or True
        mgr.switch_headless(True)
        assert 次序 == ["stop", "start", "stop", "start"]
        assert 等过 == [0, 1], "每起一个都要等它跑起来，才轮到下一个"

    def test_一个号没起来也要接着处理另一个(self, monkeypatch):
        monkeypatch.setattr(ML, "UnifiedBotLoop", 假循环)
        monkeypatch.setattr(ML.threading, "Thread", 假线程)
        mgr = MultiAccountManager(_配置())
        次序 = []
        for loop in mgr._loops.values():
            loop.running = True
            loop.calls = 次序
        mgr._等在跑起来 = lambda idx, **kw: idx != 0     # 号 0 起不来
        mgr.switch_headless(True)
        assert 次序 == ["stop", "start", "stop", "start"], "不能因为一个号卡住就不管另一个"


class Test接口:
    @pytest.fixture
    def flask_app(self, 落盘, monkeypatch):
        flask_dir = str(Path(__file__).parent.parent / "flask-version")
        if flask_dir not in sys.path:
            sys.path.insert(0, flask_dir)
        from unittest.mock import patch
        with patch("app.UnifiedBotLoop"):
            import app as FLASK_APP
        monkeypatch.setattr(FLASK_APP, "_config", None)
        monkeypatch.setattr(FLASK_APP, "_multi_manager", None)
        monkeypatch.setattr(ML, "UnifiedBotLoop", 假循环)
        monkeypatch.setattr(ML.threading, "Thread", 假线程)
        monkeypatch.setattr(ML.time, "sleep", lambda s: None)
        return FLASK_APP, 落盘

    def test_面板没启动时切形态也要写进配置(self, flask_app):
        FLASK_APP, 落盘 = flask_app
        client = FLASK_APP.app.test_client()
        r = client.post("/api/browser/headless", json={"headless": True})
        assert r.status_code == 200
        assert r.get_json()["headless"] is True
        落盘后 = json.loads((落盘 / "bot_config.json").read_text(encoding="utf-8"))
        assert 落盘后["browser"]["headless"] is True

    def test_面板在跑时走管理器那条路(self, flask_app):
        FLASK_APP, 落盘 = flask_app
        mgr = MultiAccountManager(_配置())
        FLASK_APP._multi_manager = mgr
        mgr._loops[0].running = True
        r = FLASK_APP.app.test_client().post("/api/browser/headless", json={"headless": False})
        数据 = r.get_json()
        assert 数据["restarting"] == [0]
        assert mgr._loops[0].calls == ["stop", "start"]

    def test_非法入参不要静默当成有头(self, flask_app):
        FLASK_APP, 落盘 = flask_app
        r = FLASK_APP.app.test_client().post("/api/browser/headless", json={})
        assert r.status_code == 400

    def test_形态栏问的端口按配置算(self, flask_app, monkeypatch):
        """界面现在把端口印在 chip 上，写死 9222 的话改过端口的机器上全是错话。"""
        FLASK_APP, 落盘 = flask_app
        看的 = []

        def 假形态(port):
            看的.append(port)
            return {"running": False, "port": port}

        monkeypatch.setattr(FLASK_APP, "browser_mode", 假形态)
        FLASK_APP._ensure_config().browser.debug_port = 9400
        FLASK_APP._enrich_status({"accounts": [{"index": 0}, {"index": 1}]})
        assert 看的 == [9400, 9401]


class Test界面接得上:
    """模板是运行时读的大文件，改坏一处整个面板就空白，所以按锚点断言。"""

    def test_形态开关在操作条上并且指向新接口(self):
        html = (Path(__file__).parent.parent / "flask-version" / "templates"
                / "index.html").read_text(encoding="utf-8")
        assert 'id="modeHeadless"' in html and "setBrowserMode(true)" in html
        assert "'/api/browser/headless'" in html
        assert '<span class="acc-mode"></span>' in html, "每个号自己也要显示形态"
        assert "重启面板才生效" not in html, "这句已经不是事实了：切换会当场重开"
