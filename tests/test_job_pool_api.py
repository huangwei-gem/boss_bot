# -*- coding: utf-8 -*-
"""岗位池那五个面板接口的行为锁（读 / 改 / 批量 / 删 / 手工加）。

只测接口层独有的三件事，数据层自己的规矩在 tests/test_job_pool.py 里已经钉过：
① 写接口**必须点名是哪个号** —— 池按号分开存，默认成主账号会让"看着账号2 却写进
   账号1"这种错悄悄发生；
② 回读才算改成 —— 后端写了、界面还显示旧值，是他反复说过的那类"没生效"；
③ 「勾选投递」只改状态，不在 HTTP 线程里点沟通 —— 那会和抓取线程抢同一张搜索页。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLASK_DIR = str(ROOT / "flask-version")


@pytest.fixture
def 面板(tmp_path):
    if FLASK_DIR not in sys.path:
        sys.path.insert(0, FLASK_DIR)
    with patch("app.UnifiedBotLoop"):
        import app as FLASK_APP
    # 池文件按号落在 data/ 下；不隔开就是拿他真攒的候选清单做测试
    with patch("boss_bot.job_pool.resolve_path", return_value=tmp_path):
        yield FLASK_APP


def _塞(FLASK_APP, 号=0, n=3):
    from boss_bot.job_pool import 岗位池
    池 = 岗位池(account_index=号)
    池.收([{"job_name": "数据分析师%d" % i,
            "url": "https://www.zhipin.com/job_detail/%d.html" % (i + 1),
            "company": "公司%d" % i, "salary": "8-10K"} for i in range(n)])
    return 池


class 读Test:
    def test_列表带回行与统计(self, 面板):
        _塞(面板)
        r = 面板.app.test_client().get("/api/job_pool?account=0")
        assert r.status_code == 200
        d = r.get_json()
        assert d["total"] == 3 and d["stats"]["总数"] == 3
        assert d["rows"][0]["job_name"].startswith("数据分析师")

    def test_不给账号就把两号的池并成一张表(self, 面板):
        """"全部账号"是面板开机的默认范围：这一档回空表，他看着就是"一条都没抓到"。"""
        _塞(面板, 号=0, n=3)
        _塞(面板, 号=1, n=2)
        with patch.object(面板, "_ensure_config") as 配:
            配.return_value.greet.accounts = [{}, {}]
            r = 面板.app.test_client().get("/api/job_pool")
        assert r.status_code == 200
        d = r.get_json()
        assert d["total"] == 5 and d["stats"]["总数"] == 5
        assert d["per_account"]["0"]["总数"] == 3
        assert d["per_account"]["1"]["总数"] == 2
        assert {x["account_index"] for x in d["rows"]} == {0, 1}, "合并视图的行得带着自己是哪个号的"

    def test_合并视图里同一条链接在两号下要分得开(self, 面板):
        """行 id 是链接算出来的：同一个岗位被两个号都抓到，id 一模一样。
        合并成一张表时只能靠 account_index 分，不然勾一条会勾中两条。"""
        _塞(面板, 号=0, n=3)
        _塞(面板, 号=1, n=2)
        with patch.object(面板, "_ensure_config") as 配:
            配.return_value.greet.accounts = [{}, {}]
            d = 面板.app.test_client().get("/api/job_pool").get_json()
        assert len({x["id"] for x in d["rows"]}) == 3, "前提变了：同一条链接该算出同一个 id"
        assert len({(x["account_index"], x["id"]) for x in d["rows"]}) == 5

    def test_状态与关键字筛(self, 面板):
        池 = _塞(面板)
        编号 = 池.列表()[0]["id"]
        池.改(编号, status="rejected")
        c = 面板.app.test_client()
        assert c.get("/api/job_pool?account=0&status=rejected").get_json()["total"] == 1
        assert c.get("/api/job_pool?account=0&q=数据分析师").get_json()["total"] == 3
        assert c.get("/api/job_pool?account=1").get_json()["total"] == 0, "别号不该看见"


class 写Test:
    def _post(self, 面板, 路径, 体):
        return 面板.app.test_client().post(路径, json=体)

    def test_改一条要回读到才算成(self, 面板):
        池 = _塞(面板)
        编号 = 池.列表()[0]["id"]
        r = self._post(面板, "/api/job_pool/update",
                       {"account": 0, "id": 编号, "note": "这家认识"})
        assert r.status_code == 200
        assert r.get_json()["row"]["note"] == "这家认识"
        assert 池.取(编号)["note"] == "这家认识", "接口回 ok 但盘上没有，就是没生效"

    def test_状态只认四种写错的挡回来(self, 面板):
        池 = _塞(面板)
        r = self._post(面板, "/api/job_pool/update",
                       {"account": 0, "id": 池.列表()[0]["id"], "status": "随便"})
        assert r.status_code == 400
        assert "四种" in r.get_json()["message"]

    def test_不写account就不许改(self, 面板):
        池 = _塞(面板)
        r = self._post(面板, "/api/job_pool/update",
                       {"id": 池.列表()[0]["id"], "note": "想偷渡"})
        assert r.status_code == 400
        assert 池.取(池.列表()[0]["id"])["note"] == "", "挡回来就不能已经写进去了"

    def test_勾选投递只改状态不动浏览器(self, 面板):
        """这条要钉住：接口绝不在 HTTP 线程里点「立即沟通」。"""
        池 = _塞(面板)
        编号s = [r["id"] for r in 池.列表()][:2]
        with patch.object(池.__class__, "标状态", wraps=池.标状态) as 盯:
            r = self._post(面板, "/api/job_pool/batch",
                           {"account": 0, "ids": 编号s, "status": "selected"})
            assert r.status_code == 200 and 盯.call_count == 1
        assert [池.取(n)["status"] for n in 编号s] == ["selected", "selected"]
        assert 池.取(编号s[0]).get("投递") in ({}, None) or not 池.取(编号s[0])["投递"], \
            "没投过就不该有投递结局"

    def test_勾选取留痕要认来源(self, 面板):
        """10-29 口径④「勾了要留来源/动作/时间」：来源得真能从接口传进来。

        界面那条按钮不传＝面板点击（就是他点的）；代他点的必须自己报名字，
        否则机器挑的行会在池子里记成"他亲手勾的"——而"勾了就等于判过了"
        这条免复核口径正是按谁勾的成立的。
        """
        池 = _塞(面板)
        他勾的, 代勾的 = [r["id"] for r in 池.列表()][:2]
        self._post(面板, "/api/job_pool/batch",
                   {"account": 0, "ids": [他勾的], "status": "selected"})
        r = self._post(面板, "/api/job_pool/batch",
                       {"account": 0, "ids": [代勾的], "status": "selected",
                        "来源": "代勾（agent 按他点的数挑）"})
        assert r.status_code == 200
        assert 池.取(他勾的)["勾选"]["来源"] == "面板点击"
        assert 池.取(代勾的)["勾选"]["来源"] == "代勾（agent 按他点的数挑）", \
            "接口把来源丢了：池子里分不清这行是他勾的还是机器勾的"

    def test_删掉的回读不到(self, 面板):
        池 = _塞(面板)
        编号 = 池.列表()[0]["id"]
        r = self._post(面板, "/api/job_pool/delete", {"account": 0, "ids": [编号]})
        assert r.status_code == 200
        assert 池.取(编号) == {}
        assert r.get_json()["stats"]["总数"] == 2

    def test_手工加没链接就挡(self, 面板):
        r = self._post(面板, "/api/job_pool/add", {"account": 0, "job_name": "某岗"})
        assert r.status_code == 400
        assert "链接" in r.get_json()["message"]

    def test_手工加带链接进池(self, 面板):
        r = self._post(面板, "/api/job_pool/add", {
            "account": 0, "url": "https://www.zhipin.com/job_detail/999.html",
            "job_name": "数据分析实习生"})
        assert r.status_code == 200
        assert r.get_json()["stats"]["总数"] == 1
