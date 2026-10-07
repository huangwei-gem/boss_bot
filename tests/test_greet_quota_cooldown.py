# -*- coding: utf-8 -*-
"""账号当日沟通额度用完后要能停手，而不是整夜每 7 分钟白试一次。

现场（2026-10-04）：账号2 投到 118 单后，从 14:32 起 36 次打招呼全部失败在
同一句话上——"点了「立即沟通」但聊天抽屉没在这个标签页里出现"。
这段时间它每试一个岗位都要跑完 搜索→详情→AI 判分→点沟通→找输入框 全套，
既不出活又把页面访问节奏拉得很怪。这里锁三件事：
原因要说清页面提示了什么、连续几次后进冷却、冷却期不再碰浏览器。
"""
import inspect
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from boss_bot.greet_engine import (NO_DRAWER_COOLDOWN_SEC, NO_DRAWER_REASON,
                                   NO_DRAWER_STREAK_LIMIT, GreetEngine,
                                   chat_failure_reason)


def _engine():
    """只装冷却这一段逻辑要用的字段，不起浏览器。"""
    eng = GreetEngine.__new__(GreetEngine)
    eng.running = True
    eng._log = lambda *a, **k: None
    eng._no_drawer_streak = 0
    eng._no_drawer_recent = []
    eng._applied_recent = []
    eng._greet_cooldown_reason = ""
    eng._greet_cooldown_until = 0.0
    eng.browser_manager = MagicMock()
    # 冷却检查在招呼语检查之前，但 _greeting_for 要读账号配置：
    # 这个裸实例没有，给一条现成的免得别的分支炸在配置上
    eng._greeting_for = lambda job: ("您好，我对这个岗位很感兴趣", "测试用")
    return eng


def test_抽屉没出现时页面提示要一起写进原因():
    """BOSS 把"今日沟通次数已用完"只写在 toast 里。不抓下来，
    日志就只剩"抽屉没出现"，分不清是额度、风控还是页面卡住。"""
    snap = {"url": "https://www.zhipin.com/job_detail/abc.html",
            "chat_elements": [], "inputs": [], "toast": "今日沟通次数已达上限"}
    assert chat_failure_reason(snap) == NO_DRAWER_REASON + "；页面提示：今日沟通次数已达上限"


def test_没有提示时原因保持原样():
    """老快照里没有 toast 字段，原因串不能被改成带尾巴的新文案——
    记录表里同一类跳过要能归到一组。"""
    snap = {"url": "https://www.zhipin.com/job_detail/abc.html",
            "chat_elements": [], "inputs": []}
    assert chat_failure_reason(snap) == NO_DRAWER_REASON


def test_冷却期内不再碰浏览器():
    eng = _engine()
    eng._no_drawer_streak = NO_DRAWER_STREAK_LIMIT
    eng._greet_cooldown_until = time.time() + 600

    ok, reason = eng._apply_job_inner({"url": "https://www.zhipin.com/job_detail/x.html"})

    assert ok is False
    assert "打招呼已冷却" in reason and "疑似本号当日沟通额度用完" in reason
    assert "10 分钟后重试" in reason, "要报出还剩几分钟，光说冷却中没法判断等到什么时候"
    eng.browser_manager.get_instance.assert_not_called()


def test_冷却到点就放行():
    eng = _engine()
    eng._greet_cooldown_until = time.time() - 1
    # 到点后应该继续往下走（这里没有浏览器，走到取实例就抛），
    # 关键是别再把岗位按"冷却中"挡回去
    try:
        eng._apply_job_inner({"url": "https://www.zhipin.com/job_detail/x.html"})
    except Exception:
        pass
    eng.browser_manager.get_instance.assert_called()


def test_计数与停手写在同一条失败路径上():
    """三处必须都在：数次数、到限进冷却、成功清零。少一处就会永远冷却或永远不停。"""
    body = inspect.getsource(GreetEngine._apply_job_inner)
    assert "self._note_drawer_fail(" in body
    assert "self._drawer_quota_hit(" in body
    assert ">= NO_DRAWER_STREAK_LIMIT" in body
    assert "self._greet_cooldown_until = time.time() + NO_DRAWER_COOLDOWN_SEC" in body
    assert "else:\n                    self._no_drawer_streak = 0" in body, \
        "换了别的失败原因要把连续计数清零，否则攒够三次就误停"
    投 = inspect.getsource(GreetEngine.send_greeting)
    assert "self._note_applied()" in 投, "投成功要记账，窗口那份账靠它才判得出来"


def test_冷却时长是一个半小时以内():
    """太长就等于今天不再投递；BOSS 的额度按天回，半小时够避开临时限流。"""
    assert 300 <= NO_DRAWER_COOLDOWN_SEC <= 3600
    assert NO_DRAWER_STREAK_LIMIT == 3


def test_提示探针不许用宽匹配():
    """第一版写了 [class*="limit"]、[class*="tip-txt"]，结果抓到过顶导航"南通招聘"，
    等于往跳过原因里塞一句无关的话。toast 探针只认浮层自己的类名和 role，
    并且要求它是 fixed/absolute 悬在页面上的。"""
    import inspect
    src = inspect.getsource(inspect.getmodule(GreetEngine))
    probe = src[src.index("var toast ="):src.index("return JSON.stringify({url:")]
    assert '[class*="limit"]' not in probe and '[class*="tip-txt"]' not in probe
    assert "position" in probe, "没要求 fixed/absolute，页面里任何元素都能被当提示"


def test_冷却是在这一轮中途挂上的也要整轮收手():
    """2026-10-05 21:54 现场：账号2 投到 118 单后当轮里挂上冷却，剩下的岗位还是一个
    个点开再被岗位级闸门弹回去，两分钟刷出 6 条一模一样的"已冷却"跳过记录。
    轮首那道闸门只挡得住"上一轮就已经冷却"的情况。"""
    from unittest.mock import MagicMock

    from boss_bot.main_loop import UnifiedBotLoop

    loop = UnifiedBotLoop.__new__(UnifiedBotLoop)
    loop._running = True
    loop._greet_paused = False
    loop._log = lambda *a, **k: None
    loop._stats_dict = {"greet_total": 0, "greet_applied": 0,
                        "greet_skipped": 0, "greet_rounds": 0}
    loop._build_greet_tasks = lambda: [{"query": "数据分析", "city": "全国"}]

    eng = MagicMock()
    eng.search_jobs.return_value = [{"job_name": f"岗位{i}"} for i in range(8)]
    eng._rate_limit_enabled = False
    hits = []

    def left():
        hits.append(1)
        # 只有轮首那一次是"还没冷却"，之后一律算冷却中：
        # 岗位循环里只要还在逐个调用，就会露出来
        return 0.0 if len(hits) == 1 else 25 * 60.0

    eng.greet_cooldown_left = left
    loop._greet_engine = eng

    assert loop._run_greet_round() is True, "返回 False 会被当空轮，三轮就永久停打招呼了"
    assert len(hits) <= 2, f"冷却挂上后还在逐岗位往下走（探测了 {len(hits)} 次）"
    eng._apply_job_inner.assert_not_called()
    eng.apply_job.assert_not_called()



# ── 冷却期整轮跳过 ──
# 闸门加在 _apply_job_inner 里之后，线上观察到的新毛病：打招呼线程每 30 秒起一轮，
# 一轮里有几十上百个岗位，每个岗位都要走到闸门才返回"已冷却"，
# 于是 2026-10-04 23:40 起 1 分钟内攒了 22 条跳过记录（整份记录才 1117 条）。
# 用户看到的"记录全红"就是这个；更糟的是岗位被这一轮过完，冷却结束也不会回头补投。


def test_剩余冷却时间可读():
    """轮次要在进岗位循环之前就判断该不该跑，所以引擎得把剩余秒数交出去。"""
    eng = _engine()
    assert eng.greet_cooldown_left() == 0.0
    eng._greet_cooldown_until = time.time() + 600
    assert 590 <= eng.greet_cooldown_left() <= 600


def test_冷却中整轮不搜索不建记录():
    """一轮都不该开：既不搜索也不落记录，只报一句还剩几分钟。"""
    from boss_bot.main_loop import UnifiedBotLoop

    calls = []
    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    lp._log = lambda *a, **k: calls.append(("log", a[1] if len(a) > 1 else ""))
    lp._stats_dict = {"greet_rounds": 0}
    lp._probe_used_this_round = 0
    lp._running = True
    lp._greet_paused = False
    lp._hot_reload_config = lambda: None

    def _boom():
        raise AssertionError("冷却中不该构建任务/搜索岗位")

    lp._build_greet_tasks = _boom
    lp._greet_engine = SimpleNamespace(
        greet_cooldown_left=lambda: 900.0,
        search_jobs=_boom,
        account_index=1,
    )

    assert lp._run_greet_round() is True, \
        "返回 False 会被当成空搜索，连续三轮就把打招呼永久暂停了"
    assert not any("打招呼任务" in str(c) for c in calls)


def test_没冷却时照常构建任务():
    from boss_bot.main_loop import UnifiedBotLoop

    lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
    lp._log = lambda *a, **k: None
    lp._stats_dict = {"greet_rounds": 0}
    lp._probe_used_this_round = 0
    lp._running = True
    lp._greet_paused = False
    lp._hot_reload_config = lambda: None
    lp._greet_engine = SimpleNamespace(greet_cooldown_left=lambda: 0.0)
    built = []
    lp._build_greet_tasks = lambda: built.append(1) or []

    assert lp._run_greet_round() is False
    assert built, "没冷却就必须照常搜索，别把这条闸门变成永远不投递"



def _窗口引擎():
    """按时间窗判额度：失败与成功的时间戳都要能灌进去。"""
    eng = _engine()
    eng._no_drawer_recent = []
    eng._applied_recent = []
    return eng


def test_夹着成功的交替失败也要停手():
    """2026-10-07 白天那 41 条就是这么漏的：老口径要"连续 3 次"，一次成功就清零。

    真实形状是 失败,成功,失败,成功… 交替——投得动几单、又连着点不出抽屉，
    这种就是账号层面的额度在限流，不是每个岗位各自的问题。
    """
    eng = _窗口引擎()
    for i in range(8):
        eng._note_drawer_fail(now=1000.0 + i * 100)
        if i < 7:
            eng._note_applied(now=1000.0 + i * 100 + 50)
    话 = eng._drawer_quota_hit(now=1900.0)
    assert 话, "交替失败没触发窗口判定，还是会整晚一单一单白试"
    assert "额度" in 话 and "8" in 话, 话


def test_投得动的时候不许误停():
    """失败 6 次但成功 25 次：说明是偶发页面抖动，不能停 30 分钟。"""
    eng = _窗口引擎()
    for i in range(6):
        eng._note_drawer_fail(now=1000.0 + i * 300)
    for i in range(25):
        eng._note_applied(now=1000.0 + i * 70)
    assert eng._drawer_quota_hit(now=2800.0) == ""


def test_窗口外的老账不算():
    eng = _窗口引擎()
    for i in range(8):
        eng._note_drawer_fail(now=1000.0 + i * 1200)      # 每 20 分钟一次，跨 2.6 小时
    assert eng._drawer_quota_hit(now=1000.0 + 8 * 1200) == "", "只该看窗口内那一截"


def test_成功清零只清连续不清窗口():
    """连续计数清零是对的，窗口里的历史还得留着——否则永远攒不到。"""
    eng = _窗口引擎()
    eng._note_drawer_fail(now=1000.0)
    eng._note_applied(now=1100.0)
    assert eng._no_drawer_streak == 0
    assert len(eng._no_drawer_recent) == 1


def test_接线还在同一条失败路径上():
    """窗口判定必须挂在原来那条计数路径里，别另起一处，不然又是一份没人调的孤本。"""
    import inspect
    body = inspect.getsource(GreetEngine._apply_job_inner)
    assert "_note_drawer_fail(" in body
    assert "_drawer_quota_hit(" in body
    send = inspect.getsource(GreetEngine.send_greeting)
    assert "_note_applied(" in send
