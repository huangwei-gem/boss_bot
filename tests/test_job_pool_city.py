# -*- coding: utf-8 -*-
"""岗位池按城市/地点筛（2026-10-28「怎么地点不能直接筛选呢」）。

BOSS 给的 location 是「长沙·岳麓区·麓谷」这种全地址：按原串分组，光是长沙
就能拆出几十个选项，他选不过来。城市认的是「·」前面那一段；筛的口径只在
`job_pool.筛行` 一份里——单号和「全部账号」共用，两处各写一套就会同一条
岗位换个范围筛出来的结果不一样。
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.job_pool import 岗位池, 城市名, 城市清单, 筛行   # noqa: E402


def 抓一条(**over):
    j = {"job_name": "数据分析", "company": "某公司", "salary": "8-10K",
         "location": "长沙·岳麓区·麓谷", "city": "长沙·岳麓区·麓谷",
         "url": "https://www.zhipin.com/job_detail/aaa111.html"}
    j.update(over)
    return j


class 城市名Test:
    def test_全地址取第一段(self):
        assert 城市名({"city": "长沙·岳麓区·麓谷"}) == "长沙"
        assert 城市名({"location": "上海·浦东新区·陆家嘴"}) == "上海"

    def test_没有city才回落location(self):
        assert 城市名({"city": "", "location": "北京·朝阳区"}) == "北京"
        assert 城市名({"city": "杭州"}) == "杭州"

    def test_别的分隔符也认(self):
        assert 城市名({"city": "广州 | 天河"}) == "广州"
        assert 城市名({"location": "深圳-南山"}) == "深圳"
        assert 城市名({"location": "成都 高新区"}) == "成都"

    def test_空行不许造出假城市(self):
        assert 城市名({}) == ""
        assert 城市名(None) == ""
        assert 城市名({"city": "   "}) == ""


class 城市清单Test:
    def test_按条数从多到少且同城只一条(self):
        行s = [抓一条(city="长沙·岳麓区"), 抓一条(city="长沙·天心区"),
               抓一条(city="上海"), 抓一条(city="长沙"), 抓一条(city="", location="")]
        清单 = 城市清单(行s)
        assert [x["city"] for x in 清单] == ["长沙", "上海"]
        assert [x["count"] for x in 清单] == [3, 1]


class 筛行Test:
    行s = [
        {"id": "1", "job_name": "数据分析", "company": "甲", "city": "长沙·岳麓区·麓谷",
         "location": "长沙·岳麓区", "status": "new"},
        {"id": "2", "job_name": "数据标注", "company": "乙", "city": "上海·浦东新区",
         "location": "上海", "status": "rejected"},
        {"id": "3", "job_name": "运营", "company": "丙", "city": "长沙·天心区",
         "location": "长沙", "status": "new"},
    ]

    def test_按城市筛是选长沙就只剩长沙(self):
        出 = 筛行(self.行s, 城市="长沙")
        assert {r["id"] for r in 出} == {"1", "3"}

    def test_没选城市就不删任何一条(self):
        assert len(筛行(self.行s, 城市="")) == 3

    def test_城市和状态是交集(self):
        出 = 筛行(self.行s, 状态="new", 城市="长沙")
        assert {r["id"] for r in 出} == {"1", "3"}

    def test_关键字也搜城市和地点(self):
        """以前 q 只搜岗位名/公司/备注：他搜「岳麓」搜不出东西，以为池里没有。"""
        assert {r["id"] for r in 筛行(self.行s, 关键字="岳麓")} == {"1"}
        assert {r["id"] for r in 筛行(self.行s, 关键字="上海")} == {"2"}

    def test_城市按包含筛_长沙县也算长沙(self):
        行s = [{"id": "a", "city": "长沙"}, {"id": "b", "city": "长沙县"}]
        assert {r["id"] for r in 筛行(行s, 城市="长沙")} == {"a", "b"}, "长沙县属于长沙，该留"


class AI匹配筛Test:
    行s = [
        {"id": "1", "ai": {"合": True, "doc": "h1", "判于": 1.0}},
        {"id": "2", "ai": {"合": False, "doc": "h1", "判于": 1.0}},
        {"id": "3", "ai": {"合": True, "doc": "h2", "判于": 1.0}},
        {"id": "4", "ai": {"合": True}},          # 老行：没盖过文件指纹
    ]

    def test_只认当前这份文件下判的合(self):
        assert {r["id"] for r in 筛行(self.行s, AI档="合", 文档指纹="h1")} == {"1"}

    def test_换了文件旧结论不算(self):
        assert {r["id"] for r in 筛行(self.行s, AI档="合", 文档指纹="h2")} == {"3"}

    def test_没有文件时一条都不匹配(self):
        """没指纹却把 ai.doc=='' 当相等，就会把「以前判过」冒充成「按我的要求判过」。"""
        assert 筛行(self.行s, AI档="合", 文档指纹="") == []

    def test_顶部那个AI匹配数字与筛出来的行数同一句判据(self):
        """数字说 12 条、筛出来 10 行，他只会花时间猜哪边在骗人"""
        from boss_bot.job_pool import AI匹配数
        for 指纹 in ("h1", "h2", ""):
            assert AI匹配数(self.行s, 指纹) == len(筛行(self.行s, AI档="合", 文档指纹=指纹))

    def test_接口没答上来的那行不算判成不合(self):
        """合=False 有两种来路：机器真判了不合，和接口报错留下的空结论。
        后者混进「判成不合」，就等于替他说了"AI 说这条不要"，其实它没看。"""
        行s = [{"id": "a", "ai": {"合": False, "判于": 1.0}},
              {"id": "b", "ai": {"合": False, "判于": 1.0, "接口异常": True}},
              {"id": "c", "ai": {}}]
        assert {r["id"] for r in 筛行(行s, AI档="不合")} == {"a"}
        assert {r["id"] for r in 筛行(行s, AI档="判过")} == {"a", "b"}
        assert {r["id"] for r in 筛行(行s, AI档="未判")} == {"c"}


class 落到池子上Test:
    def test_列表接城市参数并照样按分排(self):
        with tempfile.TemporaryDirectory() as td:
            p = 岗位池(account_index=0, 目录=td)
            p.收([抓一条(url="https://zhipin/job/1", city="长沙·岳麓区"),
                  抓一条(url="https://zhipin/job/2", city="上海"),
                  抓一条(url="https://zhipin/job/3", city="长沙·天心区")])
            行s = p.列表(城市="长沙")
            assert len(行s) == 2

    def test_城市统计不受当前筛选影响(self):
        with tempfile.TemporaryDirectory() as td:
            p = 岗位池(account_index=0, 目录=td)
            p.收([抓一条(url="https://zhipin/job/1", city="长沙"),
                  抓一条(url="https://zhipin/job/2", city="上海")])
            清单 = p.城市统计()
            assert {x["city"] for x in 清单} == {"长沙", "上海"}, \
                "下拉里得能选到每个城市：统计要是跟着当前筛选走，一筛就只剩一个选项"
