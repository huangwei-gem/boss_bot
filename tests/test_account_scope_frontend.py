# -*- coding: utf-8 -*-
"""账号区前端这几条改动的源码锁。

都是"点了没反应"那一类：数据范围条开机不出现、点切换 chip 不高亮、新增账号选不中、
右侧还留着登录入口。这些在 JS 里没法单测跑出来（要真浏览器），所以按本仓库的既有做法
锁源码形状，改回去就红。真机点击另有一套（tests/e2e_account_scope_ui.py）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")


def _body(fn_name):
    """取一个顶层 function 的源码块（按花括号配对，够锁形状用）"""
    m = re.search(r"\nfunction " + re.escape(fn_name) + r"\s*\(", HTML)
    assert m, f"界面上已经没有 {fn_name}() 了"
    i = HTML.index("{", m.start())
    depth = 0
    for j in range(i, len(HTML)):
        if HTML[j] == "{":
            depth += 1
        elif HTML[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML[i:j + 1]
    raise AssertionError(f"{fn_name} 的花括号没闭合")


def test_点数据范围立刻重绘chip():
    """以前只有 renderMetrics 末尾或 status_update 会重绘：机器人没跑过时点了确实切了
    数据但视觉不动，用户就判成"切换无效" """
    assert "renderMetricsScope()" in _body("setDataScope")


def test_配置一到就画数据范围条():
    """开机 loadMetrics 与 loadConfig 并发，metrics 先到时账号列表还是空的，
    切换条要等 20 秒轮询才冒出来 """
    assert "renderMetricsScope()" in _body("loadConfig")


def test_loadConfig_把_promise_传出去():
    """不 return 的话调用方 .then 里读到的是旧的 accounts 长度 """
    assert "return fetch(" in _body("loadConfig")


def test_新增账号等配置落地后才选中():
    body = _body("addAccount")
    assert "loadConfig().then(" in body, f"还是同步算长度：{body[:200]}"
    after = body.split("loadConfig().then(", 1)[1]
    assert "config.accounts.length - 1" in after, "选中项算在新账号进列表之前"
    assert "activeAccountIdx = config.accounts.length - 1;" not in body.split(
        "loadConfig().then(", 1)[0], "长度赋值不能留在 then 外面"


def test_新增账号顺手切到它的数据范围():
    """新增完不切范围，用户还得自己去右侧点一下才明白归属 """
    assert "setDataScope(String(activeAccountIdx))" in _body("addAccount")


def test_右侧不再留登录入口():
    """口径：左侧只管登录，右侧数据范围只管查看 """
    assert 'id="btnConfirmLogin"' not in HTML
    assert 'onclick="confirmLogin(' not in HTML
    assert "function confirmLogin(" not in HTML, "入口撤了函数还留着，迟早又被接回去"


def test_左侧账号仍然能登录和检测():
    row = _body("renderAccounts")
    assert "loginAccount(" in row, "登录能力只能从左侧走，不能一起撤掉"
    assert "checkAccountCookie(" in row, "登录状态检测的点没了"
    assert "cookie-status-dot" in row


def test_看不准时给灰点而不是蒙颜色():
    """后端 uncertain 会回 valid=null，界面必须走"未知"分支 """
    row = _body("renderAccounts")
    assert "cookieStatusCache[i].valid === true" in row
    assert "cookieStatusCache[i].valid === false" in row


def test_脚本里没有裸中文注释行():
    """上一轮把一条注释写成两行，第二行丢了 //：中文在 JS 里是合法标识符，
    语法照样过，运行时 loadConfig 第一句就抛 ReferenceError——整个面板空着。
    pytest 全绿也发现不了，只有真浏览器会炸，所以在这里补一道静态锁。"""
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, re.S)
    assert scripts, "页面里没有内联脚本？"
    bad = []
    for src in scripts:
        for n, line in enumerate(src.replace("\r\n", "\n").split("\n"), 1):
            t = line.strip()
            if t and ("\u4e00" <= t[0] <= "\u9fff" or t[0] in "（、“"):
                bad.append(f"  行{n}: {t[:70]}")
    assert not bad, "这些行没有 // 前缀，会被当成表达式：\n" + "\n".join(bad)
