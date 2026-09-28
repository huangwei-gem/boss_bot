# -*- coding: utf-8 -*-
"""AI 接口"代理 vs 直连"归因测试。

起因：2026-09-28 用 tools/diagnose_ai_providers.py 出的归因里有一句
"代理/网络问题：直连能通，走代理失败"——那是**每条路由只打一次**得到的结论。
复查 AMD 三个接口各打 4 次后发现：DeepSeek-V4.1 走代理 4/4 全 200，
直连 4 次里只有 1 次通；也就是说单次抽样把"服务侧抖动"读成了"代理问题"，
归因方向整个反了。归因工具不能靠一次抽样定代理的罪。
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def _load_tool():
    path = os.path.join(ROOT, "tools", "diagnose_ai_providers.py")
    spec = importlib.util.spec_from_file_location("diag_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


diag = _load_tool()


def r(ok, status=200, body=""):
    return {"tag": "", "ok": ok, "status": status, "ms": 100, "body": body}


def route(*results, tag=""):
    return [dict(x, tag=tag) for x in results]


class ClassifyNeedsSamplesTest(unittest.TestCase):
    def test_两边都稳定失败时归因到服务商不提代理(self):
        """404 这类两边一致的错误，结论要说清是模型名/地址，不能甩锅代理"""
        p = route(r(False, 404, "404 page not found"), tag="代理")
        d = route(r(False, 404, "404 page not found"), tag="直连")
        v = diag.classify(p, d, "glm-5-3", ["z-ai/glm-5.3"])
        self.assertIn("404", v)
        self.assertNotIn("代理问题", v)

    def test_直连偶尔通不能判成代理问题(self):
        """代理 4/4 通、直连 1/4 通：是直连不稳，不是代理挡路"""
        p = route(r(True), r(True), r(True), r(True), tag="代理")
        d = route(r(True), r(False, "ReadTimeout", "timed out"),
                  r(False, "ReadTimeout", "timed out"),
                  r(False, "ReadTimeout", "timed out"), tag="直连")
        v = diag.classify(p, d, "DeepSeek-V4.1-Flash", [])
        self.assertNotIn("代理问题", v)
        self.assertIn("直连", v)

    def test_两边都不稳要说抖动而不是代理(self):
        """两边都有成功有失败 → 服务侧限流/抖动，代理不背锅"""
        p = route(r(True), r(False, 429, "rate limit"), r(True), tag="代理")
        d = route(r(True), r(True), r(False, "ReadTimeout", "timed out"), tag="直连")
        v = diag.classify(p, d, "Qwen3.8-Flash-Next", [])
        self.assertNotIn("代理问题", v)
        self.assertIn("抖动", v)

    def test_代理全挂直连全通才算代理问题(self):
        """唯一的定罪条件：代理一次都不通、直连每次都通"""
        p = route(r(False, 429, "concurrency limit"), r(False, 429, "concurrency limit"),
                  r(False, 429, "concurrency limit"), tag="代理")
        d = route(r(True), r(True), r(True), tag="直连")
        v = diag.classify(p, d, "X", [])
        self.assertIn("代理", v)

    def test_结论里带通几次样本数(self):
        """归因必须自证证据强度：N 次抽样里通了几次要写出来"""
        p = route(r(False, 403, "FreeTier"), r(False, 403, "FreeTier"), tag="代理")
        d = route(r(False, 403, "FreeTier"), r(False, 403, "FreeTier"), tag="直连")
        v = diag.classify(p, d, "mimo-v2.5-free", [])
        self.assertRegex(v, r"代理 0/2")
        self.assertRegex(v, r"直连 0/2")


class RepeatFlagTest(unittest.TestCase):
    def test_默认每条路由打三次(self):
        """单次抽样已被证明不够用（见模块 docstring），默认值必须 >1"""
        src = open(os.path.join(ROOT, "tools", "diagnose_ai_providers.py"),
                   encoding="utf-8").read()
        self.assertIn("--repeat", src)
        self.assertIn("default=3", src)


NV_IDS = ["01-ai/yi-large", "bigcode/starcoder2-15b", "microsoft/phi-3.5-moe-instruct",
          "z-ai/glm-5.3", "z-ai/glm-5.3-flash", "moonshotai/kimi-k2.6",
          "moonshotai/kimi-k3", "deepseek-ai/deepseek-coder-6.7b-instruct",
          "deepseek-ai/deepseek-v4.1-flash"]


class NearModelsTest(unittest.TestCase):
    """模型名写错时给的候选。按 token 交集挑会被 '5'/'3' 这种版本号撞名，
    正确答案 z-ai/glm-5.3 反而埋在 starcoder2-15b 那一堆里。"""

    def test_写成一串带横杠的版本号要能找到官方slug(self):
        hits = diag.near_models("glm-5-3", NV_IDS)
        self.assertIn("z-ai/glm-5.3", hits)
        self.assertNotIn("bigcode/starcoder2-15b", hits)

    def test_整名对不上就退到厂商名(self):
        """deepseek-v4-flash-0731 官方没有；退到 deepseek 前缀，给同家族的候选"""
        hits = diag.near_models("deepseek-v4-flash-0731", NV_IDS)
        self.assertIn("deepseek-ai/deepseek-v4.1-flash", hits)

    def test_八竿子打不着的不硬凑(self):
        self.assertEqual(diag.near_models("zzz-nonexistent", NV_IDS), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
