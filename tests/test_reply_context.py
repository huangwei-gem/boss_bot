# -*- coding: utf-8 -*-
"""回复必须带着这段对话说话（他 10-17 第五次提这件事，口径抬到"至少上下 30 句"）。

现场数字（本轮从真数据里量出来的，不是推测）：
- 存档 11404 行对话里有 **1843 行（16.16%）从来没进过 AI 提示词**：
  `build_conversation_history` 只读 `text`，而 HR 的"我想要一份您的附件简历，您是否同意"、
  微信号卡片、"面试超时未接受"这些行的正文在 `card_text`/`block` 里。AI 看不到对方说过什么，
  只能对着空气回一句。今天 80 条主动跟进的会话里，这样的卡片句有 89 句。
- 窗口按**原始行数**截（reply_engine 取 30 行、main_loop 取 50 行），漏掉 16% 之后，
  实际进提示词的句数今天实测是 3、11、28 这种量——离"至少 30 句"差得远。

所以判据定在"看得懂的句子"上，不再是原始行数：
- 一处 `消息正文()` 定义"这一行到底有没有正文"，拼历史、排重、拆最新一句都用它，
  不许各写一份（写两份就会像这次一样只补了界面那头）。
- `对话窗口()` 从后往前取，取够 最少对话句数=30 句为止；原始行取到 历史原始行上限 就收，
  免得几百行的长会话把 token 打满。
- 主动跟进那句要按这段对话现编（他早就说过"不要用固定的话，要用 AI 生成的"），
  接口全挂了才退回模板。
"""
import inspect
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import prompts  # noqa: E402


def 卡片句(t="我想要一份您的附件简历，您是否同意 拒绝 同意", who=False, 时="10:00"):
    return {"text": "", "card_text": t, "block": t, "time": 时,
            "is_mine": who, "mid": 时 + t[:4]}


def 说句(t, who=False, 时="10:00"):
    return {"text": t, "time": 时, "is_mine": who, "mid": 时 + t[:4]}


def 历史句数(提示词):
    return sum(1 for 行 in 提示词.splitlines() if 行.startswith(("对方:", "我: ")))


class 卡片正文也得进对话历史Test:

    def test_对方发的卡片要出现在历史里(self):
        文 = prompts.build_conversation_history([卡片句()])
        assert "附件简历" in 文, "HR 要简历这张卡片，AI 根本没看见"
        assert 文.startswith("对方:"), 文

    def test_卡片要标出来让模型知道这不是对方打的字(self):
        assert "卡片" in prompts.build_conversation_history([卡片句()])

    def test_只剩时间标签的行不算正文(self):
        """界面已经不再画这种假行，提示词里也不能凭它多出一句空气"""
        assert prompts.build_conversation_history(
            [{"text": "", "block": "10:00", "time": "10:00"}]) == "（无历史消息）"

    def test_我自己发的卡片也算我说过(self):
        文 = prompts.build_conversation_history([卡片句("您的附件简历 已发送给Boss", who=True)])
        assert 文.startswith("我: "), 文

    def test_普通文本行不受影响(self):
        文 = prompts.build_conversation_history([说句("你好"), 说句("在的", who=True)])
        assert 文.splitlines() == ["对方: 你好", "我: 在的"]


class 最新一句不许在提示词里出现两次Test:

    def test_卡片当最新一句时排重要用同一份正文判据(self):
        对 = [说句("方便聊一下吗"), 卡片句()]
        提示词 = prompts.build_user_prompt("胡女士", "数据分析",
                                        prompts.消息正文(对[-1]), 对)
        assert 提示词.count("附件简历") == 1, "卡片既留在历史里又当成最新消息发了一遍"


class 窗口要按看得懂的句子给够Test:

    def test_至少30句有正文的对话(self):
        """一半行是卡片：按原始行截 30 就只剩 15 句可读，这正是要堵的口子"""
        对 = []
        for i in range(60):
            if i % 2:
                对.append(卡片句("第%d句卡片内容" % i, who=(i % 4 == 0), 时="%02d:00" % i))
            else:
                对.append(说句("第%d句普通内容" % i, who=(i % 5 == 0), 时="%02d:01" % i))
        提示词 = prompts.build_user_prompt("胡女士", "数据分析", "第59句普通内容", 对)
        assert 历史句数(提示词) >= prompts.最少对话句数, \
            "只进了 %d 句，他要的是至少 %d 句" % (历史句数(提示词), prompts.最少对话句数)

    def test_短会话有几句给几句不许灌水(self):
        对 = [说句("你好"), 说句("在的", who=True), 卡片句(), 说句("好的")]
        # 最后那句"好的"当成最新消息单独列，历史里就是前面 3 句
        assert 历史句数(prompts.build_user_prompt("胡女士", "岗", "好的", 对)) == 3

    def test_超长会话要有原始行上限(self):
        """窗口是"取够 30 句为止"，不是"整段历史塞进去"——几百行的会话要把 token 打爆"""
        对 = [说句("第%d句" % i, who=(i % 3 == 0), 时="%03d:00" % i) for i in range(400)]
        提示词 = prompts.build_user_prompt("胡女士", "岗", "第399句", 对)
        行 = [i for i, l in enumerate(提示词.splitlines()) if l.startswith(("对方:", "我: "))]
        assert len(行) <= prompts.历史原始行上限
        assert 行[0] > 0, "400 行的会话还从第 0 行开始，说明上限没生效"

    def test_取历史的那两处都要按窗口取够原始行(self):
        """limit 还写死 30/50 的话，窗口再对也没料可取"""
        from boss_bot.reply_engine import ReplyEngine
        记 = {}

        class 库:
            def get_full_dialog(self, chat_name, limit=50, job_name="", company=""):
                记["limit"] = limit
                return [说句("在吗")]

        引擎 = ReplyEngine(message_store=库())
        引擎._ai_providers = []
        引擎._ask_ai = lambda *a, **k: None
        引擎.get_reply("在吗", "胡女士", "岗", chat_name="胡女士")
        assert 记.get("limit", 0) >= prompts.历史原始行上限, 记

    def test_主循环取历史也按同一个上限(self):
        from boss_bot.main_loop import UnifiedBotLoop
        源 = inspect.getsource(UnifiedBotLoop._process_single_chat)
        assert "历史原始行上限" in 源, "还在用 get_full_dialog 默认的 50 行"


class 跟进那句要按这段对话现编Test:

    def test_接口答得上来就用它(self):
        from boss_bot.reply_engine import ReplyEngine
        引擎 = ReplyEngine()
        收 = {}

        def 假问(message, boss, job, history, **kw):
            收.update(message=message, history=history, kw=kw)
            return "您上次问的作品我整理好了，方便的话我们约个线上沟通？"

        引擎._ask_ai = 假问
        话 = 引擎.现编跟进([说句("请问有作品吗"), 说句("我发您一份", who=True)],
                           "吴先生", "陶瓷设计", 兜底句="还在招吗")
        assert 话 == "您上次问的作品我整理好了，方便的话我们约个线上沟通？"
        assert 收["kw"].get("用缓存") is False, "跟进要现编，不能撞缓存里的旧句子"
        assert ("沉默" in 收["kw"].get("指令", "") or "没回" in 收["kw"].get("指令", "")), 收["kw"]

    def test_接口全挂了才退回模板(self):
        from boss_bot.reply_engine import ReplyEngine
        引擎 = ReplyEngine()
        引擎._ask_ai = lambda *a, **k: None
        assert 引擎.现编跟进([说句("在吗")], "吴先生", "岗", 兜底句="还在招吗") == "还在招吗"

    def test_主循环那句要走现编(self):
        from boss_bot.main_loop import UnifiedBotLoop
        assert "现编跟进" in inspect.getsource(UnifiedBotLoop._followup_text), \
            "跟进还是那三条固定模板轮流发"
        assert "现编跟进" in inspect.getsource(UnifiedBotLoop._send_followup) or \
            "对话" in inspect.getsource(UnifiedBotLoop._followup_text), \
            "现编要拿页面上刚读到的对话做依据，不是凭存档旧账"


class 每轮要量出真进了几句Test:

    def test_拼提示词时把句数记一句(self, caplog):
        """下次他再说"没参考上下文"，得有数可看，不能再靠猜"""
        with caplog.at_level(logging.INFO, logger="boss_bot.prompts"):
            prompts.build_user_prompt("胡女士", "岗", "你好", [说句("你好"), 卡片句()])
        assert any("1 句" in r.getMessage() or "2 句" in r.getMessage()
                   for r in caplog.records), [r.getMessage() for r in caplog.records]


class 上下文变了不许复用缓存句Test:
    """缓存键原来只有"最新消息+称呼+岗位"，不看这段对话。
    HR 隔半小时再说一句同样的「在吗」，对话早就往前走了，AI 却直接把第一次那句
    复读出来——在他眼里就是"没参考上下文"。今天日志里这样的缓存命中有 6 次。"""

    def _引擎(self, 收):
        from boss_bot.reply_engine import ReplyEngine
        引擎 = ReplyEngine()
        引擎._ai_providers = [{"key": "k", "model": "m", "url": "http://x", "timeout": 1}]

        def fake(client, model, msg, b, j, h, name, 指令=""):
            收.append([prompts.消息正文(m) for m in (h or []) if prompts.消息正文(m)])
            return f"第{len(收)}句"

        引擎._call_with_rate_limit_retry = fake
        return 引擎

    def test_对话变长了要重新生成(self):
        收 = []
        引擎 = self._引擎(收)
        短 = [说句("在吗")]
        长 = [说句(f"第{i}句", who=(i % 2 == 0)) for i in range(10)] + [说句("在吗")]
        a = 引擎._ask_ai("在吗", "王经理", "数据分析师", 短)
        b = 引擎._ask_ai("在吗", "王经理", "数据分析师", 长)
        assert a != b, "这段对话已经不一样了还复用同一句"
        assert len(收) == 2, 收

    def test_同样的对话第二次还是要走缓存(self):
        """反向哨兵：不能为了上下文把缓存整个废掉，那笔额度是白烧的"""
        收 = []
        引擎 = self._引擎(收)
        短 = [说句("在吗")]
        a = 引擎._ask_ai("在吗", "王经理", "数据分析师", 短)
        c = 引擎._ask_ai("在吗", "王经理", "数据分析师", 短)
        assert a == c and len(收) == 1, 收

    def test_卡片也算进上下文指纹(self):
        """HR 发来"要简历"那张卡片之后再说「在吗」，跟没发过时不能是同一句回复"""
        收 = []
        引擎 = self._引擎(收)
        a = 引擎._ask_ai("在吗", "王经理", "数据分析师", [说句("在吗")])
        b = 引擎._ask_ai("在吗", "王经理", "数据分析师", [卡片句(), 说句("在吗")])
        assert a != b, 收


class 存档对不上公司时宁可没历史也不串号Test:
    """查过一条看着像"公司漂移把上下文弄丢了"的路子，量完决定不走：
    全量日志里 562 个会话只有 1 例是"旧口径取不到、同名在本账号又只有一份存档"，
    而那正是 账号2 的「潘女士@云上在线」——她的存档只有 学洲教育 那一份。
    按那份补，等于把别家 HR 的对话当成这个人说的话喂给 AI，
    回出来的就是他骂的"没参考上下文，乱回答"。所以这里锁死严格口径。"""

    def _库(self, tmp_path, 号=1):
        from boss_bot.message_store import MessageStore
        return MessageStore(base_dir=str(tmp_path), account_index=号)

    def test_同名只有一份但公司不同也不补(self, tmp_path):
        库 = self._库(tmp_path)
        库.save_messages("潘女士", [说句("学洲教育的家教对话")], company="学洲教育")
        assert 库.get_full_dialog("潘女士", company="云上在线") == [], \
            "别家公司的对话不能凭「名字一样」就当这段历史"

    def test_公司对得上要取得到(self):
        """反向哨兵：严格不等于全都取不到，正常会话必须有历史"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            库 = self._库(d)
            库.save_messages("潘女士", [说句("这家在招线上标注")], company="云上在线")
            assert 库.get_full_dialog("潘女士", company="云上在线"), "对得上就该给历史"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
