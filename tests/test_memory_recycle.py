# -*- coding: utf-8 -*-
"""浏览器占的内存要有上限：一天跑下来那两个号会涨到 2.5 GB 左右。

2026-10-10 实测：刚重开时两个号 404 + 328 MB，跑到当天晚些时候整棵树接近 2.5 GB，
这台 15.3 GB 的机器上可用只剩 0.5 GB，整个人都卡。启动参数那条路已经试过收不住
（无头 2479 vs 有头 2484，不省），能兜住的只有一件事：涨过阈值就在轮次空档把
浏览器重开一次（会话在 profile 目录里，重开不掉登录）。
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import boss_bot.browser_launcher as BL
import boss_bot.main_loop as ML
from boss_bot.unified_config import UnifiedConfig


def _假psutil(monkeypatch, 每进程MB):
    class _P:
        def __init__(self, mb):
            self.memory_info = lambda: types.SimpleNamespace(rss=mb * 1048576)

        @staticmethod
        def children(recursive=False):
            return []

    class _Proc:
        def __init__(self, mb):
            self._mb = mb

        def __call__(self, pid):
            return _P(self._mb)

    假模块 = types.SimpleNamespace(Process=_Proc(每进程MB))
    monkeypatch.setitem(sys.modules, "psutil", 假模块)
    return 假模块


class Test量内存:
    def test_整棵树都要算进去(self, monkeypatch):
        """渲染进程挂在主进程下面，只数主进程会少算一半（10-08 量法踩过的坑）。"""
        class _P:
            def __init__(self, mb):
                self.memory_info = lambda: types.SimpleNamespace(rss=mb * 1048576)
                self.pid = 1

            def children(self, recursive=False):
                return [_P(300), _P(200)]

        假模块 = types.SimpleNamespace(Process=lambda pid: _P(500))
        monkeypatch.setitem(sys.modules, "psutil", 假模块)
        assert BL.tree_memory_mb(1234) == pytest.approx(1000.0)

    def test_拿不到进程就当零而不是抛异常(self, monkeypatch):
        """量不到就别回收——这条不能让轮次因为一个统计失败而炸掉。"""
        def 炸(pid):
            raise OSError("拒绝访问")
        monkeypatch.setitem(sys.modules, "psutil", types.SimpleNamespace(Process=炸))
        assert BL.tree_memory_mb(1) == 0.0

    def test_没有psutil时返回零(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "psutil", None)
        assert BL.tree_memory_mb(1) == 0.0

    def test_管理器按自己那个端口问(self, monkeypatch):
        cfg = UnifiedConfig()
        管 = BL.BrowserManager(config=cfg.browser, account_index=1, port=9401)
        问到的 = []
        monkeypatch.setattr(BL, "port_owner_pid", lambda p: 问到的.append(p) or 777)
        monkeypatch.setattr(BL, "tree_memory_mb", lambda pid: 1234.0 if pid == 777 else 0.0)
        assert 管.memory_mb() == 1234.0
        assert 问到的 == [9401]


class Test重开浏览器:
    def test_recycle只做关和起两步(self, monkeypatch):
        """标签页和引擎归主循环那条重建路径接（Cookie 要先加载再开页，顺序不能倒）。"""
        cfg = UnifiedConfig()
        管 = BL.BrowserManager(config=cfg.browser, account_index=0, port=9400)
        次序 = []
        monkeypatch.setattr(管, "close", lambda: 次序.append("close"))
        monkeypatch.setattr(BL.time, "sleep", lambda s: None)
        monkeypatch.setattr(管, "launch", lambda: 次序.append("launch"))
        读数 = iter([2100.0, 420.0])
        monkeypatch.setattr(管, "memory_mb", lambda: next(读数))
        结果 = 管.recycle()
        assert 次序 == ["close", "launch"]
        assert 结果 == {"前": 2100.0, "后": 420.0}

    def test_主循环重建时按起浏览器装Cookie接搜索页重跑引擎(self):
        """这条路径断线重连和内存回收共用，别再抄成两份。"""
        次序 = []

        class 管:
            def recycle(self):
                次序.append("recycle"); return {"前": 2000.0, "后": 400.0}
            def load_cookies(self, path):
                次序.append("cookie"); return True
            def get_search_page(self):
                次序.append("搜索页")
                return types.SimpleNamespace(url="https://www.zhipin.com/web/geek/jobs")

        loop = types.SimpleNamespace(
            browser_manager=管(), _cookie_file=lambda: "cookies.json",
            _init_engines=lambda: 次序.append("引擎"),
            日志=[], _log=lambda lvl, msg: None)
        ML.UnifiedBotLoop._重建浏览器(loop)
        assert 次序 == ["recycle", "cookie", "搜索页", "引擎"]

    def test_没配Cookie文件也要照样接回搜索页(self):
        次序 = []

        class 管:
            def recycle(self):
                次序.append("recycle"); return {"前": 1.0, "后": 1.0}
            def get_search_page(self):
                次序.append("搜索页"); return None

        loop = types.SimpleNamespace(
            browser_manager=管(), _cookie_file=lambda: "",
            _init_engines=lambda: 次序.append("引擎"),
            日志=[], _log=lambda lvl, msg: None)
        ML.UnifiedBotLoop._重建浏览器(loop)
        assert 次序 == ["recycle", "搜索页", "引擎"]



class Test什么时候该回收:
    def _循环(self, 阈值=1600, 量的=2000.0, 刚回收过=None):
        cfg = UnifiedConfig()
        cfg.browser.memory_recycle_mb = 阈值
        日志, 重建 = [], []
        loop = types.SimpleNamespace(
            config=cfg, _login_headed_temp=False, 内存回收冷却秒=1800,
            browser_manager=types.SimpleNamespace(memory_mb=lambda: 量的),
            _重建浏览器=lambda: (重建.append(量的), {"前": 量的, "后": 400.0})[1],
            _最后回收=刚回收过 or 0.0,
            _回收受阻已报=False,
            日志=日志, 重建=重建, _log=lambda lvl, msg: 日志.append((lvl, msg)))
        return loop

    def _跑(self, loop):
        return ML.UnifiedBotLoop._内存回收(loop)

    def test_超阈值就重开并报出前后(self):
        loop = self._循环()
        assert self._跑(loop) is True
        文本 = " ".join(m for _, m in loop.日志)
        assert "2000" in 文本 and "1600" in 文本, "要说清凭什么回收"

    def test_没到阈值一个进程都不动(self):
        loop = self._循环(量的=900.0)
        assert self._跑(loop) is False
        assert loop.日志 == []

    def test_阈值写零就是关掉(self):
        loop = self._循环(阈值=0, 量的=9000.0)
        assert self._跑(loop) is False

    def test_另一条腿在跑也要照样回收(self):
        """打招呼腿正在逐个点卡片拦不住回收——代价和断线重连一模一样。

        2026-10-10 真机：原来要求两条腿都空着，结果打招呼腿整晚都在轮次里，
        阈值 120、实际 507 MB 盯了一小时一次都没回收——等于这个开关白装。
        被打断那条腿本来就按"浏览器没了"处理（引擎会重新初始化），
        少回收一次才是没法自愈的那头。
        """
        loop = self._循环()
        assert self._跑(loop) is True
        assert loop.重建 == [2000.0]

    def test_冷却期内不连着重开(self):
        import time
        loop = self._循环(刚回收过=time.time() - 60)
        assert self._跑(loop) is False
        assert loop.重建 == []

    def test_超阈值卡在冷却要说一句但只说一遍(self):
        """不记这一句，日志里"内存一直不降"和"回收根本没开"长得一模一样。"""
        import time
        loop = self._循环(刚回收过=time.time() - 60)
        self._跑(loop)
        self._跑(loop)
        说 = [(级, 文) for 级, 文 in loop.日志 if 级 == "DEBUG"]
        assert len(说) == 1, "每个轮次边界都会走到这里，刷屏就没人看了"
        assert "2000" in 说[0][1] and "1600" in 说[0][1]

    def test_没到阈值不啰嗦(self):
        loop = self._循环(量的=900.0)
        self._跑(loop)
        assert loop.日志 == []

    def test_回收过一次之后冷却的话头要重新武装(self):
        import time
        loop = self._循环(刚回收过=time.time() - 60)
        self._跑(loop)                      # 说一句
        loop._最后回收 = 0.0
        assert self._跑(loop) is True       # 真回收
        loop._最后回收 = time.time()        # 又在冷却里
        self._跑(loop)
        assert len([1 for 级, _ in loop.日志 if 级 == "DEBUG"]) == 2

    def test_扫码那一下借的有头不能被抢回去(self):
        loop = self._循环()
        loop._login_headed_temp = True
        assert self._跑(loop) is False

    def test_回收之后要记下时刻(self):
        loop = self._循环()
        self._跑(loop)
        assert loop._最后回收 > 0


class Test什么时候算一轮跑完:
    """回收只挑"这趟活做到底"的那一刻，套在别人活中间的回收会把人拦腰砍。"""

    def _循环(self):
        记 = []
        loop = types.SimpleNamespace(_轮深=__import__("threading").local(), _回收=记)
        loop._内存回收 = lambda: 记.append("回收")
        loop._跑一轮 = lambda 做: ML.UnifiedBotLoop._跑一轮(loop, 做)
        return loop

    def test_最外层一轮跑完才试一次回收(self):
        loop = self._循环()
        got = ML.UnifiedBotLoop._跑一轮(loop, lambda: "本轮结果")
        assert got == "本轮结果", "轮次的返回值要原样传回去（打招呼腿拿它判断有没有岗位）"
        assert loop._回收 == ["回收"]

    def test_套在上一趟活里面不回收(self):
        """启动全量同步里插着回复轮：插的那轮跑完时同步还没做完，不能换浏览器。"""
        loop = self._循环()

        def 里面():
            return ML.UnifiedBotLoop._跑一轮(loop, lambda: "内层")

        assert ML.UnifiedBotLoop._跑一轮(loop, 里面) == "内层"
        assert loop._回收 == ["回收"], "只有最外层出来那一次才算空档"

    def test_轮次里抛异常也要把层数降回来(self):
        loop = self._循环()
        with pytest.raises(ValueError):
            ML.UnifiedBotLoop._跑一轮(loop, lambda: (_ for _ in ()).throw(ValueError()))
        assert loop._轮深.n == 0, "层数留在上面就再也不会回收了"
        ML.UnifiedBotLoop._跑一轮(loop, lambda: 1)
        assert loop._回收 == ["回收", "回收"], "抛错那趟和下一趟都要有那一次检查"

    def test_回收本身抛错不能带崩轮次(self):
        loop = types.SimpleNamespace(_轮深=__import__("threading").local(),
                                     _内存回收=lambda: (_ for _ in ()).throw(RuntimeError("量内存失败")),
                                     _log=lambda lvl, msg: None)
        assert ML.UnifiedBotLoop._跑一轮(loop, lambda: 1) == 1

    def test_包一层要挂在方法入口而不是五个调用点(self):
        """那五处的顺序（回复轮排在全量同步前面、补扫挂在轮后）都有按源码文本盯的回归。"""
        源 = open(r"C:\Users\35796\orca\boss_bot\boss_bot\main_loop.py", encoding="utf-8").read()
        for 方法名 in ("_run_greet_round", "_run_reply_round", "_run_followup_round",
                   "_run_resume_backfill", "_full_sync_chats"):
            assert '"%s"' % 方法名 in 源, "%s 没被登记进轮次记账" % 方法名
            assert 源.count("self._跑一轮") <= 3, "调用点被改成了手工记账，回归盯的是原文"


class Test配置和界面:
    def test_阈值有默认值并且能落盘(self, tmp_path, monkeypatch):
        import boss_bot.unified_config as UC
        monkeypatch.setattr(UC, "BOT_CONFIG_FILE", tmp_path / "bot_config.json")
        monkeypatch.setattr(UC, "OVERRIDES_FILE", tmp_path / "config_overrides.json")
        monkeypatch.setattr(UC, "USER_PROFILE_FILE", tmp_path / "user_profile.json")
        cfg = UnifiedConfig()
        assert cfg.browser.memory_recycle_mb == 1600
        cfg.browser.memory_recycle_mb = 1200
        cfg.save()
        assert UnifiedConfig.load().browser.memory_recycle_mb == 1200

    def test_面板上有这个输入框(self):
        html = (Path(__file__).parent.parent / "flask-version" / "templates"
                / "index.html").read_text(encoding="utf-8")
        assert 'id="advMemoryRecycle"' in html
        assert "memory_recycle_mb" in html, "输入框要真的写回配置，不然就是死控件"
