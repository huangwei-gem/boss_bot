# -*- coding: utf-8 -*-
"""HR 问"有没有经验"要直接答有经验。

用户 2026-10-08：「我是有标注的经验的，我所有的标注经验都有，数据分析经验也有，
AI的使用全部也是有的，这些你都写进去吧，以后问你的时候你可以直接说我是有经验的，
而且哈告诉你一个技巧，别人问你有没有经验直接说有经验就行，这是技巧，先拿下面试再说。」

以前这种问法落到 intent 表的 "other"，只能指望 AI 现编——而 AI 拿到的画像里
`experience` 一直是空串、系统提示词还写着"不要编造不存在的工作经历"，
于是它老老实实回"我可以学"。问一次丢一次机会。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.intent import classify
from boss_bot.prompts import build_system_prompt
from boss_bot.reply_engine import INTENT_REPLIES, reply_rejection


class 经验问答Test:
    def test_问经验认成独立意图(self):
        for 句 in ("请问您有相关经验吗？", "您之前做过标注这类工作吗", "有没有数据分析经验",
                   "您有这方面经验没有", "经验方面有吗", "干过这行没"):
            assert classify(句) == "ask_experience", 句

    def test_陈述经验要求不算提问(self):
        for 句 in ("经验不限，小白也能做", "我们有老带新，不需要经验",
                   "有经验者优先", "没做过也没关系，我们会教"):
            assert classify(句) != "ask_experience", 句

    def test_答话术直接用他给的那句(self):
        """他 12:07 原话：「有的 我做过标注，数据处理等项目」——开头就要是这句。"""
        动作, 话术 = INTENT_REPLIES["ask_experience"]
        assert 动作 == "text"
        assert 话术.startswith("有的"), 话术
        assert "标注" in 话术 and "数据处理" in 话术, 话术
        assert reply_rejection(话术) == "", 话术
        for 词 in ("Excel", "SQL", "AI"):
            assert 词 in 话术, 话术

    def test_画像里真的写了经历(self):
        """口径不能只活在话术里：AI 现编的那一路也要看得见这些经历。"""
        import json
        p = json.loads((ROOT / "user_profile.json").read_text(encoding="utf-8"))
        assert "标注" in p["experience"] and "AI" in p["experience"]
        assert p["skills"] and p["highlights"]
        assert "标注" in build_system_prompt(p)

    def test_提示词钉死这条口径(self):
        assert "直接答有经验" in build_system_prompt()

    def test_优先级不让问经验被别的意图抢走(self):
        from boss_bot.intent import INTENT_PRIORITY
        assert "ask_experience" in INTENT_PRIORITY
