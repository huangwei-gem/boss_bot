# -*- coding: utf-8 -*-
"""线上兼职的搜索口径：全国城市码 + 求职类型 jobType，都要真落到搜索 URL 上。

两个数都是从 BOSS 搜索页实测来的（2026-10-03，破解浏览器登录态下点筛选看 URL）：
- 城市弹层里点"全国" → city=100010000，结果地区立刻跨省份（潍坊/商丘/南阳/海东/和田…）
- 求职类型点"全职/实习/兼职" → jobType=1901 / 1902 / 1903
不是猜的编码：编码错了就是"设置了全国但只在本地找"，这种失效在面板上看不出来。
"""
import pytest

from boss_bot.greet_engine import CITY_CODES, JOB_TYPE_CODES, GreetEngine
from boss_bot.greeting import compose_account_default
from boss_bot.unified_config import AccountConfig, JobConfig


def _engine():
    e = GreetEngine.__new__(GreetEngine)
    e._log = lambda *a, **k: None
    e._city_dict = None
    e._query = ""
    e._city = ""
    e._job_type = ""
    e._scroll_pages = 1
    return e


class CityAndTypeTest:

    def test_全国有自己的城市码(self):
        assert CITY_CODES.get("全国") == "100010000"

    @pytest.mark.parametrize("label,code", [
        ("全职", "1901"), ("实习", "1902"), ("兼职", "1903"),
    ])
    def test_求职类型三个档都有码(self, label, code):
        assert JOB_TYPE_CODES[label] == code

    def test_全国加兼职都拼进URL(self):
        url = _engine()._build_search_url("数据分析 线上", "全国", "兼职")
        assert "city=100010000" in url, url
        assert "jobType=1903" in url, url

    def test_没选求职类型就不带这个参数(self):
        url = _engine()._build_search_url("数据分析", "全国")
        assert "jobType" not in url, url

    def test_不认识的求职类型要说出来而不是静默丢掉(self):
        """面板能填任意文本；码表里没有就得在日志里响，不然用户以为生效了"""
        logged = []
        e = _engine()
        e._log = lambda level, msg: logged.append((level, msg))
        url = e._build_search_url("数据分析", "全国", "临时工")
        assert "jobType" not in url
        assert any("临时工" in m for _, m in logged), f"没告警: {logged}"


class ConfigRoundTripTest:

    def test_job_type能存能读(self, tmp_path):
        from boss_bot.unified_config import UnifiedConfig

        cfg = UnifiedConfig()
        cfg.greet.accounts = [AccountConfig(
            name="主账号", jobs=[JobConfig(city="全国", query="数据分析 线上",
                                        job_type="兼职")])]
        path = tmp_path / "bot_config.json"
        cfg.save(path)
        back = UnifiedConfig.load(str(path))
        job = back.greet.accounts[0].jobs[0]
        assert (job.city, job.query, job.job_type) == ("全国", "数据分析 线上", "兼职")


class SearchWiringTest:

    def test_每个岗位跑搜索时要带上自己的求职类型(self):
        """query/city 都在按岗位切换时搬到引擎上，job_type 漏了就是"筛选没生效" """
        import inspect

        import boss_bot.greet_engine as m
        src = inspect.getsource(m)
        at = src.index("self._query = job.query")
        window = src[at - 200:at + 400]
        assert "self._job_type = " in window, f"搬字段时漏了 job_type：{window[:300]}"

    def test_搜索URL构建真的读引擎上的求职类型(self):
        e = _engine()
        e._query, e._city, e._job_type = "数据处理 线上", "全国", "兼职"
        url = e._build_search_url(e._query, e._city, e._job_type)
        assert "jobType=1903" in url and "city=100010000" in url


def _greeting_account(**job_kwargs):
    from boss_bot.unified_config import AccountConfig, JobConfig
    job = {"city": "全国", "query": "数据分析 线上", "job_type": "兼职"}
    job.update(job_kwargs)
    return AccountConfig(name="账号", jobs=[JobConfig(**job)])


class GreetingWordingTest:

    def _acc(self, **job_kwargs):
        return _greeting_account(**job_kwargs)

    def test_全国不该被念成城市(self):
        txt = compose_account_default(
            self._acc(city="全国", query="数据分析", job_type="兼职"),
            resume={"school": "长沙理工大学", "major": "统计学", "degree": "本科",
                    "skills": ["Excel", "SQL"]})
        assert "在全国找" not in txt, txt

    def test_兼职要说成线上兼职(self):
        txt = compose_account_default(
            self._acc(city="全国", query="数据分析 线上", job_type="兼职"),
            resume={"school": "长沙理工大学", "major": "统计学", "degree": "本科",
                    "skills": ["Excel", "SQL"]})
        assert "兼职" in txt, f"招呼语里看不出这是找线上兼职: {txt}"

    def test_全职口径不受影响(self):
        txt = compose_account_default(
            self._acc(city="上海", query="数据分析", job_type="全职"),
            resume={"school": "长沙理工大学", "major": "统计学", "degree": "本科",
                    "skills": ["Excel", "SQL"]})
        assert "兼职" not in txt, txt
