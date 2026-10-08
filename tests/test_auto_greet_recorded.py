# -*- coding: utf-8 -*-
"""第二种打招呼机制的结论要落成记录、进界面。

用户报的第 4 条："BOSS 平台已经完成投递，但前端打招呼记录响应延迟、不实时更新"。
发送即落库那一半早就修好了（`_record_sent_now`），剩下这一半是**归因**：
平台弹「已向BOSS发送消息」自动把招呼发出去之后，我们没能进会话核对文案时，
以前 `return False, AUTO_GREET_REASON` —— 记成失败。于是 BOSS 上明明投了，
界面里那条要么是红的、要么等外层补记完才出现，看起来就是"记录跟不上"。
而当时写好的 `job["_auto_greet_note"]` 全仓库只赋值、没有任何消费点，
结论只活在日志文件里。
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.reply_record import MAX_AI_PROBE_LEN, GreetRecord


class RecordFieldTest:
    def test_note_随记录一起落盘读回(self):
        r = GreetRecord(job_name="数据分析师", company="某公司", is_greeted=True,
                        auto_greet_note="平台已自动发出招呼语，未能进会话核对文案")
        d = r.to_dict()
        assert d["auto_greet_note"] == "平台已自动发出招呼语，未能进会话核对文案"
        back = GreetRecord.from_dict(d)
        assert back.auto_greet_note == r.auto_greet_note

    def test_没有留痕时不塞空字段(self):
        d = GreetRecord(job_name="a", company="b", is_greeted=True).to_dict()
        assert d["auto_greet_note"] is None, d["auto_greet_note"]

    def test_超长留痕要截断(self):
        """记录文件会被人导出看，一句几万字的弹窗文本不该拖垮整份 JSON"""
        r = GreetRecord(job_name="a", company="b", auto_greet_note="洞" * 5000)
        assert len(r.auto_greet_note) <= MAX_AI_PROBE_LEN


class GreetEnginePlumbingTest:
    def _engine(self):
        from boss_bot.greet_engine import GreetEngine
        from boss_bot.reply_record import _get_greet_store
        eng = GreetEngine.__new__(GreetEngine)
        eng._log = lambda *a, **k: None
        eng.account_index = 0
        eng._greet_store = _get_greet_store()
        # _record_greet 会把这些"最后一次调用"的字段一并写进记录，缺一个就会被
        # 它自己的 try/except 吞成一句 WARN，测试看到的是"记录没落库"
        eng._last_ai_result = {}
        eng._last_ai_duration_ms = 0
        eng._last_ai_system_prompt = ""
        eng._last_ai_user_prompt = ""
        eng._last_ai_model = ""
        eng._last_ai_raw_response = ""
        eng._account_label = lambda: "主账号"
        eng._greeting_for = lambda job: ("本号招呼语", "账号自定义")
        eng.events = []
        eng._emit_greet_event = lambda job, status, **kw: eng.events.append((status, dict(job)))
        return eng

    def test_留痕写进记录也写进推送(self):
        """只赋值没人读 = 结论只活在日志里，这是这条 bug 的原形"""
        from boss_bot.reply_record import _get_greet_store
        eng = self._engine()
        job = {"job_name": "数据分析师", "url": "https://x/job_detail/1.html",
               "_auto_greet_note": "平台已自动发出招呼语，未能进会话核对文案（建议抽查）"}
        eng._record_greet(job, is_greeted=True)
        rows = [r for r in _get_greet_store().get_all()
                if (r.get("job_url") if isinstance(r, dict) else r.job_url) == job["url"]]
        assert rows, "记录没落库"
        got = rows[0].auto_greet_note if not isinstance(rows[0], dict) \
            else rows[0]["auto_greet_note"]
        assert got == job["_auto_greet_note"]


class UnverifiedDialogCountsAsSentTest:
    def test_弹窗未核对那一支记成功并当场落库(self):
        # 这段结算从 _apply_job_inner 抽到了 _auto_greet_path，锁的还是同一件事：
        # 平台已经替我们把招呼发出去了，就不能记失败，而且要点完立刻落库
        src = inspect.getsource(
            __import__("boss_bot.greet_engine", fromlist=["GreetEngine"]).GreetEngine
            ._auto_greet_path)
        i = src.index("job[\"_auto_greet_note\"] = (")
        seg = src[i:i + 260]
        assert "_record_sent_now(job)" in seg, "没当场落库 → 界面就是要慢一拍"
        assert 'return True, ""' in seg, "记成失败就会出现「BOSS 上投了、记录里是红的」"
        assert "return False, AUTO_GREET_REASON" not in seg


class FrontendTest:
    def setup_method(self):
        self.html = (ROOT / "flask-version" / "templates" / "index.html").read_text(
            encoding="utf-8")

    def test_行模型带上留痕(self):
        """实时推送与历史快照都过 toGreetRow，不带字段就渲染不出来"""
        assert "auto_greet_note: data.auto_greet_note" in self.html

    def test_成功行也显示留痕(self):
        html = self.html
        i = html.index("if (data.auto_greet_note) {")
        j = html.index("function toGreetRow")
        assert i < j, "渲染点必须在 buildGreetRowHtml 里，不能挂在推送函数上"
        assert "data.skip_reason" in html[:i], "要接在原因列后面，位置才对得上"
