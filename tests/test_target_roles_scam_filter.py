# -*- coding: utf-8 -*-
"""现阶段的目标方向是"线上兼职"这一族：数据标注/AI标注、线上运营助理、数据分析、游戏代练打手。

用户 2026-10-06 原话："数据标注（AI数据标注）这一种，这种要求接受小白、有培训的这种可以，
但是要求线下的就不要了，遇到线下的直接拒绝就行"、"线上运营助理招聘的话也会上传商品，
做客服，提交订单那些东西，这种也可以"、"运营、线上运营的优先"。

两条要守住的：
①"接受小白/免费培训/无经验也可"是这批岗位的正常写法，不能再当骗子特征——
  命中两条就判骗子，等于把他要投的那一类整体拒掉；
②拒人的话术不许再自称"求职方向是数据分析岗位"——对着标注/代练/运营的 HR
  报一个不相关的方向，等于自己把话聊死。判据换成"只找线上远程能做的"。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.reply_engine import SCAM_HARD, SCAM_SOFT, _is_scam_job  # noqa: E402


def test_接受小白有培训的线上标注不算骗子():
    is_scam, reason = _is_scam_job(
        "我们招线上数据标注，接受小白，有免费培训，时间自由，不用坐班",
        "兼职·AI数据标注 线上")
    assert not is_scam, f"这类正是要投的，却被拒了：{reason}"


def test_只有正常兼职字样凑不出骗子():
    """兼职 / 时间自由 / 日结 / 上手简单 是线上兼职的常规写法。
    岗位名自带"兼职"两个字，所以旧规则里任何一条正常 JD 再蹭上一个弱特征就成骗子了。"""
    is_scam, reason = _is_scam_job(
        "线上标注，时间自由，日结，上手简单，多劳多得", "兼职·数据标注")
    assert not is_scam, f"正常岗位被误判：{reason}"


def test_真骗子的形状仍然拦得住():
    is_scam, reason = _is_scam_job(
        "主播保底8000，高额提成，无经验也可，先把生活照和身高体重发我", "主播")
    assert is_scam, "换了说法的骗子公司不能跟着一起放掉"


def test_一个硬特征加两句弱特征也算():
    """要生活照这一条就够定性了，配上兼职话术必须拦住。"""
    is_scam, _ = _is_scam_job("兼职时间自由，先把你照片发我看看", "主播")
    assert is_scam


def test_小白类字样确实从骗子特征表里拿掉了():
    flat = [k for group in (SCAM_HARD, SCAM_SOFT) for k in group]
    for kw in ("无经验也可", "免费培训", "小白可做"):
        assert kw not in flat, f"{kw} 还在表里，命中两条就误杀整类岗位"
    assert "兼职" not in SCAM_HARD and "时间自由" not in SCAM_HARD


def test_拒绝话术不许自称单一方向():
    """骗子过滤命中时发出去的那句话：方向要跟他现在投的一族对得上。"""
    src = Path("boss_bot/reply_engine.py").read_text(encoding="utf-8")
    assert "求职方向是数据分析" not in src, "对着标注/代练/运营报一个不相关的方向等于把话聊死"
    at = src.index("[骗子过滤]")
    seg = src[at - 900:at]
    assert "线上" in seg, "拒绝理由要说清只找线上，这跟他的口径一致"
