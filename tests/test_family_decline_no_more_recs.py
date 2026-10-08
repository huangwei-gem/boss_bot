# -*- coding: utf-8 -*-
"""拒这类岗位时，"别再给我推荐"这句要真的说出口。

用户 2026-10-08 晚：「现在怎么还是这么多的线下面试的主播、销售来找我，你自己到底有没有
检测啊…以后正式岗位也是，看到这些就 pass 吧，看到这种就拒绝并告诉对方不要给我推荐这类岗位。」

先分清两件事（都是盘上数出来的，不是猜的）：
- 检测在跑：词表 09:43 补齐（老师/家教/合伙人 这批 96d6314 进的统一表），
  今天 [岗位类型过滤] 命中 43 次，最早一条 11:17:26；命中之后 0 条第二类回话。
- 漏的 20 条全在 02:16–09:24（词表补齐之前），来源是 ai 9 / followup 7 / rejection 2。
  现在闸门拦住之后回的那句是「这类岗位不考虑，就不耽误您时间了」——
  没按他要求把"以后别推荐这类"说出来，HR 下次还照推。

所以这里锁两件事：拒绝话术要带上"别再推荐这类岗位"，而且不分兼职/全职——
标题里出现这些类型词就一样拦（"正式岗位也是"）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.intent import title_veto_hit  # noqa: E402
from boss_bot.reply_engine import (FAMILY_DECLINE_REPLY,  # noqa: E402
                                   REPLY_REFUSAL_MARK)
from boss_bot.unified_config import UnifiedConfig  # noqa: E402

TK = list(UnifiedConfig.load().ai.title_veto_keywords)


class 拒绝话术Test:
    def test_要明说别再给我推荐这类岗位(self):
        assert ("推荐" in FAMILY_DECLINE_REPLY and "这类" in FAMILY_DECLINE_REPLY), \
            f"用户点名要的一句没说出来: {FAMILY_DECLINE_REPLY}"

    def test_去重标记必须还在这句话里(self):
        """REPLY_REFUSAL_MARK 是"这一单已经当面拒过"的唯一凭据，
        改掉话术把它挤掉，下一句又会重新拒一遍（骚扰）"""
        assert REPLY_REFUSAL_MARK in FAMILY_DECLINE_REPLY

    def test_不许说只招线上兼职这种话(self):
        """用户 2026-10-08 晚：「岗位类型命中之后不要再说什么只招线上兼职这种话，
        你就说不要给我推荐这类岗位就行了」——报方向等于给 HR 递话接着聊，
        这一句要的是把这一类关上门。"""
        for 说法 in ("只找线上", "只招线上", "线上远程", "兼职"):
            assert 说法 not in FAMILY_DECLINE_REPLY, f"还在报口径：{说法}"

    def test_不报具体求职方向(self):
        """对着骗子/中介岗报"我要做数据分析"等于把话聊死，也暴露方向。"""
        for 方向词 in ("数据分析", "数据标注", "运营助理", "线上兼职数据"):
            assert 方向词 not in FAMILY_DECLINE_REPLY

    def test_话术短到一条发得完(self):
        assert len(FAMILY_DECLINE_REPLY) <= 90, len(FAMILY_DECLINE_REPLY)


class 正式岗位同样拦Test:
    def test_全职标题里的类型词一样命中(self):
        """用户："以后正式岗位也是，看到这些就 pass" — 判据里不能有"兼职"这个前提"""
        for 标题 in ("主播（全职）8-12K长沙", "长白班普工包吃住 正式工6-8K",
                     "房产销售专员7-12K北京", "兼职·大学生兼职家教40-120元/时长沙"):
            assert title_veto_hit(TK, 标题), 标题

    def test_要的方向照常放行不被这句话误伤(self):
        assert title_veto_hit(TK, "兼职·数据标注专员15-20元/时临沂") == ""
        assert title_veto_hit(TK, "直播运营助理6-8K长沙") == ""


class 补拒名单认得这句话Test:
    def test_按新话术也算拒过(self):
        """存量补拒工具靠 already_declined 认账，话术改了它必须还认"""
        from tools.decline_unwanted_inbound import already_declined
        assert already_declined([{"is_mine": True, "text": FAMILY_DECLINE_REPLY,
                                  "content": FAMILY_DECLINE_REPLY}])
