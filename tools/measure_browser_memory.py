# -*- coding: utf-8 -*-
"""量一个隔离浏览器实例的进程构成，盯住内存有没有涨回去。

用户 2026-10-06："我感觉你现在内存占用的太大了，可以用无头或者其他手段优化，优化完记得测试。"

已测出来的三档（每档两轮，同一台机器、同样两个页面、独立临时 profile）：
    带 gpu-process 的旧参数        2643 MB/实例
    加 --in-process-gpu            2383 MB/实例   ← 现在用的，省 260
    再把渲染进程上限压到 1          2440 MB/实例   ← 没再省，还多一个崩溃连坐，弃
    再加 --js-flags 堆上限 512      2436 MB/实例   ← 只多省 4 MB，冒页面 OOM 的险，弃
    再加 --disable-software-rasterizer  WebGL 直接变 no-webgl，风控要看这个，弃

跑法：python tools/measure_browser_memory.py                 # 量当前参数
      python tools/measure_browser_memory.py --arg --foo=1   # 再叠一个参数对比
"""
import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from boss_bot.browser_launcher import launch_browser  # noqa: E402

CHAT = "https://www.zhipin.com/web/geek/chat"
JOBS = "https://www.zhipin.com/web/geek/jobs?query=%E7%BA%BF%E4%B8%8A%E5%85%BC%E8%81%8C&city=101250100"

# 旧参数（没有 --in-process-gpu）实测 2643 MB/实例，留个上限当回归线
CEILING_MB = 2500


def measure(extra_flags, port, label):
    """起实例→开两页→按 profile 精确统计自己那棵进程树→关掉。

    只数 cmdline 里带本次临时 profile 的进程，绝不把线上那两个号的浏览器算进来。
    """
    prof = tempfile.mkdtemp(prefix=f"mem_{label}_")
    inst = None
    try:
        inst = launch_browser(headless=True, chrome_path="", browser_type="chrome",
                              user_data_dir=prof, port=port, background=True,
                              extra_args=extra_flags)
        page = inst._get_active()
        page.get(CHAT)
        time.sleep(6)
        page.new_tab(JOBS)
        time.sleep(8)
        by, total = {}, 0.0
        for p in psutil.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
            try:
                c = " ".join(p.info['cmdline'] or [])
            except Exception:
                continue
            if p.info['name'] != 'chrome.exe' or prof not in c:
                continue
            kind = 'MAIN' if '--type=' not in c else c.split('--type=')[1].split()[0][:12]
            rss = p.info['memory_info'].rss / 1e6
            by[kind] = by.get(kind, 0.0) + rss
            total += rss
        webgl = page.run_js(
            "var c=document.createElement('canvas');"
            "var g=c.getContext('webgl')||c.getContext('experimental-webgl');"
            "if(!g) return 'no-webgl';"
            "var e=g.getExtension('WEBGL_debug_renderer_info');"
            "return e ? String(g.getParameter(e.UNMASKED_RENDERER_WEBGL)) : 'no-ext';")
        print(f"  {label}: {total:.0f} MB  " +
              "  ".join(f"{k}={v:.0f}" for k, v in sorted(by.items(), key=lambda x: -x[1])))
        print(f"      WebGL: {webgl}")
        return total, webgl
    finally:
        if inst:
            try:
                inst.quit()
            except Exception:
                pass
        time.sleep(2)
        shutil.rmtree(prof, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arg", action="append", default=[],
                    help="在现有参数之上再叠一个 Chrome 启动参数，可重复")
    ap.add_argument("--rounds", type=int, default=2)
    args = ap.parse_args()

    configs = [("当前参数", [])]
    if args.arg:
        configs.append(("当前+临时", list(args.arg)))
    got = {}
    for r in range(args.rounds):
        print(f"第 {r + 1} 轮：")
        for i, (label, flags) in enumerate(configs):
            total, webgl = measure(flags, 9650 + r * 10 + i * 2, label)
            got.setdefault(label, {"mb": [], "webgl": webgl})["mb"].append(total)

    print()
    for label, v in got.items():
        avg = sum(v["mb"]) / len(v["mb"])
        flag = "超线" if avg > CEILING_MB else "正常"
        print(f"  {label}: 平均 {avg:.0f} MB（{[round(x) for x in v['mb']]}）"
              f" 回归线 {CEILING_MB} → {flag} | WebGL: {v['webgl']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
