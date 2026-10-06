# -*- coding: utf-8 -*-
"""拒线下面试之前那道"这一单真在约到场"的闸门要认全说法。

实测 --check：姬广凯那单 HR 原文给的是"公司地址：亚大时代……到了先到公司前台签到"，
陈女士那单是"感兴趣可直接来公司参观详聊"，两条都是白纸黑字的到场邀约，
但 INVITE_MARKS 只有 面试/邀约/约个时间/过来/到岗，两句都没命中，
结果 --check 报"页面上最后几条没提到面试"，一单都发不出去。

拒绝话术是真实发送，闸门不能松；但按 BOSS 上 HR 的实际写法判，
地址/签到/来公司 这类"到场指令"必须算数。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.decline_offline_interviews import looks_like_offline_invite  # noqa: E402

# 2026-10-06 从两台真机上抄下来的 HR 原文
姬广凯 = [{"text": "公司地址：亚大时代(地铁迎宾路4号出口出来直走100米，茶颜悦色和"
                    "桔子水晶酒店中间的写字楼入口处进来）22楼2202（出电梯右转两次）"}]
陈女士 = [{"text": "我们是多渠道一线品牌运营公司，主营童装 + 健康食品，现招客服岗，"
                    "感兴趣可直接来公司参观详聊～"}]
章先生 = [{"text": "方便发我听听吗？合适的话可以明天下午面试"}]
闲聊 = [{"text": "您好，我们有岗位适合您，方便聊聊吗"}, {"text": "在的"}]
拒绝 = [{"text": "感谢您的应聘，但当前我们在寻找具有不同经验背景的候选人。"}]


def _ok(messages):
    return looks_like_offline_invite(messages)[0]


def test_给了地址和签到须知就算到场邀约():
    assert _ok(姬广凯), "HR 都写了写字楼门牌号和前台签到，闸门不该判成没约面试"


def test_叫人来公司参观详聊就算到场邀约():
    assert _ok(陈女士), "「可直接来公司参观详聊」就是要人到场的说法"


def test_直接说下午面试的照旧命中():
    assert _ok(章先生)


def test_普通打招呼不算邀约():
    assert not _ok(闲聊), "只在聊岗位本身就把拒绝话术发出去，就是发错人"


def test_对方已经拒绝不算邀约():
    assert not _ok(拒绝)
