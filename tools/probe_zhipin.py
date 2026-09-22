# -*- coding: utf-8 -*-
"""
二次探查：截图 + 多选择器尝试，定位 BOSS 直聘岗位列表真实结构。
"""
import json
import os
import time
from pathlib import Path

from DrissionPage import ChromiumPage, ChromiumOptions

BASE = Path(__file__).resolve().parent.parent
CLOAK = BASE / "cloakbrowser"
BROWSER_EXE = str(CLOAK / "chrome.exe")
USER_DATA = str(CLOAK / "User Data")
COOKIE_FILE = BASE / "zhipin_cookies.json"
OUT_DIR = BASE / "tools"


def main():
    os.makedirs(USER_DATA, exist_ok=True)
    co = ChromiumOptions()
    co.set_paths(browser_path=BROWSER_EXE, user_data_path=USER_DATA)
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")

    page = ChromiumPage(co)
    if COOKIE_FILE.exists():
        cookies = json.load(open(COOKIE_FILE, encoding="utf-8"))
        for c in cookies:
            try:
                page.set.cookies(c)
            except Exception:
                pass

    url = "https://www.zhipin.com/web/geek/job?query=数据分析&city=101250100"
    page.get(url)
    # 等待充分加载
    time.sleep(8)
    # 滚动一下触发懒加载
    try:
        page.scroll.to_bottom()
        time.sleep(2)
        page.scroll.to_top()
        time.sleep(2)
    except Exception:
        pass

    print("当前URL:", page.url)
    print("标题:", page.title)

    # 截图
    shot = OUT_DIR / "zhipin_search_shot.png"
    try:
        page.get_screenshot(str(shot), full_page=True)
        print(f"[OK] 截图: {shot}")
    except Exception as e:
        print(f"[WARN] 截图失败: {e}")

    # 多选择器探测
    probe_selectors = [
        ".job-card-wrapper",
        ".job-card-left",
        ".job-card",
        ".rec-job-list",
        ".rec-job-list li",
        ".job-list ul li",
        ".job-list li",
        "ul.rec-job-list li",
        "[class*='job-card']",
        "[class*='job-list']",
        ".search-job-result li",
        ".job-list-box li",
        "li[ka]",
        ".job-card-body",
        ".job-card-info",
        ".job-name",
        ".salary",
        ".company-name",
    ]
    probe = {}
    for sel in probe_selectors:
        try:
            els = page.eles(sel, timeout=2)
            probe[sel] = len(els)
            if els:
                print(f"[HIT] {sel} -> {len(els)} 个")
        except Exception as e:
            probe[sel] = f"ERR: {e}"

    # 如果找到任何 job-card 类元素，打印第一个的 HTML
    for sel in probe_selectors:
        try:
            els = page.eles(sel, timeout=1)
            if els and ("card" in sel or "li" in sel):
                first = els[0]
                print(f"\n=== {sel} 第一个元素 ===")
                print(f"text: [{first.text[:200]}]")
                print(f"html: {first.html[:800]}")
                break
        except Exception:
            pass

    # 打印页面 body 片段，定位真实结构
    try:
        body_html = page.ele("tag:body").html
        # 搜索关键词定位
        import re as _re
        for kw in ["job-card", "rec-job", "job-list", "job-name", "salary"]:
            idx = body_html.find(kw)
            if idx >= 0:
                print(f"\n[FOUND] '{kw}' 在 body 位置 {idx}:")
                print(f"  片段: ...{body_html[max(0,idx-80):idx+200]}...")
    except Exception as e:
        print(f"[WARN] 读取 body 失败: {e}")

    with open(OUT_DIR / "zhipin_probe.json", "w", encoding="utf-8") as f:
        json.dump({"url": page.url, "title": page.title, "probe": probe}, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 探测结果: {OUT_DIR / 'zhipin_probe.json'}")
    page.quit()


if __name__ == "__main__":
    main()