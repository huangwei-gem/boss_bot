# -*- coding: utf-8 -*-
"""只读取证第二轮：整个聊天页里还有没有"置顶"这件事的 DOM 痕迹。

要看的是：①有没有任何元素文本含"置顶"；②有没有任何类名含 top/stick/pin；
③悬停那一行的操作区（icon-operate）里挂着什么。有了这些才能判断
"friend-top 判据失效"还是"他真的没有置顶了"。
只开页面读 DOM，不点任何会话。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage
from boss_bot.platform_compat import 破解版路径

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = 破解版路径(BASE)

JS = '''(function(){
  var 出={}, i, el;
  // ① 全文里出现"置顶"的元素
  var 置顶元素=[];
  document.querySelectorAll("*").forEach(function(x){
    var t=(x.childNodes&&x.childNodes.length&&x.childNodes[0].nodeType===3)
          ? x.textContent.trim() : "";
    if(t && t.indexOf("置顶")>=0 && t.length<=12) 置顶元素.push({tag:x.tagName, cls:String(x.className||"").slice(0,60), txt:t});
  });
  // ② 类名里带 top/stick/pin 的元素（限定在侧栏容器内，避免全站噪音）
  var 侧栏=document.querySelector(".friend-content")?document.querySelector(".friend-content").closest("div"):null;
  var 根=侧栏||document;
  var 类名命中=[];
  根.querySelectorAll("*").forEach(function(x){
    var c=String(x.className||"");
    if(/top|stick|pin|flag|treat/i.test(c) && c.length<70) 类名命中.push(c);
  });
  // ③ 第一行的完整 outerHTML 前 600 字（看结构）
  var 第一行=document.querySelector(".friend-content");
  return JSON.stringify({
    url: location.href,
    置顶元素: 置顶元素.slice(0,12),
    类名命中: Array.from(new Set(类名命中)).slice(0,12),
    侧栏行总数: document.querySelectorAll(".friend-content").length,
    第一行HTML: 第一行? 第一行.outerHTML.replace(/\\s+/g," ").slice(0,900): ""
  });
})()'''


def main(账号):
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(9421)
    co.set_argument("--user-data-dir=%s" % os.path.join(BASE, "browser_data", "account_%d" % 账号))
    co.set_argument("--disable-blink-features=AutomationControlled")
    page = ChromiumPage(co)
    page.get("https://www.zhipin.com/web/geek/chat")
    time.sleep(6)
    d = json.loads(page.run_js(JS, as_expr=True) or "{}")
    print("账号%d  URL=%s  侧栏渲染 %s 行" % (账号, d.get("url", "")[:60], d.get("侧栏行总数")))
    print("\n① 文本含「置顶」的元素 %d 个：" % len(d.get("置顶元素") or []))
    for x in (d.get("置顶元素") or [])[:8]:
        print("   ", x)
    print("\n② 侧栏内类名含 top/stick/pin 的：%s" % (d.get("类名命中") or []))
    print("\n③ 第一行 outerHTML：\n   %s" % (d.get("第一行HTML") or "")[:900])
    page.quit()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
