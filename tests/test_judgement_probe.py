# -*- coding: utf-8 -*-
"""判"不符合"之后追问 AI 为什么不符合（判分复盘第一步：追问链）。

需求（2026-09-29 用户）："如果说不符合，可以追问一下为什么不符合，然后不符合的原因
可以用于 AI 的自进化。" 已定口径见
docs/superpowers/specs/2026-09-29-reject-reason-followup-design.md：
只问 AI 不问 HR、只追边界带、每号每轮 ≤5 次、原因只出建议不自动改配置。

这里锁的是追问链本身：拿不到可信回答时必须安静地返回 None ——
追问是附加信息，不能把它变成打招呼轮的新炸点。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.greet_engine import AIAnalyzerChain  # noqa: E402
from tests.test_ai_failover import JOB, make_chain  # noqa: E402

PROBE_BODY = {
    "blocking_requirement": "要求 3 年以上 SQL 取数经验",
    "evidence_missing": "简历里没有写明项目里用过 SQL 取数",
    "fixable_by_resume": True,
    "score_if_fixed": 78,
}


@pytest.fixture(autouse=True)
def _fresh_cooldown():
    from boss_bot.greet_engine import reset_shared_cooldown
    reset_shared_cooldown()
    yield
    reset_shared_cooldown()


def stub_call(chain, body=None, error=None, capture=None):
    """把真实 HTTP 调用换成固定回答，返回可以进 patch.object 的 side_effect。"""
    def _call(provider, messages, **kw):
        if capture is not None:
            capture.append((provider.name, messages))
        if error is not None:
            raise error
        return dict(body if body is not None else PROBE_BODY)
    return _call


class BandTest:
    """边界带：只追"差一点就过"的那些。"""

    def test_阈值以下带内才算边界(self):
        from boss_bot.greet_engine import in_probe_band
        assert in_probe_band(68, 70) is True
        assert in_probe_band(55, 70) is True

    def test_等于阈值不追问因为已经过了(self):
        from boss_bot.greet_engine import in_probe_band
        assert in_probe_band(70, 70) is False, "等于阈值是匹配，追问它是把预算花在必过的岗位上"

    def test_差太远不追问(self):
        from boss_bot.greet_engine import in_probe_band
        assert in_probe_band(54, 70) is False, "外包/城市/学历那类明显不符，问出来的原因没有可操作性"

    def test_脏分数不算(self):
        from boss_bot.greet_engine import in_probe_band
        for bad in (None, "", "abc", {"x": 1}):
            assert in_probe_band(bad, 70) is False, bad


class TargetSelectionTest:
    """每号每轮只追问 5 条时，配额要给离线最近的那些。"""

    def test_只取带内并按分数从高到低(self):
        from boss_bot.greet_engine import select_probe_targets
        items = [{"id": 1, "score": 52}, {"id": 2, "score": 68},
                 {"id": 3, "score": 61}, {"id": 4, "score": 75}]
        got = [x["id"] for x in select_probe_targets(items, threshold=70, limit=5)]
        assert got == [2, 3], "68/61 在带内，75 已过线、52 太远"

    def test_超出上限就截断(self):
        from boss_bot.greet_engine import select_probe_targets
        items = [{"id": i, "score": s} for i, s in
                 enumerate([69, 68, 67, 66, 65, 64, 63, 62])]
        got = [x["id"] for x in select_probe_targets(items, threshold=70, limit=5)]
        assert got == [0, 1, 2, 3, 4], f"配额必须硬截断，否则这一轮要多打 8 次 AI：{got}"


class ProbeCallTest:
    def test_追问拿到四个字段并把上一轮判分带回去(self):
        chain = make_chain(2)
        seen = []
        with patch.object(AIAnalyzerChain, "_call_provider_api",
                          side_effect=stub_call(chain, capture=seen)):
            got = chain.probe_rejection(dict(JOB), {"score": 63, "is_match": False,
                                                    "reason": "缺 SQL 实战证据"})
        assert got["blocking_requirement"] == PROBE_BODY["blocking_requirement"]
        assert got["evidence_missing"] == PROBE_BODY["evidence_missing"]
        assert got["fixable_by_resume"] is True
        assert got["score_if_fixed"] == 78
        asked = seen[0][1][1]["content"]
        assert "63" in asked and "缺 SQL 实战证据" in asked, "追问没带上一轮的分数和理由"

    def test_追问用的是判出这条结果的同一个接口(self):
        """换接口问得到的不是"你为什么这么判"，是"另一个人怎么判"。"""
        chain = make_chain(3)
        chain.last_provider = chain.providers[1]
        asked = []
        with patch.object(AIAnalyzerChain, "_call_provider_api",
                          side_effect=stub_call(chain, capture=asked)):
            chain.probe_rejection(dict(JOB), {"score": 66, "is_match": False})
        assert asked[0][0] == chain.providers[1].name, f"问到别的接口上了：{asked}"

    def test_字符串真值也要认成布尔(self):
        chain = make_chain(1)
        for raw, want in (("true", True), ("是", True), ("false", False),
                          ("不具备", False), (1, True), (0, False)):
            body = dict(PROBE_BODY, fixable_by_resume=raw)
            with patch.object(AIAnalyzerChain, "_call_provider_api",
                              side_effect=stub_call(chain, body=body)):
                got = chain.probe_rejection(dict(JOB), {"score": 66, "is_match": False})
            assert got["fixable_by_resume"] is want, raw

    def test_接口炸了返回None而不是把异常抛进本轮(self):
        chain = make_chain(1)
        with patch.object(AIAnalyzerChain, "_call_provider_api",
                          side_effect=stub_call(chain, error=Exception(
                              "API 请求失败: <urlopen error SSL EOF>"))):
            assert chain.probe_rejection(dict(JOB), {"score": 66, "is_match": False}) is None

    def test_没说出哪条硬性要求就算没问到(self):
        """只有"再改改简历试试"这种空话时，回流到复盘只会产出无意义建议。"""
        chain = make_chain(1)
        body = dict(PROBE_BODY, blocking_requirement="  ")
        with patch.object(AIAnalyzerChain, "_call_provider_api",
                          side_effect=stub_call(chain, body=body)):
            assert chain.probe_rejection(dict(JOB), {"score": 66, "is_match": False}) is None

    def test_四段各自截断不把记录撑爆(self):
        """greet_records.json 已经 2MB，追问四段必须各有限长。"""
        chain = make_chain(1)
        body = dict(PROBE_BODY, blocking_requirement="硬" * 5000,
                    evidence_missing="据" * 5000)
        with patch.object(AIAnalyzerChain, "_call_provider_api",
                          side_effect=stub_call(chain, body=body)):
            got = chain.probe_rejection(dict(JOB), {"score": 66, "is_match": False})
        assert len(got["blocking_requirement"]) <= 200
        assert len(got["evidence_missing"]) <= 200

    def test_没有可用接口时直接不作答(self):
        chain = make_chain(0)
        assert chain.probe_rejection(dict(JOB), {"score": 66, "is_match": False}) is None

    def test_追问回答没有score和is_match也要收(self):
        """判分那套"必须给出 score/is_match 才算可信"的校验不能套在追问上：
        追问要的是四段原因，硬走判分校验会把它当成无效输出直接丢掉。"""
        import json as _json
        from tests.test_ai_failover import FakeResponse
        chain = make_chain(1)
        body = {"choices": [{"message": {"content": _json.dumps(PROBE_BODY,
                                                                ensure_ascii=False)},
                              "finish_reason": "stop"}]}
        with patch("boss_bot.greet_engine.urlopen", return_value=FakeResponse(body)), \
                patch("boss_bot.greet_engine.Request"):
            got = chain.probe_rejection(dict(JOB), {"score": 66, "is_match": False})
        assert got and got["score_if_fixed"] == 78, got


class ProbeRecordTest:
    """追问结果要能落库、能被老记录读通。"""

    def test_记录往返带追问四段(self):
        from boss_bot.reply_record import GreetRecord
        r = GreetRecord(job_name="数据分析师", ai_score=63, is_skipped=True,
                        skip_reason="AI判定不匹配: 缺 SQL 证据", ai_probe=dict(PROBE_BODY))
        back = GreetRecord.from_dict(r.to_dict())
        assert back.ai_probe["blocking_requirement"] == PROBE_BODY["blocking_requirement"]
        assert back.ai_probe["fixable_by_resume"] is True

    def test_老记录没有这个字段读成None(self):
        """不迁移、不回填：历史 372 条记录保持原样也要能读。"""
        from boss_bot.reply_record import GreetRecord
        old = {"job_name": "老记录", "ai_score": 60, "is_skipped": True}
        assert GreetRecord.from_dict(old).ai_probe is None

    def test_追问各段截断(self):
        from boss_bot.reply_record import GreetRecord
        r = GreetRecord(ai_probe=dict(PROBE_BODY, blocking_requirement="硬" * 3000))
        d = r.to_dict()
        assert len(d["ai_probe"]["blocking_requirement"]) <= 200


class ProbeGateTest:
    """主循环里的闸门：边界带内 + 配额没满才问，问不到也不影响这条被跳过。"""

    def _loop(self, limit=5, threshold=70):
        from unittest.mock import MagicMock
        import boss_bot.unified_config as UC
        from boss_bot.main_loop import UnifiedBotLoop
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop._log = lambda *a, **k: None
        loop.config.ai.match_threshold = threshold
        loop.config.ai.probe_max_per_round = limit
        loop._probe_used_this_round = 0
        loop._greet_engine = MagicMock()
        loop._greet_engine._ai_analyzer.probe_rejection.return_value = dict(PROBE_BODY)
        return loop

    def test_边界带内才追问(self):
        loop = self._loop()
        job = {"job_name": "x", "url": "u"}
        assert loop._maybe_probe_rejection(job, {"score": 64, "is_match": False}) is True
        assert job["_ai_probe"]["blocking_requirement"] == PROBE_BODY["blocking_requirement"]

    def test_带外不问(self):
        loop = self._loop()
        job = {"job_name": "x"}
        assert loop._maybe_probe_rejection(job, {"score": 12, "is_match": False}) is False
        assert "_ai_probe" not in job
        loop._greet_engine._ai_analyzer.probe_rejection.assert_not_called()

    def test_配额用满就不再问(self):
        loop = self._loop(limit=2)
        for score in (66, 65):
            assert loop._maybe_probe_rejection({"job_name": "x"},
                                               {"score": score, "is_match": False}) is True
        assert loop._maybe_probe_rejection({"job_name": "y"},
                                           {"score": 69, "is_match": False}) is False
        assert loop._probe_used_this_round == 2, "第三条不该再花一次 AI 调用"

    def test_问失败也要记这一次开销(self):
        """配额是给"调用次数"的，不是给"成功次数"的：问砸了同样烧了一次接口。
        返回值只表示"这轮又用掉一格"，这条岗位该跳过还是跳过。"""
        loop = self._loop(limit=1)
        loop._greet_engine._ai_analyzer.probe_rejection.return_value = None
        job = {"job_name": "x"}
        assert loop._maybe_probe_rejection(job, {"score": 66, "is_match": False}) is True
        assert "_ai_probe" not in job, "没问到东西就不该往记录里塞空追问"
        assert loop._probe_used_this_round == 1
        # 配额已满，下一条带内的也不再问
        assert loop._maybe_probe_rejection({"job_name": "y"},
                                           {"score": 69, "is_match": False}) is False

    def test_上限设成0就是关掉(self):
        loop = self._loop(limit=0)
        assert loop._maybe_probe_rejection({"job_name": "x"},
                                           {"score": 66, "is_match": False}) is False
        loop._greet_engine._ai_analyzer.probe_rejection.assert_not_called()

    def test_没有AI链时安静跳过(self):
        loop = self._loop()
        loop._greet_engine._ai_analyzer = None
        assert loop._maybe_probe_rejection({"job_name": "x"},
                                           {"score": 66, "is_match": False}) is False

    def test_每轮开始把配额清零(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._run_greet_round)
        assert "_probe_used_this_round = 0" in src, "不按轮重置的话一整天只能问 5 条"

    def test_不匹配分支里先追问再落库(self):
        import inspect
        from boss_bot.main_loop import UnifiedBotLoop
        src = inspect.getsource(UnifiedBotLoop._run_greet_round)
        i = src.index("_maybe_probe_rejection")
        seg = src[i:i + 700]
        assert "_record_greet_skip" in seg, \
            "追问结果必须落到这条岗位的跳过记录里"
        # 落库和实时推送收在同一个 helper 里，两条通道不能只走一条
        body = inspect.getsource(UnifiedBotLoop._record_greet_skip)
        assert "_emit_greet_event" in body and "_record_greet" in body, \
            "追问结果必须同时进记录和实时推送"


class ProbeConfigTest:
    def _loaded(self, ai_block):
        import json
        import os
        import tempfile
        import boss_bot.unified_config as UC
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "c.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"ai": ai_block}, f)
            return UC.UnifiedConfig.load(p)

    def test_上限是配置项且默认5(self):
        import boss_bot.unified_config as UC
        assert UC.UnifiedConfig().ai.probe_max_per_round == 5

    def test_从文件读回(self):
        assert self._loaded({"enabled": True, "probe_max_per_round": 12}
                            ).ai.probe_max_per_round == 12

    def test_越界值夹住而不是照着跑(self):
        """界面输入框填 999 就是一轮 999 次额外 AI 调用，必须在读入时夹住。"""
        assert self._loaded({"probe_max_per_round": 999}).ai.probe_max_per_round == 20
        assert self._loaded({"probe_max_per_round": -3}).ai.probe_max_per_round == 0

    def test_写盘要带上这一项否则界面改了留不住(self):
        import boss_bot.unified_config as UC
        c = UC.UnifiedConfig()
        c.ai.probe_max_per_round = 9
        assert c.to_dict()["ai"]["probe_max_per_round"] == 9



