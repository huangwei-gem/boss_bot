# -*- coding: utf-8 -*-
"""同一个岗位点了「立即沟通」两次都不出抽屉，就别每轮再点第三遍。

线上证据（2026-10-08，logs/boss_bot.log + data/greet_records.json）：
今天 34 次「点了「立即沟通」但聊天抽屉没在这个标签页里出现」，只落在 8 个岗位
URL 上，同一个 URL 最多吃了 6 次：
  65eab266… 07:42 / 10:17 / 11:29 / 14:01 / 16:00 全是同一条原因
  8e0d4756… 15:55 / 16:44 / 17:23
这 13 条记录一条都没成功过（status 全是 skipped），同期账号自己投成了 178 单，
所以不是当日额度（额度门是另一条：连败 3 次进 30 分钟冷却）；
也不是第二种打招呼机制（今天「已向BOSS发送消息」弹窗 0 次）；
17 次"现场"行里没有一次带页面提示（toast 全空）。
结论：BOSS 压根没把这些点击记成沟通，而我们每轮还去点它——
每次白烧一趟「导航→读 JD→AI 判分→点沟通」，日志里全是同一条 WARN。

所以按岗位 URL 计次：连着 NO_DRAWER_GIVE_UP 次不出抽屉，这个岗位先放一放，
判在点沟通之前，也不再花 AI 判分；投成功过就清零（万一是当天临时抽风）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boss_bot.greet_engine as ge  # noqa: E402
from boss_bot.greet_engine import GreetEngine  # noqa: E402

URL_A = "https://www.zhipin.com/job_detail/65eab266b66997460nV42NS1GFZW.html"
URL_B = "https://www.zhipin.com/job_detail/8e0d4756fd26f01603V429W5ElpT.html"


def engine():
    e = GreetEngine(MagicMock(), MagicMock(), account_index=0)
    e.logs = []
    e._log = lambda level, msg: e.logs.append(f"[{level}] {msg}")
    return e


def job(url=URL_A, name="线上运营"):
    return {"url": url, "job_name": name, "company": "某公司"}


class 计数Test:
    def test_连败两次就算放弃(self):
        e = engine()
        e._note_drawer_fail(URL_A)
        assert e._drawer_given_up(URL_A) is False, "只败过一次，还要再给它一次机会"
        e._note_drawer_fail(URL_A)
        assert e._drawer_given_up(URL_A) is True

    def test_按URL分开算不算岗位名(self):
        """BOSS 岗位名重复（"数据分析师"两个号各挂几个），只认链接"""
        e = engine()
        e._note_drawer_fail(URL_A)
        e._note_drawer_fail(URL_A)
        assert e._drawer_given_up(URL_B) is False

    def test_投成功过一次就清零(self):
        e = engine()
        e._note_drawer_fail(URL_A)
        e._note_drawer_fail(URL_A)
        e._drawer_fails_ok(URL_A)
        assert e._drawer_given_up(URL_A) is False

    def test_空链接不参与计数(self):
        e = engine()
        e._note_drawer_fail("")
        assert e._drawer_given_up("") is False


class 放弃的岗位不再走整套流程Test:
    def _fake_apply(self, e, url=URL_A):
        """把 _apply_job 里所有会碰浏览器的步骤换成探针，看它到底有没有提前收手"""
        e.running = True
        calls = []
        e._greeting_for = lambda j: calls.append("招呼语") or ("你好，我对这个岗位感兴趣", "账号默认")
        e.browser_manager.get_instance = lambda: calls.append("取页面") or SimpleNamespace(
            url="https://www.zhipin.com/web/geek/job-search")
        e._find_chat_button = lambda timeout=8: calls.append("点沟通") or None
        e._note_drawer_fail = lambda u: calls.append("记失败")
        return calls

    def test_黑名单命中就直接返回不点沟通(self):
        e = engine()
        e._drawer_fails[URL_A] = ge.NO_DRAWER_GIVE_UP
        calls = self._fake_apply(e)
        ok, reason = e._apply_job(job())
        assert ok is False
        assert "点沟通" not in calls, "还在每轮白点一次"
        assert "取页面" not in calls, "连页面都不该去取"
        assert "招呼语" not in calls and "记失败" not in calls

    def test_原因串要说清是第几次放弃的(self):
        e = engine()
        e.running = True
        e._drawer_fails[URL_A] = ge.NO_DRAWER_GIVE_UP
        _ok, reason = e._apply_job(job())
        assert str(ge.NO_DRAWER_GIVE_UP) in reason
        assert "抽屉" in reason, f"要能照着这条改判据: {reason}"
        assert "没把这次沟通记下来" in reason, f"得说清不是额度问题: {reason}"

    def test_没进黑名单的照常走(self):
        """第一次试总得试：门只拦连败到位的那一个 URL"""
        e = engine()
        calls = self._fake_apply(e)
        _ok, reason = e._apply_job(job())
        assert "先放一放" not in reason, reason
        assert "取页面" in calls, "没进黑名单的岗位照旧要去看页面"


class 现场证据Test:
    def test_抽屉没出现时要报出沟通按钮长什么样(self):
        """判"没出抽屉"时页面还剩什么，是分不清"额度/风控/这个岗根本不能沟通"的关键。
        探针早就抓了按钮文本+类名，只是没念出来。"""
        src = (ROOT / "boss_bot" / "greet_engine.py").read_text(encoding="utf-8")
        at = src.index('f"  现场 url={')
        现场行 = src[at:at + 400]
        assert "按钮=" in 现场行, "现场行要把 snap 里的 buttons 一起打出来"
        assert "抽屉元素=" in 现场行 and "可见输入框=" in 现场行


class 界面不许被同一句挤满Test:
    def test_先放一放只报第一次(self):
        e = engine()
        e._drawer_fails[URL_A] = ge.NO_DRAWER_GIVE_UP
        assert e._announce_giveup(URL_A) is True
        assert e._announce_giveup(URL_A) is False, "每轮念一遍，界面里全是重复行"

    def test_投出去过就允许重新报一次(self):
        e = engine()
        e._drawer_fails[URL_A] = ge.NO_DRAWER_GIVE_UP
        e._announce_giveup(URL_A)
        e._drawer_fails_ok(URL_A)
        assert e._announce_giveup(URL_A) is True

    def test_选岗位那一圈就把它拦下不烧判分(self):
        """AI 判分排在 send_greeting 之前，闸门只放 _apply_job 里等于白付一次预算"""
        from boss_bot.main_loop import UnifiedBotLoop
        import inspect
        源 = inspect.getsource(UnifiedBotLoop._run_greet_round)
        at = 源.index("_drawer_given_up")
        assert "_analyze_job_with_ai" not in 源[:at], "闸门要在 AI 判分之前"
        assert "_record_greet_skip" not in 源[at:at + 400], \
            "连败的岗位每轮写一条记录会把界面和记录数撑爆"

