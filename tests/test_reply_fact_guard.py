# -*- coding: utf-8 -*-
"""AI 回的那句话不许替他说没影儿的事实。

2026-10-08 用户：「我现在才21啊，你别乱说我25啊，你看一下朱鑫磊的那个对话。」
盘上取证 messages/朱鑫磊_江苏昱泓玥拍卖.json：
  HR：你多大了，兄弟
  机器（reply_source=ai）：我今年25岁，期待有机会进一步沟通，了解岗位具体需求。
  HR：不是兄弟，你21                ← 是 HR 翻了他的简历在纠正我们
  机器：抱歉，是我表述有误，我实际是21岁。
同一形状还有 messages/a1_何女士_西安九鸿盛世网络科技.json：「我今年23岁」。
以前的出口校验只管"像不像一句人话"（长度、markdown、思考痕迹），不管事实，
所以 25 这种数就是能发出去；而年龄、学历阶段、工作年限这类数一旦报错，
对面是一个真人，改都改不回来。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.reply_engine import reply_rejection

画像 = {"age": 21, "education": "本科在读（统计学）", "contact": "",
        "experience": "有数据标注经验", "skills": ["SQL"], "position": "线上兼职",
        "salary_expectation": "面议", "available_interview_time": "工作日下午",
        "highlights": []}


class 年龄Test:
    def test_报大几岁要拦(self):
        assert reply_rejection("我今年25岁，可以兼职。", 画像) != ""

    def test_报小几岁也要拦(self):
        assert reply_rejection("我23岁，时间充裕。", 画像) != ""

    def test_照画像说21岁放行(self):
        assert reply_rejection("我今年21岁，工作日时间比较充裕。", 画像) == ""

    def test_画像没填年龄时一个岁字都不许出现(self):
        got = reply_rejection("我今年25岁。", {})
        assert got != "", got


class 学历与年限Test:
    def test_画像是在读就不许说毕业(self):
        assert reply_rejection("我本科毕业，现在是求职状态。", 画像) != ""

    def test_画像是在读也不许自己安年级(self):
        assert reply_rejection("大四课程不多，工作日基本可以安排面试。", 画像) != ""

    def test_照画像原话答放行(self):
        assert reply_rejection("我是本科在读，学的是统计学。", 画像) == ""

    def test_工作年限这类数也不许编(self):
        assert reply_rejection("我有3年数据分析经验。", 画像) != ""
        assert reply_rejection("做了三年标注了。", 画像) != ""


class 号码Test:
    def test_没有联系方式就不许报手机号(self):
        assert reply_rejection("我的微信同手机号 13800001234。", 画像) != ""

    def test_画像里真给的号码可以用(self):
        有号 = dict(画像, contact="13800001234")
        assert reply_rejection("我的号码是 13800001234。", 有号) == ""


class 接线Test:
    def test_招呼语那条链也用同一闸门(self):
        import inspect
        from boss_bot import greeting as G
        assert "reply_rejection" in inspect.getsource(G)

    def test_提示词把这条口径写给模型(self):
        from boss_bot.prompts import build_system_prompt
        sp = build_system_prompt(画像)
        assert "21" in sp, "画像里的年龄没进背景，模型只能猜"
        assert "只许照【你的背景】" in sp

    def test_画像里确实写着21(self):
        import json
        p = json.loads((ROOT / "user_profile.json").read_text(encoding="utf-8"))
        assert int(p.get("age") or 0) == 21, p.get("age")
