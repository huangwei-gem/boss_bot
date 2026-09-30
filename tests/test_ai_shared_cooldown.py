# -*- coding: utf-8 -*-
"""并发下 AI 冷却表必须整个进程共享。

起因：2026-09-29 用演练模式真并发跑两个号，5 分钟里 33 次 HTTP 429，
Agnes-2 / SenseNova-DeepSeek-1 / SenseNova-DeepSeek-2 三个接口先后被冷却。
根因不是接口坏了，是冷却表 `AIAnalyzerChain._cooldown_until` 挂在每个实例上——
每个号各有一份，主号已经踩到限流了，账号2 完全不知道，接着再撞同一家服务商，
等于把一次限流打成两次，还把整条容灾链的可用接口一起烧进 5 分钟冷却。

429 也归错类了：它是"稍后再试"，旧代码按"其他错误"给 5 分钟，直接把一个
本来 60 秒就能恢复的接口从这一天的判分里剔掉了。
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
from tests.test_ai_failover import make_chain

RATE_429 = RuntimeError("API 请求失败: HTTP Error 429: Too Many Requests")
AUTH_401 = RuntimeError("API 请求失败: HTTP Error 401: Unauthorized")


@pytest.fixture(autouse=True)
def _clean_cooldown():
    from boss_bot.greet_engine import reset_shared_cooldown
    reset_shared_cooldown()
    yield
    reset_shared_cooldown()


def _provider(chain, i=0):
    return chain.providers[i]


class SharedCooldownTest:
    def test_两个号的链共用一张冷却表(self):
        from boss_bot.greet_engine import AIAnalyzerChain
        a = make_chain(2)
        b = make_chain(2)
        assert a._cooldown_until is b._cooldown_until, \
            "各存一份就是今天 33 次 429 的来源"

    def test_主号踩限流后账号2的链直接跳过同一接口(self):
        a, b = make_chain(2), make_chain(2)
        b._cool_down(_provider(b), RATE_429)
        assert _provider(a).name in a._cooldown_until, "跨号没共享，账号2 还会再撞一次"

    def test_任一号调用成功就替所有号解除冷却(self):
        a, b = make_chain(2), make_chain(2)
        a._cool_down(_provider(a, 0), RATE_429)
        b._cooldown_until.pop(_provider(b, 0).name, None)
        assert _provider(a, 0).name not in a._cooldown_until

    def test_同名接口才共享不同互不误伤(self):
        a, b = make_chain(1), make_chain(1)
        b._cool_down(_provider(b), RATE_429)
        assert len(a._cooldown_until) == 1


class CooldownAppliesAcrossAccountsTest:
    """表共用还不够，容灾循环真的得按它让路。

    23:35 那次并发里主号把 Agnes-2 打进 5 分钟冷却，19 秒后同一个号又试了一次，
    账号2 也在 15 秒后接着试 —— 要么冷却没写进去，要么循环没按它筛。
    """

    def test_主号打进冷却后另一个号一次都不碰同一接口(self):
        from unittest.mock import patch
        from boss_bot.greet_engine import AIAnalyzerChain
        from tests.test_ai_failover import JOB
        a, b = make_chain(2), make_chain(2)
        calls = []

        def fail(self, provider, prompt, *args, **kw):
            calls.append(provider.name)
            raise RATE_429

        with patch.object(AIAnalyzerChain, "_call_provider_api", fail):
            a.analyze_job(dict(JOB))
            assert len(a._cooldown_until) == 2, "两个接口都该进冷却"
            calls.clear()
            b.analyze_job(dict(JOB, url=JOB["url"] + "?b=1"))
        assert calls == [], f"冷却表没生效，账号2 又撞了 {calls}"

class CooldownBucketTest:
    def test_429只冷却一分钟左右不是五分钟(self):
        chain = make_chain(1)
        chain._cool_down(_provider(chain), RATE_429)
        left = chain._cooldown_until[_provider(chain).name] - time.time()
        assert 0 < left <= 90, f"429 冷却了 {left:.0f}s，太久了：它是稍后再试不是接口坏了"

    def test_鉴权类错误仍然长冷却(self):
        chain = make_chain(1)
        chain._cool_down(_provider(chain), AUTH_401)
        left = chain._cooldown_until[_provider(chain).name] - time.time()
        assert left > 600, f"401 只冷却 {left:.0f}s，这种错误不会自己好"

    def test_冷却日志要说清多久(self):
        msgs = []
        chain = make_chain(1)
        chain.log_cb = lambda m: msgs.append(m)      # 链的回调是单串，不是 (level, msg)
        chain._cool_down(_provider(chain), RATE_429)
        assert any("冷却" in m for m in msgs), msgs
