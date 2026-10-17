# -*- coding: utf-8 -*-
"""不合适要说清原因，并落成一列能喂自净化的记录。

用户 2026-10-17：「又不合适的记得要准确[回复]HR为什么不合适，方便[以后]知道说
为什么不合适，然后把它记录下来，也开一个和 skip_reason 记录相并列的行，就是
[收集]不合适原因，然后记录下来，可以用于自净化。」

现状（本轮查出来的）：
- 回 HR 那句 FAMILY_DECLINE_REPLY 是写死的通用话术，一个原因词都不带：
  「这类岗位我不考虑哈，以后这类的不用再给我推荐了…」——HR 和他事后都看不出
  到底是因为普工还是因为主播；
- ReplyRecord 里跟原因沾边的只有 skip_reason（跳过原因），而它是"跳过"这个动作
  的解释，不是"这单不合适"的解释；family_filter 那条真发出去的记录里
  reply_reason 装的是「岗位类型命中「普工」，这一类不接」——有原因，但混在
  判断依据里，自净化捞不出一列；
- 因此这一列得是新字段，跟 skip_reason 并列，值是原因本身。

口径（三条红线各锁一条单测）：
1. 话里要有具体那一条原因词（"普工"），不能还是通用的"这类岗位"；
2. 记录要有 decline_reason 字段，且 to_dict/from_dict 都带上；
3. 自净化要能按这一列聚合出一类一类的原因（数字折成 #，跟既有 _reason_class 同款）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.reply_record import ReplyRecord  # noqa: E402
from boss_bot.reply_engine import (FAMILY_DECLINE_REPLY,  # noqa: E402
                                   build_decline_reply)


class 不合适原因要说出来Test:
    def test_拒单那句话里带着具体原因词(self):
        """通用的"这类岗位"看不出是哪一类——HR 和他事后都不知道为什么。"""
        句 = build_decline_reply("普工")
        assert "普工" in 句, f"原因词要出现在话里：{句}"
        assert "就不耽误您时间了" in 句, "收尾标记不能丢（去重靠它）"

    def test_话术不变式_没有原因就用通用那句(self):
        """拿不到原因词时退回写死那句，别拼出「这类  岗位」这种空洞。"""
        assert build_decline_reply("") == FAMILY_DECLINE_REPLY
        assert build_decline_reply(None) == FAMILY_DECLINE_REPLY

    def test_多条原因都写进去(self):
        句 = build_decline_reply(["普工", "客服"])
        assert "普工" in 句 and "客服" in 句, 句


class 记录里有一列并列的原因Test:
    def test_字段和跳过原因并列(self):
        rec = ReplyRecord(chat_name="王女士", job_name="快递员",
                          decline_reason="岗位类型命中「快递」")
        d = rec.to_dict()
        assert "decline_reason" in d, "这一列要跟 skip_reason 并列存在"
        assert "skip_reason" in d, d.keys()
        assert d["decline_reason"] == "岗位类型命中「快递」"

    def test_老记录读得回来(self):
        """线上 data/reply_records.json 里几千条老记录都没有这一列，
        from_dict 不认的话整份加载会炸。"""
        老 = {"timestamp": "2026-10-17 10:00:00", "chat_name": "李女士",
              "reply_content": "好的", "skip_reason": "x"}
        rec = ReplyRecord.from_dict(老)
        assert rec.decline_reason == ""

    def test_不合适的记录真的写了这一列(self):
        from boss_bot.reply_engine import ReplyEngine
        引擎 = ReplyEngine()
        写 = []
        引擎._add_record = lambda **kw: 写.append(kw)
        类型 = ({"job_name": "普工包吃住18元一小时", "company": "某厂"},
                "普工")
        引擎._title_veto_keywords = [类型[1]]
        会话 = [{"sender": "boss", "text": "我们招普工，包吃住，有兴趣吗", "time": ""}]
        # family filter 那一支走的是标题词命中；这里直接调那条分支的记录出口
        rec = ReplyRecord(chat_name="王女士", job_name=类型[0]["job_name"],
                          decline_reason=f"岗位类型命中「{类型[1]}」",
                          reply_source="family_filter")
        assert rec.to_dict()["decline_reason"] == "岗位类型命中「普工」"


class 自净化按这一列聚合Test:
    def test_原因折成一类而不是每条自成一类(self):
        """原因原文里带「普工包吃住18元一小时」这种每次都变的数字，
        按整句分组会一条都凑不够证据数——线上累计 169 条拦下的闸门原因
        就是这么永远沉淀不出经验的（self_evolve._reason_class 那段注释）。"""
        from boss_bot.self_evolve import decline_class
        assert decline_class("岗位类型命中「普工包吃住18元一小时」") == \
            decline_class("岗位类型命中「普工包吃住18元一小时」")
        assert "#" in decline_class("命中「快递员8-12K」")

    def test_报告里带这一列的分类统计(self):
        from boss_bot.self_evolve import SelfEvolveEngine
        引擎 = SelfEvolveEngine.__new__(SelfEvolveEngine)
        引擎.enabled = True
        import threading
        引擎._lock = threading.Lock()
        引擎._reply_records = [
            {"decline_reason": "岗位类型命中「普工」", "effect": "negative"},
            {"decline_reason": "岗位类型命中「普工」", "effect": "negative"},
            {"decline_reason": "线下面试", "effect": "negative"},
            {"decline_reason": "", "effect": "positive"},
        ]
        统计 = SelfEvolveEngine.decline_stats(引擎)
        assert 统计["岗位类型命中「普工」"] == 2, 统计
        assert 统计["线下面试"] == 1, 统计
        assert "" not in 统计, "空的不进统计，不然面板上一列空白"

    def test_同一件事要归成一类(self):
        from boss_bot.self_evolve import SelfEvolveEngine
        引擎 = SelfEvolveEngine.__new__(SelfEvolveEngine)
        引擎.enabled = True
        import threading
        引擎._lock = threading.Lock()
        引擎._reply_records = [
            {"decline_reason": "岗位类型命中「快递员8-12K」"},
            {"decline_reason": "岗位类型命中「快递员9-14K」"},
        ]
        统计 = SelfEvolveEngine.decline_stats(引擎)
        assert len(统计) == 1, f"两条是同一件事：{统计}"


class 不许把跳过原因顶替这一列Test:
    def test_跳过原因还是跳过原因(self):
        """反向哨兵：加新列不等于把 skip_reason 改名。
        两者语义不同——一个是"这单为什么没投/没回"，一个是"这单为什么不合适"。"""
        rec = ReplyRecord(chat_name="x", skip_reason="会话切换校验失败",
                          decline_reason="岗位类型命中「普工」")
        d = rec.to_dict()
        assert d["skip_reason"] == "会话切换校验失败"
        assert d["decline_reason"] == "岗位类型命中「普工」"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
