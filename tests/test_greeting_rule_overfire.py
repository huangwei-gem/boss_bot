# -*- coding: utf-8 -*-
"""关键词规则是子串匹配，打招呼那几个键会把带"您好"开头的正事当成开场白。

实测 2026-10-06 20:54 账号2：HR 问
「您好，咱们之前做过类似数据标注的工作吗，会运用wps软件和熟悉常用的快捷键吗」
规则表用"您好"命中，回了一句「您好！我对这个岗位很感兴趣，方便了解一下具体情况吗？」
——答非所问，还把这一单聊死了。当天这种"规则代答"有 18 条。

打招呼的词只在该会话确实只是在打招呼时才该接话；后面还跟了别的内容，
就该让给意图/AI 按上下文回，所以判据是"把招呼词和标点去掉之后还剩不剩话"。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.rules import RuleEngine  # noqa: E402

开场白 = "您好！我对这个岗位很感兴趣，方便了解一下具体情况吗？"
招呼表 = {"你好": 开场白, "您好": 开场白, "在吗": 开场白, "在不在": 开场白}


def _引擎(extra=None):
    rules = dict(招呼表)
    rules.update(extra or {})
    return RuleEngine(rules)


def test_纯打招呼还是走规则():
    for text in ("您好", "你好呀", "在吗", "您好～", "在不在", "你好，在吗"):
        assert _引擎().match(text)[1] == 开场白, text


def test_打招呼后面带正事就不许代答():
    """HR 以"您好"开头问技能，规则层必须让路。"""
    for text in ("您好，咱们之前做过类似数据标注的工作吗，会运用wps软件吗",
                 "你好，请问你什么时候能到岗？",
                 "在吗？想跟你确认一下面试安排"):
        assert _引擎().match(text) is None, text


def test_其他关键词不受影响():
    assert _引擎({"薪资": "面议"}).match("您好，请问薪资多少？")[1] == "面议"


def test_线上那份规则表里确实有打招呼键():
    """这条是前提：内置表用裸"您好"当子串键，才会在真消息上误命中。"""
    from boss_bot.config import REPLY_RULES
    assert "您好" in REPLY_RULES and "你好" in REPLY_RULES
