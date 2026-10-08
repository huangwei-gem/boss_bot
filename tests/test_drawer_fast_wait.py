# -*- coding: utf-8 -*-
"""点完「立即沟通」要快速判定结局，别把岗位逐个空等过去。

现场（2026-10-05 20:12–20:19，主账号）：两个岗位从点击到认输各花 3.5 分钟
（20:16:17 点击 → 20:19:51 才记"未找到输入框"），而这 3.5 分钟里成功的岗位
只要 60–70 秒。空等的钱都花在同一处：十个输入框选择器每个 timeout=3 轮着试、
试三轮，最后才去看 BOSS 的"已自动发送"弹窗。
更糟的是那个弹窗常常来得比这轮空等晚——20:20 只读探针在同一个岗位页上明明
看到"已向BOSS发送消息…留在此页/继续沟通"，而引擎 20:19:51 已经按失败收工了，
于是 BOSS 上其实投出去了的岗位在我们记录里是红的。

这里锁四件事：点完之后先用一次 JS 探测（抽屉 / 自动发送弹窗）再决定要不要
继续等；探测必须认得出可见性（页面上挂着 18 个隐藏的登录模板输入框）；
探测不到才走原来的慢路径，成功分支不许被改动；弹窗要尽早识别。
"""
import inspect
import math

from boss_bot.greet_engine import (DRAWER_READY_JS, DRAWER_READY_POLL_SEC,
                                   DRAWER_READY_TIMEOUT_SEC, GreetEngine,
                                   parse_drawer_probe)


def _engine(sleep_log=None):
    eng = GreetEngine.__new__(GreetEngine)
    eng.running = True
    eng._log = lambda *a, **k: None
    eng._interruptible_sleep = lambda s: (sleep_log.append(s) if sleep_log is not None else None)
    return eng


class FakeInstance:
    """按脚本回答 run_js；答完就一直说"啥也没有"。"""

    def __init__(self, answers=()):
        self.answers = list(answers)
        self.calls = 0

    def run_js(self, js, *a, **k):
        self.calls += 1
        if self.answers:
            return self.answers.pop(0)
        return '{"drawer": false, "dialog": false}'


def _ans(drawer=False, dialog=False):
    return '{"drawer": %s, "dialog": %s}' % ("true" if drawer else "false",
                                             "true" if dialog else "false")


def test_探针结果要能解析():
    assert parse_drawer_probe(_ans(drawer=True)) == {"drawer": True, "dialog": False}
    assert parse_drawer_probe("") == {}
    assert parse_drawer_probe("不是 json") == {}
    assert parse_drawer_probe(None) == {}


def test_探针JS要求输入框真的可见():
    """失败的现场页面上有 18 个隐藏模板输入框（ipt-phone 那些），
    不看可见性就会把"页面挂着登录模板"当成"抽屉已经弹出来了"。"""
    js = DRAWER_READY_JS
    assert "getBoundingClientRect" in js and "getComputedStyle" in js
    assert "#chat-input" in js and "contenteditable" in js


def test_探针JS顺带认出BOSS自动发送弹窗():
    """弹窗文案与 AUTO_GREET_PROBE_JS 同源：「已向BOSS发送…」+「留在此页」。
    缺一条就不算，单看"继续沟通"会把岗位页本来就挂着的按钮误判成已发送。"""
    js = DRAWER_READY_JS
    assert "已向BOSS发送" in js and "留在此页" in js
    assert 'class*="tip"' not in js, "宽匹配抓到过顶导航「南通招聘」"


def test_抽屉一出现就立刻收():
    eng = _engine()
    ins = FakeInstance([_ans(), _ans(), _ans(drawer=True)])
    assert eng._wait_drawer_or_dialog(ins) == "drawer"
    assert ins.calls == 3, "探测就该一次 JS 一次，别一个选择器等 3 秒"


def test_自动发送弹窗也算命中且优先级不压过抽屉():
    eng = _engine()
    ins = FakeInstance([_ans(dialog=True)])
    assert eng._wait_drawer_or_dialog(ins) == "dialog"
    eng2 = _engine()
    ins2 = FakeInstance([_ans(drawer=True, dialog=True)])
    assert eng2._wait_drawer_or_dialog(ins2) == "drawer", "抽屉能输入，先走正常发送"


def test_什么都没等到就按预算收手():
    """预算就是最坏情况的多花时间；再往下就该走原路径而不是无限等。"""
    slept = []
    eng = _engine(slept)
    ins = FakeInstance()
    assert eng._wait_drawer_or_dialog(ins) == ""
    assert sum(slept) <= DRAWER_READY_TIMEOUT_SEC
    assert len(slept) <= math.ceil(DRAWER_READY_TIMEOUT_SEC / DRAWER_READY_POLL_SEC)


def test_停了就不再探测():
    eng = _engine()
    eng.running = False
    ins = FakeInstance([_ans(drawer=True)])
    assert eng._wait_drawer_or_dialog(ins) == ""
    assert ins.calls == 0


def test_探针报错不能把整个岗位带崩():
    class Boom:
        def run_js(self, *a, **k):
            raise RuntimeError("页面连接已断开")

    assert _engine()._wait_drawer_or_dialog(Boom()) == ""


def test_点击后先探测再决定要不要固定等待():
    """成功路径不能改：探测到抽屉就跳过 8–12 秒死等，探测不到才照原样等、
    照原样扫选择器（万一 BOSS 换了弹窗形态，慢路径仍然是兜底）。"""
    src = inspect.getsource(GreetEngine._apply_job_inner)
    probe = "self._wait_drawer_or_dialog(instance)"
    assert probe in src
    assert src.index(probe) < src.index("self._random_delay(8, 12)"), \
        "先探测再等，否则等于什么都没省"
    assert "if not drawer_ready:" in src, "固定等待与选择器扫描要收进「没探测到」的分支里"
    assert "self._auto_greet_path(instance, job, greeting)" in src, \
        "早点看到的弹窗要当场结算，别等空转到超时"


def test_自动发送结算路径与原逻辑同源():
    """抽出来的 _auto_greet_path 必须还是那三条出口：核对上/没核对上/没有弹窗，
    少一条就会把"平台已替我们发出去"重新记成失败。"""
    src = inspect.getsource(GreetEngine._auto_greet_path)
    assert "self._auto_greet_dialog(instance)" in src
    assert "self._auto_greet_followup(instance, greeting, dialog, job)" in src
    assert "self._record_sent_now(job)" in src
    assert "return None" in src, "没有弹窗要交回原路径继续走"
