"""「未找到沟通按钮」归因测试。

界面里这句话又出现了 9 次（2026-09-29）。把 logs/greet_engine.log 里 20 次
pre-click 命中的"导航到 / 导航后URL"逐条比对，现场其实是三种，处置完全不同：

     9 次  请求 job_detail，落地在 /web/geek/chat   —— 该岗位此前已沟通，BOSS
                                                       直接把详情页跳成会话页
     9 次  请求 job_detail，落地在同一个 job_detail  —— 页面到位但没有可点按钮
     2 次  请求 job_detail，落地在另一个 job_detail  —— 原岗位已下线，BOSS 跳相似职位

以前三条都写成"未找到沟通按钮"，既看不出该干什么，也害得已沟通的岗位每轮被
重新撞一次。现在按落点分档，并且跳会话页的那类直接标已沟通。
"""

import unittest

from boss_bot.greet_engine import (
    CHAT_REDIRECT_REASON, LOGIN_WALL_REASON, OFFLINE_JOB_REASON,
    chat_button_failure_reason, job_id_of, same_job_page)

JOB_A = "https://www.zhipin.com/job_detail/aaa111.html?lid=xxx"
JOB_B = "https://www.zhipin.com/job_detail/bbb222.html"
CHAT = "https://www.zhipin.com/web/geek/chat"


class JobIdTest(unittest.TestCase):
    def test_取岗位id(self):
        self.assertEqual(job_id_of(JOB_A), "aaa111")
        self.assertEqual(job_id_of(CHAT), "")


class SameJobPageTest(unittest.TestCase):
    def test_带query参数也算同一岗位(self):
        self.assertTrue(same_job_page(JOB_A, "https://www.zhipin.com/job_detail/aaa111.html"))

    def test_不同岗位id不算同一页(self):
        self.assertFalse(same_job_page(JOB_A, JOB_B))


class ChatButtonReasonTest(unittest.TestCase):
    def _snap(self, **kw):
        base = {"url": "", "inputs": [], "chat_elements": [], "buttons": [], "notice": "",
                "captcha": False, "error": ""}
        base.update(kw)
        return base

    def test_跳进会话页要说已沟通(self):
        reason, already = chat_button_failure_reason(JOB_A, CHAT, self._snap())
        self.assertEqual(reason, CHAT_REDIRECT_REASON)
        self.assertTrue(already, "这类必须标已沟通，否则下一轮又撞同一个岗位")

    def test_跳到别的岗位页要说已下线(self):
        reason, already = chat_button_failure_reason(JOB_A, JOB_B, self._snap())
        self.assertEqual(reason, OFFLINE_JOB_REASON)
        self.assertFalse(already)

    def test_落地页有登录框要说登录态(self):
        snap = self._snap(inputs=["ipt ipt-phone required", "ipt ipt-sms required"])
        reason, already = chat_button_failure_reason(JOB_A, JOB_A, snap)
        self.assertEqual(reason, LOGIN_WALL_REASON)
        self.assertFalse(already)

    def test_落地页有验证码要说风控(self):
        reason, _ = chat_button_failure_reason(
            JOB_A, JOB_A, self._snap(captcha=True))
        self.assertIn("验证", reason)

    def test_按钮没渲染要带上现场证据(self):
        """这一类以前只能干写"没找到按钮"；现场必须进原因，否则下次还是查不出。"""
        snap = self._snap(buttons=["完善简历|btn-resume", "搜索|btn-search"],
                          notice="该职位已过期")
        reason, already = chat_button_failure_reason(JOB_A, JOB_A, snap)
        self.assertIn("没有可点的沟通按钮", reason)
        self.assertIn("该职位已过期", reason)
        self.assertIn("完善简历", reason)
        self.assertFalse(already)

    def test_什么现场都没有也不能给空原因(self):
        reason, _ = chat_button_failure_reason("", "", self._snap())
        self.assertTrue(reason.strip())

    def test_连接断开优先(self):
        reason, _ = chat_button_failure_reason(
            JOB_A, JOB_A, self._snap(error="页面与浏览器的连接已断开"))
        self.assertIn("断开", reason)


class WiringTest(unittest.TestCase):
    """归因函数必须真的接在按钮缺失分支上，且图片那条路径不再冒充同一句话。"""

    @classmethod
    def setUpClass(cls):
        import inspect

        from boss_bot.greet_engine import GreetEngine
        cls.src = inspect.getsource(GreetEngine._apply_job_inner)
        cls.img = inspect.getsource(GreetEngine._send_images_after_message)
        # 归因搬进了 _explain_missing_chat_button（要接验证码闸门，嵌在几十行的
        # 投递流程里没法测），所以两处都得看：分支确实调它、它确实做归因
        cls.explain = inspect.getsource(GreetEngine._explain_missing_chat_button)

    def test_按钮缺失分支走归因(self):
        self.assertIn("_explain_missing_chat_button(", self.src)
        self.assertIn("chat_button_failure_reason(", self.explain)

    def test_归因认出验证页要叫闸门(self):
        """这条锁的是"卡在验证图"别再回来：认出验证页必须等人工，不能只 return 原因"""
        self.assertIn("_captcha_handoff()", self.explain)

    def test_已沟通类现场要标已沟通(self):
        self.assertIn("_mark_chatted(job)", self.src)

    def test_图片路径不再复用同一句话(self):
        self.assertNotIn("未找到沟通按钮，无法上传图片", self.img)
        self.assertIn("图片未上传", self.img)

    def test_快照带按钮和提示现场(self):
        from boss_bot.greet_engine import CHAT_SNAPSHOT_JS
        self.assertIn("button", CHAT_SNAPSHOT_JS)
        self.assertIn("return", CHAT_SNAPSHOT_JS.lstrip()[:8])


if __name__ == "__main__":
    unittest.main()
