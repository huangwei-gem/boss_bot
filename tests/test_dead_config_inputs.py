# tests/test_dead_config_inputs.py
"""面板上有几颗输入框是"填了不生效"的——这几条把它们接到实路上。

10-23 逐个查下来的实情（不是猜的，每条都对着代码追过一遍赋值/读取）：

- `browser.page_load_timeout`：`_apply_bot_config` 收进配置、`to_dict()` 发回界面，可
  `BrowserManager` 从没把它交给任何 DrissionPage 调用。
  → 现在：值一路传到 `ChromiumOptions.set_timeouts(page_load=)`，真机起一台浏览器读到
    `page.timeouts.page_load == 13`（探针脚本 probe_ua_and_timeout.py，10-23 跑过）。
- `browser.custom_user_agent`：同上从没被读过。**但这条不能接**：实测（cloakbrowser 和
  系统 Chrome 146 各起一台无头，只开 data: 页）只要带 `--user-agent`，
  `navigator.userAgent` 就读成空串——那是给风控送一个不可能的指纹。
  → 界面上那格 UA 撤掉（入口没了），配置字段仍读得到并会在启动时告警说明代价。
- `greet.retry.base_delay` / `backoff_factor`：赋值完从没被读过，页面加载重试
  走的是硬编码 `_random_delay(2, 4)`。→ 现在按 base × factor^n 退避。
- 「每小时最多打招呼」有两颗输入框：顶层 `config.rate_limit.max_per_hour`（真生效，
  加载器把它写进 greet.rate_limit）和 `config.greet.rate_limit.max_per_hour`
  （加载器不读这个形状，app.py 还专门造了镜像让它"看着能填"）。→ 撤掉第二颗。
- 话术模板、画像、规则话术：值住 `boss_bot.config` 的 import 快照里，热重载不刷就
  只有重启才生效。→ 加 `config.刷新兼容层()`，主循环每轮热重载调用；发送那一路改成
  现取（`意图话术表()` / `config.USER_PROFILE`），规则表按新配置重建 `RuleEngine`。

判据都打在"实际动作"上：假 ChromiumOptions 记下真被调了什么、假 launch_browser
记下真被传了什么、退避算出的秒数直接比。
"""
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import DrissionPage

from boss_bot import browser_launcher as BL
from boss_bot.greet_engine import GreetEngine
from boss_bot.unified_config import UnifiedConfig


class 假选项:
    """替 ChromiumOptions：只记"被调了什么参数"，不去碰真浏览器。"""

    记下 = []

    def __init__(self):
        self.ua_调用 = []
        self.timeouts = {}
        假选项.记下.append(self)

    def set_browser_path(self, p):
        pass

    def set_argument(self, a):
        pass

    def set_local_port(self, p):
        pass

    def set_proxy(self, p):
        pass

    def set_user_agent(self, ua):
        self.ua_调用.append(ua)

    def set_timeouts(self, base=None, page_load=None, script=None):
        self.timeouts = {"base": base, "page_load": page_load, "script": script}

    def __getattr__(self, name):
        return lambda *a, **k: None


class 假页面:
    browser = SimpleNamespace(process_id=0)


@pytest.fixture()
def 选项台(monkeypatch):
    假选项.记下 = []
    monkeypatch.setattr(DrissionPage, "ChromiumOptions", 假选项)
    monkeypatch.setattr(DrissionPage, "ChromiumPage", lambda co: 假页面())
    yield 假选项
    假选项.记下 = []


class Test启动旋钮真进浏览器:
    def test_窗口分支把UA与加载超时交给ChromiumOptions(self, 选项台):
        BL._launch_windows(chrome_path="C:/x/chrome.exe", headless=True,
                           user_agent="我的UA/9", proxy="", viewport_width=1280,
                           viewport_height=800, port=0, user_data_dir="",
                           background=True, page_load_timeout=44)
        assert len(选项台.记下) == 1
        co = 选项台.记下[0]
        assert co.ua_调用 == ["我的UA/9"], "面板里那格 UA 没进启动参数"
        assert co.timeouts["page_load"] == 44, "面板里那格超时没进 DrissionPage"

    def test_不填UA就不许塞一个空值进去(self, 选项台):
        """默认留空 = 用浏览器自己的 UA；填了才覆盖，这条不能反。"""
        BL._launch_windows(chrome_path="C:/x/chrome.exe", headless=True,
                           user_agent="", proxy="", viewport_width=1280,
                           viewport_height=800, port=0, user_data_dir="",
                           background=True, page_load_timeout=30)
        assert 选项台.记下[0].ua_调用 == []
        assert 选项台.记下[0].timeouts["page_load"] == 30

    def test_mac分支也带加载超时(self):
        """mac 是手拼启动参数再连 ws，旋钮走的是同一个 ChromiumOptions。"""
        源 = io.open(ROOT / "boss_bot" / "browser_launcher.py", encoding="utf-8").read()
        段 = 源[源.index("def _launch_macos"):]
        段 = 段[:段.index("\ndef ")].replace("\n", " ").replace("    ", "")
        assert "co.set_timeouts(page_load=page_load_timeout)" in 段, 段[:200]

    def test_从launch_browser到平台分支参数不许丢(self, monkeypatch):
        收 = {}

        def 记(**kw):
            收.update(kw)
            return SimpleNamespace()
        monkeypatch.setattr(BL, "_IS_MACOS", False)
        monkeypatch.setattr(BL, "_launch_windows", 记)
        BL.launch_browser(chrome_path=sys.executable, user_agent="UA-透传",
                          page_load_timeout=51)
        assert 收.get("user_agent") == "UA-透传", 收
        assert 收.get("page_load_timeout") == 51, 收


class Test管理器认面板那个键名:
    def _配置(self, **kw):
        底 = dict(headless=True, background=True, proxy="", viewport_width=1280,
                  viewport_height=800, chrome_path="", browser_type="chrome",
                  cookie_file="", user_data_dir="C:/tmp/不存在", debug_port=9222)
        底.update(kw)
        return SimpleNamespace(**底)

    def test_custom_user_agent与page_load_timeout读得到(self):
        m = BL.BrowserManager(self._配置(custom_user_agent="甲UA", page_load_timeout=12))
        assert m._user_agent == "甲UA"
        assert m._page_load_timeout == 12

    def test_老键名照样认(self):
        m = BL.BrowserManager(self._配置(user_agent="乙UA", page_timeout=13))
        assert m._user_agent == "乙UA"
        assert m._page_load_timeout == 13

    def test_什么都没填就是默认30秒不覆盖UA(self):
        m = BL.BrowserManager(self._配置())
        assert m._user_agent == ""
        assert m._page_load_timeout == 30

    def test_启动时把这两枚真交给launch_browser(self, monkeypatch):
        收 = {}
        monkeypatch.setattr(BL, "launch_browser",
                            lambda **kw: 收.update(kw) or SimpleNamespace())
        m = BL.BrowserManager(self._配置(custom_user_agent="丙UA", page_load_timeout=21))
        m._让旧形态让位 = lambda: None
        m.start_idle_tab_watchdog = lambda: None
        m.launch()
        assert 收.get("user_agent") == "丙UA", 收
        assert 收.get("page_load_timeout") == 21, 收


class Test重试退避按配置算:
    def _引擎(self, base, factor):
        机 = GreetEngine.__new__(GreetEngine)
        机._retry_base_delay = base
        机._retry_backoff_factor = factor
        return 机

    def test_第0次就是base_delay往后按factor翻(self):
        收 = []
        机 = self._引擎(2.0, 3.0)
        机._random_delay = lambda a, b: 收.append((a, b))
        for i in (0, 1, 2):
            机._退避重试等待(i)
        assert [a for a, _ in 收] == pytest.approx([1.6, 4.8, 14.4]), 收
        assert [b for _, b in 收] == pytest.approx([2.4, 7.2, 21.6]), 收

    def test_填5秒就该真等5秒(self):
        """反向哨兵：这条在硬编码 _random_delay(2, 4) 的年代是红的。"""
        收 = []
        机 = self._引擎(5.0, 1.0)
        机._random_delay = lambda a, b: 收.append((a, b))
        机._退避重试等待(0)
        assert 收 == [(4.0, 6.0)], 收

    def test_导航重试那一处不许再写死秒数(self):
        源 = io.open(ROOT / "boss_bot" / "greet_engine.py", encoding="utf-8").read()
        段 = 源[源.index("for _retry in range(self._retry_max_attempts)"):]
        段 = 段[:段.index("else:")]
        assert "_退避重试等待(_retry)" in 段, "重试又回到硬编码延迟了"
        assert "_random_delay(2, 4)" not in 段


class Test话术改了下一句就用新的:
    """模板与画像住在兼容层的模块常量里，热重载不刷就是"重启才生效"。"""

    def _换掉(self, 值):
        from boss_bot import config as 兼容层
        cfg = UnifiedConfig()
        cfg.templates.salary_reply = 值
        return 兼容层, cfg

    def test_刷新兼容层写回常量并被现取拿到(self):
        兼容层, cfg = self._换掉("10-23 新写的薪资话术")
        cfg.rules.reply_rules = {"问薪资": "新规则话术"}
        原 = 兼容层.SALARY_REPLY
        try:
            assert 兼容层.刷新兼容层(cfg) == 10
            assert 兼容层.SALARY_REPLY == "10-23 新写的薪资话术"
            assert 兼容层.REPLY_RULES == {"问薪资": "新规则话术"}
            from boss_bot.reply_engine import 意图话术表
            assert 意图话术表()["ask_salary"][1] == "10-23 新写的薪资话术"
        finally:
            兼容层.刷新兼容层(UnifiedConfig.load())
            assert 兼容层.SALARY_REPLY == 原, "这份测试把真配置的话术改脏了"

    def test_发送那一路走的是现取的表不是import快照(self):
        文 = io.open(ROOT / "boss_bot" / "reply_engine.py", encoding="utf-8").read()
        assert "话术表 = 意图话术表()" in 文
        assert "INTENT_REPLIES[meta[" not in 文
        assert "render_template(template, config.USER_PROFILE)" in 文

    def test_热重载既刷常量也重建规则引擎(self):
        文 = io.open(ROOT / "boss_bot" / "main_loop.py", encoding="utf-8").read()
        段 = 文[文.index("def _hot_reload_config"):]
        段 = 段[:段.index("\n    def ")] if "\n    def " in 段 else 段
        assert "刷新兼容层(self.config)" in 段, "热重载没刷兼容层，话术改动仍要重启"
        assert "RuleEngine(新表)" in 段, "规则话术表改完仍要重启才生效"

    def test_这条门会红(self):
        """反向变异：把常量写法塞回发送路径，判据要挑得出来。"""
        文 = 'content = render_template(template, USER_PROFILE) if template else None'
        assert "render_template(template, config.USER_PROFILE)" not in 文


class TestUA那格已撤掉入口:
    """接不住的东西不给输入框：实测设了 UA 会把 navigator.userAgent 变成空串。"""

    def test_界面里没有UA格(self):
        文 = io.open(ROOT / "flask-version" / "templates" / "index.html",
                     encoding="utf-8").read()
        assert "advUserAgent" not in 文
        assert "config.browser.custom_user_agent" not in 文

    def test_配置里手填仍会被告知代价(self):
        文 = io.open(ROOT / "boss_bot" / "browser_launcher.py", encoding="utf-8").read()
        段 = 文[文.index("self._user_agent = (("):]
        段 = 段[:段.index("self._proxy")]
        assert "if self._user_agent:" in 段 and "navigator.userAgent" in 段

    def test_这条门会红(self):
        """反向变异：UA 格塞回界面，判据必须挑出来。"""
        文 = '<input class="side-form-input" id="advUserAgent"> config.browser.custom_user_agent = x'
        assert "advUserAgent" in 文 and "config.browser.custom_user_agent" in 文


class Test每小时上限只留一颗出口:
    def test_界面不再有第二颗同名输入框(self):
        文 = io.open(ROOT / "flask-version" / "templates" / "index.html",
                     encoding="utf-8").read()
        assert "advMaxGreet" not in 文
        assert 文.count('id="advRatePerHour"') == 1

    def test_后端不再另造greet_rate_limit镜像(self):
        文 = io.open(ROOT / "flask-version" / "app.py", encoding="utf-8").read()
        assert 'config_dict["greet"]["rate_limit"]' not in 文

    def test_留下的那颗真写进生效字段(self):
        """顶层 rate_limit 段就是那颗输入框提交的路径，必须落到 greet.rate_limit。"""
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"rate_limit": {"max_per_hour": 77}})
        assert cfg.greet.rate_limit.max_per_hour == 77
        assert cfg.to_dict()["rate_limit"]["max_per_hour"] == 77

    def test_这条门会红(self):
        """反向变异：把第二颗键塞回去，判据必须挑出来。"""
        文 = 'x = 1\nconfig_dict["greet"]["rate_limit"] = {}\n'
        assert 'config_dict["greet"]["rate_limit"]' in 文
