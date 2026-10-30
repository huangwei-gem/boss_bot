# -*- coding: utf-8 -*-
"""许过的"发简历"必须真的发出去，不许只留在存档里。

2026-10-23 17:00:33 何女士回「可以滴」，机器回「好的，我这就把简历发您哈」——
`send_resume()` 一次都没调。按号现数盘上的账：2602 个存档会话里 142 个有简历卡片，
而 46 条许过发简历的回复，对应的会话**一张卡片都没有**（0/46）。
根因是发简历的闸门只认"对方那句是索要"，「可以滴」这种应声不在判据里，
于是那句话成了没人兑现的承诺，跟进腿还把它当成"对方最新消息"往下传。

现在：文字里许了发简历 + 对方确实要了或应了声 → 这一单改走真实发送动作；
真发不出去时由既有降级话术说实话，不再让承诺独自留在存档里。
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import main_loop as ML  # noqa: E402
from boss_bot.pending_resume import 简历承诺, 简历已获准  # noqa: E402


class 认承诺Test:
    """判据只能用线上真发出去过的那几句，不拿"看着像"当依据"""

    真句 = [
        "李女士，岗位还在招的话我把简历发您看下，随时方便线上聊",
        "好的，我这就把简历发您哈，我的数据标注和分析经验可能对工作有帮助。",
        "那我直接把简历发您，您看行不？",
        "嗯，对，我目前是本科在读，25年9月后可全职投入。先把简历发您看看？",
        "姚杰峰，您方便时我们线上聊聊？我可以先把简历发您看看。",
    ]

    @pytest.mark.parametrize("句", 真句)
    def test_线上许过的每一句都认得(self, 句):
        assert 简历承诺(句), 句

    不是承诺 = [
        "简历上写了这三段数据标注的项目",
        "我先把岗位描述看一遍再回您",
        "在的，这个岗我合适，随时方便线上聊",
    ]

    @pytest.mark.parametrize("句", 不是承诺)
    def test_没许发简历的不算(self, 句):
        assert 简历承诺(句) == "", 句

    说反了的 = [
        "先不把简历发您了，等您方便再说",
        "暂时没把简历发您",
        "不用发简历给您了",
    ]

    @pytest.mark.parametrize("句", 说反了的)
    def test_否定式不算承诺(self, 句):
        """嘴上说不发、手上却发出去，比漏发更糟"""
        assert 简历承诺(句) == "", 句

    def test_收紧之后真承诺照算(self):
        assert 简历承诺("好的，我这就把简历发您哈")
        assert 简历承诺("不太忙的时候我把简历发您看看"), "「不太忙」里的不该挡掉这句承诺"


class 对方允没允Test:

    def test_直接索要算(self):
        assert 简历已获准([{"is_mine": False, "text": "方便发一份简历过来吗"}])

    def test_对我们那句承诺应一声也算(self):
        """何女士那一单：索要没写全，但「可以滴」就是把简历要过去了"""
        对话 = [{"is_mine": True, "text": "在的，这个岗我合适，简历发您看下？"},
                {"is_mine": False, "text": "可以滴"}]
        assert 简历已获准(对话)

    def test_没许过事的时候对方说可以不算(self):
        """反向哨兵：上一句我们只说了"随时线上聊"，「可以滴」不是在要简历"""
        对话 = [{"is_mine": True, "text": "在的，这个岗我合适，随时方便线上聊"},
                {"is_mine": False, "text": "可以滴"}]
        assert not 简历已获准(对话)

    def test_长句里夹一个可以不算(self):
        对话 = [{"is_mine": True, "text": "简历发您看下？"},
                {"is_mine": False, "text": "可以先把我加进面试名单里吗，谢谢啦"}]
        assert not 简历已获准(对话)

    def test_对方压根没说话不算(self):
        assert not 简历已获准([{"is_mine": True, "text": "简历发您看下？"}])
        assert not 简历已获准([])


def _造循环(monkeypatch, 存档=None):
    with patch("boss_bot.main_loop.BrowserManager"):
        lp = ML.UnifiedBotLoop(account_index=0)
    lp.日志 = []
    lp._log = lambda level, msg: lp.日志.append((level, msg))
    lp._msg_store = SimpleNamespace(get_messages=lambda *a, **k: list(存档 or []))
    return lp


class 承诺要升格Test:

    def test_对方应了声就要升格(self, monkeypatch):
        存档 = [{"is_mine": True, "text": "在的，这个岗我合适，简历发您看下？"},
                {"is_mine": False, "text": "可以滴"}]
        lp = _造循环(monkeypatch, 存档)
        assert lp._简历承诺要升格("好的，我这就把简历发您哈", "何女士",
                                 "数据标注", "某公司", "可以滴")
        assert any("改走真实发送动作" in m for _, m in lp.日志), lp.日志

    def test_对方没要也没应就不升格(self, monkeypatch):
        """简历不能因为机器自己想说就发出去——闸门照旧只认对方的话"""
        lp = _造循环(monkeypatch, [{"is_mine": True, "text": "简历发您看下？"}])
        assert not lp._简历承诺要升格("我把简历发您看看", "何女士",
                                     "数据标注", "某公司", "在忙，晚点说")

    def test_这句没许事就不去读存档(self, monkeypatch):
        叫过 = []
        lp = _造循环(monkeypatch)
        lp._msg_store = SimpleNamespace(
            get_messages=lambda *a, **k: 叫过.append(1) or [])
        assert not lp._简历承诺要升格("在的，随时线上聊", "何女士", "", "", "在吗")
        assert 叫过 == [], "没有承诺也去读一遍存档，每轮白拷一次盘"

    def test_存档读不动时只按那一句判(self, monkeypatch):
        """读不到上下文不许当成"对方应了"，把简历甩给没要过的人"""
        lp = _造循环(monkeypatch)

        def 炸(*a, **k):
            raise RuntimeError("存档读不动")

        lp._msg_store = SimpleNamespace(get_messages=炸)
        assert not lp._简历承诺要升格("我把简历发您看看", "何女士", "", "", "在吗")
        assert lp._简历承诺要升格("我把简历发您看看", "何女士", "", "",
                                 "麻烦发一份简历过来")

    def test_文字承诺真的走了一次发送动作(self, monkeypatch):
        """端到端一句：action=text + 许了发简历 → send_resume 被调用、那句话不再发"""
        存档 = [{"is_mine": True, "text": "简历发您看下？"},
                {"is_mine": False, "text": "可以滴"}]
        lp = _造循环(monkeypatch, 存档)
        lp._dry_run = lambda *a, **k: False
        lp._resume_delivered = lambda *a, **k: False
        lp._chat_handler = MagicMock()
        lp._chat_handler.send_resume.return_value = True
        lp._state_store = MagicMock()
        lp._stats = MagicMock()
        lp._stats_dict = {"resume_sent": 0}
        lp._metrics = MagicMock()
        lp._msg_store.append_bot_message = MagicMock()
        lp._msg_store.get_messages = lambda *a, **k: list(存档)
        lp._reply_engine = MagicMock()
        lp._persist_resume_card = MagicMock()
        lp._emit_reply_event = MagicMock()
        lp._account_name_for_log = "号0"

        句 = "好的，我这就把简历发您哈"
        assert lp._handle_reply_action("text", 句, {"source": "ai"}, "何女士",
                                       "数据标注", "可以滴",
                                       chat_company="某公司", hr_recent="可以滴")
        assert lp._chat_handler.send_resume.called, "许了却没发，还是那单悬着的账"
        assert 句 not in [c.args[0] for c in
                          lp._chat_handler.send_text.call_args_list], "那句承诺不该单独发出去"
        由 = [c.kwargs.get("reply_reason") for c in
              lp._reply_engine._add_record.call_args_list]
        assert any("按承诺发送" in (r or "") for r in 由), \
            "记录里要写明这句是文字里许下来的，不能混成 HR 直接索要: %s" % 由


def test_判据只有一份():
    """main_loop 用 pending_resume 的那一份，不许再抄一遍词表"""
    import inspect
    源 = inspect.getsource(ML.UnifiedBotLoop._简历承诺要升格)
    assert "简历承诺(" in 源 and "简历已获准(" in 源
    assert "说法 = (" not in 源, "词表被抄进主循环，改一处漏一处"
