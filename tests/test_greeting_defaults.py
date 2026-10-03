# -*- coding: utf-8 -*-
"""账号默认招呼语与三档来源的解析链。

口径来自用户 2026-10-03 的要求：招呼语不能再"留空就整轮跳过"——每个账号先按
这个账号自己的信息给一条默认（可改），发送时再由 AI 按岗位+公司+JD 现编一条。
所以优先级是：岗位手写 > AI 按岗位定制 > 账号默认 > 三条都没有才算没配。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from boss_bot.greeting import (  # noqa: E402
    account_greeting_mode, compose_account_default, ensure_account_default,
    sanitize_ai_greeting)
from boss_bot.unified_config import DEFAULT_GREETING  # noqa: E402
from tests.test_config_hot_reload import flask_app  # noqa: E402,F401  跨文件复用夹具

RESUME = {
    "school": "某某大学",          # 占位值：绝不能出现在发给 HR 的话里
    "major": "统计学",
    "degree": "本科",
    "skills": ["Excel", "SQL", "Python"],
    "experience": "负责业务数据报表与洞察分析",
    "target_position": "数据分析师",
}
PROFILE = {"available_interview_time": "工作日下午", "position": "数据分析"}


def acc(city="长沙", query="数据分析", images=None, greeting="", name="主账号"):
    return SimpleNamespace(
        name=name,
        greeting_message=greeting,
        image_files=images if images is not None else [],
        jobs=[SimpleNamespace(city=city, query=query, enabled=True)],
    )


class ComposeAccountDefaultTest:

    def test_默认里带上这个账号自己的城市和方向(self):
        text = compose_account_default(acc(city="上海", query="数据分析"), RESUME, PROFILE)
        assert "上海" in text
        assert "数据分析" in text

    def test_两个账号的默认不是同一句话(self):
        """用户要的是"每个账号不一样"：城市/作品集这些按号走，不能一套话术发两边。"""
        a = compose_account_default(acc(city="长沙", images=["dashboard/a.png"]), RESUME, PROFILE)
        b = compose_account_default(acc(city="上海", images=[]), RESUME, PROFILE)
        assert a != b

    def test_有作品图的账号才提作品(self):
        with_img = compose_account_default(acc(images=["dashboard/a.png"]), RESUME, PROFILE)
        without = compose_account_default(acc(images=[]), RESUME, PROFILE)
        assert "作品" in with_img
        assert "作品" not in without

    def test_不写占位学校(self):
        text = compose_account_default(acc(), RESUME, PROFILE)
        assert "某某大学" not in text

    def test_带上技能和可面试时间(self):
        text = compose_account_default(acc(), RESUME, PROFILE)
        assert "SQL" in text
        assert "工作日下午" in text

    def test_没有岗位没有画像也不会给出空话术(self):
        bare = SimpleNamespace(name="新号", greeting_message="", image_files=[], jobs=[])
        text = compose_account_default(bare, {}, {})
        assert text.strip()

    def test_方向和技能不相关时不硬凑技能(self):
        """账号2 现在找的是 AI 漫剧，把 SQL/Excel 塞进招呼语就是答非所问。"""
        text = compose_account_default(acc(city="长沙", query="AI漫剧"), RESUME, PROFILE)
        assert "AI漫剧" in text
        assert "SQL" not in text


class GreetingModeTest:
    """界面要能说出这句话是谁的：存的默认文本本身也算"系统生成的"。"""

    def test_生成的默认落盘后仍认得出是默认(self):
        from boss_bot.greeting import account_greeting_mode
        a = acc(greeting="")
        ensure_account_default(a, RESUME, PROFILE)
        assert a.greeting_message.strip()          # 已经写进配置，界面看得见
        assert account_greeting_mode(a, RESUME, PROFILE) == "自动生成的默认"

    def test_改过一个字就算自己写的(self):
        from boss_bot.greeting import account_greeting_mode
        a = acc(greeting="")
        ensure_account_default(a, RESUME, PROFILE)
        a.greeting_message = a.greeting_message + "（这句是我加的）"
        assert account_greeting_mode(a, RESUME, PROFILE) == "账号自写"


class EnsureAccountDefaultTest:

    def test_没写过就落一条默认(self):
        a = acc(greeting="")
        assert ensure_account_default(a, RESUME, PROFILE) is True
        assert a.greeting_message.strip()

    def test_自己写过的不覆盖(self):
        a = acc(greeting="我自己想的话术")
        assert ensure_account_default(a, RESUME, PROFILE) is False
        assert a.greeting_message == "我自己想的话术"

    def test_等于历史默认串算没写(self):
        """pick_greeting 把"等于 DEFAULT_GREETING"当成未定制，这里必须同一口径，
        否则配置里看着有字、实际一轮下来还是全跳过。"""
        a = acc(greeting=DEFAULT_GREETING)
        assert ensure_account_default(a, RESUME, PROFILE) is True
        assert a.greeting_message != DEFAULT_GREETING


class ResolveViaEngineTest:
    """引擎侧真的按这三档取：判分链现编的那条必须被用上。"""

    def _engine(self, account):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from boss_bot.greet_engine import GreetEngine
        e = GreetEngine(MagicMock(), MagicMock(), account_index=0)
        e.config = SimpleNamespace(resume=RESUME, user_profile=PROFILE)
        e._account = lambda: account
        return e

    def _job(self, **kw):
        job = {"job_name": "数据分析师", "company": "某公司", "greeting_message": ""}
        job.update(kw)
        return job

    def test_判分链给的那条会用上(self):
        e = self._engine(acc(greeting=""))
        text, src = e._greeting_for(self._job(
            _ai_suggested_greeting="您好，看了贵司的数据分析 JD，我用 SQL 做过同类看板。"))
        assert src == "AI 按岗位定制"
        assert "SQL" in text

    def test_AI给的过不了校验就退回账号那句话(self):
        e = self._engine(acc(greeting="账号自己写的那句话"))
        text, src = e._greeting_for(self._job(
            _ai_suggested_greeting="**分析：**\n- 岗位匹配\n- 技能匹配"))
        assert (text, src) == ("账号自己写的那句话", "账号自定义")

    def test_账号没写时用按本账号信息生成的默认(self):
        e = self._engine(acc(city="上海", images=[]))
        text, src = e._greeting_for(self._job())
        assert src == "账号自定义"
        assert "上海" in text

    def test_岗位里手写的仍然最大(self):
        e = self._engine(acc(greeting="账号那句"))
        text, src = e._greeting_for(self._job(
            greeting_message="这个岗位就用这句",
            _ai_suggested_greeting="AI 现编的一句"))
        assert (text, src) == ("这个岗位就用这句", "岗位配置")


class SanitizeAiGreetingTest:

    def test_空白的不用(self):
        assert sanitize_ai_greeting("   ") == ""

    def test_带markdown结构的不用(self):
        assert sanitize_ai_greeting("**您好**\n- 会 SQL\n- 会 Python") == ""

    def test_像在复述提示词的不用(self):
        assert sanitize_ai_greeting("用户是求职者，我需要分析这个招聘岗位") == ""

    def test_太长的不用(self):
        assert sanitize_ai_greeting("好" * 300) == ""

    def test_正常一句话照用(self):
        ok = "您好，我做过三份数据分析实习项目，方便把简历发您看看吗？"
        assert sanitize_ai_greeting(ok) == ok


class SuggestEndpointTest:
    """面板上那个「重新生成」按的是账号自己的信息；写过的号不能被动辄抹掉。"""

    def _client(self, flask_app, accounts):
        import json
        FLASK_APP, tmp = flask_app
        (tmp / "bot_config.json").write_text(json.dumps({
            "accounts": accounts,
            "resume": {"major": "统计学", "degree": "本科",
                       "skills": ["SQL", "Excel"], "target_position": "数据分析师"},
            "user_profile": {"available_interview_time": "工作日下午"},
        }, ensure_ascii=False), encoding="utf-8")
        FLASK_APP._config = None
        return FLASK_APP, tmp

    def test_生成结果写进这个账号自己的配置(self, flask_app):
        import json
        accs = [{"name": "主账号", "greeting_message": "",
                 "jobs": [{"city": "长沙", "query": "数据分析"}]},
                {"name": "账号2", "greeting_message": "我自己写的那句",
                 "jobs": [{"city": "上海", "query": "AI漫剧"}]}]
        FLASK_APP, tmp = self._client(flask_app, accs)
        body = FLASK_APP.app.test_client().post(
            "/api/accounts/greeting_suggest", json={"index": 0}).get_json()
        assert body["status"] == "ok"
        assert "长沙" in body["greeting_message"]
        saved = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
        assert saved["accounts"][0]["greeting_message"] == body["greeting_message"]
        assert saved["accounts"][1]["greeting_message"] == "我自己写的那句"

    def test_已经写过的号不确认就不覆盖(self, flask_app):
        import json
        accs = [{"name": "主账号", "greeting_message": "我自己写的那句",
                 "jobs": [{"city": "长沙", "query": "数据分析"}]}]
        FLASK_APP, tmp = self._client(flask_app, accs)
        r = FLASK_APP.app.test_client().post(
            "/api/accounts/greeting_suggest", json={"index": 0})
        body = r.get_json()
        assert body.get("need_confirm"), "没确认就覆盖了用户自己写的话"
        saved = json.loads((tmp / "bot_config.json").read_text(encoding="utf-8"))
        assert saved["accounts"][0]["greeting_message"] == "我自己写的那句"


class ResolveTierTest:
    """三档优先级：岗位手写 > AI 定制 > 账号默认 > 没配。"""

    def _pick(self, *args, **kw):
        from boss_bot.greet_engine import pick_greeting
        return pick_greeting(*args, **kw)

    def test_岗位手写字压过一切(self):
        text, src = self._pick("岗位里写死的话", "账号默认", DEFAULT_GREETING, ai_text="AI 现编")
        assert (text, src) == ("岗位里写死的话", "岗位配置")

    def test_没有手写用AI现编(self):
        text, src = self._pick("", "账号默认", DEFAULT_GREETING, ai_text="AI 现编的招呼语")
        assert (text, src) == ("AI 现编的招呼语", "AI 按岗位定制")

    def test_AI没给就用账号默认(self):
        text, src = self._pick("", "账号默认", DEFAULT_GREETING, ai_text="")
        assert (text, src) == ("账号默认", "账号自定义")

    def test_三档都空仍然算没配置(self):
        """默认是"给一条可发的"，不是"什么都能发"：连账号都没默认时照旧拦住不发。"""
        text, src = self._pick("", "", DEFAULT_GREETING, ai_text="")
        assert text == "" and src == "未配置"

    def test_旧调用少传参数照旧可用(self):
        assert self._pick("", "账号默认", DEFAULT_GREETING)[0] == "账号默认"
