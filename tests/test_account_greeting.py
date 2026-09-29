"""按账号自定义招呼语（task #30 的一半）。

用户有两段现场：一段是自己点开抽屉再输入发送，另一段是 BOSS 点了"立即沟通"
就把预设招呼语自动发出去了。无论哪种，招呼语都该是"这个账号自己的"，
而不是历史里每个岗位都塞着的同一段默认文案。
"""

import json
import unittest

from boss_bot.unified_config import DEFAULT_GREETING, UnifiedConfig
from boss_bot.greet_engine import pick_greeting


class PickGreetingTest(unittest.TestCase):
    def test_岗位改过才算岗位自定义(self):
        text, source = pick_greeting("岗位专用招呼语", "账号专用招呼语", DEFAULT_GREETING)
        self.assertEqual((text, source), ("岗位专用招呼语", "岗位配置"))

    def test_岗位没改过就落到账号自定义(self):
        """历史配置里每个岗位的 greeting 都等于默认串，那不算「这个岗位定制过」"""
        text, source = pick_greeting(DEFAULT_GREETING, "账号专用招呼语", DEFAULT_GREETING)
        self.assertEqual((text, source), ("账号专用招呼语", "账号自定义"))

    def test_账号也没填就用默认模板(self):
        text, source = pick_greeting("", "", DEFAULT_GREETING)
        self.assertEqual((text, source), (DEFAULT_GREETING, "默认模板"))

    def test_空岗位配空账号不留空串(self):
        """招呼语为空会把空白消息发给 HR"""
        text, _ = pick_greeting(None, None, DEFAULT_GREETING)
        self.assertTrue(text.strip())


class AccountGreetingConfigTest(unittest.TestCase):
    def test_账号招呼语读得写得出(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"accounts": [
            {"name": "主账号", "greeting_message": "主账号的招呼语", "jobs": []},
            {"name": "账号2", "greeting_message": "账号2的招呼语", "jobs": []},
        ]})
        self.assertEqual(cfg.greet.accounts[0].greeting_message, "主账号的招呼语")
        self.assertEqual(cfg.greet.accounts[1].greeting_message, "账号2的招呼语")

        out = cfg.to_dict()["accounts"]
        self.assertEqual(out[1]["greeting_message"], "账号2的招呼语")

    def test_没有该字段的老配置不会炸(self):
        cfg = UnifiedConfig()
        cfg._apply_bot_config({"accounts": [{"name": "老账号", "jobs": []}]})
        self.assertEqual(cfg.greet.accounts[0].greeting_message, "")


class AutoGreetDialogTest(unittest.TestCase):
    """BOSS 第二种打招呼机制：平台自己把招呼语发出去了，不能再报"未找到输入框"。"""

    def test_弹窗现场解析成结构(self):
        from boss_bot.greet_engine import parse_auto_greet_dialog
        raw = json.dumps({"text": "已向BOSS发送消息 留在此页 继续沟通",
                          "buttons": ["继续沟通|btn-continue", "留在此页|btn-cancel"],
                          "cls": "dialog-greet"})
        got = parse_auto_greet_dialog(raw)
        self.assertEqual(got["cls"], "dialog-greet")
        self.assertEqual(len(got["buttons"]), 2)

    def test_没有弹窗时为空(self):
        from boss_bot.greet_engine import parse_auto_greet_dialog
        for raw in ("", None, "not json", "{}", "[]"):
            self.assertEqual(parse_auto_greet_dialog(raw), {})

    def test_探针JS显式return(self):
        """run_js 不会把裸 IIFE 的值带回来（这个坑踩过一次）"""
        from boss_bot.greet_engine import AUTO_GREET_PROBE_JS
        self.assertTrue(AUTO_GREET_PROBE_JS.lstrip().startswith("return"))

    def test_探针按弹窗文案认(self):
        from boss_bot.greet_engine import AUTO_GREET_PROBE_JS
        self.assertIn("已向BOSS发送", AUTO_GREET_PROBE_JS)
        self.assertIn("留在此页", AUTO_GREET_PROBE_JS)

    def test_输入框缺失分支先认自动发送(self):
        import inspect

        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine._apply_job_inner)
        pos_probe = src.index("self._auto_greet_dialog(instance)")
        pos_snap = src.index("chat_failure_reason(snap)")
        self.assertLess(pos_probe, pos_snap, "自动发送的现场被通用归因吃掉了")
        self.assertIn("AUTO_GREET_REASON", src)

    def test_自动发送要算已沟通(self):
        """平台已经发出去了，下一轮不该再撞同一个岗位"""
        import inspect

        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine._apply_job_inner)
        self.assertIn("_mark_chatted(job)", src[:src.index("chat_failure_reason(snap)")])


class GreetingSourceTraceTest(unittest.TestCase):
    """发送时要能看出这段文字是哪来的，否则改了半天配置不知道生效没有"""

    def test_发送路径走统一优先级(self):
        import inspect

        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine._apply_job_inner)
        self.assertIn("self._greeting_for(job)", src,
                      "招呼语还是各读各的字段，优先级没人统一")
        self.assertNotIn("acc.jobs[0].greeting_message", src,
                         "还在拿第一个岗位的文案当本条招呼语")

    def test_界面有按账号填招呼语的地方(self):
        from pathlib import Path
        html = Path("flask-version/templates/index.html").read_text(encoding="utf-8")
        self.assertIn("accGreeting", html, "账号设置里没有招呼语输入框")
        body = html[html.index("function onAccChange"):]
        body = body[:body.index("\nfunction ")]
        self.assertIn("greeting_message", body, "招呼语输入框改了也不写回配置")


if __name__ == "__main__":
    unittest.main()
