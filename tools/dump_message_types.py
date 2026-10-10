"""抓取 BOSS 直聘右侧消息面板的完整结构，为「回复记录 1:1 复刻」定选择器。

只读：点开没有未读角标的会话（不会把未读改成已读），把每种结构的消息项
outerHTML 分类保存，并打印一份紧凑的分类清单。

运行：python tools/dump_message_types.py
输出：tools/message_types.json
"""

import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PORT = 9364
PROFILE = BASE / "browser_data" / "account_0"

# 逐条消息：类名、方向、内部结构指纹、纯文本、时间、送达/已读、媒体线索
JS_MESSAGES = r"""
function txt(el){ return el ? (el.innerText || '').replace(/\s+/g,' ').trim() : ''; }
var out = [];
var items = document.querySelectorAll('.chat-record li.message-item, .chat-record .message-item');
for (var i = 0; i < items.length; i++) {
  var it = items[i];
  var cls = (it.className || '').toString();
  var kids = [];
  for (var j = 0; j < it.children.length; j++) {
    var k = it.children[j];
    kids.push(k.tagName.toLowerCase() + (k.className ? '.' + k.className.toString().trim().split(/\s+/).slice(0,3).join('.') : ''));
  }
  var inner = [];
  it.querySelectorAll('*').forEach(function(e){
    var c = (e.className || '').toString().trim();
    if (c) inner.push(e.tagName.toLowerCase() + '.' + c.split(/\s+/)[0]);
  });
  out.push({
    index: i,
    cls: cls,
    child_tags: kids,
    inner_classes: Array.from(new Set(inner)).slice(0, 25),
    text: txt(it).slice(0, 220),
    has_img: it.querySelectorAll('img').length,
    has_a: it.querySelectorAll('a[href]').length,
    html: it.outerHTML.replace(/ data-v-[0-9a-f]+=""/g, '').slice(0, 1500)
  });
}
return JSON.stringify({url: location.href, count: items.length, items: out});
"""

# 面板级结构：消息容器、时间分隔条、系统条、输入框、顶部信息
JS_PANEL = r"""
function txt(el){ return el ? (el.innerText || '').replace(/\s+/g,' ').trim() : ''; }
var res = {};
res.container_candidates = {};
['.chat-record','.chat-record .chat-message','.chat-record ul.im-list','.message-tip-bar'].forEach(function(s){
  var n = document.querySelectorAll(s).length; if (n) res.container_candidates[s] = n;
});
// 顶层：找出包含最多 .message-item 的祖先作为消息容器
var mi = document.querySelector('.message-item');
var chain = [];
var cur = mi;
while (cur && chain.length < 8) {
  chain.push(cur.tagName.toLowerCase() + (cur.className ? '.' + cur.className.toString().trim().split(/\s+/).slice(0,3).join('.') : ''));
  cur = cur.parentElement;
}
res.item_ancestor_chain = chain;
// 居中条（时间分隔 / 系统提示）
res.center_bars = [];
document.querySelectorAll('.chat-content, .history-list, .message-list, .chat-message-list').forEach(function(box){
  Array.prototype.forEach.call(box.children, function(c){
    var t = txt(c);
    if (t && t.length < 40 && !c.querySelector('.message-item')) {
      res.center_bars.push({cls: (c.className||'').toString().slice(0,60), text: t.slice(0,60)});
    }
  });
});
// 顶部：对方姓名/职位、岗位名
res.header = {
  name: txt(document.querySelector('.chat-head .name, .boss-name, .name-box, .user-name')),
  job: txt(document.querySelector('.chat-head .job, .job-name, .source-job, .position-name')),
};
res.input_area = !!document.querySelector('.input-area, .chat-input, .send-message');
return JSON.stringify(res);
"""


def main() -> int:
    from DrissionPage import ChromiumPage, ChromiumOptions
    from boss_bot.unified_config import resolve_path
    from boss_bot.browser_launcher import BrowserInstance
    from boss_bot.platform_compat import 破解版路径

    co = ChromiumOptions()
    co.set_paths(browser_path=破解版路径(str(BASE)),
                 user_data_path=str(PROFILE))
    co.set_local_port(PORT)
    p = ChromiumPage(co)
    inst = BrowserInstance(chrome_page=p)
    cookie = resolve_path(json.load(open(BASE / "bot_config.json", encoding="utf-8"))
                          ["login"]["cookie_file"])
    if cookie.exists():
        inst.load_cookies(str(cookie))

    p.get("https://www.zhipin.com/web/geek/chat")
    time.sleep(6)
    if "chat" not in (p.url or ""):
        print("未登录，终止")
        return 1

    picked = p.run_js(r"""
        var items = document.querySelectorAll('li[role="listitem"]');
        for (var i = 0; i < items.length; i++) {
            if (!items[i].querySelector('.notice-badge')) { items[i].click(); return 'index ' + i; }
        }
        return 'none';
    """)
    print("打开会话:", picked)
    time.sleep(5)

    panel = json.loads(p.run_js(f"return (function(){{{JS_PANEL}}})()"))
    msgs = json.loads(p.run_js(f"return (function(){{{JS_MESSAGES}}})()"))

    # 按「内部类名指纹」归类，每种只保留一个样本
    groups = {}
    for m in msgs["items"]:
        key = "|".join(m["inner_classes"][:6]) or m["cls"]
        groups.setdefault(key, []).append(m)

    summary = {
        "url": msgs["url"],
        "message_count": msgs["count"],
        "panel": panel,
        "distinct_structures": len(groups),
        "groups": [
            {
                "n": len(v),
                "cls": v[0]["cls"][:80],
                "inner_classes": v[0]["inner_classes"],
                "has_img": max(x["has_img"] for x in v),
                "has_a": max(x["has_a"] for x in v),
                "sample_text": v[0]["text"][:120],
                "sample_html": v[0]["html"],
            }
            for v in sorted(groups.values(), key=lambda x: -len(x))
        ],
    }
    out = BASE / "tools" / "message_types.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"消息 {msgs['count']} 条，结构种类 {len(groups)} 种 → {out.name}")
    print("容器候选:", panel["container_candidates"])
    print("祖先链:", " < ".join(panel["item_ancestor_chain"]))
    print("顶部:", panel["header"], "输入框:", panel["input_area"])
    print("居中条:", panel["center_bars"][:6])
    print("--- 结构分组 ---")
    for g in summary["groups"]:
        print(f"  x{g['n']:<3} cls={g['cls'][:44]!r} img={g['has_img']} a={g['has_a']} "
              f"text={g['sample_text'][:44]!r}")
        print(f"        inner={g['inner_classes'][:8]}")
    p.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
