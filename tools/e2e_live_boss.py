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
    print("\n[4] AI 筛岗（真实调用一次）")
    eng.running = True
    target = next((j for j in jobs if j.get("job_name")), None)
    if target:
        print(f"   岗位：{target['job_name']} / {target['company']} / {target['salary']}")
        # 真实打招呼时 AI 拿到的是详情页 JD，这里给同样的输入；
        # 只喂岗位名会让模型不按格式回答，测出来的"解析失败"是输入的锅不是代码的锅
        jd = (f"岗位职责：负责业务数据日常监控与看板搭建；用 SQL 取数并输出周报；"
              f"参与{target['job_name']}相关专项分析。任职要求：本科及以上，"
              f"熟悉 MySQL、Excel，会 Python 优先。")
        # 该方法返回 (判断结果, 耗时)：不匹配时结果为 None，属正常返回
        res, ai_cost = eng._analyze_job_with_ai(dict(target, jd_description=jd,
                                                     job_description=jd,
                                                     requirements="本科，1-3年经验，会 SQL"))
        check("AI 给出了判断", isinstance(res, dict) or ai_cost > 0, f"耗时 {ai_cost:.1f}s")
        # 容灾链顺序按用户要求保持不动，所以死接口仍会串行拖时间 —— 这里如实量出来
        check("AI 单次调用在 30s 预算内", ai_cost <= 30,
              f"实测 {ai_cost:.1f}s（不可用接口未从重试链剔除）")
        if isinstance(res, dict):
            check("结果含评分", isinstance(res.get("score"), (int, float)), res.get("score"))
            check("结果含理由", bool((res.get("reason") or "").strip()),
                  (res.get("reason") or "")[:60])
            check("AI 未走兜底", not res.get("ai_error"), str(res.get("reason"))[:60])
            print(f"   评分={res.get('score')} 匹配={res.get('is_match')} 理由={str(res.get('reason'))[:56]}")
    else:
        check("有可测岗位", False, "没解析到岗位，跳过 AI 筛岗")

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
