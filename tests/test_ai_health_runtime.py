"""AI 体检的运行时反馈

体检用的是 4 个字的短提示词，有些接口探活得很好、真岗位提示词却 30s 不回话。
这种接口在体检表里永远是"可用"，容灾链每个岗位都要白等它 30~60s，最后
"默认通过"，等于悄悄不筛岗。所以把真实调用的超时也记进体检表。
"""

import json

from types import SimpleNamespace


def _provider(name="慢接口"):
    return SimpleNamespace(name=name, model="m-slow", api_base="http://slow/v1")


class RuntimeHealthFeedbackTest:

    def _file(self, tmp_path, monkeypatch):
        from boss_bot import ai_health
        p = tmp_path / "ai_health.json"
        monkeypatch.setattr(ai_health, "HEALTH_FILE", p)
        monkeypatch.setattr(ai_health, "_runtime_strikes", {})
        return p

    def _entries(self, p):
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8")).get("results") or {}

    def test_一次超时不拉黑(self, tmp_path, monkeypatch):
        from boss_bot.ai_health import report_runtime_result
        p = self._file(tmp_path, monkeypatch)
        report_runtime_result(_provider(), False, "请求超时（30s）", path=p)
        assert self._entries(p) == {}, "偶发一次超时就把接口判死，太激进"

    def test_连续两次超时标成不可用(self, tmp_path, monkeypatch):
        from boss_bot.ai_health import report_runtime_result, STATUS_UNAVAILABLE
        p = self._file(tmp_path, monkeypatch)
        report_runtime_result(_provider(), False, "请求超时（30s）", path=p)
        report_runtime_result(_provider(), False, "请求超时（30s）", path=p)
        entries = self._entries(p)
        assert list(entries.values())[0]["status"] == STATUS_UNAVAILABLE
        assert "超时" in list(entries.values())[0]["reason"]

    def test_非超时错误不写运行时标记(self, tmp_path, monkeypatch):
        from boss_bot.ai_health import report_runtime_result
        p = self._file(tmp_path, monkeypatch)
        report_runtime_result(_provider(), False, "401 Unauthorized", path=p)
        report_runtime_result(_provider(), False, "401 Unauthorized", path=p)
        assert self._entries(p) == {}, "鉴权类错误由体检和冷却表负责，不该冒充体检结论"

    def test_真成功后撤销运行时标记(self, tmp_path, monkeypatch):
        from boss_bot.ai_health import report_runtime_result
        p = self._file(tmp_path, monkeypatch)
        report_runtime_result(_provider(), False, "请求超时（30s）", path=p)
        report_runtime_result(_provider(), False, "请求超时（30s）", path=p)
        assert self._entries(p)
        report_runtime_result(_provider(), True, path=p)
        assert self._entries(p) == {}

    def test_探活结论不会被运行时成功抹掉(self, tmp_path, monkeypatch):
        """运行时成功只撤销"运行时判的死刑"，体检自己测出来的结论要留着"""
        from boss_bot.ai_health import report_runtime_result, provider_key
        p = self._file(tmp_path, monkeypatch)
        prov = _provider()
        probe_entry = {"name": prov.name, "model": prov.model,
                       "api_base": prov.api_base, "status": "unavailable",
                       "reason": "API Key 无效或已过期", "latency_ms": 10}
        p.write_text(json.dumps({"results": {provider_key(probe_entry): probe_entry}}),
                     encoding="utf-8")
        report_runtime_result(prov, True, path=p)
        assert self._entries(p)[provider_key(probe_entry)]["status"] == "unavailable"


class ChainSkipsSlowProviderTest:
    """容灾链：慢接口被标记后，下一个岗位直接用好接口"""

    def _chain(self, tmp_path, monkeypatch):
        from boss_bot import ai_health
        from boss_bot.greet_engine import AIAnalyzerChain
        p = tmp_path / "ai_health.json"
        monkeypatch.setattr(ai_health, "HEALTH_FILE", p)
        monkeypatch.setattr(ai_health, "_runtime_strikes", {})
        monkeypatch.setattr("boss_bot.greet_engine.AI_CACHE_FILE",
                            tmp_path / "ai_cache.json")
        slow = SimpleNamespace(name="慢接口", api_key="k", api_base="http://slow/v1",
                               model="m-slow", timeout=30)
        fast = SimpleNamespace(name="快接口", api_key="k", api_base="http://fast/v1",
                               model="m-fast", timeout=30)
        chain = AIAnalyzerChain([slow, fast], match_threshold=60)
        chain._resume_hash = "h"      # 打开缓存路径，和真实运行一致

        def fake_call(provider, messages):
            if provider.name == "慢接口":
                raise Exception("请求超时（30s）")
            return {"score": 88, "is_match": True, "reason": "技能吻合",
                    "suggested_greeting": "你好"}

        monkeypatch.setattr(chain, "_call_provider_api", fake_call)
        return chain, p

    def _job(self, url="https://www.zhipin.com/job_detail/1.html"):
        return {"job_name": "数据分析师", "url": url, "company": "x",
                "description": "SQL 取数", "requirements": "本科"}

    def test_两次超时后才写进体检表(self, tmp_path, monkeypatch):
        chain, p = self._chain(tmp_path, monkeypatch)
        first = chain.analyze_job(self._job("u1"))
        assert first.get("score") == 88 and not first.get("ai_error"), \
            "第一个岗位该拿到好接口的判断"
        assert not p.exists(), "只超时一次就写死刑太激进"

        # 5 分钟冷却到期后再次真打，第二次超时才落到体检表
        chain._cooldown_until.clear()
        second = chain.analyze_job(self._job("u2"))
        assert second.get("score") == 88
        entries = json.loads(p.read_text(encoding="utf-8")).get("results") or {}
        assert any("超时" in (e.get("reason") or "") for e in entries.values()), \
            "慢接口没被回写进体检表"

    def test_超时接口在冷却期内不再白等(self, tmp_path, monkeypatch):
        chain, _ = self._chain(tmp_path, monkeypatch)
        calls = []
        real = chain._call_provider_api

        def spy(provider, messages):
            calls.append(provider.name)
            return real(provider, messages)

        monkeypatch.setattr(chain, "_call_provider_api", spy)
        chain.analyze_job(self._job("u1"))     # 慢接口超时一次 + 快接口成功
        chain.analyze_job(self._job("u2"))     # 运行时标记 + 冷却都该生效
        chain.analyze_job(self._job("u3"))
        assert calls.count("慢接口") == 1, f"慢接口被反复调用: {calls}"

    def test_全部接口都失败时不跳过任何接口(self, tmp_path, monkeypatch):
        """宁可慢，也不能因为体检说"都坏了"就完全不筛岗"""
        from boss_bot import ai_health
        from boss_bot.greet_engine import AIAnalyzerChain
        p = tmp_path / "ai_health.json"
        monkeypatch.setattr(ai_health, "HEALTH_FILE", p)
        prov = SimpleNamespace(name="唯一接口", api_key="k", api_base="http://a/v1",
                               model="m", timeout=30)
        p.write_text(json.dumps({"updated_at": "2026-09-28 10:00:00", "results": {
            "http://a/v1|m|唯一接口": {"name": "唯一接口", "model": "m",
                                       "api_base": "http://a/v1",
                                       "status": "unavailable", "reason": "超时"}}}),
            encoding="utf-8")
        chain = AIAnalyzerChain([prov], match_threshold=60, cache_enabled=False)
        called = []

        def fake(provider, messages):
            called.append(provider.name)
            return {"score": 70, "is_match": True, "reason": "ok"}

        monkeypatch.setattr(chain, "_call_provider_api", fake)
        out = chain.analyze_job({"job_name": "x", "url": "u"})
        assert called == ["唯一接口"]
        assert out["score"] == 70
