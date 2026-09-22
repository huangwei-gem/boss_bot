# -*- coding: utf-8 -*-
"""
精确提取：基于已探明的真实结构，逐字段提取岗位卡片，
确认 .job-name / .job-salary / .tag-list / 公司名 选择器，
并查看反爬 Unicode 字符在 raw text 中的分布。
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
OUT = BASE / "tools" / "zhipin_cards_detail.json"


def show_codes(text):
    """显示文本中每个字符的码点，标注私用区字符。"""
    if not text:
        return ""
    out = []
    for ch in text:
        cp = ord(ch)
        if 0xE030 <= cp <= 0xE039:
            out.append(f"[U+{cp:04X}→{cp-0xE030}]")
        elif 0xE000 <= cp <= 0xF8FF:
            out.append(f"[U+{cp:04X}]")
        else:
            out.append(ch)
    return "".join(out)


def main():
    os.makedirs(USER_DATA, exist_ok=True)
    co = ChromiumOptions()
    co.set_paths(browser_path=BROWSER_EXE, user_data_path=USER_DATA)
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")

    page = ChromiumPage(co)
    if COOKIE_FILE.exists():
        for c in json.load(open(COOKIE_FILE, encoding="utf-8")):
            try:
                page.set.cookies(c)
            except Exception:
                pass

    page.get("https://www.zhipin.com/web/geek/job?query=数据分析&city=101250100")
    time.sleep(8)

    # 用真实的卡片选择器
    cards = page.eles("li.job-card-box", timeout=5)
    print(f"[INFO] li.job-card-box 共 {len(cards)} 个")
    if not cards:
        cards = page.eles(".job-card-wrap", timeout=5)
        print(f"[INFO] .job-card-wrap 共 {len(cards)} 个")

    result = []
    for i, card in enumerate(cards[:10]):
        item = {"index": i}

        # 岗位名：a.job-name
        name_el = card.ele(".job-name", timeout=1)
        job_name = name_el.text if name_el else ""
        item["job_name"] = job_name
        item["job_name_html"] = name_el.html if name_el else ""
        item["job_name_codes"] = show_codes(job_name)

        # 薪资：span.job-salary（关键！不是 .salary）
        sal_el = card.ele(".job-salary", timeout=1)
        salary = sal_el.text if sal_el else ""
        item["salary"] = salary
        item["salary_html"] = sal_el.html if sal_el else ""
        item["salary_codes"] = show_codes(salary)

        # 标签：.tag-list li
        tags = [t.text for t in card.eles(".tag-list li", timeout=1)]
        item["tags"] = tags

        # 公司名：.company-name a
        comp_el = card.ele(".company-name", timeout=1) or card.ele(".company-name a", timeout=1)
        company = comp_el.text if comp_el else ""
        item["company"] = company

        # 地区：.job-area
        area_el = card.ele(".job-area", timeout=1)
        item["area"] = area_el.text if area_el else ""

        # HR/活跃：.boss-active-time 或 .boss-name
        boss_el = card.ele(".boss-name", timeout=0.5)
        item["boss"] = boss_el.text if boss_el else ""

        result.append(item)
        print(f"\n=== 卡片 {i} ===")
        print(f"  岗位名: [{job_name}]")
        print(f"  岗位名码点: {item['job_name_codes']}")
        print(f"  薪资: [{salary}]")
        print(f"  薪资码点: {item['salary_codes']}")
        print(f"  薪资HTML: {item['salary_html'][:200]}")
        print(f"  标签: {tags}")
        print(f"  公司: [{company}]")
        print(f"  地区: [{item['area']}]")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n[OK] 详情已保存 {OUT}")
    page.quit()


if __name__ == "__main__":
    main()