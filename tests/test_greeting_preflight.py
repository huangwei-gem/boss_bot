"""跑一轮到底发不发得出去，必须在看板上说清楚。

2026-10-03 口径：账号没写招呼语不再是"一条都不发"，而是按这个账号自己的
城市/方向/技能先生成一条默认（界面里看得见、改了立刻生效），发送时再由 AI 按
岗位+公司+JD 现编。以前"留空即不发"造成过一整天 218 条全跳过、0 条投递，
用户看到的只是"日志一片跳过、记录对不上"。
ready 现在只剩一个前提：有没有启用中的岗位；另外报一个 greeting_mode 说明
这句话是账号自己写的还是系统生成的默认。
"""
from pathlib import Path

from boss_bot.greet_engine import account_greeting_mode, account_greeting_ready
from boss_bot.unified_config import (DEFAULT_GREETING, AccountConfig, JobConfig,
                                     UnifiedConfig)


def _acc(acc_greeting="", job_greetings=(), enableds=()):
    jobs = []
    for i, g in enumerate(job_greetings):
        jobs.append(JobConfig(greeting_message=g,
                              enabled=enableds[i] if i < len(enableds) else True))
    # 真实配置里 AccountConfig 默认就带一条岗位，空列表是测试替身造出来的假场景
    return AccountConfig(name="测试号", greeting_message=acc_greeting,
                         jobs=jobs or [JobConfig()])


class AccountGreetingReadyTest:

    def test_账号级填了就可用(self):
        assert account_greeting_ready(_acc(acc_greeting="本号自己的话术")) is True

    def test_岗位填了也可用(self):
        assert account_greeting_ready(
            _acc(job_greetings=["岗位专用的话术"])) is True

    def test_两处都空会自动给一条默认(self):
        """用户要求：每个账号先按它自己的信息给个默认，别再整轮空转"""
        assert account_greeting_ready(_acc()) is True

    def test_自动默认不等于账号自己写的(self):
        assert account_greeting_mode(_acc()) == "自动生成的默认"
        assert account_greeting_mode(_acc(acc_greeting="自己写的")) == "账号自写"

    def test_只有历史默认文案算没写(self):
        """那份每个岗位都塞着的默认串按口径算"没写"：模式必须是自动生成，
        不能让它顶着"账号自写"的名号混过去"""
        acc = _acc(acc_greeting=DEFAULT_GREETING)
        assert account_greeting_mode(acc) == "自动生成的默认"

    def test_停用的岗位不算数(self):
        assert account_greeting_ready(
            _acc(job_greetings=["岗位话术"], enableds=[False])) is False

    def test_没有账号对象不炸(self):
        assert account_greeting_ready(None) is False


class MetricsReportsReadinessTest:

    def test_metrics_每个账号都带_greeting_ready(self):
        """一个号岗位全停用、一个号能发：看板得分别报，不能一个"全部就绪"糊过去"""
        cfg = UnifiedConfig()
        cfg.greet.accounts = [
            _acc(acc_greeting="主号话术"),
            _acc(job_greetings=["岗位话术"], enableds=[False]),
        ]
        rows = [{"index": i, "name": a.name, "enabled": a.enabled,
                 "greeting_ready": account_greeting_ready(a),
                 "greeting_mode": account_greeting_mode(a)}
                for i, a in enumerate(cfg.greet.accounts)]
        assert [r["greeting_ready"] for r in rows] == [True, False]
        assert [r["greeting_mode"] for r in rows] == ["账号自写", "自动生成的默认"]

    def test_面板接口真的把字段发出去(self):
        body = (Path(__file__).resolve().parent.parent / "flask-version" / "app.py"
                ).read_text(encoding="utf-8")
        start = body.index("def api_metrics()")
        # 截到下一个路由：函数里第一个 return jsonify 是参数校验的报错分支，
        # 只看它会漏判"字段其实没加在成功响应上"
        func = body[start:body.index("@app.route", start)]
        assert "greeting_ready" in func, "metrics 没报就绪状态，面板只能猜"
        assert "greeting_mode" in func, "metrics 没报这句话是谁的话，面板只能猜「没填」"



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
        e._account = lambda: None          # 连账号配置都取不到：真的没话可发
        e._emit_greet_event = lambda *a, **k: None
        e._record_greet = lambda *a, **k: None
        return e

    def _skip_lines(self, logged):
        return [m for m in logged if "招呼语" in m or "⏭️ 跳过" in m]

    def test_招呼语没配只打一行日志(self):
        logged = []
        self._engine(logged).send_greeting(
            {"job_name": "数据分析师", "url": "https://www.zhipin.com/job_detail/x.html"})
        hits = self._skip_lines(logged)
        assert len(hits) == 1, f"一条记录配了 {len(hits)} 行日志: {hits}"

    def test_原因还是要留在日志里(self):
        """合并成一行不能把原因丢掉——为什么发不出去必须写在日志上"""
        logged = []
        self._engine(logged).send_greeting(
            {"job_name": "数据分析师", "url": "https://www.zhipin.com/job_detail/x.html"})
        assert "未配置招呼语" in self._skip_lines(logged)[0]
