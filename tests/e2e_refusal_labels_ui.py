# -*- coding: utf-8 -*-
"""拒绝类记录在界面上是不是真看得境——真机（无头可选）点击测试。

pytest 那侧锁的是 HTML 里有没有中文名、有没有筛选项；这里要看的是渲染后的东西：
用户 2026-10-07 说"该拒绝的也要给人家拒绝了，而且也要显示到前端"，
前端"显示"有两个会骗人的地方：
1. 标签函数没登记那个来源 → 行上直接印 family_filter / policy 这种内部键；
2. 筛选项没那一项 → 数据在盘上，用户筛不出来，等于没显示。
而且这次真踩到过第三种：工具在别的进程写 reply_records.json，被在线面板整份写回盖掉
（68 单拒绝在 BOSS 里有气泡、界面上筛不到），所以这条实测还要真的数得到行数。

绝不碰用户那份 bot_config.json：临时目录放副本，起第二个 Flask（默认 5059）。
只读页面、只点筛选下拉，不点任何发送/打招呼按钮，也不碰 BOSS。

运行：
    python tests/e2e_refusal_labels_ui.py
    BOSS_PROBE_HEADLESS=1 python tests/e2e_refusal_labels_ui.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from boss_bot.platform_compat import 终止进程

sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import e2e_greet_records_ui as ui  # noqa: E402
from e2e_greet_records_ui import check, close_browser, open_browser  # noqa: E402

results = ui.results
TEST_PORT = int(os.environ.get("BOSS_REFUSE_UI_PORT", "5059"))
PROBE_PORT = int(os.environ.get("BOSS_REFUSE_PROBE_PORT", "9347"))
URL = f"http://127.0.0.1:{TEST_PORT}"

# (筛选值, 下拉里的短名, 行内角标该显示的名字)
要看 = [("family_filter", "岗位类型拒绝", "岗位类型·拒绝"),
        ("reject_contact", "拒绝交换", "拒绝交换联系方式"),
        ("policy", "线下面试拒绝", "线下面试·拒绝")]

LAUNCHER = """
import sys, pathlib
sys.path.insert(0, %(root)r)
sys.path.insert(0, %(flask)r)
import boss_bot.unified_config as UC
tmp = pathlib.Path(%(tmp)r)
UC.BOT_CONFIG_FILE = tmp / "bot_config.json"
UC.OVERRIDES_FILE = tmp / "config_overrides.json"
UC.USER_PROFILE_FILE = tmp / "user_profile.json"
import app as A
A._ensure_config()
kwargs = {"host": "127.0.0.1", "port": %(port)d, "debug": False, "use_reloader": False}
if (getattr(A, "_socketio_kwargs", {}) or {}).get("async_mode") == "threading":
    kwargs["allow_unsafe_werkzeug"] = True
A.socketio.run(A.app, **kwargs)
"""


def start_server(tmp_dir):
    script = LAUNCHER % {"root": str(PROJECT_ROOT),
                         "flask": str(PROJECT_ROOT / "flask-version"),
                         "tmp": str(tmp_dir), "port": TEST_PORT}
    env = dict(os.environ, BOSS_BOT_CONFIG=str(tmp_dir / "bot_config.json"),
               PYTHONUTF8="1")
    proc = subprocess.Popen([sys.executable, "-c", script], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    import requests
    for _ in range(60):
        if proc.poll() is not None:
            raise AssertionError("实测用的 Flask 实例自己退了")
        try:
            if requests.get(URL + "/api/config", timeout=1).ok:
                return proc
        except Exception:
            time.sleep(0.5)
    proc.kill()
    raise AssertionError("实测用的 Flask 实例 30 秒内没起来")


def api_groups(src):
    """接口里"至少有一条该来源记录"的会话数——界面筛完剩下的行数应该就是这个数。"""
    import requests
    groups = requests.get(URL + "/api/reply_records/grouped", timeout=20).json().get("groups") or []
    记录 = sum(1 for g in groups for r in (g.get("records") or [])
              if r.get("reply_source") == src)
    会话 = sum(1 for g in groups
              if any(r.get("reply_source") == src for r in (g.get("records") or [])))
    return 会话, 记录


def 等数据载入(page, 秒=14):
    """按真人路径进「回复记录」标签页，等列表渲染出来。

    实测踩过两种假失败：①直接调 switchRecordTab 不触发加载，allReplyGroups 是空的；
    ②[0 0 []]——数据在飞但没落到 DOM。都得等真实渲染。
    """
    page.run_js("""
        var b = document.querySelector('.record-tab[onclick*="reply"]');
        if (b) b.click();
        return 1;
    """)
    for _ in range(int(秒 / 0.4)):
        time.sleep(0.4)
        行 = int(page.run_js(
            "return document.querySelectorAll('#replyTabContent .boss-chat-item').length;") or 0)
        if 行 > 0:
            return 行
    return 0


def pick_filter(page, src):
    """返回 (会话条数, 页面自己算出的"该来源占几个会话", 右侧气泡上的来源角标)。

    数会话条数只能用 .boss-chat-item 本身：那条列表里每条会话还嵌着
    avatar/info/name/job/preview/meta/time/badge 八九个子元素，
    用 [class*=chat-item] 会把 100 条数成 900 多，验证不到东西（这条踩过）。
    """
    page.run_js(f"""
        var s = document.getElementById('replyFilterSource');
        s.value = {json.dumps(src)};
        s.dispatchEvent(new Event('change'));
        return 1;
    """)
    time.sleep(1.8)
    return json.loads(page.run_js("""
        (function () {
            var 行 = document.querySelectorAll('#replyTabContent .boss-chat-item').length;
            var 组 = allReplyGroups.filter(function (g) {
                return (g.records || []).some(function (r) {
                    return r.reply_source === arguments0;
                });
            }).length;
            var 标 = [];
            var els = document.querySelectorAll('.boss-msg-source');
            for (var i = 0; i < els.length; i++) 标.push((els[i].textContent || '').trim());
            return JSON.stringify([行, 组, 标]);
        })()
    """.replace("arguments0", json.dumps(src))) or "[0, 0, []]")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="boss_refuse_ui_"))
    src_cfg = json.loads((PROJECT_ROOT / "bot_config.json").read_text(encoding="utf-8"))
    (tmp / "bot_config.json").write_text(
        json.dumps(src_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    server = None
    browser = proc = None
    try:
        server = start_server(tmp)
        ui.DEBUG_PORT = PROBE_PORT
        browser, proc = open_browser()
        page = browser
        page.get(URL)
        time.sleep(2.5)

        选项 = page.run_js(
            "return JSON.stringify([...document.querySelectorAll("
            "'#replyFilterSource option')].map(o => o.value + '|' + o.textContent.trim()))")
        表 = {}
        for one in json.loads(选项 or "[]"):
            v, t = one.split("|", 1)
            表[v] = t

        组总数 = 等数据载入(page)
        check("回复记录标签页真渲染出会话列表", int(组总数 or 0) > 0, f"渲染 {组总数} 个会话")
        for src, 下拉名, 角标名 in 要看:
            会话, 记录 = api_groups(src)
            if 记录 == 0:
                print(f"  （跳过 {src}：盘上一条都没有，没法验证显示）", flush=True)
                continue
            check(f"{src} 在下拉里叫「{下拉名}」",
                  表.get(src) == 下拉名, f"下拉里是 {表.get(src)!r}")
            行, 页面组数, 标 = pick_filter(page, src)
            check(f"筛「{下拉名}」剩 {行} 个会话（接口 {会话} 个 / {记录} 条，页面自算 {页面组数}）",
                  行 == 页面组数 > 0 and abs(行 - 会话) <= 1,
                  f"页面 {行} 行 / 页面自算 {页面组数} 组 / 接口 {会话} 组")
            check(f"来源筛选真的收窄了（不是把 {组总数} 个会话全留着）",
                  行 * 4 < int(组总数 or 0), f"{行} 行 / 全部 {组总数} 组")
            check(f"点开的会话里角标写着「{角标名}」",
                  角标名 in 标, f"页面角标：{标[:6]}")
            check(f"「{角标名}」没把内部键印出来",
                  not [x for x in 标 if x in ("family_filter", "reject_contact", "policy",
                                            "未登记来源")], f"看到 {标[:6]}")
    finally:
        if browser is not None:
            close_browser(proc)
        if server is not None:
            终止进程(server.pid)
        shutil_rmtree(tmp)

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + ("全部通过" if not failed
                  else f"失败 {len(failed)} 项: " + ", ".join(failed)), flush=True)
    return 1 if failed else 0


def shutil_rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
