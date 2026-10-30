# -*- coding: utf-8 -*-
"""三端一致性里「岗位候选池」那条判据自己该不该红。

10-29 这一趟真跑出来 4 条"不一致"，逐条看都是判据停在旧口径上：
· 界面一次只铺 60 行（分批），旧判据要它等于池里 1492 条；
· 「全部账号」10-26 就改成合并视图了（#259/#260），旧判据还在等接口 400。

改判据最怕改成"永远绿"：所以这里既钉住真实形状必须绿，也逐条做变异证明它会红。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
脚本 = ROOT / "tools" / "consistency_test" / "run_consistency_test.py"


def _载入():
    spec = importlib.util.spec_from_file_location("一致性脚本_池判据", str(脚本))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _载入()


def _行(ranges):
    return [{"id": i} for i in ranges]


def _单号形状(号=0, 盘条数=1492, 行数=60):
    状态 = {"new": 1486, "selected": 0, "applied": 1, "rejected": 5}
    接 = {"total": 盘条数, "stats": dict(状态), "rows": _行(range(盘条数))}
    画 = {"行": 行数, "角": str(盘条数), "条数格": "筛出 %d 条" % 盘条数,
          "id们": [str(i) for i in range(行数)]}
    return 盘条数, 状态, 接, 画


def _合并形状(行数=300):
    各号 = [{"total": 1492, "状态": {"new": 1486, "selected": 0, "applied": 1, "rejected": 5}},
            {"total": 1617, "状态": {"new": 1615, "selected": 0, "applied": 0, "rejected": 2}}]
    总 = 3109
    接 = {"status_code": 200, "total": 总,
          "stats": {"new": 3101, "selected": 0, "applied": 1, "rejected": 7},
          "rows": _行(range(总))}
    画 = {"行": 行数, "角": str(总), "条数格": "筛出 %d 条" % 总, "空态可见": False,
          "id们": [str(i) for i in range(行数)]}
    return 各号, 接, 画


class Test单号池:
    def test_真实形状_盘上等于接口等于角标_不红(self):
        """10-29 实测：盘上 1492、接口 1492、屏幕上只铺了 60 行——这不算不一致。"""
        盘条数, 状态, 接, 画 = _单号形状()
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画) == []

    def test_盘上与接口差一条要红(self):
        盘条数, 状态, 接, 画 = _单号形状()
        接["total"] = 盘条数 - 1
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画)

    def test_状态计数对不上要红(self):
        盘条数, 状态, 接, 画 = _单号形状()
        接["stats"] = dict(状态, new=1485)
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画)

    def test_角标不是池里那个数要红(self):
        盘条数, 状态, 接, 画 = _单号形状()
        画["角"] = "1491"
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画)

    def test_筛出条数不是池里那个数要红(self):
        盘条数, 状态, 接, 画 = _单号形状()
        画["条数格"] = "筛出 1491 条"
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画)

    def test_一行都没铺出来要红(self):
        盘条数, 状态, 接, 画 = _单号形状(行数=0)
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画)

    def test_界面比池里还多要红(self):
        盘条数, 状态, 接, 画 = _单号形状(行数=1493)
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画)

    def test_屏幕上有池里没有的行要红(self):
        盘条数, 状态, 接, 画 = _单号形状()
        画["id们"] = 画["id们"] + ["池里没有的这一条"]
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画)

    def test_屏幕上同一行画两遍要红(self):
        盘条数, 状态, 接, 画 = _单号形状()
        画["id们"] = 画["id们"] + [画["id们"][0]]
        assert M.单号池该不该红(0, 盘条数, 状态, 接, 画)

    def test_界面读取失败要红而不是抛异常(self):
        盘条数, 状态, 接, _ = _单号形状()
        问题 = M.单号池该不该红(0, 盘条数, 状态, 接, {"读取失败": "<读取失败: 超时>"})
        assert 问题 and "读取失败" in str(问题[0])


class Test合并池:
    def test_真实形状_合计等于两个号的和_不红(self):
        各号, 接, 画 = _合并形状()
        assert M.合并池该不该红(各号, 接, 画) == []

    def test_接口没回200要红(self):
        各号, 接, 画 = _合并形状()
        接["status_code"] = 400
        assert M.合并池该不该红(各号, 接, 画)

    def test_合计不是两个号的和要红(self):
        各号, 接, 画 = _合并形状()
        接["total"] = 3108
        assert M.合并池该不该红(各号, 接, 画)

    def test_合计状态不是两份的和要红(self):
        各号, 接, 画 = _合并形状()
        接["stats"] = dict(接["stats"], applied=2)
        assert M.合并池该不该红(各号, 接, 画)

    def test_合并视图一行都没铺出来要红(self):
        各号, 接, 画 = _合并形状(行数=0)
        assert M.合并池该不该红(各号, 接, 画)

    def test_合并视图还露着空态框要红(self):
        各号, 接, 画 = _合并形状()
        画["空态可见"] = True
        assert M.合并池该不该红(各号, 接, 画)

    def test_合并视图角标不对要红(self):
        各号, 接, 画 = _合并形状()
        画["角"] = "3108"
        assert M.合并池该不该红(各号, 接, 画)

    def test_合并视图画出池里没有的行要红(self):
        各号, 接, 画 = _合并形状()
        画["id们"] = 画["id们"] + ["别的号也没有"]
        assert M.合并池该不该红(各号, 接, 画)
