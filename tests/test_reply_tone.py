# -*- coding: utf-8 -*-
"""回复要有人味：上下文得真用上，但话里不许把上下文念出来。

用户 2026-10-17：「一定记得回答的时候一定要带上下文哈…记得不要回复上下文过程，
要有人味，我看有好多的人说我像机器人了…一定要记得要有人味，并且要参考书 30 行左右的上下文」。

盘上两条形状：
- 彭女士|轩辰物流 直接问「你是机器人吗」；
- 正常回复里最常见的开头是「您好，感谢您的介绍。不过我的求职方向是…」——
  每条都您好开头 + 感谢您的 X + 转折 + 书面敬语，这就是客服模板腔。

口径两条：
① 上下文照旧带够 30 句（test_reply_context.py 那边锁），但**别复述**：不许
   "您刚才说的/针对您的问题/根据我们的对话"这种把这段对话念出来的开头；
② 生成后检出模板腔就地换说法再问一次（只重问一次，不许循环），判据是客观字符串，
   不是"看着像"。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import prompts  # noqa: E402


class 提示词要写死人味要求Test:
    def test_不许复述上下文这一条在规则里(self):
        规则 = prompts.build_system_prompt({"skills": [], "highlights": []})
        for 词 in ("复述", "您刚才", "您好"):
            assert 词 in 规则, f"规则里没写清「{词}」这一条，模型只会照默认写法来"

    def test_书面敬语那族要列进反例(self):
        """拿真发出去过的那 12 条当样本钉死，换措辞就得连判据一起改。"""
        规则 = prompts.build_system_prompt({"skills": [], "highlights": []})
        for 词 in ("感谢您的", "期待与您", "❌", "✅"):
            assert 词 in 规则, f"规则里没给 {词} 这一族的样例，模型分不清什么叫人话"

    def test_用户提示词收尾要叫它直接回话(self):
        词 = prompts.user_prompt_template()
        assert "别复述" in 词 or "不要复述" in 词, 词
        assert "只输出回复内容" in 词


class 模板腔判据Test:
    def test_把上下文念出来的开头要认出来(self):
        for 句 in ("您刚才说的薪资问题，我的期望薪资是面议，具体可以面谈。",
                   "针对您提到的工作内容，我之前做过数据整理和报表。",
                   "根据我们的对话，我这边时间比较灵活，可以配合排班。"):
            assert prompts.机器人腔(句), 句

    def test_书面敬语要认出来(self):
        for 句 in ("您好，感谢您的介绍。不过我的求职方向是数据分析，跟这个岗不太匹配。",
                   "感谢您的来信，期待与您进一步沟通，静候佳音。"):
            assert prompts.机器人腔(句), 句

    def test_像人打的话不能误杀(self):
        """反向哨兵：判据一宽就把正常回复全送去重问，白烧额度还变慢。"""
        for 句 in ("好嘞，那我先看下您说的那个岗位内容哈",
                   "在的，您说的这个岗我挺感兴趣的",
                   "薪资这块看岗位来，面谈就行",
                   "好的，那我明天下午可以面试"):
            assert prompts.机器人腔(句) == "", 句

    def test_拒收那句客套收尾不能误杀(self):
        """「感谢您的时间，理解您的考虑」是拒单的收尾话，说得出口。"""
        assert prompts.机器人腔("感谢您的时间，理解您的考虑，祝您招聘顺利~") == ""


class 嫌机器就换说法再问Test:
    def _引擎(self, 说出):
        from boss_bot.reply_engine import ReplyEngine
        引擎 = ReplyEngine()
        引擎._ai_providers = [{"key": "k", "model": "m", "url": "http://x", "timeout": 1}]
        问了 = []
        指令收 = []

        def fake(client, model, msg, b, j, h, name, 指令=""):
            问了.append(1)
            指令收.append(指令)
            return 说出[len(问了) - 1]

        引擎._call_with_rate_limit_retry = fake
        return 引擎, 问了, 指令收

    def test_第一句是模板腔就换说法再问一次(self):
        引擎, 问了, 指令 = self._引擎([
            "您好，感谢您的介绍。不过我的求职方向是数据分析。",
            "好嘞，我这边就是想做数据整理这类的，您看下合不合适？"])
        句 = 引擎._ask_ai("在吗", "王经理", "数据分析师", [])
        assert 句 == "好嘞，我这边就是想做数据整理这类的，您看下合不合适？", 句
        assert len(问了) == 2, "模板腔要就地换说法重问一次"
        assert "别复述" in 指令[-1] or "人话" in 指令[-1], 指令[-1]

    def test_重问还是机器腔就不再问了(self):
        """有界：重问一次还那样就照发，不许一个接口上打转烧额度。"""
        引擎, 问了, _ = self._引擎([
            "您好，感谢您的介绍。", "您好，感谢您的回复。"])
        句 = 引擎._ask_ai("在吗", "王经理", "数据分析师", [])
        assert 句 == "您好，感谢您的回复。", 句
        assert len(问了) == 2, 问了

    def test_像人的第一句不许多问一次(self):
        引擎, 问了, _ = self._引擎(["好嘞，那我先看下您说的岗位内容哈"])
        assert 引擎._ask_ai("在吗", "王经理", "数据分析师", []) == \
            "好嘞，那我先看下您说的岗位内容哈"
        assert len(问了) == 1, "正常的一句别去重问，那是白烧额度"

    def test_重问那句不能进缓存_两问的键一样(self):
        引擎, 问了, _ = self._引擎([
            "您好，感谢您的介绍。", "好嘞，那我先看下哈"])
        第一次 = 引擎._ask_ai("在吗", "王经理", "数据分析师", [])
        问了.clear()
        第二次 = 引擎._ask_ai("在吗", "王经理", "数据分析师", [])
        assert 第二次 == 第一次, "缓存里存的必须是最终那句，不是第一句模板腔"
        assert 问了 == [], "第二次同样上下文走缓存，别再问接口"


class 判据和引擎共用一份Test:
    def test_引擎调的是prompts那份判据(self):
        源 = (ROOT / "boss_bot" / "reply_engine.py").read_text(encoding="utf-8")
        assert "机器人腔" in 源, "别在引擎里再写一份判据，两份早晚会漂"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


class 出厂话术也要像人Test:
    """模板腔不只长在 AI 头上。

    2026-10-17 拿现判据量了当天 251 条真发出的回复：12 条模板腔（4.8%），
    其中绝大多数是**出厂拒绝话**「您好，感谢您的介绍。这类岗位我不考虑…」——
    它不是 AI 生成的，换说法重问那条路管不着，只能改文案本身。
    """

    def test_出厂拒绝话自己不是模板腔(self):
        from boss_bot.main_loop import OFFLINE_INTERVIEW_DECLINE
        from boss_bot.reply_engine import FAMILY_DECLINE_REPLY
        for 句 in (FAMILY_DECLINE_REPLY, OFFLINE_INTERVIEW_DECLINE):
            assert prompts.机器人腔(句) == "", 句

    def test_拒绝收尾的标记还在(self):
        """去重靠 REFUSAL_ENDINGS 认"这一单已经拒过了"，改文案不许把标记改没。"""
        from boss_bot.main_loop import OFFLINE_INTERVIEW_DECLINE
        from boss_bot.reply_engine import FAMILY_DECLINE_REPLY
        from boss_bot.reply_queue import REFUSAL_ENDINGS
        for 句 in (FAMILY_DECLINE_REPLY, OFFLINE_INTERVIEW_DECLINE):
            assert any(m in 句 for m in REFUSAL_ENDINGS), 句

    def test_代码里一句客服腔都不许再有(self):
        """发给 HR 的话全在 boss_bot/ 里，谁再写「感谢您的介绍」这道门就要红。

        prompts.py 放行：那一句在它是反例教材和判据词表（机器人腔的检测串），
        不会被发出去。
        """
        坏 = []
        for p in (ROOT / "boss_bot").glob("*.py"):
            if p.name == "prompts.py":
                continue
            if "感谢您的介绍" in p.read_text(encoding="utf-8"):
                坏.append(p.name)
        assert not 坏, f"这些文件里还有客服腔：{坏}"
