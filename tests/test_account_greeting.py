"""按账号自定义招呼语（task #30 的一半）。

用户有两段现场：一段是自己点开抽屉再输入发送，另一段是 BOSS 点了"立即沟通"
就把预设招呼语自动发出去了。无论哪种，招呼语都该是"这个账号自己的"，
而不是历史里每个岗位都塞着的同一段默认文案。
"""

import json
import unittest
from unittest.mock import MagicMock

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

    def test_两级都没填就返回空串不发(self):
        """2026-09-30 口径改了：招呼语不再回落全局默认模板。

        旧行为是"没填就发系统预设那句"，等于程序替使用者跟 HR 说话；现在返回空串，
        由调用方拦住不发（拦截位置见 test_空缺必须在点沟通之前拦住）。
        """
        text, source = pick_greeting("", "", DEFAULT_GREETING)
        self.assertEqual((text, source), ("", "未配置"))

    def test_空缺必须在点沟通之前拦住(self):
        """点「沟通」在 BOSS 上就等于发起招呼（第二种机制还会自动发预设文案），

        所以空缺的返回必须排在找按钮/点按钮之前 —— 走到输入框才发现就晚了。
        """
        import inspect

        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine._apply_job_inner)
        guard = src.index("GREETING_MISSING_REASON")
        click = src.index("_find_chat_button")
        self.assertLess(guard, click, "空缺拦截掉到了点击之后，等于已经打过去了才发现没话")
        self.assertIn("self._greeting_for(job)", src[:guard])


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
        inner = inspect.getsource(GreetEngine._apply_job_inner)
        pos_probe = inner.index("self._auto_greet_path(instance, job, greeting)")
        pos_snap = inner.index("chat_failure_reason(snap)")
        self.assertLess(pos_probe, pos_snap, "自动发送的现场被通用归因吃掉了")
        path = inspect.getsource(GreetEngine._auto_greet_path)
        self.assertIn("_auto_greet_note", path, "认出自动发送后没留痕，界面就看不出少核对了一步")

    def test_自动发送要算已沟通(self):
        """平台已经发出去了，下一轮不该再撞同一个岗位。

        结算逻辑从 _apply_job_inner 抽成了 _auto_greet_path（点完沟通的快速探测
        也要用同一套），所以断言跟着挪，但锁的还是那件事：认出台自动发送必须
        标记已沟通。
        """
        import inspect

        from boss_bot.greet_engine import GreetEngine
        path = inspect.getsource(GreetEngine._auto_greet_path)
        self.assertIn("_mark_chatted(job)", path)
        inner = inspect.getsource(GreetEngine._apply_job_inner)
        self.assertIn("self._auto_greet_path(instance, job, greeting)", inner)


class FakeEle:
    def __init__(self, name=""):
        self.name = name
        self.clicked = 0
        self.typed = []

    def click(self):
        self.clicked += 1

    def input(self, text):
        self.typed.append(text)


class FakePage:
    """够用的假 DOM：探针按脚本原文回话，元素按选择器回话。"""

    def __init__(self, bubble=None, elements=None, browser=None):
        self.bubble = bubble
        self.elements = elements or {}
        self._browser = browser
        self.probes = []

    def run_js(self, script, *args, as_expr=False):
        from boss_bot.greet_engine import LAST_MINE_BUBBLE_JS
        self.probes.append(script)
        if script == LAST_MINE_BUBBLE_JS:
            return json.dumps(self.bubble) if self.bubble is not None else ""
        return "{}"

    def ele(self, selector, timeout=None):
        return self.elements.get(selector)

    def _get_browser(self):
        return self._browser


class FakeBrowser:
    def __init__(self, pages):
        self._pages = pages          # tab_id -> FakePage
        self.tab_ids = list(pages)

    def get_tab(self, tab_id):
        return self._pages[tab_id]


def _engine():
    from boss_bot.greet_engine import GreetEngine
    eng = GreetEngine(MagicMock(), UnifiedConfig(), account_index=0)
    eng.running = True
    eng._log = lambda *a, **k: None
    eng._random_delay = lambda *a, **k: None
    eng._interruptible_sleep = lambda *a, **k: None
    return eng


class AutoGreetMatchTest(unittest.TestCase):
    """BOSS 替我们发出去的那条，是不是本号想发的招呼语。"""

    def test_内容一致算已经发过(self):
        from boss_bot.greet_engine import auto_greet_matches
        ours = "你好，我对这个岗位很感兴趣"
        self.assertTrue(auto_greet_matches(ours, ours))

    def test_只差空白换行也算一致(self):
        """BOSS 气泡里会多出不可见空白，逐字符比会误判成不一致而重发一遍"""
        from boss_bot.greet_engine import auto_greet_matches
        self.assertTrue(auto_greet_matches(" 你好，\n 我 对这个岗位很感兴趣 ",
                                           "你好， 我 对这个岗位很感兴趣"))

    def test_内容不一致要补发(self):
        from boss_bot.greet_engine import auto_greet_matches
        self.assertFalse(auto_greet_matches("您好，方便发个简历吗", "你好，我对这个岗位很感兴趣"))

    def test_没读到气泡不能当作已发过(self):
        from boss_bot.greet_engine import auto_greet_matches
        self.assertFalse(auto_greet_matches("", "你好"))

    def test_我方文案为空不许认账(self):
        """空串相等会把平台默认文案冒充成本号自定义招呼语"""
        from boss_bot.greet_engine import auto_greet_matches
        self.assertFalse(auto_greet_matches("", ""))

    def test_探针读我方最后一条气泡(self):
        from boss_bot.greet_engine import LAST_MINE_BUBBLE_JS
        self.assertTrue(LAST_MINE_BUBBLE_JS.lstrip().startswith("return"))
        self.assertIn("item-myself", LAST_MINE_BUBBLE_JS)
        self.assertIn("text-content", LAST_MINE_BUBBLE_JS)


class AutoGreetContinueTest(unittest.TestCase):
    def test_认得出继续沟通(self):
        from boss_bot.greet_engine import pick_continue_btn
        dialog = {"buttons": ["继续沟通|btn-continue", "留在此页|btn-cancel"]}
        self.assertEqual(pick_continue_btn(dialog), "继续沟通")

    def test_只有留在此页时不点(self):
        from boss_bot.greet_engine import pick_continue_btn
        self.assertEqual(pick_continue_btn({"buttons": ["留在此页|btn-cancel"]}), "")

    def test_按钮文本带空白也认(self):
        from boss_bot.greet_engine import pick_continue_btn
        self.assertEqual(pick_continue_btn({"buttons": [" 继续沟通 |btn"]}), "继续沟通")


class AutoGreetFollowupTest(unittest.TestCase):
    """认出弹窗之后：进会话，是我们那段就不重发，不是才发本号那段。"""

    def _dialog(self):
        return {"text": "已向BOSS发送消息 留在此页 继续沟通",
                "buttons": ["继续沟通|btn-continue", "留在此页|btn-cancel"]}

    def test_内容一致时一个字都不发(self):
        from boss_bot.greet_engine import LAST_MINE_BUBBLE_JS
        ours = "你好，我对这个岗位很感兴趣"
        btn = FakeEle("继续沟通")
        page = FakePage(bubble={"chat_page": True, "total": 2, "mine": 1, "text": ours},
                       elements={"text=继续沟通": btn, "#chat-input": FakeEle("input")})
        got = _engine()._auto_greet_followup(page, ours, self._dialog())
        self.assertEqual(got, "matched")
        self.assertEqual(btn.clicked, 1, "要先点「继续沟通」进会话")
        self.assertEqual(page.elements["#chat-input"].typed, [])

    def test_内容不一致时补发本号招呼语(self):
        ours = "你好，我对这个岗位很感兴趣"
        btn = FakeEle("继续沟通")
        inp = FakeEle("input")
        send = FakeEle("send")
        page = FakePage(bubble={"chat_page": True, "total": 2, "mine": 1,
                                "text": "您好，方便发份简历吗",
                                "head": "陈女士 朔珩咨询 HR 更多"},
                        elements={"text=继续沟通": btn, "#chat-input": inp,
                                  ".btn-send": send})
        got = _engine()._auto_greet_followup(page, ours, self._dialog(),
                                             {"company": "朔珩咨询有限公司"})
        self.assertEqual(got, "sent")
        self.assertEqual(inp.typed, [ours])
        self.assertEqual(send.clicked, 1)

    def test_会话里没有我方气泡时也要发出去(self):
        """BOSS 弹窗说发了，可读到的我方气泡是 0 条——这段必须补上"""
        ours = "你好，我对这个岗位很感兴趣"
        inp = FakeEle("input")
        page = FakePage(bubble={"chat_page": True, "total": 3, "mine": 0, "text": "",
                                "head": "陈女士 朔珩咨询 HR 更多"},
                        elements={"text=继续沟通": FakeEle("继续沟通"),
                                  "#chat-input": inp, ".btn-send": FakeEle("send")})
        got = _engine()._auto_greet_followup(page, ours, self._dialog(),
                                             {"company": "朔珩咨询"})
        self.assertEqual(got, "sent")
        self.assertEqual(inp.typed, [ours])

    def test_进不去会话就老实说发不了(self):
        """读不到消息列表时不能盲发，否则可能对着搜索页打字"""
        ours = "你好，我对这个岗位很感兴趣"
        inp = FakeEle("input")
        page = FakePage(bubble={"chat_page": False, "total": 0, "mine": 0, "text": ""},
                        elements={"text=继续沟通": FakeEle("继续沟通"),
                                  "#chat-input": inp})
        got = _engine()._auto_greet_followup(page, ours, self._dialog())
        self.assertEqual(got, "none")
        self.assertEqual(inp.typed, [])

    def test_当前页没会话时去别的标签页找(self):
        """BOSS 点「继续沟通」常常是把会话开到新标签页"""
        from boss_bot.greet_engine import LAST_MINE_BUBBLE_JS
        ours = "你好，我对这个岗位很感兴趣"
        detail_btn = FakeEle("继续沟通")
        detail = FakePage(bubble={"chat_page": False, "total": 0, "mine": 0, "text": ""},
                          elements={"text=继续沟通": detail_btn})
        chat = FakePage(bubble={"chat_page": True, "total": 1, "mine": 1, "text": ours})
        browser = FakeBrowser({"a": detail, "b": chat})
        detail._browser = browser
        got = _engine()._auto_greet_followup(detail, ours, self._dialog())
        self.assertEqual(got, "matched")
        self.assertIn(LAST_MINE_BUBBLE_JS, chat.probes)


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


class AutoGreetWiringTest(unittest.TestCase):
    def test_弹窗分支先走补发再落库(self):
        import inspect

        from boss_bot.greet_engine import GreetEngine
        inner = inspect.getsource(GreetEngine._apply_job_inner)
        path = inspect.getsource(GreetEngine._auto_greet_path)
        pos = path.index("self._auto_greet_followup(instance, greeting, dialog, job)")
        self.assertLess(inner.index("self._auto_greet_path(instance, job, greeting)"),
                        inner.index("chat_failure_reason(snap)"),
                        "补发要排在通用归因之前，否则自动发送会被报成未找到输入框")
        seg = path[pos:pos + 900]
        self.assertIn("_record_sent_now(job)", seg, "补发成功当场就该落库")
        self.assertIn("_mark_chatted(job)", seg)
        self.assertIn("return True", seg, "招呼语已发出就该算投递成功，不能再算跳过")

    def test_补发用本号招呼语而不是岗位默认(self):
        import inspect

        from boss_bot.greet_engine import GreetEngine
        path = inspect.getsource(GreetEngine._auto_greet_path)
        pos = path.index("self._auto_greet_followup(")
        seg = path[max(0, pos - 400):pos + 100]
        self.assertIn("greeting", seg, "补发那段文案必须来自 _greeting_for 的结果")
        inner = inspect.getsource(GreetEngine._apply_job_inner)
        self.assertIn("self._auto_greet_path(instance, job, greeting)", inner,
                      "传进去的必须是本号招呼语，不是岗位默认模板")


if __name__ == "__main__":
    unittest.main()


class GreetingUiLockTest(unittest.TestCase):
    """界面不能再留着那句全局模板：留着就等于"说了不用、还会偷偷用" """

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.html = (Path(__file__).resolve().parent.parent
                    / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")

    def test_界面没有硬编码招呼语(self):
        self.assertNotIn("defaultGreeting", self.html)

    def test_说明写的是留空自动生成默认(self):
        """2026-10-03 口径：不再"两级都空就不发"（那样一整轮全跳过），而是按本账号
        信息生成一条默认、发送时再由 AI 按岗位现编。那段全局模板仍然不许当回落。"""
        self.assertNotIn("最后才用系统默认", self.html)
        self.assertNotIn("两级都留空", self.html)
        self.assertIn("自动生成一条默认", self.html)
        self.assertIn("AI 按岗位+公司+JD", self.html)

    def test_岗位弹窗不再预填模板(self):
        self.assertNotIn("greeting_message:defaultGreeting", self.html)
        self.assertNotIn("|| defaultGreeting", self.html)


class 会话页直落结算Test(unittest.TestCase):
    """等登录时新发现的第三种形状（2026-10-30 账号2 勾投递线上现测）：

    点完「立即沟通」既没出聊天抽屉、也没弹「已向BOSS发送」，这一页自己跳进了
    `https://www.zhipin.com/web/geek/chat?id=…&jobId=…`。旧判据只认前两种形状，
    于是红字写成「页面已跳走且没有聊天元素」——可那条 URL 本身就是一个刚建立的
    会话，现场 dump 里还数出「隐藏模板输入框=1 个」（会话页正在挂载）。
    记成失败会让下一轮对同一个 HR 再点一次，所以这里就地结算。
    """

    会话页 = "https://www.zhipin.com/web/geek/chat?id=fc69884b886208df0HB7&jobId=891a8"
    本公司 = "朔珩咨询"
    顶栏 = "陈女士 朔珩咨询 HR 更多"

    def _引擎(self):
        eng = _engine()
        self.标记, self.落库 = [], []
        eng._mark_chatted = lambda job: self.标记.append(job.get("company"))
        eng._record_sent_now = lambda job: self.落库.append(job.get("_auto_greet_note"))
        return eng

    def _页(self, bubble, 元素=None, url=None):
        页 = FakePage(bubble=bubble, elements=元素 or {})
        页.url = url or self.会话页
        return 页

    def test_会话里已有本号那句就不重发(self):
        ours = "你好，我对这个岗位很感兴趣"
        eng = self._引擎()
        页 = self._页({"chat_page": True, "total": 2, "mine": 1, "text": ours,
                      "head": self.顶栏})
        job = {"company": self.本公司}
        self.assertEqual(eng._会话页直落结算(页, job, ours), (True, ""))
        self.assertIn("未重复发送", job["_auto_greet_note"])
        self.assertEqual(self.标记, [self.本公司], "算送达就得按 已沟通 落账")
        self.assertEqual(len(self.落库), 1)

    def test_会话是空的就把本号那句发进去(self):
        ours = "你好，我对这个岗位很感兴趣"
        inp, send = FakeEle("input"), FakeEle("send")
        eng = self._引擎()
        页 = self._页({"chat_page": True, "total": 0, "mine": 0, "text": "",
                      "head": self.顶栏},
                      {"#chat-input": inp, ".btn-send": send})
        job = {"company": self.本公司}
        self.assertEqual(eng._会话页直落结算(页, job, ours), (True, ""))
        self.assertEqual(inp.typed, [ours])
        self.assertIn("已发进这个会话", job["_auto_greet_note"])

    def test_顶栏对不上公司就不认领(self):
        """认错人比这一单没投严重得多：不能把本号招呼语发给别家 HR，
        也不能因为"进了个会话"就记成功。"""
        ours = "你好，我对这个岗位很感兴趣"
        inp = FakeEle("input")
        eng = self._引擎()
        页 = self._页({"chat_page": True, "total": 1, "mine": 0, "text": "",
                      "head": "王女士 别家信息科技 HR 更多"},
                      {"#chat-input": inp})
        self.assertIsNone(eng._会话页直落结算(页, {"company": self.本公司}, ours))
        self.assertEqual(inp.typed, [], "顶栏没对上还打字，就是把话发给了别家")
        self.assertEqual(self.标记, [], "没证实送达就不许记 已沟通")
        self.assertEqual(self.落库, [])

    def test_读不出会话形状就不认领(self):
        """URL 像会话页但页面还没渲染出会话：宁可让原来的失败归因照实报红。"""
        eng = self._引擎()
        页 = self._页({"chat_page": False, "total": 0, "mine": 0, "text": ""})
        self.assertIsNone(eng._会话页直落结算(页, {"company": self.本公司}, "你好"))
        self.assertEqual(self.标记, [])
        self.assertEqual(self.落库, [])

    def test_还在搜索页就不走这一支(self):
        """没跳进会话时不许去等会话——那会把每一单的失败归因拖长，
        抽屉真没出那一族（额度/风控）的计数还得照旧走。"""
        跑过 = []
        eng = self._引擎()
        eng._wait_chat_open = lambda instance: (跑过.append(1), (None, {}))[1]
        页 = self._页({"chat_page": True, "total": 1, "mine": 1, "text": "你好"},
                      url="https://www.zhipin.com/web/geek/job-recommend")
        self.assertIsNone(eng._会话页直落结算(页, {"company": self.本公司}, "你好"))
        self.assertEqual(跑过, [], "还在搜索页就去等会话，等于替抽屉失败续命")

    def test_无弹窗那一支接上了会话页结算(self):
        import inspect

        from boss_bot.greet_engine import GreetEngine
        源 = inspect.getsource(GreetEngine._auto_greet_path)
        self.assertIn("return self._会话页直落结算(instance, job, greeting)", 源,
                      "弹窗没出现就直接回 None，会话页那种形状永远没人结算")
        # 弹窗那条老路一个字没动：还是同一份 followup、同一份落账
        self.assertIn("self._auto_greet_followup(instance, greeting, dialog, job)", 源)

    def test_没有弹窗可点时补发腿照样走得通(self):
        """_auto_greet_followup 以前第一行就要 pick_continue_btn(dialog)，
        会话页那一路没有「继续沟通」可点，得能跳过点击直接核对。"""
        ours = "你好，我对这个岗位很感兴趣"
        btn = FakeEle("继续沟通")
        eng = _engine()
        页 = FakePage(bubble={"chat_page": True, "total": 2, "mine": 1, "text": ours,
                             "head": self.顶栏},
                     elements={"text=继续沟通": btn})
        self.assertEqual(eng._auto_greet_followup(页, ours, None), "matched")
        self.assertEqual(btn.clicked, 0, "dialog=None 还去点按钮，就是拿不存在的东西当凭据")
