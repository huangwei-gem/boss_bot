# -*- coding: utf-8 -*-
"""面板与引擎里不许有"跑到那一刻才炸"的名字。

起因：2026-10-20 用 pyflakes 扫常驻面板，扫出两类从来没被任何测试碰过的洞——
- 八个 socket 处理器（start_all / stop_all / confirm_login）调的是 `_now()`，
  而这个函数在整个文件里从来没定义过。谁发这几个事件，第一条 emit 就 NameError，
  回执一条也发不出去。`confirm_login` 正是「我已登录」的 socket 那条路。
- 界面「删除 Cookie」里 `resolve_path(...)` 同样没定义：那条本来要把用户的会话
  文件归档（不许真删），一进 except 就回一句"name 'resolve_path' is not defined"。

这类洞单元测试抓不到——它们只在"人真去点那一下"的路径上。所以门做成静态的：
把生产入口扫一遍，未定义名字和重复定义一律红。
"""

import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 扫这些文件：常驻面板 + 引擎主循环。都是"改了不重启就一直跑旧代码"的那一批，
# 也是他每天真在点的那一批。
生产文件 = ("flask-version/app.py", "boss_bot/main_loop.py")

要去掉的码 = ("undefined name", "redefinition of unused")


def _扫(text: str, 名字: str = "<内存里的样例>") -> list:
    """跑 pyflakes，只回"会当场炸"的那两类（未定义名字 / 重复定义）。"""
    from pyflakes.api import check
    from pyflakes.reporter import Reporter

    收 = io.StringIO()
    check(text, 名字, reporter=Reporter(收, 收))
    出 = []
    for 行 in 收.getvalue().splitlines():
        if any(码 in 行 for 码 in 要去掉的码):
            出.append(行)
    return 出


class 生产入口不许有未定义名字Test(unittest.TestCase):
    def test_每个生产文件都扫得动(self):
        """pyflakes 不在就当场红：这条门一旦静默跳过，上面那两个洞会原样留在里面"""
        try:
            import pyflakes  # noqa: F401
        except ImportError as e:
            self.fail(f"没装 pyflakes，这条门扫不了任何东西（requirements.txt 里要它）: {e}")

    def test_面板与引擎没有会当场炸的名字(self):
        红 = []
        for 相对 in 生产文件:
            路径 = os.path.join(ROOT, *相对.split("/"))
            self.assertTrue(os.path.exists(路径), f"生产文件不见了: {相对}")
            with io.open(路径, encoding="utf-8") as f:
                文本 = f.read()
            红 += [f"{相对} {行}" for 行 in _扫(文本, 相对)]
        assert not 红, "这些名字要么没定义、要么被重复定义覆盖，点到的那一刻就是 NameError：\n" \
                       + "\n".join(红)

    def test_这条门自己抓得住未定义名字(self):
        """变异哨兵：拿一段真会炸的代码喂进去，门必须报出 undefined name"""
        样例 = "def a():\n    return _从来没见过这个名字()\n"
        出 = _扫(样例)
        assert any("undefined name" in 行 for 行 in 出), \
            f"pyflakes 调用没生效（这条门是空转的假绿）: {出}"


class 登录回执不许只剩一条路Test(unittest.TestCase):
    """socket 那条 confirm_login 以前调 _now() 直接炸。前端现在走 HTTP，
    但两条路都得活着——留一条会炸的等于埋一个"某天又没人回执"的坑。"""

    def setUp(self):
        with io.open(os.path.join(ROOT, "flask-version", "app.py"),
                     encoding="utf-8") as f:
            self.文本 = f.read()

    def test_socket登录回执用的时间函数真有定义(self):
        assert "_now()" in self.文本, "confirm_login 那批 socket 回执不再打时间，改口径要同步这条门"
        assert "def _now(" in self.文本, \
            "调 _now() 却不定义它：emit 的第一句就 NameError，点「我已登录」永远没人回执"

    def test_界面删除Cookie走的是真归档(self):
        段 = self.文本[self.文本.index("def api_cookies_delete"):]
        段 = 段[:段.index("\n@app.route")] if "\n@app.route" in 段 else 段
        assert "archive_cookie_file" in 段, "删除按钮不许再直接删用户的会话文件"
        assert "resolve_path(" not in 段 or "uc.resolve_path(" in 段 \
            or "import resolve_path" in self.文本, \
            "归档目录用了没定义的 resolve_path，那句 except 会把 NameError 当成失败原因回给界面"


if __name__ == "__main__":
    unittest.main()
