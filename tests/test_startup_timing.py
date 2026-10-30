# -*- coding: utf-8 -*-
"""启动要一步一步报秒，不许再拿猜的数当依据。

2026-10-25 查"启动到第一轮开工 6 分 41 秒"时，能证实的只有跨度（日志首末时间戳），
每一步各吃掉多少秒没有任何一条记录——"三次整页加载各约 128 秒"是我猜的。
猜的数改不了任何东西：要降 retry、要砍一趟导航，都得先知道那一趟值多少秒。
所以 `_计时()` 把启动路上每一趟真发请求的调用（起进程、开主站、导航会话页、
开两张标签页、复验登录、初始化引擎、重开浏览器）各自包成一条 INFO。
"""
import inspect
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import main_loop as ML  # noqa: E402


def _造启动(monkeypatch):
    """一条不碰浏览器的启动链：每一步用替身，只留下"报了几秒"这条账。"""
    with patch("boss_bot.main_loop.BrowserManager"):
        lp = ML.UnifiedBotLoop(account_index=0)
    lp.日志 = []
    lp._log = lambda level, msg: lp.日志.append((level, msg))
    lp._running = True
    lp._stop_event = MagicMock()
    lp._stop_event.is_set.return_value = False
    lp._stop_event.wait.return_value = True
    lp.browser_manager = MagicMock()
    monkeypatch.setattr(lp, "_init_browser", lambda 安静=False: True)
    monkeypatch.setattr(lp, "_handle_login", lambda: True)
    monkeypatch.setattr(lp, "_init_engines", lambda: None)
    monkeypatch.setattr(lp, "_greet_loop", lambda: None)
    monkeypatch.setattr(lp, "_reply_loop", lambda: None)
    # 尾部那句 while 靠 wait 返回 True 收尾，别让测试真等 5 秒
    monkeypatch.setattr(lp._stop_event, "is_set", lambda: True)
    return lp


def _秒数(日志, 什么事):
    行 = [m for lv, m in 日志 if lv == "INFO" and m.startswith("⏱ " + 什么事 + ":")]
    assert len(行) == 1, "「%s」这一步的秒数没落进日志（或落了两条）: %s" % (什么事, 日志)
    return float(行[0].split(":")[1].replace(" 秒", "").strip())


class 计时助手Test:

    def test_返回的是被包那一步的真结果(self, monkeypatch):
        lp = _造启动(monkeypatch)
        assert lp._计时("做事", lambda: "成") == "成"

    def test_级别能降(self, monkeypatch):
        """端口被占时启动每 60 秒重试一趟，第 2 趟起不许再上 INFO"""
        lp = _造启动(monkeypatch)
        lp._计时("启动浏览器", lambda: None, 级别="DEBUG")
        assert [lv for lv, m in lp.日志 if m.startswith("⏱ 启动浏览器")] == ["DEBUG"]

    def test_报的秒数是真量出来的不是零(self, monkeypatch):
        """反向哨兵：把起点挪到调用之后就红了——0.0 秒说明表是假的"""
        lp = _造启动(monkeypatch)
        lp._计时("慢活", lambda: time.sleep(0.2))
        assert _秒数(lp.日志, "慢活") >= 0.15

    def test_那一步炸了也要把秒数留下(self, monkeypatch):
        """导航超时是这条链最常见的坏消息，报不出它花多久就等于白装表"""
        lp = _造启动(monkeypatch)

        def 炸():
            time.sleep(0.2)
            raise RuntimeError("页面加载失败")

        with pytest.raises(RuntimeError):
            lp._计时("开主站", 炸)
        assert _秒数(lp.日志, "开主站") >= 0.1, "异常把秒数一起冲掉了"


class 启动每一步都要报秒Test:

    def test_整条启动链每一步都有账(self, monkeypatch):
        lp = _造启动(monkeypatch)
        lp.browser_manager.get_search_page.side_effect = lambda *a, **k: time.sleep(0.2)
        lp.browser_manager.get_chat_page.side_effect = lambda *a, **k: time.sleep(0.2)
        lp._init_and_run()
        for 名 in ("启动浏览器", "开搜索标签页", "开会话标签页", "初始化引擎"):
            assert _秒数(lp.日志, 名) >= 0, 名
        # 报的是 %.1f：这两步各睡 0.2 秒，还印成 0.0 就是起点记在了调用之后
        for 名 in ("开搜索标签页", "开会话标签页"):
            assert _秒数(lp.日志, 名) >= 0.1, "%s 报 0.0，这一步没真包进调用" % 名
        assert any(m.startswith("⏱ 启动到两条腿开跑共") for lv, m in lp.日志), lp.日志

    def test_重试那几趟的秒数不许淹日志(self, monkeypatch):
        """端口被占一小时 = 六十趟：只有第一趟上 INFO，其余走 DEBUG"""
        lp = _造启动(monkeypatch)
        lp._stop_event.is_set = lambda: False      # 没人叫停，靠替身自己把趟数走完
        # 现场必须答"是别人的窗口"，否则主循环按"坏了，重开也是坏"一趟就撤
        lp.browser_manager.等登录现场.return_value = ("窗口被别人占了", "PID=45228")
        趟 = []

        def 假起(安静=False):
            趟.append(1)
            if len(趟) >= 3:
                lp._running = False
            return False

        monkeypatch.setattr(lp, "_init_browser", 假起)
        lp._init_and_run()
        级 = [(lv, m) for lv, m in lp.日志 if m.startswith("⏱ 启动浏览器")]
        assert len(级) == len(趟), "每趟都要有秒数，不然这表还是缺一列"
        assert [lv for lv, _ in 级] == ["INFO"] + ["DEBUG"] * (len(级) - 1), 级

    def test_总账要盖得住各步之和(self, monkeypatch):
        """漏记一步的话总数还是能报出来，所以拿各步之和去顶总账的门"""
        lp = _造启动(monkeypatch)
        lp._init_and_run()
        总 = [m for lv, m in lp.日志 if m.startswith("⏱ 启动到两条腿开跑共")][0]
        总秒 = float(总.split("共")[1].replace(" 秒", "").strip())
        各步 = sum(_秒数(lp.日志, 名) for 名 in
                   ("启动浏览器", "开搜索标签页", "开会话标签页", "初始化引擎"))
        assert 总秒 >= 各步, "总账 %s 秒 比记下来的各步 %s 秒 还短——有一步没进总账" % (
            总秒, 各步)


class 导航不许再裸调Test:
    """包了 _计时 才算数：这三处就是当年那 6 分 41 秒里唯一真发整页加载的地方。"""

    def test_开主站走计时(self, monkeypatch):
        源 = inspect.getsource(ML.UnifiedBotLoop._自动登录尝试)
        assert 'self._计时("开主站", instance.get,' in 源
        assert '\n                instance.get("https://www.zhipin.com")' not in 源

    def test_会话页导航与开会话页走计时(self):
        源 = inspect.getsource(ML.UnifiedBotLoop._login_state_now)
        assert '_计时("导航会话页"' in 源
        assert "\n            instance.get(" not in 源
        源2 = inspect.getsource(ML.UnifiedBotLoop._init_engines)
        assert '_计时("开会话页"' in 源2
        assert "\n        chat_page = self.browser_manager.get_chat_page(" not in 源2

    def test_重开浏览器那三步也走计时(self):
        源 = inspect.getsource(ML.UnifiedBotLoop._重建浏览器)
        for 名 in ("重开浏览器", "接回搜索页", "初始化引擎"):
            if 名 == "初始化引擎":
                assert "_init_engines(开页=先登着)" in 源, "重开完必须把引擎接回来"
                continue
            assert ('_计时("%s"' % 名) in 源, "%s 这一步没计时" % 名


class 复验登录整体要报秒Test:

    def test_复验包在计时里(self):
        源 = inspect.getsource(ML.UnifiedBotLoop._handle_login)
        assert '_计时("登录复验", self._自动登录尝试' in 源, \
            "复验整段不报秒，就分不清是页面慢还是窗口等满了"
