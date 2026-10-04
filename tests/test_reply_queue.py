# -*- coding: utf-8 -*-
"""回复/跟进候选判定的单测。

判据全部照 2026-10-04 盘上真实存档的形状造：卡片正文只在 card_text、
顺序要按 mid、平台"竞争者PK"卡片占了 HR 侧卡片的绝大多数。
"""
import io
import json
import glob
from datetime import datetime, timedelta

from boss_bot.reply_queue import (chat_state, followup_due, inbound_body,
                                  mark_followed, msg_time, owed_replies)

NOW = datetime(2026, 10, 4, 14, 0, 0)


def hr(text, mid, minutes_ago=30, kind="bubble"):
    t = (NOW - timedelta(minutes=minutes_ago)).strftime("%H:%M")
    return {"mid": str(mid), "time": t, "is_mine": False, "is_system": False,
            "sender": "hr", "kind": kind, "text": text if kind == "bubble" else "",
            "card_text": "" if kind == "bubble" else text}


def me(text, mid, minutes_ago=10):
    t = (NOW - timedelta(minutes=minutes_ago)).strftime("%H:%M")
    return {"mid": str(mid), "time": t, "is_mine": True, "is_system": False,
            "sender": "bot", "kind": "bubble", "text": text}


def chat(messages, name="刘女士", company="珍岛集团", job="数据分析", idx=0, updated=None):
    return {"chat_name": name, "company": company, "job_name": job,
            "account_index": idx, "messages": messages,
            "updated_at": updated or NOW.strftime("%Y-%m-%d %H:%M:%S")}


class TestInboundBody:
    def test_平台PK卡片不算对方说话(self):
        m = hr("你与该职位竞争者PK情况 共人投递，你超过竞争者 优秀竞争者会，建议你 查看详细分析",
               900, kind="card")
        assert inbound_body(m) == "", "对这张卡片回话等于对着空气说话"

    def test_要附件简历的卡片算对方说话(self):
        m = hr("我想要一份您的附件简历，您是否同意 拒绝 同意", 901, kind="card")
        assert "附件简历" in inbound_body(m)

    def test_换微信要电话也是(self):
        for text in ("我想要和您交换微信，您是否同意 拒绝 同意",
                     "我想要一个您的电话号码，您是否同意 拒绝 同意",
                     "您是否接受此工作地点? 长沙 暂不考虑"):
            assert inbound_body(hr(text, 902, kind="card")), text


class TestChatState:
    def test_对方最后一句没接就是欠回复(self):
        kind, info = chat_state([me("你好", 10), hr("大四还有课吗", 11, minutes_ago=20)], NOW)
        assert kind == "reply" and info["ask"] == "大四还有课吗"

    def test_我们回过就不欠了(self):
        kind, _ = chat_state([hr("大四还有课吗", 11), me("没课，可以全职实习", 12)], NOW)
        assert kind == "follow"

    def test_顺序按mid不按时间字符串(self):
        # 两条时间串一样（都只到分钟），mid 大的才是后说的
        a = hr("在吗", 100)
        a["time"] = "13:00"
        b = me("在的，您好", 101)
        b["time"] = "13:00"
        assert chat_state([a, b], NOW)[0] == "follow"

    def test_只有PK卡片压尾不算欠(self):
        msgs = [hr("你好同学，看看岗位", 5), me("好的", 6),
                hr("你与该职位竞争者PK情况 共人投递", 7, minutes_ago=1, kind="card")]
        assert chat_state(msgs, NOW)[0] == "follow"


class TestOwedReplies:
    def test_挑出来并带上原文(self):
        got = owed_replies([chat([me("你好", 1), hr("通勤距离怎么样呢宝", 2, minutes_ago=40)])], NOW)
        assert len(got) == 1
        assert got[0]["ask"] == "通勤距离怎么样呢宝"
        assert got[0]["company"] == "珍岛集团"

    def test_太旧的账不翻(self):
        old = (NOW - timedelta(hours=80)).strftime("%Y-%m-%d %H:%M")
        c = chat([hr("还在吗", 3, )], updated=old)
        c["messages"][0]["time"] = old[5:10] + " " + old[11:]
        assert owed_replies([c], NOW) == []

    def test_等最久的排前面(self):
        a = chat([hr("在吗", 1, minutes_ago=60)], name="A")
        b = chat([hr("在吗", 2, minutes_ago=600)], name="B")
        got = owed_replies([a, b], NOW)
        assert [r["name"] for r in got] == ["B", "A"]


class TestFollowup:
    def test_静默够久才追(self):
        c = chat([hr("大四还有课吗", 1), me("没课", 2, minutes_ago=300)])
        assert len(followup_due([c], {}, NOW, after_hours=4)) == 1
        assert followup_due([c], {}, NOW, after_hours=8) == []  # 才静默 5 小时就追，太急

    def test_追够次数就停(self):
        c = chat([me("没课，方便约个时间吗", 2, minutes_ago=300)], name="刘女士")
        state = mark_followed({"0|刘女士|珍岛集团": {"times": 1, "last_at": (NOW - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")}},
                              "0|刘女士|珍岛集团", NOW)
        assert state["times"] == 2
        assert followup_due([c], {"0|刘女士|珍岛集团": {"times": 2, "last_at": ""}}, NOW,
                            max_times=2) == []

    def test_两次之间要隔够(self):
        c = chat([me("没课", 2, minutes_ago=300)])
        recent = (NOW - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        assert followup_due([c], {"0|刘女士|珍岛集团": {"times": 1, "last_at": recent}}, NOW,
                            after_hours=4) == []
        older = (NOW - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
        assert len(followup_due([c], {"0|刘女士|珍岛集团": {"times": 1, "last_at": older}}, NOW,
                                after_hours=4)) == 1

    def test_太陈旧的会话不再追(self):
        c = chat([me("没课", 2, minutes_ago=60 * 24 * 9)])
        assert followup_due([c], {}, NOW, within_days=4) == []


class TestTimeParsing:
    def test_三种时间写法(self):
        assert msg_time({"time": "09:40"}, "2026-10-04 12:00:00").hour == 9
        assert msg_time({"time": "09-29 09:18"}, "2026-10-04 12:00:00").month == 9
        assert msg_time({"time": "2026-09-29 09:18"}, "").year == 2026

    def test_读不出时间退回存档更新时间(self):
        got = msg_time({"time": "已读"}, "2026-10-04 11:22:25")
        assert got.strftime("%Y-%m-%d %H:%M") == "2026-10-04 11:22"


class TestAgainstRealArchive:
    """拿盘上真实存档跑一遍：挑出来的每条都必须是真的在等我们。"""

    def _chats(self):
        out = []
        for p in sorted(glob.glob("messages/*.json"))[:400]:
            try:
                out.append(json.load(io.open(p, encoding="utf-8")))
            except Exception:
                continue
        assert out, "盘上没有可读存档，这条测试就没意义了"
        return out

    def test_挑出来的ask不能是平台噪声(self):
        for r in owed_replies(self._chats(), max_age_hours=72, limit=40):
            assert r["ask"], r
            assert "竞争者PK" not in r["ask"] and "AI生成回复" not in r["ask"], r["ask"]

    def test_真有人压着没回就必须挑出来(self):
        """2026-10-04 的事故形态：红点被全量同步清掉，存档里却压着真提问。"""
        chats = self._chats()
        manual = 0
        for c in chats:
            kind, _ = chat_state(c.get("messages") or [], datetime.now(), c.get("updated_at") or "")
            manual += (kind == "reply")
        # 不限账龄时两个算法必须给出同一批人，否则说明筛选口径分叉了
        got = owed_replies(chats, max_age_hours=10 ** 9, limit=9999)
        assert len(got) == manual, "两条腿判据必须一致，不一致说明筛选漏了或多了"
