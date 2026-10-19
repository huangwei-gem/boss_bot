"""「这一眼看不准」要让他看得见，也不能每 30 秒刷一条。

2026-10-19 09:00 之后账号2 两条腿每隔 30 秒各打一条「报登录失效，但复核是
uncertain」，一上午几十条；同一时刻 /api/status 里这个号写的是
logged_in=true / needs_login=false / login_reason=""。盘面上"这个号好得很"，
日志里它连一次会话页都没读通过。他决定要不要人工登一次只看面板，不看几十
兆日志——盘面这句假话比刷屏更有害。
"""

import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from boss_bot.main_loop import UnifiedBotLoop
import boss_bot.unified_config as UC


def _回路(state):
    """走真 __init__，只把"眼睛"（复核读页面）换掉。"""
    with patch("boss_bot.main_loop.BrowserManager"):
        loop = UnifiedBotLoop(config=UC.UnifiedConfig())
    loop._logged_in = True
    loop._needs_login = False
    loop._login_reason = ""
    loop._log = lambda *a, **k: None
    loop._login_state_read = lambda inst, 早收=True: state
    loop._discard_stale_cookies = MagicMock()
    loop._login_fail_streak = 0
    return loop


class 复核看不准要留痕Test(unittest.TestCase):

    def _抓日志(self, lp):
        条 = []
        lp._log = lambda lvl="", m="": 条.append((lvl, m))
        return 条

    def test_复核读不出结论要在状态里说不准(self):
        """Cookie 在、页面是登录墙：两路证据矛盾就是"看不准"。
        这个事实不进状态，面板就只能说"已登录"。"""
        lp = _回路("uncertain")
        self.assertFalse(lp._recheck_before_archive(MagicMock(), "回复侧"))
        self.assertEqual("login_uncertain", lp._login_reason,
                         f"存疑没记进状态：{lp._login_reason!r}")
        lp._discard_stale_cookies.assert_not_called()

    def test_反复复核不许每轮重复同一条告警(self):
        """每 30 秒一条同样的话，一个上午几十条，真要看的行被埋在里面。
        第一条说 WARN，后面转 DEBUG——状态没变就没有新话。"""
        lp = _回路("uncertain")
        条 = self._抓日志(lp)
        for _ in range(4):
            lp._recheck_before_archive(MagicMock(), "回复侧")
        响 = [m for lvl, m in 条 if lvl == "WARN"]
        self.assertEqual(1, len(响), f"同一条告警刷了 {len(响)} 遍：{响}")

    def test_复核读回已登录要撤掉存疑(self):
        """页面稳定下来证明是虚惊：存疑必须跟着撤，不能一直挂着让他白登一次。"""
        lp = _回路("uncertain")
        lp._recheck_before_archive(MagicMock(), "回复侧")
        self.assertEqual("login_uncertain", lp._login_reason)
        lp._login_state_read = lambda inst, 早收=True: "logged_in"
        lp._recheck_before_archive(MagicMock(), "回复侧")
        self.assertEqual("", lp._login_reason, "复核已登录，存疑却没撤掉")

    def test_撤掉存疑后再看不准要重新说一次(self):
        """去重只能按"状态变了没有"，不能按次数——否则第二次真存疑就没人知道了。"""
        lp = _回路("uncertain")
        lp._recheck_before_archive(MagicMock(), "回复侧")
        lp._login_state_read = lambda inst, 早收=True: "logged_in"
        lp._recheck_before_archive(MagicMock(), "回复侧")
        lp._login_state_read = lambda inst, 早收=True: "uncertain"
        条 = self._抓日志(lp)
        lp._recheck_before_archive(MagicMock(), "回复侧")
        self.assertTrue(any(lvl == "WARN" for lvl, m in 条),
                        "存疑撤掉后再次出现，应该重新有一条说得清的告警")

    def test_确认是登录墙时不许挂着看不准(self):
        """已经定罪成登录墙了，原因就得让给真正的原因，两种状态不能并存。"""
        lp = _回路("uncertain")
        lp._recheck_before_archive(MagicMock(), "回复侧")
        lp._login_state_read = lambda inst, 早收=True: "login_wall"
        lp._recheck_before_archive(MagicMock(), "回复侧")
        self.assertNotEqual("login_uncertain", lp._login_reason)


class 前端要说清看不准Test(unittest.TestCase):

    def _模板(self):
        p = Path(__file__).resolve().parents[1] / "flask-version" / "templates" / "index.html"
        return p.read_text(encoding="utf-8")

    def test_原因标签表里有这一项(self):
        """后端会报 login_uncertain（启动路径早就在写这个值），标签表里没有它
        就等于鼠标移上去什么也不多说。"""
        self.assertIn("login_uncertain:", self._模板(),
                      "后端有 login_uncertain，前端标签表接不住=空话")

    def test_看不准的点要用中性色不是绿的(self):
        """绿的点在"这个号好得很"，而他真正需要知道的是"这一格定不了论"；
        但也不许画成待登录（红色系）——看不准不是"现在就得动手"。"""
        源 = self._模板().replace(" ", "")
        self.assertIn("st.login_reason==='login_uncertain'", 源,
                      "状态点完全不看 login_reason，存疑在界面上等于不存在")
        行 = [ln for ln in 源.splitlines()
              if "login_uncertain" in ln and "return" in ln]
        self.assertTrue(行, "没有把存疑映射到某个点样式的分支")
        self.assertFalse(any("needs_login" in ln for ln in 行),
                         "把看不准画成待登录=让他白登一次")


class 等待秒数要说真话Test(unittest.TestCase):

    def test_两处提示不许写死60秒(self):
        """摆了窗口实际等 180 秒：09:31:32 预告"最多 60 秒"，
        09:34:57 收尾那条写的是 180 秒——同一件事两个数。"""
        源 = (Path(__file__).resolve().parents[1] / "boss_bot" / "main_loop.py"
              ).read_text(encoding="utf-8")
        self.assertNotIn("等人工处理（最多 60 秒）", 源,
                         "写死的秒数和闸门真正等的数不是一回事")


class 每个登录原因界面都要有句人话Test(unittest.TestCase):

    def test_后端能置的原因不许在界面上没字(self):
        """新增 login_reason（今天加的 browser_taken）只改后端的话，界面上这个号
        就只剩一个黄色点和"等你登录"，说不清到底要他动手在哪——这正是
        「提示不许狼来了」要避免的那种半截真话。"""
        后 = (Path(__file__).resolve().parents[1] / "boss_bot" / "main_loop.py"
              ).read_text(encoding="utf-8")
        前 = (Path(__file__).resolve().parents[1] / "flask-version" / "templates" /
              "index.html").read_text(encoding="utf-8")
        能置的 = set(re.findall(r'_login_reason\s*=\s*["\']([a-z_]+)["\']', 后))
        self.assertTrue(能置的, "一条 login_reason 都没抓到，这个门已经空转")
        缺 = [原 for 原 in sorted(能置的)
              if not re.search(r'^\s*%s\s*:' % 原, 前, re.M)]
        self.assertEqual(缺, [], "这些原因在界面 LOGIN_REASON_LABEL 里没有说法: %s" % (缺,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
