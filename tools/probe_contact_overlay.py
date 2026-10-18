# -*- coding: utf-8 -*-
"""只读探针：聊天页联系人搜索框写名字之后，浮层到底长什么样（不点行、不点发送）。

连的是线上 9222/9223 的 CDP，只做 Runtime.evaluate 读 DOM；唯一的写动作是把
姓名填进联系人搜索框（机器人本来每轮也在填），填完立刻清空复原。

用法：
    python tools/probe_contact_overlay.py --port 9222 --name 宋女士 --company 长沙南舒文化传媒
"""
import argparse
import json
import sys
import time
import urllib.request

from websocket import create_connection

CHAT_MARK = "/web/geek/chat"

FIND = r"""
(function(){
  function n(s){return document.querySelectorAll(s).length;}
  var inp=document.querySelector('input.boss-search-input');
  return JSON.stringify({
    url: location.href,
    ph: inp? inp.getAttribute('placeholder'): null,
    value: inp? inp.value: null,
    li_search_list: n('li.search-list'),
    any_search_list: n('[class*=search-list]'),
    friend: n('.friend-content'),
    boss_name: n('.boss-name'),
    overlay: (function(){
      var best=null;
      [].slice.call(document.querySelectorAll('[class*=search]')).forEach(function(e){
        if(e.offsetParent===null) return;
        var r=e.getBoundingClientRect();
        if(r.height<40) return;
        if(!best||r.height>best.h) best={cls:String(e.className).slice(0,70),
            h:Math.round(r.height), kids:e.children.length,
            kidCls:[].slice.call(e.children).slice(0,6).map(function(c){
              return c.tagName+'.'+String(c.className).slice(0,55);}).join(' | ')};
      });
      return best;
    })(),
    rows: [].slice.call(document.querySelectorAll('li')).filter(function(li){
        return li.offsetParent!==null && li.querySelector('.boss-name');
      }).slice(0,10).map(function(li){
        var c=li.querySelector('.company-name');
        return {cls:String(li.className).slice(0,45),
                name:(li.querySelector('.boss-name')||{}).textContent,
                comp:c?c.textContent:''};
      })
  });
})()
"""

TYPE = r"""
(function(){
  var inp=document.querySelector('input.boss-search-input');
  if(!inp) return 'no-input';
  var setter=Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,'value').set;
  setter.call(inp,%s);
  inp.dispatchEvent(new Event('input',{bubbles:true}));
  return 'typed';
})()
"""


def chat_ws(port):
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=6))
    for t in tabs:
        if t.get("type") == "page" and CHAT_MARK in (t.get("url") or ""):
            return create_connection(t["webSocketDebuggerUrl"], timeout=20,
                                     suppress_origin=True)
    raise SystemExit(f"端口 {port} 上没有聊天页标签，先启动这个号")


def ev(ws, expr):
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == 1:
            r = m.get("result", {}).get("result", {})
            return r.get("value", m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--name", default="宋女士")
    ap.add_argument("--company", default="")
    a = ap.parse_args()
    ws = chat_ws(a.port)
    print("== 填之前 ==")
    print(json.dumps(json.loads(ev(ws, FIND)), ensure_ascii=False, indent=1))
    print("== 写入", a.name, "==")
    print(ev(ws, TYPE % json.dumps(a.name, ensure_ascii=False)))
    for _ in range(4):
        time.sleep(0.8)
        d = json.loads(ev(ws, FIND))
        print(f"浮层: li.search-list={d['li_search_list']} 任意search-list={d['any_search_list']} "
              f"侧栏={d['friend']} .boss-name={d['boss_name']}")
        if d["li_search_list"] or d["rows"]:
            print(json.dumps(d["overlay"], ensure_ascii=False))
            for r in d["rows"]:
                print("   行", r["cls"], "|", (r["name"] or "").strip(), "|",
                      (r["comp"] or "").strip().replace("\n", " ")[:40])
            break
    print("== 复原（清空搜索框）==")
    print(ev(ws, TYPE % '""'))
    ws.close()


if __name__ == "__main__":
    sys.exit(main())
