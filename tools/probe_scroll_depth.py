# -*- coding: utf-8 -*-
"""侧栏滚到位到底要几步：逐步打 scrollTop/scrollHeight、渲染行数、目标行出现在第几步。

为什么要它：`_scroll_to_chat_row` 一步滚 700px、上限 30 步（=21000px），
而刚实测账号0 侧栏有 388 行。要是列表总高比 21000 大，那"滚到底没这个人"
这句话根本就是假的——人排在滚不到的那半截里。
这里把每一步的真实位置打出来，用步数×行高把这条判死。

只开临时端口 + 临时 profile + Cookie，不碰线上面板；全程只滚只读，不点会话。
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.page_handler import BossChatHandler  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402
from boss_bot.unified_config import 新建临时目录  # noqa: E402

CHAT_URL = "https://www.zhipin.com/web/geek/chat"

STEP_JS = BossChatHandler._SIDEBAR_SCROLL_JS
RESET_JS = BossChatHandler._SIDEBAR_RESET_JS
ROWS_JS = r"""
(function(){
  var out=[];
  [].slice.call(document.querySelectorAll('.friend-content')).forEach(function(el){
    var n=el.querySelector('.name-text');
    var box=el.querySelector('.name-box'), spans=[];
    if(box) for(var k=0;k<box.children.length;k++){
      var c=box.children[k];
      if(c.tagName==='SPAN'){var t=(c.textContent||'').trim(); if(t) spans.push(t);}
    }
    out.push((n?n.textContent.trim():'?')+'@@'+(spans.length>1?spans[1]:''));
  });
  return JSON.stringify(out);
})()
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", type=int, default=0)
    ap.add_argument("--port", type=int, default=9435)
    ap.add_argument("--targets", nargs="*",
                    default=["宋女士@@", "李女士@重庆劳多拉", "苏女士@厦门中洲联创"])
    ap.add_argument("--steps", type=int, default=80)
    a = ap.parse_args()

    cfg = UnifiedConfig()
    accounts = cfg.greet.accounts or []
    fname = "zhipin_cookies.json"
    if a.account < len(accounts):
        fname = accounts[a.account].cookie_file or fname
    ck = Path(str(resolve_path(fname)))
    sha = hashlib.sha256(ck.read_bytes()).hexdigest()[:16]

    inst = launch_browser(headless=True, port=a.port,
                          user_data_dir=str(新建临时目录("boss_scroll_")))
    try:
        if not inst.load_cookies(str(ck)):
            print("Cookie 加载失败")
            return 1
        inst.get(CHAT_URL)
        time.sleep(8)
        inst.run_js(RESET_JS, as_expr=True)
        time.sleep(0.6)
        found_at = {}
        seen = set()
        prev = None
        for step in range(a.steps):
            pos = str(inst.run_js(STEP_JS, as_expr=True))
            time.sleep(0.55)
            rows = json.loads(inst.run_js(ROWS_JS, as_expr=True) or "[]")
            seen.update(rows)
            for t in a.targets:
                if t in found_at:
                    continue
                for r in rows:
                    if r.startswith(t):
                        found_at[t] = (step, r.replace("@@", "|"))
                        break
            top, total = (pos.split("/") + ["?"])[:2]
            if step % 5 == 0 or pos == prev:
                print(f"第{step+1:3}步 pos={pos:14} 屏上{len(rows):3}行 累计{len(seen):3}行")
            if not BossChatHandler._SIDEBAR_POS_RE.match(pos):
                print(f"  第{step+1}步读不出位置（{pos}）→ 线上代码在这里就停了")
                break
            if pos == prev:
                print(f"  第{step+1}步位置不动了 = 到底，累计 {len(seen)} 行")
                break
            prev = pos
        print("=" * 60)
        for t in a.targets:
            if t in found_at:
                print(f"  {found_at[t][1]} 出现在第 {found_at[t][0]+1} 步")
            else:
                print(f"  {t} 从头滚到尾都没出现")
        print(f"30 步上限能覆盖的滚动距离 = 700×30 = 21000px；"
              f"实测列表总高 {prev}")
    finally:
        try:
            inst.quit()
        except Exception:
            pass
    print("Cookie 未改动:", sha == hashlib.sha256(ck.read_bytes()).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
