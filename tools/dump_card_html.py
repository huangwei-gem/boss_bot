# -*- coding: utf-8 -*-
"""打印第一个卡片的完整HTML，确认公司名/标签/地区选择器。"""
import json, os, time
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions

BASE = Path(__file__).resolve().parent.parent
CLOAK = BASE / "cloakbrowser"
co = ChromiumOptions()
co.set_paths(browser_path=str(CLOAK / "chrome.exe"), user_data_path=str(CLOAK / "User Data"))
co.set_argument("--disable-blink-features=AutomationControlled")
co.set_argument("--no-first-run")
page = ChromiumPage(co)
if (BASE / "zhipin_cookies.json").exists():
    for c in json.load(open(BASE / "zhipin_cookies.json", encoding="utf-8")):
        try: page.set.cookies(c)
        except: pass
page.get("https://www.zhipin.com/web/geek/job?query=数据分析&city=101250100")
time.sleep(8)

cards = page.eles(".job-card-wrap", timeout=5)
print(f"卡片数: {len(cards)}")
if cards:
    c0 = cards[0]
    print("\n=== 第一个卡片完整HTML ===")
    print(c0.html)
    print("\n=== texts() ===")
    for i, t in enumerate(c0.texts()):
        print(f"  [{i}] {t!r}")
    # 试探各种选择器
    print("\n=== 选择器探测 ===")
    for sel in [".job-name", ".job-salary", ".tag-list li", ".company-name",
                ".company-name a", ".job-area", ".boss-name", ".boss-active-time",
                ".job-info", ".job-card-footer", ".company-info",
                ".job-card-info", ".info-public", ".job-tags"]:
        els = c0.eles(sel, timeout=1)
        if els:
            print(f"  {sel}: {len(els)} 个 -> {[e.text[:60] for e in els[:5]]}")
        else:
            print(f"  {sel}: 0")

page.quit()