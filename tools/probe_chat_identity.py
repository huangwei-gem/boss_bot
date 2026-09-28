# -*- coding: utf-8 -*-
"""会话身份判据实测（只读）：行上的 (姓名, 公司) 能不能唯一定位一段对话"""
import importlib.util
import json
import time
import collections

spec = importlib.util.spec_from_file_location("cbc", "tools/compare_boss_chat.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

JS = r'''(function(){
  var out=[];var items=document.querySelectorAll(".friend-content");
  for(var i=0;i<items.length;i++){
    var it=items[i];
    var q=function(s){var e=it.querySelector(s);return e?(e.textContent||"").trim():"";};
    // .name-box 里依次是 姓名 / 公司 / 头衔，姓名带 class，另两个是裸 span
    var box=it.querySelector(".name-box");
    var spans=[];
    if(box) for(var k=0;k<box.children.length;k++){
      var c=box.children[k];
      if(c.tagName==="SPAN"){var t=(c.textContent||"").trim(); if(t) spans.push(t);}
    }
    var img=it.querySelector("img");
    out.push({i:i, name:q(".name-text"), parts:spans,
              company:spans.length>1?spans[1]:"", title:spans.length>2?spans[2]:"",
              av:((img?img.getAttribute("src"):"")||"").split("/").pop().slice(0,40)});
  }
  return JSON.stringify(out);
})()'''

JS_HEAD = r'''(function(){
  var q=function(s){var e=document.querySelector(s);return e?(e.textContent||"").trim():"";};
  return JSON.stringify({name:q(".top-info-content .name-text"),
                         job:q(".chat-position-content .position-content"),
                         company:q(".chat-position-content .company-name")});
})()'''

page = m.launch(0)
page.get("https://www.zhipin.com/web/geek/chat")
time.sleep(8)
rows = json.loads(page.run_js(JS, as_expr=True) or "[]")
print("侧栏 %d 行" % len(rows))
for r in rows[:6]:
    print("  #%d %s | 公司=%s | 头衔=%s" % (r["i"], r["name"], r["company"], r["title"]))

pairs = [(r["name"], r["company"]) for r in rows]
dup = {k: v for k, v in collections.Counter(pairs).items() if v > 1}
print("\n判据 A (姓名,公司)：唯一 %d/%d，重复 %d 组 %s"
      % (len(set(pairs)), len(pairs), len(dup), dup or ""))

names = collections.Counter(r["name"] for r in rows)
dupn = {k: v for k, v in names.items() if v > 1}
print("只用姓名：唯一 %d/%d，重复 %d 组 %s" % (len(names), len(rows), len(dupn), dupn))
print("公司字段为空的行数：%d" % sum(1 for r in rows if not r["company"]))
av = collections.Counter(r["av"] for r in rows)
print("头像段唯一：%d/%d；同头像不同公司的组：%s"
      % (len(av), len(rows),
         [k for k, v in collections.Counter(
             [(r["av"], r["company"]) for r in rows]).items()
          if v > 1]))

print("\n重名昵称逐行核对（点开后头部是谁）")
for name in sorted(dupn):
    idxs = [r["i"] for r in rows if r["name"] == name]
    for i in idxs:
        r = rows[i]
        page.run_js('(function(){var f=document.querySelectorAll(".friend-content");'
                    'if(f[%d])f[%d].click();})()' % (i, i), as_expr=True)
        time.sleep(2.6)
        head = json.loads(page.run_js(JS_HEAD, as_expr=True) or "{}")
        sel = json.loads(page.run_js(
            '(function(){var o=[];var f=document.querySelectorAll(".friend-content");'
            'for(var i=0;i<f.length;i++){if((f[i].className||"").indexOf("selected")>=0)o.push(i);}'
            'return JSON.stringify(o);})()', as_expr=True) or "[]")
        same = (head.get("name", "") == r["name"])
        print("  #%d %s|%s → selected=%s 头部=%s|%s %s"
              % (i, r["name"], r["company"], sel, head.get("name", ""),
                 head.get("company", "") or head.get("job", "")[:22],
                 "✓" if same and sel == [i] else "✗"))
page.quit()
