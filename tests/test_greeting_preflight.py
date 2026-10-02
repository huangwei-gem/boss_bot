"""招呼语没配 → 整轮一条都不发，这件事必须在看板上说清楚。

日志里一片「未配置招呼语」而记录区看着只是"跳过很多"，用户就只剩一个感受：
日志和记录对不上。实测过一整天 218 条全是跳过、0 条投递，原因就是这个号
连一句自己的招呼语都没填（历史配置里那份默认文案按口径算"没写"）。

所以：账号一个可用招呼语都没有时，metrics 要报 greeting_ready=false，
面板打招呼区要顶上前提示，而不是等用户自己去翻日志。
"""
from pathlib import Path

from boss_bot.greet_engine import account_greeting_ready
from boss_bot.unified_config import (DEFAULT_GREETING, AccountConfig, JobConfig,
                                     UnifiedConfig)


def _acc(acc_greeting="", job_greetings=(), enableds=()):
    jobs = []
    for i, g in enumerate(job_greetings):
        jobs.append(JobConfig(greeting_message=g,
                              enabled=enableds[i] if i < len(enableds) else True))
    return AccountConfig(name="测试号", greeting_message=acc_greeting, jobs=jobs)


class AccountGreetingReadyTest:

    def test_账号级填了就可用(self):
        assert account_greeting_ready(_acc(acc_greeting="本号自己的话术")) is True

    def test_岗位填了也可用(self):
        assert account_greeting_ready(
            _acc(job_greetings=["岗位专用的话术"])) is True

    def test_两处都空就是不可用(self):
        assert account_greeting_ready(_acc()) is False

    def test_只有历史默认文案算没配(self):
        """pick_greeting 把"等于默认串"判成未定制，这里必须同一口径，
        否则面板提示"可以去跑"，实际一轮下来全是跳过"""
        assert account_greeting_ready(
            _acc(job_greetings=[DEFAULT_GREETING])) is False

    def test_停用的岗位不算数(self):
        assert account_greeting_ready(
            _acc(job_greetings=["岗位话术"], enableds=[False])) is False

    def test_没有账号对象不炸(self):
        assert account_greeting_ready(None) is False


class MetricsReportsReadinessTest:

    def test_metrics_每个账号都带_greeting_ready(self):
        """账号2 没填、主账号填了：看板得分别报，不能一个"全部就绪"糊过去"""
        cfg = UnifiedConfig()
        cfg.greet.accounts = [
            _acc(acc_greeting="主号话术"),
            _acc(),
        ]
        rows = [{"index": i, "name": a.name, "enabled": a.enabled,
                 "greeting_ready": account_greeting_ready(a)}
                for i, a in enumerate(cfg.greet.accounts)]
        assert [r["greeting_ready"] for r in rows] == [True, False]

    def test_面板接口真的把字段发出去(self):
        body = (Path(__file__).resolve().parent.parent / "flask-version" / "app.py"
                ).read_text(encoding="utf-8")
        start = body.index("def api_metrics()")
        # 截到下一个路由：函数里第一个 return jsonify 是参数校验的报错分支，
        # 只看它会漏判"字段其实没加在成功响应上"
        func = body[start:body.index("@app.route", start)]
        assert "greeting_ready" in func, "metrics 没报就绪状态，面板只能猜"



class OneLogLinePerSkipTest:
    """一次跳过只准留下一行日志——否则用户数日志永远对不上记录条数。

    实测 2026-10-02：记录 91 条「未配置招呼语」，日志里却是 182 行
    （闸门一行 + send_greeting 收尾一行），AI 判定不匹配只有 21 行
    因为那条路径只打一行。日志翻倍正是"日志一堆跳过、记录对不上"的直接来源。
    """

    def _engine(self, logged):
        from unittest.mock import MagicMock

        from boss_bot.greet_engine import GreetEngine
        e = GreetEngine(MagicMock(), MagicMock(), account_index=0)
        e.running = True
        e._log = lambda level, msg: logged.append(msg)
        e._account = lambda: None          # 这个账号没填自定义招呼语
        e._emit_greet_event = lambda *a, **k: None
        e._record_greet = lambda *a, **k: None
        return e

    def _skip_lines(self, logged):
        return [m for m in logged if "未配置招呼语" in m or "⏭️ 跳过" in m]

    def test_招呼语没配只打一行日志(self):
        logged = []
        self._engine(logged).send_greeting(
            {"job_name": "数据分析师", "url": "https://www.zhipin.com/job_detail/x.html"})
        hits = self._skip_lines(logged)
        assert len(hits) == 1, f"一条记录配了 {len(hits)} 行日志: {hits}"

    def test_原因还是要留在日志里(self):
        """合并成一行不能把原因丢掉——留空就不发送这条口径要靠它自证"""
        logged = []
        self._engine(logged).send_greeting(
            {"job_name": "数据分析师", "url": "https://www.zhipin.com/job_detail/x.html"})
        assert "未配置招呼语" in self._skip_lines(logged)[0]
