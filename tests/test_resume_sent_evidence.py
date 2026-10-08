# -*- coding: utf-8 -*-
"""发简历必须拿到"真发出去了"的证据才算成功。

用户 2026-10-08：「现在发简历的功能好像缺失了，你看一下你现在的功能我看好多面试官
说要简历，你没给。」台账现算欠简历 57 单（号0 32 / 号1 25）。

判错过两次，方向相反，都在这份文件里留着：

1. 旧 JS 在消息列表最后三条里找"简历"两个字就算送达——HR 索要简历的卡片正文里
   就带"简历"，BOSS 那句"附件简历大小超出限制，最多发送2000K的附件"也照样在页面上，
   于是每次都判成功、写 [简历已发送]、这一单不再发，实际一份都没出去。
2. 于是改成"只扫 .message-item.item-myself（我方气泡）"。2026-10-08 13:47 和 13:59
   两次真发简历，弹层点了确认、日志"发送简历结果: sent:…"，回来一律报
   "简历送达验证超时：没看到我方简历条目"。翻存档数：带附件卡片字样的消息 157 条，
   is_mine 全为 False、is_system 全为 True（采集器按 className 含 item-system 判的）
   ——那张卡片在 BOSS 里根本不是气泡，是居中系统卡片。只扫我方气泡等于永远判失败，
   好简历白送出去还被机器报成没发。

所以现在的判据是：扫全部 .message-item，点确认前先数一遍卡片条数当基线，
点完要求"卡片条数变多"才算送达——既不看方向，也不拿上一单的旧卡片冒充这一单。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.page_handler import RESUME_DELIVERY_MARKS, judge_resume_sent  # noqa: E402

# BOSS 送达后的样子：会话里多出一条居中卡片（不是我方气泡）
NEW_CARD = {"card_total": 1, "toast": "", "dialog_visible": False}


class 送达判据Test:
    def test_卡片比点之前多了一条才算送达(self):
        ok, why = judge_resume_sent(NEW_CARD, baseline=0)
        assert ok is True and why == ""

    def test_原来就有的卡片不算这一单发出去了(self):
        """这一单点确认前会话里就有 1 条（上一单留的），点完还是 1 条＝没多出来"""
        ok, why = judge_resume_sent({"card_total": 1, "toast": "",
                                     "dialog_visible": False}, baseline=1)
        assert ok is False
        assert "卡片" in why

    def test_弹层关了但一条卡片都没有不算送达(self):
        """这是第 1 个坑：HR 索要简历的那张卡片里也含"简历"两个字"""
        ok, why = judge_resume_sent({"card_total": 0, "toast": "",
                                     "dialog_visible": False}, baseline=0)
        assert ok is False and "卡片" in why

    def test_尺寸超限的提示要当成失败原因(self):
        ok, why = judge_resume_sent({"card_total": 0, "dialog_visible": False,
                                     "toast": "发送职位失败，附件简历大小超出限制，"
                                              "最多发送2000K的附件"}, baseline=0)
        assert ok is False and "2000K" in why

    def test_超限提示压过卡片(self):
        """页面既报了超限又留着上一单的旧卡片时，不能判成功"""
        ok, why = judge_resume_sent({"card_total": 3, "dialog_visible": False,
                                     "toast": "发送职位失败，附件简历大小超出限制，"
                                              "最多发送2000K的附件"}, baseline=0)
        assert ok is False and "2000K" in why

    def test_弹层还挂着就是没点成发送(self):
        ok, why = judge_resume_sent({"card_total": 0, "toast": "",
                                     "dialog_visible": True}, baseline=0)
        assert ok is False and "弹层" in why

    def test_送达标记必须是附件条目自己的字样(self):
        """只认"简历"会把 HR 的索要卡片读成送达，这两类词必须分开"""
        assert "点击预览附件简历" in RESUME_DELIVERY_MARKS
        assert "已发送给Boss" in RESUME_DELIVERY_MARKS
        assert "简历" not in RESUME_DELIVERY_MARKS


class 接线Test:
    def test_探针扫全部气泡不许锁死我方(self):
        """实测 157 条卡片全是 is_system=True，扫 item-myself 会一条也碰不到"""
        from boss_bot.page_handler import RESUME_SENT_PROBE_JS
        assert 'querySelectorAll(".message-item")' in RESUME_SENT_PROBE_JS
        assert "item-myself" not in RESUME_SENT_PROBE_JS
        assert "card_total" in RESUME_SENT_PROBE_JS
        assert "超出限制" in RESUME_SENT_PROBE_JS

    def test_送达判定只有一份(self):
        import inspect
        from boss_bot.page_handler import BossChatHandler
        验证 = inspect.getsource(BossChatHandler._verify_resume_sent)
        assert "judge_resume_sent" in 验证, "判据散在 JS 和 Python 两处会漂"
        发送 = inspect.getsource(BossChatHandler.send_resume)
        assert "_verify_resume_sent(" in 发送, "点完发送不核对，就又是假报[简历已发送]"

    def test_点确认前先取卡片基线(self):
        """不取基线就只能二选一：要么拿旧卡片冒充成功，要么把好送的报成失败"""
        import inspect
        from boss_bot.page_handler import BossChatHandler
        发送 = inspect.getsource(BossChatHandler.send_resume)
        assert "card_total" in 发送
        assert "baseline" in 发送

    def test_面板把2000K上限讲清(self):
        html = (ROOT / "flask-version" / "templates" / "index.html").read_text(
            encoding="utf-8")
        assert "2000K" in html, "欠简历的 57 单为什么发不出去，界面要自己说清楚"
