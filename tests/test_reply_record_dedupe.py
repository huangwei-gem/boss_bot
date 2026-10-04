# -*- coding: utf-8 -*-
"""一次回复在记录表里只能占一行。

2026-10-05 盘上 1730 条回复记录里 657 条是同一件事写了两遍：
decide_reply 生成回复时先记一条（带 system_prompt / ai_raw_response 这些证据），
main_loop 发出去以后又记一条（"按 …来源生成的回复已发送"）。
于是界面上每条回复都成对出现，"已回复"的计数也翻倍。
两条各有用途，都不能简单删掉，所以合并在落库这一层：同一件事 = 第二条并回第一条。
"""
from datetime import datetime, timedelta

from boss_bot.reply_record import ReplyRecord, ReplyRecordStore


def _store(tmp_path):
    return ReplyRecordStore(path=str(tmp_path / "reply_records.json"))


def _gen(ts, **kw):
    base = dict(chat_name="毛先生", job_name="家教兼职",
                received_message="我们需要高考单科满分150的不低于110",
                reply_content="我高考数学是125分，符合要求",
                reply_source="ai", timestamp=ts)
    base.update(kw)
    return ReplyRecord(**base)


def test_同一件事两次落库只剩一条(tmp_path):
    st = _store(tmp_path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.add(_gen(now, reply_reason="规则和意图均未命中，使用AI生成回复",
                system_prompt="系统提示词", ai_model="deepseek-x"))
    st.add(_gen(now, reply_reason="按 ai 来源生成的回复已发送"))

    got = st.get_all()
    assert len(got) == 1, f"应该并成一条，实际 {len(got)} 条"
    assert got[0].system_prompt == "系统提示词", "AI 证据不能被后一条的空值抹掉"
    assert got[0].ai_model == "deepseek-x"
    assert got[0].reply_reason == "按 ai 来源生成的回复已发送", "后写的要覆盖，界面才看得出真发出去了"


def test_超出十分钟窗口的重复内容仍是两条(tmp_path):
    """同一句被回两次是两件事（比如对方连着问了两遍），不能并。"""
    st = _store(tmp_path)
    early = (datetime.now() - timedelta(minutes=11)).strftime("%Y-%m-%d %H:%M:%S")
    late = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.add(_gen(early, reply_reason="生成"))
    st.add(_gen(late, reply_reason="已发送"))
    assert len(st.get_all()) == 2


def test_内容或对象不同不合并(tmp_path):
    st = _store(tmp_path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.add(_gen(now, reply_content="第一句"))
    st.add(_gen(now, reply_content="第二句"))
    st.add(_gen(now, chat_name="李女士", reply_content="第一句"))
    assert len(st.get_all()) == 3


def test_跳过的记录不会被发出去的记录顶掉(tmp_path):
    """决定跳过和真的发出去是两件事：跳过那条是"为什么没回"的证据。"""
    st = _store(tmp_path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.add(_gen(now, reply_content=None, reply_source="skip",
                is_skipped=True, skip_reason="AI回复与历史已发内容重复"))
    st.add(_gen(now, reply_reason="按 ai 来源生成的回复已发送"))
    assert len(st.get_all()) == 2
