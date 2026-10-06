# -*- coding: utf-8 -*-
"""判分要给单接口设一个比体检更短的超时。

实测 2026-10-07 00:04-00:22：167 条打招呼记录里 41 条 ai_error，其中 30 条
照样把招呼语发出去了（盲投）。翻 data/ai_health.json 对得上原因——
AMD-DeepSeek / NVIDIA-GLM-5.3 / NVIDIA-Kimi 三家是 46s、50s 的请求超时，
判分链按服务商自己那 45 秒等，一轮下来卡在全网接口上，链子还没换到能用的一家
这一单就先按"默认通过"投出去了。

体检必须留 45 秒（慢但活着的服务商不能被误判成不可用，这是"只体检不重排"的前提），
所以判分单独用一个短上限。

但这个数只能从"判成功的那些用了多久"里取：两天日志 411 条成功判分的耗时是
p50 12.8s / p75 20.2s / p90 42.2s / p95 53.0s。第一版取 12 秒砍在一半上，
被自己掐死的那 56% 会记 strike（连续 2 次判该接口不可用），活着的一家家的被除名，
池子空了之后每个岗位 0.0 秒就报"所有 AI 接口均失败"、全按默认通过盲投——
比它要修的毛病更严重。默认因此定 20 秒（只削 42~135 秒那条尾巴）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boss_bot.greet_engine as ge  # noqa: E402
from boss_bot.unified_config import UnifiedConfig  # noqa: E402


class _记录超时:
    def __init__(self):
        self.timeouts = []

    def __call__(self, req, timeout=None):
        self.timeouts.append(timeout)
        raise OSError("故意断开，只看这次用了多少秒")


def _链(judge_timeout, provider_timeout=45, monkeypatch=None):
    chain = ge.AIAnalyzerChain(
        providers=[{"name": "测试家", "api_key": "k", "api_base": "https://x.invalid/v1",
                    "model": "m", "timeout": provider_timeout}],
        judge_timeout=judge_timeout,
    )
    probe = _记录超时()
    # _call_provider_api 里用的是模块级 urlopen，必须patch到模块上
    monkeypatch.setattr(ge, "urlopen", probe)
    return chain, probe


def _调用(chain):
    try:
        chain._call_provider_api(chain.providers[0], [{"role": "user", "content": "hi"}])
    except Exception:
        pass


def test_判分默认只等二十秒():
    assert UnifiedConfig().ai.judge_timeout == 20


def test_判分超时压住服务商的四十秒(monkeypatch):
    chain, probe = _链(judge_timeout=12, monkeypatch=monkeypatch)
    _调用(chain)
    assert probe.timeouts == [12], f"实际用了 {probe.timeouts}，全接口超时就是把这一单拖成盲投的原因"


def test_服务商自己更快时不放大(monkeypatch):
    chain, probe = _链(judge_timeout=12, provider_timeout=5, monkeypatch=monkeypatch)
    _调用(chain)
    assert probe.timeouts == [5], "上限只能收紧，不能把 5 秒放大成 12 秒"


def test_设零表示不另设上限(monkeypatch):
    chain, probe = _链(judge_timeout=0, provider_timeout=45, monkeypatch=monkeypatch)
    _调用(chain)
    assert probe.timeouts == [45]


def test_配置文件读写都带上这个值():
    cfg = UnifiedConfig.load(str(ROOT / "bot_config.json"))
    assert cfg.ai.judge_timeout >= 0
    dumped = cfg.to_dict()
    assert "judge_timeout" in dumped["ai"], "没有这一项，面板保存一次就把配置里的值抹掉"


def test_配置改动会重建分析器():
    """热重载比的是这份签名；judge_timeout 不在里面，改了就不生效。"""
    import inspect
    src = inspect.getsource(ge.GreetEngine._apply_ai_config)
    sig = src[src.index("sig = ("):src.index("_ai_config_sig")]
    assert "judge_timeout" in sig, "改了面板上的判分超时，运行中的引擎必须重建分析器才会听"


def test_面板三个端口都接上了():
    html = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="aiJudgeTimeout"' in html, "面板上得能改这个数"
    assert "setVal('aiJudgeTimeout'" in html, "回填：打开面板要看到盘上的真值"
    assert "aiJudgeTimeout\"" in html and "judge_timeout" in html, "收集：改了要写回配置"
