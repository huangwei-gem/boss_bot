# -*- coding: utf-8 -*-
"""真实运行实测（只读侧）— 破解版浏览器登录 BOSS，跑通"读岗位 → AI 筛岗 → 读会话"

这条腿验证的是单元测试覆盖不到的东西：反爬能不能过、页面结构还认不认、
AI 筛岗在真实岗位数据上给不给得出分数。

全程不点任何发送动作（不打招呼、不发文字、不发简历），
所以跑完不会给任何 HR 留下消息。

用法：python tools/e2e_live_boss.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from DrissionPage import ChromiumOptions, ChromiumPage
from unittest.mock import MagicMock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOAK = os.path.join(BASE, "cloakbrowser", "chrome.exe")
PORT = 9403
SHOTS = os.path.join(BASE, "tools", "e2e_live")
os.makedirs(SHOTS, exist_ok=True)
RESULTS = []
PAGE = None


def shutdown_browser():
    """退出时关掉破解版浏览器：留着它会占住 profile，
    下一个用同一 profile 的脚本（verify_three_way）就连不上了。"""
    try:
        if PAGE is not None and hasattr(PAGE, "quit"):
            PAGE.quit()
    except Exception:
        pass


def check(name, ok, detail=""):
    ok = bool(ok)
    RESULTS.append({"name": name, "ok": ok, "detail": str(detail)[:200]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({str(detail)[:100]})" if detail else ""))


# 独立实现的岗位卡片提取，故意不复用 greet_engine 的选择器
JS_JOBS = '''(function(){
  var out=[];
  var cards=document.querySelectorAll(".job-card-wrapper, .job-card-body, li.job-card-box");
  for(var i=0;i<cards.length && out.length<12;i++){
    var c=cards[i];
    var name=(c.querySelector(".job-name, .job-title .job-name")||{}).textContent||"";
    var comp=(c.querySelector(".company-name a, .company-name, .boss-name")||{}).textContent||"";
    var sal=(c.querySelector(".job-salary, .salary")||{}).textContent||"";
    var a=c.querySelector("a[href*='job_detail'], a[href*='position']");
    name=name.trim(); if(!name) continue;
    out.push({job_name:name, company:comp.trim(), salary:sal.trim(),
              url:a?a.href:"", boss:(c.querySelector(".boss-name")||{}).textContent||""});
  }
  return JSON.stringify(out);
})()'''


def main():
    from boss_bot.unified_config import UnifiedConfig
    cfg = UnifiedConfig.load()
    acc = cfg.greet.accounts[0]
    job_cfg = acc.jobs[0]
    print(f"配置：账号={acc.name} 岗位={job_cfg.query} 城市={job_cfg.city}")

    assert os.path.exists(CLOAK), "必须用项目自带破解版浏览器（BOSS 有反爬）"
    co = ChromiumOptions()
    co.set_browser_path(CLOAK)
    co.set_local_port(PORT)
    co.set_argument(f"--user-data-dir={os.path.join(BASE, 'browser_data', 'account_0')}")
    co.set_argument("--disable-blink-features=AutomationControlled")
    page = ChromiumPage(co)
    globals()['PAGE'] = page

    # ── 1. 反爬自检 ──
    print("\n[1] 反爬自检")
    page.get("https://www.zhipin.com/web/geek/job-recommend")
    time.sleep(7)
    wd = page.run_js("navigator.webdriver", as_expr=True)
    check("navigator.webdriver 被抹掉", wd in (False, None), f"webdriver={wd}")
    ua = page.run_js("navigator.userAgent", as_expr=True) or ""
    check("UA 不含 Headless", "Headless" not in ua, ua[-40:])
    check("没被跳到安全校验页", "security" not in (page.url or "")
          and "verify" not in (page.url or ""), page.url)

    # ── 2. 登录态 ──
    print("\n[2] 登录态与会话读取")
    page.get("https://www.zhipin.com/web/geek/chat")
    time.sleep(7)
    conv = json.loads(page.run_js(
        'JSON.stringify(Array.prototype.map.call('
        'document.querySelectorAll(".friend-content .name-text"),function(e){return e.textContent.trim()}))'
        , as_expr=True) or "[]")
    check("会话列表读得到（说明登录有效）", len(conv) >= 1, f"{len(conv)} 个会话")
    page.get_screenshot(path=os.path.join(SHOTS, "chat.png"))
    if conv:
        first = conv[0]
        page.run_js(f'''(function(){{var f=document.querySelectorAll(".friend-content");
          for(var i=0;i<f.length;i++){{var n=f[i].querySelector(".name-text");
          if(n&&n.textContent.trim()==={json.dumps(first)}){{f[i].click();return;}}}}}})()''',
                    as_expr=True)
        time.sleep(3)
        msgs = json.loads(page.run_js(
            'JSON.stringify(Array.prototype.map.call('
            'document.querySelectorAll(".message-item"),function(e){return (e.innerText||"").trim().slice(0,20)}))'
            , as_expr=True) or "[]")
        check("点进会话能读到消息", len(msgs) >= 1, f"{len(msgs)} 条")
        header = page.run_js(
            '(document.querySelector(".top-info-content .name-text")||{}).textContent||""',
            as_expr=True)
        check("会话头部姓名与侧栏一致", (not header) or header.strip() == first,
              f"头部={header} 侧栏={first}")
        job = page.run_js(
            '(document.querySelector(".chat-position-content .position-content")||{}).textContent||""',
            as_expr=True)
        check("会话岗位名可读（防骚扰判定要用）", bool((job or "").strip()), (job or "")[:40])

    # ── 3. 岗位搜索解析 ──
    print("\n[3] 岗位列表解析")
    # URL 必须由生产代码构造：搜索页是 /web/geek/jobs 且 city 传的是城市编码，
    # 之前测试自己拼了 /web/geek/job?city=长沙，页面本来就渲染不出卡片
    from boss_bot.greet_engine import GreetEngine
    eng = GreetEngine(browser_manager=MagicMock(), config=cfg)
    search_url = eng._build_search_url(job_cfg.query, job_cfg.city)
    print(f"  搜索 URL: {search_url}")
    page.get(search_url)
    time.sleep(8)
    for _ in range(4):
        page.scroll.down(700)
        time.sleep(1.2)
    jobs = json.loads(page.run_js(JS_JOBS, as_expr=True) or "[]")
    check("搜索结果解析出岗位", len(jobs) >= 5, f"{len(jobs)} 个")
    named = [j for j in jobs if j.get("job_name")]
    check("岗位名非空", len(named) == len(jobs) and len(jobs) > 0)
    withco = [j for j in jobs if j.get("company")]
    check("公司名解析到", len(withco) >= max(1, len(jobs) // 2), f"{len(withco)}/{len(jobs)}")
    withurl = [j for j in jobs if j.get("url")]
    check("岗位链接解析到（URL 去重靠它）", len(withurl) >= max(1, len(jobs) // 2),
          f"{len(withurl)}/{len(jobs)}")
    page.get_screenshot(path=os.path.join(SHOTS, "jobs.png"))

    # ── 4. AI 真实筛岗 ──
    # 缓存换到临时文件：既不污染 data/ai_cache.json，也保证每次都是真打接口
    import boss_bot.greet_engine as gem
    from pathlib import Path as _P
    gem.AI_CACHE_FILE = _P(SHOTS) / "ai_cache_probe.json"
    if gem.AI_CACHE_FILE.exists():
        gem.AI_CACHE_FILE.unlink()

    print("\n[4] AI 筛岗（真实连打 3 个岗位：验证慢接口会被自动甩开）")
    eng.running = True
    target = next((j for j in jobs if j.get("job_name")), None)

    def make_job(j):
        jd = (f"岗位职责：负责业务数据日常监控与看板搭建；用 SQL 取数并输出周报；"
              f"参与{j['job_name']}相关专项分析。任职要求：本科及以上，"
              f"熟悉 MySQL、Excel，会 Python 优先。")
        return dict(j, jd_description=jd, job_description=jd,
                    requirements="本科，1-3年经验，会 SQL")

    if target:
        print(f"   岗位：{target['job_name']} / {target['company']} / {target['salary']}")
        picks = [j for j in jobs if j.get("job_name")][:3]
        costs, verdicts = [], []
        for i, j in enumerate(picks, 1):
            res, cost = eng._analyze_job_with_ai(make_job(j))
            # res 为 None 表示 AI 判定"不匹配"（有判断），完整结果看 _last_ai_result
            full = dict(eng._last_ai_result or {})
            costs.append(cost)
            verdicts.append(full)
            print(f"   第{i}个岗位 {j['job_name'][:18]} 耗时 {cost:.1f}s "
                  f"评分={full.get('score')} 理由={str(full.get('reason'))[:48]}")
        check("三个岗位都有 AI 结果", len(verdicts) == 3 and all(verdicts),
              [bool(v) for v in verdicts])
        check("单岗位耗时不超过预算", max(costs) <= 62, f"最慢 {max(costs):.1f}s")
        # 关键功能判据：每个岗位都拿到 AI 的真实判断，没有一个走"超时默认通过"。
        # 接口本身延迟会抖（实测 18~50s），所以这里只断言不兜底，不断言快慢顺序。
        # 现实：容灾链头两个接口"ping 得通但真提示词 30s 不回话"，第一个岗位会
        # 白等 61s 后默认通过；真超时让它们进 5 分钟冷却，之后的岗位几秒就出真判断。
        # 所以判据是"最多兜底一个岗位，且最后一个必须是真判断"。
        fallbacks = [v for v in verdicts if v.get("ai_error")]
        check("最多一个岗位走超时兜底", len(fallbacks) <= 1,
              [str(v.get("reason"))[:36] for v in verdicts])
        check("最后一个岗位是真判断", not verdicts[-1].get("ai_error"),
              str(verdicts[-1].get("reason"))[:60])
        check("真判断含评分与理由",
              all(isinstance(v.get("score"), (int, float))
                  and bool((v.get("reason") or "").strip()) for v in verdicts),
              [v.get("score") for v in verdicts])
        print(f"   耗时序列 {[round(c, 1) for c in costs]}")
    else:
        check("有可测岗位", False, "没解析到岗位，跳过 AI 筛岗")

    # ── 5. 多账号归属（真实配置 + 真实岗位数据，不写盘） ──
    print("\n[5] 多账号归属")
    accounts = cfg.greet.accounts
    check("配置里有两个账号", len(accounts) >= 2, f"{len(accounts)} 个")
    acc2_page = None
    if len(accounts) >= 2:
        e1 = GreetEngine(browser_manager=MagicMock(), config=cfg, account_index=1)
        check("账号2 用自己的 cookie 文件",
              (e1._cookie_file or "") != (accounts[0].cookie_file or ""),
              f"{accounts[0].cookie_file} vs {e1._cookie_file}")
        check("账号2 记录标签是账号名不是文件名",
              e1._account_label() == accounts[1].name
              and not e1._account_label().endswith(".json"), e1._account_label())
        got = []
        e1._greet_store = MagicMock()
        e1._greet_store.add.side_effect = got.append
        if target:
            # 只落内存，绝不写 data/greet_records.json
            e1._record_greet(target, is_skipped=True, skip_reason="实测占位-未发送")
            rec = got[0]
            check("真实岗位记录带 account_index=1", rec.account_index == 1, rec.account_index)
            check("真实岗位记录带账号名", rec.account_name == accounts[1].name,
                  rec.account_name)
            ev = []
            e1._greet_event_cb = lambda d: ev.append(d)
            e1._emit_greet_event(target, "skip", skip_reason="实测占位")
            check("实时投递事件也带账号", ev and ev[0]["account_index"] == 1,
                  ev[0].get("account_index") if ev else None)

        # 去重库合并写：换到临时文件，不碰 data/chatted_jobs.json
        import boss_bot.greet_engine as gem
        tmpdb = os.path.join(SHOTS, "chatted_probe.json")
        real_db = gem.CHATTED_DB_FILE
        try:
            from pathlib import Path as _P
            gem.CHATTED_DB_FILE = _P(tmpdb)
            if os.path.exists(tmpdb):
                os.remove(tmpdb)
            e0 = GreetEngine(browser_manager=MagicMock(), config=cfg, account_index=0)
            e0._mark_chatted({"url": "probe_main"})
            e1._chatted_cache = None
            e1._mark_chatted({"url": "probe_two"})
            merged = set(json.loads(open(tmpdb, encoding="utf-8").read()))
            check("两号共用去重库不会互相覆盖",
                  {"probe_main", "probe_two"} <= merged, sorted(merged))
            e0._chatted_loaded_at = 0.0
            e0._chatted_cache = set()
            check("对方打过的岗位本账号能识别",
                  e0._is_already_chatted({"url": "probe_two"}) is True)
        finally:
            gem.CHATTED_DB_FILE = real_db
            if os.path.exists(tmpdb):
                os.remove(tmpdb)

        # 另开一个浏览器实例（账号2 自己的 profile），只读会话列表：
        # 这是"到底算不算两个号"最硬的证据
        try:
            co2 = ChromiumOptions()
            co2.set_browser_path(CLOAK)
            co2.set_local_port(PORT + 1)
            co2.set_argument(f"--user-data-dir={os.path.join(BASE, 'browser_data', 'account_1')}")
            co2.set_argument("--disable-blink-features=AutomationControlled")
            acc2_page = ChromiumPage(co2)
            acc2_page.get("https://www.zhipin.com/web/geek/chat")
            time.sleep(9)
            conv2 = json.loads(acc2_page.run_js(
                'JSON.stringify(Array.prototype.map.call('
                'document.querySelectorAll(".friend-content .name-text"),'
                'function(e){return e.textContent.trim()}))', as_expr=True) or "[]")
            check("账号2 浏览器登录有效", len(conv2) >= 1, f"{len(conv2)} 个会话")
            check("两个号看到的会话不是同一份",
                  set(conv2) != set(conv),
                  f"主{len(conv)} 二号{len(conv2)} 交集{len(set(conv) & set(conv2))}")
            acc2_page.get_screenshot(path=os.path.join(SHOTS, "chat_account_1.png"))
        except Exception as e:
            check("账号2 浏览器可启动", False, f"{type(e).__name__}: {e}")
        finally:
            try:
                if acc2_page is not None:
                    acc2_page.quit()
            except Exception:
                pass

    bad = [r for r in RESULTS if not r["ok"]]
    print(f"\n== 合计 {len(RESULTS)} 项，失败 {len(bad)} 项 ==")
    for r in bad:
        print(f"  FAIL {r['name']}: {r['detail']}")
    with open(os.path.join(SHOTS, "report.json"), "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print("截图与明细：tools/e2e_live/")
    return 0 if not bad else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutdown_browser()
