# -*- coding: utf-8 -*-
"""只读验证：项目里每个"页面探针"在 DrissionPage 真实语义下到底有没有返回值。

DrissionPage 的 run_js(script, as_expr=False) 会先问 is_js_func(script)：
只有以 `function` 开头、以 `}` 结尾的脚本才原样当函数声明调用；
否则包成 `function(){<脚本>}` 当函数体——我们的探针是 IIFE（`(function(){…})()`），
包完没有 return，结果恒为 undefined。

这个脚本连上正在跑的 cloakbrowser，把每个探针按两种调用各跑一次，
只读 DOM，不点、不发、不导航。

用法：python tools/probe_as_expr_check.py
"""
import json
import re
import urllib.request
from pathlib import Path

import websocket

from DrissionPage._elements.chromium_element import is_js_func

import boss_bot.greet_engine as GE
import boss_bot.main_loop as ML
import boss_bot.page_handler as PH

MODS = (GE, PH, ML)


def collect_calls():
    """从源码里抓 `<名字>.run_js(常量名[, as_expr=…])`，返回 (模块, 常量名, 带没带 as_expr)。"""
    found, seen = [], set()
    for mod in MODS:
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for m in re.finditer(r"run_js\(\s*([A-Z][A-Z0-9_]*)\s*(,[^)\n]*)?\)", src):
            name = m.group(1)
            rest = m.group(2) or ""
            js = getattr(mod, name, None)
            if not isinstance(js, str):
                continue
            key = (mod.__name__, name)
            if key in seen:
                continue
            seen.add(key)
            found.append((mod.__name__.split(".")[-1], name,
                          "as_expr=True" in rest, js))
    return found


def evaluate(ws_url, script, as_expr):
    expr = script if as_expr else f"(function(){{{script}}})()"
    conn = websocket.create_connection(ws_url, timeout=8, suppress_origin=True)
    try:
        conn.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True}}))
        while True:
            msg = json.loads(conn.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    return {"error": msg["error"].get("message")}
                res = msg.get("result", {}).get("result", {})
                return {"type": res.get("type"), "value": res.get("value")}
    finally:
        conn.close()


def _mode(js: str) -> str:
    """decl=函数声明原样调用；body=函数体(以 return 开头)，要靠包一层；
    expr=IIFE 表达式，必须 as_expr=True。"""
    s = js.strip()
    if is_js_func(s):
        return "decl"
    return "body" if s.startswith("return") else "expr"


def main():
    pages = []
    for port in (9222, 9223):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                        timeout=5) as r:
                for t in json.load(r):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        pages.append((port, t))
        except Exception as e:
            print(f"端口 {port} 连不上: {e}")
    if not pages:
        print("没有可连的标签页（浏览器没开？）")
        return

    print("=" * 90)
    print("静态判定：脚本形态与调用方式配不配对")
    dead = []
    for mod_name, name, has_expr, js in collect_calls():
        mode = _mode(js)
        need_expr = mode == "expr"
        wrong = need_expr != has_expr
        if wrong:
            dead.append(f'{mod_name}.{name}')
        print(f'  {mod_name+"."+name:<44} 形态={mode:<5} 带as_expr={"是" if has_expr else "否"} '
              f'→ {"★ 配错，读回来是空/报错" if wrong else "正常"}')
    print(f"\n  配错的调用点: {dead or '无'}")

    print("=" * 90)
    print("活体复核：在真页面上各跑一次（两种调用方式都跑，看哪种真有返回值）")
    for port, t in pages:
        print(f'-- 端口 {port} {t["url"][:70]}')
        for mod_name, name, _has_expr, js in collect_calls():
            try:
                a = evaluate(t["webSocketDebuggerUrl"], js, True)
                b = evaluate(t["webSocketDebuggerUrl"], js, False)
            except Exception as e:
                print(f'   {name} 跑失败: {e}')
                continue
            def show(r):
                if r.get("error"):
                    return f'报错: {r["error"][:50]}'
                if r.get("type") == "undefined":
                    return "undefined（拿不到东西）"
                if r.get("type") == "string":
                    return repr((r.get("value") or "")[:70])
                return f'<{r.get("type")}>'
            print(f'   {name:<24} as_expr=True  → {show(a)}')
            print(f'   {"":<24} as_expr=False → {show(b)}')
            print(f'   {"":<24} 该脚本形态={_mode(js)}，正确用法='
                  f'{"as_expr=True" if _mode(js) == "expr" else "不带 as_expr"}')


if __name__ == "__main__":
    main()
