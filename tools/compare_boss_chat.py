# -*- coding: utf-8 -*-
"""逐条比对：BOSS 聊天页真实显示 vs 我们存的回复记录。

只读：打开会话、读 DOM、读本地存储，绝不点发送/打招呼/发简历。

用法：
    python -X utf8 tools/compare_boss_chat.py                # 前 3 个会话
    python -X utf8 tools/compare_boss_chat.py --chat 肖蒙 --account 1 --verbose
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = os.path.join(BASE, "cloakbrowser", "chrome.exe")

JS_CONVS = '''(function(){
  var out=[];
  var items=document.querySelectorAll(".friend-content");
  for(var i=0;i<items.length;i++){
    var n=items[i].querySelector(".name-text");
    if(n) out.push(n.textContent.trim());
  }
  return JSON.stringify(out);
})()'''

# 每条消息把 DOM 里能看到的一切都吐出来：mid、左右方向、时间标签、正文、整块文本
JS_MESSAGES = '''(function(){
  var items=document.querySelectorAll(".message-item");
  var out=[];
  for(var i=0;i<items.length;i++){
    var it=items[i];
    var q=function(sel){var e=it.querySelector(sel);return e?e.textContent.trim():"";};
    var cls=it.className||"";
    out.push({
      dom_index:i,
      mid: it.getAttribute("data-mid")||"",
      dir: cls.indexOf("item-friend")>=0?"hr":(cls.indexOf("item-myself")>=0?"me":
           (cls.indexOf("item-system")>=0?"system":"other")),
      time: q(".item-time .time") || q(".time"),
      text: q(".text-content"),
      block: (it.innerText||"").replace(/\\s+/g," ").trim().slice(0,120),
      cls: cls.slice(0,80)
    });
  }
  return JSON.stringify(out);
})()'''


def launch(account_index):
    assert os.path.exists(CLOAK), "必须用项目自带破解版浏览器（BOSS 有反爬）"
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(9407)
    co.set_argument("--user-data-dir=%s" % os.path.join(
        BASE, "browser_data", "account_%d" % account_index))
    co.set_argument("--disable-blink-features=AutomationControlled")
    return ChromiumPage(co)


def open_chat(page, name):
    page.run_js('''(function(){var n=%s;var f=document.querySelectorAll(".friend-content");
      for(var i=0;i<f.length;i++){var e=f[i].querySelector(".name-text");
        if(e&&e.textContent.trim()===n){f[i].click();return true;}}return false;})()'''
                % json.dumps(name, ensure_ascii=False), as_expr=True)
    time.sleep(2.5)


JS_LOAD_HISTORY = '''(function(){
  var c=document.querySelector(".chat-content")||document.querySelector(".message-list")
        ||document.querySelector(".chat-message-wrap")||document.querySelector(".message-wrap");
  if(!c) return JSON.stringify({ok:false,reason:"no_container"});
  c.scrollTop=0;
  return JSON.stringify({ok:true, count:document.querySelectorAll(".message-item").length});
})()'''


def read_dom(page, rounds=8):
    """滚到顶部把历史加载出来，再按页面顺序读取（顺序键就是 DOM 顺序 + data-mid）"""
    prev = -1
    for _ in range(rounds):
        page.run_js(JS_LOAD_HISTORY, as_expr=True)
        time.sleep(0.9)
        cur = len(json.loads(page.run_js(JS_MESSAGES, as_expr=True) or "[]"))
        if cur == prev:
            break
        prev = cur
    return json.loads(page.run_js(JS_MESSAGES, as_expr=True) or "[]")


def norm(msgs):
    """把存储里的消息归一化成比较用的形态（正文/方向/时间标签）"""
    out = []
    for m in msgs:
        text = (m.get("text") or m.get("content") or "").strip()
        t = (m.get("time") or "").strip()
        mine = m.get("is_mine")
        if mine is None:
            mine = m.get("sender") == "me" or m.get("sender") == "bot"
        out.append({"text": text, "time": t, "dir": "me" if mine else "hr",
                    "mid": str(m.get("mid") or "")})
    return out


def compare(name, dom, stored, verbose=False):
    print("\n" + "=" * 78)
    print(f"会话：{name}    BOSS 显示 {len(dom)} 条    本地存 {len(stored)} 条")
    dtexts = [d["text"] for d in dom]
    stexts = [s["text"] for s in stored]
    missing = [t for t in dtexts if t and t not in stexts]
    extra = [t for t in stexts if t and t not in dtexts]
    blanks = [i for i, s in enumerate(stored) if not s["text"]]
    print(f"  线上有而本地没有：{len(missing)}    本地有而线上没有：{len(extra)}"
          f"    本地空正文：{len(blanks)}")

    # mid 是否随页面往下单调（决定能不能拿它当稳定顺序键）
    mids = [d["mid"] for d in dom if d["mid"]]
    mono = all(str(mids[i]).isdigit() and str(mids[i + 1]).isdigit() and int(mids[i]) <= int(mids[i + 1])
               for i in range(len(mids) - 1)) if mids else None
    print(f"  data-mid：{len(mids)}/{len(dom)} 条有值，自上而下{'单调递增' if mono else '不单调/不可用'}"
          + (f"（例 {mids[:2]}）" if mids else ""))

    # 时间标签覆盖率
    labelled = sum(1 for d in dom if d["time"])
    print(f"  线上带时间标签：{labelled}/{len(dom)}（BOSS 只在分段处显示时间）")
    rel = {}
    for d in dom:
        if d["time"]:
            key = ("昨天/前天" if d["time"].startswith(("昨天", "前天")) else
                   "MM-DD" if "-" in d["time"] else "纯时分")
            rel[key] = rel.get(key, 0) + 1
    if rel:
        print(f"  线上时间格式分布：{rel}")

    if verbose:
        print("\n  序号  线上方向 线上时间      线上正文 / 本地正文")
        n = max(len(dom), len(stored))
        for i in range(n):
            d = dom[i] if i < len(dom) else {}
            s = stored[i] if i < len(stored) else {}
            same = "✓" if (d.get("text") or "") == (s.get("text") or "") else "✗"
            print(f"  {i:>3}  {d.get('dir','-'):<6} {d.get('time','-'):<10} "
                  f"{same} {str(d.get('text',''))[:34]!r} | {str(s.get('text',''))[:34]!r}")
    else:
        # 只打印前 6 处不一致，够定位
        shown = 0
        for i in range(max(len(dom), len(stored))):
            d = dom[i] if i < len(dom) else {}
            s = stored[i] if i < len(stored) else {}
            if (d.get("text") or "") != (s.get("text") or "") or \
               (d.get("dir") or "") != (s.get("dir") or ""):
                print(f"  第{i}条不一致：线上[{d.get('dir')} {d.get('time')}] "
                      f"{str(d.get('text',''))[:40]!r} ↔ 本地[{s.get('dir')} {s.get('time')}] "
                      f"{str(s.get('text',''))[:40]!r}")
                shown += 1
                if shown >= 6:
                    print("  …（加 --verbose 看全部）")
                    break
    return {"dom": len(dom), "stored": len(stored), "missing": len(missing),
            "extra": len(extra), "blanks": len(blanks), "mid_mono": mono}


JS_PROBE = '''(function(){
  var out=[];
  var items=document.querySelectorAll(".friend-content");
  for(var i=0;i<items.length && i<40;i++){
    var it=items[i];
    var attrs={};
    var n=it.querySelector(".name-text");
    // 往上找带 data-* 的容器，BOSS 的会话 id 常挂在 li/div 上
    var node=it, hops=0;
    while(node && hops<4){
      for(var a=0;a<node.attributes.length;a++){
        var at=node.attributes[a];
        if(at.name.indexOf("data-")===0 || at.name==="id") attrs[at.name]=at.value;
      }
      node=node.parentElement; hops++;
    }
    out.push({index:i, name:n?n.textContent.trim():"", attrs:attrs});
  }
  return JSON.stringify(out);
})()'''


JS_ROWS = '''(function(){
  var out=[];
  var items=document.querySelectorAll(".friend-content");
  for(var i=0;i<items.length;i++){
    var it=items[i];
    var q=function(s){var e=it.querySelector(s);return e?(e.textContent||"").trim():"";};
    // 把行本身和往上 3 层的属性全部打出来：BOSS 的会话主键常常挂在 li/div 上
    var attrs={}, node=it, hops=0;
    while(node && hops<3){
      var tag=node.tagName.toLowerCase();
      for(var a=0;a<node.attributes.length;a++){
        var at=node.attributes[a];
        if(at.name==="style"||at.name.indexOf("data-v-")===0) continue;
        attrs[tag+"@"+at.name]=at.value;
      }
      node=node.parentElement; hops++;
    }
    out.push({i:i, name:q(".name-text"), last:q(".last-msg-text"),
              attrs:attrs, outer:(it.outerHTML||"").replace(/\\s+/g," ").slice(0,260)});
  }
  return JSON.stringify(out);
})()'''


JS_ROW_FULL = '''(function(){
  var out=[];
  var items=document.querySelectorAll(".friend-content");
  for(var i=0;i<items.length;i++){
    var it=items[i];
    var img=it.querySelector("img");
    var src=img?(img.getAttribute("src")||""):"";
    out.push({
      i:i,
      sel: (it.className||"").indexOf("selected")>=0,
      name:(it.querySelector(".name-text")||{}).textContent||"",
      text:(it.innerText||"").replace(/\\s+/g," ").trim(),
      // 头像文件名段就是 HR 的唯一标识，行上没有职位字段时靠它认人
      av:(src.split("/").pop()||"").slice(0,24)
    });
  }
  return JSON.stringify(out);
})()'''

JS_HEADER_FULL = '''(function(){
  var q=function(s){var e=document.querySelector(s);return e?(e.textContent||"").trim():"";};
  var img=document.querySelector(".chat-top-portrait img, .friend-info img, .chat-detail img");
  return JSON.stringify({
    name: q(".top-info-content .name-text") || q(".chat-header .name-text"),
    job: q(".chat-position-content .position-content"),
    company: q(".chat-position-content .company-name"),
    av: ((img?img.getAttribute("src"):"")||"").split("/").pop().slice(0,24),
    // 整块头部文本：行上取不到的岗位信息在这里
    head:(document.querySelector(".chat-top-wrap")||document.querySelector(".chat-header")
          ||{innerText:""}).innerText.replace(/\\s+/g," ").trim().slice(0,120)
  });
})()'''


def bind_probe(page, picks):
    """点一行 → 到底选中了谁：用 selected 行 + 头部信息互相印证，不靠索引稳定这种假设"""
    rows = json.loads(page.run_js(JS_ROW_FULL, as_expr=True) or "[]")
    by_name = {}
    for r in rows:
        by_name.setdefault(r["name"].strip(), []).append(r["i"])
    dups = {k: v for k, v in by_name.items() if len(v) > 1}
    print(f"\n[bind] 侧栏 {len(rows)} 行；重名昵称 {len(dups)} 组：{dups}")

    targets = picks or [i for v in dups.values() for i in v][:4]
    print(f"  行上能取到的文本（决定身份能不能只靠侧栏）：")
    for i in targets:
        print(f"    #{i} {rows[i]['text'][:60]!r} 头像={rows[i]['av']!r}")

    print("\n  点击 → selected 是否落在同一行 → 头部是谁")
    results = []
    for i in targets:
        page.run_js('(function(){var f=document.querySelectorAll(".friend-content");'
                    'if(f[%d])f[%d].click();})()' % (i, i), as_expr=True)
        time.sleep(2.8)
        after = json.loads(page.run_js(JS_ROW_FULL, as_expr=True) or "[]")
        sel_idx = [j for j, r in enumerate(after) if r["sel"]]
        head = json.loads(page.run_js(JS_HEADER_FULL, as_expr=True) or "{}")
        clicked_name = rows[i]["name"].strip()
        ok_sel = sel_idx == [i]
        ok_name = (head.get("name", "").strip() == clicked_name)
        results.append({"i": i, "name": clicked_name, "sel": sel_idx,
                        "head": head, "ok_sel": ok_sel, "ok_name": ok_name})
        print(f"    点#{i} {clicked_name!r} → selected={sel_idx} "
              f"{'✓' if ok_sel else '✗ 漂移'} 头部={head.get('name','')!r}"
              f"/{head.get('job','')[:22]!r} 头像={head.get('av','')!r} "
              f"{'✓' if ok_name else '✗ 名字不符'}")
        print(f"       头部整块={head.get('head','')[:90]!r}")

    # 身份唯一性：重名昵称靠 (名字, 岗位) 够不够分开
    print("\n  同昵称不同行，点开后头部岗位是否不同：")
    for name, idxs in dups.items():
        seen = []
        for rec in results:
            if rec["name"] == name:
                seen.append((rec["i"], rec["head"].get("job", "")[:26],
                             rec["head"].get("company", "")[:14]))
        if seen:
            uniq = len({j for _, j, _ in seen}) == len(seen)
            print(f"    {name} ×{len(idxs)} 实测{len(seen)}次：{seen} "
                  f"{'→ 岗位可区分' if uniq else '→ 岗位相同/未测全，需再找判据'}")
    moved = sum(1 for r in results if not r["ok_sel"])
    print(f"\n  结论：{len(results)} 次点击里 selected 漂移 {moved} 次；"
          f"头部与行名字不符 {sum(1 for r in results if not r['ok_name'])} 次")
    return results


def rows_probe(page, limit=8):
    """找侧栏每行的稳定主键：昵称会重、索引会变，只有行上的 id 能认会话"""
    rows = json.loads(page.run_js(JS_ROWS, as_expr=True) or "[]")
    names = [r["name"] for r in rows]
    dup = {n: names.count(n) for n in set(names) if names.count(n) > 1}
    print(f"\n[rows] 侧栏 {len(rows)} 行，重名昵称 {len(dup)} 组：{dup or '无'}")
    keycount = {}
    for r in rows:
        for k in r["attrs"]:
            keycount[k] = keycount.get(k, 0) + 1
    print(f"  每行都带的属性：{[k for k, v in keycount.items() if v >= len(rows) and len(rows)]}")
    for r in rows[:limit]:
        print(f"  #{r['i']} {r['name']!r} last={r['last'][:22]!r}")
        print(f"     attrs={r['attrs']}")
    if dup:
        one = sorted(dup)[0]
        for r in rows:
            if r["name"] == one:
                print(f"  重名样本 #{r['i']} {one} attrs={r['attrs']}")
    return rows


def probe(page):
    """会话身份探查：昵称会不会重复、DOM 上有没有可当稳定 key 的 id"""
    rows = json.loads(page.run_js(JS_PROBE, as_expr=True) or "[]")
    names = [r["name"] for r in rows]
    dup = {n: names.count(n) for n in set(names) if names.count(n) > 1}
    print(f"\n[probe] 侧栏 {len(rows)} 项，重名昵称：{dup or '无'}")
    keys_seen = {}
    for r in rows[:6]:
        print(f"  #{r['index']} {r['name']!r} attrs={r['attrs']}")
        for k in r["attrs"]:
            keys_seen.setdefault(k, 0)
            keys_seen[k] += 1
    print(f"  出现过的属性：{keys_seen}")
    print(f"  当前 URL：{page.url}")


JS_ITEM_IDENTITY = '''(function(){
  var out=[];
  var items=document.querySelectorAll(".friend-content");
  for(var i=0;i<items.length;i++){
    var it=items[i];
    var q=function(s){var e=it.querySelector(s);return e?(e.textContent||"").trim():"";};
    var img=it.querySelector("img");
    var src=img?(img.getAttribute("src")||""):"";
    out.push({i:i, name:q(".name-text"), job:q(".position-name, .company-name, .push-info, .gray"),
              avatar:src.slice(-64), label:(src.match(/([0-9a-zA-Z_-]{8,})_?(st|md)?\\.jpg/)||[])[1]||""});
  }
  return JSON.stringify(out);
})()'''

JS_HEADER = '''(function(){
  var q=function(s){var e=document.querySelector(s);return e?(e.textContent||"").trim():"";};
  var img=document.querySelector(".chat-detail-wrap img, .boss-chatter img, .name-text img");
  return JSON.stringify({
    name: q(".top-info-content .name-text") || q(".chat-input-area .name-text"),
    job: q(".chat-position-content .position-content") || q(".position-name"),
    company: q(".chat-company-name") || q(".company-name"),
    avatar: (img?img.getAttribute("src"):"").slice(-64)
  });
})()'''


def probe_identity(page, names):
    """同名会话靠什么分人：头像文件名段 + 岗位 + 公司，加上点开会话时的真实请求"""
    rows = json.loads(page.run_js(JS_ITEM_IDENTITY, as_expr=True) or "[]")
    same = [r for r in rows if r["name"] in names]
    print(f"  侧栏里这些名字的条目 {len(same)} 个：")
    for r in same:
        print(f"    #{r['i']} {r['name']} job={r['job'][:24]!r} 头像段={r['label'][:24]!r}")

    for r in same:
        page.run_js('''(function(){var f=document.querySelectorAll(".friend-content");
          if(f[%d]) f[%d].click();})()''' % (r["i"], r["i"]), as_expr=True)
        time.sleep(2.5)
        head = json.loads(page.run_js(JS_HEADER, as_expr=True) or "{}")
        caps = page.run_js(
            'JSON.stringify((window.__cap||[]).slice(-6))', as_expr=True) or "[]"
        try:
            tail = [str(c)[:150] for c in json.loads(caps)]
        except Exception:
            tail = []
        print(f"\n    点开 #{r['i']} {r['name']}：头部={head}")
        for t in tail:
            print(f"      请求: {t}")
    return same


def install_request_hook(page):
    page.run_js('''(function(){var w=window.__cap=[];
      var o=XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open=function(m,u){w.push(String(u));return o.apply(this,arguments);};
      var f=window.fetch;
      window.fetch=function(a){try{w.push(String(a && a.url ? a.url : a));}catch(e){}
        return f.apply(this,arguments);};
      var X=XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.send=function(b){try{if(b)w.push("BODY::"+String(b).slice(0,240));}catch(e){}
        return X.apply(this,arguments);};
    })()''', as_expr=True)


def scroll_probe(page, name):
    """线上历史到底能不能加载全：决定"完全一致"是比窗口还是比全量"""
    rows = json.loads(page.run_js(JS_ITEM_IDENTITY, as_expr=True) or "[]")
    idxs = [r["i"] for r in rows if r["name"] == name]
    if not idxs:
        print(f"  侧栏没有 {name!r}")
        return

    def count():
        return int(page.run_js('document.querySelectorAll(".message-item").length',
                               as_expr=True) or 0)

    for i in idxs:
        page.run_js('(function(){var f=document.querySelectorAll(".friend-content");'
                    'if(f[%d])f[%d].click();})()' % (i, i), as_expr=True)
        time.sleep(2.5)
        before = count()
        page.run_js('''(function(){var c=document.querySelector(".chat-content")
            ||document.querySelector(".message-list")||document.querySelector(".chat-message-wrap");
            if(c)c.scrollTop=0;})()''', as_expr=True)
        time.sleep(1.8)
        after_top = count()
        page.run_js('''(function(){var c=document.querySelector(".chat-content")
            ||document.querySelector(".message-list")||document.querySelector(".chat-message-wrap")||document;
            for(var k=0;k<8;k++)c.dispatchEvent(new WheelEvent("wheel",{deltaY:-900,bubbles:true}));})()''',
                    as_expr=True)
        time.sleep(2.5)
        after_wheel = count()
        head = page.run_js('(document.querySelector(".chat-position-content .position-content")||{}).textContent||""',
                           as_expr=True) or ""
        boxes = json.loads(page.run_js('''JSON.stringify([".chat-content",".message-list",
            ".chat-message-wrap",".message-wrap"].map(function(s){var e=document.querySelector(s);
            return s+":"+(e?(e.scrollHeight+"/"+e.clientHeight):"none");}))''', as_expr=True) or "[]")
        print(f"  #{i} {name} 岗位={head.strip()[:24]!r}")
        print(f"     条数 初始{before} → scrollTop{after_top} → wheel{after_wheel}  容器={boxes}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--chat", default="", help="只比对这个会话名")
    ap.add_argument("--n", type=int, default=3, help="比对前 N 个会话")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--probe", action="store_true", help="只做身份/重名探查")
    ap.add_argument("--dup", default="", help="探查同名会话的真实主键")
    ap.add_argument("--scroll", default="", help="探查该会话能否加载出完整历史")
    ap.add_argument("--rows", action="store_true", help="探查侧栏每行的稳定主键")
    ap.add_argument("--bind", default=None, nargs="?", const="*",
                    help="探查 点击某行→selected→头部 是否指向同一会话（逗号分隔索引，不给值=所有重名行）")
    args = ap.parse_args()

    from boss_bot.message_store import MessageStore
    store = MessageStore(account_index=args.account)

    page = launch(args.account)
    try:
        page.get("https://www.zhipin.com/web/geek/chat")
        time.sleep(7)
        convs = json.loads(page.run_js(JS_CONVS, as_expr=True) or "[]")
        if not convs:
            print("!! 会话列表为空 —— 该账号浏览器可能没登录")
            return 1
        print(f"账号{args.account} 侧栏会话 {len(convs)} 个，"
              f"本地已存 {len(store.get_chat_list())} 个")
        if args.rows:
            rows_probe(page)
            return 0
        if args.bind is not None:
            picks = [] if args.bind == "*" else [int(x) for x in args.bind.split(",") if x.strip()]
            bind_probe(page, picks)
            return 0
        if args.probe:
            probe(page)
            return 0
        if args.scroll:
            print("\n[scroll] 历史加载能力")
            scroll_probe(page, args.scroll)
            return 0
        if args.dup:
            print("\n[dup] 同名会话身份探查（昵称重复时靠什么分人）")
            install_request_hook(page)
            probe_identity(page, [args.dup])
            return 0
        stored_names = [c.get("chat_name") for c in store.get_chat_list()]
        missing_chats = [c for c in convs[:20] if c not in stored_names]
        print(f"  侧栏前 20 个里本地没有记录的：{len(missing_chats)} 个 {missing_chats[:5]}")

        targets = [args.chat] if args.chat else convs[:args.n]
        summary = []
        for name in targets:
            if not name:
                continue
            open_chat(page, name)
            dom = read_dom(page)
            stored = norm(store.get_messages(name) or [])
            cards = [d for d in dom if not d["text"]]
            if cards:
                print(f"  线上无正文气泡 {len(cards)} 条，整块文本是：")
                for c in cards[:4]:
                    print(f"    [{c['dir']} {c['time'] or '-'}] cls={c['cls'][:34]} "
                          f"→ {c['block'][:70]!r}")
            bots = [s["text"] for s in stored if "简历已发送" in s["text"] or "PK" in s["text"]]
            if bots:
                print(f"  本地存了非 BOSS 气泡的条目 {len(bots)} 条：{bots[:3]}")
            summary.append((name, compare(name, dom, stored, args.verbose)))
        print("\n" + "=" * 78)
        for name, s in summary:
            print(f"  {name:<12} 线上{s['dom']:>3} 本地{s['stored']:>3} "
                  f"缺{s['missing']:>3} 多{s['extra']:>3} 空{s['blanks']:>3} "
                  f"mid单调={s['mid_mono']}")
    finally:
        try:
            page.quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
