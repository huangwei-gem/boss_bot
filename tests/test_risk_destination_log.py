# -*- coding: utf-8 -*-
"""「风控到底是谁招来的」这条判据：从验证页 URL 的 callbackUrl 读出当时要去哪一步。

起因（2026-10-28 自己查出来的卡点）：想统计八天日志里 134 次风控各是哪一步引来的，
结果只有 15 次定得了因。原因是判据行写成 `URL={url[:90]}`，而风控页长这样

    https://www.zhipin.com/web/passport/zp/security.html?callbackUrl=https%3A%2F%2Fwww.zhipin.

那一步的名字全在 callbackUrl 里，90 字正好把它切在半截上——截出来的半截既不是地址
也不是步骤名，留着只会让人误读成"当时要去主站"。所以补一份判据：从整条 URL 里解出
落地页，按参数名找（实测 seed/name/ts 有排在 callbackUrl 前面的），并在四个已经会
读到风控页的出口都挂上。

这一族关键词只住在 browser_launcher 一处（和 是风控验证页 做邻居），调用方再抄一份
就会走偏——这条也有闸。
"""

import os
import re
import sys
import unittest
from urllib.parse import quote, unquote
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "flask-version"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import boss_bot.unified_config as UC
from boss_bot.browser_launcher import (风控去向表, 风控去向后缀, 是风控验证页,
                                       _剥到路径, _解出落地页)
from boss_bot.main_loop import UnifiedBotLoop

# 下面几条样例是从 logs/boss_bot.log* 与 logs/greet_engine.log 里原样捞出来的真实地址。
真实_详情页_相对写法 = (
    "https://www.zhipin.com/web/passport/zp/security.html"
    "?seed=BdYsUlfnn%2BndBSx9DLt4xmQx1DciwCRVA09mKsAxESI%3D&name=f45bbe80"
    "&ts=1792288820747&callbackUrl=%2Fjob_detail%2F0b9ea103fa2f39690nB90tW_GVBR.html"
    "&srcReferer=https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fgeek%2Fjob-recommend")
真实_详情页_绝对写法 = (
    "https://www.zhipin.com/web/passport/zp/verify.html"
    "?callbackUrl=https%3A%2F%2Fwww.zhipin.com%2Fjob_detail"
    "%2Fc51e263122446bf903J52t6-EFpQ.html%3F_security_check%3D1_1792305043385")
真实_搜索页 = (
    "https://www.zhipin.com/web/passport/zp/security.html"
    "?callbackUrl=https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fgeek%2Fjobs"
    "&appName=zhipin_geek_spa_web&code=37&seed=pUF7riEHo3WEXGa&ts=1792551600870")
真实_推荐页 = (
    "https://www.zhipin.com/web/passport/zp/verify.html"
    "?callbackUrl=https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fgeek%2Fjob-recommend"
    "&appName=zhipin_geek_spa_web&code=36")
真实_主站 = ("https://www.zhipin.com/web/passport/zp/verify.html"
             "?callbackUrl=https%3A%2F%2Fwww.zhipin.com%2F")
# 老判据行留在盘上的就是这副样子（断在 half 个域名上）——历史数据救不回来，
# 但判据绝不能替它编一个去向。
真实_被切断 = "https://www.zhipin.com/web/passport/zp/verify.html?callbackUrl=https%3A%2F%2Fwww.zhipin.co"


class 读得出真地址那一步Test(unittest.TestCase):
    def test_相对写法读出岗位详情页(self):
        self.assertEqual(风控去向后缀(真实_详情页_相对写法),
                         "（去向：岗位详情页 /job_detail/0b9ea103fa2f39690nB90tW_GVBR.html）")

    def test_认的是callbackUrl不是srcReferer(self):
        """这条 URL 里 srcReferer 也写着 job-recommend，读错了就说成"从推荐页来的"。"""
        self.assertIn("job-recommend", 真实_详情页_相对写法)
        self.assertNotIn("推荐", 风控去向后缀(真实_详情页_相对写法))

    def test_绝对写法读出岗位详情页且不带查询串(self):
        后 = 风控去向后缀(真实_详情页_绝对写法)
        self.assertEqual(后, "（去向：岗位详情页 /job_detail/c51e263122446bf903J52t6-EFpQ.html）")
        self.assertNotIn("_security_check", 后)

    def test_搜索结果页(self):
        self.assertEqual(风控去向后缀(真实_搜索页), "（去向：搜索结果页 /web/geek/jobs）")

    def test_推荐页不许读成搜索结果页(self):
        """job-recommend 排在 jobs 前面才分得开；顺序错了两条腿的账就混成一行。"""
        self.assertEqual(风控去向后缀(真实_推荐页), "（去向：岗位推荐页 /web/geek/job-recommend）")

    def test_裸根读成主站首页(self):
        self.assertEqual(风控去向后缀(真实_主站), "（去向：主站首页 /）")

    def test_验证页套验证页要解到最里层(self):
        """实测有双重编码的链（一张 security.html 的来路就是另一张 verify.html）。"""
        链 = ("https://www.zhipin.com/web/passport/zp/security.html?callbackUrl="
              "https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fpassport%2Fzp%2Fverify.html"
              "%3FcallbackUrl%3Dhttps%253A%252F%252Fwww.zhipin.com%252Fweb%252Fgeek%252Fjobs"
              "%26appName%3Dzhipin_geek_spa_web%26code%3D35")
        self.assertEqual(风控去向后缀(链), "（去向：搜索结果页 /web/geek/jobs）")

    def test_参数名不分大小写(self):
        self.assertIn("岗位详情页", 风控去向后缀(真实_详情页_绝对写法.replace("callbackUrl", "CallbackURL")))


class 读不出就留空Test(unittest.TestCase):
    """"取不到"不许印成一个看起来像答案的名字——半截域名补成"主站首页"就是编。"""

    def test_被截断的历史写法读不出(self):
        self.assertEqual(风控去向后缀(真实_被切断), "")

    def test_半截百分号编码读不出(self):
        for 残 in ("https://www.zhipin.com/web/passport/zp/security.html?callbackUrl=https%3",
                   "https://www.zhipin.com/web/passport/zp/security.html?callbackUrl=https））",
                   "https://www.zhipin.com/web/passport/zp/security.html?callbackUrl=",
                   "https://www.zhipin.com/web/passport/zp/security.html?seed=guGz4VLA266N"):
            self.assertEqual(风控去向后缀(残), "", 残)

    def test_没有callbackUrl的正常页面不加后缀(self):
        """反向哨兵：会话页、岗位页本来就带不出"去向"，给每行都挂个空括号就是刷屏。"""
        for 正常 in ("https://www.zhipin.com/web/geek/chat",
                     "https://www.zhipin.com/web/geek/job-recommend",
                     "https://www.zhipin.com/job_detail/0b9ea103fa2f39690nB90tW_GVBR.html",
                     "", None, "   "):
            self.assertEqual(风控去向后缀(正常), "", repr(正常))

    def test_套到第五层还没到底就不猜(self):
        """链太深时宁可留空：解到第 6 层还看见验证页，说明这串根本不是给人读的。"""
        from urllib.parse import quote
        最深 = "https://www.zhipin.com/web/passport/zp/security.html?callbackUrl=%2Fjob_detail%2Fx.html"
        起点 = 最深
        for _ in range(6):
            最深 = "https://www.zhipin.com/web/passport/zp/verify.html?callbackUrl=" + quote(最深, safe="")
        self.assertEqual(风控去向后缀(最深), "", 起点)
        # 反向哨兵：只套两层时照样要解到岗位详情页，别把有解的也判成读不出
        浅 = 起点
        for _ in range(2):
            浅 = "https://www.zhipin.com/web/passport/zp/verify.html?callbackUrl=" + quote(浅, safe="")
        self.assertIn("岗位详情页", 风控去向后缀(浅))

    def test_落地页本身还是验证页时不算去向(self):
        那页 = ("https://www.zhipin.com/web/passport/zp/verify.html?callbackUrl="
                "https%3A%2F%2Fwww.zhipin.com%2Fweb%2Fpassport%2Fzp%2Fsecurity.html")
        # 往里一层还是验证页且再没有下一层：解出来是空，不是"去向：又一张验证页"
        self.assertEqual(_解出落地页(那页), "")
        self.assertEqual(风控去向后缀(那页), "")


class 判据形状Test(unittest.TestCase):
    def test_剥路径只认带路径的地址(self):
        self.assertEqual(_剥到路径("https://www.zhipin.com/a/b?x=1"), "/a/b")
        self.assertEqual(_剥到路径("https://www.zhipin.com/a/b#frag"), "/a/b")
        self.assertEqual(_剥到路径("/job_detail/x.html"), "/job_detail/x.html")
        self.assertEqual(_剥到路径("https://www.zhipin.com/"), "/")
        # 半截的、只有主机名的、没头没尾的：一律空串，不许蒙成 /
        for 坏 in ("https://www.zhipin.com", "https://www.zhipin.co", "https%3", "job_detail/x.html"):
            self.assertEqual(_剥到路径(坏), "", 坏)

    def test_长路径要截住且留得住步骤名(self):
        """200 个字符的路径也要只占一行：截的是路径尾巴，步骤名不许跟着被截。"""
        后 = 风控去向后缀("https://www.zhipin.com/web/passport/zp/verify.html?callbackUrl="
                         "https%3A%2F%2Fwww.zhipin.com%2Fjob_detail%2F" + "A" * 200 + ".html")
        self.assertTrue(后.startswith("（去向：岗位详情页 /job_detail/"), 后)
        self.assertLessEqual(len(后), 60 + 20, "这一行要进日志的，长到几百字就没人看了")

    def test_每一步都有名字不许出现空名字(self):
        for 样 in (真实_详情页_相对写法, 真实_详情页_绝对写法, 真实_搜索页, 真实_推荐页, 真实_主站):
            后 = 风控去向后缀(样)
            self.assertRegex(后, r"^（去向：\S+ /\S*）$", 后)

    def test_表里不许把验证页自己登记成去向(self):
        """去向是"滑完要回去干的那一步"，不是拦路那张页——按前缀查 是风控验证页 查不住
        （键是半截路径，比整条 URL 短），所以直接钉死这一族词不许进表。"""
        for 键, 名 in 风控去向表:
            低 = 键.lower()
            for 拦路词 in ("passport", "verify", "security"):
                self.assertNotIn(拦路词, 低, "%s → %s：这是拦路页不是去向" % (键, 名))

    def test_主站首页这条兜底必须排在最后(self):
        """("/") 是任何路径的前缀，它排前面就把每一步都说成主站。"""
        self.assertEqual(风控去向表[-1][0], "/")


class 提示要真的带上去向Test(unittest.TestCase):
    """静态"函数体里出现过这个名字"抓不出"算了去向却没拼进那句话"，所以按行为验。"""

    def _loop(self, 验中):
        with patch("boss_bot.main_loop.BrowserManager"):
            loop = UnifiedBotLoop(config=UC.UnifiedConfig())
        loop.browser_manager = MagicMock()
        loop.browser_manager.验证页标签.return_value = [
            {"id": i, "url": u, "type": "page"} for i, u in enumerate(验中)]
        loop._emit_wind = MagicMock()
        loop._ensure_login_window = MagicMock(return_value=True)
        self.说的 = []
        loop._log = lambda 级, 消息, *a, **k: self.说的.append(str(消息))
        return loop

    def _开轮提示(self, 验中):
        loop = self._loop(验中)
        self.assertTrue(loop._被验证页挡着())
        警 = [m for m in self.说的 if "停在安全验证页" in m]
        self.assertEqual(len(警), 1, self.说的)
        return 警[0]

    def test_开轮提示里要写出是哪一步招来的(self):
        警 = self._开轮提示([真实_搜索页])
        self.assertIn("去向：搜索结果页 /web/geek/jobs", 警)

    def test_多张标签各自的去向都要说出来(self):
        警 = self._开轮提示([真实_搜索页, 真实_详情页_相对写法])
        self.assertIn("搜索结果页", 警)
        self.assertIn("岗位详情页", 警)

    def test_同一个去向不许说两遍(self):
        警 = self._开轮提示([真实_搜索页, 真实_搜索页, 真实_搜索页])
        self.assertEqual(警.count("去向：搜索结果页"), 1, 警)

    def test_读不出去向时那句话不许留个空括号(self):
        """提示要短：没解出 callbackUrl 就照旧说那一句，不许拼出「（去向：）」那种尾巴。"""
        警 = self._开轮提示(["https://www.zhipin.com/web/passport/zp/verify.html?seed=abc"])
        self.assertNotIn("去向", 警)
        self.assertTrue(警.endswith("滑完它自己会跳走"), 警)

    def test_健康检查那一行也要带上去向(self):
        """定因主要靠这行 DEBUG/WARN：它少了去向，134 次风控就又只剩几句"有验证码"。"""
        loop = self._loop([])
        inst = MagicMock()
        inst.url = 真实_详情页_绝对写法
        inst.run_js.return_value = {"inputs": 1, "text": "请完成下方验证", "iframe": 0, "img": 0}
        loop.browser_manager.get_instance.return_value = inst
        loop.browser_manager.get_search_page.return_value = inst
        loop._chat_handler = None
        self.assertEqual(loop._check_health(side="greet"), "captcha")
        判 = [m for m in self.说的 if "页面判据=captcha" in m]
        self.assertEqual(len(判), 1, self.说的)
        self.assertIn("去向：岗位详情页 /job_detail/c51e263122446bf903J52t6-EFpQ.html", 判[0])


class 判据只住一处Test(unittest.TestCase):
    def test_callbackUrl这个参数名只有判据模块认识(self):
        for 文件 in ("main_loop.py", "page_handler.py", "greet_engine.py"):
            源 = open(os.path.join(ROOT, "boss_bot", 文件), encoding="utf-8").read()
            代码 = "\n".join(l for l in 源.splitlines() if not l.lstrip().startswith("#"))
            self.assertNotIn("callbackUrl", 代码,
                             文件 + "：自己解 callbackUrl 就是第二份判据，会跟着一份漂")
            self.assertIn("风控去向后缀", 代码, 文件 + "：这一族日志得挂上去向")

    def test_四个出口都真的挂了后缀(self):
        锚 = [("main_loop.py", "def _被验证页挡着"),
              ("main_loop.py", "def _check_health"),
              ("page_handler.py", "def check_health"),
              ("greet_engine.py", "def _apply_job_inner")]
        for 文件, 函数 in 锚:
            源 = open(os.path.join(ROOT, "boss_bot", 文件), encoding="utf-8").read()
            i = 源.index(函数)
            j = 源.find("\n    def ", i + 10)
            段 = 源[i:j if j > 0 else len(源)]
            self.assertIn("风控去向后缀", 段, "%s/%s：这个出口读到风控页却没记去向" % (文件, 函数))

    def test_健康检查两条分支都记(self):
        """全标签页扫到那张、和读自己那张页判出来的，两条都得带去向——否则又只剩一半。"""
        源 = open(os.path.join(ROOT, "boss_bot", "main_loop.py"), encoding="utf-8").read()
        i = 源.index("def _check_health")
        段 = 源[i:源.find("\n    def ", i + 10)]
        self.assertEqual(段.count("风控去向后缀"), 2, 段[段.index("全标签页扫到") - 200:])


class 历史数据要说清定不了因Test(unittest.TestCase):
    """这条是给"以后怎么不触发"那次统计兜底的：读不出就报读不出，不许印成 0 次或全成功。

    10-30 换过一次判据。原来钉的是「盘上定得了因的比例 < 0.5」，当天全量 pytest 就红在
    这儿（0.5 not less than 0.5）。两条错处合成一次假红：
    ① 比例这个数天生要漂——新行每条都带去向、老行在截，攒得越久越往 1 走，红的那天
       未必是判据坏了，只是日子久了；
    ② 归档压成 .gz 之后 `open(...).read()` 读到的是乱码，一条地址都匹配不上，等于把
       老行从分母里悄悄抽走——分子没变、分母小了一大截，比例自己就窜上去了。
    所以改成三条与"攒了多少条"无关的事实：截到 90 字的老行一条都不许定得了因；报出去
    的路径必须在解出来的落地地址里真有过；没路径的残段不许被补成根。比例照数照印，
    只当读数，不再当闸。
    """

    def _盘上地址(self):
        """logs/ 与 logs/kept/ 里所有风控页地址——kept/ 现在多半是 .gz，必须走那份统一读法。

        这里只用 log_retention.读日志文本，不许自己 open()：这条闸上次就是被"压缩件读成
        乱码"抽掉分母才误报的，再写一份只认纯文本的读法等于把同一个坑留在原地。
        """
        glob_ = __import__("glob").glob
        from log_retention import 读日志文本
        out = []
        for pat in ("logs/boss_bot.log*", "logs/kept/*", "logs/greet_engine.log"):
            for 档 in glob_(os.path.join(ROOT, pat)):
                try:
                    文 = 读日志文本(档)
                except OSError:
                    continue
                out += re.findall(r"https://www\.zhipin\.com/web/passport/zp/"
                                  r"[a-z]+\.html\?[^\s\"']+", 文)
        return out

    def test_截到90字的老行一条都不许定得了因(self):
        """当年写成 `URL={url[:90]}`，正好 90 字那一批就是救不回来的存量。

        样本为空也必须红：那说明归档读不了了，"没样本"不许当成"通过"。
        """
        址们 = self._盘上地址()
        老行 = [u for u in 址们 if len(u) == 90]
        self.assertGreater(len(老行), 0,
                           "一条截到 90 字的老行都没读到——归档读不了或 glob 错了，"
                           "别把没样本印成通过")
        冒 = [u for u in 老行 if 风控去向后缀(u)]
        self.assertEqual(冒, [],
                         "%d 条半截地址被读成了去向，判据在替不存在的一步编名字" % len(冒))

    def test_定得了因的必须报得出原地址里真有的路径(self):
        """堵的是文档里那句"不许拿未知/主站首页填空"：印出去那串字得有出处。

        靶子是 风控去向后缀() 的返回值本身——自己复算一遍再跟它比，等于没比（第 4 条
        变异试出来过：把印出去的路径换成假的照样绿）。_解出落地页 在这里只用来
        取"原地址解出来是什么"，判据仍然只住在那一份函数里。
        """
        址们 = self._盘上地址()
        self.assertGreater(len(址们), 0, "日志一条都没匹配上——glob 错了，不许印结论")
        定 = [u for u in 址们 if 风控去向后缀(u)]
        print("盘上 %d 条风控地址，定得了因 %d 条（%.1f%%）"
              % (len(址们), len(定), 100.0 * len(定) / len(址们)))
        for u in 定:
            址 = _解出落地页(u)
            # 判据打在"这一行真要印出去的那串字"上，不是打在自己复算出来的路径上——
            # 复算的话，把印出去的路径换成假的也照样绿（变异试过一次，就是这么漏的）
            印 = 风控去向后缀(u).rpartition(" ")[2].rstrip("）")
            self.assertIn(印, unquote(址),
                          "%s → %r：这条路径不在解出来的落地地址里，是编的" % (u[:70], 印))

    def test_切断的残段不许被补成主站首页(self):
        """`https://www.zhipin.co` 这种半截串补一个 `/` 就会被读成"当时要去主站"。

        盘上真有整条落到根上的地址（10-29 13:09 那 4 条 callbackUrl 就是 %2F），
        所以不能拿"报根就算编"当判据；要钉的是"没有路径的残段被补出路径"这一种。
        """
        for 残 in ("https://www.zhipin.co", "https://www.zhipin.com", "https:", "https%3"):
            self.assertEqual(_剥到路径(残), "", "%s：带 scheme 没路径的残段该回空串" % 残)
            self.assertEqual(风控去向后缀(
                "https://www.zhipin.com/web/passport/zp/security.html?callbackUrl="
                + quote(残)), "", "%s 被读成了去向" % 残)


if __name__ == "__main__":
    unittest.main()
