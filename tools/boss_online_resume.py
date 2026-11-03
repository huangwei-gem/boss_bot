#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOSS 直聘「在线简历」与「附件简历」的读写工具（按号操作）。

为什么要单独一个工具：在线简历改的是他账号本身的资料，不是投递动作，
跟打招呼/回复那条流水线无关，绝不能为了改简历把整条流水线跑起来。
所以这里只自己起浏览器、只碰简历页，一个发送按钮都不点。

用法：
    python tools/boss_online_resume.py --acct 0 --action probe      # 只读取页面结构
    python tools/boss_online_resume.py --acct 0 --action dump       # 把简历现状捞成 JSON
    python tools/boss_online_resume.py --acct 0 --action attach     # 上传附件简历
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import (  # noqa: E402
    BrowserInstance, launch_browser, login_state_of,
)

RESUME_CANDIDATES = [
    "https://www.zhipin.com/web/geek/resume",
    "https://www.zhipin.com/web/geek/resume-detail",
    "https://www.zhipin.com/web/geek/profile-resume",
]
CHAT_URL = "https://www.zhipin.com/web/geek/chat"
# --slot 填的值：换一份 profile 会话（见 open_browser 的说明）
_槽 = ""

# 每个号的在线简历/附件简历用哪份 docx（他 10-20 定的分工）
ACCOUNT_RESUME = {
    0: Path(r"C:\Users\35796\Downloads\简历备份\AI产品经理.docx"),
    1: Path(r"C:\Users\35796\Downloads\简历备份\数据分析简历-黄维.docx"),
}


def cookie_file(idx: int) -> Path:
    return ROOT / ("zhipin_cookies.json" if idx == 0 else f"zhipin_cookies_{idx}.json")


def open_browser(idx: int) -> BrowserInstance:
    """起这条工具自己的浏览器：端口和 profile 都另开一份，绝不碰面板那两台。

    10-20 抢过 9222/9223 的下场：面板一点「启动」就发现"端口上挂着有头形态的旧
    浏览器"，把他正在编辑的在线简历窗口连同我这条工具一起收掉——表现成
    "简历页自己没了 + 这个号又未登录"。Cookie 检测那条路（app.py 用 9500+idx 和
    临时 profile）已经是现成的先例，照它走。

    --slot 换一个 profile 目录名（resume_probe_0_<slot>）：11-03 现测 BOSS 的简历路由
    是按会话限流的，同一个 profile 连开三次整页加载都只回「操作失败，请刷新页面重试！」，
    换个会话就能读到正文——这不是绕过风控的招，是别再拿一个已经被限死的会话反复撞。
    """
    port = 9600 + idx
    profile = ROOT / "browser_data" / ("resume_probe_%s%s" % (idx, ("_" + _槽) if _槽 else ""))
    profile.mkdir(parents=True, exist_ok=True)
    instance = launch_browser(headless=False, port=port,
                              user_data_dir=str(profile), background=True)
    cf = cookie_file(idx)
    if cf.exists():
        if not instance.load_cookies(str(cf)):
            print(f"⚠️ 账号{idx + 1} Cookie 注入失败：{cf}")
    return instance


def 登录态(instance: BrowserInstance) -> str:
    url = instance.url or ""
    state = login_state_of(url, bool(instance.cookies()))
    return f"{state} url={url[:110]}"


def 找简历入口(instance: BrowserInstance) -> list:
    """从聊天页顶部导航里捞真实存在的简历链接，不靠猜 URL。"""
    js = """
    (() => {
      const out = [];
      for (const a of document.querySelectorAll('a')) {
        const t = (a.innerText || '').trim();
        const h = a.getAttribute('href') || '';
        if (!t && !h) continue;
        if (t.includes('简历') || h.includes('resume')) out.push(t + ' -> ' + h);
      }
      return out;
    })()
    """
    r = instance.run_js(js, as_expr=True)
    return r if isinstance(r, list) else [str(r)]


def 页面骨架(instance: BrowserInstance) -> dict:
    js = """
    (() => {
      const txt = (e) => (e.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
      return {
        url: location.href,
        title: document.title,
        heads: [...document.querySelectorAll('h1,h2,h3,h4,.title,[class*=title]')]
            .map(e => txt(e)).filter(Boolean).slice(0, 60),
        buttons: [...document.querySelectorAll('button,a[role=button],[class*=btn]')]
            .map(e => txt(e)).filter(Boolean).slice(0, 80),
        editables: [...document.querySelectorAll('textarea,[contenteditable=true],input')]
            .map(e => ({tag: e.tagName, type: e.type || '', name: e.name || '',
                        ph: e.placeholder || '', cls: (e.className || '').toString().slice(0, 60),
                        val: (e.value || e.innerText || '').slice(0, 60)})).slice(0, 60),
        fileInputs: [...document.querySelectorAll('input[type=file]')]
            .map(e => ({name: e.name || '', accept: e.accept || '',
                        cls: (e.className || '').toString().slice(0, 60)})),
      };
    })()
    """
    r = instance.run_js(js, as_expr=True)
    return r if isinstance(r, dict) else {"raw": str(r)[:2000]}


def action_probe(idx: int) -> int:
    instance = open_browser(idx)
    print(f"账号{idx + 1} 浏览器: {登录态(instance)}")
    instance.get(CHAT_URL)
    time.sleep(6)
    print(f"聊天页: {登录态(instance)}")
    print("简历入口:", json.dumps(找简历入口(instance), ensure_ascii=False)[:1200])
    for u in RESUME_CANDIDATES:
        instance.get(u)
        time.sleep(5)
        here = instance.url or ""
        if "resume" in here or "profile" in here:
            print(f"✅ {u} → 落在 {here}")
            print(json.dumps(页面骨架(instance), ensure_ascii=False, indent=1)[:6000])
            return 0
        print(f"❌ {u} → 被带到 {here[:100]}")
    return 1


DUMP_JS = """
(() => {
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').replace(/[ \\t]+/g, ' ').trim();
  // 板块 = 卡片容器。BOSS 这一页每个板块一张卡，卡头是板块名，卡里带「编辑/添加」按钮。
  const cards = [];
  for (const el of document.querySelectorAll('div,section')) {
    const t = clean(el.innerText);
    if (!t || t.length > 4000) continue;
    const hasEdit = [...el.querySelectorAll('span,a,button')].some(
        b => /^(编辑|添加|修改|新增)/.test(clean(b.innerText)));
    const kids = [...el.children].filter(c => clean(c.innerText).length > 40);
    if (!hasEdit || kids.length === 0) continue;
    cards.push({
      cls: (el.className || '').toString().slice(0, 80),
      text: t.slice(0, 1800),
      editLabels: [...el.querySelectorAll('span,a,button')]
          .map(b => clean(b.innerText)).filter(x => /^(编辑|添加|修改|新增)/.test(x)).slice(0, 8),
    });
  }
  return {
    url: location.href,
    // 只留最小的那块：外层容器会把所有板块吞在一起，最小的才是单块
    cards: cards.sort((a, b) => a.text.length - b.text.length).slice(0, 24),
    body: clean(document.body.innerText).slice(0, 6000),
  };
})()
"""


def _等简历渲染(instance, 上限秒: int = 40):
    """BOSS 这一页是 SPA：固定等 6 秒经常只捞到外壳（板块 0 块，正文只剩顶部导航）。

    2026-10-27 实测就是这样——登录态明明在（页面里有「黄维」「简历 new」），
    dump 出来 cards=[]。改成有界轮询，等到出板块为止，等不够就照实报"没渲染出来"。
    """
    开 = time.time()
    数据 = None
    while time.time() - 开 < 上限秒:
        数据 = instance.run_js(DUMP_JS, as_expr=True)
        if (数据 or {}).get("cards"):
            return 数据, time.time() - 开
        time.sleep(2)
    return 数据, time.time() - 开


def action_dump(idx: int) -> int:
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    time.sleep(6)
    print(f"账号{idx + 1} 在线简历: {登录态(instance)}")
    data, 等了 = _等简历渲染(instance)
    out = ROOT / "data" / f"resume_dump_account_{idx}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    板块 = len((data or {}).get("cards") or [])
    print(f"已落盘 {out}（⏱ 等渲染 {等了:.0f} 秒，板块 {板块} 块）")
    for i, c in enumerate((data or {}).get("cards") or []):
        print(f"── 板块{i} {c['editLabels']}  [{c['cls'][:40]}]")
        print("   ", c["text"].replace("\n", " ⏎ ")[:700])
    return 0


SECTIONS_JS = """
(() => {
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const out = [];
  for (const el of document.querySelectorAll('div[class*="resume-item"]')) {
    const t = clean(el.innerText);
    if (!t) continue;
    out.push({
      cls: (el.className || '').toString().slice(0, 70),
      text: t.slice(0, 3000),
      // 按 DOM 顺序记按钮下标，后面就是靠这个下标点「编辑」
      btns: [...el.querySelectorAll('span,a,button')].map(b => clean(b.innerText)).slice(0, 20),
    });
  }
  return out;
})()
"""


def action_sections(idx: int) -> int:
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    time.sleep(6)
    secs = instance.run_js(SECTIONS_JS, as_expr=True) or []
    print(f"账号{idx + 1} 在线简历: {登录态(instance)}")
    out = ROOT / "data" / f"resume_sections_account_{idx}.json"
    out.write_text(json.dumps(secs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已落盘 {out}（{len(secs)} 块）")
    for i, s in enumerate(secs):
        if "resume-item" not in s["cls"]:
            continue
        print(f"[{i}] {s['cls']}")
        print("    ", s["text"].replace("\n", " ⏎ ")[:900])
    return 0


OPEN_EDIT_JS = """
(() => {
  const want = %s, which = %s;
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const shown = (e) => { const r = e.getBoundingClientRect();
                         return e.offsetParent !== null && r.width > 2 && r.height > 2; };
  const seen = [];
  for (const el of document.querySelectorAll('div[class*="resume-item"]')) {
    if (!clean(el.innerText).startsWith(want)) continue;
    const btns = [...el.querySelectorAll('span,a,button,div[class*=edit]')].filter(
        b => /^(编辑|添加|新增|修改)$/.test(clean(b.innerText)));
    btns.forEach((b, i) => seen.push(want + '#' + i + (shown(b) ? '可见' : '隐藏')));
    const ok = btns.filter(shown);
    if (ok[which]) { ok[which].click();
      return 'clicked ' + want + ' 可见第' + which + '个（全部：' + seen.join(',') + '）'; }
  }
  return 'NOT FOUND/全隐藏 [' + seen.join(',') + ']';
})()
"""


def action_open_edit(idx: int, section: str, which: int) -> int:
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    time.sleep(6)
    print("板块:", section, instance.run_js(
        OPEN_EDIT_JS % (json.dumps(section, ensure_ascii=False), int(which)),
        as_expr=True))
    time.sleep(2.5)
    print("登录态:", 登录态(instance))
    shot = ROOT / "data" / f"resume_edit_account_{idx}.png"
    try:
        instance.page.get_screenshot(str(shot))
        print("截图:", shot)
    except Exception as e:
        print("截图失败:", e)
    print("点完之后这块板块变成:", json.dumps(instance.run_js(
        '(() => {for (const el of document.querySelectorAll(\'div[class*="resume-item"]\'))'
        ' { const t=(el.innerText||\'\').trim(); if (t.startsWith(%s))'
        ' return {html: el.outerHTML.slice(0,1500), text: t.slice(0,400)}; } return null; })()'
        % json.dumps(section, ensure_ascii=False), as_expr=True), ensure_ascii=False)[:2200])
    print("浮层:", json.dumps(instance.run_js(OVERLAY_JS, as_expr=True),
                             ensure_ascii=False)[:1800])
    return 0

DIALOG_JS = """
(() => {
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const dlgs = [...document.querySelectorAll('div')]
      .filter(d => /dialog|modal|popup|drawer/i.test((d.className || '').toString()))
      .filter(d => d.offsetParent !== null && clean(d.innerText).length > 4);
  const d = dlgs.sort((a, b) => a.innerText.length - b.innerText.length)[0];
  if (!d) return {found: false, url: location.href};
  return {
    found: true,
    cls: (d.className || '').toString().slice(0, 90),
    text: clean(d.innerText).slice(0, 1200),
    fields: [...d.querySelectorAll('input,textarea,[contenteditable=true]')].map((e, i) => ({
      i, tag: e.tagName, type: e.type || '', ph: e.placeholder || '',
      label: clean((e.closest('div') || {}).innerText).slice(0, 50),
      val: clean(e.value || e.innerText).slice(0, 200),
      ro: e.readOnly || e.getAttribute('readonly'),
    })).slice(0, 30),
    btns: [...d.querySelectorAll('span,a,button')].map(b => clean(b.innerText))
          .filter(Boolean).slice(0, 25),
  };
})()
"""


OVERLAY_JS = """
(() => {
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  // 点完「编辑」之后到底是什么形态不确定：可能是弹层，也可能就地变成可写区。
  // 按「fixed/absolute 且盖住半屏」找浮层，再兜底列出全页可写控件。
  const layers = [];
  for (const el of document.querySelectorAll('div')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    if (!/fixed|absolute/.test(cs.position)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < innerWidth * 0.3 || r.height < innerHeight * 0.3) continue;
    if (parseInt(cs.zIndex || 0) < 10) continue;
    const t = clean(el.innerText);
    if (t.length < 6) continue;
    layers.push({cls: (el.className || '').toString().slice(0, 90),
                 z: cs.zIndex, text: t.slice(0, 900),
                 fields: [...el.querySelectorAll('input,textarea,[contenteditable=true]')]
                     .map((e, i) => ({i, tag: e.tagName, type: e.type || '',
                          ph: e.placeholder || '', ro: !!e.readOnly,
                          val: clean(e.value || e.innerText).slice(0, 160),
                          near: clean(((e.parentElement || {}).innerText) || '').slice(0, 40)})).slice(0, 30),
                 btns: [...el.querySelectorAll('span,a,button')].map(b => clean(b.innerText))
                       .filter(Boolean).slice(0, 30)});
  }
  layers.sort((a, b) => a.text.length - b.text.length);
  return {url: location.href, layers: layers.slice(0, 3)};
})()
"""


KA_JS = """
(() => {
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const seen = {};
  for (const e of document.querySelectorAll('[ka]')) {
    const k = e.getAttribute('ka');
    const r = e.getBoundingClientRect();
    const key = k + '|' + clean(e.innerText).slice(0, 12);
    if (seen[key]) continue;
    seen[key] = 1;
  }
  return Object.keys(seen);
})()
"""


def action_ka(idx: int) -> int:
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    time.sleep(6)
    for k in instance.run_js(KA_JS, as_expr=True) or []:
        print(k)
    return 0


RECT_JS = """
(() => {
  const ka = %s;
  const a = document.querySelector('a[ka="' + ka + '"]');
  if (!a) return null;
  // 「编辑」这一列要悬停在整个板块上才会显示，所以先拿板块的可见坐标去移鼠标
  const host = a.closest('div[class*="resume-item"]') || a.parentElement;
  const r = host.getBoundingClientRect();
  return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + Math.min(r.height / 2, 60)),
          w: Math.round(r.width), h: Math.round(r.height)};
})()
"""


def 当前可写控件(instance) -> str:
    return json.dumps(instance.run_js(
        """
        (() => {
          const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
          return [...document.querySelectorAll('input,textarea,[contenteditable=true]')]
            .filter(e => { const r = e.getBoundingClientRect();
                           return r.width > 2 && r.height > 2; })
            .map(e => ({tag: e.tagName, type: e.type || '', ph: e.placeholder || '',
                        cls: (e.className || '').toString().slice(0, 50),
                        val: clean(e.value || e.innerText).slice(0, 120),
                        near: clean(((e.closest('div') || {}).innerText) || '').slice(0, 40)}))
            .slice(0, 40);
        })()
        """, as_expr=True), ensure_ascii=False)[:3500]


def action_clickedit(idx: int, ka: str) -> int:
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    time.sleep(6)
    browser = instance.page.browser          # Windows 下 instance.browser 是 None，页面上挂的才是浏览器
    tabs_before = set(browser.tab_ids)
    rect = instance.run_js(RECT_JS % json.dumps(ka), as_expr=True)
    if not rect:
        print(f"页面上没有 ka={ka}")
        return 1
    print("板块矩形:", rect)
    instance.page.actions.move_to((rect["x"], rect["y"]))
    time.sleep(0.8)
    print("悬停后点:", instance.run_js(
        '(() => {const a=document.querySelector(\'a[ka="%s"]\');'
        ' if(!a) return "没了"; const r=a.getBoundingClientRect();'
        ' a.click(); return "w="+Math.round(r.width)+" h="+Math.round(r.height);})()' % ka,
        as_expr=True))
    time.sleep(3)
    new = [t for t in browser.tab_ids if t not in tabs_before]
    print("新标签页:", new, " 当前 url:", instance.url)
    shot = ROOT / "data" / f"resume_edit_account_{idx}.png"
    try:
        instance.page.get_screenshot(str(shot))
        print("截图:", shot)
    except Exception as e:
        print("截图失败:", e)
    print("可写控件:", 当前可写控件(instance))
    return 0


def _板块矩形(instance, sid: str) -> dict:
    return instance.run_js(
        """(() => {const e=document.getElementById(%s); if(!e) return null;
        const r=e.getBoundingClientRect();
        return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + Math.min(r.height/2, 60)),
                h: Math.round(r.height)};})()""" % json.dumps(sid), as_expr=True)


打开编辑 = """
(() => {
  const sid = %s;
  const host = document.getElementById(sid);
  if (!host) return 'NO SECTION ' + sid;
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const btns = [...host.querySelectorAll('a[ka^="user-resume-edit"],a[ka^="user-resume-add"],'
                                        +'a[ka^="user-resume-del"],span,a,button')].filter(
      b => /^(编辑|添加|新增|修改)$/.test(clean(b.innerText)));
  const shown = btns.filter(b => { const r = b.getBoundingClientRect();
                                   return r.width > 2 && r.height > 2; });
  const pick = shown[0] || btns[0];
  if (!pick) return 'NO BUTTON in ' + sid;
  pick.click();
  return 'clicked ' + (pick.getAttribute('ka') || clean(pick.innerText));
})()
"""

打开第几个编辑 = """
(() => {
  const sid = __SID__, 第几 = __N__;
  const host = document.getElementById(sid);
  if (!host) return 'NO SECTION ' + sid;
  // 一个板块里每条经历各挂一个「编辑」，第几 按 DOM 顺序数（0 起）。
  // 没 hover 时这些 a 的 rect 是 0×0（DrissionPage 因此报 NoRectError），
  // 但它们一直在 DOM 里，JS 的 .click() 照样打得开那张抽屉。
  const 件 = [...host.querySelectorAll('a[ka^="user-resume-edit"]')];
  if (!件.length) return 'NO EDIT in ' + sid + '（共 0 个）';
  if (第几 >= 件.length) return 'NO EDIT 第' + 第几 + '个（共 ' + 件.length + ' 个）';
  件[第几].click();
  return 'clicked 第' + 第几 + '个/' + 件.length + ' ' + (件[第几].getAttribute('ka') || '');
})()
"""

列字段 = """
(() => {
  const sid = %s;
  const host = document.getElementById(sid) || document;
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  return {
    url: location.href,
    fields: [...host.querySelectorAll('input,textarea,[contenteditable=true]')]
        .filter(vis).map((e, i) => ({i, tag: e.tagName, type: e.type || '',
            ph: e.placeholder || '', ro: !!e.readOnly, dis: !!e.disabled,
            val: clean(e.value || e.innerText).slice(0, 140),
            near: clean(((e.closest('div') || {}).innerText) || '').slice(0, 40)})),
    btns: [...host.querySelectorAll('span,a,button')].map(b => clean(b.innerText))
          .filter(Boolean).slice(0, 30),
    text: clean(host.innerText).slice(0, 700),
  };
})()
"""

# Vue 受控组件只认原生 setter + input 事件；直接改 .value 会在下一次渲染被冲掉
赋值 = """
(() => {
  const sid = %s, vals = %s;
  const host = document.getElementById(sid) || document;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const fields = [...host.querySelectorAll('input,textarea,[contenteditable=true]')].filter(vis);
  const done = [];
  for (const [k, v] of Object.entries(vals)) {
    const e = fields[Number(k)];
    if (!e) { done.push('缺字段' + k); continue; }
    e.focus();
    if (e.isContentEditable) {
      e.innerText = v;
    } else {
      const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
      setter.call(e, '');
      e.dispatchEvent(new Event('input', {bubbles: true}));
      setter.call(e, v);
    }
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
    e.dispatchEvent(new Event('blur', {bubbles: true}));
    done.push(k + '=' + (e.value || e.innerText || '').length + '字');
  }
  return done.join(', ');
})()
"""

点按钮 = """
(() => {
  const sid = %s, label = %s;
  const host = (sid && document.getElementById(sid)) || document;
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const btns = [...host.querySelectorAll('span,a,button')].filter(
      b => clean(b.innerText) === label || new RegExp('^' + label + '$').test(clean(b.innerText)));
  const shown = btns.filter(b => { const r = b.getBoundingClientRect();
                                   return r.width > 2 && r.height > 2; });
  const pick = shown[0] || btns[0];
  if (!pick) return 'NO ' + label + ' 按钮（可见按钮：'
      + btns.map(b => clean(b.innerText)).join('/') + '）';
  pick.click();
  return 'clicked ' + label;
})()
"""


按ka开 = """
(() => {
  const ka = %s;
  const e = document.querySelector('[ka="' + ka + '"]');
  if (!e) return 'NO KA ' + ka;
  e.click();
  return 'clicked ' + ka;
})()
"""


def action_fill(idx: int, sid: str, values: dict, save: str,
                inspect_only: bool, ka: str = "") -> int:
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    time.sleep(6)
    js = lambda tpl, arg: instance.run_js(tpl % json.dumps(arg), as_expr=True)
    anchor = ka or sid
    rect = _板块矩形(instance, sid) if sid else None
    if ka and not rect:
        # 自定义添加那排按钮（资格证书/专业技能）不在带 id 的板块里，只能按 ka 开
        rect = instance.run_js(
            """(() => {const e=document.querySelector('[ka=' + %s + ']');
            if(!e) return null; const r=e.getBoundingClientRect();
            return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};})()"""
            % json.dumps(ka), as_expr=True)
    if not rect:
        print(f"页面上没有板块 #{sid} / ka={ka}")
        return 1
    instance.page.actions.move_to((rect["x"], rect["y"]))
    time.sleep(0.8)
    print("开编辑:", instance.run_js(按ka开 % json.dumps(ka), as_expr=True) if ka
          else js(打开编辑, sid))
    扫描域 = "" if ka else sid      # ka 开出来的表单不一定落在那个板块里，按整页找
    time.sleep(2)
    info = instance.run_js(列字段 % json.dumps(扫描域), as_expr=True)
    print("字段:", json.dumps(info, ensure_ascii=False)[:3000])
    if inspect_only:
        return 0
    if values:
        print("赋值:", instance.run_js(
            赋值 % (json.dumps(扫描域), json.dumps(values, ensure_ascii=False)), as_expr=True))
        time.sleep(1)
        print("赋值后:", json.dumps(instance.run_js(列字段 % json.dumps(扫描域), as_expr=True),
                                   ensure_ascii=False)[:1500])
    if save:
        print("保存:", instance.run_js(
            点按钮 % (json.dumps(扫描域), json.dumps(save)), as_expr=True))
        time.sleep(3)
        print("回读:", json.dumps(instance.run_js(列字段 % json.dumps(扫描域), as_expr=True),
                                  ensure_ascii=False)[:1500])
    return 0


def action_ids(idx: int) -> int:
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    time.sleep(6)
    print(json.dumps(instance.run_js(
        """
        (() => [...document.querySelectorAll('div[class*="resume-item"]')]
          .map(e => ({id: e.id, cls: (e.className||'').toString().slice(0,58),
                      head: (e.innerText||'').trim().split(String.fromCharCode(10))[0].slice(0,22),
                      kas: [...e.querySelectorAll('[ka]')].map(x => x.getAttribute('ka'))
                           .filter((v,i,a) => a.indexOf(v) === i)})))()
        """, as_expr=True), ensure_ascii=False, indent=1))
    return 0


附件区 = """
(() => {
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const side = document.querySelector('.user-sider') || document;
  return {
    url: location.href,
    text: clean(side.innerText).slice(0, 900),
    fileInputs: [...document.querySelectorAll('input[type=file]')]
        .map((e, i) => ({i, cls: (e.className||'').toString().slice(0,40),
                         accept: (e.getAttribute('accept')||'').slice(0,60),
                         w: Math.round(e.getBoundingClientRect().width)})),
  };
})()
"""

# 选完文件后那张确认框：文案在版本之间换过（老的是「确定添加」，11-03 现测是「确定」）。
# 只在这个框里找，别拿整页的「确定」——同一页上还挂着「发送简历」「确认提交」那两个
# 是会把简历发给 HR 的，这条工具一个发送按钮都不许碰。
点上传确认 = """
(() => {
  const 名 = %s;
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const 框住 = (b) => {
    let e = b;
    for (let i = 0; i < 8 && e; i++) {
      e = e.parentElement;
      if (e && /dialog|modal|popup|preview|mask/i.test((e.className || '') + '')) return e;
    }
    return b.closest('.dialog-wrap, .common-dialog, [class*="modal"]') || document.body;
  };
  const 候选 = [...document.querySelectorAll('span,a,button')]
      .filter(b => /^(确定添加|确定|确认)$/.test(clean(b.innerText)) && vis(b));
  const 命中 = 候选.find(b => clean(框住(b).innerText).includes(名))
            || 候选.find(b => /上传|附件|预览/.test(clean(框住(b).innerText)));
  if (!命中) return 'NO 上传确认按钮（可见确定类：' + 候选.map(clean).join('/') + '）';
  const 现场 = clean(框住(命中).innerText).slice(0, 180);
  命中.click();
  return 'clicked ' + clean(命中.innerText) + ' 框内=' + 现场;
})()
"""


开附件口 = """
(() => {
  const clean = (s) => (s || '').replace(/\\u00a0/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  // 侧栏「附件管理」标题右边那个 + 号是常驻入口；顶栏的「附件上传」藏在悬停菜单里
  const side = [...document.querySelectorAll('div,section')].filter(
      e => clean(e.innerText).startsWith('附件管理') && clean(e.innerText).length < 200)[0];
  if (side) {
    // 侧栏标题右边那排图标里，operate-item-resume 才是「上传附件简历」
    const icon = side.querySelector('.operate-item-resume')
              || side.querySelector('[class*="operate-item-resume"]');
    if (icon) {
      const item = icon.closest('.operate-list-item') || icon;
      item.click(); icon.click();
      return 'clicked operate-item-resume';
    }
    return 'side 没找到加号：' + [...side.querySelectorAll('[class]')]
        .map(e => (e.className || '').toString()).filter(Boolean).slice(0, 12).join(',');
  }
  return '没有附件管理侧栏';
})()
"""


def action_attach(idx: int, path: str, step: str) -> int:
    """上传附件简历。只走「附件上传」这一条入口，绝不碰任何「发送」按钮。"""
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    time.sleep(6)
    print("上传前:", json.dumps(instance.run_js(附件区, as_expr=True), ensure_ascii=False)[:1200])
    if step == "peek":
        return 0
    print("开口:", instance.run_js(开附件口, as_expr=True))
    可见框 = """(() => {const out = [];
      [...document.querySelectorAll('input[type=file]')].forEach((e, i) => {
        const a = e.getAttribute('accept') || '';
        const r = e.getBoundingClientRect();
        if ((a.includes('word') || a.includes('.docx')) && r.width > 0) out.push(i);
      });
      return out;})()"""
    idxs = []
    # 弹层要 2~4 秒才挂出来；DrissionPage 的 rect 读不到这个框，只能用页面里的 JS 量
    for _ in range(30):
        time.sleep(0.6)
        idxs = instance.run_js(可见框, as_expr=True) or []
        if idxs:
            break
    if not idxs:
        见 = instance.run_js(
            """(() => [...document.querySelectorAll('input[type=file]')]
               .map((e,i) => i + ':w=' + Math.round(e.getBoundingClientRect().width)
                 + ':accept=' + (e.getAttribute('accept')||'').slice(0,24)))()""",
            as_expr=True)
        print("轮询 18 秒没等到可见的 docx 文件框；页面上的文件框:", 见)
        return 1
    inputs = instance.eles('css:input[type=file]', timeout=4) or []
    target = inputs[int(idxs[0])]
    print(f"用第 {idxs[0]} 个文件框（可见候选：{idxs}）")
    target.input(path)
    print("已选文件:", path)
    # 选完文件只是进了预览，要点框里那个确认才会真的进附件管理。
    # 文案会变（老版「确定添加」，11-03 现测「确定」），所以按"这个框里的确定类按钮"找，
    # 而且只在框内找：同页还有「发送简历」「确认提交」，那条工具一个发送按钮都不碰。
    # 框不是秒出：11-03 传 794KB 那份，8 秒后按钮还是 w=0，得等它自己弹出来（最多 45 秒）。
    名 = Path(path).name
    点了 = ""
    for 第几 in range(30):
        time.sleep(1.5 if 第几 else 4)
        回 = instance.run_js(点上传确认 % json.dumps(名), as_expr=True)
        if isinstance(回, str) and 回.startswith("clicked"):
            点了 = 回
            print("确认(等 %s 次):" % (第几 + 1), 回[:220])
            break
    if not 点了:
        print("⚠️ 轮询 45 秒没等到可点的上传确认按钮:", 回)
    # 点完立刻读会拿到上一刻：轮询到「文件（3/3）」或 24 秒为止
    for _ in range(20):
        time.sleep(1.2)
        现 = (instance.run_js(附件区, as_expr=True) or {}).get("text") or ""
        if "3/3" in 现:
            break
    shot = ROOT / "data" / f"resume_attach_account_{idx}.png"
    try:
        instance.page.get_screenshot(str(shot))
        print("截图:", shot)
    except Exception as e:
        print("截图失败:", e)
    print("上传后:", json.dumps(instance.run_js(附件区, as_expr=True), ensure_ascii=False)[:1500])
    print("当前可点:", json.dumps(instance.run_js(
        """(() => {const clean=(s)=>(s||'').trim();
        return [...document.querySelectorAll('span,a,button')].map(b=>clean(b.innerText))
          .filter(t=>t && t.length<14 && /确定|完成|上传|保存|取消|发送|替换|确认/.test(t));})()""",
        as_expr=True), ensure_ascii=False)[:900])
    return 0


# 面板里的格子（行业那一格、公司联想那一条）常常在视口外，DrissionPage 的
# scroll_to_element 在这一页会超时（实测），所以先把目标 JS 滚进视口再真点。
_滚动到可见 = """(() => {
  let e = document.querySelector('[data-orca-tag=hit]');
  if (!e) e = document.querySelector(__CSS__);
  if (e) e.scrollIntoView({block: 'center'});
})()"""

聚焦字段 = """
(() => {
  const sid = %s, n = %s;
  const host = document.getElementById(sid) || document;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const fields = [...host.querySelectorAll('input,textarea,[contenteditable=true]')].filter(vis);
  const e = fields[n];
  if (!e) return 'NO FIELD ' + n + '（可见字段共 ' + fields.length + '）';
  e.scrollIntoView({block: 'center'});
  e.click(); e.focus();
  return 'focused ' + n + ' ph=' + (e.placeholder || '');
})()
"""


# 按标记（ka / placeholder 片段 / 表单标签片段）赋值，比按可见字段下标稳：
# 下标会随页面上还有几个可见输入框而漂，标签不会。
# 这里用 __SPECS__ 占位再 replace，不用 % 格式化——简历正文里有 "提升 30%" 这种
# 百分号，`模板 % json` 会当场 ValueError: unsupported format character。
读字段 = """
(() => {
  const sp = __SPEC__;
  const host = (sp.sid && document.getElementById(sp.sid)) || document;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const all = [...host.querySelectorAll('input,textarea,[contenteditable=true]')].filter(vis);
  const 值Of = (e) => (e.value !== undefined && e.value !== '' ? e.value : (e.innerText || ''));
  const lblOf = (e) => { const p = e.closest('div[class*=form-item]') || e.parentElement;
                         return ((p && p.innerText) || '').trim(); };
  let hits = [];
  if (sp.ph) hits = all.filter((x) => (x.placeholder || '').includes(sp.ph));
  if (sp.lbl) hits = all.filter((x) => lblOf(x).includes(sp.lbl));
  if (sp.ka) hits = all.filter((x) => x.getAttribute('ka') === sp.ka);
  // 含：按"这一格里正写着这段话"定位。抽屉里同名的框不止一个（教育经历既有
  // 「专业排名」又有「在校担任职务」），拿占位符猜就会改错格（11-03 实测：
  // 占位符命中的那一格是担任职务，专业排名那句在别处）。
  if (sp.含) hits = all.filter((x) => 值Of(x).includes(sp.含));
  if (!hits.length) return {命中: 0, 说明: '没找到这一格', 全部: all.map((e) => (e.placeholder || e.tagName))};
  // 全文回显，不截断：要照原样改一句话（比如只删掉「专业排名：前1%」那一行），
  // 截到 140 字就只能靠猜，猜错就是把他的经历抹掉一半。
  return {命中: hits.length,
          第几: hits.map((e, i) => i),
          占位: hits.map((e) => e.placeholder || ''),
          值: hits.map(值Of)};
})()
"""


改文 = """
(() => {
  const sp = __SPEC__;
  const host = (sp.sid && document.getElementById(sp.sid)) || document;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const all = [...host.querySelectorAll('input,textarea,[contenteditable=true]')].filter(vis);
  const 值Of = (e) => (e.value !== undefined && e.value !== '' ? e.value : (e.innerText || ''));
  const lblOf = (e) => { const p = e.closest('div[class*=form-item]') || e.parentElement;
                         return ((p && p.innerText) || '').trim(); };
  let e = null;
  if (sp.ka) e = all.find((x) => x.getAttribute('ka') === sp.ka);
  if (!e && sp.ph) e = all.find((x) => (x.placeholder || '').includes(sp.ph));
  if (!e && sp.lbl) e = all.find((x) => lblOf(x).includes(sp.lbl));
  if (!e && sp.含) e = all.find((x) => 值Of(x).includes(sp.含));
  if (!e) return {命中: 0, 说明: '没找到这一格', 全部: all.map((x) => x.placeholder || x.tagName)};
  const 原 = (e.value !== undefined && e.value !== '') ? e.value : (e.innerText || '');
  const 账 = [];
  let 新 = 原;
  for (const p of (sp.pairs || [])) {
    const 条 = p.定则 ? new RegExp(p.找, p.志 || 'g') : null;
    const 前 = 新;
    新 = 条 ? 新.replace(条, p.换) : 新.split(p.找).join(p.换);
    账.push((p.名 || p.找) + (新 !== 前 ? '：改了' : '：没命中'));
  }
  if (新 !== 原) {
    e.focus();
    if (e.isContentEditable) { e.innerText = 新; }
    else {
      const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
      setter.call(e, ''); e.dispatchEvent(new Event('input', {bubbles: true}));
      setter.call(e, 新);
    }
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
  }
  // 只报"改了几处 + 改完的长度"，正文不整段回显：这是他的简历，日志里留全文没意义
  return {命中: 1, 长度: [原.length, 新.length], 逐条: 账,
          尾: 新.slice(Math.max(0, 新.length - 60))};
})()
"""


按标记赋值 = """
(() => {
  const specs = __SPECS__;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const all = [...document.querySelectorAll('input,textarea,[contenteditable=true]')].filter(vis);
  const lblOf = (e) => { const p = e.closest('div[class*=form-item]') || e.parentElement;
                         return ((p && p.innerText) || '').trim(); };
  const write = (e, v) => {
    e.focus();
    if (e.isContentEditable) { e.innerText = v; }
    else {
      const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
      setter.call(e, '');
      e.dispatchEvent(new Event('input', {bubbles: true}));
      setter.call(e, v);
    }
    e.dispatchEvent(new Event('input', {bubbles: true}));
    e.dispatchEvent(new Event('change', {bubbles: true}));
    return ((e.value || e.innerText) || '').length;
  };
  const out = [];
  for (const sp of specs) {
    let e = null;
    if (sp.ka) e = all.find((x) => x.getAttribute('ka') === sp.ka);
    if (!e && sp.ph) e = all.find((x) => (x.placeholder || '').includes(sp.ph));
    if (!e && sp.lbl) e = all.find((x) => lblOf(x).includes(sp.lbl));
    if (!e) { out.push('找不到 ' + JSON.stringify(sp)); continue; }
    out.push((sp.lbl || sp.ph || sp.ka) + '=' + write(e, sp.value) + '字');
  }
  return out.join(', ');
})()
"""

# nth：同名的第几个。在职时间那一排「开始/结束」两个框 placeholder 一模一样
# （都是"选择在职时间"），只按 placeholder 找永远只能点开头那个。
矩形按标记 = """
(() => {
  const sp = __SPEC__;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const lblOf = (e) => { const p = e.closest('div[class*=form-item]') || e.parentElement;
                         return ((p && p.innerText) || '').trim(); };
  const all = [...document.querySelectorAll(sp.any ? 'input,textarea,div,span,a' : 'input,textarea,[contenteditable=true]')].filter(vis);
  let hit = [];
  if (sp.ka) hit = all.filter((x) => x.getAttribute('ka') === sp.ka);
  if (!hit.length && sp.ph) hit = all.filter((x) => (x.placeholder || '').includes(sp.ph));
  if (!hit.length && sp.lbl) hit = all.filter((x) => lblOf(x).includes(sp.lbl));
  if (!hit.length) return {ok: false, 原因: '找不到', 可见控件: all.length};
  const 中 = hit[Math.min(Number(sp.nth || 0), hit.length - 1)];
  // 先滚进视口再量：视口外的框量出来的 y 在屏幕外，move_to 点的是空气（实测敲字全丢）
  中.scrollIntoView({block: 'center'});
  const r = 中.getBoundingClientRect();
  return {ok: true, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
          命中: hit.length, 标签: lblOf(中).slice(0, 20)};
})()
"""

点开按标记 = """
(() => {
  const sp = __SPEC__;
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const all = [...document.querySelectorAll('input,textarea,[contenteditable=true]')].filter(vis);
  const lblOf = (e) => { const p = e.closest('div[class*=form-item]') || e.parentElement;
                         return ((p && p.innerText) || '').trim(); };
  let hit = [];
  if (sp.ka) hit = all.filter((x) => x.getAttribute('ka') === sp.ka);
  if (!hit.length && sp.ph) hit = all.filter((x) => (x.placeholder || '').includes(sp.ph));
  if (!hit.length && sp.lbl) hit = all.filter((x) => lblOf(x).includes(sp.lbl));
  if (!hit.length) return '找不到 ' + JSON.stringify(sp) + '（可见控件 ' + all.length + ' 个）';
  const e = hit[Math.min(Number(sp.nth || 0), hit.length - 1)];
  e.scrollIntoView({block: 'center'});
  const wrap = e.closest('div') || e.parentElement;
  for (const t of [e, wrap]) {
    for (const ty of ['mousedown', 'mouseup', 'click']) {
      t.dispatchEvent(new MouseEvent(ty, {bubbles: true, cancelable: true, view: window}));
    }
  }
  return '点开 第' + (sp.nth || 0) + '个 ' + (sp.ka || sp.ph || sp.lbl)
       + ' 当前值=' + ((e.value || e.innerText) || '').trim().slice(0, 30);
})()
"""

勾选按标记 = """
(() => {
  const sp = __SPEC__;
  const lblOf = (e) => { const p = e.closest('div[class*=form-item]') || e.parentElement;
                         return ((p && p.innerText) || '').trim(); };
  const all = [...document.querySelectorAll('input[type=checkbox]')];
  let e = null;
  if (sp.ka) e = all.find((x) => x.getAttribute('ka') === sp.ka);
  if (!e && sp.lbl) e = all.find((x) => lblOf(x).includes(sp.lbl));
  if (!e) return '找不到勾选框 ' + JSON.stringify(sp) + '（共 ' + all.length + ' 个）';
  const 要 = !sp.uncheck;
  if (e.checked === 要) return '已经是' + (要 ? '勾上' : '空着') + '：' + lblOf(e).slice(0, 20);
  e.click();
  return '点了 ' + lblOf(e).slice(0, 20) + ' checked=' + e.checked;
})()
"""

# 真往控件里"打字"，而不是改 DOM.value。
# BOSS 这些框是 Vue 受控的：原生 setter + input 事件只改了 DOM，模型里还是空的，
# 表现就是 公司名称=天鹅到家 明明看得见，点完成却报"请填写所在公司"（实测）。
# execCommand('insertText') 会走 beforeinput/input 这条真输入路径，模型才收。
插入按标记 = """
(() => {
  const specs = __SPECS__;
  const clean = (s) => (s || '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const lblOf = (e) => { const p = e.closest('div[class*=form-item]') || e.parentElement;
                         return ((p && p.innerText) || '').trim(); };
  const all = [...document.querySelectorAll('input,textarea,[contenteditable=true]')].filter(vis);
  const out = [];
  for (const sp of specs) {
    let e = null;
    if (sp.ka) e = all.find((x) => x.getAttribute('ka') === sp.ka);
    if (!e && sp.ph) e = all.find((x) => (x.placeholder || '').includes(sp.ph));
    if (!e && sp.lbl) e = all.find((x) => lblOf(x).includes(sp.lbl));
    if (!e) { out.push('找不到 ' + JSON.stringify(sp)); continue; }
    e.scrollIntoView({block: 'center'});
    e.focus();
    if (!sp.keep) {
      const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
      const s = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
      s.call(e, ''); e.dispatchEvent(new Event('input', {bubbles: true}));
      if (e.isContentEditable) e.innerText = '';
    }
    let 成 = false;
    try { 成 = document.execCommand('insertText', false, sp.value); } catch (err) { 成 = '抛错:' + err; }
    if (成 !== true && !e.value) {
      // 兜底：execCommand 在某些只读/富文本框上返回 false，退回原生 setter（DOM 有值但模型可能没值）
      const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
      const s = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
      s.call(e, sp.value);
      e.dispatchEvent(new Event('input', {bubbles: true}));
      e.dispatchEvent(new Event('change', {bubbles: true}));
    }
    out.push((sp.ph || sp.lbl || sp.ka) + '=' + clean(e.value || e.innerText).length + '字/insertText=' + 成);
  }
  return out.join(', ');
})()
"""


# 给"页面上那一个格子"打个临时标记，下一步再用 DrissionPage 按标记做元素级真点。
# 为什么绕这一圈：行业面板里那一格、公司联想里的哪一条，都不是 input，
# 按标记那套（只认 input/textarea）够不着；而 JS 的 .click() 又不被 BOSS 的
# Vue 组件认账（职位弹层认、行业弹层不认，实测）。打标 → 真点，两边都占上。
打标 = """
(() => {
  const sp = __SPEC__;
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const 已有 = document.querySelector('[data-orca-tag=hit]');
  if (已有 && sp.覆盖 !== true) return '已有标：' + clean(已有.innerText).slice(0, 30);
  let host = document;
  if (sp.scope) {
    const h = [...document.querySelectorAll(sp.scope)];
    if (!h.length) return 'NO SCOPE ' + sp.scope;
    host = h[Number(sp.scopeNth || 0)];
  }
  let pool = [...host.querySelectorAll(sp.tag || '*')].filter((e) => sp.隐藏也认 || vis(e));
  // 输入框没有 innerText，所以除了按文字，还要能按 placeholder / ka 打标记
  const 判 = (e) => {
    const t = clean(e.innerText);
    if (sp.ph) return (e.placeholder || '').includes(sp.ph);
    if (sp.ka) return e.getAttribute('ka') === sp.ka;
    return sp.regex ? new RegExp(sp.regex).test(t) : (sp.包含 ? t.includes(sp.want) : t === sp.want);
  };
  pool = pool.filter(判);
  if (sp.只要叶子 !== false) pool = pool.filter((e) => e.children.length === 0);
  const e = pool[Number(sp.nth || 0)];
  if (!e) return '没打上标 ' + (sp.regex || sp.want) + '（候选 ' + pool.length + ' 个）';
  [...document.querySelectorAll('[data-orca-tag]')].forEach((x) => x.removeAttribute('data-orca-tag'));
  e.setAttribute('data-orca-tag', 'hit');
  return '打上了 ' + clean(e.innerText).slice(0, 30) + ' 候选=' + pool.length
       + ' 是=' + (e.tagName + '.' + (e.className || '')).slice(0, 44);
})()
"""


点任意词 = """
(() => {
  const want = __SPEC__;
  const clean = (s) => ((s || '') + '').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
  const 全部 = [...document.querySelectorAll('*')].filter((x) => vis(x) && clean(x.innerText) === want);
  if (!全部.length) return 'NO ' + want;
  const 叶子 = 全部.filter((x) => x.children.length === 0);
  const 点 = 叶子[0] || 全部[全部.length - 1];
  点.click();
  return '点了 ' + want + ' 是' + 点.tagName + '.' + (点.className || '').toString().slice(0, 28);
})()
"""


def _跑js(instance, js):
    """简历页是 SPA：整页加载完的头几秒里执行上下文还会被换掉一次，那一眼 run_js
    报的是"js运行环境出错"（11-03 实测：readyState=complete、#education 在、15 个
    编辑按钮都在，脚本仍崩在第一个 JS 上）。重试到上下文稳住，而不是让整趟脚本
    因为一次呼吸没跟上就废掉——每次重跑都要再加载一整页。
    """
    for 第几 in range(6):
        try:
            return instance.run_js(js, as_expr=True)
        except Exception as e:
            if 第几 == 5:
                raise
            print("JS 上下文没就绪（%s），%d 秒后重试" % (type(e).__name__, 2 * (第几 + 1)))
            time.sleep(2 * (第几 + 1))


def _存现场(instance, 名字: str, idx: int) -> list:
    """把"这一眼页面上真有什么"整张留下：形状 + HTML + 截图，一次都不多点页面。

    要这个是因为有两回光凭日志查不出形状：号0 那趟 BOSS 回了「操作失败，请刷新
    页面重试！」（11-03 01:34），还有一回连续硬刷被限流、整页只回来一个壳。
    这里只读页面，不点任何东西——多点一下就是多一分风控。
    """
    目录 = ROOT / "data" / "简历现场"
    目录.mkdir(parents=True, exist_ok=True)
    基 = 目录 / ("%s_%s_号%s" % (time.strftime("%m%d-%H%M%S"), 名字, idx))
    留 = []
    try:
        状 = instance.run_js("""(() => ({url: location.href,
            节点: document.querySelectorAll('*').length,
            板块: !!document.getElementById('education'),
            编辑按钮: document.querySelectorAll("a[ka^='user-resume-edit']").length,
            弹层: [...document.querySelectorAll('.boss-popup__content,[class*=toast],[class*=message]')]
                     .map(e => (e.innerText || '').trim()).filter(t => t).slice(0, 8)}))()""",
            as_expr=True)
        路径 = 基.with_suffix(".txt")
        路径.write_text(json.dumps(状, ensure_ascii=False, indent=1), encoding="utf-8")
        留.append(str(路径))
        print("现场形状:", json.dumps(状, ensure_ascii=False)[:600])
    except Exception as e:
        print("现场形状没取到:", type(e).__name__, str(e)[:60])
    try:
        文 = getattr(instance.page, "html", None) or instance.run_js(
            "return document.documentElement.outerHTML;")
        if 文:
            路径 = 基.with_suffix(".html")
            路径.write_text(str(文), encoding="utf-8")
            留.append(str(路径))
    except Exception as e:
        print("HTML 没存下:", type(e).__name__, str(e)[:60])
    try:
        路径 = 基.with_suffix(".png")
        instance.page.get_screenshot(str(路径))
        留.append(str(路径))
    except Exception as e:
        print("截图没存下:", type(e).__name__, str(e)[:60])
    print("📸 现场已留:", " | ".join(留) or "三样都没成")
    return 留


def 等简历页就绪(instance, 最多=90, idx=None):
    """整页加载实测 126~135 秒，也可能只回一个壳（连续硬刷被限流那回就是这样）。
    等到真拿到简历板块再动手；到点没就绪就如实说，后面的步骤会报"找不到"，
    不许悄悄往下跑成"改了"。"""
    过 = 0
    while 过 < 最多:
        try:
            形 = instance.run_js("""(() => ({ready: document.readyState,
              板块: !!document.getElementById('education'),
              按钮: document.querySelectorAll("a[ka^='user-resume-edit']").length}))()""",
              as_expr=True)
            if isinstance(形, dict) and 形.get("板块") and 形.get("按钮", 0) >= 3:
                print("简历页就绪:", json.dumps(形, ensure_ascii=False), "等了 %d 秒" % 过)
                return True
        except Exception:
            pass
        time.sleep(3)
        过 += 3
    print("⚠️ 简历页 %d 秒还没就绪，这一趟只能当没做成" % 最多)
    _存现场(instance, "没就绪", idx if idx is not None else "?")
    return False


def _跑模板(instance, 模板, 载荷):
    """把载荷塞进 JS 模板再跑。用 replace 不用 %：正文里的百分号会把 % 格式化打炸。"""
    文 = json.dumps(载荷, ensure_ascii=False)
    return _跑js(instance, 模板.replace("__SPECS__", 文).replace("__SPEC__", 文))


def action_script(idx: int, sid: str, ka: str, steps: list) -> int:
    """按脚本驱动一张表单：开板块 → 填字段 → 点年月 → 点完成 → 回读。

    项目/工作经历这种带级联时间选择器的表单，一次赋值填不完，必须点着走。
    """
    instance = open_browser(idx)
    instance.get("https://www.zhipin.com/web/geek/resume")
    if not 等简历页就绪(instance, idx=idx):
        return 1
    域 = sid
    for st in steps:
        动作 = st.get("do")
        if 动作 == "open":
            # sid/ka 优先看这一步自己给的：一趟脚本要连着改几个板块（教育、项目…），
            # 只认命令行的那一个 sid 就没法走第二条。
            板 = st.get("sid") or sid
            卡 = st.get("ka") or ka
            第几 = int(st.get("第几", 0) or 0)
            rect = _板块矩形(instance, 板) if 板 else None
            if 卡 and not rect:
                rect = _跑js(instance,
                    """(() => {const e=document.querySelector('[ka=' + %s + ']');
                    if(!e) return null; const r=e.getBoundingClientRect();
                    return {x: Math.round(r.left+r.width/2), y: Math.round(r.top+r.height/2)};})()"""
                    % json.dumps(卡))
            if not rect:
                print("开板块失败：找不到", 板 or 卡)
                return 1
            instance.page.actions.move_to((rect["x"], rect["y"]))
            time.sleep(0.8)
            if 卡:
                print("开编辑:", _跑js(instance, 按ka开 % json.dumps(卡)))
            else:
                print("开编辑:", _跑js(instance, 打开第几个编辑
                      .replace("__SID__", json.dumps(板, ensure_ascii=False))
                      .replace("__N__", str(第几))))
            域 = "" if 卡 else 板
            time.sleep(2)
        elif 动作 == "set":
            print("赋值:", instance.run_js(
                赋值 % (json.dumps(域), json.dumps(st.get("fields") or {}, ensure_ascii=False)),
                as_expr=True))
            time.sleep(1)
        elif 动作 == "setph":
            print("按标记赋值:", _跑模板(instance, 按标记赋值, st.get("specs") or []))
            time.sleep(float(st.get("wait", 1.0)))
        elif 动作 == "insert":
            print("真打字:", _跑模板(instance, 插入按标记, st.get("specs") or []))
            time.sleep(float(st.get("wait", 1.2)))
        elif 动作 == "mark":
            # 分类面板那些格子是异步挂出来的：同一套步骤里，行业那一格有时 2 秒就有了、
            # 有时 5 秒还没有（实测）。打标支持重试，省得整趟白跑。
            第几 = 0
            while True:
                第几 += 1
                结 = _跑模板(instance, 打标, st.get("at") or {})
                print("打标:", 结)
                if not str(结).startswith("没打上标") or 第几 >= int(st.get("试几次", 1)):
                    break
                time.sleep(float(st.get("隔", 1.5)))
            time.sleep(float(st.get("wait", 0.6)))
        elif 动作 == "openfield":
            print("点开字段:", _跑模板(instance, 点开按标记, st.get("field") or {}))
            time.sleep(float(st.get("wait", 2.0)))
        elif 动作 == "checkph":
            print("按标记勾选:", _跑模板(instance, 勾选按标记, st.get("field") or {}))
            time.sleep(float(st.get("wait", 0.8)))
        elif 动作 == "realclick":
            # 所属行业这种框，JS 派发 mousedown/mouseup/click 打不开面板（BOSS 只认真事件），
            # 而 ka 挂在包层 div 上、DrissionPage 按 input[ka=..] 找不到元素。
            # css 那条走元素级 click（坐标 DrissionPage 自己算，滚动过的页面不会点歪，
            # 底层是 CDP mousePressed/mouseReleased，isTrusted=true）；
            # 没给 css 就退回"JS 量坐标 + 移鼠标真点"。
            选 = st.get("css", "")
            if 选:
                try:
                    _跑js(instance, _滚动到可见.replace("__CSS__", json.dumps(选, ensure_ascii=False)))
                    time.sleep(0.5)
                    件 = instance.eles('css:' + 选, timeout=4) or []
                    第几 = int(st.get("第几", 0) or 0)
                    if not 件 or 第几 >= len(件):
                        # 不撤整趟：这一格本来就可能不存在（比如公司联想里根本没有
                        # 「新公司」那一条），后面的闸门会核最终结果
                        print("真点：找不到", 选, "第", 第几, "个（共 %d 个，继续走后面的步骤）" % len(件))
                    else:
                        件[第几].click()
                        print("真点了元素", 选, "第%d个" % 第几)
                except Exception as e:
                    print("真点失败:", type(e).__name__, e)
            else:
                位 = _跑模板(instance, 矩形按标记, st.get("field") or {})
                if not isinstance(位, dict) or not 位.get("ok"):
                    print("真点：定位失败", json.dumps(位, ensure_ascii=False)[:200])
                    return 1
                instance.page.actions.move_to((位["x"], 位["y"]))
                time.sleep(0.4)
                try:
                    instance.page.actions.click()
                    print("真点了坐标", 位)
                except Exception as e:
                    print("真点失败:", type(e).__name__, e)
            time.sleep(float(st.get("wait", 2.0)))
        elif 动作 == "typein":
            # 元素级输入（DrissionPage 的 element.input）：中文走得到模型，
            # 比"移鼠标点一下 + 键盘敲"稳（那种在框滚出视口后一个字符都不落地，实测）。
            选 = st.get("css") or "[data-orca-tag=hit]"
            文 = st.get("text", "")
            try:
                件 = instance.eles('css:' + 选, timeout=4) or []
                if not 件:
                    print("输入：找不到", 选)
                else:
                    件[0].input(文, clear=True)
                    print("输入了 %d 字 → %s" % (len(文), (件[0].value or "")[:24]))
            except Exception as e:
                print("输入失败:", type(e).__name__, e)
            time.sleep(float(st.get("wait", 1.5)))
        elif 动作 == "edittext":
            # 就地改几句话（删一行 / 换几个词），而不是整段重写：
            # 整段重写要先把原文捞出来喂回去，捞的时候列字段截到 140 字，
            # 一回读就把他半段经历抹了（这条踩过，见 memory「dump 截断会把已存误判成没存」）。
            print("改文:", json.dumps(_跑模板(instance, 改文, st.get("at") or {}),
                                    ensure_ascii=False)[:1200])
            time.sleep(float(st.get("wait", 0.8)))
        elif 动作 == "readfield":
            print("读全文:", json.dumps(_跑模板(instance, 读字段, st.get("at") or {}),
                                     ensure_ascii=False)[: int(st.get("限", 4000))])
            time.sleep(float(st.get("wait", 0.4)))
        elif 动作 == "clickword":
            # BOSS 弹窗那对「暂不删除 / 继续删除」不是 span/a/button，是 div，
            # 按文字点那条找不到（实测点了个空）
            print("点词:", _跑模板(instance, 点任意词, st.get("text", "")))
            time.sleep(float(st.get("wait", 1.5)))
        elif 动作 == "hover":
            # 附件行的「删除」是 CSS :hover 才出现的，JS 派发 mouseenter 骗不过 :hover，
            # 只有真把鼠标移上去才渲染（实测 NoRectError 就是这么来的）。
            选 = st.get("css", "")
            try:
                件 = instance.eles('css:' + 选, timeout=4) or []
                if not 件:
                    print("悬停：找不到", 选)
                else:
                    件[int(st.get("nth", 0))].hover()
                    print("悬停了", 选, "第", st.get("nth", 0), "个")
            except Exception as e:
                print("悬停失败:", type(e).__name__, e)
            time.sleep(float(st.get("wait", 1.2)))
        elif 动作 == "focus":
            print("聚焦:", instance.run_js(
                聚焦字段 % (json.dumps(域), json.dumps(int(st.get("field", 0)))), as_expr=True))
            time.sleep(float(st.get("wait", 1.2)))
        elif 动作 == "peek":
            # 先看清这一刻页面上有哪些可写控件再决定往哪个下标写。
            # 为什么需要：编辑已有那一条时开出来的表单，字段下标和「添加」那条不一样
            # （实测「添加教育经历」是 0学历/1学校/2专业/3在校经历，而板块里的 编辑
            # 走的是另一条挂载路径）；照猜的下标写下去，写进「专业」那一格就是把他的
            # 真实信息抹了。看一眼不要钱，写错要人命。
            print("看一眼:", json.dumps(instance.run_js(
                列字段 % json.dumps(st.get("scope", 域)), as_expr=True),
                ensure_ascii=False)[: int(st.get("限", 2600))])
            time.sleep(float(st.get("wait", 0.5)))
        elif 动作 == "clicktext":
            print("点文本:", _跑js(instance, 点按钮 % (json.dumps(st.get("scope", 域)),
                                                    json.dumps(st.get("text", "")))))
            time.sleep(float(st.get("wait", 1.0)))
        elif 动作 == "type":
            # 只读选择器（职位名称/所属行业）要真敲键盘才会出下拉，直接改 value 它不认。
            # 框在 DrissionPage 里量不到宽高，所以靠上一步 JS 聚焦，再往页面当前焦点敲。
            文 = st.get("text", "")
            # ChromiumPage 没有 .input()，键盘输入在 actions.type() 上（敲给当前聚焦元素）
            try:
                instance.page.actions.type(文)
                print(f"键盘敲入: {文}")
            except Exception as e:
                print("键盘输入失败:", type(e).__name__, e)
            time.sleep(float(st.get("wait", 2.0)))
        elif 动作 == "check":
            print("勾选:", instance.run_js(
                """(() => {const sid = %s, n = %s;
                const host = document.getElementById(sid) || document;
                const vis = (e) => { const r = e.getBoundingClientRect();
                                     return r.width > 2 && r.height > 2; };
                const f = [...host.querySelectorAll('input,textarea')].filter(vis)[n];
                if (!f) return 'NO FIELD';
                if (f.checked) return '已勾';
                f.click(); return '勾了 checked=' + f.checked;})()"""
                % (json.dumps(域), json.dumps(int(st.get("field", 0)))), as_expr=True))
            time.sleep(0.8)
        elif 动作 == "wait":
            # 联想下拉/分类面板挂出来要一两秒，不等就会读到空面板
            time.sleep(float(st.get("sec", 1.0)))
        elif 动作 == "clickka":
            # 保存/完成这类按钮用文本点不稳：同一页上「完成」「保存」不止一个，
            # 而 BOSS 给每个按钮都留了 ka（resume_form_education_ok 这种），按 ka 点才是点它。
            print("点ka:", instance.run_js(按ka开 % json.dumps(st["ka"]), as_expr=True))
            time.sleep(float(st.get("wait", 1.5)))
        elif 动作 == "reload":
            # 写完之后必须换一页再读：就地读到的可能是还没提交的前端状态
            instance.get("https://www.zhipin.com/web/geek/resume")
            time.sleep(float(st.get("sec", 6)))
            print("重载后:", 登录态(instance))
        elif 动作 == "show":
            print("现状:", json.dumps(instance.run_js(列字段 % json.dumps(域), as_expr=True),
                                      ensure_ascii=False)[:2200])
        elif 动作 == "js":
            # 弹层/分类选择器的选项常常挂在板块容器外面，列字段看不见——
            # 留一条能直接跑 JS 的口子，把"页面上此刻真有什么"抄回来，别靠猜。
            值 = instance.run_js(st["code"], as_expr=True)
            print("JS:", json.dumps(值, ensure_ascii=False)[:int(st.get("limit", 2500))])
            time.sleep(float(st.get("wait", 1.0)))
        elif 动作 == "shot":
            p = ROOT / "data" / f"resume_step_account_{idx}.png"
            try:
                instance.page.get_screenshot(str(p))
                print("截图:", p)
            except Exception as e:
                print("截图失败:", e)
        elif 动作 == "dump":
            # 哪一步可疑就在哪一步后面插一条 dump：存形状+HTML+截图，不多点一下页面
            _存现场(instance, str(st.get("名", "步骤")), idx)
        else:
            print("未知步骤:", 动作)
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--acct", type=int, required=True, help="0=主账号 1=账号2")
    ap.add_argument("--slot", default="", help="profile 后缀：换一份简历页会话（被限流时用）")
    ap.add_argument("--action", default="probe")
    ap.add_argument("--section", default="")
    ap.add_argument("--which", type=int, default=0)
    ap.add_argument("--ka", default="")
    ap.add_argument("--sid", default="", help="板块 DOM id，如 summary / workExpList")
    ap.add_argument("--values", default="", help='JSON 字典：{"字段下标": "要写的内容"}')
    ap.add_argument("--values-file", default="", help="同上，但从文件读，避免命令行转义把中文弄坏")
    ap.add_argument("--save", default="")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--file", default="")
    ap.add_argument("--step", default="upload")
    ap.add_argument("--script", default="", help="步骤 JSON 文件，见 action_script")
    args = ap.parse_args()
    global _槽
    _槽 = re.sub(r"[^0-9A-Za-z_-]", "", args.slot or "")
    if args.acct not in (0, 1):
        print("只支持 --acct 0 或 1")
        return 2
    if args.action == "probe":
        return action_probe(args.acct)
    if args.action == "dump":
        return action_dump(args.acct)
    if args.action == "sections":
        return action_sections(args.acct)
    if args.action == "openedit":
        return action_open_edit(args.acct, args.section, args.which)
    if args.action == "ka":
        return action_ka(args.acct)
    if args.action == "ids":
        return action_ids(args.acct)
    if args.action == "clickedit":
        return action_clickedit(args.acct, args.ka)
    if args.action == "fill":
        值 = args.values
        if args.values_file:
            值 = Path(args.values_file).read_text(encoding="utf-8")
        return action_fill(args.acct, args.sid,
                           json.loads(值 or "{}"), args.save, args.inspect, args.ka)
    if args.action == "attach":
        return action_attach(args.acct, args.file, args.step)
    if args.action == "script":
        步 = json.loads(Path(args.script).read_text(encoding="utf-8"))
        return action_script(args.acct, args.sid, args.ka, 步)
    print(f"未实现的动作: {args.action}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
