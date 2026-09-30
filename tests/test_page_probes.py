# -*- coding: utf-8 -*-
"""页面探针脚本与 run_js 调用方式必须配对。

DrissionPage 的 `run_js(script, as_expr=False)` 先看 `is_js_func(script)`：
以 `function` 开头、以 `}` 结尾的才原样当函数声明调用，否则整段包成
`function(){<脚本>}` 当函数体。于是两种写法各有陷阱：

  - IIFE 表达式（`(function(){…})()`）不带 as_expr → 包进函数体后没有 return，
    结果恒为 undefined。**不抛异常**，调用方只看到空。
  - 函数体型脚本（第一个语句就是 `return`）带了 as_expr → 变成顶层 return，
    直接执行失败。

本仓两种都有：`CAPTCHA_PROBE_JS` 是 IIFE，`CHAT_SNAPSHOT_JS`/
`AUTO_GREET_PROBE_JS`/`LAST_MINE_BUBBLE_JS` 是函数体型。12:20 打招呼被停 3 次
就是 `_check_health` 用 IIFE 探针却没带 as_expr——页面证据一条拿不到，只剩
URL 那个不可靠的标记。活体取证见 tools/probe_as_expr_check.py。
"""
import re
from pathlib import Path

from DrissionPage._elements.chromium_element import is_js_func

import boss_bot.browser_launcher as BL
import boss_bot.greet_engine as GE
import boss_bot.main_loop as ML
import boss_bot.page_handler as PH

MODULES = (("boss_bot/greet_engine.py", GE),
           ("boss_bot/page_handler.py", PH),
           ("boss_bot/main_loop.py", ML),
           ("boss_bot/browser_launcher.py", BL))

CALL = re.compile(r"run_js\(\s*([A-Z][A-Z0-9_]*)\s*(,[^)\n]*)?\)")


def _mode(js: str) -> str:
    """这份脚本要让 DrissionPage 怎么执行。

    decl  完整的函数声明（`function…}`）——原样调用，不要 as_expr。
    body  函数体（第一个语句就是 return）——必须让 DrissionPage 包一层
          `function(){…}`，加了 as_expr 反而变成顶层 return，非法。
    expr  表达式（IIFE `(function(){…})()`）——必须 as_expr=True，
          否则被当函数体包进去，没有 return，结果恒为 undefined。
    """
    s = js.strip()
    if is_js_func(s):
        return "decl"
    if s.startswith("return"):
        return "body"
    return "expr"


def _calls():
    """每个 `run_js(大写常量, …)` 调用点，配上那份 JS 的真身。"""
    out = []
    for rel, mod in MODULES:
        src = Path(rel).read_text(encoding="utf-8")
        for m in CALL.finditer(src):
            name, rest = m.group(1), m.group(2) or ""
            js = getattr(mod, name, None)
            if isinstance(js, str):
                out.append((rel, name, "as_expr=True" in rest, js))
    return out


def test_所有探针调用点都能找到对应的JS常量():
    assert len(_calls()) >= 4, "正则没抓到几个 run_js(常量) 调用，测试本身失效了"


def test_表达式探针必须带as_expr():
    """不带的话 DrissionPage 把它包成函数体，没有 return，调用方永远拿到空。

    2026-09-30 12:20 打招呼被停 3 次就是这么来的：`_check_health` 读不到探针，
    只剩 URL 一个信号，而 `?_security_check=` 是静默风控参数、页面照常可用。
    """
    broken = [(rel, name) for rel, name, has_expr, js in _calls()
              if _mode(js) == "expr" and not has_expr]
    assert not broken, f"这些 IIFE 探针没带 as_expr=True，实测返回 undefined：{broken}"


def test_函数体型探针不许误带as_expr():
    """反过来也一样：脚本以 return 开头时，as_expr=True 会让它变成顶层 return。"""
    broken = [(rel, name) for rel, name, has_expr, js in _calls()
              if _mode(js) != "expr" and has_expr]
    assert not broken, f"这些脚本是函数体型，带 as_expr=True 会执行失败：{broken}"
