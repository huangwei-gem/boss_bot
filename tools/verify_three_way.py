# -*- coding: utf-8 -*-
"""三端一致性检测 — BOSS 线上 DOM / 后端存储 / 前端渲染 必须显示同一件事

为什么单独做这个：界面是按前端好看写的，实际很容易出现
"线上有 12 条、后端存 9 条、界面渲染 8 条"这种错位，
而且方向（我方/对方）和送达/已读标签最容易悄悄不一致。

三端各取一次数据，逐会话比对：
  A 端 BOSS 线上：破解版浏览器打开 web/geek/chat，点进会话读 DOM（只读，绝不发消息）
  B 端 后端：message_store 落盘的会话（也就是 /api/chats 给前端的数据）
  C 端 前端：仪表盘渲染出来的气泡（含被前端过滤掉的系统消息规则）

用法：python tools/verify_three_way.py [--chats 3]
"""

import argparse
import io
import re
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage
from boss_bot.platform_compat import 破解版路径

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = 破解版路径(BASE)
PORT = 9401
DASH = "http://127.0.0.1:5000"
CHAT_URL = "https://www.zhipin.com/web/geek/chat"

# BOSS 线上会话列表：昵称 + 岗位（岗位在右侧头部，列表里没有）
JS_BOSS_LIST = '''(
  function(){
    var out=[];
    var items=document.querySelectorAll(".friend-content");
    for(var i=0;i<items.length;i++){
      var nameEl=items[i].querySelector(".name-text");
      if(!nameEl) continue;
      var badge=items[i].querySelector(".notice-badge");
      // 公司就在行上（.name-box 里的第 2 个 span）：同昵称靠它分人，
      // 实测 34 行只用姓名有 4 组重名，加上公司后全唯一
      var box=items[i].querySelector(".name-box"), spans=[];
      if(box) for(var k=0;k<box.children.length;k++){
        var c=box.children[k];
        if(c.tagName==="SPAN"){var t=(c.textContent||"").trim(); if(t) spans.push(t);}
      }
      out.push({name:nameEl.textContent.trim(),
                company:spans.length>1?spans[1]:"",
                selected:(items[i].className||"").indexOf("selected")>=0,
                unread:badge?badge.textContent.trim():"",
                preview:(items[i].querySelector(".last-msg-text")||{}).textContent||""});
    }
    return JSON.stringify(out);
  }
)()'''

# BOSS 线上消息：独立实现，不复用 page_handler，避免"用同一段代码自证一致"
JS_BOSS_MSGS = '''(
  function(){
    var out=[];
    var items=document.querySelectorAll(".message-item");
    for(var i=0;i<items.length;i++){
      var it=items[i];
      var te=it.querySelector(".text-content");
      var text=te?(te.textContent||"").trim():"";
      var card=!te;
      // 岗位卡/简历卡没有 .text-content，取整行文本，否则两端都会漏算同一行
      if(!text) text=(it.innerText||"").trim();
      // 整行文本会把时间头也带进来，后端只存卡片正文，这里去掉时间再比
      var tm=it.querySelector(".item-time");
      if(tm && text) text=text.replace((tm.innerText||"").trim(),"").trim();
      var st=it.querySelector("i.message-status");
      var stCls=st?(st.className||""):"";
      out.push({
        text:text,
        card:card,
        mine:(it.className||"").indexOf("item-friend")<0,
        status:stCls.indexOf("status-read")>=0?"read":(stCls.indexOf("status-delivery")>=0?"delivery":""),
        time:((it.querySelector(".item-time .time")||{}).textContent||"").trim()
      });
    }
    return JSON.stringify(out);
  }
)()'''

# 前端渲染出来的气泡
JS_UI_BUBBLES = '''(
  function(){
    var box=document.getElementById("bossChatMessages");
    if(!box) return "[]";
    var rows=box.querySelectorAll(".boss-msg-row");
    var out=[];
    for(var i=0;i<rows.length;i++){
      // 方向看 class 里的 me 这个词："system" 也含 me，用 indexOf 会把系统条算成我方
      var mine=/(^|\\s)me(\\s|$)/.test(rows[i].className);
      var b=rows[i].querySelector(".boss-msg-bubble")
             || rows[i].querySelector(".boss-msg-card")
             || rows[i].querySelector(".boss-msg-img")
             || rows[i].querySelector(".boss-msg-divider");
      var st=rows[i].querySelector(".boss-msg-status");
      var c=b.querySelector?b.querySelector(".boss-msg-card-title"):null;
      var txt=b?((b.textContent||b.getAttribute("alt")||"").trim()):"";
      if(c) txt=txt.replace(c.textContent.trim(),"").trim();
      out.push({text:txt,mine:mine,status:st?st.textContent.trim():""});
    }
    return JSON.stringify(out);
  }
)()'''

report = {"chats": [], "errors": [], "summary": {}}


def norm(s):
    """归一化空白：BOSS 卡片 innerText 带换行，后端存的是拼接后的单行，
    不统一就会把同一句话判成两条不同消息。"""
    return re.sub(r"\s+", " ", (s or "").replace("\u00a0", " ")).strip()


def squash(t):
    """去掉所有空白再比。BOSS 把卡片文字拆成多个节点，innerText 会多出空格，
    和后端存的连续文本不是同一个字符串，但内容确实是同一句。"""
    return re.sub(r"\s+", "", norm(t))


def same_text(a, b):
    """两条文本是否指同一条消息。

    纯文本要求完全相等；岗位卡/简历卡这类整行取 innerText 的，
    BOSS 会把"优""查看详细分析"这类角标拆成独立节点，所以允许包含关系。
    """
    a, b = squash(a), squash(b)
    if not a or not b:
        return a == b
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 10 and shorter in longer


def start_flask():
    """前端这一腿要有个面板：线上面板已经起着就直接用，别再绑一个 5000。

    Windows 允许两个进程同时 bind 同一端口，请求会被随机分到其中一边——
    之前在这里又起一个实例，界面读到的就是"半真半假"的面板，
    而且那一份实例的启停请求会打到真的投递轮上。
    """
    sys.path.insert(0, os.path.join(BASE, "flask-version"))
    import urllib.request

    def alive():
        try:
            return urllib.request.urlopen(DASH, timeout=2).status == 200
        except Exception:
            return False

    if alive():
        print("   复用已在运行的面板，不再另起 5000 实例")
        import app as A
        return A
    import app as A
    A._open_dashboard = lambda *a, **k: None
    A._auto_ai_health = lambda *a, **k: None
    kwargs = {"host": "127.0.0.1", "port": 5000, "debug": False, "use_reloader": False}
    if A._socketio_kwargs.get("async_mode") == "threading":
        kwargs["allow_unsafe_werkzeug"] = True
    threading.Thread(target=lambda: A.socketio.run(A.app, **kwargs), daemon=True).start()
    for _ in range(60):
        if alive():
            return A
        time.sleep(0.5)
    raise RuntimeError("Flask 未就绪")


def launch_browser(account):
    assert os.path.exists(CLOAK), f"破解版浏览器不存在: {CLOAK}"
    prof = os.path.join(BASE, "browser_data", f"account_{account}")
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(9401 + account)
    # 用该账号的真实 profile：里面是已登录的 BOSS 会话
    co.set_argument(f"--user-data-dir={prof}")
    co.set_argument("--disable-blink-features=AutomationControlled")
    try:
        return ChromiumPage(co)
    except Exception as e:
        # 最常见原因：同一个 user-data-dir 已被另一个 Chrome 实例占用
        # （account_N 是正式 profile，机器人正在跑时也会占）
        print(f"浏览器起不来：{e}\n"
              f"多半是 profile 被占用：{prof}\n"
              f"查占用进程（只列不改）：\n"
              f'  powershell -NoProfile -Command "Get-CimInstance Win32_Process '
              f'-Filter \\"Name=\'chrome.exe\'\\" | Where-Object {{ $_.CommandLine -like \'*account_{account}*\' }} '
              f'| Select-Object ProcessId,CreationDate"\n'
              f"若机器人正在运行，请先在界面里点「停止」再跑本脚本。")
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chats", type=int, default=3, help="比对最近几个会话")
    ap.add_argument("--account", type=int, default=0,
                    help="比对哪个号（0=主账号）：profile、存档、界面数据范围都跟着切")
    args = ap.parse_args()
    account = args.account

    print(f"1) 起后端（账号{account}）...")
    start_flask()
    from boss_bot.message_store import MessageStore
    store = MessageStore(account_index=account)
    # 只比对该号自己的存档：get_chat_list 是全目录扫描，别的号的文件混进来
    # 就会把"BOSS 有、后端没存"判成有
    backend_chats = {c["chat_id"]: c for c in store.get_chat_list()
                     if int(c.get("account_index") or 0) == account}
    backend_by_name = {}
    for _cid, _c in backend_chats.items():
        backend_by_name.setdefault(_c["chat_name"], []).append(_c)
    print(f"   后端已存会话 {len(backend_chats)} 个")

    print("2) 起破解版浏览器，打开 BOSS 聊天页 ...")
    page = launch_browser(account)
    # 用完必须关掉：这个浏览器占的是 browser_data/account_N 正式 profile，
    # 脚本退了它不退，机器人再启动就连不上 922x（实测账号2 就是这么起不来的）
    import atexit
    atexit.register(lambda: getattr(page, "quit", lambda: None)())
    boss = page.new_tab(CHAT_URL)
    time.sleep(8)

    boss_list = json.loads(boss.run_js(JS_BOSS_LIST, as_expr=True) or "[]")
    print(f"   BOSS 侧栏会话 {len(boss_list)} 个")
    if not boss_list:
        report["errors"].append("BOSS 侧栏没读到会话：登录可能已失效，或被风控页拦截")
        print("   !! 未读到会话，BOSS 端这条腿走不通（登录态/风控），先只做后端↔前端")
    report["summary"]["boss_conversations"] = len(boss_list)
    report["summary"]["backend_conversations"] = len(backend_chats)

    def identity_of(boss_row):
        """BOSS 行 → 后端会话：必须 姓名+公司 都对得上。

        以前认不出就退到"同名唯一的那条"，结果把两段不同的对话当成一段比：
        实测 熊女士 线上在聊 长沙乐芒少年文化传媒，存档里只有 熊女士#益丰大药房
        （另一段对话），退路让脚本报出"后端 0 条 / 前端 2 条"这种假不一致。
        对不上就是对不上，让它进"BOSS 有但后端没存"的名单。
        """
        cands = backend_by_name.get(boss_row["name"]) or []
        comp = (boss_row.get("company") or "").strip()
        if not comp:
            return cands[0] if len(cands) == 1 else None
        hits = [c for c in cands if (c.get("company") or "").strip() == comp]
        return hits[0] if hits else None

    # 会话身份集合比对：BOSS 有而后端没有的，按 姓名 报（重名不再互相顶替）
    boss_names = [b["name"] for b in boss_list if b["name"]]
    missing_in_backend = [n for n in boss_names if n not in backend_by_name]
    report["summary"]["boss_not_in_backend"] = missing_in_backend[:20]
    report["summary"]["boss_not_in_backend_count"] = len(missing_in_backend)

    # 只比对线上和后端都有的会话
    targets = [b for b in boss_list if identity_of(b)][:args.chats]
    print(f"3) 逐会话比对（{len(targets)} 个）...")

    dash = page.new_tab(DASH)
    time.sleep(4)
    # 前端这一腿也要跟着号走：数据范围不停在"全部账号"，
    # 否则 selectBossChat(裸身份) 会挑到另一个号的同名会话
    dash.run_js("setDataScope(%s)" % json.dumps(str(account)), as_expr=True)
    time.sleep(2.5)
    if account and str(account) not in (dash.run_js(
            "(function(){var b=document.querySelector('.scope-chip.active');"
            "return String((b&&b.getAttribute('data-scope'))||dataScope);})()",
            as_expr=True) or ""):
        print("   !! 界面数据范围没切到账号%d，前端这一腿不可信" % account)

    for t in targets:
        name, comp = t["name"], t.get("company") or ""
        chat = identity_of(t)
        row = {"chat_name": name, "chat_id": chat["chat_id"],
               "company": comp, "issues": []}
        # A 端：点进 BOSS 会话读 DOM。按 姓名+公司 定位行 —— 侧栏 34 行里
        # 有 4 组重名昵称，只按姓名点就会随机打开其中一个，比的就不是同一段对话
        clicked = boss.run_js(f'''(
            function(){{
              var items=document.querySelectorAll(".friend-content");
              var wantN={json.dumps(name)}, wantC={json.dumps(comp)};
              function compOf(el){{
                var box=el.querySelector(".name-box"),sp=[];
                if(box) for(var k=0;k<box.children.length;k++){{
                  var c=box.children[k];
                  if(c.tagName==="SPAN"){{var x=(c.textContent||"").trim(); if(x)sp.push(x);}}
                }}
                return sp.length>1?sp[1]:"";
              }}
              for(var i=0;i<items.length;i++){{
                var n=items[i].querySelector(".name-text");
                if(!n||n.textContent.trim()!==wantN) continue;
                if(wantC && compOf(items[i])!==wantC) continue;
                items[i].click(); return i;
              }}
              return -1;
            }}
        )()''', as_expr=True)
        if clicked in (-1, None):
            row["issues"].append("BOSS 侧栏里点不到该会话")
            report["chats"].append(row)
            continue
        time.sleep(3)
        # 点完复核 selected 落在哪一行：侧栏会重排，点第 i 行不等于选中第 i 行
        after = json.loads(boss.run_js(JS_BOSS_LIST, as_expr=True) or "[]")
        sel = [b for b in after if b.get("selected")]
        if len(sel) != 1 or sel[0]["name"] != name \
                or (comp and (sel[0].get("company") or "") != comp):
            row["issues"].append(
                f"点击行 #{clicked}，但 selected 是 "
                f"{[(b['name'], b.get('company')) for b in sel]}")
        boss_msgs = json.loads(boss.run_js(JS_BOSS_MSGS, as_expr=True) or "[]")
        boss_msgs = [m for m in boss_msgs if m["text"]]
        header = boss.run_js(
            'document.querySelector(".top-info-content .name-text") ? '
            'document.querySelector(".top-info-content .name-text").textContent.trim() : ""',
            as_expr=True)
        row["boss_header_name"] = header
        row["boss_count"] = len(boss_msgs)
        if header and header != name:
            row["issues"].append(f"BOSS 头部姓名({header})与侧栏({name})不一致")

        # B 端：后端存储（按 姓名+公司 取那一路；引擎自记的 action 线上没有，不比）
        be = store.get_messages(name, chat.get("job_name", ""), comp)
        be_norm = [{"text": norm(m.get("text") or m.get("content") or m.get("card_text")),
                    "mine": bool(m.get("is_mine"))}
                   for m in be if m.get("kind") != "action"]
        be_norm = [m for m in be_norm if m["text"]]
        row["backend_count"] = len(be_norm)

        # C 端：前端渲染（按身份选中；按昵称选会把同名的两路混成一路）
        dash.run_js('selectBossChat(%s)' % json.dumps(chat["chat_id"], ensure_ascii=False),
                    as_expr=True)
        time.sleep(2)
        ui = json.loads(dash.run_js(JS_UI_BUBBLES, as_expr=True) or "[]")
        ui_norm = [{"text": norm(m["text"]), "mine": m["mine"]} for m in ui if norm(m["text"])]
        row["ui_count"] = len(ui_norm)

        # 比对 1：BOSS 线上每条消息后端都要有（BOSS 只渲染可视窗口，是子集关系）
        def card_key(t):
            return squash(t)[:12]
        lost = []
        for m in boss_msgs:
            if m.get("card"):
                ok = any(card_key(m["text"]) == card_key(x["text"]) for x in be_norm)
            else:
                ok = any(same_text(m["text"], x["text"]) for x in be_norm)
            if not ok:
                lost.append(m["text"])
        if lost:
            row["issues"].append(f"后端缺 {len(lost)} 条线上消息，例：{lost[0][:28]}")

        # 比对 2：后端与前端条数/文本/方向必须完全一致
        if len(be_norm) != len(ui_norm):
            row["issues"].append(f"后端 {len(be_norm)} 条 vs 前端 {len(ui_norm)} 条")
        else:
            diff = [(i, a, b) for i, (a, b) in enumerate(zip(be_norm, ui_norm))
                    if not same_text(a["text"], b["text"]) or a["mine"] != b["mine"]]
            if diff:
                i, a, b = diff[0]
                row["issues"].append(
                    f"第{i+1}条不一致 后端[{a['mine']}]{a['text'][:24]} vs 前端[{b['mine']}]{b['text'][:24]}")

        # 比对 3：送达/已读标签不能凭空出现
        ui_status = sum(1 for m in ui if m.get("status"))
        be_status = sum(1 for m in be if m.get("status"))
        if ui_status > be_status:
            row["issues"].append(f"前端显示 {ui_status} 条送达/已读，后端只有 {be_status} 条")
        row["ui_status_count"] = ui_status
        row["backend_status_count"] = be_status
        report["chats"].append(row)
        flag = "OK " if not row["issues"] else "差异"
        print(f"   [{flag}] {name}: 线上{row.get('boss_count','-')} / "
              f"后端{row.get('backend_count','-')} / 前端{row.get('ui_count','-')}"
              + ("" if not row["issues"] else "  -> " + "; ".join(row["issues"])))

    bad = [c for c in report["chats"] if c["issues"]]
    report["summary"]["account"] = account
    report["summary"]["checked"] = len(report["chats"])
    report["summary"]["inconsistent"] = len(bad)
    print("\n结论：")
    print(f"  账号{account}：比对会话 {len(report['chats'])} 个，三端不一致 {len(bad)} 个")
    print(f"  BOSS 有但后端没存的会话：{report['summary']['boss_not_in_backend_count']} 个"
          f"（新会话未抓取/重名合并都会进这个名单）")
    out = os.path.join(BASE, "tools", f"verify_three_way_a{account}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  明细：{os.path.relpath(out, BASE)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
