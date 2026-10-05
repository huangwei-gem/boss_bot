# -*- coding: utf-8 -*-
"""只读探针：连到已在跑的浏览器，看岗位详情页上「立即沟通」点下去之后到底发生了什么。

不点击、不输入、不发送——只读 DOM 和截图。用来区分两种失败：
  A. 账号当日沟通额度用完（BOSS 只弹 toast，抽屉永不出现）
  B. 页面/流程问题（抽屉其实在别的标签页/iframe 里）

运行：python tools/probe_no_drawer.py [端口，默认 9222]
"""
import json
import sys

import requests
import websocket

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9222

JS = """
(() => {
  const vis = el => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 4 && r.height > 4 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const btns = [...document.querySelectorAll('a,button,div[role="button"],span')]
    .filter(e => /沟通|继续|投递|打招呼/.test(e.textContent || ''))
    .filter(e => (e.textContent || '').trim().length < 24)
    .map(e => ({tag: e.tagName, text: e.textContent.trim(), vis: vis(e), cls: (e.className || '').toString().slice(0, 60)}));
  const overlays = [...document.querySelectorAll('body *')]
    .filter(e => {
      const s = getComputedStyle(e);
      return (s.position === 'fixed' || s.position === 'absolute') && vis(e)
        && (e.textContent || '').trim().length > 1 && (e.textContent || '').trim().length < 120;
    })
    .map(e => ({cls: (e.className || '').toString().slice(0, 50), text: e.textContent.trim().replace(/\\s+/g, ' ').slice(0, 100)}))
    .slice(0, 25);
  return {
    url: location.href,
    hasChatInput: !!document.querySelector('#chat-input, .chat-input, textarea'),
    inputs: [...document.querySelectorAll('input,textarea')].map(i => i.id || i.className.toString().slice(0, 30)).slice(0, 12),
    iframes: [...document.querySelectorAll('iframe')].map(f => ({id: f.id, cls: (f.className||'').toString().slice(0,40), src: (f.src||'').slice(0, 70)})),
    greetButtons: btns.slice(0, 10),
    overlays,
  };
})()
"""


def evaluate(ws_url, expr):
    ws = websocket.create_connection(ws_url, timeout=20, suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                           "params": {"expression": expr, "returnByValue": True}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return msg.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


def main():
    tabs = requests.get(f"http://127.0.0.1:{PORT}/json", timeout=10).json()
    for t in tabs:
        if t.get("type") != "page":
            continue
        url = t.get("url", "")
        if "job_detail" not in url:
            print(f"--- 跳过非岗位页: {url[:80]}")
            continue
        print(f"=== {t.get('title','')[:40]} | {url[:90]} ===")
        got = evaluate(t["webSocketDebuggerUrl"], JS)
        if not got:
            print("  求值失败")
            continue
        print("  chat输入框:", got["hasChatInput"], "输入框列表:", got["inputs"])
        print("  iframe:", json.dumps(got["iframes"], ensure_ascii=False)[:300])
        print("  沟通类按钮:", json.dumps(got["greetButtons"], ensure_ascii=False)[:400])
        print("  可见浮层:")
        for o in got["overlays"]:
            print("    -", o["cls"], "|", o["text"])


if __name__ == "__main__":
    main()
