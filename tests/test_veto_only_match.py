# -*- coding: utf-8 -*-
""""只看自定义筛选词"要真的只看筛选词。

2026-10-04 现场：用户把判分口径改成"只按否决词拦，别管专业背景"，提示词也换了，
但 AI 还在用"与求职方向不符/跨行岗位/统计学背景"这类理由拦岗位（实测 11:47-11:49
三条：线上兼职客服 10 分、线上视频剪辑 15 分、兼职线上数学老师 25 分）。
根因有两条，都得钉住：
  1. 判分提示词只是"求模型别考虑背景"，基础提示词里那段简历/方向照样压过它；
     要靠开关把结论变成代码说了算。
  2. 判分缓存的键只有"岗位URL+简历哈希"，换了口径照样命中旧结论，
     等于改了不生效（实测 11:44 那一轮 62 次判分里 54 次是缓存命中）。
"""
import json
from pathlib import Path

from boss_bot.greet_engine import AIAnalyzerChain
from boss_bot.unified_config import UnifiedConfig

PROVIDERS = [{"name": "p0", "api_key": "k0", "api_base": "https://h0/v1", "model": "m0"}]


def _chain(veto_only=False, **kw):
    kw.setdefault("custom_filter_keywords", ["主播", "需坐班"])
    return AIAnalyzerChain(providers=PROVIDERS, match_threshold=70,
                           cache_enabled=False, veto_only_match=veto_only, **kw)


class VetoOnlyGateTest:
    def test_开了开关后方向不符也放行(self):
        got = _chain(veto_only=True)._normalize_result(
            {"score": 15, "is_match": False, "reason": "与求职方向不符",
             "suggested_greeting": "您好，看到贵司在招线上视频剪辑…"})
        assert got["is_match"] is True, got
        assert got["score"] >= 70, got
        assert got["suggested_greeting"].startswith("您好"), "招呼语不该被动"

    def test_命中否决词照样拦(self):
        got = _chain(veto_only=True)._normalize_result(
            {"score": 95, "is_match": True, "reason": "看着挺合适", "veto_hit": "需坐班"})
        assert got["is_match"] is False, got
        assert "需坐班" in got["reason"]

    def test_没开开关时维持原判定(self):
        got = _chain(veto_only=False)._normalize_result(
            {"score": 15, "is_match": False, "reason": "与求职方向不符"})
        assert got["is_match"] is False, got

    def test_开了开关也要留痕说明为什么放行(self):
        got = _chain(veto_only=True)._normalize_result(
            {"score": 15, "is_match": False, "reason": "与求职方向不符"})
        assert "只看否决词" in got["reason"], got["reason"]


class CacheKeyTest:
    def _key(self, **kw):
        chain = _chain(**kw)
        chain._resume_hash = "resume-hash"
        return chain._make_cache_key("https://zhipin/job/1", "resume-hash")

    def test_同一口径同一个键(self):
        assert self._key(custom_scoring_prompt="A") == self._key(custom_scoring_prompt="A")

    def test_换提示词必须换键(self):
        assert self._key(custom_scoring_prompt="A") != self._key(custom_scoring_prompt="B")

    def test_换阈值必须换键(self):
        assert (AIAnalyzerChain(providers=PROVIDERS, match_threshold=70, cache_enabled=False)
                ._make_cache_key("u", "r") !=
                AIAnalyzerChain(providers=PROVIDERS, match_threshold=60, cache_enabled=False)
                ._make_cache_key("u", "r"))

    def test_否决词变了必须换键(self):
        assert (self._key(custom_filter_keywords=["主播"]) !=
                self._key(custom_filter_keywords=["主播", "需坐班"]))

    def test_开关切换必须换键(self):
        assert self._key(veto_only=True) != self._key(veto_only=False)


class ConfigWiringTest:
    def test_配置存得下读得出(self, tmp_path):
        cfg_file = tmp_path / "bot_config.json"
        cfg = UnifiedConfig()
        cfg.ai.veto_only_match = True
        cfg.save(str(cfg_file))
        back = UnifiedConfig.load(
            config_path=str(cfg_file),
            profile_path=str(tmp_path / "none.json"),
            overrides_path=str(tmp_path / "none_overrides.json"))
        assert back.ai.veto_only_match is True

    def test_默认是关的(self):
        assert UnifiedConfig().ai.veto_only_match is False

    def test_引擎把开关传进分析器(self):
        import inspect
        from boss_bot import greet_engine
        src = inspect.getsource(greet_engine)
        assert "veto_only_match=self._ai_veto_only" in src, "分析器拿不到这个开关"
        sig = src[src.index("def _apply_ai_config"):src.index("def reload_runtime_settings")]
        assert "veto_only" in sig, "配置签名没带这个开关，改了不会重建容灾链"

    def test_面板上有这个开关(self):
        html = (Path(__file__).resolve().parents[1] / "flask-version"
                / "templates" / "index.html").read_text(encoding="utf-8")
        assert "veto_only_match" in html, "界面上没有这个开关，用户改不了"
