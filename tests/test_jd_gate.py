# -*- coding: utf-8 -*-
"""JD 到手后的第二道闸门：空泛 JD、工厂岗、到场岗一律拦在点「立即沟通」之前。

用户的口径原话："那种工厂的也不要""一看 jd 没写具体的工作内容的一看就是诈骗
内容就直接拒绝就行了"。之前这两条只写进了提示词和标题级关键词——存档里的岗位
对象根本没有 JD 正文，等于没拦。现在详情页读完 JD 再判一次，判完才允许点。

门槛 80 字是现算的：日志里 934 个取到 JD 的岗位，中位 316 字、p10 还有 146 字，
低于 80 字只有 15 个（1.6%），其中 2 个正文一个字都没有。
"""
import inspect

import pytest

from boss_bot.greet_engine import JD_THIN_CHARS, jd_gate, veto_keyword_hit

WORDS = ["进厂", "工厂直招", "车间", "普工", "驻场", "外勤", "到店", "包吃住"]
# 长度按日志里的真实分布取：934 个岗位里最短的那一成（p10）也还有 146 字，
# 这条 150 字上下的正文就是"刚刚够被放行"的形状。
FULL_JD = ("工作周期：长期兼职，每周工时不低于 20 小时，全程线上办公，不用到岗。\n"
           "岗位职责：负责电商平台每日销售数据的清洗与汇总，用 SQL 取数、"
           "Python 做可视化周报，并向运营负责人输出结论；"
           "兼职期间按周结算，日结或月结可选。")


class Test否决词直查:
    def test_命中正文里的词(self):
        job = {"job_name": "数据录入", "jd_description": "到岗面试，进厂流水线，包吃住"}
        assert veto_keyword_hit(WORDS, job) == "进厂"

    def test_标题干净也照样查得到(self):
        """判分链只看标题那会儿，这类词全都漏过去了"""
        job = {"job_name": "数据分析", "company": "某科技", "jd_requirements": "需驻场"}
        assert veto_keyword_hit(WORDS, job) == "驻场"

    def test_没配词时什么都拦不住(self):
        assert veto_keyword_hit([], {"job_name": "进厂"})== ""

    def test_空白词不顶位(self):
        assert veto_keyword_hit(["", "  "], {"job_name": "随便"}) == ""

    def test_字段缺项不炸(self):
        assert veto_keyword_hit(WORDS, {"jd_description": None}) == ""


class Test空泛JD:
    def test_正文太短挡下(self):
        job = {"job_name": "线上兼职", "jd_description": "工作轻松，待遇好，详情面谈"}
        assert "没写具体工作内容" in jd_gate([], job)

    def test_一个字都没有也挡(self):
        assert jd_gate([], {"jd_description": ""}) != ""

    def test_写清楚工作量的放行(self):
        assert jd_gate([], {"jd_description": FULL_JD}) == ""

    def test_正文写在任职要求那一栏也算数(self):
        """只量 .job-sec-text 的话，把内容写在要求栏的正规岗位会被误杀"""
        assert jd_gate([], {"jd_description": "", "jd_requirements": FULL_JD}) == ""

    def test_门槛就是注释里那个数(self):
        assert JD_THIN_CHARS == 80

    def test_长度按去掉空白算(self):
        """BOSS 的正文里全是换行和缩进，不 strip 的话空话也能凑够字数"""
        job = {"jd_description": "\n  \n\t" * 30}
        assert jd_gate([], job) != ""


class Test闸门位置:
    def test_否决词命中就不发招呼(self):
        job = {"job_name": "数据分析", "jd_description": "进厂流水线作业" + FULL_JD}
        assert "命中否决词" in jd_gate(WORDS, job)

    def test_拦在点沟通之前(self):
        """顺序错了就是"已经发出去了再说不合适"——那一下点下去收不回来。"""
        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine._apply_job_inner)
        assert "jd_gate(" in src, "JD 闸门没接进投递链路"
        assert src.index("jd_gate(") < src.index("chat_btn.click()"), \
            "闸门排在点击之后，等于没拦"

    def test_拦下来说得清是谁(self):
        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine._apply_job_inner)
        seg = src[src.index("jd_gate("):src.index("jd_gate(") + 400]
        assert "job_name" in seg, "跳过原因里要带上是哪个岗位，不然日志没法核"

    @pytest.mark.parametrize("job,expect_pass", [
        ({"jd_description": FULL_JD}, True),
        ({"jd_description": "日结 钱多 事少"}, False),
    ])
    def test_两条真实形状(self, job, expect_pass):
        assert (jd_gate(WORDS, job) == "") is expect_pass
