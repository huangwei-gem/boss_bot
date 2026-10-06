# -*- coding: utf-8 -*-
"""打招呼被机器判定暂停之后，必须能自己醒过来。

实测 2026-10-06：两个号各投满 120 单撞上 BOSS 当日沟通额度，搜索页跟着返回空结果，
07:09「连续3轮搜索结果为0」把打招呼置成暂停 —— 而这条暂停只有人工点「恢复」才会解。
到 11:40 我查状态时两个号还是 greet_paused=True，等于机器人悄悄停了四个半小时。
用户要的是"一直投递"，所以任何机器自己判出来的暂停都得带过期时间；
只有他亲手点的那颗暂停按钮才允许一直挂着。
"""
import inspect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boss_bot.main_loop import GREET_PAUSE_RETRY_SEC, UnifiedBotLoop  # noqa: E402


def _loop():
    loop = UnifiedBotLoop.__new__(UnifiedBotLoop)
    loop._greet_paused = False
    loop._greet_paused_by_cap = False
    loop._greet_cap_paused_on = ""
    loop._greet_auto_resume_at = 0.0
    loop._greet_engine = None
    loop.account_index = 0
    loop._logs = []
    loop._log = lambda level, msg: loop._logs.append((level, msg))
    return loop


def test_到点要自己醒():
    loop = _loop()
    loop._greet_paused = True
    loop._greet_auto_resume_at = time.time() - 1
    loop._maybe_auto_resume_greet()
    assert loop._greet_paused is False, "过了重试时间点还挂着，就是悄悄停机的老 bug"
    assert loop._greet_auto_resume_at == 0.0, "醒过来要把时间清掉，否则下一轮又立刻判成到期"
    assert any("恢复" in m for _, m in loop._logs), "自己醒了得在日志里说一声"


def test_没到点先不动():
    loop = _loop()
    loop._greet_paused = True
    loop._greet_auto_resume_at = time.time() + GREET_PAUSE_RETRY_SEC
    loop._maybe_auto_resume_greet()
    assert loop._greet_paused is True


def test_人工点的暂停不许自动醒():
    """用户按「暂停打招呼」是要接手去 BOSS 上操作，机器自己跑回去会把他的事搅黄。"""
    loop = _loop()
    loop._greet_paused = True
    loop._greet_auto_resume_at = 0.0
    loop._maybe_auto_resume_greet()
    assert loop._greet_paused is True


def test_投满上限的暂停仍按跨零点恢复():
    from datetime import date
    loop = _loop()
    loop._greet_paused = True
    loop._greet_paused_by_cap = True
    loop._greet_cap_paused_on = date.today().isoformat()
    loop._maybe_auto_resume_greet()
    assert loop._greet_paused is True, "当天投满就是投满了，不该十几分钟后又去撞额度"


def test_重试时长在十分钟到半小时之间():
    """再快会反复撞同一堵空页，再慢就等于今天不投了。"""
    assert 600 <= GREET_PAUSE_RETRY_SEC <= 1800


def test_两处机器暂停都要记下恢复时间():
    """空搜索和验证码连击这两处是机器自己判的，漏一处就还有一条悄悄死掉的路。"""
    loop_src = inspect.getsource(UnifiedBotLoop._greet_loop)
    gate_src = inspect.getsource(UnifiedBotLoop._captcha_gate)
    for src, where in ((loop_src, "连续空搜索"), (gate_src, "验证码连击")):
        assert "self._greet_auto_resume_at = time.time() + GREET_PAUSE_RETRY_SEC" in src, \
            f"{where} 这条暂停没设恢复时间，会永久挂着"


def test_人工暂停入口要清掉自动恢复时间():
    src = inspect.getsource(UnifiedBotLoop.pause_greet)
    assert "self._greet_auto_resume_at = 0.0" in src, \
        "暂停按钮不清时间的话，上一轮机器设的到期时间会让用户手点的暂停自己跑掉"
