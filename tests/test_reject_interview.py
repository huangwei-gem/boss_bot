# -*- coding: utf-8 -*-
"""面试邀请的「拒绝」只能点面试那一块，别把送上门的号拒掉。

页面实测（tools/probe_interview_invites.py，2026-10-06）：
- 面试邀请的面板是 .btns 里两个 button.btn-v2 —— 拒绝是 .btn-outline-v2、接受是 .btn-sure-v2；
- 交换联系方式那张卡也叫"拒绝/同意"，但它是 .message-card-wrap 里的 .card-btn。
两种"拒绝"长在同一页上，所以定位必须靠"往上几层里有没有'面试'两个字"来分家。
"""
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.page_handler import BossChatHandler  # noqa: E402


class _脚本页:
    """按调用顺序返回预设结果，并记下每轮跑了哪段 JS。"""

    def __init__(self, rets):
        self._rets = list(rets)
        self.seen = []

    def run_js(self, script, *args, **kwargs):
        self.seen.append(script)
        if not self._rets:
            raise AssertionError("多问了一轮页面")
        return self._rets.pop(0)


def _跑(rets):
    page = _脚本页(rets)
    return BossChatHandler.reject_interview_invite(SimpleNamespace(page=page)), page


def test_没有可点的拒绝按钮就报no_btn():
    got, page = _跑(["no-btn"])
    assert got == "no-btn"
    assert len(page.seen) == 1, "没点到就不该再去问二次确认"


def test_点掉之后没有二次确认就是clicked():
    assert _跑(["clicked", "no-dialog"])[0] == "clicked"


def test_有二次确认要跟着点掉():
    assert _跑(["clicked", "confirmed"])[0] == "clicked-confirmed"


def test_弹了却不认识确认按钮时不许乱点():
    assert _跑(["clicked", "dialog-no-confirm:请选择拒绝原因 关闭"])[0] == "clicked-dialog-unknown"


def test_断线异常不能炸调用方():
    class _Boom:
        def run_js(self, *args, **kwargs):
            raise RuntimeError("与页面的连接已断开")

    assert BossChatHandler.reject_interview_invite(
        SimpleNamespace(page=_Boom())) == "no-btn"


def test_定位必须靠面试字样而不是card_btn():
    src = inspect.getsource(BossChatHandler.reject_interview_invite)
    assert "btn-outline-v2" in src, "拒绝按钮的类名变了要重新实测"
    assert 'indexOf("面试")' in src, "不按「面试」分家就会点到交换联系方式那张的拒绝"
    assert ".card-btn" not in src, "card-btn 是联系方式/简历卡片，不是面试邀请"


def test_展开卡片只点面试邀请上的立即查看():
    src = inspect.getsource(BossChatHandler.open_interview_invite)
    assert "立即查看" in src and 'indexOf("面试")' in src


def test_回复引擎把现场邀请改判成拒绝动作():
    """路由写死在 reply_engine 的意图分支里：判成 offline 就不该再回
    "工作日下午都可以安排面试"——那等于替 HR 把到场面试应下来。"""
    from boss_bot.reply_engine import ReplyEngine
    src = inspect.getsource(ReplyEngine)
    assert 'classify_interview_invite(latest) == "offline"' in src
    assert '"reject_interview"' in src


class 落账Test:
    """四条路都要留痕：点成、展开后点成、点不到退回发文字、演练。"""

    def _loop(self, *, dry_run=False, verdicts=("clicked",), open_ok=False, send_ok=True):
        from boss_bot.main_loop import OFFLINE_INTERVIEW_DECLINE, UnifiedBotLoop
        lp = UnifiedBotLoop.__new__(UnifiedBotLoop)
        lp.config = SimpleNamespace(dry_run=dry_run, reply=SimpleNamespace())
        lp.records, lp.events, lp.bots, lp.logs = [], [], [], []
        lp.calls = {"reject": 0, "open": 0, "send": 0}
        queue = list(verdicts)

        def _reject():
            lp.calls["reject"] += 1
            return queue.pop(0) if queue else "no-btn"

        def _open():
            lp.calls["open"] += 1
            return open_ok

        def _send(text):
            lp.calls["send"] += 1
            lp.sent = text
            return send_ok

        lp._chat_handler = SimpleNamespace(reject_interview_invite=_reject,
                                           open_interview_invite=_open, send_text=_send)
        lp._reply_engine = SimpleNamespace(
            wait_human_delay=lambda: None, record_reply=lambda: None,
            _add_record=lambda **kw: lp.records.append(kw), _last_ai_model="")
        lp._msg_store = SimpleNamespace(append_bot_message=lambda *a, **k: lp.bots.append(k))
        lp._stats = SimpleNamespace(record_reply=lambda **k: None, dict={})
        lp._stats_dict = {"reply_sent": 0}
        lp._emit_reply_event = lambda **kw: lp.events.append(kw)
        lp._log = lambda level, msg: lp.logs.append((level, msg))
        lp.DECLINE = OFFLINE_INTERVIEW_DECLINE
        return lp

    def _call(self, lp):
        return lp._handle_reply_action(
            "reject_interview", None, {"intent": "invite_interview", "source": "intent"},
            "周杰", "运营助理", "珞壹文化邀请您现场面试，前往查看，确认是否接受",
            chat_company="珞壹文化")

    def test_点成拒绝就记一条不再发文字(self):
        lp = self._loop(verdicts=("clicked",))
        assert self._call(lp) is True
        assert lp.calls["reject"] == 1 and lp.calls["send"] == 0, "点成了还追一句就是重复拒绝"
        rec = lp.records[-1]
        assert rec["reply_content"] == "[已拒绝现场面试邀请]"
        assert rec.get("is_skipped") is None
        assert lp.events[-1]["status"] == "replied"

    def test_第一次点不到就先展开卡片再点(self):
        lp = self._loop(verdicts=("no-btn", "clicked"), open_ok=True)
        assert self._call(lp) is True
        assert lp.calls["open"] == 1 and lp.calls["reject"] == 2
        assert lp.calls["send"] == 0
        assert lp.records[-1]["reply_content"] == "[已拒绝现场面试邀请]"

    def test_展开也点不到才退回发文字拒绝(self):
        lp = self._loop(verdicts=("no-btn", "no-btn"), open_ok=True)
        assert self._call(lp) is True
        assert lp.calls["send"] == 1
        assert "只找线上" in lp.sent
        assert lp.records[-1]["reply_content"] == lp.DECLINE

    def test_文字也发不出去要记跳过(self):
        lp = self._loop(verdicts=("no-btn", "no-btn"), open_ok=False, send_ok=False)
        assert self._call(lp) is True
        assert lp.records[-1]["is_skipped"] is True

    def test_演练模式不点也不发(self):
        lp = self._loop(dry_run=True)
        assert self._call(lp) is False, "演练要返回 False，调用方才不会标成已回复"
        assert lp.calls == {"reject": 0, "open": 0, "send": 0}
