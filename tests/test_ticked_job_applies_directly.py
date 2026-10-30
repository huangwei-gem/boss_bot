# -*- coding: utf-8 -*-
"""「先选再投」：他在候选池里勾了的那条，投递腿不许再判第二遍。

口径（2026-10-29 他的原话）：「既然我是先选再投递，那么这里投递就不需要AI判断了，
只要在候选池里面投递就行了」。现场是他勾了「数据标注/AI训练师」，招呼记录里那一行
却写成「JD 命中否决词「坐班」」被弹了回来 —— 词表判的是"这条岗位要不要"，
可他刚点过名；机器替他推翻他刚做的选择，就是这条要锁死的。

方向相反的两件事：
① 勾了的不再走否决词/方向闸/薄正文这三道本地判定（机械闸门照旧：BOSS 侧已有会话、
   页面断开/风控、抽屉没出来、这一刻这句话发不出去——那些不是"判断该不该投"）；
② 没勾的那一路（关掉只抓不投、机器自己搜到就投）判据一个字不许变。
"""

from types import SimpleNamespace

import pytest

import boss_bot.greet_engine as GE
from boss_bot.greet_engine import GreetEngine, 勾选标记, 勾选投递的

岗位URL = "https://www.zhipin.com/job_detail/abc.html"
坐班JD = "任职要求：每周坐班三天，负责文本与图片标注的质检，服从排班。"
长JD = " ".join(["负责数据清洗、指标口径梳理与看板搭建，配合业务复盘"] * 6)


def _岗位(标题="数据标注/AI训练师", **多):
    基 = {"job_name": 标题, "url": 岗位URL, "company": "某公司",
          "job_description": 坐班JD}
    基.update(多)
    return 基


class _按钮:
    text = "立即沟通"


class _页面:
    """只长 `_apply_job_inner` 走到 JD 闸门那一步真会碰的几样东西。

    `.btn btn-startchat` 一律取不到：闸门放行后它下一步就是这个选择器，
    于是"过了闸门"这件事在返回值里看得见（未找到沟通按钮（点击阶段）），
    而不是靠读源码猜。
    """

    def __init__(self, jd):
        self._jd = jd
        self.url = 岗位URL

    def run_js(self, js):
        return None

    def ele(self, sel, timeout=1):
        if sel == ".job-sec-text":
            return SimpleNamespace(text=self._jd)
        return None


def _裸引擎(monkeypatch, 否决词=(), 方向词=(), jd=坐班JD):
    """不起浏览器、不读配置，只把 JD 闸门之前那几步垫平。"""
    monkeypatch.setattr(GE, "准整页加载", lambda 地址="", 记=None: 0.0)
    eng = GreetEngine.__new__(GreetEngine)
    eng.running = True
    eng._log = lambda *a, **k: None
    eng._drawer_fails = {}
    eng._drawer_announced = set()
    eng._no_drawer_streak = 0
    eng._no_drawer_recent = []
    eng._applied_recent = []
    eng._greet_cooldown_until = 0.0
    eng._greet_cooldown_reason = ""
    eng._retry_max_attempts = 1
    eng._ai_custom_filter_keywords = list(否决词)
    eng._ai_title_veto_keywords = []
    eng._greeting_for = lambda job: ("您好，我对这个岗位很感兴趣", "账号默认")
    eng._节流岗位详情打开 = lambda: 0.0
    eng._落地后判性 = lambda instance: ""
    eng._random_delay = lambda a, b: None
    eng._find_chat_button = lambda timeout=8: _按钮()
    eng.方向词表 = lambda: list(方向词)
    eng.browser_manager = SimpleNamespace(get_instance=lambda: _页面(jd))
    return eng


def _投递(eng, job):
    return eng._apply_job_inner(dict(job))


class 勾了的不再判Test:
    def test_没勾的岗位否决词真拦得住(self, monkeypatch):
        """对照：这条 fixture 确实触发得了否决词，否则下面那条"放行"是空的。"""
        eng = _裸引擎(monkeypatch, 否决词=["坐班"])
        成, 因 = _投递(eng, _岗位())
        assert 成 is False
        assert 因.startswith("JD 命中否决词「坐班」"), 因

    def test_勾了的同一条否决词拦不住(self, monkeypatch):
        eng = _裸引擎(monkeypatch, 否决词=["坐班"])
        成, 因 = _投递(eng, _岗位(**{勾选标记: True}))
        assert "否决词" not in 因, "他点名勾的岗位被词表弹回来了"
        assert 因 == "未找到沟通按钮（点击阶段）", "已过闸门，走到点沟通那一步才停"

    def test_没勾的方向闸拦得住(self, monkeypatch):
        eng = _裸引擎(monkeypatch, 方向词=["数据分析"], jd=长JD)
        成, 因 = _投递(eng, _岗位(标题="线上运营专员", job_description=长JD))
        assert 成 is False and 因.startswith("不是要投的方向"), 因

    def test_勾了的绕过方向闸(self, monkeypatch):
        eng = _裸引擎(monkeypatch, 方向词=["数据分析"], jd=长JD)
        成, 因 = _投递(eng, _岗位(标题="线上运营专员", job_description=长JD,
                              **{勾选标记: True}))
        assert 因.startswith("不是要投的方向") is False, 因

    def test_没勾的薄正文拦得住(self, monkeypatch):
        eng = _裸引擎(monkeypatch, jd="好岗快来")
        成, 因 = _投递(eng, _岗位(标题="数据分析师", job_description="好岗快来"))
        assert 成 is False and "正文仅" in 因, 因

    def test_勾了的薄正文照样往下走(self, monkeypatch):
        """这条岗位正文只写了六个字——池里那条的 AI 结论可以很差，
        但他勾了就是看过标题了，不该由机器再说"信息不够"。"""
        eng = _裸引擎(monkeypatch, jd="好岗快来")
        成, 因 = _投递(eng, _岗位(标题="数据分析师", job_description="好岗快来",
                              **{勾选标记: True}))
        assert "正文仅" not in 因, 因

    def test_勾选标记只在勾的那一条上(self, monkeypatch):
        """标记从 job 里读，不是全局开关：同一条腿里没标记的那份照旧判。"""
        eng = _裸引擎(monkeypatch, 否决词=["坐班"])
        assert 勾选投递的(_岗位(**{勾选标记: True})) is True
        assert 勾选投递的(_岗位()) is False
        assert 勾选投递的(None) is False
        assert 勾选投递的("x") is False


class 机械闸门照旧Test:
    """放行的是"该不该投"这一问，不是"投得投不出去"。这几道一道都不许跟着松。"""

    def test_抽屉连败过的岗位仍旧不点(self, monkeypatch):
        eng = _裸引擎(monkeypatch, 否决词=["坐班"])
        eng._drawer_fails[岗位URL] = GE.NO_DRAWER_GIVE_UP
        成, 因 = _投递(eng, _岗位(**{勾选标记: True}))
        assert 成 is False
        assert "否决词" not in 因
        assert 因 != "未找到沟通按钮（点击阶段）", "连败这道门不能被勾绕过"

    def test_招呼语发不出去仍旧不点(self, monkeypatch):
        eng = _裸引擎(monkeypatch, 否决词=["坐班"])
        eng._greeting_for = lambda job: ("", "话术不可用：报了学历档次「双一流」")
        成, 因 = _投递(eng, _岗位(**{勾选标记: True}))
        assert 成 is False and 因.startswith("话术不可用"), 因

    def test_冷却中仍旧不碰浏览器(self, monkeypatch):
        eng = _裸引擎(monkeypatch, 否决词=["坐班"])
        eng._greet_cooldown_until = __import__("time").time() + 600
        碰过 = []
        eng.browser_manager = SimpleNamespace(
            get_instance=lambda: 碰过.append(1) or _页面(坐班JD))
        成, 因 = _投递(eng, _岗位(**{勾选标记: True}))
        assert 成 is False and "冷却" in 因
        assert 碰过 == [], "冷却里还去取实例就是白烧一趟导航"

    def test_按钮文案是继续沟通就是已经投过(self, monkeypatch):
        eng = _裸引擎(monkeypatch, 否决词=["坐班"])
        eng._find_chat_button = lambda timeout=8: SimpleNamespace(text="继续沟通")
        成, 因 = _投递(eng, _岗位(**{勾选标记: True}))
        assert 成 is False and "之前已投递过" in 因, 因
