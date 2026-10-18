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
    def _循环(self, 阈值=1600, 量的=2000.0, 刚回收过=None, 收了=0, 扫后=None):
        """量的 = 还没清扫时的真实占用，扫后 = 清扫完几张闲置页之后的占用。

        memory_mb 按"清扫有没有做过"回不同的数，这样判据用错顺序也会被抓住：
        拿清扫前的旧数去决定要不要重开，等于白收几张页还要把整个浏览器推倒。
        """
        cfg = UnifiedConfig()
        cfg.browser.memory_recycle_mb = 阈值
        日志, 重建, 扫过 = [], [], []
        现场 = {"扫过了": False}

        def _扫():
            扫过.append(1)
            现场["扫过了"] = True
            return 收了

        def _量():
            return 扫后 if (现场["扫过了"] and 扫后 is not None) else 量的

        loop = types.SimpleNamespace(
            config=cfg, _login_headed_temp=False, 内存回收冷却秒=1800,
            browser_manager=types.SimpleNamespace(memory_mb=_量, sweep_idle_tabs=_扫,
                                                  端口还活着=lambda: True),
            _重建浏览器=lambda: (重建.append(量的), {"前": 量的, "后": 400.0})[1],
            _最后回收=刚回收过 or 0.0,
            _回收受阻已报=False, _进程没了已报=False, _轮深=types.SimpleNamespace(n=0),
            日志=日志, 重建=重建, 扫过=扫过, _log=lambda lvl, msg: 日志.append((lvl, msg)))
        loop._清扫闲置页 = types.MethodType(ML.UnifiedBotLoop._清扫闲置页, loop)
        loop._浏览器没了就重开 = types.MethodType(ML.UnifiedBotLoop._浏览器没了就重开, loop)
        loop._try_reconnect_browser = lambda: 重建.append("重连")
        loop._内存回收 = types.MethodType(ML.UnifiedBotLoop._内存回收, loop)
        loop._跑一轮 = types.MethodType(ML.UnifiedBotLoop._跑一轮, loop)
        return loop

    def _跑(self, loop):
        return loop._内存回收()

    def _跑一圈(self, loop):
        """走真的收口：一趟活出来先清扫、最外层那趟再判要不要重开。"""
        return loop._跑一轮(lambda: None)

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

    # ── 轮次收尾的标签页清扫（一张岗位详情页约 400 MB）────────────────
    def test_每轮收尾都先清扫闲置标签页(self):
        """没到阈值也要扫：漏下来的 job_detail 就是内存慢慢涨上去的那部分。

        现场：10-16 10:12 只读探 9223，账号2 的聊天页与搜索页之外挂着 3 张
        job_detail——按"这一次点击的差集"关的那条路漏下了它们。
        """
        loop = self._循环(量的=900.0, 收了=3)

        self._跑一圈(loop)

        assert loop.扫过 == [1], "收口不扫，漏下来的页就一直挂到重启"
        assert any("3 张" in m for _, m in loop.日志), loop.日志

    def test_清扫之后才量内存(self):
        """判据必须用清扫之后的读数：能靠收几张页解决的，不必打断两条腿重开。"""
        loop = self._循环(阈值=1600, 量的=2400.0, 扫后=1200.0, 收了=2)

        self._跑一圈(loop)

        assert loop.重建 == [], "清扫后已低于阈值，重开浏览器是白打断一轮"

    def test_清扫完还是超阈值就照旧重开(self):
        loop = self._循环(阈值=1600, 量的=2400.0, 扫后=2000.0, 收了=2)

        self._跑一圈(loop)

        assert loop.重建 == [2400.0]

    def test_关掉回收开关也要照样清扫(self):
        """memory_recycle_mb=0 关的是"重开浏览器"，不是"别把没人认领的页攒着"。"""
        loop = self._循环(阈值=0, 量的=9000.0, 收了=1)

        self._跑一圈(loop)

        assert loop.扫过 == [1]
        assert loop.重建 == []

    def test_嵌套的轮次也要清扫(self):
        """清扫每一层出来都做；只有"重开浏览器"才要等最外层那趟做完。"""
        loop = self._循环(量的=900.0, 收了=1)
        loop.扫过.clear()
        loop._轮深.n = 3            # 全量同步套着回复轮的那种嵌套

        loop._跑一轮(lambda: None)

        assert loop.扫过 == [1], "里面那层出来也要扫，不然嵌套期间永远不扫"
        assert loop.重建 == [], "里面那层不许重开浏览器"
        assert loop._轮深.n == 3, "层数要还原成进来时那样，别把外面的账销了"

    def test_两条腿各自的轮次末尾都扫(self):
        """层数收口靠不住，两条腿都得在自己的"轮次结束"那句上扫。

        10-16 11:43:51 账号2 的「回复轮次结束」之后日志里没有一条 9223 的清扫——
        同一个进程里主账号有。只挂在那一层，就有一个号一辈子不扫。
        """
        src = (Path(__file__).resolve().parents[1] / "boss_bot" / "main_loop.py"
               ).read_text(encoding="utf-8")
        for 锚 in ("━━━ 打招呼轮次结束 ━━━", "━━━ 回复轮次结束 ━━━"):
            at = src.index(锚)
            assert "self._清扫闲置页()" in src[max(0, at - 400):at + 40], 锚

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
        loop._清扫闲置页 = lambda: 记.append("清扫")
        loop._浏览器没了就重开 = lambda: 记.append("探活")
        loop._log = lambda lvl, msg: None
        loop._跑一轮 = lambda 做: ML.UnifiedBotLoop._跑一轮(loop, 做)
        return loop

    def test_最外层一轮跑完才试一次回收(self):
        loop = self._循环()
        got = ML.UnifiedBotLoop._跑一轮(loop, lambda: "本轮结果")
        assert got == "本轮结果", "轮次的返回值要原样传回去（打招呼腿拿它判断有没有岗位）"
        # 探活排在回收之前：进程没了等不了那 30 分钟冷却，先把它救回来
        assert loop._回收 == ["清扫", "探活", "回收"]

    def test_套在上一趟活里面不回收(self):
        """启动全量同步里插着回复轮：插的那轮跑完时同步还没做完，不能换浏览器。"""
        loop = self._循环()

        def 里面():
            return ML.UnifiedBotLoop._跑一轮(loop, lambda: "内层")

        assert ML.UnifiedBotLoop._跑一轮(loop, 里面) == "内层"
        assert [x for x in loop._回收 if x == "回收"] == ["回收"], \
            "只有最外层出来那一次才算空档"
        assert loop._回收.count("清扫") == 2, "两层出来各扫一次（清扫不打断活，随便做）"

    def test_轮次里抛异常也要把层数降回来(self):
        loop = self._循环()
        with pytest.raises(ValueError):
            ML.UnifiedBotLoop._跑一轮(loop, lambda: (_ for _ in ()).throw(ValueError()))
        assert loop._轮深.n == 0, "层数留在上面就再也不会回收了"
        ML.UnifiedBotLoop._跑一轮(loop, lambda: 1)
        assert [x for x in loop._回收 if x == "回收"] == ["回收", "回收"], \
            "抛错那趟和下一趟都要有那一次检查"

    def test_回收本身抛错不能带崩轮次(self):
        loop = types.SimpleNamespace(_轮深=__import__("threading").local(),
                                     _内存回收=lambda: (_ for _ in ()).throw(RuntimeError("量内存失败")),
                                     _清扫闲置页=lambda: 0,
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
