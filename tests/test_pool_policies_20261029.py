# -*- coding: utf-8 -*-
"""2026-10-29 岗位池四个口径落地后的行为锁。

四条都是拿盘上 3182 行池数据现算出来再定的（口径原文见任务 #223）：
①人事/行政族漏放 → 补进标题否决词表，但**不进** NO_EXEMPT（「人事数据分析」那种
  人事只是被分析的业务域，那份工还是数据岗，跟"信贷"同一个道理）；
②方向命中又撞上修饰型否决词的 20 条 → 不再被「只看本地没否过的」静默藏掉，
  硬拦族（NO_EXEMPT）继续藏；
③粗筛仍然只读卡片不开 JD（开详情页是安全验证的主因，111/112 次里 111 次出自它）；
④勾选要留痕：10-19 进池以来 selected/applied/note 全是 0，光一个 status
  答不出"他没勾过"还是"勾了没投"。

每条都配反向哨兵：判据摘掉必须红，不然是假门。
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLASK_DIR = str(ROOT / "flask-version")
sys.path.insert(0, str(ROOT))

from boss_bot.intent import NO_EXEMPT, title_veto_hit  # noqa: E402
from boss_bot.job_pool import _被粗筛否过, 要人看一眼, 岗位池  # noqa: E402
from boss_bot.unified_config import (DEFAULT_GREETING,  # noqa: E402
                                     TITLE_VETO_KEYWORDS_DEFAULT)

# 盘上真出现过的标题（两份 job_pool*.json 里逐条捞的，不是编的）
新该拦住 = ["人事兼职线上办公时间灵活", "人事档案整理", "人事档案整理专员",
            "干部人事档案整理", "成都人事档案整理专员", "线上人事专员—员工关系"]


@pytest.fixture
def 面板(tmp_path):
    if FLASK_DIR not in sys.path:
        sys.path.insert(0, FLASK_DIR)
    with patch("app.UnifiedBotLoop"):
        import app as FLASK_APP
    with patch("boss_bot.job_pool.resolve_path", return_value=tmp_path):
        yield FLASK_APP


@pytest.fixture(autouse=True)
def 池隔离(tmp_path, monkeypatch):
    """这个模块里每一条测试都只管临时目录里的池。

    12:54 那一趟全量 pytest 里 `_塞()` 走的正是没被接住的默认路径：假岗位进了线上
    账号0 的池，一条真岗位被改成 applied。现在默认路径会当场抛（见 job_pool
    ._不许测试碰真实池），这一层是把测试留在它该在的地方。
    """
    monkeypatch.setattr("boss_bot.job_pool.resolve_path", lambda _p: tmp_path)
    return tmp_path


def _塞(号=0, 标题s=("数据分析师",)):
    池 = 岗位池(account_index=号)
    池.收([{"job_name": 标题, "url": "https://www.zhipin.com/job_detail/%d.html" % i,
            "company": "公司%d" % i, "salary": "8-10K"}
           for i, 标题 in enumerate(标题s)])
    return 池


class Test人事族漏放补词:
    def test_这六条现在拦得住(self):
        for 标题 in 新该拦住:
            中 = title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, 标题)
            assert 中, f"「{标题}」还是漏放：一个否决词都没命中"

    def test_人事里的数据岗不许被顺手杀掉(self):
        """不进 NO_EXEMPT 的全部理由：「人事数据分析」那份工是数据分析。"""
        assert title_veto_hit(TITLE_VETO_KEYWORDS_DEFAULT, "人事数据分析（线上兼职）") == "", \
            "人事这一族压过了职业白名单，把数据岗一起杀了"

    def test_新词确实进了每一端(self):
        新词 = {"人事", "人力资源", "HR", "行政", "员工关系"}
        assert 新词 <= set(TITLE_VETO_KEYWORDS_DEFAULT), "正本里缺词"
        assert not (新词 & set(NO_EXEMPT)), "这一族进了硬拦组＝会杀掉「人事数据分析」"
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "veto", str(ROOT / "boss-apply" / "scripts" / "veto.py"))
        分发副本 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(分发副本)
        assert 新词 <= set(分发副本.DEFAULT_TITLE_VETO), "分发出去的 skill 还在用旧表"

    def test_这条门真的会红(self):
        """把新加的那五个词摘掉，这六条必须回到漏放——证明拦它们的是这五个词。"""
        旧表 = [w for w in TITLE_VETO_KEYWORDS_DEFAULT
                if w not in ("人事", "人力资源", "HR", "行政", "员工关系")]
        assert all(title_veto_hit(旧表, 标题) == "" for 标题 in 新该拦住), \
            "摘掉新词还拦得住＝判据另有出处，这条门是假的"


class Test方向命中不再被静默藏掉:
    def test_修饰型否决词加方向命中_不藏(self):
        行 = {"gate": {"方向命中": True, "否决命中": "住宿"}}
        assert _被粗筛否过(行) is False, "方向正主又被静默删了"
        assert 要人看一眼(True, "住宿") is True

    def test_硬拦族照旧藏(self):
        for 拦 in ("合伙人", "老师", "讲师", "客服", "主播"):
            assert 拦 in NO_EXEMPT, f"{拦} 不在硬拦组，这条判据和 intent 脱钩了"
            assert _被粗筛否过({"gate": {"方向命中": True, "否决命中": 拦}}) is True, \
                f"「{拦}」这一族本来就不该出现在他眼前"
            assert 要人看一眼(True, 拦) is False

    def test_方向不合与没判过照旧(self):
        assert _被粗筛否过({"gate": {"方向命中": False}}) is True
        assert _被粗筛否过({"gate": {}}) is False, "没判过的不能当成机器说他不要"
        assert 要人看一眼(None, "住宿") is False, "方向都没判过，不许标成要人看"

    def test_这条门真的会红(self):
        """把"软词不藏"那半句拆掉（当成所有否决词都藏），方向正主必须重新消失。"""
        假旧 = lambda 行: (行["gate"].get("方向命中") is False
                          or bool(str(行["gate"].get("否决命中") or "").strip()))
        行 = {"gate": {"方向命中": True, "否决命中": "住宿"}}
        assert 假旧(行) is True and _被粗筛否过(行) is False, "两条判据没分开＝门是假的"


class Test勾选留痕:
    def test_勾一下盘上就答得出是谁什么时候动的(self):
        池 = _塞(标题s=("数据分析师",))
        编号 = 池.列表()[0]["id"]
        assert 池.标状态([编号], "selected")[0]
        痕 = 池.取(编号)["勾选"]
        assert 痕["来源"] == "面板点击" and 痕["动作"] == "勾成要投"
        assert 痕["时间"] and 痕["首于"]

    def test_再动一次只换时间不抹掉第一次(self):
        池 = _塞(标题s=("数据分析师",))
        编号 = 池.列表()[0]["id"]
        池.标状态([编号], "selected")
        首 = 池.取(编号)["勾选"]["首于"]
        池.标状态([编号], "new", 留痕={"时间": 首 + 99})
        痕 = 池.取(编号)["勾选"]
        assert 痕["首于"] == 首, "第一次动这一行的时间被后一次抹了"
        assert 痕["动作"] == "撤勾回待选", 痕

    def test_机器动的要写明是机器(self):
        池 = _塞(标题s=("数据分析师",))
        编号 = 池.列表()[0]["id"]
        池.改(编号, status="applied",
             勾选={"来源": "打招呼线程", "动作": "机器投出去了", "时间": 10.0})
        痕 = 池.取(编号)["勾选"]
        assert 痕["来源"] == "打招呼线程" and 痕["动作"] == "机器投出去了"

    def test_接口一行一行点勾也留痕(self, 面板):
        池 = _塞(标题s=("数据分析师",))
        编号 = 池.列表()[0]["id"]
        r = 面板.app.test_client().post("/api/job_pool/update",
                                       json={"account": 0, "id": 编号,
                                             "status": "selected"})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["row"]["勾选"]["来源"] == "面板点击", \
            "逐行点勾不写留痕＝事后只剩一个 status，答不出他勾没勾过"

    def test_同一条既改状态又改备注时两句都要算数(self, 面板):
        池 = _塞(标题s=("数据分析师",))
        编号 = 池.列表()[0]["id"]
        r = 面板.app.test_client().post("/api/job_pool/update",
                                       json={"account": 0, "id": 编号,
                                             "status": "rejected", "note": "薪资谈不拢"})
        回 = r.get_json()["row"]
        assert 回["status"] == "rejected" and 回["note"] == "薪资谈不拢", 回
        assert 回["勾选"]["动作"] == "标成不投", 回


class Test默认招呼语与画像同源:
    def test_默认串过得了自己的闸(self):
        from boss_bot.reply_engine import reply_rejection
        assert reply_rejection(DEFAULT_GREETING) == ""

    def test_画像没写档次时同一句必须拦(self):
        from boss_bot.reply_engine import reply_rejection
        没写 = {"education": "本科在读（统计学）", "school": ""}
        # 默认串本身不报学校档次（2026-11-01 按在线简历对齐后），
        # 反向哨兵改成"给同一句补一个画像里没有的档次"——证明这道门还在，不是被删了
        assert reply_rejection("您好，我是双一流的。" + DEFAULT_GREETING, 没写), \
            "闸门坏了不会拦＝只是摆设"


class Test测试不许碰线上池:
    """12:54 那一趟把假岗位写进了 data/job_pool.json，这条门就是那一次的账。"""

    def test_测试里走默认路径要当场炸(self, monkeypatch):
        monkeypatch.undo()    # 撤掉本模块 autouse 的隔离，还原成默认路径
        import boss_bot.job_pool as 池模块
        with pytest.raises(RuntimeError, match="data/"):
            池模块.岗位池(account_index=0)

    def test_给了临时目录就照走(self, tmp_path):
        池 = 岗位池(account_index=0, 目录=str(tmp_path))
        assert str(池.路径).startswith(str(tmp_path)), 池.路径
        assert 池.收([{"job_name": "数据分析师", "url": "https://www.zhipin.com/job_detail/1.html",
                      "company": "公司0"}])["新增"] == 1

    def test_线上那份池里现在没有测试的痕迹(self):
        """真数据回读：假行、假时间留痕都必须为 0（改完门也要盯着存量别留赃）。"""
        import json
        for 名 in ("job_pool.json", "job_pool_account_1.json"):
            行s = json.loads((ROOT / "data" / 名).read_text(encoding="utf-8"))
            假行 = [r for r in 行s if str(r.get("company", "")).startswith("公司")
                    and r.get("job_name") == "数据分析师"]
            假痕 = [r for r in 行s if (r.get("勾选") or {}).get("时间", 9e9) < 1e6]
            assert not 假行 and not 假痕, f"{名} 里还有测试写进去的 {假行 + 假痕}"
