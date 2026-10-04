# -*- coding: utf-8 -*-
"""HR 发来"交换微信/电话"的卡片，要真去点那张卡片上的「同意」。

2026-10-04 用户原话：「我感觉你现在发不了微信啊」。盘上取证：这类卡片以前只会被
回一句"还是在平台上聊吧"——平台把两个按钮送到眼前，我们用文字把送上门的
联系方式推了回去。修完之后这条链必须有：判据、点按钮、落账、开关、界面回读。

卡片真实结构取自 tools/chat_page_structure.json：
.message-card-wrap > .message-card-top-title + .message-card-buttons > span.card-btn
卡片正文样本取自 messages/*.json 里线上采到的 card_text（见 CARD_WECHAT 等常量）。
"""
import inspect
from types import SimpleNamespace

import pytest

from boss_bot.intent import is_contact_exchange_card
from boss_bot.reply_engine import ReplyEngine
from boss_bot.unified_config import UnifiedConfig

# 线上真实卡片文案（去掉我们自己的动作行，只留平台原话）
CARD_WECHAT = "我想要和您交换微信，您是否同意 拒绝 同意"
CARD_WECHAT_SAFE = ("我想要和您交换微信，您是否同意 为保障您的安全建议在平台内沟通，"
                    "微信沟通中需特别保护您的个人信息，谨防受骗。 拒绝 同意")
CARD_PHONE = "我想要一个您的电话号码，您是否同意 拒绝 同意 或者发送 微信号码 附件简历"
CARD_RESUME = "我想要一份您的附件简历，您是否同意 拒绝 同意"


class 判据Test:
    """判据必须窄：对着不是卡片的话点"同意"，等于凭空找一个不存在的按钮。"""

    @pytest.mark.parametrize("text", [CARD_WECHAT, CARD_WECHAT_SAFE, CARD_PHONE])
    def test_真卡片要认(self, text):
        assert is_contact_exchange_card(text), text

    @pytest.mark.parametrize("text", [
        "方便加个微信聊吗？",                      # HR 顺口提微信，没有按钮可点
        "我的微信号是 abc123，你加我",
        "加微信详聊",
        "",
        None,
    ])
    def test_顺口提微信不算卡片(self, text):
        assert not is_contact_exchange_card(text)

    def test_简历卡片不能被当成联系方式卡片(self):
        """同样带"您是否同意"，点错卡片就是把简历发出去——那是另一个动作"""
        assert not is_contact_exchange_card(CARD_RESUME)

    def test_没有是否同意就不算(self):
        assert not is_contact_exchange_card("我想要和您交换微信")


class 决策Test:
    def test_卡片要走contact动作(self):
        action, content, meta = ReplyEngine().get_reply(CARD_WECHAT)
        assert meta["intent"] == "contact_request"
        assert action == "contact", f"卡片还走文字回复：{action} {content}"
        assert content is None, "点按钮就不该再发一句话，两号各回一次是骚扰"

    def test_顺口提微信仍走平台内沟通话术(self):
        action, content, meta = ReplyEngine().get_reply("方便加个微信聊吗？")
        assert meta["intent"] == "contact_request"
        assert action == "text"
        assert content and "沟通" in content


class 点按钮Test:
    """JS 只准点卡片内部那枚「同意」，不退而求其次点页面上别的同名按钮。"""

    def test_选择器锁死在卡片范围内(self):
        from boss_bot.page_handler import BossChatHandler
        src = inspect.getsource(BossChatHandler.accept_contact_exchange)
        assert ".message-card-wrap" in src
        assert ".message-card-top-title" in src
        assert ".message-card-buttons .card-btn" in src
        assert '=== "同意"' in src, "按钮文字必须逐字对上，别拿模糊匹配点掉「拒绝」"
        assert "return result == \"clicked\"" in src

    def test_看不见的按钮不算点到(self):
        from boss_bot.page_handler import BossChatHandler
        src = inspect.getsource(BossChatHandler.accept_contact_exchange)
        assert "getClientRects" in src, "已经处理过的卡片按钮会留在 DOM 里但不可见"

    def test_三种结果只有一种算成功(self):
        from boss_bot.page_handler import BossChatHandler

        class _Page:
            def __init__(self, ret):
                self._ret = ret

            def run_js(self, *args, **kwargs):
                return self._ret

        for ret, expect in [("clicked", True), ("no-card", False),
                            ("no-agree-btn", False), ("", False)]:
            handler = SimpleNamespace(page=_Page(ret))
            got = BossChatHandler.accept_contact_exchange(handler)
            assert got is expect, f"{ret!r} 被判成 {got}"

    def test_JS抛异常要当没点到而不是炸掉回复轮(self):
        from boss_bot.page_handler import BossChatHandler

        class _Boom:
            def run_js(self, *args, **kwargs):
                raise RuntimeError("与页面的连接已断开")

        assert BossChatHandler.accept_contact_exchange(
            SimpleNamespace(page=_Boom())) is False


class 落账Test:
    """四条路径都要留痕：开关关、演练、点成功、点失败。"""

    def _loop(self, *, accept=True, dry_run=False, click_ok=True):
        from boss_bot.main_loop import UnifiedBotLoop
        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp.config = SimpleNamespace(
            dry_run=dry_run,
            reply=SimpleNamespace(accept_contact_exchange=accept))
        lp.records = []
        lp.events = []
        lp.bots = []
        lp.replies = []
        lp.logs = []
        lp.clicked = 0

        def _add_record(**kw):
            lp.records.append(kw)

        def _emit(**kw):
            lp.events.append(kw)

        def _click():
            lp.clicked += 1
            return click_ok

        lp._reply_engine = SimpleNamespace(
            wait_human_delay=lambda: None, _add_record=_add_record,
            _last_ai_model="")
        lp._chat_handler = SimpleNamespace(accept_contact_exchange=_click)
        lp._msg_store = SimpleNamespace(append_bot_message=lambda *a, **k: lp.bots.append(k))
        lp._stats = SimpleNamespace(record_reply=lambda **k: lp.replies.append(k))
        lp._emit_reply_event = _emit
        lp._log = lambda level, msg: lp.logs.append((level, msg))
        return lp

    def _call(self, lp):
        return lp._handle_reply_action(
            "contact", None, {"intent": "contact_request", "source": "intent"},
            "杨女士", "数据分析", CARD_WECHAT, chat_company="某某科技")

    def test_点成了要记一条同意交换联系方式(self):
        lp = self._loop()
        assert self._call(lp) is True
        assert lp.clicked == 1
        rec = lp.records[-1]
        assert rec["reply_source"] == "contact"
        assert rec["reply_content"] == "[已同意交换联系方式]"
        assert rec.get("is_skipped") is None, "点成了不能记成跳过"
        assert lp.events[-1]["status"] == "replied"
        assert lp.bots and lp.bots[0]["action"] == "contact"

    def test_开关关着要留痕并留给人工(self):
        lp = self._loop(accept=False)
        assert self._call(lp) is True
        assert lp.clicked == 0, "关了开关还去点按钮 = 改了不生效"
        rec = lp.records[-1]
        assert rec["is_skipped"] is True and rec["reply_source"] == "skip"
        assert "开关" in rec["skip_reason"]

    def test_演练模式不点按钮也不算处理过(self):
        lp = self._loop(dry_run=True)
        assert self._call(lp) is False, "演练返回 False，调用方才不会把这条标成已回复"
        assert lp.clicked == 0

    def test_没点到就记跳过不能再追一句文字(self):
        lp = self._loop(click_ok=False)
        assert self._call(lp) is True
        assert lp.records[-1]["is_skipped"] is True
        assert lp.events[-1]["status"] == "skipped"
        assert lp.bots == [], "没交换成功就别往会话里写动作行"
        assert all(r["reply_content"] != "[已同意交换联系方式]" for r in lp.records)


class 配置Test:
    def test_默认开(self):
        assert UnifiedConfig().reply.accept_contact_exchange is True

    def test_界面写进去要读得回来(self):
        saved = UnifiedConfig().to_dict()
        assert "accept_contact_exchange" in saved["reply"]
        saved["reply"]["accept_contact_exchange"] = False
        cfg = UnifiedConfig()
        cfg._apply_bot_config(saved)
        assert cfg.reply.accept_contact_exchange is False, "关掉后被默认值吞掉就是改了不生效"

    def test_按账号覆盖也认这个键(self):
        base = UnifiedConfig()
        base.greet.accounts[0].settings = {"reply": {"accept_contact_exchange": False}}
        assert base.apply_account(0).reply.accept_contact_exchange is False
        assert base.reply.accept_contact_exchange is True, "覆盖不能漏到全局基准"


class 界面Test:
    """红线：面板上任何开关都要真回读、真落盘，否则撤掉入口。"""

    @pytest.fixture(scope="class")
    def html(self):
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "flask-version" / "templates" / "index.html"
        return p.read_text(encoding="utf-8")

    def test_开关有控件有回填有落盘(self, html):
        for anchor in (
            'id="advAcceptContact"',
            "toggleAdvBool('accept_contact_exchange')",
            "setEl('advAcceptContact', 'toggle-switch' + (_rl.accept_contact_exchange !== false ? ' on' : ''));",
            "document.getElementById('advAcceptContact').classList.contains('on')",
        ):
            assert anchor in html, f"界面缺这一处：{anchor}"

    def test_来源标签和筛选项都登记了(self, html):
        assert "if (replySource === 'contact') return '同意交换联系方式';" in html
        assert '<option value="contact">' in html, "来源筛选少了这一类就筛不到"
