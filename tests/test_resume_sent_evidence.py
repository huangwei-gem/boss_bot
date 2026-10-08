# -*- coding: utf-8 -*-
"""发简历必须拿到"真发出去了"的证据才算成功。

用户 2026-10-08：「现在发简历的功能好像缺失了，你看一下你现在的功能我看好多面试官
说要简历，你没给。」台账现算欠简历 69 单（号0 37 / 号1 32）。

根因是送达判据太松：旧 JS 在消息列表最后三条里找"简历"两个字就算 delivered。
可 HR 索要简历的那张卡片正文里就带"简历"（「方便发一份简历过来吗」/
「求附件简历」卡片），BOSS 那句"附件简历大小超出限制，最多发送2000K的附件"
也照样在页面上——于是每次都判成功、写 [简历已发送]、`mark_resume_sent` 记下
这一单不再发，实际一份都没出去。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.page_handler import RESUME_DELIVERY_MARKS, judge_resume_sent  # noqa: E402

# BOSS 的"发送"证据只有这一种形状：我方气泡里出现简历条目
MINE_CARD = {"mine_card": True, "toast": "", "dialog_visible": False}


class 送达判据Test:
    def test_我方简历条目出现才算送达(self):
        ok, why = judge_resume_sent(MINE_CARD)
        assert ok is True and why == ""

    def test_弹层关了但没我方简历条目不算送达(self):
        """旧判据在这里判成功：HR 索要简历的卡片里也含"简历"两个字"""
        ok, why = judge_resume_sent({"mine_card": False, "toast": "",
                                     "dialog_visible": False})
        assert ok is False
        assert "简历" in why

    def test_尺寸超限的提示要当成失败原因(self):
        ok, why = judge_resume_sent({"mine_card": False, "dialog_visible": False,
                                     "toast": "发送职位失败，附件简历大小超出限制，"
                                              "最多发送2000K的附件"})
        assert ok is False and "2000K" in why

    def test_弹层还挂着就是没发出去(self):
        ok, why = judge_resume_sent({"mine_card": False, "toast": "", "dialog_visible": True})
        assert ok is False and "弹层" in why

    def test_送达标记必须是附件条目自己的字样(self):
        """只认"简历"会把 HR 的索要卡片读成送达，这两类词必须分开"""
        assert "点击预览附件简历" in RESUME_DELIVERY_MARKS
        assert "简历" not in RESUME_DELIVERY_MARKS


class 接线Test:
    def test_探针只看我方气泡(self):
        from boss_bot.page_handler import RESUME_SENT_PROBE_JS
        assert "item-myself" in RESUME_SENT_PROBE_JS
        assert "超出限制" in RESUME_SENT_PROBE_JS

    def test_送达判定只有一份(self):
        import inspect
        from boss_bot.page_handler import BossChatHandler
        验证 = inspect.getsource(BossChatHandler._verify_resume_sent)
        assert "judge_resume_sent" in 验证, "判据散在 JS 和 Python 两处会漂"
        发送 = inspect.getsource(BossChatHandler.send_resume)
        assert "_verify_resume_sent()" in 发送, "点完发送不核对，就又是假报[简历已发送]"

    def test_面板把2000K上限讲清(self):
        html = (ROOT / "flask-version" / "templates" / "index.html").read_text(
            encoding="utf-8")
        assert "2000K" in html, "欠简历的 69 单为什么发不出去，界面要自己说清楚"
