# -*- coding: utf-8 -*-
"""AI 判不出来的那一单：投递这一侧不许盲投，回复那一侧照常句句有回应。

2026-10-08 00:42 那条"第一行的错误"（点了「立即沟通」抽屉不出来）复盘出来的形状：
四个接口连着失败（正文被思考吃光），判分落到 `AI 匹配度: 50/100 … 默认通过`，
于是这一单连 JD 都没被看过就被投出去了——而它恰好就是抽屉失败那一条。
`ai.fail_action` 这一个值同时管着两条链（回复侧 default=发兜底话术），
直接把默认值翻成 skip 会把"HR 说话没人接"改成另一个毛病，所以拆开：
投递用 greet_fail_action，回复用 fail_action。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.unified_config import AIConfig, UnifiedConfig


class 投递兜底开关Test:
    def test_默认不盲投(self):
        assert AIConfig().greet_fail_action == "skip"

    def test_回复那一条开关不变(self):
        """句句有回应是回复侧的行为，拆开之后不能被投递这条牵连。"""
        assert AIConfig().fail_action == "default"

    def test_配置读写往返(self):
        import tempfile
        cfg = UnifiedConfig()
        cfg.ai.greet_fail_action = "default"
        d = cfg.to_dict()
        assert d["ai"]["greet_fail_action"] == "default"
        path = tempfile.mkdtemp() + "/bot_config.json"
        cfg.save(path)
        assert UnifiedConfig.load(path).ai.greet_fail_action == "default"


class 引擎接线Test:
    def _engine(self, greet_fail_action):
        from boss_bot.greet_engine import GreetEngine
        e = GreetEngine.__new__(GreetEngine)
        e._ai_analyzer = None
        e.config = UnifiedConfig()
        e.config.ai.enabled = True
        e.config.ai.providers = []
        e.config.ai.greet_fail_action = greet_fail_action
        e.config.ai.fail_action = "default"
        e._log = lambda *a: None
        return e

    def test_引擎按投递那条开关建链(self):
        from boss_bot.greet_engine import AIAnalyzerChain
        e = self._engine("skip")
        GreetEngine = type(e)
        import inspect
        src = inspect.getsource(GreetEngine._apply_ai_config)
        assert "greet_fail_action" in src, "还在读 ai.fail_action，投递这条没拆开"
        e._apply_ai_config()
        assert e._ai_fail_action == "skip"

    def test_判不出就不投(self):
        """容灾链全挂时 is_match 必须为 False，主循环才走"不投"那一支。"""
        from boss_bot.greet_engine import AIAnalyzerChain
        chain = AIAnalyzerChain(providers=[], fail_action="skip")
        got = chain._fallback_result("所有接口都失败")
        assert got["is_match"] is False and got["score"] == 0, got
