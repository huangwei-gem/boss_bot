# -*- coding: utf-8 -*-
"""求职者视角的投递产出：方向对、能线上做完的岗，不能因为"HR 没写 JD"就被当成引流杀掉。

取证来自 tools/audit_jobseeker_view.py：拿今天的判据重放机器真看过的 1774 个岗，
288 个"这单按他自己的口径该投"仍被杀掉，其中 140 个死在
`JD 没写具体工作内容（正文<80 字按引流处理）`那一条——样本是「外贸数据分析」
「数据标注/AI训练师」「视频数据标注/AI训练师」，BOSS 上这类兼职岗正文经常就一句
甚至空着，标题已经把活写明白了。真引流的那类挂法不一样：「兼职·高薪日结岗位」
「急招！来看看再说」「数据录入｜日入300手机电脑都行」——标题没方向词，或者一开口
就要钱/要人。

方向那一遍不归这里管：他点名的方向只有
`user_profile.json.position` =「线上兼职（数据标注/AI数据标注、数据分析、线上运营助理）」，
所以这一条测"标题说清了就不算空泛"，并守住方向表没被悄悄放宽到别的行当。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boss_bot.greet_engine import jd_gate, title_states_the_job
from boss_bot.unified_config import TITLE_VETO_KEYWORDS_DEFAULT, UnifiedConfig

ROOT = Path(__file__).resolve().parents[1]
# 方向表按正本那张配置读：这张表是他自己维护的口径，测试里再抄一份就会和盘上漂移
_cfg = UnifiedConfig.load(str(ROOT / "bot_config.json"),
                          overrides_path=str(ROOT / "__none__.json"))
方向表 = list(_cfg.ai.target_job_keywords or [])
否决表 = ["仅线下", "不接受远程", "需坐班", "坐班", "线下驻场", "驻场", "外勤", "到店",
          "包吃住", "无门槛", "零门槛", "刷单", "打字员", "手工活", "中介", "配送", "住宿"]
类型表 = list(TITLE_VETO_KEYWORDS_DEFAULT)   # 岗位类型那遍用正本那张表，别自己抄一份


def 闸(标题, 正文=""):
    return jd_gate(否决表, {"job_name": 标题, "jd_description": 正文, "description": 正文},
                   title_keywords=类型表, target_keywords=方向表)


class 空泛那一条Test:
    def test_标题把方向说清就不算空泛(self):
        """这些 10-10 到 10-16 都真被"没写具体工作内容"拦过，正文空着也照样该投。"""
        assert not 闸("外贸数据分析", "")
        assert not 闸("AI产品经理（长沙）", "")
        assert not 闸("数据分析师 8-12K", "")
        assert not 闸("AI产品｜大模型方向", "")

    def test_方向都没写清的仍然杀(self):
        """他 10-10 要拦的那类还在拦，只是换个死法：先被方向闸杀。"""
        assert 闸("兼职·高薪日结岗位", "")
        assert 闸("急招！", "来看看再说")
        # 这条才是"空泛"那一条自己杀的：方向词命中了，但标题是引流形状
        assert "没写具体工作内容" in 闸("数据分析｜日入300手机电脑都行", "")

    def test_标题写着到岗的正文薄照样不投(self):
        """方向对但标题写着到岗、正文又空着——按"没写具体工作内容"拦。"""
        assert 闸("AI产品经理（需到公司）", "")
        assert 闸("数据分析兼职（可转正-线下面试和到岗）", "")

    def test_标题命中方向但带否决词的仍然杀(self):
        assert "否决词" in 闸("数据分析（需坐班）", "")
        assert "否决词" in 闸("数据分析｜包吃住进厂", "")

    def test_认否定式(self):
        assert "没写具体工作内容" not in 闸("数据分析｜不用坐班", "")

    def test_正文写足了的不用豁免(self):
        """豁免口只为"标题说清了但 HR 没写 JD"开；正文本来就长的走正常那条路。"""
        长 = ("负责业务数据清洗、指标口径核对与周报看板搭建，配合业务方做专题分析。"
              "要求熟练使用 Excel、SQL、Python，能独立完成数据整理与结论输出。") * 2
        assert not 闸("数据分析师", 长)


class 方向那一路Test:
    def test_标注族不再是方向(self):
        """2026-11-01 用户口径：BOSS 上不需要数据标注岗位了。

        正文里"标注"只是给话务打标签的，本来就不算；现在标题里写标注的也不再放行——
        这条锁的是"删了配置里的词就真的不进来"，不是代码里还兜着半壁。
        """
        assert "不是要投的方向" in 闸("话务专员（线上兼职）",
                                  "负责接听来电，按要求标注通话场景的质检标签，记录工单。" * 3)
        assert "不是要投的方向" in 闸("在线标注专员（兼职）", "")
        assert "不是要投的方向" in 闸("视频数据标注/AI训练师", "")

    def test_他没点名的行当仍然按不是方向杀掉(self):
        """剪辑/代练/运营这些不在他点名的方向里——要放得他自己在面板上填，不是测试默认。"""
        for 标题 in ("线上视频剪辑",
                     "游戏代练线上接单日结100-300元长沙", "新媒体运营（线上）"):
            判 = 闸(标题, "工作周期：长期兼职，每周工期无要求，具体工作内容会在沟通后说明，"
                          "需要会用办公软件，能按平台要求交付批次任务并按条结算报酬。")
            assert "不是要投的方向" in 判, f"{标题} 不该被算成他的方向"

    def test_客服那一族现在死在否决词上(self):
        """2026-10-17 用户原话「客服不要哈」：它不再是"方向不对"，是直接不要。"""
        判 = 闸("网络客服专员（线上兼职 接受无经验）",
                "负责在线咨询回复，无销售性质，居家办公，按排班在线。")
        assert "否决词" in 判 and "客服" in 判, 判

    def test_方向表没被悄悄放宽到别的行当(self):
        """10-09 那条警示的机器版：有人嫌投得少去松这张闸，这张表最先被加词。

        他点名的方向写在 user_profile.position 里（数据标注/AI数据标注、数据分析、
        线上运营助理）+ 2026-10-17 他自己追加的「去找一些数据分析的兼职之类的，
        或者办公问题的，飞书处理问题类的一些问题，兼职的都可以去找」。
        这张表只许是这两句原话覆盖的行当；往里塞别的属于政策改动，得他点头。
        """
        许 = {"AI产品经理", "AI产品", "大模型产品", "AIGC产品", "智能体产品",
              "Agent产品", "人工智能产品", "AI应用",
              "数据分析", "数据分析师", "商业分析", "经营分析", "数据建模",
              "数据仓库", "数据清洗", "指标体系", "BI", "数据专员", "报表分析"}
        超出 = [w for w in 方向表 if w not in 许]
        assert not 超出, f"方向表里多出这些行当：{超出}"


class 标题说清判据Test:
    """title_states_the_job 单独锁：它是"空泛"这一条唯一的豁免口。"""

    def test_方向判据和正向闸同一个口径(self):
        """豁免口不能自己再数一遍词：方向只认配置那张表，这里跟着它走。"""
        assert title_states_the_job("外贸数据分析", 方向表)
        assert title_states_the_job("AI产品经理（长沙）", 方向表)
        assert not title_states_the_job("高薪日结急招", 方向表)
        assert not title_states_the_job("数据分析", [])
        # 标注族已经不在方向表里，代码也不再兜底——它不该再被豁免
        assert not title_states_the_job("在线标注专员（兼职）", 方向表)

    def test_到场字样一票否决(self):
        for 标题 in ("数据分析（到公司办公）", "AI产品经理·需驻场", "数据分析 现场面试"):
            assert not title_states_the_job(标题, 方向表), 标题

    def test_引流形状一票否决(self):
        """方向词也在，但这种挂法就是来要人的。"""
        for 标题 in ("数据分析｜日入300", "AI产品经理 副业天花板", "数据分析 交押金上岗",
                     "AI产品｜手机电脑都行"):
            assert not title_states_the_job(标题, 方向表), 标题

    def test_方向词要来自配置那张表(self):
        """表交给配置（面板能改），判据自己不藏词。"""
        assert not title_states_the_job("外贸数据分析", ["剪辑", "运营"])
        assert title_states_the_job("外贸数据分析", ["数据分析"])
