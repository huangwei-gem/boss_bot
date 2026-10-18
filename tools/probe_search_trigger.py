# -*- coding: utf-8 -*-
"""联系人搜索到底是被什么事件触发的：同一次开浏览器里并排试三种写法。

背景：线上日志大量「侧栏滚到底、搜索也找不到会话」，但同一个浏览器里换个名字
又能搜出来——说明不是选择器坏了，是"写字进去"这一下有时没真的触发搜索。
这里把三种触发方式对同一个名字各试一遍，把每种方式下浮层的行数和时间戳打出来。

只用临时端口 + 临时 profile + Cookie 文件；全程只点浮层以外的读操作，
不点发送、不点打招呼、不发简历。

用法：
    python tools/probe_search_trigger.py --names 宋女士 唐女士 李女士
"""
import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402

CHAT_URL = "https://www.zhipin.com/web/geek/chat"

# 三种把字弄进输入框的方式：
#   input  : 现在代码里那套（原生 setter + input 事件）
#   keyup  : 再补一组键盘事件（组件可能挂在 keyup/keydown 上）
#   realkey: 先 focus 再逐字符 dispatch keydown/keypress/input/keyup（最像人打字）
KINDS = ("input", "keyup", "realkey")

TYPE_JS = r"""
(function(){
  var mode=%(mode)s, text=%(text)s;
  var inp=document.querySelector('input.boss-search-input');
  if(!inp) return 'no-input';
  var setter=Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,'value').set;
  function fire(el,t,extra){
    var o={bubbles:true,cancelable:true};
    if(extra) for(var k in extra) o[k]=extra[k];
    el.dispatchEvent(new (t==='input'?Event:KeyboardEvent)(t,o));
  }
  if(mode==='input'){
    setter.call(inp,text); fire(inp,'input'); return 'ok';
  }
  if(mode==='keyup'){
    setter.call(inp,text); fire(inp,'input');
    ['keydown','keypress','keyup'].forEach(function(t){
      fire(inp,t,{key:'Enter',code:'Enter',keyCode:13,which:13});});
    return 'ok';
  }
  inp.focus();
  for(var i=0;i<text.length;i++){
    var ch=text[i];
    fire(inp,'keydown',{key:ch});
    fire(inp,'keypress',{key:ch});
    setter.call(inp,inp.value+ch); fire(inp,'input');
    fire(inp,'keyup',{key:ch});
  }
  return 'ok';
})()
"""

CLEAR_JS = r"""
(function(){
  var inp=document.querySelector('input.boss-search-input');
  if(!inp) return 'no-input';
  var setter=Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,'value').set;
  setter.call(inp,''); inp.dispatchEvent(new Event('input',{bubbles:true}));
  inp.blur();
  return 'ok';
})()
"""

READ_JS = r"""
(function(){
  var inp=document.querySelector('input.boss-search-input');
  var lis=document.querySelectorAll('li.search-list');
  var names=[];
  for(var i=0;i<lis.length&&i<6;i++){
    var n=lis[i].querySelector('.boss-name'), c=lis[i].querySelector('.company-name');
    names.push(((n?n.textContent:'')+'|'+(c?c.textContent:'')).replace(/\s+/g,' ').trim());
  }
  return JSON.stringify({value:inp?inp.value:null, rows:lis.length, names:names});
})()
"""

SIDEBAR_NAMES_JS = r"""
return JSON.stringify([].slice.call(
  document.querySelectorAll('.friend-content .name-text')).slice(0,6)
  .map(function(e){return e.textContent.trim();}));
"""


def read_state(inst):
    """这些脚本是 IIFE 表达式，必须 as_expr=True；漏了会拿到 None，
    然后被 `or "[]"` 兜成"浮层一行都没有"——那是 harness 的假象不是页面的事实。"""
    return json.loads(inst.run_js(READ_JS, as_expr=True) or "{}")


def run_case(inst, name, mode, wait=8.0):
    inst.run_js(CLEAR_JS, as_expr=True)
    time.sleep(1.0)
    inst.run_js(TYPE_JS % {"mode": json.dumps(mode), "text": json.dumps(name)},
                as_expr=True)
    t0 = time.time()
    while time.time() - t0 < wait:
        time.sleep(1.0)
        d = read_state(inst)
        if d.get("rows"):
            return round(time.time() - t0, 1), d
    return None, read_state(inst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=9433)
    ap.add_argument("--names", nargs="*", default=["宋女士", "李女士"])
    a = ap.parse_args()

    cfg = UnifiedConfig()
    accounts = cfg.greet.accounts or []
    fname = "zhipin_cookies.json"
    if a.account < len(accounts):
        fname = accounts[a.account].cookie_file or fname
    ck = Path(str(resolve_path(fname)))
    sha = hashlib.sha256(ck.read_bytes()).hexdigest()[:16]

    inst = launch_browser(headless=True, port=a.port,
                          user_data_dir=tempfile.mkdtemp(prefix="boss_trig_"))
    try:
        if not inst.load_cookies(str(ck)):
            print("Cookie 加载失败")
            return 1
        inst.get(CHAT_URL)
        time.sleep(8)
        print("侧栏前几个名字:", json.loads(inst.run_js(SIDEBAR_NAMES_JS) or "[]"))
        targets = list(a.names)
        for n in json.loads(inst.run_js(SIDEBAR_NAMES_JS) or "[]")[:1]:
            if n not in targets:
                targets.append(n)
        for name in targets:
            print("=" * 72)
            print(f"名字: {name}")
            for mode in KINDS:
                dt, d = run_case(inst, name, mode)
                状态 = f"{dt}s 出 {d['rows']} 行" if dt else "超时没出结果"
                print(f"  [{mode:8}] {状态:20} 框里={d['value']!r} "
                      f"前几行={d['names'][:4]}")
            inst.run_js(CLEAR_JS, as_expr=True)
    finally:
        try:
            inst.quit()
        except Exception:
            pass
    print("Cookie 未改动:", sha == hashlib.sha256(ck.read_bytes()).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
