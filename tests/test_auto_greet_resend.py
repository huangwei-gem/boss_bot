# -*- coding: utf-8 -*-
"""BOSS 自己把招呼发出去之后，本号那句 AI 现编的话必须补发得出去。

取证（logs/greet_engine.log 全量 + 今天 data/greet_records.json）：
- 「点了「继续沟通」也读不到会话气泡」累计 427 次，「已补发本号招呼语」0 次，
  「BOSS 自动发出的就是本号招呼语」0 次——这条补发腿从来没通过。
- 于是今天 155 条真投出去的里 120 条 HR 只看到平台预设那句
  「您好，看到您的招聘信息，我很感兴趣，希望可以进一步沟通。」
  这正是用户两次反对的"固定招呼语"。
- 只读探针看线上会话页：刚建立的会话里 .message-item 可以是 0（平台那句是系统条），
  但 #chat-input 在、顶栏 .top-info-content 在（"杨海波 合肥海瑾云科技 经理 更多 …"）。
  旧判据只认 .message-item，把"进得去但还没气泡"一律判成进不去。

补发前必须用顶栏公司名对上这个岗位的公司：认错会话等于把话发给别家，
比发一句固定招呼语严重得多，对不上就不发。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.greet_engine import chat_is_open, header_matches_company  # noqa: E402

# 线上实测的两段顶栏（9222 / 9223 只读探针）
HEAD_YANG = "杨海波 合肥海瑾云科技 经理 更多 兼职·数据标注专员 5-30元/时 合肥 查看职位"
HEAD_WU = "伍女士 云山鲜 人事主管 更多 长沙市快递员 7-12K 长沙 查看职位"


class 会话页判据Test:
    def test_刚建立的会话没有气泡也算进得去(self):
        read = {"items": 0, "input": True, "head": HEAD_YANG}
        assert chat_is_open(read) is True

    def test_岗位页不算会话页(self):
        assert chat_is_open({"items": 0, "input": False, "head": ""}) is False

    def test_只有输入框没有顶栏不算(self):
        """抽屉刚点开时顶栏可能还没渲染：那时宁可再多等一轮"""
        assert chat_is_open({"items": 0, "input": True, "head": ""}) is False

    def test_有气泡当然算(self):
        assert chat_is_open({"items": 3, "input": False, "head": ""}) is True


class 补发前必须对上人Test:
    def test_公司对得上(self):
        assert header_matches_company(HEAD_YANG, "合肥海瑾云科技有限公司") is True
        assert header_matches_company(HEAD_WU, "云山鲜") is True

    def test_公司对不上绝不发(self):
        """把 AI 招呼语发给别家的 HR，比发一句固定话严重得多"""
        assert header_matches_company(HEAD_YANG, "星辰时代") is False

    def test_顶栏或公司缺一个就不发(self):
        assert header_matches_company("", "合肥海瑾云科技") is False
        assert header_matches_company(HEAD_YANG, "") is False

    def test_后缀与括号写法不影响判定(self):
        assert header_matches_company(HEAD_YANG, "合肥海瑾云科技（安徽）有限公司") is True

    def test_共用一个通用词不算同一家(self):
        """"数据标注"这种通用词两边都有，不能当成同一家公司"""
        assert header_matches_company("李女士 某某数据标注招聘专员", "北京数据标注中心") is False


class 接线Test:
    def test_等会话用的是新判据(self):
        import inspect
        from boss_bot.greet_engine import GreetEngine
        源 = inspect.getsource(GreetEngine._wait_chat_open)
        assert "chat_is_open" in 源, "还在只认气泡，新会话永远判成进不去"

    def test_打字之前先对上公司(self):
        import inspect
        from boss_bot.greet_engine import GreetEngine
        源 = inspect.getsource(GreetEngine._auto_greet_followup)
        assert 源.index("header_matches_company") < 源.index("input_area.input(greeting)"), \
            "先打字再核对，认错人就发出去了"

    def test_探针把输入框和顶栏一起带回来(self):
        from boss_bot.greet_engine import LAST_MINE_BUBBLE_JS
        assert "top-info-content" in LAST_MINE_BUBBLE_JS
        assert "input: input" in LAST_MINE_BUBBLE_JS
        assert "head: head" in LAST_MINE_BUBBLE_JS
