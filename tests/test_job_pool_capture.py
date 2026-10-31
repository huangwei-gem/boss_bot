# -*- coding: utf-8 -*-
"""抓取腿"只抓不投 + 进池即精筛"与勾选投递的行为锁。

口径（2026-10-19 他定的）：先抓岗位进池，AI 把池里的筛一遍，投哪条由他在界面勾。
所以这里要钉死的是三件相反方向的事：
① 开着只抓不投时**一条都不许点沟通**（点了就是替他决定投什么）；
② 关掉之后**旧行为要原样还在**（本地还得能跑，见 README 启动那一节）；
③ 勾了的必须真投出去，而且是由打招呼线程投 —— 不是 HTTP 线程当场点页面。
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from boss_bot.job_pool import (状态不投, 状态已投, 状态已选, 状态待选, 岗位池, 行id,
                               是可重试的失败)
from boss_bot.main_loop import UnifiedBotLoop
import boss_bot.unified_config as UC


@pytest.fixture(autouse=True)
def 去重库为空(monkeypatch):
    """抓取腿那一步要查"BOSS 侧是不是已经有这条会话"，查的是线上 `data/chatted_jobs.json`。

    不顶掉的话这些测试就跟着那 2028 条真 URL 变绿变红：线上多一条 fixture 撞得上的 URL，
    岗位会"莫名其妙没进池"，而没人看得出为什么。统一顶成一份可写的空集合，
    谁要测"拦得住"谁自己往里塞身份。
    """
    库 = set()
    monkeypatch.setattr("boss_bot.greet_engine.沟通身份集",
                        lambda urls=None: set(库), raising=True)
    monkeypatch.setattr("boss_bot.greet_engine.读去重库", lambda: set(), raising=True)
    return 库


def _默认引擎():
    from boss_bot.unified_config import TITLE_VETO_KEYWORDS_DEFAULT
    引擎 = MagicMock()
    # 引擎是替身，词表必须是真表：留 MagicMock 的话 veto 命中变成"永远命中"，
    # 粗筛那条断言就成了自证，抓不出真回归
    引擎.custom_filter_keywords = []
    引擎.title_veto_keywords = list(TITLE_VETO_KEYWORDS_DEFAULT)
    引擎.方向词表 = lambda: ["数据分析"]
    引擎.greet_cooldown_left.return_value = 0
    return 引擎


class _光引擎:
    """只长真 GreetEngine 真有的那几只方法——一个词表属性都不给。

    替身要是一应俱全，"读错地方"这类 bug 就永远测不出来：
    `custom_filter_keywords` / `title_veto_keywords` 这两个名字根本不在
    GreetEngine 上（它们在 AIAnalyzerChain 上），真引擎在这里就是取不到。
    """

    def __init__(self):
        self.search_jobs = MagicMock(return_value=[])
        self.send_greeting = MagicMock()
        self._analyze_job_with_ai = MagicMock(return_value=(None, 0))
        self.greet_cooldown_left = MagicMock(return_value=0)
        self.greet_cooldown_reason = MagicMock(return_value="")

    def 方向词表(self):
        return ["数据分析"]


def _loop(tmp_path, 引擎=None, 目录=None):
    with patch("boss_bot.main_loop.BrowserManager"):
        loop = UnifiedBotLoop(config=UC.UnifiedConfig())
    loop._log = lambda *a, **k: None
    loop._greet_engine = 引擎 or _默认引擎()
    loop._pool = 岗位池(account_index=0, 目录=str(目录 or tmp_path))
    loop._running = True
    loop._greet_paused = False
    # 真 metrics 会写 data/metrics.json —— 测试不许碰他账号的累计数
    loop._metrics = MagicMock()
    loop._dry_run = lambda *a, **k: False
    return loop


def 岗位(n, 标题="数据分析师", **多):
    基 = {"job_name": 标题, "url": "https://www.zhipin.com/job_detail/%d.html" % n,
          "company": "公司%d" % n, "salary": "8-10K", "city": "长沙"}
    基.update(多)
    return 基


class 抓取Test:
    def test_只抓不投时一条都不点沟通(self, tmp_path):
        loop = _loop(tmp_path)
        loop._hot_reload_config = lambda: None   # 不顶住热重载，下面这个开关会被盘上那份盖掉
        loop.config.greet.capture_only = True
        loop._greet_engine.search_jobs.return_value = [岗位(1), 岗位(2)]
        loop._build_greet_tasks = lambda: [{"query": "数据分析", "city": "长沙",
                                           "scroll_pages": 1}]
        loop._run_greet_round()
        loop._greet_engine.send_greeting.assert_not_called()
        assert loop._pool.统计()["总数"] == 2
        assert loop._pool.统计()["new"] == 2, "抓来的都该是待选，等他勾"

    def test_关掉只抓不投后旧投递路径还在(self, tmp_path):
        loop = _loop(tmp_path)
        loop._hot_reload_config = lambda: None
        loop.config.greet.capture_only = False
        loop._greet_engine.search_jobs.return_value = [岗位(1)]
        loop._build_greet_tasks = lambda: [{"query": "数据分析", "city": "长沙",
                                           "scroll_pages": 1}]
        loop._capture_jobs = lambda jobs: pytest.fail("关掉后不该走抓取腿")
        loop._run_greet_round()
        assert loop._pool.统计()["总数"] == 0

    def test_粗筛只贴标签不删人(self, tmp_path):
        """命中否决词的岗位照样进池，只是带上标签。

        删了就等于回到"他看不见为什么没投"，而这一层存在的全部理由就是让他看得见、
        并且能反过来勾"这条我偏要"。
        """
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1, 标题="快递分拣员"), 岗位(2, 标题="数据分析师")])
        行s = {r["job_name"]: r for r in loop._pool.列表()}
        assert "快递分拣员" in 行s, "被否决词命中的也该留在池里"
        assert 行s["快递分拣员"]["gate"].get("否决命中"), "要带上是哪条词命中的"
        assert 行s["数据分析师"]["gate"].get("方向命中") is True

    def test_词表从配置拿_引擎上根本没那两个名字(self, tmp_path):
        """粗筛读的必须是现盘配置，不是引擎属性。

        `custom_filter_keywords` / `title_veto_keywords` 住在 AIAnalyzerChain 上，
        GreetEngine 只有带前缀的 `_ai_*`。以前这里写 `getattr(引擎, "custom_filter_keywords", None) or []`，
        `or []` 把"属性不存在"悄悄兜成"表是空的"，于是 1623 条池子里 `否决命中`
        一条都没有（2026-10-21 实测：号0 722 条 / 号1 901 条全为 0），而标题写着
        「兼职在线打字客服」「5K居家线上客服」的 16 条按现盘词表能命中 14 条。
        测试原来用 MagicMock 当引擎，`getattr` 什么都取得到，所以这条一直在自证。
        """
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.custom_filter_keywords = ["打字"]
        loop.config.ai.title_veto_keywords = ["客服"]
        标 = loop._粗筛标记(岗位(1, 标题="兼职在线打字客服"))
        assert 标.get("否决命中"), "配置里的否决词必须真能命中"

    def test_词表两份都取不到要留下痕迹(self, tmp_path):
        """"一条都没命中"和"没词表可查"在界面上不能长成同一个样子。

        这条就是那个静默 bug 的显式出口：读不到就写 `词表没读到`，
        不许留一个空 gate 让他以为这岗位干干净净没碰过闸。
        """
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.custom_filter_keywords = []
        loop.config.ai.title_veto_keywords = []
        # 10-22 之后"内存空表"会先回读盘上那一份，所以这条要的是"盘上也真没有"
        with patch("boss_bot.unified_config.load_config", return_value={"ai": {}}):
            标 = loop._粗筛标记(岗位(1, 标题="兼职在线打字客服"))
        assert 标.get("词表没读到") is True
        assert not 标.get("否决命中")

    def test_改过词表后池里的旧行重贴标(self, tmp_path):
        """他改一次否决词，已经抓进池的 1600 条要跟着变，不然"自定义筛选"明天又是死的。

        只有新增行在抓取时贴标 = 词表一改，旧行永远停在改之前的结论上；他改完刷新
        界面看见的还是那 722 条老标签，跟没改一样。
        """
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.custom_filter_keywords = []
        loop.config.ai.title_veto_keywords = ["主播"]
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1, 标题="直播主播"), 岗位(2, 标题="数据分析师")])
        行s = {r["job_name"]: r for r in loop._pool.列表()}
        assert 行s["直播主播"]["gate"].get("否决命中")

        loop.config.ai.title_veto_keywords = ["客服"]
        loop._capture_jobs([])
        行s = {r["job_name"]: r for r in loop._pool.列表()}
        assert not 行s["直播主播"]["gate"].get("否决命中"), "旧行那条词命中要跟着撤掉"
        loop._capture_jobs([岗位(3, 标题="居家线上客服")])
        行s = {r["job_name"]: r for r in loop._pool.列表()}
        assert 行s["居家线上客服"]["gate"].get("否决命中")

    def test_内存标题表被读空要回读盘上那份(self, tmp_path):
        """2026-10-22 一致性门抓到两行：「一对一线上高薪家教」「【线上办公】带背单词英语老师」
        盘上 gate 只有 `方向命中`+`依据`，既没 `否决命中` 也没 `词表没读到`。

        这个形状只有一种来路：自定义非空、标题表为空——面板热重载赶上 bot_config 的
        半截 ai 段，内存里 `title_veto_keywords` 成了 []，而当时 [] 被当成"他清空了"
        静默放行，蓝领/老师/家教这一整遍标题词就一条都没参与。空表不能替他说口径，
        先回读盘上那一份；盘上也是空才算他清空，并且要把这一格亮出来。
        """
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.custom_filter_keywords = ["推广"]
        loop.config.ai.title_veto_keywords = []
        with patch("boss_bot.unified_config.load_config",
                   return_value={"ai": {"title_veto_keywords": ["家教", "老师"]}}):
            标 = loop._粗筛标记(岗位(1, 标题="一对一线上高薪家教"))
        assert 标.get("否决命中") == "家教", 标

    def test_盘上也是空表就明确亮出空表(self, tmp_path):
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.custom_filter_keywords = ["推广"]
        loop.config.ai.title_veto_keywords = []
        with patch("boss_bot.unified_config.load_config", return_value={"ai": {}}):
            标 = loop._粗筛标记(岗位(1, 标题="一对一线上高薪家教"))
        assert 标.get("标题词表为空") is True, 标
        assert not 标.get("否决命中")

    def test_重贴每轮都跑不等到词表变(self, tmp_path):
        """词表没变也要重贴：一次抖动写歪的标签不能永远挂着。

        以前这里只在"词表键变了"那一轮动手，代价是任何一次读到坏表留下的旧行
        就再也没人修——10-22 那两行正是这样。整池重算实测 0.12 秒/917 行，
        一轮抓取远不止这个数，省这 0.12 秒不值两条错标签。
        """
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.custom_filter_keywords = []
        loop.config.ai.title_veto_keywords = ["主播"]
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1, 标题="直播主播")])
        行s = loop._pool._读()
        行s[0]["gate"] = {"方向命中": False, "依据": "卡片(未开JD)"}   # 模拟那次抖动写下的形状
        loop._pool._写(行s)
        loop._capture_jobs([])
        行 = loop._pool.列表()[0]
        assert 行["gate"].get("否决命中") == "主播", 行["gate"]

    def test_精筛按限额剩下的下轮判(self, tmp_path):
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 2
        loop._greet_engine._analyze_job_with_ai.return_value = (
            {"score": 82, "is_match": True, "reason": "对得上", "model": "deepseek"}, 1.0)
        loop._capture_jobs([岗位(n) for n in range(1, 6)])
        判过 = [r for r in loop._pool.列表() if (r.get("ai") or {}).get("判于")]
        assert len(判过) == 2, "一轮最多判 2 条，别把回复腿饿死"
        assert 判过[0]["ai"]["分"] == 82
        assert loop._greet_engine._analyze_job_with_ai.call_count == 2

    def test_接口全挂不留假分(self, tmp_path):
        """AI 一个接口都没轮到试时返回 None —— 那不是"判了不合"。

        写成 0 分会让这条再也不会被补判，池子里就永远躺着几条被误判成 0 的岗位。
        """
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 5
        loop._greet_engine._analyze_job_with_ai.return_value = (None, 0)
        loop._capture_jobs([岗位(1)])
        行 = loop._pool.列表()[0]
        assert not (行.get("ai") or {}).get("判于"), "没判成不许留时间戳"
        loop._greet_engine._analyze_job_with_ai.return_value = (
            {"score": 71, "is_match": True, "reason": "", "model": "x"}, 1.0)
        loop._capture_jobs([岗位(1)])
        assert loop._pool.列表()[0]["ai"]["分"] == 71, "下一轮要能自己补判上"

    def test_限额设0就是关掉精筛(self, tmp_path):
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1), 岗位(2)])
        loop._greet_engine._analyze_job_with_ai.assert_not_called()


class 判分落池Test:
    """AI 判完的三种结局要落成三种样子——2026-10-24 现场抓到的错全在这一层。

    真 `_analyze_job_with_ai` 在"判了但不合"时也返回 None（打招呼腿靠这个不投），
    但它先把结果绑进 `job["_ai_result"]`。精筛以前只看返回值是不是 None，
    于是把"AI 判了 0 分（命中否决词主播）"读成"一个接口都没轮到试"：
    11:11:18 一秒里八条 `🧪 精筛 xx: 暂无可用 AI 接口，留给下一轮` 前面都跟着一条
    `🤖 AI 匹配度: 0/100 (0.0s) —— 命中否决词…`。代价是这三类岗位永远拿不到结论，
    每轮从 710 条"待筛"里被重挑出来再判一遍，`screen_max_per_round` 的 40 个名额
    全烧在旧岗位上，新岗位反而排不上（10-23 全天 13348 条这句，池子一直没动）。
    """

    @staticmethod
    def _引擎替身(结果, 判了=True, 没接口=False):
        """照真引擎的绑法复现一次调用现场：结果绑进 job，返回值另说。"""

        def 分析(job):
            if 判了:
                job["_ai_result"] = dict(结果)
                job["_ai_meta"] = {"model": "agnes-2.5-flash"}
            if 没接口:
                job["_ai_no_candidate"] = True
            return (结果, 1.0) if 结果.get("is_match") and 结果.get("score", 0) >= 70 else (None, 1.0)

        return 分析

    def _接进光引擎(self, loop, 分析):
        loop._greet_engine._analyze_job_with_ai.side_effect = 分析
        return loop

    def test_AI判了不合要把结论落进池子(self, tmp_path):
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.screen_max_per_round = 5
        self._接进光引擎(loop, self._引擎替身(
            {"score": 0, "is_match": False, "veto_hit": "主播",
             "reason": "命中否决词「主播」（代码直查 JD）"}))
        loop._capture_jobs([岗位(1, 标题="线上兼职主播")])
        行 = loop._pool.列表()[0]
        assert (行.get("ai") or {}).get("判于"), "判过就要留时间戳，不然下一轮又重判一遍"
        assert 行["ai"]["分"] == 0 and 行["ai"]["合"] is False
        assert 行["ai"]["否决"] == "主播"
        assert "主播" in 行["ai"]["理由"], 行["ai"]

    def test_判分不到阈值也要落进池子(self, tmp_path):
        """50 分是"判过了、不够"，不是"没判"。"""
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.screen_max_per_round = 5
        self._接进光引擎(loop, self._引擎替身(
            {"score": 40, "is_match": True, "reason": "方向不太对"}))
        loop._capture_jobs([岗位(1)])
        行 = loop._pool.列表()[0]
        assert 行["ai"]["分"] == 40, 行.get("ai")
        assert 行["ai"]["接口"] == "agnes-2.5-flash", "哪一家判的要留得下来"

    def test_一个接口都没轮到试仍然不留时间戳(self, tmp_path):
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.screen_max_per_round = 5
        self._接进光引擎(loop, self._引擎替身(
            {"score": 0, "is_match": False, "reason": ""}, 判了=False, 没接口=True))
        loop._capture_jobs([岗位(1)])
        行 = loop._pool.列表()[0]
        assert not (行.get("ai") or {}).get("判于"), "真·没接口不许写成判过"

    def test_AI这次没答上来不留时间戳(self, tmp_path):
        """接口超时/正文截断带 ai_error：把它钉成 0 分等于一次抖动永久判死这单。"""
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.screen_max_per_round = 5
        self._接进光引擎(loop, self._引擎替身(
            {"score": 0, "is_match": False, "ai_error": True, "reason": "请求超时（30s）"}))
        loop._capture_jobs([岗位(1)])
        行 = loop._pool.列表()[0]
        assert not (行.get("ai") or {}).get("判于"), 行.get("ai")
        assert not (行.get("ai") or {}).get("分")

    def test_判过不合的下轮不再重判(self, tmp_path):
        """结论落了盘，名额就要让给还没判的行——这是这条链存在的意义。"""
        loop = _loop(tmp_path, 引擎=_光引擎())
        loop.config.ai.screen_max_per_round = 5
        分析 = MagicMock(side_effect=self._引擎替身(
            {"score": 0, "is_match": False, "veto_hit": "主播", "reason": "命中否决词"}))
        loop._greet_engine._analyze_job_with_ai = 分析
        loop._capture_jobs([岗位(1, 标题="线上兼职主播")])
        assert 分析.call_count == 1
        loop._capture_jobs([岗位(2, 标题="线上兼职主播")])
        assert 分析.call_count == 2, "只有新来的那条该判，旧的那条已经结了"
        assert (loop._pool.取(loop._pool.列表()[0]["id"])["ai"] or {}).get("分") == 0



class 勾选投递Test:
    def test_勾了的由打招呼线程投出去(self, tmp_path):
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = False
        loop._greet_engine._greeting_for.return_value = ("话术", "AI 现编")
        loop._greet_engine.send_greeting.return_value = True
        loop._capture_jobs([岗位(1), 岗位(2)])
        两 = [r["id"] for r in loop._pool.列表()]
        loop._pool.标状态([两[0]], 状态已选)
        assert loop._drain_apply_queue() == 1
        assert loop._greet_engine.send_greeting.call_count == 1
        投 = loop._pool.取(两[0])
        assert 投["status"] == 状态已投
        assert 投["投递"]["成"] is True
        assert loop._pool.取(两[1])["status"] == 状态待选, "没勾的不许动"

    def test_冷却中勾了的留着等下一轮(self, tmp_path):
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 300
        loop._greet_engine.greet_cooldown_reason.return_value = "本号当日沟通额度用完"
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态已选)
        assert loop._drain_apply_queue() == 0
        loop._greet_engine.send_greeting.assert_not_called()
        assert loop._pool.取(编号)["status"] == 状态已选, "投不成要把勾留着，别偷偷取消"

    def test_BOSS说早有会话的那行判成不投不再重开详情页(self, tmp_path):
        """「继续沟通」不是暂时投不成：这一单在 BOSS 侧已经有了，重试多少次同一个答案。

        10-31 现账：号2 勾的 5 行里 4 行是这一类，池子把它们留在「已勾待投」，
        今天 70 次跳过有 50 次（71.4%）就是这么烧出来的——每轮都重开一次岗位详情页，
        而打开详情页正是风控最集中那一步。
        """
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = False
        loop._greet_engine._greeting_for.return_value = ("话术", "AI 现编")
        loop._greet_engine.send_greeting.return_value = False
        loop._greet_engine.最近一次跳过原因.return_value = "该岗位之前已投递过（继续沟通）"
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态已选)
        assert loop._drain_apply_queue() == 0
        行 = loop._pool.取(编号)
        assert 行["status"] == 状态不投, 行["status"]
        assert 行["投递"]["可重试"] is False and 行["投递"]["原因"] == "该岗位之前已投递过（继续沟通）"
        assert "不再重投" in (行["勾选"]["动作"]), 行["勾选"]
        assert loop._pool.列表(状态=状态已选) == [], "判完还留在队列＝下一轮再点一次同一张详情页"
        # 放回可重试 只放话术那一类，这一行不许被偷偷放回队列
        assert loop._pool.放回可重试()[0] == 0

    def test_腿上的毛病要留在队列里并且把真因写出来(self, tmp_path):
        """页面加载失败是"这一刻投不成"，不是这一单的判决；红字要写因，不许写「详见记录」。"""
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = False
        loop._greet_engine._greeting_for.return_value = ("话术", "AI 现编")
        loop._greet_engine.send_greeting.return_value = False
        loop._greet_engine.最近一次跳过原因.return_value = "页面加载失败"
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态已选)
        loop._drain_apply_queue()
        行 = loop._pool.取(编号)
        assert 行["status"] == 状态已选, "判成不投就成了『改了还是不投』那种假死"
        assert 行["投递"]["原因"] == "页面加载失败", 行["投递"]
        assert 行["投递"]["可重试"] is True
        assert 是可重试的失败(行["投递"]) is True

    def test_投成的那行不许留可重试(self, tmp_path):
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = False
        loop._greet_engine._greeting_for.return_value = ("话术", "AI 现编")
        loop._greet_engine.send_greeting.return_value = True
        loop._greet_engine.最近一次跳过原因.return_value = ""
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态已选)
        assert loop._drain_apply_queue() == 1
        投 = loop._pool.取(编号)["投递"]
        assert 投["成"] is True and set(投) == {"成", "时间"}, 投

    def test_跳过原因四档只有永久那两种判永久(self):
        from boss_bot.greet_engine import (CHAT_REDIRECT_REASON, OFFLINE_JOB_REASON,
                                           PERMANENT_SKIP_REASONS, 是永久不投原因)
        assert CHAT_REDIRECT_REASON in PERMANENT_SKIP_REASONS and OFFLINE_JOB_REASON in PERMANENT_SKIP_REASONS, \
            "原因串在两份地方各写一遍——归因那边改了这里不会跟着改"
        assert 是永久不投原因(CHAT_REDIRECT_REASON + "｜岗位名 数据分析师") is True, \
            "带现场尾巴的成因判不出来，那一行就会永远留在队列里"
        assert 是永久不投原因("该岗位之前已投递过（继续沟通）") is True
        assert 是永久不投原因("页面加载失败") is False
        assert 是永久不投原因("") is False and 是永久不投原因(None) is False

    def test_继续沟通那支先标已沟通再交原因(self):
        """标进去重库才不会被抓取腿再塞回池子（口径同 _explain_missing_chat_button 的 already）。"""
        import inspect

        from boss_bot.greet_engine import GreetEngine
        源 = inspect.getsource(GreetEngine._apply_job_inner)
        命中 = 源.index('if "继续沟通" in btn_text')
        段 = 源[命中:命中 + 400]
        assert "self._mark_chatted(job)" in 段, "没标库，下趟搜索还会把同一条岗位塞回池子"
        assert 段.index("_mark_chatted") < 段.index("return False"), "先 return 就轮不到标库那一句"

    def test_勾了的要把他勾的这件事带到投递腿(self, tmp_path):
        """投递腿靠这个标记免掉二次判定（见 tests/test_ticked_job_applies_directly.py）。

        标记漏了不会报错，只会让他勾的那条又被词表弹回来——界面红字写着
        「点了沟通但没成」，查起来两头都看不出是判据没让开，所以钉在传参上。
        """
        from boss_bot.greet_engine import 勾选标记, 勾选投递的
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = False
        loop._greet_engine._greeting_for.return_value = ("话术", "账号默认")
        loop._greet_engine.send_greeting.return_value = True
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态已选)
        loop._drain_apply_queue()
        递 = loop._greet_engine.send_greeting.call_args[0][0]
        assert 勾选投递的(递) is True
        assert 递[勾选标记] is True

    def test_自动腿搜到的岗位不许带这个标记(self, tmp_path):
        """关掉只抓不投：机器自己搜到就去投，那一刻没人替他挑过，
        否决词/方向闸一道都不能少——所以标记只能由勾选那一条路打上。"""
        from boss_bot.greet_engine import 勾选投递的
        loop = _loop(tmp_path)
        loop._hot_reload_config = lambda: None
        loop.config.greet.capture_only = False
        引擎 = loop._greet_engine
        引擎._ai_enabled = False            # 这条断言只看标记，不陪 AI 判分链
        引擎._rate_limit_enabled = False
        引擎._is_already_chatted.return_value = False
        引擎._drawer_given_up.return_value = False
        引擎._greeting_for.return_value = ("话术", "账号默认")
        引擎.send_greeting.return_value = True
        引擎.search_jobs.return_value = [岗位(1)]
        loop._build_greet_tasks = lambda: [{"query": "数据分析", "city": "长沙",
                                           "scroll_pages": 1,
                                           "message_interval_min": 0,
                                           "message_interval_max": 0}]
        loop._run_greet_round()
        递 = 引擎.send_greeting.call_args[0][0]
        assert 勾选投递的(递) is False, "自动投的那一条被当成「他勾的」放行，就是没人判的盲投"

    def test_已沟通过的标成不投并写原因(self, tmp_path):
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = True
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态已选)
        loop._drain_apply_queue()
        行 = loop._pool.取(编号)
        assert 行["status"] == 状态不投
        assert "已沟通过" in 行["投递"]["原因"]
        loop._greet_engine.send_greeting.assert_not_called()

    def test_招呼语这一刻不可用要留在队列里再试(self, tmp_path):
        """这不是"这条岗位不能投"，是"这一刻这句话不能发"。

        2026-10-29 他截图问的那行红字就是这么留死的：01:38 判「话术不可用：报了学历档次
        『双一流』，画像里只写了本科在读」，画像 12:39 就补上了双一流，行上还挂着"投不出"。
        判成永久不投＝他改了配置也不生效。所以留在「已勾待投」，只记最近一次原因 + 可重试。
        """
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = False
        loop._greet_engine._greeting_for.return_value = ("", "话术不可用：报了学历档次「双一流」")
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态已选)
        loop._drain_apply_queue()
        行 = loop._pool.取(编号)
        assert 行["status"] == 状态已选, "话术这一类不许判死，勾还在他手上，改了下一轮就该投出去"
        assert 行["投递"]["成"] is False and "话术不可用" in 行["投递"]["原因"]
        assert 行["投递"]["可重试"] is True, "界面靠这个键把「投不出」换成「上次没投出去」"
        loop._greet_engine.send_greeting.assert_not_called()
        # 下一轮把话术改对了，同一条要真的能投出去（不用他重新勾）
        loop._greet_engine._greeting_for.return_value = ("您好，我是双一流的统计学本科", "账号默认")
        assert loop._drain_apply_queue() == 1
        assert loop._pool.取(编号)["status"] == 状态已投


class 池里不留投不出去的Test:
    """他 2026-10-29 的原话：「已经投递过的就不应该显示在投递记录里面啊」。

    现数：两号池 3276 行里 328 行（10.0%）的 URL 已在去重库里 —— 勾了只会被判「已沟通过」。
    这一族要么在抓的时候就别进来，要么进来了要被挪走，不能留在那儿挡路。
    """

    def test_去重库命中的岗位根本不进池(self, tmp_path, 去重库为空):
        去重库为空.add(行id(岗位(2)["url"]))
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 0
        话s = []
        loop._log = lambda 级, 话: 话s.append(话)
        loop._capture_jobs([岗位(1), 岗位(2)])
        在池 = [r["url"] for r in loop._pool.列表()]
        assert 在池 == [岗位(1)["url"]], "已沟通过的那条不许进池，没沟通过的照常进"
        assert any("已有会话" in 话 and "1 条" in 话 for 话 in 话s), \
            "拦了几条要在日志里说一句，不然看着像漏抓了：%s" % 话s

    def test_追踪参数不同的同一条岗位也算沟通过(self, tmp_path):
        """去重库里存的是当时那条 URL，BOSS 回头给同一条岗位挂别的参数。

        只比整串的话"这条没投过"，同一个 HR 就收到两条一模一样的招呼——
        所以三处（抓取腿/投递腿/池子清理）认的都是 `行id` 折出来的那一份身份。
        """
        原 = "https://www.zhipin.com/job_detail/abc.html"
        变 = "https://www.zhipin.com/job_detail/abc.html?_security_check=1&ut=2#c"
        assert 行id(原) == 行id(变)
        from boss_bot.greet_engine import 这条沟通过了
        assert 这条沟通过了(变, {行id(原)})
        assert not 这条沟通过了("", {行id(原)}), "没有链接就判不了，不许顺手当成投过"
        assert not 这条沟通过了(原, set()), "去重库读不出来时按没沟通过处理，不能把整池判死"

    def test_存量里已沟通的行被挪走且留原件(self, tmp_path, 去重库为空):
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1), 岗位(2), 岗位(3), 岗位(4)])
        四 = [r["id"] for r in loop._pool.列表()]
        loop._pool.标状态([四[1]], 状态已选)
        loop._pool.标状态([四[2]], 状态不投)
        loop._pool.标状态([四[3]], 状态已投)
        去重库为空.update(行id(岗位(n)["url"]) for n in (1, 2, 3, 4))
        挪了, 话 = loop._pool.挪走已沟通(去重库为空)
        assert 挪了 == 3, "待选/已选/不投的三类都算投不出去，只有已投的要留（那是账）：%s" % 话
        assert [r["id"] for r in loop._pool.列表()] == [四[3]]
        import json
        from pathlib import Path
        原件 = json.loads(Path(loop._pool.挪出路径).read_text(encoding="utf-8"))
        assert len(原件) == 3 and all(r.get("挪出原因") for r in 原件), "只挪不删：原件要一份不少在那份文件里"
        assert loop._pool.挪走已沟通(去重库为空)[0] == 0, "第二次跑不该再挪一遍"

    def test_挪出文件读不出来时一条都不动(self, tmp_path, 去重库为空):
        """那份是唯一的留档。读不出来还照写＝把它盖掉，等于删了用户的候选清单。"""
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        from pathlib import Path
        Path(loop._pool.挪出路径).write_text("{半截的 JSON", encoding="utf-8")
        去重库为空.add(行id(岗位(1)["url"]))
        挪了, 话 = loop._pool.挪走已沟通(去重库为空)
        assert 挪了 == 0 and "没动" in 话
        assert [r["id"] for r in loop._pool.列表()] == [编号], "留档不可用时宁可不动池子"

    def test_话术那一类的旧红字放回待选(self, tmp_path):
        """老行没有「可重试」这个键（10-29 之前不写），按原因串认，认完盖上键。"""
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1), 岗位(2)])
        两 = [r["id"] for r in loop._pool.列表()]
        for 编号, 原因, 谁 in ((两[0], "话术不可用：报了学历档次「双一流」，画像里只写了「本科在读」",
                              "机器判招呼语不可用"),
                             (两[1], "已沟通过（BOSS 侧已有这条会话）", "机器判已沟通过")):
            loop._pool.标状态([编号], 状态不投)
            loop._pool.改(编号, 投递={"成": False, "原因": 原因, "时间": time.time()},
                         勾选={"动作": 谁, "来源": "打招呼线程", "时间": time.time()})
        assert 是可重试的失败(loop._pool.取(两[0])["投递"]) is True
        assert 是可重试的失败(loop._pool.取(两[1])["投递"]) is False, "BOSS 已有会话那种放回多少次都投不出去"
        放回, 话 = loop._pool.放回可重试()
        assert 放回 == 1, 话
        行 = loop._pool.取(两[0])
        assert 行["status"] == 状态待选 and 行["投递"]["可重试"] is True
        assert 行["投递"]["原因"].startswith("话术不可用"), "原因要留着，界面才说得出上次为什么没投"
        assert loop._pool.取(两[1])["status"] == 状态不投

    def test_他自己标的不投机器不许偷偷放回(self, tmp_path):
        """池子的头一条规矩是"他改过的东西机器不许重置"。

        同一行如果那次「不投」是他从下拉里挑的（`标状态` 写的留痕是 来源=面板点击 /
        动作=标成不投），哪怕行上挂着一句旧的话术原因也不许放回——放回就是替他改了决定。
        """
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态不投)      # 他自己点的，留痕是"标成不投"
        loop._pool.改(编号, 投递={"成": False, "原因": "话术不可用：以前那次判的",
                                "时间": time.time()})
        assert str((loop._pool.取(编号).get("勾选") or {}).get("动作")) == "标成不投"
        assert loop._pool.放回可重试()[0] == 0
        assert loop._pool.取(编号)["status"] == 状态不投

    def test_没有留痕的老红字也得放回(self, tmp_path):
        """线上他截图那一行就是 `勾选=null`——留痕是 10-29 当天晚些才全端接上的。

        判成"必须写着机器判招呼语才放回"，正好把那一行漏在外面：他改了画像，
        红字还是红的。所以这里改成排除式：只挡写明"面板点击/标成不投"的，
        没有留痕的算机器判的（那时候也只有机器会写 投递）。
        """
        loop = _loop(tmp_path)
        loop.config.ai.screen_max_per_round = 0
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        盘 = loop._pool.路径
        import json
        行s = json.loads(Path(盘).read_text(encoding="utf-8"))
        行s[0]["status"] = 状态不投
        行s[0]["勾选"] = None
        行s[0]["投递"] = {"成": False, "原因": "话术不可用：报了学历档次「双一流」，画像里只写了「本科在读（统计学）」",
                         "时间": 1793209114.457283}
        Path(盘).write_text(json.dumps(行s, ensure_ascii=False), encoding="utf-8")
        assert loop._pool.取(编号).get("勾选") is None
        assert loop._pool.放回可重试()[0] == 1, "线上那行红字就是这个形状，放回必须认它"
        行 = loop._pool.取(编号)
        assert 行["status"] == 状态待选 and 行["投递"]["可重试"] is True

    def test_演练模式不真点沟通(self, tmp_path):
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = False
        loop._greet_engine._greeting_for.return_value = ("话术", "默认")
        loop._dry_run = lambda *a, **k: True
        loop._capture_jobs([岗位(1)])
        编号 = loop._pool.列表()[0]["id"]
        loop._pool.标状态([编号], 状态已选)
        loop._drain_apply_queue()
        loop._greet_engine.send_greeting.assert_not_called()

    def test_投出去的要计进统计(self, tmp_path):
        loop = _loop(tmp_path)
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine._is_already_chatted.return_value = False
        loop._greet_engine._greeting_for.return_value = ("话术", "默认")
        loop._greet_engine.send_greeting.return_value = True
        loop._capture_jobs([岗位(1)])
        loop._pool.标状态([loop._pool.列表()[0]["id"]], 状态已选)
        loop._drain_apply_queue()
        assert loop._stats_dict["greet_applied"] == 1
        loop._metrics.bump.assert_called_once_with(0, "greet_sent")


class 池与轮次接线Test:
    def test_每轮开头先排空勾选再出去搜(self, tmp_path):
        """顺序要钉住：勾是"我现在就要这条"，排在搜索后面最快也要等下一轮才动。"""
        序 = []
        loop = _loop(tmp_path)
        loop._drain_apply_queue = lambda: 序.append("投")
        loop._build_greet_tasks = lambda: 序.append("搜") or []
        loop.config.greet.capture_only = True
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._run_greet_round()
        assert 序 == ["投", "搜"]

    def test_每个搜索任务收尾都再排空一次勾选(self, tmp_path):
        """2026-10-29 口径：一轮里几个搜索任务串下来要跑十几分钟，只在轮头排空一次
        等于他中途勾的岗位最快也要等整轮跑完才动——点了没反应就是那种"没生效"。"""
        序 = []
        loop = _loop(tmp_path)
        loop.config.greet.capture_only = True
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine.search_jobs.return_value = [岗位(1)]
        loop._build_greet_tasks = lambda: [
            {"query": "数据分析", "city": "长沙", "scroll_pages": 1},
            {"query": "数据标注", "city": "长沙", "scroll_pages": 1},
        ]
        loop._drain_apply_queue = lambda: 序.append("投") or 0
        loop._capture_jobs = lambda jobs: 序.append("抓")
        loop._run_greet_round()
        assert 序 == ["投", "抓", "投", "抓", "投"], f"搜索任务之间没排空：{序}"

    def test_这条门真的会红(self, tmp_path):
        """抓那一步一抛，收尾那次排空就走不到——只剩轮头那一次。
        断言的是次数差，不是"代码里有没有这一行"。"""
        def _跑(会抛):
            序 = []
            loop = _loop(tmp_path)
            loop.config.greet.capture_only = True
            loop._greet_engine.greet_cooldown_left.return_value = 0
            loop._greet_engine.search_jobs.return_value = [岗位(1)]
            loop._build_greet_tasks = lambda: [
                {"query": "数据分析", "city": "长沙", "scroll_pages": 1}]
            loop._drain_apply_queue = lambda: 序.append("投") or 0
            if 会抛:
                def 炸(jobs):
                    序.append("抓")
                    raise RuntimeError("池文件被谁锁了")
                loop._capture_jobs = 炸
            else:
                loop._capture_jobs = lambda jobs: 序.append("抓")
            loop._run_greet_round()
            return 序.count("投")
        assert _跑(False) == 2, _跑
        assert _跑(True) == 1, "抓取挂了之后收尾那次排空还在跑＝这两条没分开"

    def test_抓取腿异常不许把整轮带走(self, tmp_path):
        loop = _loop(tmp_path)
        loop.config.greet.capture_only = True
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._greet_engine.search_jobs.return_value = [岗位(1)]
        loop._build_greet_tasks = lambda: [{"query": "x", "city": "长沙",
                                           "scroll_pages": 1}]
        loop._capture_jobs = MagicMock(side_effect=RuntimeError("池文件被谁锁了"))
        assert loop._run_greet_round() is not None, "轮次要收得住，不许把线程炸掉"

    def test_排空勾选时池读不成也不许带走搜索腿(self, tmp_path):
        """排空队列写在轮次最前面：它一抛（半截构造的 loop 没有 account_index、
        池文件坏了都算），这一轮就一条都不搜，界面上看像"投递腿又冻住了" """
        走 = []
        喊 = []
        loop = _loop(tmp_path)
        loop.config.greet.capture_only = True
        loop._greet_engine.greet_cooldown_left.return_value = 0
        loop._build_greet_tasks = lambda: 走.append("搜") or []
        loop._capture_jobs = lambda jobs: 走.append("抓")
        loop._log = lambda lvl, msg, *a, **k: 喊.append(str(msg))
        del loop.account_index          # 模拟池还没配上就挨的一脚
        del loop._pool                  # 夹具里预置过；不撤掉就走不到"读不成"这条分支
        loop._run_greet_round()
        assert 走 == ["搜"], f"池读不成把搜索腿一起带走了：{走}"
        assert any("没排空" in x for x in 喊), f"读不成要念出来，不许静默吞掉：{喊}"
