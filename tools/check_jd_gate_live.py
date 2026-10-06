# -*- coding: utf-8 -*-
"""拿真实岗位详情页量一遍 JD 闸门会挡掉多少——不点任何沟通按钮。

为什么要跑：门槛 80 字是从日志算的，日志只有长度没有内容；
到底挡下的是"来看看再说"的空壳还是正规短招，得看正文本身。

隔离：临时端口 + 临时 profile + Cookie 只读（跑前跑后 sha 一致）；
全程只读详情页文字，一个沟通按钮都不点。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.greet_engine import jd_gate  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402


def read_detail(instance):
    def txt(sel):
        try:
            el = instance.ele(sel, timeout=3)
            return (el.text or "").strip() if el else ""
        except Exception:
            return ""
    return {"job_name": txt(".job-name") or txt("h1.name"),
            "company": txt(".company-name"),
            "jd_description": txt(".job-sec-text"),
            "jd_requirements": txt(".requirements")}


def wait_jd(instance, timeout: float = 14.0) -> bool:
    """详情页是 SPA，点过去还要等一会儿才有正文；等不到就整屏读成 0 字，
    量出来的"挡下多少"全是假的。"""
    end = time.time() + timeout
    while time.time() < end:
        got = instance.run_js(
            "var e=document.querySelector('.job-sec-text');"
            "return e?(e.textContent||'').trim().length:0;")
        if got and int(got) > 0:
            return True
        time.sleep(1.0)
    return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--account", type=int, default=0)
    p.add_argument("--port", type=int, default=9441)
    p.add_argument("--limit", type=int, default=8)
    a = p.parse_args()

    cfg = UnifiedConfig.load()
    acc = cfg.greet.accounts[a.account]
    ck = Path(str(resolve_path(acc.cookie_file or "zhipin_cookies.json")))
    sha_before = hashlib.sha256(ck.read_bytes()).hexdigest()[:16]
    keywords = list(cfg.ai.custom_filter_keywords or [])
    job = (acc.jobs or [None])[0]
    query = getattr(job, "query", "") or "数据分析"
    city = getattr(job, "city", "") or "全国"
    profile = Path(tempfile.mkdtemp(prefix="boss_jdgate_"))
    print(f"关键词 {len(keywords)} 条 | 搜索 {city}·{query}")

    instance = None
    try:
        instance = launch_browser(headless=True, port=a.port, user_data_dir=str(profile))
        instance.load_cookies(str(ck))
        from urllib.parse import quote
        instance.get(f"https://www.zhipin.com/web/geek/jobs?query={quote(query)}"
                     f"&city={quote(city)}")
        time.sleep(6)
        hrefs = instance.run_js(
            "return Array.prototype.slice.call("
            "document.querySelectorAll('a[href*=job_detail]')).map("
            "function(a){return a.href||'';});")
        seen, uniq = set(), []
        for h in (hrefs or []):
            if not h:
                continue
            key = h.split('/job_detail/')[-1].split('?')[0]
            if key and key not in seen:
                seen.add(key)
                uniq.append(h)
        hrefs = uniq[: a.limit]
        print(f"取到 {len(hrefs)} 个岗位详情页")

        blocked = 0
        for url in hrefs:
            instance.get(url)
            wait_jd(instance)
            info = read_detail(instance)
            why = jd_gate(keywords, info)
            if why:
                blocked += 1
            print(f"  [{'挡' if why else '放'}] {info['job_name'][:22]}|"
                  f"{info['company'][:14]} 正文 {len(info['jd_description'])} 字 "
                  f"{why} || {info['jd_description'][:60].replace(chr(10), ' ')}")
        print(f"挡下 {blocked}/{len(hrefs)}")
        print(json.dumps({"blocked": blocked, "total": len(hrefs)}, ensure_ascii=False))
        return 0
    finally:
        if instance is not None:
            try:
                instance.quit()
            except Exception:
                pass
        shutil.rmtree(str(profile), ignore_errors=True)
        sha_after = hashlib.sha256(ck.read_bytes()).hexdigest()[:16]
        print(("Cookie 文件未被改动 ✓" if sha_before == sha_after
               else f"警告：Cookie 文件被改动 ✗ {sha_before} → {sha_after}"))


if __name__ == "__main__":
    raise SystemExit(main())
