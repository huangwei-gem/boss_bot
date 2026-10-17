# -*- coding: utf-8 -*-
"""薪资列不许长成负数的样子。

现场（2026-10-17 他指着界面问"怎么会出现负数"）：打招呼记录里 12 条薪资显示成
`-23K`、`-24K·26薪`、`-21K`。用无头实例把面板真渲染出来抓过 DOM：那些格子
`<td>` 里的原文就是 `-23K`，不是样式问题。

根因在反爬解码：BOSS 的薪资字体把数字放在私用区，`_decode_anti_scrape` 只映射
U+E030–U+E039（0–9），**其余私用区字符一律 `continue` 直接丢掉**。段位一挪，
"15-23K" 的前两位被无声吃掉，剩下 `-23K`——界面读起来像个负数，而且这个值还会
往三个地方去：AI 判分提示词（`薪资：-23K`）、薪资格子、以及"薪资≥"筛选
（parseSalaryMin("-23K") 数到 23，残缺值冒充过线岗）。

两头都要堵：
- 解码层：读不出的字符改成 `?` 占位，绝不静默删。占位让"这里没读全"可见，
  删字符会把残缺伪装成合法数字；同一趟把没映射上的码点写进日志，
  下一轮才有依据决定是扩映射还是换退路（本轮不猜新段位对应哪个数字）。
- 展示/判分/筛选层：含 `?` 或缺了开头的薪资一律当"没读到"——界面画 `—`、
  提示词留空、薪资筛选不认它。存量那 12 条坏数据靠这一层也不再骗人。

私用区字符在源码里一律用 chr(0xXXXX) 造，不写裸字符：裸私用区字符会被工具链
当成空白吞掉（本文件第一版就因此变成"什么都没断言"）。
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INDEX_HTML = ROOT / "flask-version" / "templates" / "index.html"

U_1 = chr(0xE031)      # 已映射段位里的 1（U+E030 起是 0）
U_5 = chr(0xE035)      # 已映射段位里的 5
U_NEW_A = chr(0xF0B7)  # 未映射段位——本轮只证明"读不出要留痕"，不猜它对应哪个数字
U_NEW_B = chr(0xF06C)


def _引擎(收到的=None):
    """绕开 __init__：解码只用到 log_cb，构造引擎要拉浏览器和配置。"""
    from boss_bot.greet_engine import GreetEngine
    引擎 = GreetEngine.__new__(GreetEngine)
    引擎.log_cb = (lambda 行: 收到的.append(str(行))) if 收到的 is not None else None
    return 引擎


def 解码(text):
    return _引擎()._decode_anti_scrape(text)


class 解码层不吞字符Test:

    def test_认得的私用区数字照旧映射(self):
        """反向哨兵：已映射段位不能被占位符替代，否则整列薪资全变 ??"""
        assert 解码(U_1 + U_5 + "-23K") == "15-23K"

    def test_没映射上的私用区字符要留占位符(self):
        assert 解码(U_NEW_A + U_NEW_B + "-23K") == "??-23K"
        assert not 解码(U_NEW_A + U_NEW_B + "-23K").startswith("-")

    def test_纯文字与空值不受影响(self):
        assert 解码("22-29K·14薪") == "22-29K·14薪"
        assert 解码("") == ""
        assert 解码(None) is None

    def test_没映射上的码点要写进日志(self):
        """不写日志就只能靠猜：下一轮没法判断该扩映射还是换退路"""
        收到的 = []
        引擎 = _引擎(收到的)
        assert 引擎._decode_anti_scrape(U_NEW_A + "-23K") == "?-23K"
        assert any("F0B7" in 行.upper() for 行 in 收到的), 收到的

    def test_同一个码点不逐条刷屏(self):
        """一张搜索页几十个卡片都是同一个段位，日志只该说一句"""
        收到的 = []
        引擎 = _引擎(收到的)
        for _ in range(20):
            引擎._decode_anti_scrape(U_NEW_A + "-23K")
        记了 = [行 for 行 in 收到的 if "F0B7" in 行.upper()]
        assert len(记了) == 1, 收到的


class 判分提示不许拿残缺薪资Test:

    def test_残缺薪资在提示词里留空(self):
        from boss_bot.greet_engine import 薪资可信
        assert 薪资可信("22-29K") == "22-29K"
        assert 薪资可信("361-411元/天") == "361-411元/天"
        assert 薪资可信("15K·14薪") == "15K·14薪"
        assert 薪资可信("??-23K") == ""
        assert 薪资可信("-23K") == "", "存量那种残缺值也不能进提示词"
        assert 薪资可信("-24K·26薪") == ""
        assert 薪资可信("22-K") == "", "区间那头被吃掉一位，剩下的看着也像残缺"
        assert 薪资可信("面议") == "面议", "没数字的合法写法不能被当成残缺"
        assert 薪资可信("") == ""
        assert 薪资可信(None) == ""

    def test_提示词那一行用的是同一个判据(self):
        源 = (ROOT / "boss_bot" / "greet_engine.py").read_text(encoding="utf-8")
        assert "薪资：{薪资可信" in 源, "提示词还在直接拼 job['salary']"


def _抽函数(src, name):
    m = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", src)
    assert m, "index.html 里找不到 %s()" % name
    start = src.index("{", m.start())
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError("%s() 大括号没配对" % name)


class 界面那一格Test:
    """真把 index.html 的函数交给 node 跑：扫字符串证明不了表格里印的是什么"""

    def 跑(self, 函数名, 值列表):
        if shutil.which("node") is None:
            pytest.skip("需要 node 才能真跑前端函数")
        src = INDEX_HTML.read_text(encoding="utf-8")
        # parseSalaryMin 要用可信薪资，单抽一个函数会 ReferenceError：
        # 依赖表在这里只有一层，写死比造通用解析器诚实
        要抽 = (["可信薪资", 函数名] if 函数名 != "可信薪资" else [函数名])
        js = "const V = %s;\n%s\nconsole.log(JSON.stringify(V.map(%s)));\n" % (
            json.dumps(值列表, ensure_ascii=False),
            "\n".join(_抽函数(src, n) for n in 要抽), 函数名)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as f:
            f.write(js)
            路径 = f.name
        try:
            进程 = subprocess.run(["node", 路径], capture_output=True, text=True, timeout=30)
        finally:
            Path(路径).unlink(missing_ok=True)
        assert 进程.returncode == 0, 进程.stderr
        return json.loads(进程.stdout.strip())

    def test_残缺薪资不画成负数(self):
        assert self.跑("可信薪资", ["-23K", "??-23K", "-24K·26薪"]) == ["", "", ""]

    def test_完整薪资原样显示(self):
        """反向哨兵：收紧判据不能把正常薪资也藏掉"""
        assert self.跑("可信薪资", ["22-29K", "361-411元/天", "15K·14薪", "面议"]) == \
            ["22-29K", "361-411元/天", "15K·14薪", "面议"]
        assert self.跑("可信薪资", ["22-K"]) == [""]

    def test_薪资筛选不认残缺值(self):
        """parseSalaryMin("-23K") 以前数到 23：残缺值冒充过线岗"""
        assert self.跑("parseSalaryMin", ["-23K", "??-23K"]) == [0, 0]
        assert self.跑("parseSalaryMin", ["22-29K", "15K·14薪"]) == [22, 15]

    def test_两端判据对同一批值要给一样的答案(self):
        """判分那头是 Python、界面这头是 JS：两份实现必须逐值相同，
        否则会出现"提示词里没这条薪资、界面上却印着 -23K"这种对不上的日子"""
        from boss_bot.greet_engine import 薪资可信
        样本 = ["22-29K", "361-411元/天", "15K·14薪", "面议", "-23K", "-24K·26薪",
                "??-23K", "22-K", "8-K", "", "10K", "1.5万-3万", "23K-", "—23K"]
        assert self.跑("可信薪资", 样本) == [薪资可信(x) for x in 样本]

    def test_薪资格子与筛选都走这个判据(self):
        源 = INDEX_HTML.read_text(encoding="utf-8")
        assert 源.count("可信薪资(") >= 3, "打招呼记录或台账那一格还在直接印 salary"
        筛选源 = re.sub(r"\s+", "", _抽函数(源, "parseSalaryMin"))
        assert "可信薪资(salary)" in 筛选源, "薪资筛选没接残缺判定"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
