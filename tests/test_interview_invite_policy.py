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


# 2026-10-06 从当天回复记录里抄的真句子：这几条都被旧判据放过了，
# 引擎回的是"工作日下午都可以安排面试"——13:43 那条就是这么把 10/07 的线下面试应下来的。
让人过来 = "什么时候方便过来面试呢？"
合适再过来 = "您好，蓝思直招非中介，加微信发完整简章和福利待遇。平台回复不及时，加微信随时沟通，合适再过来面试。"
会议室那句 = "#腾讯会议：834-440-982，今天全天都在这个会议室，您可以随时进入会议室。如果正巧有人在面试就请排队稍等片刻"
只是提到面试 = "您好，我们需要有教学经验的老师，就可以参加面试哦"


def test_让人过来的就是到场面试():
    assert classify_interview_invite(让人过来) == "offline", \
        '"过来面试"没进判据，机器就替 HR 把到场面试答应了'


def test_合适再过来面试同样算到场():
    assert classify_interview_invite(合适再过来) == "offline"


def test_会议室里面试的是远程():
    assert classify_interview_invite(会议室那句) == "online"


def test_只是提到面试不算到场():
    assert classify_interview_invite(只是提到面试) == "unknown", \
        '"可以参加面试哦"没说在哪儿面，判成现场就是凭空拒一单'


def test_现场面试卡片不能被关键词规则抢先答应():
    """规则表里 '面试' 是子串匹配，回"工作日下午都可以安排面试"。

    那张卡片正文就带"面试"两个字，所以规则层跑在策略层前面的话，
    拒绝那条路永远走不到——实测 10-06 00:33、01:21 两条现场面试卡片就是这么被答应的。
    """
    from boss_bot.reply_engine import ReplyEngine
    e = _引擎()
    action, content, meta = e.get_reply([{"is_mine": False, "text": 现场卡片}])
    assert action == "reject_interview", f"实际走了 {action}/{content}"


def test_策略不能挂在意图上():
    """detect_intent 认这张卡片是 other（它是系统卡片，不是 HR 说话）。

    所以"invite_interview/ask_interview 且判成现场"那条老写法永远不会触发，
    判据只能落在正文本身。
    """
    from boss_bot.reply_engine import ReplyEngine
    e = _引擎()
    action, _, meta = e.get_reply([{"is_mine": False, "text": 现场卡片}])
    assert meta["intent"] == "other", "意图判出来了就说明这条前提变了，策略要跟着改"
    assert action == "reject_interview"


def test_让人过来的问句也走拒绝():
    """13:43 阳霖龙那句"什么时候方便过来面试呢？"是 HR 白话，意图判成 invite_interview。"""
    from boss_bot.reply_engine import ReplyEngine
    e = _引擎()
    action, content, _ = e.get_reply([{"is_mine": False, "text": 让人过来}])
    assert action == "reject_interview", f"实际走了 {action}/{content}"


def test_线上面试照旧答应():
    from boss_bot.reply_engine import ReplyEngine
    e = _引擎()
    action, content, _ = e.get_reply([{"is_mine": False, "text": 线上邀请}])
    assert action == "text" and "面试" in content


def _引擎():
    from boss_bot.reply_engine import ReplyEngine
    from boss_bot.rules import RuleEngine
    e = ReplyEngine()
    e.rule_engine = RuleEngine({"面试": "工作日下午都可以安排面试，您看哪个时间段方便？"})
    e._message_store = None
    e._self_evolve = None
    e._ask_ai = lambda *a, **k: None
    e._add_record = lambda **kw: None
    e._record_to_evolve = lambda *a, **k: None
    e._log_decision = lambda *a, **k: None
    return e


# ────────────────────────────────────────────────────────────────────────────
# 2026-10-07 中午真发出去的到场承诺（存档原文，工具核对过）：
#   12:02 迟女士  「收到，明天10点准时到新天地1310面试」  ← HR 上一句只有「公司地址新天地1310」
#   12:35 王博    「方便，明天下午两点我可以准时到公司面试」
#   13:39 周杰    「好的，我确定来，现在就接受您的邀请，四点半准时到」
#   07:45 陈先生  「好的，收到。下午三点我准时过去」        ← HR 只发了「a栋3307 到前台刷电梯卡上来」
# 根因：classify_interview_invite 第一行就要看见"面试"两个字才判，
# 而 HR 约到场时经常只发门牌号、只问"几点方便过来"，压根不提面试。
# ────────────────────────────────────────────────────────────────────────────

只发地址 = "公司地址新天地1310"
电梯卡 = "a栋3307 到前台刷电梯卡上来"
大厦房间号 = "聊城创业大厦A塔3115 到了联系我就行"
问几点过来 = "明天下午几点可以过来"
来看线下 = "方便来线下看看吗"
要接受邀请 = "来的话可以接受一下邀请 上面也有地址方便看"
平台告诫 = "亲，先不要直接过来面试哈，请等待微信或电话沟通确认之后再确定哈～"
要简历 = "方便发一份你的简历过来吗？"


def test_只发门牌号也算到场():
    assert classify_interview_invite(只发地址) == "offline"


def test_写楼层前台电梯的也算到场():
    assert classify_interview_invite(电梯卡) == "offline"
    assert classify_interview_invite(大厦房间号) == "offline"


def test_问几点过来算到场():
    assert classify_interview_invite(问几点过来) == "offline"
    assert classify_interview_invite(来看线下) == "offline"
    assert classify_interview_invite(要接受邀请) == "offline"


def test_平台那句别直接过来的告诫不算邀请():
    """那句是提醒你别贸然上门，不是约你到场——判成现场会凭空撤掉别人的面试。"""
    assert classify_interview_invite(平台告诫) != "offline"


def test_要简历那句带过来的不算到场():
    assert classify_interview_invite(要简历) == "unknown"


def test_地址后面跟一句别的也要拒():
    """策略层只看最新那一句时，"公司地址新天地1310"在上一句就等于没看见。"""
    e = _引擎()
    action, content, _ = e.get_reply([
        {"is_mine": False, "text": "对 明天10点?"},
        {"is_mine": False, "text": 只发地址},
        {"is_mine": True, "text": "好的，我准时到"},
        {"is_mine": False, "text": "嗯"}])
    assert action == "reject_interview", f"实际走了 {action}/{content}"


def test_承诺到场的句子发不出去():
    """最后一道闸：不管哪一层生成的，只要是我们应下来场面的话，一律不发。"""
    from boss_bot.intent import commits_offline_visit
    for 句 in ("收到，明天10点准时到新天地1310面试",
               "方便，明天下午两点我可以准时到公司面试",
               "好的，我确定来，现在就接受您的邀请，四点半准时到",
               "明天下午3点可以，我会准时到达。期待与您交流。"):
        assert commits_offline_visit(句), 句


def test_拒绝话术自己不能被闸门拦掉():
    from boss_bot.intent import commits_offline_visit
    from boss_bot.main_loop import OFFLINE_INTERVIEW_DECLINE
    assert commits_offline_visit(OFFLINE_INTERVIEW_DECLINE) == ""
    assert commits_offline_visit("不好意思，我只找线上远程的兼职，线下面试就不占用您的时间了") == ""
    assert commits_offline_visit("请问这个岗位是线上远程的吗？") == ""


def test_发送出口真的接了这道闸():
    import inspect
    from boss_bot.page_handler import BossChatHandler
    src = inspect.getsource(BossChatHandler.send_text)
    assert "commits_offline_visit" in src, "send_text 没接闸门，承诺照样发得出去"
