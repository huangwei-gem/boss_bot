# -*- coding: utf-8 -*-
"""追问链要长在真正筛岗那条腿上。

2026-11-01 的现场：盘上 3288 条池子判分、`ai_probe` 一条都没有。两处同时成立——
① 两个号走「只抓不投」，AI 判分发生在池子（screen_judge / screen_runner），
   而追问只挂在打招呼腿里，等于永远不触发；
② `ai.veto_only_match=true` 把每个没命的岗都抬到 95，55-69 的边界带一条都不会有。

这里锁住修好之后的三件事：池子这条腿会问、边界带/否决词/接口异常不会乱问、
复盘接口能读到池子里的追问。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import judgement_review, screen_judge  # noqa: E402
from boss_bot.greet_engine import in_probe_band  # noqa: E402
from boss_bot.screen_runner import _追问器, 按号轮转  # noqa: E402


class 假链:
    def __init__(self, 答案=None):
        self.答案 = 答案 or {"blocking_requirement": "需要 3 年数据分析经验",
                             "evidence_missing": "实习里没写项目周期",
                             "fixable_by_resume": True,
                             "score_if_fixed": 78}
        self.次数 = 0

    def probe_rejection(self, job, verdict):
        self.次数 += 1
        return self.答案


class 假池:
    def __init__(self):
        self.写进去 = None

    def 改(self, 编号, **字段):
        self.写进去 = 字段
        return True, ""


class 假AI:
    def __init__(self, 上限=5, 阈值=70):
        self.probe_max_per_round = 上限
        self.match_threshold = 阈值


class 假配置:
    def __init__(self, 上限=5, 阈值=70):
        self.ai = 假AI(上限, 阈值)


class 假引擎:
    def __init__(self, 判分, 上限=5, 阈值=70):
        self._判分 = 判分
        self.config = 假配置(上限, 阈值)
        self._ai_analyzer = 假链()

    def _analyze_job_with_ai(self, job):
        job["_ai_result"] = dict(self._判分)
        return dict(self._判分), 0.01


def _行():
    return {"id": "abc", "url": "https://www.zhipin.com/job_detail/x.html",
            "job_name": "数据分析师", "company": "某公司",
            "account_index": 0, "captured_at": 1793431537.9}


class Test池子这条腿会追问:
    def _跑(self, 判分, 追问=None):
        池 = 假池()
        引擎 = 假引擎(判分)
        追问 = 追问 if 追问 is not None else _追问器(引擎)
        话, 写 = screen_judge.判分写回(池, 引擎, _行(), 文档={"hash": "h"}, 追问=追问)
        return 池, 写, 话, 引擎

    def test_边界带里判不合会写进追问(self):
        池, 写, 话, 引擎 = self._跑({"score": 65, "is_match": False,
                                     "reason": "方向对但经验不够"})
        assert 写 and 写["合"] is False
        assert 写.get("追问", {}).get("blocking_requirement"), 写
        assert 引擎._ai_analyzer.次数 == 1
        assert 池.写进去["ai"].get("追问"), "追问没跟着结论一起写回池子"

    def test_分数太远不问(self):
        _, 写, _, 引擎 = self._跑({"score": 35, "is_match": False, "reason": "方向不对"})
        assert 写 and "追问" not in 写
        assert 引擎._ai_analyzer.次数 == 0

    def test_命中硬否决不问(self):
        _, 写, _, 引擎 = self._跑({"score": 65, "is_match": False,
                                   "veto_hit": "主播", "reason": "命中否决词"})
        assert 写 and "追问" not in 写
        assert 引擎._ai_analyzer.次数 == 0

    def test_接口异常不问(self):
        _, 写, _, 引擎 = self._跑({"score": 65, "is_match": False,
                                   "ai_error": True, "reason": "接口没回话"})
        assert 写 and "追问" not in 写
        assert 引擎._ai_analyzer.次数 == 0

    def test_判过了不问(self):
        _, 写, _, 引擎 = self._跑({"score": 65, "is_match": True, "reason": "过了"})
        assert 写 and "追问" not in 写
        assert 引擎._ai_analyzer.次数 == 0

    def test_每号每轮配额真的生效(self):
        引擎 = 假引擎({"score": 65, "is_match": False}, 上限=2)
        追问 = _追问器(引擎)
        判 = {"score": 65, "is_match": False}
        assert 追问({"job_name": "a"}, 判)
        assert 追问({"job_name": "b"}, 判)
        assert 追问({"job_name": "c"}, 判) is None
        assert 引擎._ai_analyzer.次数 == 2

    def test_追问抛异常不许把判分写回带崩(self):
        def 炸(job, verdict):
            raise RuntimeError("模型又抽了")
        池, 写, 话, _ = self._跑({"score": 65, "is_match": False}, 追问=炸)
        assert 写 and 写["合"] is False
        assert "追问" not in 写
        assert 池.写进去, "追问炸了，判分结论也必须照常写回"

    def test_精筛一条把追问回调接上了(self):
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        at = 源.index("def _精筛一条")
        段 = 源[at:at + 500]
        assert "_probe_callback()" in 段, "池子精筛没接追问，界面永远是 0 条"


class Test复盘读得到池子那份:
    def test_池子行能折成复盘认识的记录(self):
        行 = {"account_index": 1, "captured_at": 1793431537.9,
              "ai": {"分": 65, "合": False, "判于": 1793431580.8,
                     "追问": {"blocking_requirement": "需要 3 年经验",
                              "evidence_missing": "没写项目周期",
                              "fixable_by_resume": True,
                              "score_if_fixed": 80}}}
        recs = judgement_review.pool_rows_as_records([行])
        assert len(recs) == 1
        assert recs[0]["account_index"] == 1
        assert recs[0]["ai_probe"]["blocking_requirement"] == "需要 3 年经验"
        data = judgement_review.build_review(recs, account_index=1, days=7,
                                             now=judgement_review.datetime(2026, 11, 2))
        # 时间戳在 2026-11，按 7 天窗口会被排除；这条只验形状与过滤不会炸
        assert data["probed"] in (0, 1)
        assert isinstance(data["top_blockers"], list)

    def test_没有追问的池子行不进复盘(self):
        assert judgement_review.pool_rows_as_records(
            [{"ai": {"分": 95, "合": True}}]) == []

    def test_追问接口把池子那份一起读(self):
        源 = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
        at = 源.index("def api_judgement_review")
        段 = 源[at:at + 1600]
        assert "pool_rows_as_records" in 段, "复盘接口只读打招呼记录，池子那条腿白问"


class Test边界带判据:
    @pytest.mark.parametrize("分数, 是否", [
        (54, False), (55, True), (69, True), (70, False), (95, False), (None, False),
    ])
    def test_阈值70带宽15(self, 分数, 是否):
        assert in_probe_band(分数, 70) is 是否


# ── 链是懒加载的：构造时取链 = 整趟不问 ──────────────────────────
class 假懒引擎(假引擎):
    """greet_engine 的真实形状：`_ai_analyzer` 在 __init__ 里是 None，
    第一次判分才建起来。上面的 假引擎 一上来就塞好链，所以它测不出"构造时取链"。"""

    def __init__(self, 判分, **字段):
        super().__init__(判分, **字段)
        self._ai_analyzer = None

    def _analyze_job_with_ai(self, job):
        if self._ai_analyzer is None:
            self._ai_analyzer = 假链()
        return super()._analyze_job_with_ai(job)


class 假无链引擎(假引擎):
    """判完分仍然没有链（AI 接口一个都没起来）：这种时候不许把配额烧掉。"""

    def __init__(self, 判分, **字段):
        super().__init__(判分, **字段)
        self._ai_analyzer = None

    def _analyze_job_with_ai(self, job):
        job["_ai_result"] = dict(self._判分)
        return dict(self._判分), 0.01


class 假号:
    """只喂 _probe_callback 用到的那几个成员，不建整个 UnifiedBotLoop。"""

    def __init__(self, 引擎, 上限=5, 阈值=70):
        self._greet_engine = 引擎
        self.config = 假配置(上限, 阈值)
        self._probe_used_this_round = 0

    def _log(self, *args, **kwargs):
        pass


class Test懒加载链:
    def test_面板重判这趟判分前没有链也要问出来(self):
        池 = 假池()
        引擎 = 假懒引擎({"score": 65, "is_match": False, "reason": "方向对但经验不够"})
        assert 引擎._ai_analyzer is None, "fixture 没造出懒加载的形状，这条门是空的"
        追问 = _追问器(引擎)
        assert 追问 is not None, "构造时就取链：整趟重判一条都不会问，复盘永远是 0 条"
        话, 写 = screen_judge.判分写回(池, 引擎, _行(), 文档={"hash": "h"}, 追问=追问)
        assert 引擎._ai_analyzer.次数 == 1, "判完分链已经有了，追问却没问出去"
        assert 写.get("追问", {}).get("blocking_requirement"), 写

    def test_面板重判一直没有链就不问也不烧配额(self):
        池 = 假池()
        引擎 = 假无链引擎({"score": 65, "is_match": False, "reason": "方向对但经验不够"})
        追问 = _追问器(引擎)
        assert 追问 is not None
        _, 写 = screen_judge.判分写回(池, 引擎, _行(), 文档={"hash": "h"}, 追问=追问)
        assert 引擎._ai_analyzer is None
        assert "追问" not in 写, "没有链却写了追问，等于把没问过的事当成问过"

    def test_池子腿的回调判分前没有链也要问出来(self):
        from boss_bot.main_loop import UnifiedBotLoop

        引擎 = 假懒引擎({"score": 65, "is_match": False})
        号 = 假号(引擎)
        回调 = UnifiedBotLoop._probe_callback(号)
        assert 回调 is not None, "构造时取链：每轮第一行、每次热重载后第一行都问不出去"
        引擎._ai_analyzer = 假链()          # 这一行判完分之后链才出现
        assert 回调({"job_name": "数据分析师"}, {"score": 65, "is_match": False})
        assert 号._probe_used_this_round == 1

    def test_池子腿没链不烧配额(self):
        from boss_bot.main_loop import UnifiedBotLoop

        号 = 假号(假无链引擎({"score": 65, "is_match": False}))
        回调 = UnifiedBotLoop._probe_callback(号)
        assert 回调 is not None
        assert 回调({"job_name": "数据分析师"}, {"score": 65, "is_match": False}) is None
        assert 号._probe_used_this_round == 0, "没问出去也记一次，配额会被空烧光"


class Test追问每条出口都要出声:
    """probe_rejection 四条出口以前只有一条会写日志。

    2026-11-02 现账：号0 边界带里 12 行、追问 0 条，而 logs/greet_engine.log 全天
    「追问」两个字 0 行——"没问到""问砸了""压根没试"在盘上长得一模一样，
    没法归因，只能靠人拿离线脚本重跑一遍才知道是接口 30 秒没回话。
    """

    @staticmethod
    def 出过声(记, 词):
        """出口要留痕，而且得挂同一个前缀——线上是按「🔎」捞这一族的。"""
        return any(词 in m and "🔎" in m for m in 记)

    def _链(self, 回):
        from boss_bot.greet_engine import AIAnalyzerChain

        记 = []
        链 = AIAnalyzerChain(
            providers=[{"name": "测接口", "api_key": "k", "api_base": "http://x",
                        "model": "m"}],
            cache_enabled=False,
            log_callback=lambda 文: 记.append(str(文)))
        链._call_provider_api = 回
        return 链, 记

    def test_接口没回话要出声并且就地再问一次(self):
        次 = {"n": 0}

        def 炸(provider, messages, **kw):
            次["n"] += 1
            raise RuntimeError("请求超时（30s）")

        链, 记 = self._链(炸)
        assert 链.probe_rejection({"job_name": "a"}, {"score": 60}) is None
        assert 次["n"] == 2, "超时是最常见的死法，一次都不重试就把这条追问丢了"
        assert self.出过声(记, "追问没问出来"), 记

    def test_回来的形状不对要出声(self):
        链, 记 = self._链(lambda provider, messages, **kw: "我觉得还行")
        assert 链.probe_rejection({"job_name": "a"}, {"score": 60}) is None
        assert self.出过声(记, "不是那个 JSON 形状"), 记

    def test_它说不出卡哪条要出声(self):
        链, 记 = self._链(lambda provider, messages, **kw: {"blocking_requirement": "  "})
        assert 链.probe_rejection({"job_name": "a"}, {"score": 60}) is None
        assert self.出过声(记, "说不出卡在哪一条"), 记

    def test_问到了也要出声(self):
        链, 记 = self._链(lambda provider, messages, **kw:
                          {"blocking_requirement": "要 3 年经验", "score_if_fixed": 80})
        assert 链.probe_rejection({"job_name": "a"}, {"score": 60})
        assert self.出过声(记, "问到了"), 记

    def test_一个接口都没有要出声(self):
        from boss_bot.greet_engine import AIAnalyzerChain

        记 = []
        链 = AIAnalyzerChain(providers=[], cache_enabled=False,
                            log_callback=lambda 文: 记.append(str(文)))
        assert 链.probe_rejection({"job_name": "a"}, {"score": 60}) is None
        assert self.出过声(记, "一个可用 AI 接口都没有"), 记


class Test死岗不再每轮烧详情页:
    """同一行连着几轮撞「没有沟通按钮」就放手。

    判据只能数我们自己记下的失败，不能靠猜 BOSS 那张兜底页是什么意思；
    而风控/登录/额度/整条腿坏都属"这一刻不行"，判永久会出现
    「改了配置还是不投」那种假死，所以门要同时钉住"这些不许放手"。
    """

    def test_尾巴变了也算同一族(self):
        from boss_bot.greet_engine import 失败族

        甲 = "岗位页没有可点的沟通按钮（详情页已打开但按钮没渲染）；页面上的按钮: 搜索|a, 查看更多优选职位|b"
        乙 = "岗位页没有可点的沟通按钮（详情页已打开但按钮没渲染）；页面上的按钮: 搜索|c"
        assert 失败族(甲) == 失败族(乙) == "岗位页没有可点的沟通按钮"
        assert 甲 != 乙, "fixture 得真是两种现场，否则这条门是空的"

    def test_够三次才放手别的族永远不放(self):
        from boss_bot.greet_engine import 该放手这一行

        原 = "岗位页没有可点的沟通按钮（详情页已打开但按钮没渲染）；页面上的按钮: 搜索|a"
        会话壳 = "页面已跳走且没有聊天元素（当前 URL: https://www.zhipin.com/web/geek/chat?id=x&jobId=y）"
        assert 该放手这一行(原, 2) is False
        assert 该放手这一行(原, 3) is True
        # 跳进会话地址却没核对出形状：这一单在 BOSS 侧已经有会话，再点也投不出去
        assert 该放手这一行(会话壳, 2) is False
        assert 该放手这一行(会话壳, 3) is True
        for 暂 in ["BOSS 弹出人机验证，需要人工在浏览器窗口完成（超时会自动跳过）",
                   "BOSS 要求重新登录（页面出现手机号+短信验证码框），登录态已失效",
                   "页面加载失败：等不到内容", "当日沟通额度已用完",
                   "点了「立即沟通」但聊天抽屉没在这个标签页里出现（URL 仍停在岗位详情页）"]:
            assert 该放手这一行(暂, 99) is False, 暂

    def _号(self, 行, 原因):
        from boss_bot.main_loop import UnifiedBotLoop
        from boss_bot.job_pool import 状态已选

        class 假池:
            def 列表(self, 状态=None):
                return [行] if 状态 == 状态已选 else []

            def 改(self, 编号, **字段):
                行.update(字段)
                return True, ""

        class 假引擎:
            def greet_cooldown_left(self):
                return 0

            def _is_already_chatted(self, job):
                return False

            def _greeting_for(self, job):
                return True, "您好，方便聊聊吗"

            def begin_event_ts(self):
                pass

            def send_greeting(self, job):
                return False

            def 最近一次跳过原因(self):
                return 原因

        class 假量:
            def bump(self, *a):
                pass

        class 假号:
            account_index = 1
            _running = True
            _greet_paused = False
            _stats_dict = {"greet_applied": 0, "greet_skipped": 0}
            _metrics = 假量()
            _greet_engine = 假引擎()

            def _job_pool(self):
                return 假池()

            def _dry_run(self, *a):
                return False

            def _log(self, *a):
                pass

        return UnifiedBotLoop._drain_apply_queue(假号())

    def test_连着三轮同一句就判不投(self):
        from boss_bot.job_pool import 状态不投, 状态已选

        原 = "岗位页没有可点的沟通按钮（详情页已打开但按钮没渲染）；页面上的按钮: 搜索|a"
        行 = {"id": "abc", "url": "https://www.zhipin.com/job_detail/x.html",
              "job_name": "数据分析师", "company": "某公司", "status": 状态已选,
              "account_index": 1, "captured_at": 1793431537.9, "投递": {}}
        self._号(行, 原)
        assert 行["status"] == 状态已选 and 行["投递"]["失败次数"] == 1, 行["投递"]
        self._号(行, 原)
        assert 行["投递"]["失败次数"] == 2, "计数没接着上一轮，等于永远攒不到头"
        self._号(行, 原)
        assert 行["status"] == 状态不投 and 行["投递"]["可重试"] is False, 行
        assert "连着 3 轮" in 行["勾选"]["动作"], 行["勾选"]

    def test_中途换了成因就从头数(self):
        原 = "岗位页没有可点的沟通按钮（详情页已打开但按钮没渲染）；页面上的按钮: 搜索|a"
        暂 = "页面加载失败：等不到内容"
        行 = {"id": "abc", "url": "https://www.zhipin.com/job_detail/x.html",
              "job_name": "数据分析师", "company": "某公司", "status": "selected",
              "account_index": 1, "captured_at": 1793431537.9, "投递": {}}
        self._号(行, 原)
        self._号(行, 暂)
        assert 行["投递"]["失败次数"] == 1, 行["投递"]
        self._号(行, 原)
        assert 行["status"] == "selected", "换过一次成因还把旧账叠上去，会误判成岗位死了"


class Test分数与结论打架按阈值改判:
    """is_match 不许压着分数走——否则阈值和他写的分段提示词一起被架空。"""

    def _链(self, 只看否决=False):
        from boss_bot.greet_engine import AIAnalyzerChain

        记 = []
        链 = AIAnalyzerChain(providers=[{"name": "测", "api_key": "k",
                                         "api_base": "http://x", "model": "m"}],
                             match_threshold=70, cache_enabled=False,
                             veto_only_match=只看否决,
                             log_callback=lambda 文: 记.append(str(文)))
        return 链, 记

    def test_62分却判合改成不合并留痕(self):
        链, 记 = self._链()
        r = 链._normalize_result({"score": 62, "is_match": True, "reason": "方向对，差经验"})
        assert r["is_match"] is False, r
        assert any("自相矛盾" in m for m in 记), 记

    def test_75分却判不合改成合(self):
        链, _ = self._链()
        assert 链._normalize_result({"score": 75, "is_match": False})["is_match"] is True

    def test_一致的不许乱改也不留痕(self):
        链, 记 = self._链()
        for 分, 合 in ((80, True), (70, True), (55, False), (30, False)):
            assert 链._normalize_result({"score": 分, "is_match": 合})["is_match"] is 合
        assert not any("自相矛盾" in m for m in 记), 记

    def test_命中否决词仍然压过一切(self):
        链, _ = self._链()
        r = 链._normalize_result({"score": 62, "is_match": True, "veto_hit": "主播"})
        assert r["is_match"] is False and r["score"] < 70, r
        assert "主播" in str(r.get("reason")), r

    def test_只看否决词那档不许被这行改动带跑(self):
        链, 记 = self._链(只看否决=True)
        r = 链._normalize_result({"score": 62, "is_match": True})
        assert r["is_match"] is True and r["score"] == 95, r
        assert not any("自相矛盾" in m for m in 记), 记


class 重判名额要分给两个号Test:
    """2026-11-03 线上现测：`/api/ai_screen_doc/run` 起的一趟 limit=120，
    跑完 `judged` 全在号0，号1 一行没判——队列是「号0 池 → 号1 池」拼起来后
    直接截前 120 条，号0 的待判数一超过上限，号1 就永远排不上。
    他这两条腿各投各的方向，号1 判不到，界面上就一直是旧口径（veto_only_match
    那阵子）留下的「95 分 合」，勾哪条都像盲投。
    """

    def test_两个号轮着取不许一个号吃光名额(self):
        队列 = [(0, {"id": "a%d" % i}) for i in range(10)] + \
               [(1, {"id": "b%d" % i}) for i in range(10)]
        出 = 按号轮转(队列, 6)
        assert len(出) == 6
        号们 = [号 for 号, _ in 出]
        assert 号们.count(0) == 3 and 号们.count(1) == 3, 号们

    def test_一个号行少时名额要让给另一个号(self):
        """轮转不能空耗：号0 只有 1 条，剩下 5 个名额要落到号1，别空转。"""
        队列 = [(0, {"id": "a0"})] + [(1, {"id": "b%d" % i}) for i in range(10)]
        出 = 按号轮转(队列, 6)
        assert len(出) == 6, 出
        assert [号 for 号, _ in 出].count(0) == 1

    def test_取出来的顺序还是各号内部的新抓在前(self):
        队列 = [(0, {"id": "a0"}), (0, {"id": "a1"}), (1, {"id": "b0"}), (1, {"id": "b1"})]
        出 = 按号轮转(队列, 4)
        assert [r["id"] for _, r in 出] == ["a0", "b0", "a1", "b1"], 出

    def test_上限为零就是全量原序(self):
        队列 = [(0, {"id": "a0"}), (1, {"id": "b0"}), (0, {"id": "a1"})]
        assert 按号轮转(队列, 0) == 队列

    def test_单号池子不许凭空多出别的号(self):
        队列 = [(1, {"id": "b%d" % i}) for i in range(5)]
        出 = 按号轮转(队列, 3)
        assert [r["id"] for _, r in 出] == ["b0", "b1", "b2"], 出
