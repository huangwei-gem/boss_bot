# -*- coding: utf-8 -*-
"""浏览器进程死了要当场重开，不能让两条腿拿着死句柄把每一单都烧掉。

2026-10-18 11:23:35 线上（logs/boss_bot.log）：
    [主账号] [ERROR] 页面断开后两个落点加一次重绑都没起来
    标签页看守(127.0.0.1:9222)：读不到标签页列表，这一圈不动手:
        <urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>
10061 = 那个端口上没人听了 —— Chromium 进程已经不在了，不是"句柄钉住的那张页没了"。
于是后面每一条都是死句柄上的白干：
    投递腿每单一句「页面断开且恢复失败」（11:23:33、:49、11:24:04、:21、:37、:50…）；
    回复/跟进腿每个候选 6 次「侧栏跳到 Npx 失败」+ 一次「联系人搜索框没找到」，
    11:10~11:28 这一族数出来是 搜索:no_input 91 / name_only 3 / no_result 1。
挂在侧栏里那几张交换微信卡片就是这么一直点不开的——不是判据不严，是这个号早就没浏览器了。

现有的断开恢复（greet_engine：两个落点导航 + 一次重绑）全都要求浏览器还活着，
它自己在日志里也承认"重绑搜索页：开新标签页也起不来"。真会重开浏览器的是
`UnifiedBotLoop._try_reconnect_browser`，而那条路一个调用点都不在这个失败分支上
（greet_engine 的注释原话）。所以这里补的不是重试，是**探到进程没了就重开**，
而且不受内存回收那 30 分钟冷却的管——号停着一分钟就是实打实一分钟没人回。
"""
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import BrowserManager  # noqa: E402
from boss_bot.main_loop import UnifiedBotLoop  # noqa: E402


class 假响应:
    def read(self):
        return b"[]"

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def 管理(port=9222):
    m = BrowserManager.__new__(BrowserManager)
    m._debug_port = port
    return m


class Test端口探针:
    def test_拒连就是进程没了(self):
        def 打开(url, timeout):
            raise OSError(10061, "由于目标计算机积极拒绝，无法连接")
        assert 管理().端口还活着(打开=打开) is False

    def test_答话就是活着(self):
        看的 = []

        def 打开(url, timeout):
            看的.append(url)
            return 假响应()
        assert 管理(9223).端口还活着(打开=打开) is True
        assert "127.0.0.1:9223" in 看的[0], 看的
        assert "/json" in 看的[0], 看的

    def test_探针自己坏了要当成活着(self):
        """探不准就重开，等于拿一个可能好好的浏览器反复重开——两条腿正点着卡片呢。"""
        def 打开(url, timeout):
            raise RuntimeError("探针写错了")
        assert 管理().端口还活着(打开=打开) is True

    def test_没有自己的端口就不算进程没了(self):
        """连的是别人的浏览器（复用调试端口那种）时，拒连不该由我们来重开。"""
        def 打开(url, timeout):
            raise AssertionError("没有端口就不该去碰端口")
        assert 管理(0).端口还活着(打开=打开) is True


def 环(活着=True):
    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    记 = []
    lp.logs = []
    lp._log = lambda level, msg: lp.logs.append(f"[{level}] {msg}")
    lp.browser_manager = types.SimpleNamespace(
        端口还活着=lambda: 活着,
        sweep_idle_tabs=lambda: 0,
    )
    lp._轮深 = types.SimpleNamespace(n=0)
    lp._最后回收 = None
    lp._内存回收 = lambda: None
    lp._try_reconnect_browser = lambda: 记.append("重连")
    lp._进程没了已报 = False
    return lp, 记


class Test没了就重开:
    def test_拒连要立刻走重连(self):
        lp, 记 = 环(活着=False)
        assert lp._浏览器没了就重开() is True
        assert 记 == ["重连"], 记
        assert any("不在了" in m for m in lp.logs), lp.logs

    def test_活着什么都不动(self):
        lp, 记 = 环(活着=True)
        assert lp._浏览器没了就重开() is False
        assert 记 == []

    def test_刚回收过也照样重开(self):
        """内存那条要等 30 分钟，进程没了等不了。"""
        lp, 记 = 环(活着=False)
        lp._最后回收 = time.time()          # 回收冷却还没到
        assert lp._浏览器没了就重开() is True
        assert 记[:1] == ["重连"], 记

    def test_重连不逐单刷屏(self):
        """每一单/每一候选都探一次，重开那句只能说一遍，否则日志淹掉。"""
        lp, 记 = 环(活着=False)
        lp._浏览器没了就重开()
        lp._浏览器没了就重开()
        lp._浏览器没了就重开()
        assert 记 == ["重连"] * 3, 记
        assert sum(1 for m in lp.logs if "不在了" in m) == 1, lp.logs

    def test_浏览器回来之后那句要能再说(self):
        lp, 记 = 环(活着=False)
        lp._浏览器没了就重开()
        lp.browser_manager.端口还活着 = lambda: True
        lp._浏览器没了就重开()
        assert lp._进程没了已报 is False, "下回再死一次就该重新说一遍"

    def test_轮次收口真会探这一趟(self):
        """挂在 `_跑一轮` 的收口上：两条腿每一轮出来都经过那里。"""
        lp, 记 = 环(活着=False)
        assert lp._跑一轮(lambda: "返回值") == "返回值"
        assert "重连" in 记, 记

    def test_探针异常不许把整轮带崩(self):
        lp, 记 = 环()
        lp.browser_manager.端口还活着 = lambda: (_ for _ in ()).throw(RuntimeError("坏了"))
        assert lp._跑一轮(lambda: 1) == 1
        assert 记 == [], 记


class Test判据只有一份:
    def test_主循环里真有探活这一手(self):
        src = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert "端口还活着" in src, "主循环里没有探活，断开恢复失败就还是只写一句日志"
