# -*- coding: utf-8 -*-
"""面试邀请要先分得出"现场"还是"线上"，才能决定直接拒还是接着约。

用户 2026-10-06（手机截图：面试日程里两条"待接受"都写着线下面试）：
"面试不是所有的都接受的，像这种不符合我目标的（我要的是线上的，这种线下的就不合适）
直接拒绝就行了，他发面试邀请你直接拒绝"。

判据取自真实卡片文案——存档里 7 条面试邀请卡片一律是
「湖南九片云邀请您现场面试，前往查看，确认是否接受」，BOSS 把到场面试写成"现场"，
所以"现场/线下"就是可以直接定罪的字样；而"视频/线上/远程"是要留下的那一头。
什么都没说清的（只写"邀请您面试"）返回 unknown，不自动点拒绝——
点错一次就是当着 HR 的面把人家面试撤了。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.intent import classify_interview_invite  # noqa: E402

# 2026-10-06 从 messages/ 里抄的真句子
现场卡片 = "湖南九片云邀请您现场面试，前往查看，确认是否接受 立即查看"
线下日程 = "珞壹文化 线下面试 运营助理 · 8-12K 待接受"
视频邀请 = "我们想约您视频面试，方便的话点同意"
线上邀请 = "线上面试，腾讯会议进行，您是否同意"
没说清 = "长沙坤舆互动文化传媒邀请您面试，前往查看，确认是否接受"


def test_写着现场的就是到场面试():
    assert classify_interview_invite(现场卡片) == "offline"


def test_写着线下的也是():
    assert classify_interview_invite(线下日程) == "offline"


def test_视频面试是要留下的():
    assert classify_interview_invite(视频邀请) == "online"


def test_线上远程同样要留():
    assert classify_interview_invite(线上邀请) == "online"


def test_没说清地点的不自动定罪():
    assert classify_interview_invite(没说清) == "unknown", \
        "判错就是当着 HR 的面撤掉人家发的面试，宁可不动"


def test_既写现场又写线上时按现场处理():
    # 实测见过"线上/线下兼职 含线上督学"这种两头都带的岗位名，
    # 邀约本身写了"现场"就到现场，按现场判更安全：拒绝话术里会说明只找线上
    assert classify_interview_invite("该岗位线上运营，需现场面试，您是否同意") == "offline"


def test_空文本不算邀请():
    assert classify_interview_invite("") == "unknown"
    assert classify_interview_invite(None) == "unknown"
