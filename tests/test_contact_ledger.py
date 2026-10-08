# -*- coding: utf-8 -*-
"""联系与简历台账的判据测试。

原文全部抄自 messages/*.json 真实存档，不改一个字：
台账是用来人工判断"这个岗位值不值得跟"的，判据一旦被改坏，
用户看到的就是错的号码或错的岗位，所以宁可把真实文案钉死在测试里。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from boss_bot.contact_ledger import (  # noqa: E402
    contact_rows,
    extract_contacts,
    split_job_title,
)

# 真实卡片：BOSS 交换联系方式后，号码是以「xxx 的微信号」这张卡片进对话流的
CARD_PHONE = "13272464870 蒲慧芝的微信号 13272464870 复制微信号"
CARD_ALNUM = "Muzang623 柯女士的微信号 Muzang623 复制微信号"
# 真实误报源：HR 发了个腾讯文档链接让加企业微信
DOC_LINK = ("您好！我们是做ai数据标注和采集有兴趣可以加下企业微信【腾讯文档】"
            "请复制到浏览器打开，扫描二维码添加微信https://docs.qq.com/doc/DZGt1V0NYWmptR1ds")
HINT_ONLY = "可以加个微信详细聊聊"
# 真实误报源：岗位标题里有数字，薪资尾巴「5-85元/单」「10-14K」
JOB_TITLE_TEXT = "美团骑手（超时差评不扣钱）提供吃住+就近5-85元/单长沙查看职位"


def _chat(messages, name="蒲慧芝", company="锦言里", job=JOB_TITLE_TEXT,
          account=0, updated_at="2026-10-06 09:00:00"):
    return {
        "chat_name": name, "chat_id": f"{name}#{company}", "account_index": account,
        "job_name": job, "company": company, "updated_at": updated_at,
        "messages": messages,
    }


def _hr(text="", card=None, system=False):
    return {"text": text, "card_text": card or "", "isFriend": not system,
            "is_mine": False, "is_system": system,
            "kind": "system" if system else ("card" if card else "bubble"),
            "sender": "hr"}


def _ours(text="", card=None):
    return {"text": text, "card_text": card or "", "isFriend": False,
            "is_mine": True, "kind": "bubble", "sender": "bot"}


def test_卡片号码进微信不重复算成电话():
    """卡片写的是「微信号」，同一个数字再报一遍成电话，界面会像两个联系方式。"""
    got = extract_contacts(CARD_PHONE)
    assert got["wechats"] == ["13272464870"]
    assert got["phones"] == []
    assert got["hinted"] is False


def test_英文微信号卡片也能取到():
    assert extract_contacts(CARD_ALNUM)["wechats"] == ["Muzang623"]


def test_对方正文字里的手机号算电话():
    got = extract_contacts("我的电话是 15574975718，直接打给我")
    assert got["phones"] == ["15574975718"]


def test_岗位标题里的薪资不会被当成联系方式():
    """「5-85元/单」「10-14K上海」都是数字，误报一次用户就白点一次。"""
    for text in (JOB_TITLE_TEXT, "数据分析师（浦江）10-14K上海查看职位"):
        got = extract_contacts(text)
        assert got["phones"] == [] and got["wechats"] == [], text


def test_文档链接不算微信号但要留下说要给():
    """企业微信文档链接里没有微信号，硬抓会抓到「https」；
    但对方确实说了加微信，所以标成「说要给没留号」让人自己去看原话。"""
    got = extract_contacts(DOC_LINK)
    assert got["wechats"] == [], f"把 URL 当成微信号了：{got['wechats']}"
    assert got["phones"] == []
    assert got["hinted"] is True


def test_只说加微信没留号():
    got = extract_contacts(HINT_ONLY)
    assert got["hinted"] is True and got["phones"] == [] and got["wechats"] == []


def test_岗位话术里提到电话不算要联系方式():
    """真实存档里被判成"说要给"的一多半是 JD 文本：「我们招电话客服岗位能接受吗」
    「请保持电话畅通」「不用打电话」。这些一行号码都没有，混进表里就是让人白翻。"""
    for text in ("我们招电话客服岗位能接受吗？",
                 "简历已收到，感谢投递！筛选通过后我们将以电话或短信通知您，请保持电话畅通～",
                 "办公室内勤岗位，不用外跑、不用打电话，没有试岗期",
                 "在高校周边的宣传点位以摆展的形式进行活动宣传、意向客户加企业微信。120/天加提成"):
        assert extract_contacts(text)["hinted"] is False, text


def test_当面要加微信的要留下():
    for text in ("方便加个微信聊吗，也多个选择",
                 "😊请您加我微信，备注学科➕姓名🤝",
                 "可以添加微信详细沟通哦",
                 "如果您愿意来，流程就是加v，登记，我给您考核说明+账密"):
        assert extract_contacts(text)["hinted"] is True, text


def test_交换请求卡片要写清是请求不是给了号():
    """BOSS 的「我想要和您交换微信，您是否同意」是系统卡片（存档里 38 条），
    里面有"微信"两字但没号。标成"说要给没留号"会让人以为去对话里能找到号，
    单独一种说法才看得出现在还差一步点同意。"""
    chat = _chat([_hr(card="我想要和您交换微信，您是否同意 拒绝 同意")])
    rows = contact_rows([chat])
    assert len(rows) == 1, "对方发起了交换，这一单值得跟，不能不显示"
    assert rows[0]["contact_kind"] == "发起交换请求"
    assert rows[0]["wechats"] == [] and rows[0]["phones"] == []
    assert "交换微信" in rows[0]["matched_quote"], "发起交换这条也得留原话，不然不知道判据是什么"


def test_互换电话卡片也算发起交换():
    """「你可以直接跟我互换电话联系我 电话联系TA」是 BOSS 的电话交换卡片，
    存档里出现 23 次，和交换微信卡片同一类：差一步点同意，不是已经给了号。"""
    rows = contact_rows([_chat([_hr(card="我觉得你很适合我们的岗位，你可以直接跟我互换电话联系我 电话联系TA")])])
    assert rows[0]["contact_kind"] == "发起交换请求"


def test_要简历的系统卡片算对方要过简历():
    chat = _chat([_hr(card="我想要一份您的附件简历，您是否同意 拒绝 同意")])
    rows = contact_rows([chat])
    assert len(rows) == 1 and rows[0]["resume_asked"] is True


def test_我方自己的客套不算对方给了联系方式():
    """只有我们说过「方便的话我加您微信」的会话不该进台账，否则一屏假线索。"""
    chat = _chat([_hr("岗位还合适吗"), _ours(HINT_ONLY)])
    assert contact_rows([chat]) == []


def test_台账行带出公司岗位联系方式与原话():
    chat = _chat([
        _hr("你好，美团骑手岗位，不知你是否有兴趣了解一下"),
        _hr(card="我想要和您交换微信，您是否同意 拒绝 同意"),
        _ours("[已同意交换联系方式]"),
        _hr(card=CARD_PHONE),
    ])
    rows = contact_rows([chat])
    assert len(rows) == 1
    row = rows[0]
    assert row["chat_name"] == "蒲慧芝"
    assert row["company"] == "锦言里"
    assert row["account_index"] == 0
    assert row["wechats"] == ["13272464870"]
    assert row["contact_kind"] == "卡片已同意"
    assert CARD_PHONE in row["matched_quote"], "判据原话没留痕，事后没法核对判错没有"


def test_简历已发送单独成行():
    """只发了简历、没拿到联系方式的会话也要进表——用户要看的正是这类后续。

    但「已发」这一栏必须拿 BOSS 那张卡作证：以前拿我们自记的 [简历已发送] 当证据，
    旧送达判据（消息里找"简历"两个字）判成功就会写那行，于是 42 单实际没发出去的
    在表里显示成已发，用户看到的就是「面试官要简历你没给，台账却说发了」。
    """
    chat = _chat([_hr("简历发我一下"), _ours("[简历已发送]")])
    rows = contact_rows([chat])
    assert len(rows) == 1
    assert rows[0]["resume_sent"] is False, "自记那一行不算送达证据"
    assert rows[0]["resume_asked"] is True, "没发出去就得留在欠账里"
    assert rows[0]["contact_kind"] == ""

    已送达 = _chat([_hr("简历发我一下"), _ours("[简历已发送]"),
                    _hr(card="您的附件简历 数据分析简历-黄维.docx 已发送给Boss，请查看")])
    assert contact_rows([已送达])[0]["resume_sent"] is True


def test_对方要了简历但我们没发():
    rows = contact_rows([_chat([_hr("方便把附件简历发我看看吗")])])
    assert len(rows) == 1, "欠简历也要捞出来，否则漏发没人知道"
    assert rows[0]["resume_asked"] is True
    assert rows[0]["resume_sent"] is False


def test_已发过简历的不算欠简历():
    rows = contact_rows([_chat([_hr("简历发我"),
                                _hr(card="您的附件简历已发送给Boss，请查看")])])
    assert rows[0]["resume_asked"] is False


def test_拆解岗位标题的岗位薪资城市():
    """侧栏岗位标题本身就带薪资和城市，直接拆比去打招呼记录里 fuzzy 对靠谱。"""
    got = split_job_title("兼职·线上图文制作110-120元/天成都查看职位")
    assert got == {"title": "兼职·线上图文制作", "salary": "110-120元/天", "city": "成都"}


def test_数字粘连时薪资只取带单位的那段():
    """「月入9000」和「9-14K」在侧栏标题里被拼成 90009-14K，
    K 单位前最多三位数才不会出这种数，取后一段才对。"""
    got = split_job_title("岳麓快递员月入90009-14K长沙查看职位")
    assert got["salary"] == "9-14K"
    assert got["city"] == "长沙"


def test_台账行拆出薪资城市():
    rows = contact_rows([_chat([_hr(card=CARD_PHONE)])])
    assert rows[0]["salary"] == "5-85元/单"
    assert rows[0]["city"] == "长沙"
    assert rows[0]["job_title"] == "美团骑手（超时差评不扣钱）提供吃住+就近"


def test_同公司同账号才带出岗位链接():
    """招呼语发出去时记的链接才是这一单的，公司或账号对不上就不能借用。"""
    greet = [
        {"account_index": 0, "company": "锦言里", "job_name": "美团骑手（超时差评不扣钱）提供吃住+就近",
         "job_url": "https://www.zhipin.com/job_detail/aaa.html", "salary": "5-8K"},
        {"account_index": 1, "company": "锦言里", "job_name": "美团骑手（超时差评不扣钱）提供吃住+就近",
         "job_url": "https://www.zhipin.com/job_detail/bbb.html", "salary": "5-8K"},
    ]
    rows = contact_rows([_chat([_hr(card=CARD_PHONE)], account=0)], greet)
    assert rows[0]["job_url"].endswith("aaa.html")
    other = contact_rows([_chat([_hr(card=CARD_PHONE)], account=1, company="别家")], greet)
    assert other[0]["job_url"] == ""


def test_招呼记录里的薪资兜底():
    """个别标题拆不出薪资（如「薪资面议」），这时用打招呼记录里的那份。"""
    greet = [{"account_index": 0, "company": "锦言里", "job_name": "骑手", "salary": "5-8K",
              "job_url": "https://www.zhipin.com/job_detail/aaa.html"}]
    rows = contact_rows([_chat([_hr(card=CARD_PHONE)], job="骑手薪资面议查看职位")], greet)
    assert rows[0]["salary"] == "5-8K"


def test_按最后活动时间倒序():
    chats = [
        _chat([_hr(card=CARD_PHONE)], name="早的", updated_at="2026-10-04 09:00:00"),
        _chat([_hr(card=CARD_PHONE)], name="晚的", updated_at="2026-10-06 09:00:00"),
    ]
    rows = contact_rows(chats)
    assert [r["chat_name"] for r in rows] == ["晚的", "早的"]


def test_没有联系方式也没有简历的会话不进表():
    chat = _chat([_hr("你好"), _ours("您好，我对岗位感兴趣")])
    assert contact_rows([chat]) == []
