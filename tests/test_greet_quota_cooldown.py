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
    assert "self._no_drawer_streak += 1" in body
    assert ">= NO_DRAWER_STREAK_LIMIT" in body
    assert "self._greet_cooldown_until = time.time() + NO_DRAWER_COOLDOWN_SEC" in body
    assert "else:\n                    self._no_drawer_streak = 0" in body, \
        "换了别的失败原因要把计数清零，否则攒够三次就误停"
    send = inspect.getsource(GreetEngine.send_greeting)
    assert "self._no_drawer_streak = 0" in send, "投成功要清零"


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
