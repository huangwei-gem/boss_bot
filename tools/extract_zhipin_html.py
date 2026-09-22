# -*- coding: utf-8 -*-
"""
使用破解浏览器提取 BOSS 直聘搜索页面的岗位卡片 HTML 结构。
用于逆向分析反爬 Unicode 私用区字符的分布。
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
OUT_FILE = BASE / "tools" / "zhipin_html_structure.json"


def main():
    os.makedirs(USER_DATA, exist_ok=True)

    co = ChromiumOptions()
    co.set_paths(browser_path=BROWSER_EXE, user_data_path=USER_DATA)
    # 反检测基础设置
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")

    page = ChromiumPage(co)

    # 加载 Cookie
    if COOKIE_FILE.exists():
        try:
            cookies = json.load(open(COOKIE_FILE, encoding="utf-8"))
            for c in cookies:
                try:
                    page.set.cookies(c)
                except Exception:
                    pass
            print(f"[INFO] 已加载 {len(cookies)} 条 Cookie")
        except Exception as e:
            print(f"[WARN] 加载 Cookie 失败: {e}")

    url = "https://www.zhipin.com/web/geek/job?query=数据分析&city=101250100"
    print(f"[INFO] 打开: {url}")
    page.get(url)
    time.sleep(6)

    result = {"url": url, "title": page.title, "cards": [], "rec_list": None}

    # 1. job-card-wrapper 结构
    cards = page.eles(".job-card-wrapper", timeout=5)
    print(f"[INFO] 找到 {len(cards)} 个 .job-card-wrapper")

    selectors = {
        "job_name": ".job-name",
        "salary": ".salary",
        "company_name": ".company-name",
        "job_area": ".job-area",
        "tag_list": ".tag-list",
        "company_info": ".company-info",
        "job_info": ".job-info",
        "job_card_info": ".job-card-info",
    }

    for i, card in enumerate(cards[:8]):
        card_data = {"index": i, "html_head": card.html[:800] if card.html else ""}
        for key, sel in selectors.items():
            el = card.ele(sel, timeout=1)
            if el:
                card_data[key] = {
                    "text": el.text,
                    "html": el.html[:500],
                }
            else:
                card_data[key] = None
        # 也尝试 .job-title / .name 等备选
        for alt_sel in [".job-title", ".name", ".job-card-name", ".job-card-title"]:
            el = card.ele(alt_sel, timeout=0.5)
            if el:
                card_data.setdefault("alt", {})[alt_sel] = {"text": el.text, "html": el.html[:300]}
        result["cards"].append(card_data)
        print(f"\n=== 卡片 {i} ===")
        for key in ["job_name", "salary", "company_name", "job_area"]:
            v = card_data.get(key)
            if v:
                print(f"  {key}: text=[{v['text']}]")
                print(f"  {key}: html={v['html'][:200]}")

    # 2. rec-job-list 结构
    rec_list = page.ele(".rec-job-list", timeout=3)
    if rec_list:
        items = rec_list.eles("li", timeout=2)
        print(f"\n[INFO] rec-job-list 找到 {len(items)} 个 li")
        rec_data = {"li_count": len(items), "items": []}
        for i, item in enumerate(items[:5]):
            li_data = {
                "index": i,
                "text": item.text,
                "html_head": item.html[:600] if item.html else "",
            }
            # 子元素
            children = item.eles(":scope > *", timeout=1)
            li_data["children"] = []
            for j, child in enumerate(children[:8]):
                li_data["children"].append({
                    "idx": j,
                    "tag": child.tag,
                    "class": child.attr("class"),
                    "text": (child.text or "")[:120],
                })
            rec_data["items"].append(li_data)
            print(f"\n--- rec li {i} ---")
            print(f"  text=[{item.text}]")
            for ch in li_data["children"]:
                print(f"  child{ch['idx']}: <{ch['tag']} class='{ch['class']}'> [{ch['text']}]")
        result["rec_list"] = rec_data
    else:
        print("[WARN] 未找到 .rec-job-list")

    # 保存
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 结构已保存到 {OUT_FILE}")

    page.quit()


if __name__ == "__main__":
    main()