# -*- coding: utf-8 -*-
"""真机验证"启动浏览器不抢焦点"：前台窗口前后必须是同一个句柄。

日志说"已收进任务栏"不算数 —— 只有 GetForegroundWindow() 启动前后没变、
而且浏览器窗口自己确实处于最小化态，才叫真的在幕后跑。

只用临时端口 + 临时 profile，不碰线上两个号；不点任何发送类按钮。
"""
import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

from boss_bot.browser_launcher import (  # noqa: E402
    CHROME_WINDOW_CLASS, _enumerate_top_windows, current_foreground_window,
    launch_browser,
)
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402


def browser_windows(pid: int) -> list:
    return [w for w in _enumerate_top_windows()
            if w[1] == CHROME_WINDOW_CLASS and w[4] == pid and w[2]]


def cookie_file(account_index: int) -> Path:
    cfg = UnifiedConfig()
    accounts = cfg.greet.accounts or []
    name = "zhipin_cookies.json"
    if account_index < len(accounts):
        name = accounts[account_index].cookie_file or name
    return Path(str(resolve_path(name)))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=9420)
    p.add_argument("--background", choices=("0", "1"), default="1")
    p.add_argument("--headless", choices=("0", "1"), default="0")
    p.add_argument("--account", type=int, default=0)
    a = p.parse_args()

    ck = cookie_file(a.account)
    sha_before = hashlib.sha256(ck.read_bytes()).hexdigest()[:16] if ck.is_file() else "<缺失>"

    profile = Path(tempfile.mkdtemp(prefix="boss_bg_probe_"))
    # 焦点先给记事本一样的东西：直接在终端里跑，前台就是终端自己
    fg_before = current_foreground_window()
    print(f"启动前前台窗口 = {fg_before}")

    instance = None
    ok = False
    try:
        instance = launch_browser(
            headless=a.headless == "1", background=a.background == "1",
            port=a.port, user_data_dir=str(profile),
        )
        pid = int(instance._page.browser.process_id or 0)
        time.sleep(1.0)
        fg_after = current_foreground_window()
        wins = browser_windows(pid)
        iconic = [w for w in wins if w[3]]
        print(f"浏览器 PID={pid} 顶层可见窗口 {len(wins)} 个，其中最小化 {len(iconic)} 个")
        for w in wins:
            print(f"  hwnd={w[0]} 类名={w[1]} 可见={w[2]} 最小化={w[3]} pid={w[4]}")
        print(f"启动后前台窗口 = {fg_after}")

        # 必须带 Cookie：空 profile 上去 BOSS 直接弹登录页，那不是"后台跑坏了"
        instance.load_cookies(str(ck))
        instance.get("https://www.zhipin.com/web/geek/chat")
        time.sleep(6)
        rows = len(instance.eles(".friend-content", timeout=5) or [])
        print(f"聊天页侧栏行数 = {rows} URL={instance.url}")

        no_steal = fg_after == fg_before
        minimized = bool(wins) and len(iconic) == len(wins)
        if a.background == "1" and a.headless == "0":
            ok = no_steal and minimized and rows > 0
            print(f"结论：不抢焦点={'是' if no_steal else '否'} "
                  f"窗口已收起={'是' if minimized else '否'} 页面可用={'是' if rows else '否'}")
        else:
            ok = rows > 0
            print(f"结论：对照组（background={a.background}, headless={a.headless}）"
                  f"页面可用={'是' if rows else '否'} 抢焦点={'否' if no_steal else '是'}")
        sha_after = hashlib.sha256(ck.read_bytes()).hexdigest()[:16] if ck.is_file() else "<缺失>"
        print(("Cookie 文件未被改动 ✓" if sha_before == sha_after
               else f"警告：Cookie 文件被改动 ✗ {sha_before} → {sha_after}"))
        return 0 if (ok and sha_before == sha_after) else 1
    finally:
        if instance is not None:
            try:
                instance.quit()
            except Exception:
                pass
        shutil.rmtree(str(profile), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
