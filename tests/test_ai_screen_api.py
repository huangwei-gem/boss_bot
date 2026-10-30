# -*- coding: utf-8 -*-
"""AI 自定义筛选那五个接口 + 池子的城市/匹配筛选（2026-10-28）。

接口层独有的三件事：
① 上传/粘贴的要求要真落盘、回读得到，空文件要挡回来；
② 保存后要真的按它开始匹配（这里把重判线程打成桩，只看"有没有开跑"）；
③ 「只看 AI 匹配」只认当前这份文件——换了文件，旧结论不许继续冒充匹配。
"""

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLASK_DIR = str(ROOT / "flask-version")


@pytest.fixture
def 面板(tmp_path):
    if FLASK_DIR not in sys.path:
        sys.path.insert(0, FLASK_DIR)
    with patch("app.UnifiedBotLoop"):
        import app as FLASK_APP
    配 = MagicMock()
    配.return_value.greet.accounts = [{}, {}]
    # 池和筛选要求都落在 tmp：不碰他真攒的候选清单和要求文件
    with patch("boss_bot.job_pool.resolve_path", return_value=tmp_path), \
            patch("boss_bot.ai_screen_doc.resolve_path", return_value=tmp_path), \
            patch.object(FLASK_APP, "_ensure_config", 配), \
            patch.object(FLASK_APP.screen_runner, "起",
                         return_value=(False, "测试里不真跑")) as 起:
        yield FLASK_APP, 起


def _塞(FLASK_APP, 号=0, 行s=None):
    from boss_bot.job_pool import 岗位池
    池 = 岗位池(account_index=号)
    池.收(行s or [{"job_name": "数据分析师%d" % i,
                  "url": "https://www.zhipin.com/job_detail/%d.html" % (i + 1),
                  "company": "公司%d" % i, "salary": "8-10K",
                  "city": "长沙·岳麓区", "location": "长沙·岳麓区"}
                 for i in range(3)])
    return 池


class 筛选要求接口Test:
    def test_还没有要求时照实回空(self, 面板):
        FLASK_APP, _ = 面板
        d = FLASK_APP.app.test_client().get("/api/ai_screen_doc").get_json()
        assert d["status"] == "ok"
        assert d["doc"]["hash"] == "" and d["doc"]["name"] == ""
        assert d["stats"] == {"total": 0, "judged": 0, "matched": 0,
                              "pending": 0, "skipped": 0}
        assert d["run"]["running"] is False

    def test_保存文本会落盘并开跑(self, 面板):
        FLASK_APP, 起 = 面板
        _塞(FLASK_APP)
        r = FLASK_APP.app.test_client().post(
            "/api/ai_screen_doc",
            json={"name": "我的要求", "text": "只要长沙的数据分析，不要外包"})
        assert r.status_code == 200
        d = r.get_json()
        assert d["doc"]["hash"] and d["doc"]["chars"] == len("只要长沙的数据分析，不要外包")
        assert d["started"] is False and d["message"] == "测试里不真跑"
        起.assert_called_once()
        号们 = 起.call_args[0][0]
        assert 号们 == [0, 1], "池是按号存的：两个号的池都要按这份要求判"
        # 回读：存了就是存了，不是只在响应里
        e = FLASK_APP.app.test_client().get("/api/ai_screen_doc").get_json()
        assert e["doc"]["text"] == "只要长沙的数据分析，不要外包"
        assert e["stats"]["total"] == 3 and e["stats"]["pending"] == 3

    def test_空文本挡回来也不许留下半份(self, 面板):
        FLASK_APP, 起 = 面板
        r = FLASK_APP.app.test_client().post("/api/ai_screen_doc",
                                             json={"name": "空", "text": "  "})
        assert r.status_code == 400
        assert "没读出文字" in r.get_json()["message"]
        起.assert_not_called()
        assert FLASK_APP.app.test_client().get(
            "/api/ai_screen_doc").get_json()["doc"]["hash"] == ""

    def test_上传txt解析不保存(self, 面板):
        FLASK_APP, _ = 面板
        r = FLASK_APP.app.test_client().post(
            "/api/ai_screen_doc/parse",
            data={"file": (io.BytesIO("只要上海".encode("utf-8")), "要求.txt")},
            content_type="multipart/form-data")
        assert r.status_code == 200
        d = r.get_json()
        assert d["text"] == "只要上海" and d["chars"] == 4 and d["name"] == "要求.txt"
        assert FLASK_APP.app.test_client().get(
            "/api/ai_screen_doc").get_json()["doc"]["hash"] == "", "只看一眼，不许悄悄存盘"

    def test_上传读不出字要当场说(self, 面板):
        FLASK_APP, _ = 面板
        r = FLASK_APP.app.test_client().post(
            "/api/ai_screen_doc/parse",
            data={"file": (io.BytesIO(b"   "), "空.txt")},
            content_type="multipart/form-data")
        assert r.status_code == 400 and "没有文字" in r.get_json()["message"]

    def test_没收到文件挡回来(self, 面板):
        FLASK_APP, _ = 面板
        r = FLASK_APP.app.test_client().post("/api/ai_screen_doc/parse",
                                             content_type="multipart/form-data")
        assert r.status_code == 400 and "没收到文件" in r.get_json()["message"]

    def test_重新匹配接口把消息回给界面(self, 面板):
        FLASK_APP, 起 = 面板
        from boss_bot import ai_screen_doc
        ai_screen_doc.存("有.txt", "只要长沙")
        r = FLASK_APP.app.test_client().post("/api/ai_screen_doc/run", json={})
        assert r.status_code == 400, "桩子回的是没开起来：要照实报 400，不许弹绿"
        assert r.get_json()["message"] == "测试里不真跑"
        起.assert_called_once()

    def test_停止接口(self, 面板):
        FLASK_APP, _ = 面板
        r = FLASK_APP.app.test_client().post("/api/ai_screen_doc/stop")
        assert r.status_code == 200 and "停下" in r.get_json()["message"]


class 池子筛选接口Test:
    行s = [
        {"job_name": "数据分析", "url": "https://zhipin/job/1", "company": "甲",
         "salary": "8-10K", "city": "长沙·岳麓区·麓谷", "location": "长沙·岳麓区"},
        {"job_name": "数据标注", "url": "https://zhipin/job/2", "company": "乙",
         "salary": "6-8K", "city": "上海·浦东新区", "location": "上海"},
        {"job_name": "运营", "url": "https://zhipin/job/3", "company": "丙",
         "salary": "7-9K", "city": "长沙·天心区", "location": "长沙"},
    ]

    def test_城市下拉按整池现算(self, 面板):
        FLASK_APP, _ = 面板
        _塞(FLASK_APP, 行s=self.行s)
        d = FLASK_APP.app.test_client().get("/api/job_pool?account=0").get_json()
        assert d["cities"][0] == {"city": "长沙", "count": 2}
        assert {x["city"] for x in d["cities"]} == {"长沙", "上海"}

    def test_按城市筛(self, 面板):
        FLASK_APP, _ = 面板
        _塞(FLASK_APP, 行s=self.行s)
        c = FLASK_APP.app.test_client()
        d = c.get("/api/job_pool?account=0&city=长沙").get_json()
        assert d["total"] == 2 and {x["job_name"] for x in d["rows"]} == {"数据分析", "运营"}
        assert c.get("/api/job_pool?account=0&city=上海").get_json()["total"] == 1

    def test_关键字能搜到城市和地点(self, 面板):
        FLASK_APP, _ = 面板
        _塞(FLASK_APP, 行s=self.行s)
        d = FLASK_APP.app.test_client().get(
            "/api/job_pool?account=0&q=岳麓").get_json()
        assert d["total"] == 1 and d["rows"][0]["job_name"] == "数据分析"

    def test_只看AI匹配只认当前这份文件(self, 面板):
        FLASK_APP, _ = 面板
        from boss_bot import ai_screen_doc
        文档 = ai_screen_doc.存("要求.txt", "只要长沙")
        池 = _塞(FLASK_APP, 行s=self.行s)
        编号 = [r["id"] for r in 池.列表(排序="新抓的在前")]
        池.改(编号[0], ai={"合": True, "doc": 文档["hash"], "判于": 1.0})
        池.改(编号[1], ai={"合": True, "doc": "旧文件", "判于": 1.0})
        c = FLASK_APP.app.test_client()
        d = c.get("/api/job_pool", query_string={"account": 0, "ai_state": "合"}).get_json()
        assert d["total"] == 1 and d["rows"][0]["job_name"] == "数据分析"
        assert d["stats"]["AI匹配"] == 1
        assert d["ai_doc"]["hash"] == 文档["hash"]

    def test_没有文件时勾匹配一条都不给(self, 面板):
        FLASK_APP, _ = 面板
        池 = _塞(FLASK_APP, 行s=self.行s)
        编号 = [r["id"] for r in 池.列表(排序="新抓的在前")]
        池.改(编号[0], ai={"合": True, "判于": 1.0})     # 老行：没盖过指纹
        d = FLASK_APP.app.test_client().get(
            "/api/job_pool", query_string={"account": 0, "ai_state": "合"}).get_json()
        assert d["total"] == 0, "没文件时 ai.doc=='' 和指纹 '' 相等，会拿老结论冒充匹配"

    def test_接口不再认那两个旧参数(self, 面板):
        """界面上原来那两颗勾选框换成了表头两格，HTTP 那边不能留一套没人发的别名：
        同一个意思两种写法，早晚会出现"一种生效一种不生效"的那种对不上。"""
        FLASK_APP, _ = 面板
        _塞(FLASK_APP, 行s=self.行s)
        c = FLASK_APP.app.test_client()
        全 = c.get("/api/job_pool", query_string={"account": 0}).get_json()["total"]
        for 旧 in ("ai_match=1", "only_pass=1"):
            d = c.get("/api/job_pool?account=0&" + 旧).get_json()
            assert d["total"] == 全, "%s 还在筛——别名没撤干净" % 旧

    def test_合并视图的城市与匹配数都按整池算(self, 面板):
        FLASK_APP, _ = 面板
        from boss_bot import ai_screen_doc
        文档 = ai_screen_doc.存("要求.txt", "只要长沙")
        池0 = _塞(FLASK_APP, 号=0, 行s=self.行s[:2])
        池1 = _塞(FLASK_APP, 号=1, 行s=self.行s[2:])
        池0.改(池0.列表()[0]["id"], ai={"合": True, "doc": 文档["hash"], "判于": 1.0})
        池1.改(池1.列表()[0]["id"], ai={"合": True, "doc": 文档["hash"], "判于": 1.0})
        d = FLASK_APP.app.test_client().get("/api/job_pool").get_json()
        assert d["total"] == 3
        assert {x["city"] for x in d["cities"]} == {"长沙", "上海"}
        assert d["stats"]["AI匹配"] == 2, "「全部账号」那一行得把两号的匹配数加起来"
        assert d["per_account"]["0"]["AI匹配"] == 1
        assert d["per_account"]["1"]["AI匹配"] == 1
