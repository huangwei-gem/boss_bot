# -*- coding: utf-8 -*-
"""AI 判分链路的真实行为测试。

起因是一次实测（tools/measure_ai_quality.py，2026-09-28）：22 个接口里
AskDiandian-Dots3 因为 enable_thinking 把 1024 token 预算全花在思考上，
正文为空、finish_reason=length —— 而旧代码把这种输出当成"成功"返回兜底 dict，
容灾链既没换接口，还把这个坏接口回报成健康。结果岗位静默"默认通过"，
界面上完全看不出来 AI 没筛过。

这里锁定的就是这条路径：拿不到可信判断必须抛异常换接口，
截断要认出来，兜底要留痕。
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def payload(content="", finish="stop", reasoning=None, extra=None):
    msg = {"content": content}
    if reasoning is not None:
        msg["reasoning_content"] = reasoning
    body = {"choices": [{"message": msg, "finish_reason": finish}]}
    if extra:
        body.update(extra)
    return body


class FakeResponse:
    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def make_chain(n=1, **kw):
    from boss_bot.greet_engine import AIAnalyzerChain
    providers = [{"name": f"p{i}", "api_key": f"k{i}",
                  "api_base": f"https://h{i}/v1", "model": f"m{i}"} for i in range(n)]
    kw.setdefault("cache_enabled", False)
    chain = AIAnalyzerChain(providers=providers, **kw)
    # 体检回写会落 data/ai_health.json，测试绝不碰生产文件（有专门的用例单独验证）
    chain._report_health = lambda *a, **k: None
    return chain


def call_once(chain, provider_index=0, body=None, capture=None):
    """用固定响应打一次 _call_provider_api，返回 (结果, 异常)"""
    from boss_bot.greet_engine import AIProviderConfig
    provider = chain.providers[provider_index]
    sent = {}

    class _Req:
        def __init__(self, url, data=None, method=None):
            sent["url"] = url
            sent["data"] = json.loads(data.decode("utf-8")) if data else {}

        def add_header(self, *a):
            pass

    import boss_bot.greet_engine as GE
    real_req = GE.Request
    GE.Request = _Req
    try:
        with patch("boss_bot.greet_engine.urlopen", return_value=FakeResponse(body)):
            try:
                result = chain._call_provider_api(provider, [{"role": "user", "content": "x"}])
                return result, None, sent
            except Exception as e:
                return None, e, sent
    finally:
        GE.Request = real_req


JOB = {"job_name": "数据分析师", "salary": "9-14K", "company": "某公司",
       "url": "https://www.zhipin.com/job_detail/t.html",
       "description": "负责取数与看板", "requirements": "会 SQL"}


class UnusableOutputMustRaiseTest:
    """解析不出可信判断时不能返回"默认通过"的 dict —— 那会中止容灾"""

    def test_思考耗尽预算正文为空要报截断(self):
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload("", finish="length",
                                                    reasoning="" * 100))
        assert err is not None, f"截断输出被当成成功：{res}"
        assert "截断" in str(err)

    def test_空正文没有任何内容要报未返回正文(self):
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload(""))
        assert err is not None
        assert "未返回正文" in str(err)

    def test_正文只有寒暄没有JSON要报没有JSON(self):
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload("这个岗位总体还不错，值得投递。"))
        assert err is not None
        assert "没有 JSON" in str(err)

    def test_JSON里既没score也没is_match算无效输出(self):
        """旧行为：json.loads 成功就返回，score 缺失→调用方按 50 处理→岗位被误判不匹配"""
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload('{"reason": "还行", "strengths": []}'))
        assert err is not None, f"没有判分的 JSON 被接受了：{res}"

    def test_响应缺少choices要报缺字段(self):
        chain = make_chain()
        res, err, _ = call_once(chain, body={"code": 429, "msg": "rate limited"})
        assert err is not None
        assert "缺少字段" in str(err)


class UsableOutputTest:
    """能判分的输出要稳：代码块、尾随说明、缺字段钳位都不能翻车"""

    def test_纯JSON(self):
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload('{"score": 80, "is_match": true}'))
        assert err is None and res["score"] == 80

    def test_markdown代码块(self):
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload(
            '```json\n{"score": 66, "is_match": false, "reason": "要三年经验"}\n```'))
        assert err is None and res["score"] == 66

    def test_JSON后面夹带中文说明(self):
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload(
            '好的，分析如下：\n{"score": 91, "is_match": true}\n希望对你有帮助！'))
        assert err is None and res["score"] == 91

    def test_尾随文字里的右花括号不能把JSON切坏(self):
        """find("{")..rfind("}") 会把 "详见附录}" 之后一起切进来 → JSONDecodeError"""
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload(
            '{"score": 75, "is_match": true, "reason": "技能匹配"}\n'
            '（补充：如果考虑加班情况，权重另算}）'))
        assert err is None, f"解析被尾随的右花括号带偏：{err}"
        assert res["score"] == 75

    def test_缺is_match时按阈值推导(self):
        chain = make_chain(match_threshold=70)
        res, err, _ = call_once(chain, body=payload('{"score": 55, "reason": "跨度大"}'))
        assert err is None
        assert res["is_match"] is False
        chain2 = make_chain(match_threshold=70)
        res2, _, _ = call_once(chain2, body=payload('{"score": 88}'))
        assert res2["is_match"] is True

    def test_reasoning兜底只在正文为空且没截断时用(self):
        chain = make_chain()
        res, err, _ = call_once(chain, body=payload(
            "", finish="stop", reasoning='{"score": 40, "is_match": false}',
        ))
        assert err is None and res["score"] == 40


    def test_解析逻辑可单独调用_诊断脚本用同一份(self):
        """tools/measure_ai_quality.py 必须走生产解析，别自己再实现一套判断"""
        chain = make_chain()
        choice = {"message": {"content": "", "reasoning_content": "思考" * 300},
                  "finish_reason": "length"}
        with pytest.raises(Exception) as ei:
            chain._parse_completion(choice)
        assert "截断" in str(ei.value)
        ok = chain._parse_completion({"message": {"content": '{"score":77,"is_match":true}'},
                                      "finish_reason": "stop"})
        assert ok["score"] == 77


class FailoverTest:
    """坏输出必须换接口，好输出才算成功"""

    def _chain_with_bodies(self, bodies, **kw):
        chain = make_chain(n=len(bodies), **kw)
        it = iter(bodies)
        with patch("boss_bot.greet_engine.urlopen",
                   side_effect=lambda *a, **k: FakeResponse(next(it))):
            result = chain.analyze_job(JOB)
        return chain, result

    def test_第一个接口截断后第二个接口拿到真判断(self):
        chain, result = self._chain_with_bodies([
            payload("", finish="length", reasoning="思考" * 200),
            payload('{"score": 82, "is_match": true, "reason": "对口"}'),
        ])
        assert not result.get("ai_error"), f"没能容灾到第二个接口：{result}"
        assert result["score"] == 82
        assert chain.last_model_name == "m1"

    def test_坏输出接口要被冷却且不被回报成健康(self):
        reported = []
        chain = make_chain(n=2)
        with patch("boss_bot.greet_engine.urlopen", side_effect=[
                FakeResponse(payload("这里没有 JSON")),
                FakeResponse(payload('{"score": 82, "is_match": true}'))]), \
             patch.object(chain, "_report_health",
                          side_effect=lambda p, ok, error="": reported.append((p.name, ok))):
            chain.analyze_job(JOB)
        assert "p0" in chain._cooldown_until, "坏接口没进冷却，下个岗位还会踩"
        assert reported == [("p0", False), ("p1", True)], f"健康回报搞反了：{reported}"

    def test_全部接口都坏才落到默认通过且带上最后一次原因(self):
        chain, result = self._chain_with_bodies([
            payload("没有 JSON 的散文"),
            payload("", finish="length", reasoning="思考" * 200),
        ])
        assert result["ai_error"] is True
        assert result["is_match"] is True
        assert "截断" in result["reason"] or "没有 JSON" in result["reason"]

    def test_兜底不计入判分成功(self):
        chain = make_chain(n=1)
        with patch("boss_bot.greet_engine.urlopen",
                   return_value=FakeResponse(payload("散文"))):
            chain.analyze_job(JOB)
        assert chain.analyzed_count == 0
        assert chain.fallback_count == 1

    def test_兜底时不留下上一个岗位的品牌(self):
        """last_model_name 不清零会把上一个接口的名字安到兜底记录上，by_model 就骗人。
        原始返回要留着 —— 兜底那条记录能看出接口到底回了什么。"""
        chain = make_chain(n=2)
        with patch("boss_bot.greet_engine.urlopen", side_effect=[
                FakeResponse(payload('{"score": 82, "is_match": true}')),
                FakeResponse(payload("散文")),
                FakeResponse(payload("还是散文"))]):
            chain.analyze_job(JOB)
            second = chain.analyze_job(dict(JOB, url="https://www.zhipin.com/job_detail/2.html"))
        assert second["ai_error"] is True
        assert chain.last_model_name == ""
        assert "散文" in (chain.last_raw_response or "")


class MaxTokensTest:
    """预算要能配：推理模型 1024 token 会被 thinking 吃光"""

    def test_请求体带chain的max_tokens(self):
        chain = make_chain(analyze_max_tokens=1800)
        _, _, sent = call_once(chain, body=payload('{"score": 80}'))
        assert sent["data"]["max_tokens"] == 1800

    def test_默认预算比1024宽裕(self):
        """实测 1024 会让带 thinking 的接口稳定截断"""
        chain = make_chain()
        assert chain.analyze_max_tokens > 1024

    def test_配置项读写往返(self):
        import tempfile
        from boss_bot.unified_config import UnifiedConfig
        cfg = UnifiedConfig()
        assert cfg.ai.analyze_max_tokens == chain_default()
        cfg.ai.analyze_max_tokens = 2048
        d = cfg.to_dict()
        assert d["ai"]["analyze_max_tokens"] == 2048
        path = tempfile.mkdtemp() + "/bot_config.json"
        cfg.save(path)
        assert UnifiedConfig.load(path).ai.analyze_max_tokens == 2048

    def test_引擎把配置传给容灾链(self):
        from boss_bot.greet_engine import GreetEngine
        import inspect
        src = inspect.getsource(GreetEngine._init_ai)
        assert "analyze_max_tokens" in src, "_init_ai 没把预算配置接到容灾链上"

    def test_引擎按配置重建容灾链(self):
        """改完预算要真的生效，不能只在界面上存一份"""
        from boss_bot.greet_engine import GreetEngine
        e = GreetEngine.__new__(GreetEngine)
        e._ai_analyzer = None
        e._ai_enabled = True
        e._ai_providers = [{"name": "p", "api_key": "k", "api_base": "https://a/v1",
                           "model": "m"}]
        e._ai_threshold = 70
        e._ai_skip_unhealthy = False
        e._ai_custom_filter_keywords = []
        e._ai_custom_scoring_prompt = ""
        e._resume_cfg = {}
        e._analyze_max_tokens = 1900
        e._log = lambda *a: None
        chain = e._init_ai()
        assert chain.analyze_max_tokens == 1900

    def test_运行中改预算下一个岗位就用新值(self):
        """热重载换了 config 对象，容灾链不能还停在首次启动时的快照"""
        from boss_bot.greet_engine import GreetEngine
        from boss_bot.unified_config import UnifiedConfig
        e = GreetEngine.__new__(GreetEngine)
        e._ai_analyzer = None
        e._ai_config_sig = None
        e._log = lambda *a: None
        e._resume_cfg = {}
        cfg = UnifiedConfig()
        cfg.ai.enabled = True
        cfg.ai.analyze_max_tokens = 1200
        cfg.ai.providers = []
        e.config = cfg
        e._apply_ai_config()
        assert e._analyze_max_tokens == 1200
        # 建过一次链之后再改配置
        e._ai_analyzer = object()
        cfg2 = UnifiedConfig()
        cfg2.ai.enabled = True
        cfg2.ai.analyze_max_tokens = 2400
        cfg2.ai.providers = []
        e.config = cfg2
        e._apply_ai_config()
        assert e._ai_analyzer is None, "配置改了却没重建容灾链"
        assert e._analyze_max_tokens == 2400

    def test_配置没变时不重建链路保留冷却表(self):
        from boss_bot.greet_engine import GreetEngine
        from boss_bot.unified_config import UnifiedConfig
        e = GreetEngine.__new__(GreetEngine)
        e._ai_analyzer = None
        e._ai_config_sig = None
        e._log = lambda *a: None
        cfg = UnifiedConfig()
        cfg.ai.providers = []
        e.config = cfg
        e._apply_ai_config()
        sentinel = object()
        e._ai_analyzer = sentinel
        e._apply_ai_config()          # 同一份配置再读一次
        assert e._ai_analyzer is sentinel, "配置没变也重建，冷却表被清空"


def chain_default():
    from boss_bot.unified_config import AIConfig
    return AIConfig().analyze_max_tokens


class RuntimeHealthTruncationTest:
    """截断是接口级稳定问题，两次就该在体检表里标出来"""

    def test_连续两次截断标记不可用(self, tmp_path):
        from boss_bot.ai_health import report_runtime_result, load_health, STATUS_UNAVAILABLE

        class P:
            name, model, api_base = "截断怪", "dots3", "https://askdiandian/v1"

        path = tmp_path / "ai_health.json"
        report_runtime_result(P, ok=False, error="正文被截断（thinking 用满 max_tokens=1024）",
                              path=path)
        report_runtime_result(P, ok=False, error="正文被截断（thinking 用满 max_tokens=1024）",
                              path=path)
        results = load_health(path).get("results") or {}
        hit = list(results.values())
        assert hit and hit[0]["status"] == STATUS_UNAVAILABLE
        assert "截断" in hit[0]["reason"]

    def test_普通解析失败一次不标记(self, tmp_path):
        from boss_bot.ai_health import report_runtime_result, load_health

        class P:
            name, model, api_base = "偶发", "m", "https://x/v1"

        path = tmp_path / "ai_health2.json"
        report_runtime_result(P, ok=False, error="响应里没有 JSON（前 60 字：好的）", path=path)
        assert not (load_health(path).get("results") or {})


class ReplyBudgetTest:
    """回复侧：容灾预算必须真的生效，否则一条消息最坏串行试 22 个接口"""

    def _engine(self, n=22):
        from boss_bot.reply_engine import ReplyEngine
        import boss_bot.reply_engine as RE
        e = ReplyEngine.__new__(ReplyEngine)
        e._cache = type("C", (), {"get": lambda *a: None, "set": lambda *a: None})()
        e._ai_providers = [{"key": "k", "model": f"m{i}", "url": "https://h/v1"}
                           for i in range(n)]
        e._ai_max_tokens = 200
        e._ai_rate_limit_wait = 0
        e._last_ai_system_prompt = None
        e._last_ai_user_prompt = None
        e._last_ai_model = ""
        e._last_ai_raw_response = None
        return e, RE

    def test_最多尝试AI_MAX_ATTEMPTS个接口(self):
        from boss_bot.reply_engine import AI_MAX_ATTEMPTS
        e, RE = self._engine(22)
        tried = []
        with patch.object(e, "_call_with_rate_limit_retry",
                          side_effect=lambda c, m, msg, b, j, h, name: tried.append(name)):
            reply = e._ask_ai("薪资多少", "王经理", "数据分析师")
        assert len(tried) == AI_MAX_ATTEMPTS, f"预算没生效，试了 {len(tried)} 个接口"
        assert reply is None

    def test_成功就立刻返回不再试(self):
        e, RE = self._engine(22)
        tried = []

        def fake(client, model, msg, b, j, h, name):
            tried.append(name)
            return "看经验定" if len(tried) == 2 else None

        with patch.object(e, "_call_with_rate_limit_retry", side_effect=fake):
            reply = e._ask_ai("薪资多少", "王经理", "数据分析师")
        assert reply == "看经验定"
        assert len(tried) == 2

    def test_超时间预算就停手(self):
        e, RE = self._engine(22)
        tried = []
        clock = [1000.0]

        def now():
            clock[0] += 60.0
            return clock[0]

        with patch.object(e, "_call_with_rate_limit_retry",
                          side_effect=lambda *a: tried.append(a)), \
             patch.object(RE.time, "time", side_effect=now):
            e._ask_ai("在吗", "李小姐", "运营")
        assert len(tried) <= AI_MAX_ATTEMPTS_DEFAULT()


def AI_MAX_ATTEMPTS_DEFAULT():
    from boss_bot.reply_engine import AI_MAX_ATTEMPTS
    return AI_MAX_ATTEMPTS


class HardVetoTest:
    """自定义筛选条件是"必须遵守"，不是"适当扣分" """

    def test_提示词把关键词写成硬否决(self):
        chain = make_chain(custom_filter_keywords=["不要外包", "只看长沙"])
        msgs = chain._build_prompt(JOB)
        text = msgs[0]["content"] + msgs[1]["content"]
        assert "不要外包" in text
        assert "必须" in text and "is_match" in text
        assert "适当扣分" not in text, "还在用软约束措辞"

    def test_AI报了命中硬条件就直接不匹配(self):
        """模型给 90 分但同时承认命中否决条件 → 否决优先，不能当成匹配"""
        chain = make_chain(match_threshold=50, custom_filter_keywords=["不要外包", "只看长沙"])
        with patch("boss_bot.greet_engine.urlopen", return_value=FakeResponse(payload(
                '{"score": 90, "is_match": true, "reason": "岗位不错", '
                '"veto_hit": "不要外包"}'))):
            res = chain.analyze_job(JOB)
        assert res["is_match"] is False
        assert res["veto_hit"] == "不要外包"

    def test_没有命中硬条件时不影响判分(self):
        chain = make_chain(match_threshold=50, custom_filter_keywords=["不要外包"])
        with patch("boss_bot.greet_engine.urlopen", return_value=FakeResponse(payload(
                '{"score": 90, "is_match": true, "veto_hit": ""}'))):
            res = chain.analyze_job(JOB)
        assert res["is_match"] is True


class ReplyEmptyBodyTest:
    """回复侧：接口把话写在 reasoning_content 或干脆空着时不能崩在 .strip()"""

    def _call(self, content, reasoning=None):
        from boss_bot.reply_engine import ReplyEngine
        e = ReplyEngine.__new__(ReplyEngine)
        e._ai_max_tokens = 200
        e._last_ai_system_prompt = None
        e._last_ai_user_prompt = None
        e._last_ai_model = ""
        e._last_ai_raw_response = None

        class Msg:
            def __init__(self):
                self.content = content
                self.reasoning_content = reasoning

        resp = type("R", (), {"choices": [type("C", (), {"message": Msg()})()]})()
        client = type("Cl", (), {"chat": type("Ch", (), {
            "completions": type("Co", (), {"create": staticmethod(lambda **kw: resp)})(
            )})(
        )})()
        return e, client

    def test_正文为空时用思考内容(self):
        e, client = self._call(None, "  您好，薪资可以谈  ")
        with patch("boss_bot.reply_engine.build_user_prompt", return_value="up"):
            assert e._call_chat(client, "m", "薪资多少", "王经理", "数据分析师", []) == "您好，薪资可以谈"

    def test_正文和思考都空要报错换接口(self):
        e, client = self._call("", None)
        with patch("boss_bot.reply_engine.build_user_prompt", return_value="up"):
            with pytest.raises(Exception) as ei:
                e._call_chat(client, "m", "在吗", "李小姐", "运营", [])
        assert "没有回复正文" in str(ei.value)


def _write(path, **kv):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(kv)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class PromptHotReloadTest:
    """前端改了 AI 提示词/画像，下一条回复就该用新的 —— 不该等重启。

    旧实现在 import 时把 system_rules、user_prompt_template、个人画像各快照
    一份常量，而 /api/prompts 与 /api/user_profile 只改文件，于是提示词卡片上
    编辑的内容永远进不了真实请求。
    """

    @pytest.fixture
    def isolated(self, tmp_path, monkeypatch):
        import boss_bot.prompts as P
        overrides = tmp_path / "config_overrides.json"
        profile = tmp_path / "user_profile.json"
        overrides.write_text("{}", encoding="utf-8")
        profile.write_text(json.dumps({"position": "数据分析师", "education": "本科"},
                                      ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(P, "OVERRIDES_FILE", overrides)
        monkeypatch.setattr(P, "USER_PROFILE_FILE", profile)
        return P, overrides, profile

    def test_改完system_rules立刻生效(self, isolated):
        P, overrides, _ = isolated
        assert "语气专业" in P.build_system_prompt()
        _write(overrides, system_rules="只允许回复两个字：好的")
        assert "只允许回复两个字" in P.build_system_prompt()

    def test_改完画像立刻生效(self, isolated):
        P, _, profile = isolated
        assert "数据分析师" in P.build_system_prompt()
        profile.write_text(json.dumps({"position": "运营专员", "education": "本科"},
                                      ensure_ascii=False), encoding="utf-8")
        assert "运营专员" in P.build_system_prompt()

    def test_改完user_prompt模板立刻生效(self, isolated):
        P, overrides, _ = isolated
        assert "最近对话记录" in P.build_user_prompt("王经理", "数据分析", "薪资多少", [])
        _write(overrides, user_prompt_template="对方:{message}|{boss_name}")
        assert P.build_user_prompt("王经理", "数据分析", "薪资多少", []) == "对方:薪资多少|王经理"

    def test_回复引擎每次调用都现取提示词(self):
        """reply_engine 不能 import 时抓一份常量就完事"""
        import inspect
        import boss_bot.reply_engine as RE
        src = inspect.getsource(RE.ReplyEngine._call_chat)
        assert "build_system_prompt(" in src, "回复侧还在用 import 时的提示词常量"


def _write(path, **kv):
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(kv)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class PromptDefaultsTest:
    """界面上的"默认提示词"必须就是引擎真正在用的那份。

    app.py 与 index.html 各自抄了一份只有 10 条的规则，而 prompts.py 的默认值
    有 20 条（含"必须先分析完整对话上下文""被拒绝后别再推销"这些关键约束）。
    点一次「恢复默认」就会把削弱版写进 overrides，之后每条回复都在用弱化规则。
    """

    def test_后端默认值来自引擎(self):
        import pathlib
        src = pathlib.Path("flask-version/app.py").read_text(encoding="utf-8")
        assert "_SYSTEM_RULES_DEFAULT" in src and "_USER_PROMPT_DEFAULT" in src
        assert '_DEFAULT_SYSTEM_RULES = """你的回复要求' not in src, "app.py 里还留着第二份抄本"

    def test_前端不再自带默认规则抄本(self):
        import pathlib
        html = pathlib.Path("flask-version/templates/index.html").read_text(encoding="utf-8")
        assert "const DEFAULT_SYSTEM_RULES" not in html
        assert "const DEFAULT_USER_PROMPT_TEMPLATE" not in html

    def test_恢复默认用后端返回的defaults(self):
        import pathlib
        html = pathlib.Path("flask-version/templates/index.html").read_text(encoding="utf-8")
        assert ".defaults" in html, "「恢复默认」没走后端默认值"


class AiQualityApiTest:
    """/api/ai/quality：把"AI 到底筛没筛"变成看得见的数"""

    def test_路由存在且按数据范围统计(self):
        import pathlib
        src = pathlib.Path("flask-version/app.py").read_text(encoding="utf-8")
        assert '@app.route("/api/ai/quality"' in src
        m = src.index('@app.route("/api/ai/quality"')
        assert "quality_stats(" in src[m:m + 800]

    def test_界面展示兜底率(self):
        import pathlib
        html = pathlib.Path("flask-version/templates/index.html").read_text(encoding="utf-8")
        assert "/api/ai/quality" in html
        assert "兜底" in html


class AnalyzeBudgetUiTest:
    """新增的输入框必须真的写回配置并被引擎读走"""

    def test_前端保存analyze_max_tokens(self):
        import pathlib
        html = pathlib.Path("flask-version/templates/index.html").read_text(encoding="utf-8")
        assert "analyze_max_tokens" in html
        assert "aiAnalyzeMaxTokens" in html


class FlaskAiEndpointTest:
    """接口层跑通：新增的输入框和判分质量面板不是摆设"""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        import sys
        from unittest.mock import MagicMock
        from boss_bot import unified_config as UC
        monkeypatch.setattr(UC, "BOT_CONFIG_FILE", tmp_path / "bot_config.json")
        monkeypatch.setattr(UC, "OVERRIDES_FILE", tmp_path / "config_overrides.json")
        monkeypatch.setattr(UC, "USER_PROFILE_FILE", tmp_path / "user_profile.json")

        flask_dir = str(Path(__file__).parent.parent / "flask-version")
        if flask_dir not in sys.path:
            sys.path.insert(0, flask_dir)
        with patch("boss_bot.main_loop.BrowserManager"), patch("app.UnifiedBotLoop"):
            import app as FLASK_APP
            FLASK_APP._config = None
            FLASK_APP.app.config["TESTING"] = True
            with FLASK_APP.app.test_client() as c:
                yield c

    def test_保存判分预算能读回(self, client):
        body = {"accounts": [{"name": "主账号", "enabled": True,
                              "cookie_file": "zhipin_cookies.json",
                              "jobs": [{"query": "数据分析", "city": "长沙",
                                        "enabled": True, "scroll_pages": 5,
                                        "greeting_message": "你好"}]}],
                "ai": {"enabled": True, "analyze_max_tokens": 1900}}
        r = client.post("/api/config", json=body)
        assert r.status_code == 200, r.get_json()
        got = client.get("/api/config").get_json()["config"]["ai"]
        assert got["analyze_max_tokens"] == 1900

    def test_判分质量接口按记录统计(self, client):
        from boss_bot.reply_record import _get_greet_store, GreetRecord
        store = _get_greet_store()
        store.clear()
        store.add(GreetRecord(job_name="真判分", ai_score=88, ai_model="m-a",
                              ai_duration_ms=5200))
        store.add(GreetRecord(job_name="兜底", ai_score=50, ai_error=True,
                              ai_model="m-a", ai_duration_ms=61000))
        d = client.get("/api/ai/quality").get_json()
        assert d["status"] == "ok"
        assert d["total"] == 2 and d["judged"] == 1 and d["fallback"] == 1
        assert d["fallback_rate"] == 0.5
        assert d["avg_duration_ms"] == 33100
        assert d["by_model"]["m-a"] == {"total": 2, "fallback": 1}

    def test_提示词接口带默认值(self, client):
        from boss_bot.prompts import _SYSTEM_RULES_DEFAULT
        d = client.get("/api/ai/prompts").get_json()
        assert d["defaults"]["system_rules"] == _SYSTEM_RULES_DEFAULT
        # 默认值必须是引擎那份 20 条的，不是界面上的 10 条抄本
        assert "必须根据完整对话上下文回复" in d["defaults"]["system_rules"]


class RecordQualityFieldsTest:
    """兜底要留痕：记录里能看出这条是不是 AI 真判的、花了多久"""

    def test_记录带ai_error与耗时(self):
        from boss_bot.reply_record import GreetRecord
        r = GreetRecord(job_name="x", ai_score=50, ai_error=True, ai_duration_ms=61000)
        d = r.to_dict()
        assert d["ai_error"] is True
        assert d["ai_duration_ms"] == 61000
        r2 = GreetRecord.from_dict(d)
        assert r2.ai_error is True and r2.ai_duration_ms == 61000

    def test_老记录没有这两个字段也不炸(self):
        from boss_bot.reply_record import GreetRecord
        r = GreetRecord.from_dict({"job_name": "老数据", "ai_score": 80})
        assert r.ai_error is False
        assert r.ai_duration_ms == 0

    def test_引擎把ai_error与耗时写进落库记录(self):
        """链路验证：analyze → _record_greet → 记录里真的带上这两个字段"""
        import tempfile
        from boss_bot.greet_engine import GreetEngine
        from boss_bot.reply_record import GreetRecordStore
        e = GreetEngine.__new__(GreetEngine)
        e._greet_store = GreetRecordStore(path=tempfile.mkdtemp() + "/r.json")
        e._last_ai_result = {"score": 50, "is_match": True, "ai_error": True,
                             "reason": "AI 分析异常: 正文被截断"}
        e._last_ai_duration_ms = 61000
        e._last_ai_model = ""
        e._last_ai_raw_response = "思考了很多字"
        e._last_ai_system_prompt = None
        e._last_ai_user_prompt = None
        e._greeting_message = "你好"
        e.account_index = 1
        e.log_cb = None
        e._account = lambda: type("A", (), {"name": "账号2"})()
        e._log = lambda *a: None
        e._record_greet({"job_name": "岗位A", "url": "u", "company": "c", "salary": "9K"})
        rec = e._greet_store.get_all()[-1]
        assert rec.ai_error is True
        assert rec.ai_duration_ms == 61000
        assert rec.account_name == "账号2"

    def test_按质量统计能算出兜底率(self):
        import tempfile
        from boss_bot.reply_record import GreetRecordStore, GreetRecord
        store = GreetRecordStore(path=tempfile.mkdtemp() + "/g.json")
        for i in range(8):
            store.add(GreetRecord(job_name=f"j{i}", ai_error=(i < 3), ai_model="m-a",
                                  ai_duration_ms=1000))
        q = store.quality_stats()
        assert q["total"] == 8 and q["fallback"] == 3
        assert q["fallback_rate"] == pytest.approx(3 / 8)
        assert q["avg_duration_ms"] == 1000

    def test_早期没跑AI的记录不混进分母(self):
        """历史 283 条里有 236 条根本没做过 AI 判分（当时没启用），
        把它们算成兜底会得到 83% 的假兜底率。"""
        import tempfile
        from boss_bot.reply_record import GreetRecordStore, GreetRecord
        store = GreetRecordStore(path=tempfile.mkdtemp() + "/g2.json")
        store.add(GreetRecord(job_name="老数据", ai_score=0))
        store.add(GreetRecord(job_name="老数据2", ai_score=0))
        store.add(GreetRecord(job_name="真判分", ai_model="m-a", ai_score=80))
        store.add(GreetRecord(job_name="兜底", ai_model="m-a", ai_error=True))
        q = store.quality_stats()
        assert q["total"] == 2 and q["untracked"] == 2
        assert q["fallback_rate"] == pytest.approx(0.5)
