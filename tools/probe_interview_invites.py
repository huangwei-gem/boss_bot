# -*- coding: utf-8 -*-
"""摸清 BOSS 的"现场面试邀请"卡片和它的接受/拒绝入口到底在哪个页面。

用户 2026-10-06 发了手机截图（面试日程：珞壹文化 今天16:30 线下面试 待接受）：
"面试不是所有的都接受的，像这种不符合我目标的直接拒绝就行了，他发面试邀请你直接拒绝"。

存档里这种卡片正文是「珞壹文化邀请您现场面试，前往查看，确认是否接受 立即查看」——
聊天页上只有一个"立即查看"，接受/拒绝不在这张卡上，所以要先搞清楚点进去是哪个页面、
按钮的 class 叫什么，才能写自动拒绝。

只读探针：默认只 dump 卡片结构和链接；加 --go 才点"立即查看"并 dump 落地页
（点进去只是导航，绝不点接受/拒绝）。

跑法：python tools/probe_interview_invites.py
      python tools/probe_interview_invites.py --go
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from decline_offline_interviews import attach  # noqa: E402

from boss_bot.message_store import MessageStore  # noqa: E402
from boss_bot.page_handler import BossChatHandler  # noqa: E402

# 面试邀请卡片：把标题、按钮文字、链接 href 全打出来
# 必须包成 IIFE：DrissionPage 的 run_js 拿裸语句体会报 Illegal return statement
CARD_JS = '''(function(){
function txt(e){return (e.textContent||'').replace(/\\s+/g,' ').trim();}
function vis(e){return e.getClientRects().length>0;}
var out=[];
var cards=document.querySelectorAll('.message-card-wrap,.chat-card,.message-card');
for(var i=0;i<cards.length;i++){
  var c=cards[i];
  if(!/面试/.test(txt(c))) continue;
  var btns=[],links=[];
  var bs=c.querySelectorAll('button,.card-btn,a,[class*=btn]');
  for(var j=0;j<bs.length;j++){
    if(vis(bs[j])) btns.push(txt(bs[j]).slice(0,12)+'|'+String(bs[j].className).slice(0,40));
  }
  var as=c.querySelectorAll('a[href]');
  for(var k=0;k<as.length;k++) links.push(as[k].getAttribute('href'));
  out.push({cls:String(c.className).slice(0,60), text:txt(c).slice(0,140),
            btns:btns, links:links.slice(0,3)});
}
return JSON.stringify(out);
})()'''

# 落地页上所有可见按钮 + 带"面试"字样的可点元素
PAGE_JS = '''(function(){
function txt(e){return (e.textContent||'').replace(/\\s+/g,' ').trim();}
function vis(e){return e.getClientRects().length>0;}
var out=[];
var els=document.querySelectorAll('button,a,[class*=btn],[class*=accept],[class*=refuse],[class*=reject]');
for(var i=0;i<els.length;i++){
  var e=els[i], t=txt(e);
  if(!vis(e)||!t) continue;
  out.push(t.slice(0,14)+' | '+String(e.className).slice(0,46)+' | '+(e.getAttribute('href')||''));
}
return JSON.stringify(out.slice(0,40));
})()'''


def interview_chats():
    """从存档里挑出带"现场面试/邀请您…面试"卡片的会话。"""
    out = []
    for row in MessageStore().get_all_chats_detail():
        acc = int(row.get("account_index") or 0)
        for m in row.get("messages") or []:
            body = str(m.get("card_text") or m.get("text") or "")
            if "面试" in body and ("邀请您" in body or "邀请你" in body):
                out.append({"account": acc, "name": row.get("chat_name"),
                            "company": row.get("company"), "card": body[:90]})
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="点进「立即查看」看落地页")
    args = ap.parse_args()

    rows = interview_chats()
    print(f"存档里带面试邀请卡片的会话：{len(rows)} 个")
    for r in rows[:8]:
        print(f"\n── 账号{r['account']} {r['name']}｜{r['company']}")
        print(f"   卡片: {r['card']}")
        instance = attach(r["account"])
        handler = BossChatHandler(browser_instance=instance)
        try:
            if not handler.enter_chat({"name": r["name"], "company": r["company"], "index": -1}):
                print("   进不去这个会话")
                continue
            print("   页面上的面试卡片:", handler.page.run_js(CARD_JS, as_expr=True))
            if not args.go:
                continue
            browser = instance._get_active().browser
            before = set(browser.tab_ids)
            clicked = handler.page.run_js('''(function(){
function txt(e){return (e.textContent||'').replace(/\\s+/g,' ').trim();}
var els=document.querySelectorAll('.message-card-wrap a,.message-card-wrap button,.message-card-wrap [class*=btn]');
for(var i=0;i<els.length;i++) if(txt(els[i]).indexOf('立即查看')>=0){els[i].click();return 'clicked';}
return 'no-link';})()''', as_expr=True)
            time.sleep(5)
            new_ids = set(browser.tab_ids) - before
            print(f"   点立即查看: {clicked}，新开标签 {len(new_ids)} 个")
            for tid in list(new_ids) + [None]:
                try:
                    tgt = browser.get_tab(tid) if tid else browser.latest_tab
                    print(f"   落地 URL: {tgt.url}")
                    print(f"   可见按钮: {tgt.run_js(PAGE_JS, as_expr=True)}")
                except Exception as e:
                    print("   读落地页失败:", type(e).__name__, str(e)[:60])
        finally:
            try:
                instance._get_active().close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
