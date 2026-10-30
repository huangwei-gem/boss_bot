# -*- coding: utf-8 -*-
"""2026-10-25 朱微那一路人被识破的四条根因。

取证（只读，本轮没点过任何发送）：
- messages/朱微_鲲图之火文化传媒.json 这一段 12 行里，
  10-21 16:24 我方已说「…招聘实习这个岗位方向不太合适，就不投了，谢谢您～」，
  10-23 12:56 真发出去过一句「（无需回复，对话已自然结束）」，
  10-24 13:21 又追出一句「朱微您好，之前聊的人力招聘实习生100-150元/天长沙查看职位我很有兴趣…」，
  于是 HR 回「是用ai插件主动投递的吗老师？能教教我么」。
- data/reply_records.json 里 52 组"同一会话对同一句 HR 消息发了 ≥2 条回复"（涉及 112 条）。
- 全库存档 13031 行带 data-mid、527 行没 mid 且**全部**是我方的、对方一行都没有。
"""
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from boss_bot.reply_queue import (chat_state, followup_due, inbound_body, msg_body,  # noqa: E402
                                  排序位置, 拒过这单, 问过还招吗, 拒话说法, REFUSAL_ENDINGS)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _行(文, 我方, mid, 时刻=""):
    m = {"text": 文, "content": 文, "kind": "bubble",
         "is_mine": bool(我方), "is_system": False,
         "sender": "bot" if 我方 else "hr"}
    if mid:
        m["mid"] = str(mid)
    if 时刻:
        m["timestamp"] = 时刻
    return m


class 排序归属Test:
    """没 mid 只可能是"我们刚发出去、还没从页面回填"的那一条。"""

    def test_自记气泡没有mid也要排在HR那句之后(self):
        # 朱微 10-23 的形状：HR 的「好嘟」带 mid，我们回的那条是自记的、没有 mid
        对话 = [_行("你好同学，看过你的简历", False, 399033058120452),
                _行("好嘟", False, 399335656215309),
                _行("嗯嗢，好的～", True, 0, "2026-10-23 12:56:40")]
        卡, 详 = chat_state(对话, datetime(2026, 10, 23, 13, 0), "2026-10-23 13:00:00")
        assert 卡 == "follow", "回过了还判成欠回复，就会对同一句再回一遍: %r" % (详,)

    def test_HR真的又发了一句时照旧判成欠回复(self):
        """反向哨兵：修完不许把"还欠着 HR"也一起判没了。"""
        对话 = [_行("嗯嗢，好的～", True, 0, "2026-10-23 12:56:40"),
                _行("我们又在招了", False, 399743331099393)]
        卡, 详 = chat_state(对话, datetime(2026, 10, 23, 13, 0), "2026-10-23 13:00:00")
        assert 卡 == "reply"
        assert 详["ask"] == "我们又在招了"

    def test_全段没有mid时沿用列表原顺序(self):
        对话 = [_行("第一句", False, 0), _行("第二句", True, 0)]
        键 = 排序位置(对话)
        assert sorted(range(2), key=lambda i: 键[i]) == [0, 1]
        卡, _ = chat_state(对话, datetime(2026, 10, 23, 13, 0), "2026-10-23 13:00:00")
        assert 卡 == "follow"

    def test_没mid的行沿用上一条带mid的位置(self):
        # 老存档里我们补写的那条标记落在中间：它该跟着前一条，不许跳到整段最后
        对话 = [_行("HR 早先", False, 100),
                _行("[简历已发送]", True, 0),
                _行("HR 后来又说", False, 200)]
        卡, 详 = chat_state(对话, datetime(2026, 10, 23, 13, 0), "2026-10-23 13:00:00")
        assert 卡 == "reply", "我们补写的旧标记不该盖住 HR 后来的那句"
        assert 详["ask"] == "HR 后来又说"


class 真实存档回放Test:
    """拿盘上那一路真存档回放，判据必须是这一句，不是我编的样例。"""

    def _存档(self):
        p = os.path.join(ROOT, "messages", "朱微_鲲图之火文化传媒.json")
        if not os.path.exists(p):
            pytest.skip("这条会话存档不在这份检出里")
        return json.load(open(p, encoding="utf-8"))

    def test_朱微那一路现在判成我们话说完了(self):
        d = self._存档()
        卡, _ = chat_state(d["messages"], datetime(2026, 10, 24, 13, 0), d.get("updated_at") or "")
        assert 卡 == "follow"

    def test_朱微那一路不再被列进跟进候选(self):
        """10-24 13:21 那句「我很有兴趣」就是这一列里发出去的。

        等待时间摆成 30 小时：这样"够不够静默"那道门是绿的，唯一拦得住它的
        就是新加的「整段拒过就不追」——把那条判据拆掉，这条必须红。
        """
        d = self._存档()
        会 = dict(d, account_index=0)
        # 把已经发出去的那句跟进摘掉：判的是"当时该不该发"，不是"已经发了"
        会["messages"] = [m for m in d["messages"] if "我很有兴趣" not in msg_body(m)]
        会["updated_at"] = "2026-10-23 13:19:00"
        assert 拒过这单(会) is True, "整段里那句「就不投了」必须认出来"
        now = datetime(2026, 10, 24, 19, 0, 0)
        # 前提自检：这一刻它确实"该追"（静默够久、我方话说完了），否则这条门是空的
        assert chat_state(会["messages"], now, 会["updated_at"])[0] == "follow"
        assert followup_due([会], {}, now=now) == [], "拒过这一单还追，就是朱微那一路的露馅现场"

    def test_同一句还在招吗不许问第二遍_真实那一单(self):
        """武先生 10-23 12:49 与 10-24 13:03 那两条。"""
        p = os.path.join(ROOT, "messages", "武先生_天盛传媒.json")
        if not os.path.exists(p):
            pytest.skip("这条会话存档不在这份检出里")
        d = json.load(open(p, encoding="utf-8"))
        assert 问过还招吗(d) is True


class 已处理排重Test:
    def test_回过A又回B之后A仍算已处理(self, tmp_path):
        from boss_bot.state_store import StateStore
        s = StateStore(path=tmp_path / "state.json")
        s.mark_handled("赵女士", "好的", "text")
        s.mark_handled("赵女士", "微信通过一下", "text")
        assert s.was_handled("赵女士", "好的"), "只记最后一条 → 存档把旧那句重新端上来就再回一遍"

    def test_没记过的句子仍判没处理(self, tmp_path):
        """反向哨兵：环记成"什么都算已处理"就等于永久不回。"""
        from boss_bot.state_store import StateStore
        s = StateStore(path=tmp_path / "state.json")
        s.mark_handled("赵女士", "好的", "text")
        assert s.was_handled("赵女士", "那三个岗位") is False

    def test_环形只留最近几条(self, tmp_path):
        from boss_bot.state_store import StateStore
        s = StateStore(path=tmp_path / "state.json")
        for i in range(s.已处理保留条数 + 3):
            s.mark_handled("赵女士", "句子%s" % i, "text")
        assert s.was_handled("赵女士", "句子0") is False
        assert s.was_handled("赵女士", "句子%d" % (s.已处理保留条数 + 2)) is True

    def test_旧存档没有这个键也不炸(self, tmp_path):
        from boss_bot.state_store import StateStore
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"chats": {"赵女士": {"last_msg_hash": "abc"}}}), encoding="utf-8")
        s = StateStore(path=p)
        assert s.was_handled("赵女士", "随便") is False
        s.mark_handled("赵女士", "随便", "text")
        assert s.was_handled("赵女士", "随便") is True


class 跟进不反悔Test:
    def _会(self, 我方句子, HR最后=True):
        旧 = (datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
        对话 = [{"text": "有兴趣聊聊吗？", "is_mine": False, "mid": "100", "time": 旧}]
        for i, 句 in enumerate(我方句子):
            对话.append({"text": 句, "is_mine": True, "mid": str(200 + i), "time": 旧})
        return {"account_index": 0, "chat_name": "梁玲", "company": "某某人力",
                "job_name": "兼职·数据标注专员20-30元/时", "updated_at": 旧,
                "messages": 对话}

    def test_拒话被后面客气话压在下面也不追(self):
        会 = self._会(["不好意思哈，这个岗方向不太合适，就不投了，谢谢您～",
                     "（无需回复，对话已自然结束）", "嗯嗢，好的～"])
        assert 拒过这单(会) is True
        assert followup_due([会], {}) == []

    def test_那句还在招吗只许问一次(self):
        会 = self._会(["武先生您好，天盛传媒这个岗位还在招吗？我这边只找线上远程就能做的。"])
        assert 问过还招吗(会) is True
        assert followup_due([会], {}) == [], "第一次没人回，第二次只会更像机器"

    def test_HR在那句之后回过话就放开(self):
        """审查自己写的判据时发现的过严：盘上 179 个被拦会话里有 49 个是这一种。"""
        旧 = (datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
        会 = {"account_index": 0, "chat_name": "刘工", "company": "河北鼎联",
              "job_name": "兼职·数据标注专员20-30元/时", "updated_at": 旧,
              "messages": [
                  {"text": "有兴趣聊聊吗？", "is_mine": False, "mid": "100", "time": 旧},
                  {"text": "您好，这个岗位还在招吗？我只找线上的。", "is_mine": True,
                   "mid": "200", "time": 旧},
                  {"text": "我们有线上的项目，每天在家用手机电脑做一做。", "is_mine": False,
                   "mid": "300", "time": 旧},
                  {"text": "好嘞，那我把简历发您看下。", "is_mine": True,
                   "mid": "400", "time": 旧}]}
        assert 问过还招吗(会) is False, "对方回过话之后就不是复读，拦掉等于自己掐漏斗"
        assert len(followup_due([会], {})) == 1

    def test_正常聊过又没问过还招吗的照旧追(self):
        """反向哨兵：三条门不能把漏斗整个掐掉。"""
        会 = self._会(["好的，我先把简历发您看看～"])
        assert 拒过这单(会) is False and 问过还招吗(会) is False
        assert len(followup_due([会], {})) == 1

    def test_拒话词表盖得住我们自己的两条收尾模板(self):
        from boss_bot.main_loop import OFFLINE_INTERVIEW_DECLINE
        from boss_bot.reply_engine import FAMILY_DECLINE_REPLY
        for 句 in (OFFLINE_INTERVIEW_DECLINE, FAMILY_DECLINE_REPLY):
            assert any(k in 句 for k in 拒话说法), "收尾模板改了词表没跟上: %s" % 句[:24]
        for 词 in REFUSAL_ENDINGS:
            assert 词 in 拒话说法, "旧标记一条不许掉出词表"

    def test_主循环动手前用同一份判据再核一次(self):
        import inspect
        import boss_bot.main_loop as ml
        源 = inspect.getsource(ml.UnifiedBotLoop._send_followup)
        assert "拒过这单" in 源 and "问过还招吗" in 源, \
            "候选闸门之外还要拿页面上那段实时消息再判一次"


class 旁白不发出去Test:
    def test_那句旁白拦得住(self):
        from boss_bot.reply_engine import reply_rejection
        assert reply_rejection("（无需回复，对话已自然结束）"), "10-23 12:56 真发出去过这一句"
        assert reply_rejection("无需回复")
        assert reply_rejection("(这条不用回复了)")
        # 只靠"整句裹在括号里"那一条抓住的（词表里没有这些字）：
        # 括号旁白的形状比词表更通用，词表漏了它也得拦
        assert reply_rejection("（这边没什么好回的了）")

    def test_正常句子里有括号不误伤(self):
        """反向哨兵：拦宽了就把人话也拦掉，等于回复腿哑掉。"""
        from boss_bot.reply_engine import reply_rejection
        for 句 in ("嗯嗢，好的～", "好嘞（明天见）", "薪资面谈就行，想先问下这个岗做什么？",
                 "我主要找线上数据分析这类的（可日结）哈",
                 # 「应该/建议回复」在句中是人话，只有摆在开头才是旁白
                 "这个我应该回复您一下", "您说的事我建议回复慢了点，实在抱歉"):
            assert reply_rejection(句) == "", 句

    def test_旁白开头那几条只认句子开头(self):
        from boss_bot.reply_engine import reply_rejection
        assert reply_rejection("回复如下：好的，我发简历")
        assert reply_rejection("建议回复对方一句收尾")

    def test_发送链上这一族走的是留痕加换接口(self):
        import inspect
        from boss_bot.reply_engine import ReplyEngine
        源 = inspect.getsource(ReplyEngine._call_chat)
        assert "reply_rejection" in 源, "旁白要在发送前那一道闸拦，不是拦在别处"

    def test_提示词里也讲了这条口径(self):
        from boss_bot.reply_engine import ReplyEngine
        assert "无需回复" in ReplyEngine.跟进指令


class 旁白不进上下文Test:
    """发出去的那句已经躺在存档里（用户数据不删），拼上下文这一处得把它挡掉。"""

    def _对话(self):
        return [_行("好嘟", False, 399335656215309),
                _行("（无需回复，对话已自然结束）", True, 399737650987778),
                _行("这个岗位方向不太合适，就不投了", True, 399080880149251)]

    def test_存档里的旁白行不再喂给模型(self):
        from boss_bot.prompts import 对话窗口, build_conversation_history
        留 = 对话窗口(self._对话(), 最少句数=30)
        assert [msg_body(m) for m in 留] == ["好嘟", "这个岗位方向不太合适，就不投了"]
        assert "无需回复" not in build_conversation_history(留)

    def test_正常带括号的那句照旧进上下文(self):
        """反向哨兵：把"括号"整个当旁白筛，会把人话从上下文里筛掉。"""
        from boss_bot.prompts import 对话窗口
        对话 = self._对话() + [_行("我主要找线上数据分析这类的（可日结）哈", True, 399743331099393)]
        留 = 对话窗口(对话, 最少句数=30)
        assert any("可日结" in msg_body(m) for m in 留)

    def test_旁白不占那30句的名额(self):
        from boss_bot.prompts import 对话窗口
        对话 = [_行("（无需回复，对话已自然结束）", True, str(1000 + i)) for i in range(40)]
        对话 += [_行("第%d句" % i, False, str(100 + i)) for i in range(30)]
        留 = 对话窗口(对话, 最少句数=30)
        assert len(留) == 30, "旁白把名额占满，真正聊过的 30 句就凑不齐了"
        assert all("第" in msg_body(m) for m in 留)

    def test_发送闸门和上下文用的是同一份判据(self):
        """两份词表迟早对不上：界面看到的一句"能发"、上下文里却当它是旁白。"""
        import boss_bot.reply_engine as RE
        from boss_bot.prompts import 旁白
        assert RE.旁白判据 is 旁白


class 岗位标题不进正文Test:
    def test_洗掉尾巴上的页面文字(self):
        from boss_bot.contact_ledger import 洗岗位标题
        assert 洗岗位标题("人力招聘实习生100-150元/天长沙查看职位") == "人力招聘实习生100-150元/天长沙"
        assert 洗岗位标题("兼职·数据标注专员 5-30元/时 合肥 查看职位") == "兼职·数据标注专员 5-30元/时 合肥"
        assert 洗岗位标题("  数据分析实习生  ") == "数据分析实习生"
        assert 洗岗位标题("") == ""

    class _引擎没答上来:
        """现编跟进在容灾链全空时返回的就是兜底句——这里只测那句兜底长什么样。"""

        def 现编跟进(self, 对话, boss_name, job_name, 兜底句=""):
            return 兜底句

    def _文本(self, item, boss, 标题, 对话=None):
        import boss_bot.main_loop as ml
        loop = ml.UnifiedBotLoop.__new__(ml.UnifiedBotLoop)
        loop._reply_engine = self._引擎没答上来()
        return loop._followup_text(item, boss, 标题, 对话 or [])

    def test_跟进兜底句不粘整串标题(self):
        """盘上真发出去过 4 条「之前聊的…100-150元/天长沙查看职位我很有兴趣」。"""
        文 = self._文本({"times": 1, "company": "上海能良"}, "沈女士",
                       "数据分析专员6-8K上海查看职位")
        assert 文 == ("沈女士您好，上海能良这个岗位还在招吗？我这边只找线上远程就能做的，"
                     "方便的话咱们线上细聊。")
        assert "查看职位" not in 文

    def test_跟进兜底第一条只说岗位名(self):
        文 = self._文本({"times": 0, "company": "鲲图之火"}, "朱微",
                       "人力招聘实习生100-150元/天长沙查看职位")
        assert "查看职位" not in 文, 文
        assert "100-150" not in 文, 文
        assert "人力招聘实习生" in 文, 文

    def test_提示词里钉住不许照抄标题(self):
        from boss_bot.prompts import build_user_prompt
        文 = build_user_prompt("朱微", "人力招聘实习生100-150元/天长沙查看职位", "好嘟",
                              [_行("好嘟", False, 1)])
        assert "不许原样念给对方" in 文

    def test_会话页读标题就把尾巴切掉(self):
        """判据打在读取出口那一处，不是打在下游某一次拼接上。"""
        import inspect
        from boss_bot.page_handler import BossChatHandler
        源 = inspect.getsource(BossChatHandler.get_job_name)
        assert "洗岗位标题" in 源


class 排重与上下文全库门Test:
    """这几条拿盘上真数据当门：判据一变宽/变严就红。"""

    def test_存档里没有mid的行全是我方自记的(self):
        我方无 = 对方无 = 带 = 0
        目录 = os.path.join(ROOT, "messages")
        for name in os.listdir(目录):
            if not name.endswith(".json"):
                continue
            try:
                d = json.load(open(os.path.join(目录, name), encoding="utf-8"))
            except Exception:
                continue
            for m in d.get("messages") or []:
                if not msg_body(m):
                    continue
                if str(m.get("mid") or "").isdigit():
                    带 += 1
                elif m.get("is_mine"):
                    我方无 += 1
                else:
                    对方无 += 1
        assert 带 > 1000, "样本太小这条门等于没测: %d" % 带
        assert 对方无 == 0, \
            "出现 %d 行「对方说的、没有 mid」，那 排序位置 把没 mid 当作我方自记就不成立" % 对方无
        assert 我方无 > 0

    def test_同一句被回多次的历史账还在(self):
        """这条是回归账，不是判据：数字变了说明存量被动过，要停下来问。"""
        p = os.path.join(ROOT, "data", "reply_records.json")
        if not os.path.exists(p):
            pytest.skip("这份检出里没有回复记录")
        d = json.load(open(p, encoding="utf-8"))
        记 = d["records"] if isinstance(d, dict) else d
        组 = {}
        for r in 记:
            if not (r.get("reply_content") or "").strip() or r.get("is_skipped"):
                continue
            句 = str(r.get("received_message") or "").strip()
            if 句:
                组.setdefault((r.get("chat_name"), 句), []).append(r)
        assert len([k for k, v in 组.items() if len(v) >= 2]) >= 40, \
            "重复回复的历史账对不上，说明这份数据不是我取证的那一份"
