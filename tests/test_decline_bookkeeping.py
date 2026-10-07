# -*- coding: utf-8 -*-
"""存量补拒工具认不认"我们已经拒过"。

2026-10-07 实跑两遍后发现：卡片上点过「拒绝」的会话下一轮还出现在待拒名单里
（黄女士｜长沙星城壹驰物流，配送岗），因为记账写的 "[已拒绝交换联系方式]"
从没被列进 REFUSED_MARKS——同一单会被反复处理，--check 的数也永远降不下来。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.contact_ledger import CONTACT_DECLINED_MARK  # noqa: E402
from boss_bot.reply_engine import (FAMILY_DECLINE_REPLY,  # noqa: E402
                                   REPLY_REFUSAL_MARK)
from tools.decline_unwanted_inbound import already_declined  # noqa: E402


def 我方(text):
    return {"is_mine": True, "text": text, "content": text}


def 对方(text):
    return {"is_mine": False, "sender": "hr", "text": text, "content": text}


class 已经拒过的判据Test:
    def test_卡片点过拒绝就算拒过(self):
        assert already_declined([我方(CONTACT_DECLINED_MARK)])

    def test_文字拒绝话术算拒过(self):
        assert already_declined([我方(FAMILY_DECLINE_REPLY)])
        assert already_declined([我方(REPLY_REFUSAL_MARK)])

    @pytest.mark.parametrize("mark", [CONTACT_DECLINED_MARK, FAMILY_DECLINE_REPLY])
    def test_混合历史里认得出(self, mark):
        msgs = [对方("我想要和您交换微信，您是否同意 拒绝 同意"),
                我方("您好，我对这个岗位很感兴趣"),
                我方(mark)]
        assert already_declined(msgs)

    def test_对方说的话不算我们拒过(self):
        """HR 自己说"祝您招聘顺利"不代表我们表过态。"""
        assert not already_declined([对方("祝您招聘顺利")])

    def test_没说过拒绝话就不算(self):
        assert not already_declined([我方("您好，我想了解一下工作内容")])
        assert not already_declined([])
