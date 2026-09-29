# -*- coding: utf-8 -*-
"""打招呼失败归因测试。

起因：界面里 172 条跳过原因全是"未找到输入框"，看不出到底为什么没框。
把 logs/ 里 116 次失败的现场 dump 逐条分类后是这样：
    61 次 页面被换成手机(ipt-phone)+短信验证码(ipt-sms)登录框 —— 登录态掉了
    45 次 页面上只剩搜索页的 ipt-search/city-code        —— 沟通抽屉压根没渲染
     8 次 "与页面的连接已断开"                            —— 标签页没了，被记成同一种原因
三种原因的处置完全不同，记录却一模一样，所以原因必须按现场分档写出来。

另一个坑：旧 dump 只抓 tag:input / tag:textarea，而 BOSS 聊天输入框是
contenteditable div（#chat-input.chat-input），"页面上没有输入框"这个结论
本身可能是瞎的，快照必须把 contenteditable 一起看。
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.greet_engine import chat_failure_reason


class ChatFailureReasonTest(unittest.TestCase):
    def test_手机短信登录框要说登录态失效(self):
        snap = {"url": "https://www.zhipin.com/job_detail/x.html",
                "inputs": ["ipt-search", "ipt-phone required", "ipt-sms required"],
                "chat_elements": [], "error": ""}
        self.assertIn("重新登录", chat_failure_reason(snap))

    def test_只剩搜索页元素要说抽屉没渲染(self):
        snap = {"url": "https://www.zhipin.com/job_detail/x.html",
                "inputs": ["ipt-search", "city-code"],
                "chat_elements": [], "error": ""}
        r = chat_failure_reason(snap)
        self.assertIn("沟通", r)
        self.assertNotIn("登录", r)

    def test_页面掉线不能算没找到输入框(self):
        snap = {"url": "", "inputs": [], "chat_elements": [],
                "error": "与页面的连接已断开"}
        r = chat_failure_reason(snap)
        self.assertIn("断开", r)
        self.assertNotEqual(r, "未找到输入框")

    def test_聊天容器在但输入框不在要单独说(self):
        snap = {"url": "https://www.zhipin.com/web/geek/chat",
                "inputs": [], "chat_elements": [".chat-container"], "error": ""}
        self.assertIn("容器", chat_failure_reason(snap))

    def test_原因不能还是那句笼统的未找到输入框(self):
        """整句"未找到输入框"没有任何可操作信息，历史 172 条就是这么废掉的"""
        cases = [
            {"url": "https://www.zhipin.com/job_detail/x.html", "inputs": [], "chat_elements": [], "error": ""},
            {"url": "https://www.zhipin.com/job_detail/x.html",
             "inputs": ["ipt-search"], "chat_elements": [], "error": ""},
        ]
        for snap in cases:
            self.assertNotEqual(chat_failure_reason(snap), "未找到输入框")

    def test_跳到登录页URL也算登录态失效(self):
        """真机实测：/web/geek/chat 未登录会被送到 /web/user/，
        那里的输入框类名是 tel/text，不含 ipt-phone，光靠类名会漏判"""
        snap = {"url": "https://www.zhipin.com/web/user/",
                "inputs": ["tel", "text", "agree-policy"],
                "chat_elements": [], "error": ""}
        self.assertIn("登录", chat_failure_reason(snap))

    def test_空快照也不能崩(self):
        self.assertTrue(chat_failure_reason({}))


class SnapshotWiredTest(unittest.TestCase):
    """归因函数必须真的被失败分支调用，且快照要看得见 contenteditable"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(ROOT, "boss_bot", "greet_engine.py")
        cls.src = open(path, encoding="utf-8").read()

    def test_失败分支用快照归因(self):
        self.assertIn("chat_failure_reason(", self.src)

    def test_快照包含contenteditable(self):
        """BOSS 聊天框是 contenteditable div，只查 input/textarea 会误判成没有框"""
        from boss_bot.greet_engine import CHAT_SNAPSHOT_JS
        self.assertIn("contenteditable", CHAT_SNAPSHOT_JS)

    def test_快照JS必须显式return(self):
        """DrissionPage 的 run_js 不会把裸 IIFE 的值带回来，真机上验出来返回 None，
        于是快照永远为空、归因永远走最后一条——JS 必须以 return 开头"""
        from boss_bot.greet_engine import CHAT_SNAPSHOT_JS
        self.assertTrue(CHAT_SNAPSHOT_JS.lstrip().startswith("return"),
                        "run_js 拿不到返回值，快照会是空的")

    def test_旧的盲dump已撤掉(self):
        """tag:input/tag:textarea 那句 dump 看不到聊天框，别再当证据用"""
        self.assertNotIn("未找到输入框! 尝试打印页面上的input/textarea元素", self.src)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class CaptchaInGreetFlowTest(unittest.TestCase):
    """投递路径碰到验证码页：要认出来、要能被停止打断，不能空转几分钟。"""

    def test_快照里有验证元素时原因要说人工验证(self):
        from boss_bot.greet_engine import chat_failure_reason
        snap = {"url": "https://www.zhipin.com/web/geek/jobs?_security_check=1_179",
                "inputs": [], "chat_elements": [], "captcha": True, "error": ""}
        self.assertIn("验证", chat_failure_reason(snap))

    def test_停止后等待要立刻结束(self):
        """裸 time.sleep 让"停止/暂停"在投递循环里完全不生效"""
        from boss_bot.greet_engine import GreetEngine
        e = GreetEngine.__new__(GreetEngine)
        e.running = False
        e._log = lambda *a: None
        start = time.time()
        e._interruptible_sleep(30)
        self.assertLess(time.time() - start, 1)

    def test_投递重试不再用裸sleep(self):
        import inspect
        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine._apply_job_inner)
        self.assertNotIn("time.sleep(", src,
                         "投递主路径上的等待必须可被停止打断")


class RecordOnSendTest(unittest.TestCase):
    """投递成功的瞬间就要落库+推前端，不能等收尾动作跑完。"""

    def _engine(self):
        from unittest.mock import MagicMock
        from boss_bot.greet_engine import GreetEngine
        e = GreetEngine(MagicMock(), MagicMock(), account_index=0)
        e._log = lambda *a: None
        e.emitted = []
        e._emit_greet_event = lambda job, status, **kw: self.emitted.append(status)
        e._record_greet = lambda job, **kw: job.update(_rec=True)
        return e

    def setUp(self):
        self.emitted = []
        self.e = self._engine()

    def test_写一条就推一次(self):
        job = {"url": "u", "_actual_greeting_sent": "您好"}
        self.e._record_sent_now(job)
        self.assertEqual(self.emitted, ["success"])
        self.assertTrue(job.get("_recorded"))

    def test_重复调用不写两条(self):
        """收尾路径还会再记一次，不去重就是同一岗位两条记录"""
        job = {"url": "u"}
        self.e._record_sent_now(job)
        self.e._record_sent_now(job)
        self.assertEqual(self.emitted, ["success"])

    def test_即时记录发生在图片上传之前(self):
        """图片上传+关弹窗+关标签页要 5~30 秒，界面这段时间看不到投递结果"""
        import inspect
        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine._apply_job_inner)
        self.assertLess(src.index("_record_sent_now("),
                        src.index("_send_images_after_message("))

    def test_外层不重复记录(self):
        import inspect
        from boss_bot.greet_engine import GreetEngine
        src = inspect.getsource(GreetEngine.send_greeting)
        self.assertIn("_recorded", src, "成功分支要先看过是否已即时记录，避免双写")
