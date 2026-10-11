# -*- coding: utf-8 -*-
"""只读取证：BOSS 侧栏每一行的真实 className，看"friend-top"这条置顶判据还成不成立。

只开页面、只读 DOM，不点任何会话（点开会把 HR 消息标成已读）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage
from boss_bot.platform_compat import 破解版路径

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = 破解版路径(BASE)

JS = '''(function(){
  var rows=document.querySelectorAll(".friend-content");
  var out=[];
  for(var i=0;i<rows.length && i<40;i++){
    var el=rows[i];
    var n=el.querySelector(".name-text");
    var box=el.querySelector(".name-box");
    out.push({
      i:i,
      cls:el.className||"",
      name:n?n.textContent.trim():"",
      full:(box?box.textContent.trim():"").replace(/\\s+/g," ").slice(0,40),
      inner_cls:(function(){var a=[];el.querySelectorAll("*").forEach(function(x){
        if(x.className && typeof x.className==="string"){
          var c=x.className.trim();
          if(c && c.length<48 && !/^(name|time|figure|msg|item|text|title|user|friend)/.test(c)) a.push(c);
        }});
        return Array.from(new Set(a)).slice(0,6);})()
    });
  }
  return JSON.stringify({total:rows.length, rows:out});
})()'''


def main(账号):
    assert os.path.exists(CLOAK), "必须用项目自带破解版浏览器"
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(9419)
    co.set_argument("--user-data-dir=%s" % os.path.join(BASE, "browser_data", "account_%d" % 账号))
    co.set_argument("--disable-blink-features=AutomationControlled")
    page = ChromiumPage(co)
    page.get("https://www.zhipin.com/web/geek/chat")
    import time
    time.sleep(6)
    数据 = json.loads(page.run_js(JS, as_expr=True) or "{}")
    行 = 数据.get("rows") or []
    print("账号%d 侧栏渲染 %d 行（本次取前 %d 行）" % (账号, 数据.get("total", 0), len(行)))
    类名集 = {}
    for r in 行:
        类名集[r["cls"]] = 类名集.get(r["cls"], 0) + 1
    print("\n出现过的 className 组合：")
    for k, v in sorted(类名集.items(), key=lambda x: -x[1]):
        print("  %2d 行  %s" % (v, k))
    带top = [r for r in 行 if "top" in (r["cls"] + json.dumps(r["inner_cls"], ensure_ascii=False)).lower()]
    print("\n类名里带 top 的行: %d" % len(带top))
    print("\n前 8 行明细：")
    for r in 行[:8]:
        print("  [%s] %s | %s | 子元素类名 %s" % (r["i"], r["name"], r["full"], r["inner_cls"]))
    page.quit()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
