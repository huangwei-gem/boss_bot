# -*- coding: utf-8 -*-
"""仪表盘端到端实测 — 真浏览器、真点击、真接口，逐项断言

和 pytest 的分工：pytest 管单元/集成逻辑，这个脚本管"打开来真的能用"。
全程不点任何会对外发消息的按钮（启动/打招呼/发送/发简历都不点），
只做读、切、筛、存配置这类可回滚的操作。

用法：python tools/e2e_dashboard.py [--headed]
退出码：0 全过，1 有失败项
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
from boss_bot.platform_compat import 破解版路径

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = 破解版路径(BASE)
PORT = 9402
DASH = os.environ.get("BOSS_PANEL_URL", "http://127.0.0.1:5000")
# 本脚本会真点「启动/停止/暂停」按钮：对着线上面板点下去就是把投递轮打断
# （2026-10-04 20:44 两个号的轮次就被这么停过一次）。要么走
# tests/run_dashboard_on_temp_panel.py 起临时面板，要么显式 BOSS_E2E_ALLOW_LIVE=1。
if ":5000" in DASH and os.environ.get("BOSS_E2E_ALLOW_LIVE") != "1":
    raise SystemExit(
        f"拒绝对着线上面板跑实测（{DASH}）：本脚本会点启停按钮。\n"
        f"请用 python tests/run_dashboard_on_temp_panel.py")
SHOTS = os.path.join(BASE, "tools", "e2e")
os.makedirs(SHOTS, exist_ok=True)

RESULTS = []


def check(group, name, ok, detail=""):
    ok = bool(ok)
    RESULTS.append({"group": group, "name": name, "ok": ok, "detail": str(detail)[:220]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {group} / {name}"
          + (f"  ({str(detail)[:90]})" if detail and not ok else ""))


def js(tab, code):
    """run_js 包装：把 JSON 字符串还原成 Python 对象

    60 秒而不是默认 30 秒：跑实测时线上面板正在做启动全量同步（逐个点开 270 个会话），
    加上两个真浏览器和 AI 请求，页面主线程被排到后面，读一个按钮文字也能超 30 秒——
    整场实测就这么在 64 项之后崩掉，看不出任何被测代码的问题。
    """
    raw = tab.run_js(code, as_expr=True, timeout=60)
    if isinstance(raw, str) and raw[:1] in "[{":
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw


def wait_panel():
    """等面板就绪。面板由调用方（tests/run_dashboard_on_temp_panel.py）起。

    以前这里自己 `socketio.run(port=5000)`：Windows 允许两个进程同时 bind 同一端口，
    于是实测脚本和线上面板抢同一个 5000，请求随机落到其中一边——
    落到脚本这边就是"点了停止，线上那两个号的投递轮被停"。
    """
    import urllib.request
    for _ in range(60):
        try:
            if urllib.request.urlopen(DASH, timeout=2).status == 200:
                return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"面板没起来：{DASH}")


def launch():
    assert os.path.exists(CLOAK), "破解版浏览器不存在，按用户要求必须用它跑"
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(PORT)
    co.set_argument(f"--user-data-dir={os.path.join(BASE, 'browser_data', 'e2e')}")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1440,900")
    # 机器忙时 cloakbrowser 起得慢，DrissionPage 连不上 9402 就抛 BrowserConnectError，
    # 而它拉起的浏览器还活着——下一次实测会被自己留下的 profile 锁挡住。多试两次即可。
    last_err = None
    for _ in range(3):
        try:
            page = ChromiumPage(co)
            break
        except Exception as e:
            last_err = e
            time.sleep(6)
    else:
        raise last_err
    # 脚本退了浏览器不能留着：browser_data/e2e 被它占住，下一次实测和
    # 截图巡检都会连不上（verify_three_way 还因此把账号2 的正式 profile 锁了）
    import atexit
    atexit.register(lambda: getattr(page, "quit", lambda: None)())
    return page


COLLECT_ERRORS = '''(function(){
  window.__jsErrors = window.__jsErrors || [];
  window.addEventListener("error", function(e){ window.__jsErrors.push(String(e.message)); });
  window.addEventListener("unhandledrejection", function(e){
    window.__jsErrors.push("promise: " + String(e.reason)); });
})()'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    print("== 等面板就绪 ==")
    wait_panel()
    print("== 起破解版浏览器 ==")
    page = launch()

    # ── 1. 加载 ──
    page.get(DASH)
    js(page, COLLECT_ERRORS)
    time.sleep(4)
    check("加载", "标题正确", "Boss直聘" in (page.title or ""), page.title)
    check("加载", "主标题在页面上", "自动投递" in (js(page, 'document.body.innerText') or ""))
    check("加载", "WebSocket 已连上",
          "已连接" in (js(page, 'document.getElementById("connStatus").textContent') or ""))

    # ── 2. 指标卡与后端一致 ──
    cards = js(page, '''(function(){var c=document.querySelectorAll(".stat-card"),r=[];
      for(var i=0;i<c.length;i++)r.push({label:(c[i].querySelector(".stat-label")||{}).textContent,
      value:(c[i].querySelector(".stat-value")||{}).textContent,
      sub:(c[i].querySelector(".stat-sub")||{}).textContent});return JSON.stringify(r);})()''')
    check("指标", "4 张卡", len(cards) == 4, [c["label"] for c in cards])
    # fetch 是异步的，用同步 XHR 拿后端值再比
    api = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/metrics",false);x.send();return JSON.parse(x.responseText);})()''')
    check("指标", "累计投递与后端一致",
          str(api["total"]["greet_sent"]) == cards[0]["value"].strip(),
          f"后端{api['total']['greet_sent']} 前端{cards[0]['value']}")
    check("指标", "每日上限取自配置", "150" in (cards[1]["sub"] or ""), cards[1]["sub"])
    # 上限是按号算的：全部账号范围下"已投递 160 / 今日已达上限 150"这种自相矛盾
    # 是以前真在屏上挂着的（两号各 150，合计 300 才算到顶）
    cap_line = js(page, '''(function(){var v=document.getElementById("statApplied"),
      s=document.getElementById("statAppliedSub");
      var nums=(s.textContent||"").match(/\\d+/g)||[];
      return JSON.stringify([Number(v.textContent),nums.map(Number),s.textContent.trim()]);})()''')
    check("指标", "今日已投递不会超过标签上写的上限",
          not cap_line[1] or cap_line[0] <= max(cap_line[1]),
          cap_line[2])
    chips = js(page, '''(function(){var b=document.querySelectorAll("#metricsScope .scope-chip"),r=[];
      for(var i=0;i<b.length;i++)r.push(b[i].textContent);return JSON.stringify(r);})()''')
    check("指标", "账号范围切换可用", len(chips) >= 3, chips)
    js(page, 'document.querySelectorAll("#metricsScope .scope-chip")[2].click()')
    time.sleep(1.5)
    v2 = js(page, 'document.getElementById("statTotal").textContent')
    check("指标", "切到账号2 后数值随之变化", str(v2) != str(cards[0]["value"]),
          f"全部={cards[0]['value']} 账号2={v2}")
    js(page, 'document.querySelectorAll("#metricsScope .scope-chip")[0].click()')
    time.sleep(1.2)

    # ── 3. 投递记录表 + 筛选 ──
    rows_all = js(page, 'document.querySelectorAll(".greet-table tbody tr").length')
    check("记录", "投递记录有数据", rows_all and int(rows_all) > 0, rows_all)
    picked = js(page, '''(function(){var sel=document.getElementById("greetFilterStatus");
      if(!sel) return "no-select"; sel.value="skip";
      sel.dispatchEvent(new Event("change")); return sel.value;})()''')
    check("记录", "状态筛选控件可定位", picked == "skip", picked)
    time.sleep(1.5)
    rows_filtered = js(page, 'document.querySelectorAll(".greet-table tbody tr").length')
    texts = js(page, '''(function(){var t=document.querySelectorAll(".greet-table tbody tr"),o=[];
      for(var i=0;i<Math.min(t.length,5);i++)o.push(t[i].innerText.replace(/\\s+/g," "));
      return JSON.stringify(o);})()''')
    check("记录", "状态筛选生效（行数变少且都是跳过）",
          rows_filtered is not None and int(rows_filtered) <= int(rows_all)
          and all("跳过" in (x or "") for x in texts),
          f"全部{rows_all} 筛选后{rows_filtered}")
    js(page, 'resetGreetFilter && resetGreetFilter()')
    time.sleep(1)

    # ── 4. 聊天面板：渲染顺序必须与后端数组一致，且按各自账号的文件读 ──
    from boss_bot.message_store import MessageStore
    check("聊天", "会话列表渲染",
          js(page, 'document.querySelectorAll(".boss-chat-item").length') > 0,
          js(page, 'document.querySelectorAll(".boss-chat-item").length'))

    def chat_candidates(acct):
        st = MessageStore(account_index=acct)
        own = [c for c in st.get_chat_list()
               if c.get("message_count") and int(c.get("account_index") or 0) == acct]
        return st, own

    acct0_store, acct0_chats = chat_candidates(0)
    _, acct1_chats = chat_candidates(1)

    def ui_rows(page):
        """前端画出来的每一行：正文（气泡或卡片）+ 左右方向 + 时间标签"""
        raw = js(page, '''(function(){var b=document.getElementById("bossChatMessages");
          if(!b)return "[]";var rows=b.querySelectorAll(".boss-msg-row"),o=[];
          for(var i=0;i<rows.length;i++){
            var el=rows[i].querySelector(".boss-msg-bubble")
                  ||rows[i].querySelector(".boss-msg-card");
            var t=rows[i].querySelector(".boss-msg-time");
            // 方向要看 class 里的 me 这个词，不能用 indexOf：
            // "system" 里也含 "me"，系统条会被误判成我方消息
            var mine=/(^|\\s)me(\\s|$)/.test(rows[i].className);
            o.push({mine:mine,
                    text:(el?(el.textContent||""):"").replace(/\\s+/g," ").trim(),
                    time:(t?(t.textContent||""):"").trim()});}
          return JSON.stringify(o);})()''')
        # js() 已经把 JSON 字符串还原成对象了，这里不能再 loads 一次
        return raw if isinstance(raw, list) else json.loads(raw or "[]")

    def expected_rows(store, chat):
        """后端该画成什么样：卡片/系统行也要有可见文本，引擎自记的 action 不进气泡序列"""
        msgs = store.get_messages(chat["chat_name"], chat.get("job_name", ""),
                                  chat.get("company", ""))
        out = []
        for m in msgs:
            if m.get("kind") == "action":
                continue
            text = ((m.get("text") or m.get("content") or m.get("card_text") or "")
                    .replace("\n", " ").strip())
            out.append({"mine": bool(m.get("is_mine")), "text": text,
                        "time": (m.get("time") or "").strip()})
        return out

    def select_chat(page, chat_id, acct):
        """按「身份 + 账号」选中会话。

        「全部账号」范围下两个号可能聊到同一家公司的同一个人（实测：李女士 @ 深圳市极客星球电…
        账号1 存 9 条、账号2 存 7 条），前端缓存键因此带 @a账号 后缀。
        只传裸 chat_id 会随机命中其中一路，比对就没有意义——这里让页面自己按账号找出键。
        """
        return js(page, '(function(id, acct){var keys=Object.keys(replyChatCache);'
                        'for(var i=0;i<keys.length;i++){var g=replyChatCache[keys[i]]||{};'
                        'var bare=keys[i].split("@a")[0];'
                        'if((bare===id||g.chat_id===id)&&Number(g.account_index)===acct){'
                        'selectBossChat(keys[i]);return keys[i];}}return "";})('
                        + json.dumps(chat_id, ensure_ascii=False) + ',' + str(int(acct)) + ')')

    # 会话视图挂在"回复记录"页签后面：不切过去，截图拍到的永远是打招呼表格，
    # 用户点名的"BOSS 直聘端那边没换成 svg"就看不见
    js(page, 'switchRecordTab("reply")')
    time.sleep(.6)
    check("聊天", "回复记录页签切得过去",
          "active" in js(page, 'document.getElementById("replyTabContent").className'),
          js(page, 'document.getElementById("replyTabContent").className'))

    for acct, cand in ((0, acct0_chats), (1, acct1_chats)):
        if not cand:
            continue
        store = MessageStore(account_index=acct)
        target = max(cand, key=lambda c: c["message_count"])
        # 选择会话必须用身份（姓名+公司），昵称本身在两个号上都会撞车
        used = select_chat(page, target["chat_id"], target.get("account_index", acct))
        assert used, f"界面上找不到 {target['chat_id']} 属于账号{acct} 的那一路"
        time.sleep(2)
        ui = ui_rows(page)
        be = expected_rows(store, target)
        name = f'{target["chat_name"]}|{(target.get("company") or "")[:10]}'
        tag = f"聊天(账号{acct + 1})"
        check(tag, "前端行数 = 后端消息数", len(ui) == len(be),
              f"{name} 前端{len(ui)} 后端{len(be)}")
        bad = sum(1 for a, b in zip(ui, be) if a["text"] != b["text"])
        check(tag, "逐条文本与线上存储一致", bad == 0, f"{bad} 条文本不符")
        order_bad = sum(1 for a, b in zip(ui, be)
                        if a["text"] and b["text"] and a["text"] != b["text"])
        check(tag, "顺序一致（不重排）", order_bad == 0, f"{order_bad} 处错位")
        time_bad = sum(1 for a, b in zip(ui, be) if a["time"] != b["time"])
        check(tag, "时间标签照搬线上", time_bad == 0, f"{time_bad} 条时间不符")
        mine_ok = all(a["mine"] == b["mine"] for a, b in zip(ui, be))
        check(tag, "我方/对方方向正确", mine_ok)
        # class 上有 me 不代表画在右边：气泡外层 wrapper 以前不限宽，
        # 长消息一撑就占满整行，justify-content 推不动 —— 我方气泡被画到左边，
        # 和 BOSS 端左右相反。这一条只能量像素。
        sides = js(page, '''(function(){var box=document.getElementById("bossChatMessages");
          if(!box)return JSON.stringify([null,null,null]);
          var br=box.getBoundingClientRect(),mine=[],hr=[];
          var rows=box.querySelectorAll(".boss-msg-row");
          for(var i=0;i<rows.length;i++){
            var el=rows[i].querySelector(".boss-msg-bubble")||rows[i].querySelector(".boss-msg-card");
            if(!el||!(el.textContent||"").trim())continue;
            var r=el.getBoundingClientRect();
            if(r.width<40)continue;
            var cls=rows[i].className;
            if(/(^|\\s)me(\\s|$)/.test(cls))mine.push(Math.round(br.right-r.right));
            else if(/(^|\\s)hr(\\s|$)/.test(cls))hr.push(Math.round(r.left-br.left));
          }
          return JSON.stringify([mine,hr,Math.round(br.width)]);})()''')
        m_off, h_off, pane_w = sides
        check(tag, "我方气泡贴着右边画（像素级）",
              bool(m_off) and max(m_off) <= 28, f"右边距 {m_off} 栏宽 {pane_w}")
        check(tag, "对方气泡贴着左边画（像素级）",
              bool(h_off) and max(h_off) <= 60, f"左边距 {h_off} 栏宽 {pane_w}")
        page.get_screenshot(path=os.path.join(SHOTS, f"chat_a{acct}.png"))

    # 来源标签直出内部键（截图里看到过 scam_filter）= 界面在念数据库。
    # 趁会话视图正显示着查：切回打招呼表格后这些节点全是隐藏的，等于没验
    leak = js(page, '''(function(){var s=document.querySelectorAll(".boss-msg-source"),n=[];
      for(var i=0;i<s.length;i++){if(!s[i].offsetParent)continue;
        var t=(s[i].textContent||"").trim();
        if(/^[a-z]+(_[a-z]+)+$/.test(t))n.push(t);}
      return JSON.stringify([n.slice(0,8),s.length]);})()''')
    check("聊天", "来源标签都是中文，不直出内部键",
          leak[0] == [] and leak[1] > 0, leak)

    # ── 4b. 同昵称的两段对话必须互不串台 ──
    # 实测侧栏 34 行里 4 组重名（陈女士/唐女士/刘女士/易女士），
    # 以前按昵称存文件，点开一个就把两个人的话并在一起显示
    names = {}
    for c in acct0_chats:
        names.setdefault(c["chat_name"], []).append(c)
    dups = {k: v for k, v in names.items() if len(v) > 1}
    if dups:
        name, two = next(iter(dups.items()))
        per_identity = []
        for chat in two:
            assert select_chat(page, chat["chat_id"], chat.get("account_index", 0)), \
                f"界面上找不到 {chat['chat_id']}"
            time.sleep(1.6)
            rows = ui_rows(page)
            be = expected_rows(MessageStore(account_index=0), chat)
            per_identity.append((chat["chat_id"], rows, be,
                                 {m.get("mid") for m in
                                  MessageStore(account_index=0).get_messages(
                                      chat["chat_name"], chat.get("job_name", ""),
                                      chat.get("company", ""))
                                  if m.get("mid")}))
        each_matches_own_file = all(len(r) == len(b) and
                                    all(a["text"] == c["text"] for a, c in zip(r, b))
                                    for _, r, b, _ in per_identity)
        detail = ""
        if not each_matches_own_file:
            # 光说"不对"没法定位：把每一路的行数差和第一条不一致的原文打出来
            parts = []
            for cid, r, b, _ in per_identity:
                first_bad = next(
                    (f"第{i}条 界面={(a['text'] or '')[:14]!r} 存档={(c['text'] or '')[:14]!r}"
                     for i, (a, c) in enumerate(zip(r, b)) if a["text"] != c["text"]),
                    "文本一致" if len(r) == len(b) else "条数不同")
                parts.append(f"{(cid.split('#')[-1] or cid)[:10]}: "
                             f"界面{len(r)}行/存档{len(b)}行 {first_bad}")
            detail = " | ".join(parts)
            print("      同昵称比对:", detail)
        # 两路的正文可以撞车（PK 分析卡那句话 BOSS 给谁都一样），
        # 真正能证明"没并成一个文件"的是 data-mid：线上每条唯一。
        # 只有"一条带 mid 的消息都没有"的那一路不参与比对：整路都是引擎自记的
        # action 标记或老数据时本来就没 mid，拿它去比交集只会把"没证据"报成"有 bug"
        mids = [s for _, _, _, s in per_identity]
        keyed = [s for s in mids if s]
        disjoint = len(keyed) >= 2 and not set.intersection(*[set(s) for s in keyed])
        check("会话身份", f"同昵称 {name}×{len(two)} 各自显示自己那一路",
              each_matches_own_file, detail or "点开后读到的不是该身份自己的文件")
        check("会话身份", f"同昵称 {name}×{len(two)} 的 data-mid 互不重叠"
              + (f"（{len(mids) - len(keyed)} 路无 mid，不参与）" if len(keyed) != len(mids) else ""),
              disjoint, f"mid 集合={mids}")
    else:
        check("会话身份", "存在同昵称多路会话可供校验", False,
              "当前账号没有重名会话，这条没真正验到")
    check("会话身份", "列表把身份显示出来（姓名+公司）",
          js(page, '''(function(){var it=document.querySelectorAll(".boss-chat-item-job");
            for(var i=0;i<it.length;i++){if((it[i].textContent||"").trim())return true;}
            return false;})()'''),
          "副标题（公司）没显示，同名会话在列表里分不出来")
    # 全部账号下，同一个 姓名+公司 可能是两个号各自的对话（实测 陆女士@深圳小智）。
    # 不挂账号标记，列表里就是两行一模一样的东西，点哪条全凭运气
    dup = js(page, '''(function(){var rows=document.querySelectorAll(".boss-chat-item");
      var ids={},tagged=0,bad=[];
      function base(r){return (r.getAttribute("data-chat-key")||"").split("@a")[0];}
      for(var i=0;i<rows.length;i++) ids[base(rows[i])]=(ids[base(rows[i])]||0)+1;
      for(var j=0;j<rows.length;j++){
        var b=base(rows[j]), chip=rows[j].querySelector(".boss-chat-item-acc");
        if(ids[b]>1){ if(!chip) bad.push("缺标记:"+b.slice(0,18)); else tagged++; }
        else if(chip) bad.push("多余:"+b.slice(0,18)); }
      return JSON.stringify([tagged,bad.slice(0,4),rows.length]);})()''')
    check("会话身份", "全部账号下撞车的身份挂了账号标记",
          dup[0] > 0 and dup[1] == [], dup)

    # 会话这段验完切回打招呼表格，后面的截图和检查保持原来的场景
    js(page, 'switchRecordTab("greet")')
    time.sleep(.4)

    # ── 5. AI 体检（真实网络请求） ──
    js(page, 'toggleAiProviders()')
    time.sleep(1)
    dots = js(page, 'document.querySelectorAll("#aiProviders .ai-status-dot").length')
    provs = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/ai/health",false);x.send();
      var d=JSON.parse(x.responseText);return d.summary;})()''')
    check("AI", "每个接口都有状态点", int(dots) >= provs["total"], f"{dots} vs {provs['total']}")
    check("AI", "体检结果已落盘并显示", provs["available"] >= 1, provs)
    js(page, 'runAiHealth([2])')
    st = "probing"
    for _ in range(16):          # 真实网络请求，慢接口要等，固定 sleep 会误报
        time.sleep(2)
        st = js(page, '''(function(){var d=document.getElementById("aiDot-2");
          return d?d.className:"none";})()''') or "none"
        if "available" in st:
            break
    check("AI", "单个重测后状态更新", "available" in st or "unavailable" in st, st)

    # ── 5b. 判分预算 / 判分质量 / 提示词默认值 ──
    budget = js(page, 'document.getElementById("aiAnalyzeMaxTokens").value')
    check("AI", "判分预算输入框已按配置回填", str(budget or "").isdigit() and int(budget) >= 512,
          f"值={budget}")
    cfg_budget = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/config",false);x.send();
      return JSON.parse(x.responseText).config.ai.analyze_max_tokens;})()''')
    check("AI", "预算已存进后端配置", int(cfg_budget or 0) == int(budget),
          f"后端 {cfg_budget} vs 界面 {budget}")

    quality = js(page, 'document.getElementById("aiQualityLine").textContent') or ""
    check("AI", "判分质量行有统计", ("判分质量" in quality) and
          ("真判分" in quality or "暂无记录" in quality), quality[:60])
    qapi = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/ai/quality",false);x.send();return JSON.parse(x.responseText);})()''')
    check("AI", "判分质量接口给出兜底率", qapi.get("status") == "ok" and
          "fallback_rate" in qapi and qapi.get("total", 0) >= 1,
          {k: qapi.get(k) for k in ("total", "judged", "fallback", "fallback_rate")})

    # 「恢复默认」必须用引擎那份 20 条的默认规则，不是前端抄本
    js(page, 'typeof DEFAULT_SYSTEM_RULES')
    stale = js(page, '(function(){try{return String(eval("DEFAULT_SYSTEM_RULES")).length;}'+
                     'catch(e){return "none";}})()')
    check("AI", "前端不再自带默认规则抄本", str(stale) == "none", stale)
    js(page, 'showPromptModal()')
    time.sleep(1.5)
    js(page, 'resetPromptModal()')
    time.sleep(1.0)
    sys_rules = js(page, 'document.getElementById("promptSystem").value') or ""
    check("AI", "恢复默认拿到的是引擎默认规则",
          "必须根据完整对话上下文回复" in sys_rules and "语气专业" in sys_rules,
          f"{len(sys_rules)} 字")
    # 只读校验，不点保存：用户改过的提示词不能被测试覆盖
    js(page, 'closePromptModal()')

    # ── 6. 配置保存回路（改→存→读回→还原） ──
    orig = js(page, 'document.getElementById("aiThreshold").value')
    js(page, '''(function(){var e=document.getElementById("aiThreshold");
      e.value="66";e.dispatchEvent(new Event("change"));})()''')
    time.sleep(1.5)
    js(page, 'saveConfigShow()')
    time.sleep(2.5)
    back = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/config",false);x.send();return JSON.parse(x.responseText);})()''')
    th = (back.get("config") or back).get("ai", {}).get("match_threshold")
    check("配置", "改动能存进后端", str(th) == "66", f"读回 {th}")
    toast = js(page, 'document.querySelectorAll(".toast").length')
    check("配置", "保存有轻提示反馈", int(toast or 0) >= 1, toast)
    js(page, '''(function(){var e=document.getElementById("aiThreshold");
      e.value="%s";e.dispatchEvent(new Event("change"));})()''' % orig)
    time.sleep(1)
    js(page, 'saveConfig()')
    time.sleep(2)

    # ── 6b. 演练模式开关走真实 UI 路径 + 整体回写不得抹掉未建模字段 ──
    cfg_file = os.environ.get("BOSS_CONFIG_FILE") or os.path.join(BASE, "bot_config.json")
    theme_before = ""
    try:
        with open(cfg_file, encoding="utf-8") as f:
            theme_before = json.load(f).get("theme", "")
    except Exception:
        pass
    js(page, 'toggleAdvBool("dry_run")')      # 点开关：改值 + 存盘 + 轻提示
    time.sleep(2.5)
    dr = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/config",false);x.send();
      var c=JSON.parse(x.responseText);return (c.config||c).dry_run;})()''')
    check("演练", "开关改动存得进后端", bool(dr), f"读回 {dr}")
    saved_theme = ""
    try:
        with open(cfg_file, encoding="utf-8") as f:
            saved_theme = json.load(f).get("theme", "")
    except Exception:
        pass
    check("配置", "前端整体回写不抹掉 theme", saved_theme == theme_before and bool(saved_theme),
          f"改前={theme_before} 改后={saved_theme}")
    with open(cfg_file, encoding="utf-8") as f:
        saved_dry = json.load(f).get("dry_run")
    check("演练", "演练模式已落盘为 true", saved_dry is True, saved_dry)

    # 另两个运行时开关：跳过不可用AI接口 / 自进化记录
    get_cfg = lambda expr: js(page, '(function(){var x=new XMLHttpRequest();'
                                    'x.open("GET","/api/config",false);x.send();'
                                    'var c=JSON.parse(x.responseText);c=c.config||c;'
                                    'return ' + expr + ';})()')
    for tid, key, expr in (("advSkipUnhealthy", "skip_unhealthy", "(c.ai||{}).skip_unhealthy"),
                           ("advSelfEvolve", "self_evolve", "c.self_evolve_enabled")):
        before = js(page, "document.getElementById('%s').classList.contains('on')" % tid)
        js(page, 'toggleAdvBool("%s")' % key)
        time.sleep(2.2)
        back = get_cfg(expr)
        check("开关", f"{tid} 改动存得进后端", str(bool(back)) == str(not bool(before)),
              f"点前={before} 读回={back}")
        js(page, 'toggleAdvBool("%s")' % key)
        time.sleep(2.2)
    js(page, 'toggleAdvBool("dry_run")')      # 还原
    time.sleep(2.5)
    with open(cfg_file, encoding="utf-8") as f:
        restored_dry = json.load(f).get("dry_run")
    check("演练", "再点一次能关回 false", restored_dry is False, restored_dry)

    # ── 6c. 回复补漏/主动跟进的 7 个输入框：回填 + 存回后端（红线：输入框必须真生效）──
    reply_keys = {"advOwedPerRound": "owed_per_round", "advOwedMaxAge": "owed_max_age_hours",
                  "advFollowAfter": "followup_after_hours", "advFollowGap": "followup_gap_hours",
                  "advFollowMax": "followup_max_times", "advFollowEvery": "followup_every_minutes",
                  "advPauseResumeMin": "pause_auto_resume_minutes"}
    empty = [tid for tid in reply_keys
             if not str(js(page, "document.getElementById('%s').value" % tid) or "").strip()]
    check("跟进配置", "%d 个数字框都按配置回填了" % len(reply_keys), not empty, empty or "全部有值")
    before_max = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/api/config",false);x.send();var c=JSON.parse(x.responseText);
      return ((c.config||c).reply||{}).followup_max_times;})()''')
    js(page, '''(function(){var e=document.getElementById("advFollowMax");
      e.value="3";e.dispatchEvent(new Event("change"));})()''')
    time.sleep(2.2)
    after_max = get_cfg("(c.reply||{}).followup_max_times")
    check("跟进配置", "改次数存得进后端", str(after_max) == "3", f"读回 {after_max}")
    js(page, '''(function(){var e=document.getElementById("advFollowMax");
      e.value="%s";e.dispatchEvent(new Event("change"));})()''' % before_max)
    time.sleep(2.2)
    on_before = js(page, "document.getElementById('advFollowup').classList.contains('on')")
    js(page, 'toggleAdvBool("followup_enabled")')
    time.sleep(2.2)
    fu = get_cfg("(c.reply||{}).followup_enabled")
    check("跟进配置", "主动跟进开关存得进后端", str(bool(fu)) == str(not bool(on_before)),
          f"点前={on_before} 读回={fu}")
    js(page, 'toggleAdvBool("followup_enabled")')
    time.sleep(2.2)
    check("跟进配置", "再点一次能关回原值",
          str(bool(get_cfg("(c.reply||{}).followup_enabled"))) == str(bool(on_before)),
          get_cfg("(c.reply||{}).followup_enabled"))

    # 新加的"重要消息暂停自动恢复(分钟)"：填进去要存得进后端，也得回得来
    before_pr = get_cfg("(c.reply||{}).pause_auto_resume_minutes")
    js(page, '''(function(){var e=document.getElementById("advPauseResumeMin");
      e.value="18";e.dispatchEvent(new Event("change"));})()''')
    time.sleep(2.2)
    after_pr = get_cfg("(c.reply||{}).pause_auto_resume_minutes")
    check("回复设置", "暂停自动恢复分钟数存得进后端", str(after_pr) == "18", f"读回 {after_pr}")
    js(page, '''(function(){var e=document.getElementById("advPauseResumeMin");
      e.value="%s";e.dispatchEvent(new Event("change"));})()''' % before_pr)
    time.sleep(2.2)
    check("回复设置", "改回原值也存回去",
          str(get_cfg("(c.reply||{}).pause_auto_resume_minutes")) == str(before_pr),
          get_cfg("(c.reply||{}).pause_auto_resume_minutes"))

    # ── 7. 弹窗 / Esc / 主题 / 断线横幅 ──
    js(page, 'showTemplatesModal()')
    time.sleep(1.2)
    opened = js(page, 'document.querySelectorAll(".modal-overlay").length')
    check("交互", "编辑模板弹窗能打开", int(opened or 0) >= 1, opened)
    try:
        page.actions.key_press("esc")
    except Exception:
        js(page, 'document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape"}))')
    time.sleep(1)
    closed = js(page, 'document.querySelectorAll(".modal-overlay").length')
    check("交互", "Esc 能关弹窗", int(closed or 0) == 0, closed)
    theme0 = js(page, 'document.documentElement.getAttribute("data-theme")')
    js(page, 'toggleTheme()')
    time.sleep(1.2)
    theme1 = js(page, 'document.documentElement.getAttribute("data-theme")')
    check("交互", "主题切换生效", theme0 != theme1, f"{theme0} -> {theme1}")
    js(page, 'showConnLost("测试")')
    time.sleep(0.6)
    banner = js(page, '!!document.getElementById("connLost")')
    js(page, 'hideConnLost()')
    time.sleep(0.6)
    gone = js(page, '!!document.getElementById("connLost")')
    check("交互", "断线横幅出现/消失", banner and not gone, f"{banner}/{gone}")

    # ── 8. 响应式与可达性 ──
    js(page, 'toggleTheme()')
    cols = js(page, '''(function(){var g=document.querySelector(".stats-grid");
      return getComputedStyle(g).gridTemplateColumns.split(" ").length;})()''')
    check("布局", "宽屏 4 列", int(cols) == 4, cols)
    no_title = js(page, '''(function(){var b=document.querySelectorAll("button"),n=0;
      for(var i=0;i<b.length;i++){var t=(b[i].textContent||"").trim();
      if(!t && !b[i].getAttribute("title") && !b[i].getAttribute("aria-label"))n++;}
      return n;})()''')
    check("可达性", "无文字按钮都有 title/aria-label", int(no_title or 0) == 0, no_title)
    # ── 5b. 多账号范围：切账号要带着记录、聊天、导出、按钮一起切 ──
    def api_get(path):
        raw = js(page, '''(function(){var x=new XMLHttpRequest();
          x.open("GET", %s, false); x.send();
          return x.status + "|" + x.responseText;})()''' % json.dumps(path))
        status, _, body = str(raw).partition("|")
        return int(status), json.loads(body) if body else {}

    st_a1, d_a1 = api_get("/api/greet_records?account=1")
    st_a0, d_a0 = api_get("/api/greet_records?account=0")
    st_all, d_all = api_get("/api/greet_records")
    check("多账号", "打招呼记录接口按账号分",
          st_a1 == 200 and d_a1["total"] < d_all["total"] and d_a0["total"] < d_all["total"],
          f"全部{d_all['total']} 主{d_a0['total']} 二号{d_a1['total']}")
    check("多账号", "记录里的账号字段正确",
          all(r.get("account_index") == 1 for r in d_a1["records"]) and
          all(r.get("account_index") == 0 for r in d_a0["records"]),
          set(r.get("account_index") for r in d_a1["records"]))
    check("多账号", "不再有 cookie 文件名当账号名",
          not any(str(r.get("account_name", "")).endswith(".json") for r in d_all["records"]),
          [r.get("account_name") for r in d_all["records"][:3]])

    js(page, 'document.querySelectorAll("#metricsScope .scope-chip")[2].click()')
    time.sleep(2.5)
    rows_a1 = int(js(page, 'document.querySelectorAll(".greet-table tbody tr").length') or 0)
    check("多账号", "切到账号2 后打招呼记录只剩该账号",
          rows_a1 == int(d_a1["total"]),
          f"页面{rows_a1} 接口{d_a1['total']}")   # 行模型统一后不再截到 200 行
    hint = js(page, 'document.getElementById("actionScopeLabel").textContent') or ""
    check("多账号", "操作条标明当前范围", "账号2" in hint, hint)
    labels = js(page, '''(function(){var r=[],b=["btnPauseGreet","btnPauseReply"];
      for(var i=0;i<b.length;i++){var e=document.getElementById(b[i]);
      r.push(e?e.textContent:"");} return JSON.stringify(r);})()''')
    check("多账号", "暂停按钮写着只对当前账号生效",
          all("账号2" in (x or "") for x in labels), labels)

    chats_all, dc_all = api_get("/api/reply_records/grouped")
    chats_a1, dc_a1 = api_get("/api/reply_records/grouped?account=1")
    check("多账号", "回复分组接口按账号分",
          dc_a1["total_groups"] < dc_all["total_groups"] or dc_a1["total_groups"] > 0,
          f"全部{dc_all['total_groups']} 二号{dc_a1['total_groups']}")
    check("多账号", "回复分组带账号字段",
          all(g.get("account_index") == 1 for g in dc_a1["groups"]),
          [g.get("account_index") for g in dc_a1["groups"][:5]])
    time.sleep(1.5)
    ui_chats = int(js(page, 'document.querySelectorAll(".boss-chat-item").length') or 0)
    check("多账号", "聊天列表跟着账号切",
          ui_chats == int(dc_a1["total_groups"]), f"页面{ui_chats} 接口{dc_a1['total_groups']}")

    # 只记录不真发：拦住 fetch / window.open / confirm，看前端准备往哪儿打
    js(page, '''(function(){
      window.__fired=[]; window.__opened=[]; window.__confirm=[];
      window.__of=window.fetch; window.__ow=window.open; window.__oc=window.confirm;
      window.fetch=function(u,o){window.__fired.push((o&&o.method||"GET")+" "+u);
        return Promise.resolve(new Response(JSON.stringify({status:"ok"}),
          {status:200,headers:{"Content-Type":"application/json"}}));};
      window.open=function(u){window.__opened.push(u);return null;};
      window.confirm=function(m){window.__confirm.push(m);return false;};
    })()''')
    js(page, 'pauseGreet(); resumeGreet(); pauseReply(); resumeReply();'
             ' downloadGreetRecords(); downloadReplyRecords();'
             ' loginPendingIdx=1; doConfirmLogin();')
    time.sleep(1.2)
    fired = js(page, 'JSON.stringify(window.__fired)') or []
    opened = js(page, 'JSON.stringify(window.__opened)') or []
    check("多账号", "暂停/恢复走单账号路由",
          any("POST /api/accounts/1/pause_greet" == x for x in fired) and
          any("POST /api/accounts/1/resume_reply" == x for x in fired), fired)
    check("多账号", "导出链接带账号参数",
          any("/api/export/greet_records" in u and "account=1" in u for u in opened), opened)
    check("多账号", "登录确认只发给要登录的号",
          "POST /api/accounts/1/confirm_login" in fired, fired)

    # fetch 还拦着：此时 /api/config 回来的是 {status:"ok"}，没有 accounts。
    # 界面必须保持原配置——整份赋值会把账号列表、招呼语、AI 配置一起抹掉
    js(page, '(function(){window.__accBefore=(config.accounts||[]).length;'
             ' loadConfig(); return 1;})()')
    time.sleep(1.0)
    acc_after = js(page, '(config.accounts||[]).length')
    check("配置异常响应", "接口返回没有 accounts 时不覆盖界面配置",
          acc_after == js(page, 'window.__accBefore') and acc_after >= 2,
          f"前 {js(page, 'window.__accBefore')} → 后 {acc_after}")

    js(page, 'setDataScope("all")')
    time.sleep(1.5)
    js(page, 'window.__fired = [];')
    js(page, 'pauseGreet(); confirmLoginAs(null);')
    time.sleep(1.0)
    fired_all = js(page, 'JSON.stringify(window.__fired)') or []
    check("多账号", "全部账号范围才用全局路由",
          "POST /api/pause_greet" in fired_all
          and "POST /api/confirm_login" in fired_all, fired_all)

    js(page, 'setDataScope(1)')
    time.sleep(1.2)
    # 这一步要看的是"清空确认里写的是哪个号"，而拦住的 fetch 会让
    # 切范围时的重拉拿到异常响应。先把真 fetch 放回来（confirm 钩子留着）。
    js(page, 'window.fetch = window.__of;')
    js(page, 'setDataScope(1)')
    time.sleep(1.5)
    js(page, 'clearGreetTable(); clearReplyRecords();')
    time.sleep(0.8)
    confirms = js(page, 'JSON.stringify(window.__confirm)') or []
    check("多账号", "清空前说清会不会波及其他账号",
          len(confirms) >= 2 and all("账号2" in c for c in confirms)
          and any("不受影响" in c for c in confirms), confirms[:2])
    js(page, '''(function(){window.fetch=window.__of;window.open=window.__ow;
      window.confirm=window.__oc;})()''')
    # 上面拦过 fetch，表被空响应清掉了，恢复真 fetch 后重新拉一次
    js(page, 'applyScopeToView()')
    time.sleep(2.5)

    scoped = js(page, 'JSON.stringify([inScope({account_index:0}),'
                      ' inScope({account_index:1})])')
    check("多账号", "范围外的实时行被挡掉", scoped == [False, True], scoped)
    rows_1 = int(js(page, 'document.querySelectorAll(".greet-table tbody tr").length') or 0)
    check("多账号", "恢复真接口后账号2 记录数对得上",
          rows_1 == int(d_a1["total"]), f"页面{rows_1} 接口{d_a1['total']}")

    dots = js(page, '''(function(){var d=document.querySelectorAll(".account-tab .cookie-status-dot"),r=[];
      for(var i=0;i<d.length;i++)r.push(d[i].className.replace("cookie-status-dot","").trim()+"|"+d[i].title);
      return JSON.stringify(r);})()''')
    check("多账号", "Cookie 点已自动刷新（不靠手点）",
          len(dots) >= 2 and not any(x.startswith("invalid") for x in dots), dots)
    acc_dots = js(page, '''(function(){var d=document.querySelectorAll(".account-tab .acc-status-dot"),r=[];
      for(var i=0;i<d.length;i++)r.push(d[i].className.replace("acc-status-dot","").trim()+"|"+d[i].title);
      return JSON.stringify(r);})()''')
    check("多账号", "账号状态点能说明阶段",
          all(("未启动" in x or "运行中" in x or "等你登录" in x or "初始化" in x)
              for x in acc_dots), acc_dots)

    js(page, 'setDataScope("all")')
    time.sleep(2.5)
    check("多账号", "切回全部账号后实时行不再过滤",
          js(page, 'JSON.stringify(inScope({account_index:0}))') == "true")
    rows_all_after = int(js(page, 'document.querySelectorAll(".greet-table tbody tr").length') or 0)
    check("多账号", "切回全部账号能看到所有记录",
          rows_all_after == int(d_all["total"]),
          f"页面{rows_all_after} 接口{d_all['total']}")

    # 图标尺寸与左栏简洁度：这两条都是用户 2026-10-04 直接点名的
    # （"svg 图标太小了"、"左边配置栏的文字太多了…或者把它默认隐藏起来"），
    # 只能靠真机量出来的像素说话，源码里写了 min-width 不代表渲染出来够用
    sizes = js(page, '''(function(){var s=document.querySelectorAll("svg.ki"),min=99,bad=[];
      for(var i=0;i<s.length;i++){if(!s[i].offsetParent)continue;
        var r=s[i].getBoundingClientRect();
        if(!r.width)continue;
        if(r.width<min)min=r.width;
        if(r.width<14)bad.push((s[i].getAttribute("class")||"")+"@"+Math.round(r.width));}
      return JSON.stringify([Math.round(min*10)/10,bad.slice(0,6),s.length]);})()''')
    check("图标", "看得见的图标都不小于 14px", sizes[0] >= 14 and sizes[1] == [], sizes)
    check("图标", "面板确实挂着雪碧图图标", sizes[2] > 40, f"{sizes[2]} 个 svg.ki")

    boss = js(page, '''(function(){var b=document.querySelector(".chat-header-btn");
      return JSON.stringify([!!(b&&b.querySelector("svg.ki")),
        document.body.innerText.indexOf("[图片]")<0,
        document.querySelectorAll("#bossChatMessages svg.ki").length]);})()''')
    check("BOSS视图", "刷新按钮带图标", boss[0] is True, boss)
    check("BOSS视图", "不再出现 [图片] 这类方括号占位", boss[1] is True, boss)

    side = js(page, '''(function(){
      function clipped(el){var p=el.parentElement;while(p&&p!==document.body){
        if(p.classList&&p.classList.contains("adv-section")&&!p.classList.contains("open"))return true;
        p=p.parentElement;}return false;}
      var h=document.querySelectorAll("#sideScroll .side-hint"),n=[];
      for(var i=0;i<h.length;i++){if(!h[i].offsetParent||clipped(h[i]))continue;
        var t=(h[i].textContent||"").replace(/\\s+/g,"").length;
        if(t>22)n.push(t+"|"+(h[i].textContent||"").trim().slice(0,16));}
      return JSON.stringify([n.slice(0,4),
        document.getElementById("configPreviewList").style.display,
        document.getElementById("scopeNoteBox").className]);})()''')
    check("左栏", "摊开的说明都是一行内的短句", side[0] == [], side[0])
    check("左栏", "配置预览默认收起", side[1] == "none", side[1])
    check("左栏", "按号独立清单默认收起", "open" not in side[2], side[2])
    js(page, 'toggleBlock("scopeNoteToggle","scopeNoteBox")')
    time.sleep(.6)
    opened = js(page, 'document.getElementById("scopeNoteBox").className')
    check("左栏", "点标题能展开看完整清单", "open" in opened, opened)
    # max-height 是 .3s 过渡出来的：加完 class 立刻量会量到 0（实测偶发），
    # 所以要轮询到它稳住，而不是"读一次说没高度"
    box_h = 0
    for _ in range(12):
        time.sleep(.2)
        box_h = js(page, 'Math.round(document.getElementById("scopeNoteBox")'
                         '.getBoundingClientRect().height)') or 0
        if box_h > 20:
            break
    if not box_h > 20:
        # 只报"高度 0"没法定位：把祖先链的 display/max-height 和自身 scrollHeight 打出来，
        # 才分得清是"祖先藏着"还是"点开又被谁收回去了"
        box_h = js(page, '''(function(){var el=document.getElementById("scopeNoteBox");
          var chain=[],p=el;
          for(var i=0;i<5&&p;i++){var s=getComputedStyle(p);
            chain.push((p.id||p.className).slice(0,24)+":"+s.display+"/"+s.maxHeight+"/h"+
                       Math.round(p.getBoundingClientRect().height));
            p=p.parentElement;}
          return "高"+Math.round(el.getBoundingClientRect().height)
                 +" scroll"+el.scrollHeight+" open="+el.classList.contains("open")
                 +" 链["+chain.join(" < ")+"]";})()''')
    check("左栏", "展开后清单真的占出高度", box_h > 20, box_h)
    js(page, 'toggleBlock("scopeNoteToggle","scopeNoteBox")')
    time.sleep(.6)
    check("左栏", "再点一次收回去",
          "open" not in js(page, 'document.getElementById("scopeNoteBox").className'))

    # ── 左栏两列对齐（2026-10-04 用户："你自己看你左边的配置栏乱成啥样了"）──
    # 以前标签按自然宽度排，一栏里量出 8 种控件左边缘；改成 grid 后同一容器里只能有一条竖线。
    # 按行自身的 x 分组：岗位卡片、AI 接口卡片各自带内边距，那是两套基准线，不算歪。
    align = js(page, '''(function(){
      var side=document.getElementById("sideScroll");
      if(!side)return JSON.stringify([[],0,0]);
      var rows=side.querySelectorAll(".acc-field-row,.adv-field-row"),groups={},n=0;
      for(var i=0;i<rows.length;i++){
        var row=rows[i];
        if(row.classList.contains("acc-stack"))continue;      // 标签在上、控件铺满，本来就不对齐
        var ctrl=row.querySelector(".side-form-input");        // 开关靠右是设计，不参与对齐统计
        if(!ctrl||!ctrl.offsetParent)continue;
        var rr=row.getBoundingClientRect(),cr=ctrl.getBoundingClientRect();
        if(cr.width<1)continue;
        n++;
        var k=Math.round(rr.x);
        (groups[k]=groups[k]||{})[Math.round(cr.x)]=1;
      }
      var bad=[];
      Object.keys(groups).forEach(function(k){
        var xs=Object.keys(groups[k]);
        if(xs.length>1)bad.push("行x="+k+" 控件x="+xs.join("/"));
      });
      var box=side.closest(".side");
      return JSON.stringify([bad,box.scrollWidth-box.clientWidth,n]);})()''')
    check("左栏", "同一容器里的控件都对到同一条竖线上", align[0] == [], align[0])
    check("左栏", "没有横向滚动条", align[1] <= 0, align[1])
    check("左栏", "对齐检查覆盖到的行数够多", align[2] >= 25, f"{align[2]} 行")

    veto = js(page, '''(function(){var cb=document.getElementById("aiVetoOnly");
      if(!cb)return JSON.stringify([-1,-1]);
      var row=cb.closest(".acc-field-row"),hint=row.querySelector(".side-hint");
      var r=hint.getBoundingClientRect();
      return JSON.stringify([Math.round(r.width),Math.round(r.height),
        Math.round(row.querySelector("label").getBoundingClientRect().x)]);})()''')
    check("左栏", "否决词说明不再被挤成一小坨", veto[0] >= 110 and veto[1] <= 20, veto)

    logo = js(page, '''(function(){var w=document.querySelector(".side-head .logo-wrap");
      var svg=w?w.querySelector("svg.ki use"):null;
      return JSON.stringify([!!svg,svg?svg.getAttribute("href"):"",
        !!w&&w.querySelector("svg.ki")?Math.round(w.querySelector("svg.ki").getBoundingClientRect().width):0]);})()''')
    check("图标", "左上角用的是图标库的 svg", logo[0] is True and "#i-briefcase" in logo[1], logo)
    check("图标", "logo 尺寸够看清", logo[2] >= 16, logo)

    # Excel 导出那张以前是 247x146 的宽扁透视桌，缩到 14px 只剩几道竖线
    sprite = js(page, '''(function(){var x=new XMLHttpRequest();
      x.open("GET","/static/icons/ui-sprite.svg",false);x.send();
      var m=/id="i-export"[^>]*viewBox="0 0 ([\\d.]+) ([\\d.]+)"/.exec(x.responseText);
      return m?JSON.stringify([Number(m[1]),Number(m[2]),x.responseText.length]):JSON.stringify([-1,-1,0]);})()''')
    check("图标", "导出图标是竖版画布（不是宽扁透视桌）",
          sprite[0] > 0 and sprite[0] <= sprite[1], sprite[:2])

    run = js(page, '''(function(){var s=document.getElementById("btnStartAll");
      return JSON.stringify([s.className,!!s.querySelector(".run-dot"),
        !!s.querySelector("svg.ki"),getComputedStyle(s).borderRadius,
        document.getElementById("btnStopAll").className]);})()''')
    check("运行控制", "启动是同一套胶囊样式", "btn-run" in run[0] and run[1] is True, run)
    check("运行控制", "图标没被 innerHTML 写丢", run[2] is True, run)
    check("运行控制", "胶囊圆角", float(str(run[3]).replace("px", "")) >= 12, run[3])
    check("运行控制", "停止与启动同形状（只换配色）",
          "btn-run" in run[4] and "stop" in run[4], run[4])
    # 切到运行态：只动 DOM，不发任何请求
    js(page, 'setControls(true)')
    time.sleep(.8)
    running = js(page, '''(function(){
      function shot(id){var b=document.getElementById(id);
        return [b.style.display!=="none",!!b.querySelector("svg.ki"),
                (b.querySelector(".btn-label")||b).textContent.trim()];}
      return JSON.stringify([shot("btnStopAll"),shot("btnPauseGreet"),shot("btnResumeReply")]);})()''')
    check("运行控制", "运行态下停止按钮可见且带图标", running[0][0] is True and running[0][1] is True, running[0])
    check("运行控制", "暂停/恢复按钮改文字时不吞图标",
          running[1][0] is True and running[1][1] is True and "暂停" in running[1][2], running[1])
    js(page, 'setControls(false)')
    time.sleep(.6)
    check("运行控制", "测完收回未启动态",
          js(page, 'document.getElementById("btnStopAll").style.display') == "none")

    check("主题", "首帧就是深色（不用等接口回来才转暗）",
          js(page, 'document.documentElement.getAttribute("data-theme")') == "dark")

    errs = js(page, 'JSON.stringify(window.__jsErrors||[])')
    check("稳定性", "操作过程中无 JS 报错", not errs, errs)

    page.get_screenshot(path=os.path.join(SHOTS, "final.png"))
    bad = [r for r in RESULTS if not r["ok"]]
    print(f"\n== 合计 {len(RESULTS)} 项，失败 {len(bad)} 项 ==")
    for r in bad:
        print(f"  FAIL {r['group']}/{r['name']}: {r['detail']}")
    with open(os.path.join(SHOTS, "report.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
