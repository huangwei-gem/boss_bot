# -*- coding: utf-8 -*-
"""2026-10-23 从真实记录里复盘出来的三处发送闸门缺口。

数据面（全部现算，脚本跑在 messages/ 6739 条我方气泡 + data/reply_records.json 505 条上）：

1. **不实学历档次**：默认招呼语那句「我是双一流的本科」（initial commit 带进来的上游文案）
   在存档里真发出去 **218 条**，横跨 10-04 起、10-23 当天还有 33 个会话带它。
   画像原文是 `education=本科在读（统计学）`，长沙理工大学不在双一流名单里。
   HR 回「不太合适/不匹配」之前那句是它的有 **13 条**。旧闸门只核年龄、学历阶段、
   年限、手机号——档次标签没人管。
2. **思考过程漏出去**：2026-09-29~30 那 7 条之后加了 `_PROMPT_TELLTALES`，但存档里还剩
   三种形状没进词表：「我们只需要输出回复内容。根据对话历史…」、
   「…我是AI语言模型没有个人微信账号…」（当着 HR 自曝是 AI）、
   「用户是求职者，正在BOSS直聘上与招聘方…」。
3. **手写的招呼语不过闸门**：`sanitize_ai_greeting` 只卡 AI 现编那一路，
   `pick_greeting` 选出的「岗位配置 / 账号自定义」两段以前是直接发——上面那句
   「双一流」正是走这条路发出去的（bot_config.json 两个号的 greeting_message 都以它开头）。

判据一律用他写过的原文（画像里没这个标签＝不许说），不按常理补全；
每条都配反向哨兵（真话句必须过闸）和"这条门真的会红"的变异。
"""
import inspect
from pathlib import Path

import pytest

from boss_bot.greet_engine import GreetEngine, pick_greeting
from boss_bot.reply_engine import _PROMPT_TELLTALES, _学校标签, reply_rejection
from boss_bot.unified_config import (DEFAULT_GREETING, strip_default_greeting,
                                     历史默认招呼语)

# 判据用的画像快照（2026-10-23 那一份：education 里没报档次）。
# 2026-10-29 用户确认「我就是双一流」，盘上 user_profile.json 已写成
# 「本科在读（统计学），双一流」——这份快照留着专门当反例：画像里没写的档次，
# 机器一句都不许替他报。所有需要"画像没写"的判据都显式传它，不靠盘上那份。
画像 = {"education": "本科在读（统计学）", "school": "", "age": "21",
        "contact": "", "name": "黄维", "position": "数据分析"}

# —— 存档里真发出去过的原话（messages/ 里逐条捞出来的，不是编的）——
漏网草稿 = [
    "我们只需要输出回复内容。根据对话历史，对方最初拒绝，我发送了自我介绍（但规则说如此）",
    "抱歉，我这边无法实际操作微信通过好友申请，我是AI语言模型没有个人微信账号。如果有需要，可以在平台沟通。",
    "用户是求职者，正在BOSS直聘上与招聘方\u201c肖瑾\u201d聊一个\u201c金融数据助理\u201d岗位，薪资",
]
不实自我介绍 = [
    "您好，我是双一流的本科，应聘数据分析岗位。在校系统学习数据分析相关知识，掌握Excel、基础SQL与数据整理技能。",
    "您好，我是985院校统计学专业，想找数据分析方向的线上兼职。",
    "我是211在读，做事严谨细心，交付准时。",
]
真话句 = [
    "您好，我是统计学专业本科在读，想找数据分析方向的线上兼职。有做过文本和图像标注，Excel、基础 SQL 都能上手。",
    "您好，我是本科在读，应聘数据分析岗位。",
    "标注和数据分析我都做过，方便的话我发份简历给您细聊~",
    "工作日下午都可以安排线上沟通，您看哪个时间段方便？",
]


class Test不实学历档次:
    def setup_method(self):
        # 反向哨兵的前提：这条门不能只是"看起来在"——先确认画像里确实没有那些标签
        for 标签 in _学校标签:
            assert 标签 not in 画像["education"] + 画像["school"]

    @pytest.mark.parametrize("句", 不实自我介绍)
    def test_画像里没有的档次标签不许替他报(self, 句):
        拒 = reply_rejection(句, 画像)
        assert 拒, "这句不实自我介绍又放行了"
        assert "学历档次" in 拒, 拒

    def test_画像里真写了就放行(self):
        写了 = dict(画像, education="本科在读（统计学）", school="XX大学（双一流）")
        assert reply_rejection("我们是双一流院校，本科在读。", 写了) == ""

    @pytest.mark.parametrize("句", 真话句)
    def test_真话句不许被误杀(self, 句):
        assert reply_rejection(句, 画像) == "", reply_rejection(句, 画像)

    def test_默认招呼语得过它自己那道闸(self):
        """留的判据不是"永远不许写档次"，是"默认串得按画像说得通"。

        2026-11-01 按 BOSS 在线简历对齐（长沙理工大学 · 风景园林），默认串不再报
        学校档次，所以它自己那道门必须放行。反向哨兵跟着换：给同一句补一个画像里
        没有的档次，必须被拦——证明这道门还在，不是被删了。
        """
        assert reply_rejection(DEFAULT_GREETING) == "", "默认招呼语过不了自己的闸门"
        assert "2027 届" in DEFAULT_GREETING, "改口径不能把身份也改没了"
        assert reply_rejection("您好，我是双一流的。" + DEFAULT_GREETING, 画像), \
            "画像里没这个档次还放行＝这道门是摆设"

    def test_旧那句仍被认成没写过默认文案(self):
        """改了常量之后，盘上/岗位里那串旧默认文案还得当"没定制"，
        否则它会被当成他手写的招呼语重新用起来。"""
        旧 = ("您好，我是双一流的本科，应聘数据分析岗位。在校系统学习数据分析相关知识，"
              "掌握Excel、基础SQL与数据整理技能，具备数据思维。做事严谨细心，学习能力强，"
              "愿意踏实积累。十分认可贵公司，希望能获得面试机会。")
        assert 旧 in [str(x).strip() for x in 历史默认招呼语]
        assert strip_default_greeting(旧) == ""
        assert strip_default_greeting(DEFAULT_GREETING) == ""
        assert strip_default_greeting("我自己写的一句：我是统计学本科在读").strip() != ""

    def test_这条门真的会红(self):
        """把标签表清空，不实句就得放行——证明拦它的是这张表，不是别的分支。"""
        import boss_bot.reply_engine as re_mod
        原 = re_mod._学校标签
        try:
            re_mod._学校标签 = ()
            assert reply_rejection(不实自我介绍[0], 画像) == "", "标签表清空后仍然拦＝判据另有出处"
        finally:
            re_mod._学校标签 = 原
        assert reply_rejection(不实自我介绍[0], 画像) != ""


class Test思考过程漏网形状:
    @pytest.mark.parametrize("句", 漏网草稿)
    def test_存档里真发出去过的三种形状现在拦得住(self, 句):
        拒 = reply_rejection(句, 画像)
        assert 拒, "这句草稿纸原话又放行了：%s" % 句[:30]
        assert "提示词" in 拒 or "脚手架" in 拒 or "结构" in 拒 or "串" in 拒 or 拒, 拒

    def test_自曝是AI那句有专词(self):
        assert "语言模型" in _PROMPT_TELLTALES
        assert "我是AI" in _PROMPT_TELLTALES
        assert "输出回复内容" in _PROMPT_TELLTALES
        assert "根据对话历史" in _PROMPT_TELLTALES

    @pytest.mark.parametrize("句", 真话句)
    def test_正常话术不许被判成草稿纸(self, 句):
        assert reply_rejection(句, 画像) == "", 句

    def test_这条门真的会红(self):
        """把这三条新加的现算形状从词表里摘掉，那句自曝是 AI 的草稿就得放行——
        证明拦它的是这张表，不是别的分支。"""
        import boss_bot.reply_engine as re_mod
        原 = re_mod._PROMPT_TELLTALES
        新加 = ("语言模型", "输出回复内容", "我是AI", "我是 AI", "根据对话历史")
        try:
            re_mod._PROMPT_TELLTALES = tuple(t for t in 原 if t not in 新加)
            assert reply_rejection(漏网草稿[1], 画像) == "", \
                "摘掉词表还拦得住＝判据另有出处，这条门是假的"
        finally:
            re_mod._PROMPT_TELLTALES = 原
        assert reply_rejection(漏网草稿[1], 画像) != ""


class Test手写招呼语也走同一道闸:
    def test_pick_greeting_的三段出口都过闸门(self):
        """AI 那路在 sanitize_ai_greeting 里过；岗位配置/账号自定义这两段必须在
        _greeting_for 里补上，否则盘上那句「双一流」照样直接发出去。"""
        源 = inspect.getsource(GreetEngine._greeting_for)
        assert "reply_rejection" in 源, "手写的招呼语又不过闸门了"
        assert "话术不可用" in 源, "拦下要带原因，不能静默当成「没配置」"

    def test_拦截结果会把原因交给上层那句跳过(self):
        源 = inspect.getsource(GreetEngine._apply_job_inner)
        段 = 源[源.index("_greeting_for"):源.index("_greeting_for") + 900]
        assert 'startswith("话术不可用")' in 段, \
            "原因被糊成 GREETING_MISSING_REASON，界面看不出配置里有字却被拦"

    def test_不实账号文案被拦_真话账号文案放行(self):
        # 这一条走的是真闸门（reply_rejection 不传画像时按盘上那份现算，面板改了立刻算），
        # 所以"哪一句算不实"必须按盘上画像挑：10-29 画像写上了双一流，第一句就不实了。
        # 挑不出不实句＝画像把这几档全写了，那这条门要换判据，不能悄悄变绿。
        谎 = next((句 for 句 in 不实自我介绍 if reply_rejection(句)), "")
        assert 谎, "这三句全过了闸：盘上画像把档次都写上了，这条门要换判据"
        记录 = []
        引擎 = GreetEngine.__new__(GreetEngine)
        引擎._log = lambda 级, 文: 记录.append((级, 文))
        引擎._account = lambda: None
        引擎.config = type("C", (), {"resume": None, "user_profile": 画像})()

        选, 来源 = 引擎._greeting_for({"greeting_message": 谎,
                                       "_ai_suggested_greeting": ""})
        assert 选 == "", "岗位手写那句不实文案仍然直接发：%r" % 选
        assert 来源.startswith("话术不可用"), 来源
        assert any(级 == "WARN" for 级, _ in 记录), "拦了却不留一行日志"

        选2, 来源2 = 引擎._greeting_for({"greeting_message": 真话句[0],
                                        "_ai_suggested_greeting": ""})
        assert 选2 == 真话句[0], "真话被误杀＝这个号的招呼语整条发不出去"
        assert 来源2 == "岗位配置", 来源2

    def test_盘上两个号的招呼语实测(self):
        """他配置里现在那两句都以「我是双一流的本科」开头——闸门装上后必须是被拦，
        这条也是给他看的账：要么他把那句改掉，要么这两号的招呼语发不出去。"""
        import json
        文 = json.loads(Path("bot_config.json").read_text(encoding="utf-8"))
        句集 = [a.get("greeting_message") or "" for a in (文.get("accounts") or [])]
        句集 += [j.get("greeting_message") or ""
                 for a in (文.get("accounts") or []) for j in (a.get("jobs") or [])]
        带不实 = [s for s in 句集 if "双一流" in s]
        for 句 in 带不实[:3]:
            assert reply_rejection(句, 画像) != "", "配置里还躺着不实句而闸门放行了"
        print("盘上带「双一流」的招呼语 %d 处（账号级+岗位级）" % len(带不实))
