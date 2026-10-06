# -*- coding: utf-8 -*-
"""探针判完"抽屉没来"之后，不许再按配置空转三轮扫描。

实测 2026-10-06 12:00：两个号都投满 120 单撞上 BOSS 当日沟通额度，点「立即沟通」
之后抽屉永远不出现。日志时间戳显示一单的走法是
    11:59:15 点击 → 12:00:06 弹窗容器没有 → 12:00:51 扫 iframe
    → 12:01:51 遍历标签页 → 12:02:21 重试 2/3 → 12:03:57 又扫一遍
一单 250 秒，而探针其实 20 秒内就说过"没有抽屉、也不是自动发送弹窗"。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boss_bot.greet_engine import input_lookup_attempts  # noqa: E402


def test_探针说没有就只扫一遍():
    """留一次是给探针看不见的 iframe/新标签页兜底，不再乘配置。"""
    assert input_lookup_attempts("", 3) == 1
    assert input_lookup_attempts(None, 3) == 1


def test_探针说有了才按配置重试():
    assert input_lookup_attempts("ready", 3) == 3


def test_配置为0也要至少扫一次():
    assert input_lookup_attempts("ready", 0) == 1
    assert input_lookup_attempts("", 0) == 1


def test_调用点真的用上了这个判据():
    """不卡这一条的话，改完函数但循环还照旧读配置，实测照样一单 250 秒。"""
    import inspect
    from boss_bot import greet_engine
    src = inspect.getsource(greet_engine)
    assert "_input_attempts = input_lookup_attempts(drawer_ready" in src, \
        "重试次数还是直接从配置来的，探针的结论没被用上"
