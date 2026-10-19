# -*- coding: utf-8 -*-
"""招呼语必须发 AI 现编的那一句，固定文案只能兜底。

用户 2026-10-08（这条他说了两遍，第二次明确要求解决）：
「不要用固定的招呼语，要用AI生成的，你解决一下，我看了你之前生成的招呼语，效果挺好的」

取证（data/greet_records.json，今天真发出去的 35 条）：
- 34 条是 AI 现编，1 条是账号里那句固定话（12:19:47 万图科技｜高级文本编辑+AI数据加工）。
  那一条 AI 其实编出来了，172 字，被 sanitize「太长就不用」整条丢掉，才落到固定话。
- 配置里还留着两条岗位手写的招呼语（账号0「数据分析 线上」/ 账号1「数据处理 线上」），
  旧优先级「岗位手写 > AI」让它们永远压着 AI 那句。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.greet_engine import AIAnalyzerChain, GreetEngine, pick_greeting  # noqa: E402
from boss_bot.greeting import sanitize_ai_greeting  # noqa: E402
from boss_bot.reply_record import GreetRecord  # noqa: E402
from boss_bot.unified_config import DEFAULT_GREETING  # noqa: E402

AI_GREETING_172 = (
    "您好，我是在读统计学本科生，有文本/图像/语音多模态数据标注与AI训练语料清洗质检经验，"
    "熟悉标注规范对齐、疑难样本复核与批次交付；同时熟练使用大模型与提示词，能搭建AI辅助标注/"
    "清洗/质检流程，并用Excel/SQL/Python做数据整理与复核。看到贵司『高级文本编辑+AI数据加工』"
    "线上兼职岗位，与我的经验非常匹配，希望能进一步沟通，谢谢！")

RESUME = {"major": "统计学", "degree": "本科", "skills": ["Excel", "SQL", "Python"],
          "experience": "标注与数据整理", "target_position": "数据分析"}
PROFILE = {"available_interview_time": "工作日下午", "position": "数据分析"}


def acc(greeting="账号那句固定的话", city="长沙", query="数据分析"):
    return SimpleNamespace(
        name="主账号", greeting_message=greeting, image_files=[],
        jobs=[SimpleNamespace(city=city, query=query, enabled=True)],
    )


def engine(account):
    from unittest.mock import MagicMock
    e = GreetEngine(MagicMock(), MagicMock(), account_index=0)
    e.config = SimpleNamespace(resume=RESUME, user_profile=PROFILE)
    e._account = lambda: account
    return e


class 取话顺序Test:
    def test_AI现编压过岗位手写(self):
        文, 来源 = pick_greeting("岗位里写死的话", "账号默认", DEFAULT_GREETING,
                                 ai_text="AI 按这个岗位现编的一句")
        assert (文, 来源) == ("AI 按这个岗位现编的一句", "AI 按岗位定制")

    def test_AI编不出来时岗位手写才兜底(self):
        """兜底不是把固定话当主力：AI 真的没给（接口挂了/没过校验）才轮到它，
        不然这一单干脆不发，机会就丢了。"""
        文, 来源 = pick_greeting("岗位里写死的话", "账号默认", DEFAULT_GREETING, ai_text="")
        assert (文, 来源) == ("岗位里写死的话", "岗位配置")

    def test_引擎端到端发的也是AI那句(self):
        e = engine(acc())
        文, 来源 = e._greeting_for({
            "job_name": "数据标注", "company": "某公司",
            "greeting_message": "岗位里写死的话",
            "_ai_suggested_greeting": "您好，看了贵司标注岗，我用 Excel/SQL 做过同类批次。"})
        assert 来源 == "AI 按岗位定制", 来源
        assert "Excel" in 文 and "岗位里写死" not in 文


class 超长不许丢Test:
    def test_线上那条172字的AI话裁成完整一句再用(self):
        句 = sanitize_ai_greeting(AI_GREETING_172)
        assert 句, "AI 编出来了却因为太长被丢掉，发出去的就是固定招呼语"
        assert len(句) <= 140, len(句)
        assert AI_GREETING_172.startswith(句) and 句.endswith("。")

    def test_裁出来的句子仍要过闸门(self):
        assert sanitize_ai_greeting("**您好**\n- 会 SQL\n- 会 Python" + "补" * 200) == ""

    def test_裁不出完整句才整条弃用(self):
        """没有任何停顿可依的字符串裁下去就是半截话，宁可不发也不给 HR 看残句。"""
        assert sanitize_ai_greeting("好" * 300) == ""

    def test_判分提示词把字数说在前面(self):
        """让 AI 一开始就按招呼语的长度写，比事后裁更稳。"""
        chain = AIAnalyzerChain(providers=[], cache_enabled=False)
        chain.set_resume(RESUME)
        提示 = "".join(m["content"] for m in chain._build_prompt(
            {"job_name": "数据标注", "company": "某公司", "url": "https://x/1"}))
        assert "110" in 提示, "提示词没给招呼语字数，AI 会写成复述 JD 的一大段"


class 来源必须看得见Test:
    def test_记录里带招呼语来源(self):
        r = GreetRecord(job_name="数据标注", actual_greeting_sent="AI 现编的一句",
                        greeting_source="AI 按岗位定制", is_greeted=True)
        assert r.to_dict()["greeting_source"] == "AI 按岗位定制"

    def test_发送链把取到的来源写进记录(self):
        import inspect
        源 = inspect.getsource(GreetEngine._record_greet)
        assert "greeting_source" in 源, "记录里只有句子没有来源，用户没法核对是不是 AI 的"
        事 = inspect.getsource(GreetEngine._emit_greet_event)
        assert "greeting_source" in 事, "实时推送不带来源，表格里当场看不到"

    def test_点发送那一刻必须记下来源(self):
        import inspect
        源 = inspect.getsource(GreetEngine._apply_job_inner)
        assert 源.index("_greeting_source") < 源.index("_record_sent_now"), \
            "来源在落库之后才赋值，记录里会是空的"

    def _引擎(self, 已知来源=""):
        """只给 _record_greet 要的那几样，别的都不碰——这条测的就是落库这一格。"""
        存 = []
        引 = GreetEngine.__new__(GreetEngine)
        引._greet_store = type("假库", (), {"add": staticmethod(lambda r: 存.append(r))})()
        引._account_label = lambda: "主账号"
        引.account_index = 0
        引.event_ts = lambda: "2026-10-19 09:00:00"
        引._log = lambda *a, **k: None
        引._greeting_source = 已知来源
        return 引, 存

    def test_抽屉那条路的来源要真的落库(self):
        """上面那两条是"源码里有这个字"，抓不住真丢的那次。

        2026-10-18 的日志里「打招呼语来源: AI 按岗位定制」出现了 110 次，可记录里
        带正文的 70 条这一栏全是空的——句子存了、来源没存。他要查的正是
        "有没有用固定话术"这一半，空着等于没答。
        """
        引, 存 = self._引擎(已知来源="AI 按岗位定制")
        引._record_greet({"job_name": "数据标注", "url": "https://x/1"},
                         is_greeted=True, actual_greeting_sent="您好，我是统计学在读")
        assert 存, "没落库"
        assert 存[0].to_dict()["greeting_source"] == "AI 按岗位定制", \
            "岗位字典上没带来源时，引擎已经知道的那一句来源被丢了"

    def test_岗位字典上写了来源但一单没发也不许落(self):
        """来源是在点「立即沟通」之前就写到岗位字典上的，所以跳过那类记录
        也带着它。2026-10-19 有 33 条这个样子：来源=AI 按岗位定制、正文空——
        这等于告诉用户"这句 AI 现编发出去了"，其实压根没投。"""
        引, 存 = self._引擎(已知来源="")
        引._record_greet({"job_name": "数据标注", "url": "https://x/3",
                          "_greeting_source": "AI 按岗位定制"},
                         is_skipped=True, skip_reason="打招呼已冷却")
        assert 存[0].to_dict()["greeting_source"] == "", \
            "没发出去的记录从岗位字典上把来源抄了下来"


    def test_没发出去的那条不许顶用上一条的来源(self):
        """兜底值是本号引擎级的一份"最后一次定下来的来源"，跳过那类记录不能借用
        ——本号连着投十个岗位，第九个的发出去那句是 AI 现编，第十个压根没点沟通，
        第十个的记录写着"AI 按岗位定制"就是张冠李戴。"""
        引, 存 = self._引擎(已知来源="AI 按岗位定制")
        引._record_greet({"job_name": "别的岗", "url": "https://x/2"},
                         is_skipped=True, skip_reason="打招呼已冷却")
        assert 存[0].to_dict()["greeting_source"] == "", \
            "这条根本没发话，却把上一单的来源写上了"
