# -*- coding: utf-8 -*-
"""把联系人搜索浮层里某个名字的每一行原样捞出来，看能用来认人的字段有哪些。

只用临时端口 + 临时 profile + Cookie 文件，不碰线上 9222/9223；全程只读 DOM
（写字进搜索框是机器人本来每轮都在做的动作），不点发送、不点打招呼。

用法：
    python tools/probe_search_rows.py --name 宋女士 --company 长沙南舒文化传媒
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
from boss_bot.page_handler import BossChatHandler  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402

CHAT_URL = "https://www.zhipin.com/web/geek/chat"

DUMP = r"""
(function(){
  var out=[];
  [].slice.call(document.querySelectorAll('li.search-list')).forEach(function(li){
    var q=function(s){var e=li.querySelector(s);
        return e?(e.textContent||'').replace(/\s+/g,' ').trim():'';};
    var box=li.querySelector('.company-name'), par=box?box.parentElement:null;
    out.push({
      name:q('.boss-name'),
      company:q('.company-name'),
      gray:q('.gray'),
      first:q('.first-line'),
      sec:q('.sec-line'),
      boxSpans:(function(){var b=li.querySelector('.name-box')||li;
        return [].slice.call(b.querySelectorAll('span')).slice(0,8).map(function(s){
          return (s.textContent||'').replace(/\s+/g,' ').trim();}).filter(function(t){return t;});})(),
      html:(li.innerHTML||'').replace(/\s+/g,' ').slice(0,300),
      text:(li.textContent||'').replace(/\s+/g,' ').trim().slice(0,160)
    });
  });
  return JSON.stringify(out);
})()
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=9432)
    ap.add_argument("--name", default="宋女士")
    ap.add_argument("--company", default="")
    a = ap.parse_args()

    cfg = UnifiedConfig()
    accounts = cfg.greet.accounts or []
    fname = "zhipin_cookies.json"
    if a.account < len(accounts):
        fname = accounts[a.account].cookie_file or fname
    ck = Path(str(resolve_path(fname)))
    sha = hashlib.sha256(ck.read_bytes()).hexdigest()[:16]

    inst = launch_browser(headless=True, port=a.port,
                          user_data_dir=tempfile.mkdtemp(prefix="boss_rows_"))
    try:
        if not inst.load_cookies(str(ck)):
            print("Cookie 加载失败")
            return 1
        inst.get(CHAT_URL)
        time.sleep(7)
        ph = BossChatHandler.__new__(BossChatHandler)
        ph.page = inst
        print("侧栏渲染行数:", inst.run_js(
            "return document.querySelectorAll('.friend-content').length;"))
        wrote = ph._set_contact_filter(a.name)
        print(f"写入 {a.name!r} -> {wrote}")
        got = []
        # 每秒打一行：光看"有没有行"会被上一轮残留的浮层骗过去（实测框里写
        # 宋女士、行里还是上一个人的高女士），必须把姓名和框里的值一起打出来。
        for i in range(12):
            time.sleep(1.0)
            got = json.loads(inst.run_js(DUMP, as_expr=True) or "[]")
            cur = inst.run_js(
                "var i=document.querySelector('input.boss-search-input');"
                "return i?i.value:null;")
            print(f"  第{i+1:2}秒 框里={cur!r} 行数={len(got)} "
                  f"名字={[ (r['name'] or '').strip() for r in got ][:6]}")
            if got and all((r["name"] or "").strip() == a.name for r in got):
                break
        if not got:
            print("浮层一行都没有（机制变了或这个名搜不出东西）")
        for r in got:
            print("-" * 70)
            print(json.dumps({k: v for k, v in r.items() if k != "html"},
                             ensure_ascii=False))
            print("  html:", r["html"][:220])
        # 侧栏里这个人到底在不在（滚一遍）
        if a.company:
            print("侧栏里能否找到该 姓名+公司:",
                  ph._sidebar_has_row(a.name, a.company),
                  "滚动定位:", ph._scroll_to_chat_row(a.name, a.company))
        ph._clear_contact_filter()
    finally:
        try:
            inst.quit()
        except Exception:
            pass
    print("Cookie 未改动:", sha == hashlib.sha256(ck.read_bytes()).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
