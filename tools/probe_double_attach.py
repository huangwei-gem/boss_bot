# -*- coding: utf-8 -*-
"""分清两件事：一张页"WS 连不上"是因为已经被 DrissionPage 占着，还是因为它真冻住了。

为什么要分清：2026-10-16 只读探 9223，9 张 page 里 6 张开新 WS 6 秒不答话，我据此
判过"渲染进程被内存饿死"，并顺着这条猜错了一整轮根因。另一种说法是 Chrome 一个
target 只让一个 DevTools 客户端 attach——DP 占着的那几张，第二个进程去连本来就挂。
两种说法的修法完全不同：前者要减页腾内存，后者说明那次探测根本读不出什么。

这一份把两种页都造出来，然后**另起一个进程**去连（同进程里 DP 共用浏览器级会话，
测不出"第二个客户端"这件事——上一版就是这么测错的）：
  - DP 抓过句柄的页（`get_tab()` 过）
  - DP 压根没碰过的页（只 `new_tab()` 过）
结论只看一件事：这两类页在第二个进程眼里是不是一快一慢。
全程 file:// 页面、自己的端口(9339)和自己的 profile，不发网络请求、不点任何发送。
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.unified_config import 新建临时目录  # noqa: E402

PORT = 9339


def main() -> int:
    临时目录 = 新建临时目录("attach_probe_")
    岗位文件 = 临时目录 / "job_detail_probe.html"
    岗位文件.write_text("<html><body>probe</body></html>", encoding="utf-8")
    干净文件 = 临时目录 / "untouched.html"
    干净文件.write_text("<html><body>keep</body></html>", encoding="utf-8")
    岗位url = "file:///" + str(岗位文件).replace("\\", "/")
    干净url = "file:///" + str(干净文件).replace("\\", "/")

    探针目录 = ROOT / "browser_data" / "account_probe_attach"
    shutil.rmtree(探针目录, ignore_errors=True)
    实例 = None
    try:
        实例 = launch_browser(headless=True, port=PORT, background=True,
                              user_data_dir=str(探针目录))
        页 = 实例._get_active()
        浏览器 = 页.browser
        抓过 = 浏览器.new_tab(岗位url)
        time.sleep(1.0)
        句柄 = 浏览器.get_tab(抓过.tab_id)          # DP 抓住这张，一直到结束才放
        没碰 = 浏览器.new_tab(干净url)              # 只开不 get_tab
        time.sleep(1.0)
        print("DP 抓过句柄的页 id=%s" % 句柄.tab_id)
        print("DP 没碰过的页   id=%s" % 没碰.tab_id)

        正文 = subprocess.run([sys.executable, str(ROOT / "tools" / "probe_cdp_reply.py"),
                               str(PORT)],
                              capture_output=True, text=True, timeout=180,
                              env={**os.environ, "PYTHONUTF8": "1"})
        出 = 正文.stdout + 正文.stderr
        for 行 in 出.splitlines():
            print("  " + 行)
        抓过挂 = ("Connection timed out" in 出)
        print("判读：第二个进程里「DP 抓过句柄的那张」和「没碰过的那张」——"
              + ("两种都一样快 ⇒ 线上当初那 6 张挂住是真的不应答，不是被占着"
                 if not 抓过挂 else
                 "有挂住的：还要看挂的是不是恰好 DP 碰过的那几张"))
        return 0
    finally:
        if 实例 is not None:
            try:
                实例._get_browser().quit()
            except Exception as e:
                print("收尾 quit 失败（不影响结论）:", e)
        for _ in range(6):
            shutil.rmtree(临时目录, ignore_errors=True)
            if not 临时目录.exists():
                break
            time.sleep(0.5)
        shutil.rmtree(探针目录, ignore_errors=True)
        print("临时目录已清:", not 临时目录.exists())


if __name__ == "__main__":
    sys.exit(main())
