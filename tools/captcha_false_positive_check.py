# -*- coding: utf-8 -*-
"""只读取证：验证码到底是"页面真有挑战"还是"后端自己吓自己"。

12:20:31 / 12:20:47 两个号都报"检测到验证码/风控拦截"，三次 60 秒都等不到
"已恢复"，于是打招呼被停。但 BOSS 窗口里根本没有验证图。这个脚本连上正在
跑的 cloakbrowser（纯只读，不点、不发、不导航），把每个标签页的判据逐条摊开：

  - URL 里有没有 _security_check（粘滞标记）
  - 现在的探针正文命中了哪个关键词、命中处前后是什么字
  - 现在的探针 DOM 选择器命中了什么元素、这个元素有多大/可见吗
  - 同一份 JS 用 as_expr=True 和不带 as_expr 各自返回什么

用法：python tools/captcha_false_positive_check.py
"""
import json

import websocket

from boss_bot.page_handler import CAPTCHA_PROBE_JS

PORTS = (9222, 9223)

# 逐条拆开定罪判据，看到底是哪一条在响
AUDIT_JS = r'''
(function() {
  function info(el) {
    if (!el) return null;
    var r = el.getBoundingClientRect();
    var st = window.getComputedStyle(el);
    return {cls: (el.className || '').toString().slice(0, 70),
            tag: el.tagName, w: Math.round(r.width), h: Math.round(r.height),
            display: st.display, visibility: st.visibility, opacity: st.opacity,
            inFlow: r.width > 4 && r.height > 4 && st.display !== 'none'};
  }
  var body = document.body ? (document.body.innerText || "") : "";
  var kws = ["安全验证", "验证码", "滑动验证", "请完成验证", "拖动滑块",
             "人机验证", "图形验证"];
  var textHits = [];
  for (var i = 0; i < kws.length; i++) {
    var p = body.indexOf(kws[i]);
    if (p >= 0) textHits.push({kw: kws[i], at: p,
                               around: body.slice(Math.max(0, p - 40), p + 40)
                                        .replace(/\s+/g, " ")});
  }
  var sels = [".nc-container", ".verify-wrap", ".geetest_panel", ".verify-box",
              ".captcha-box", ".security-check", ".verify-panel",
              "#tcaptcha", ".vc-captcha", ".captcha"];
  var domHits = [];
  for (var j = 0; j < sels.length; j++) {
    var els = document.querySelectorAll(sels[j]);
    for (var k = 0; k < els.length; k++) domHits.push({sel: sels[j],
                                                       info: info(els[k])});
  }
  return JSON.stringify({url: location.href, title: document.title,
                         bodyLen: body.length, textHits: textHits,
                         domHits: domHits});
})()
'''


def tabs(port):
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
        return [t for t in json.load(r) if t.get("type") == "page"]


def evaluate(ws_url, script, as_expr):
    """复现 DrissionPage run_js 的两条分支，只看返回值。

    as_expr=True 走 Runtime.evaluate，IIFE 本身就是表达式 → 有值。
    as_expr=False 时 DrissionPage 先看 is_js_func()：我们的脚本以 `(` 开头、
    以 `)` 结尾，判定不是函数，于是包成 `function(){<脚本>}` 当函数体调用——
    脚本里没有 return，结果恒为 undefined。
    """
    expr = script if as_expr else f"(function(){{{script}}})()"
    conn = websocket.create_connection(ws_url, timeout=8, suppress_origin=True)
    try:
        payload = {"id": 1, "method": "Runtime.evaluate",
                   "params": {"expression": expr, "returnByValue": True}}
        conn.send(json.dumps(payload))
        while True:
            msg = json.loads(conn.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    return {"error": msg["error"]}
                res = msg.get("result", {}).get("result", {})
                return {"type": res.get("type"), "value": res.get("value")}
    finally:
        conn.close()


def main():
    for port in PORTS:
        try:
            found = tabs(port)
        except Exception as e:
            print(f"端口 {port} 连不上: {e}")
            continue
        print("=" * 78)
        print(f"端口 {port}（账号{'1' if port == 9222 else '2'}）共 {len(found)} 个页面标签")
        for t in found:
            print("-" * 78)
            print(f'  标题: {t.get("title", "")[:60]}')
            print(f'  URL : {t.get("url", "")[:120]}')
            print(f'  _security_check 在 URL 里: '
                  f'{"是" if "_security_check" in (t.get("url") or "") else "否"}')
            ws = t.get("webSocketDebuggerUrl")
            if not ws:
                print("  无 websocket 端点，跳过 DOM 取证")
                continue
            try:
                audit = evaluate(ws, AUDIT_JS, True)
            except Exception as e:
                print(f"  取证失败: {e}")
                continue
            if audit.get("error"):
                print(f"  取证报错: {audit['error']}")
                continue
            data = json.loads(audit["value"]) if audit.get("value") else {}
            print(f'  正文长度: {data.get("bodyLen")}  命中文字判据: '
                  f'{len(data.get("textHits") or [])}  命中 DOM 判据: '
                  f'{len(data.get("domHits") or [])}')
            for h in data.get("textHits") or []:
                print(f'    [文字] 「{h["kw"]}」@{h["at"]} 上下文: …{h["around"]}…')
            for h in data.get("domHits") or []:
                print(f'    [DOM ] {h["sel"]} → {h["info"]}')
            for ae in (True, False):
                r = evaluate(ws, CAPTCHA_PROBE_JS, ae)
                shown = r.get("value") if r.get("type") != "undefined" else "undefined"
                print(f'    现行探针 as_expr={ae} → {shown!r} '
                      f'(type={r.get("type")})')
        print()


if __name__ == "__main__":
    main()
