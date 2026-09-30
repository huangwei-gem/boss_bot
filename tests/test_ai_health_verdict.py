# -*- coding: utf-8 -*-
"""AI 体检的判词与重试：别把一把好着的 Key 判死。

2026-09-30 用户要"全面排查 AI 接口失效"。实测结论是接口没坏：
`python tools/ai_health_check.py --timeout 25` 报 2 条不可用（AMD-DeepSeek-V4.1
"额度用尽或被限流"、AMD-Qwen3.8 "请求超时"），而 `tools/diagnose_ai_providers.py`
同请求两种网络各打 4 次全是 200；用默认超时重跑，8/8 全可用。
两条根因都写在下面这几条测试里：① 命令行默认 20 秒，比推理模型正常回话还短
（同一轮里 Agnes-2.5-Flash 用了 21.7 秒）；② 判到 429 就一次定罪，而 429 是频率，
等十几秒就回来 —— 混在"额度用尽"里还会把人引去换一把好 Key。
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boss_bot.ai_health as AH
from boss_bot.ai_health import PROBE_TIMEOUT, RETRY_AFTER_RATE_LIMIT, probe_one

PROVIDER = {"name": "AMD-DeepSeek-V4.1", "model": "DeepSeek-V4.1-Flash",
            "api_base": "https://example.test/v1", "api_key": "k"}
RATE_LIMIT_MSG = ("Error code: 429 - {'detail': {'error': {'message': "
                  "'Too many requests for model', 'type': 'rate_limit_exceeded'}}}")


def _fake_openai(raises_first, sink):
    """造一个只在第一次抛 429 的假 OpenAI：体检的真实形状不用网络也能验"""

    class Completions:
        def create(self, **kwargs):
            sink.append(1)
            if raises_first and len(sink) == 1:
                raise RuntimeError(RATE_LIMIT_MSG)
            msg = types.SimpleNamespace(content="连接成功",
                                         reasoning_content=None, reasoning=None)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=Completions())

    return FakeOpenAI


class RateLimitRetryTest:
    def test_429要等一会重打一次而不是直接判死(self, monkeypatch):
        import openai
        calls, slept = [], []
        monkeypatch.setattr(openai, "OpenAI", _fake_openai(True, calls))
        monkeypatch.setattr(AH.time, "sleep", lambda s: slept.append(s))
        out = probe_one(PROVIDER, timeout=5)
        assert out["status"] == AH.STATUS_AVAILABLE, out
        assert len(calls) == 2, "一次 429 就定罪，运行时容灾链会白白少用一个接口"
        assert slept and slept[0] >= RETRY_AFTER_RATE_LIMIT, slept

    def test_鉴权失败不用重打(self, monkeypatch):
        """401/Key 无效重打没意义，多等一次只是拖长体检"""
        import openai
        calls = []

        class FakeOpenAI:
            def __init__(self, **kwargs):
                pass

            chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **kw: (_ for _ in ()).throw(
                        RuntimeError("Error code: 401 - invalid api key"))))

        monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
        monkeypatch.setattr(AH.time, "sleep", lambda s: None)
        out = probe_one(PROVIDER, timeout=5)
        assert out["status"] == AH.STATUS_UNAVAILABLE
        assert not calls, "401 也走重试说明没分清哪些错误值得再打"


class ReasonWordingTest:
    def test_限流不能写成要换key(self):
        reason = AH.classify_error(RATE_LIMIT_MSG)
        assert "429" in reason and "不是 Key" in reason, reason

    def test_额度用尽单独一句(self):
        assert AH.classify_error("insufficient quota") == "额度用尽（需要充值或换 Key）"


class TimeoutFloorTest:
    def test_模块默认超时容得下推理模型(self):
        """实测推理模型正常回话要 21.7 秒，阈值低于这个就会把好接口测成超时"""
        assert PROBE_TIMEOUT >= 40, PROBE_TIMEOUT

    def test_命令行不再自带更短的默认(self):
        src = (ROOT / "tools" / "ai_health_check.py").read_text(encoding="utf-8")
        assert "default=PROBE_TIMEOUT" in src, "命令行又写了个更小的秒数"
        assert "default=20" not in src
