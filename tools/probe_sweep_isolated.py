# -*- coding: utf-8 -*-
"""隔离实例上真跑一遍 sweep_idle_tabs：单元用假对象过的那一关，真页面对象也得过。

为什么要单独量这个：`sweep_idle_tabs` 靠 `页.browser` 拿到浏览器对象再遍历 tab_ids。
单元测试里那张假页是我自己造的，`browser` 属性是手工挂上去的；线上到底是
ChromiumPage 还是 ChromiumTab 撑着、`.browser` 给不给、keep 集里那三个 id 读不读得到，
都得在真 DrissionPage 对象上看一眼（`dir()` 显示 Page/Tab 都有 browser，但属性存在
不等于取得到）。

红线：只用这个脚本自己起的隔离实例（独立端口 + 独立 profile），开的是本机
file:// 页面（路径里带 job_detail 这个词，用来喂判据），不碰 9222/9223，
不发任何网络请求、不点任何发送。
"""
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot.browser_launcher import BrowserManager, launch_browser  # noqa: E402
from boss_bot.unified_config import 新建临时目录  # noqa: E402

PORT = 9337
岗位页张数 = 3


def main() -> int:
    临时目录 = 新建临时目录("sweep_probe_")
    岗位文件 = 临时目录 / "job_detail_probe.html"
    岗位文件.write_text("<html><body>probe</body></html>", encoding="utf-8")
    在用的 = 临时目录 / "chat_like.html"
    在用的.write_text("<html><body>keep</body></html>", encoding="utf-8")
    要清的 = ["file:///" + str(岗位文件).replace("\\", "/")]
    要留的 = ["file:///" + str(在用的).replace("\\", "/")]

    配置 = type("C", (), {"headless": True, "viewport_width": 1280,
                          "viewport_height": 800, "user_agent": "", "proxy": ""})()
    实例 = None
    管 = None
    出口 = 1
    探针目录 = ROOT / "browser_data" / "account_probe_sweep"
    shutil.rmtree(探针目录, ignore_errors=True)   # 隔夜 profile 会让 new_tab 超时
    try:
        实例 = launch_browser(headless=True, port=PORT, background=True,
                              user_data_dir=str(探针目录))
        页 = 实例._get_active()
        浏览器 = getattr(页, "browser", None)
        print("页对象类型=%s  .browser=%s" % (type(页).__name__, type(浏览器).__name__))
        if 浏览器 is None:
            print("红：真页对象取不到 .browser，sweep 会直接回 0")
            return 出口

        for u in 要留的:
            浏览器.new_tab(u)
        for u in 要清的:
            for _ in range(岗位页张数):
                浏览器.new_tab(u)
        time.sleep(1.5)
        print("开完一共 %d 张标签页" % len(浏览器.tab_ids))

        管 = BrowserManager(配置, account_index=9, port=PORT)
        管._instance = 实例
        管._search_tab = 实例
        # 搜索页钉住的那张必须活下来：这一条就是 10-16 那次自杀的形状
        钉 = 页
        # 按 URL 挑"回复在用的那张"，别按 tab_ids 的下标猜：上一版按 `[-1-张数]` 取，
        # newtab 的位置一挪就把一张 job_detail 当成在用页登记进去，清扫只关了 2 张
        # ——门是绿的还是错的，全看这一行挑得对不对。
        留url = "file:///" + str(在用的).replace("\\", "/")
        聊天 = next(t for t in (浏览器.get_tab(i) for i in 浏览器.tab_ids)
                    if t.url == 留url)
        管._chat_tab = type(实例)(chrome_page=聊天)

        关 = 管.sweep_idle_tabs()
        剩 = list(浏览器.tab_ids)
        print("sweep 关掉 %d 张，剩 %d 张" % (关, len(剩)))
        try:
            钉url = 页.url
        except Exception as e:
            钉url = "读不到: %s" % e
        print("搜索页还在吗：%s" % 钉url)
        留住了 = 聊天.url if 聊天.tab_id in 剩 else "被误关了"
        print("回复用的那张：%s" % 留住了)

        if 关 != 岗位页张数:
            print("红：应当关掉 %d 张岗位详情页，实际 %d" % (岗位页张数, 关))
            return 出口
        if "读不到" in 钉url:
            print("红：搜索页被 sweep 关掉了（自杀）")
            return 出口
        if 留住了 == "被误关了":
            print("红：keep 集没护住在用的那张")
            return 出口
        print("绿：清扫按 id 保护、只收岗位详情页，真对象上成立")

        # ── 第二阶段：HTTP 那条看守路 ──
        # 线上证不了它（要等一次真的渲染进程冻结），所以在这里量：它只发
        # `GET /json` 与 `GET /json/close/{id}`，不碰 DrissionPage 的 websocket。
        from boss_bot.browser_launcher import port_owner_pid, tree_memory_mb
        for _ in range(3):
            浏览器.new_tab(要清的[0])
        time.sleep(1.5)
        前张 = len(浏览器.tab_ids)
        前内存 = tree_memory_mb(port_owner_pid(PORT) or 0)
        关 = 管.sweep_idle_tabs_http()
        time.sleep(1.0)
        后张 = len(浏览器.tab_ids)
        后内存 = tree_memory_mb(port_owner_pid(PORT) or 0)
        print("HTTP 看守：%d 张 → 报关掉 %d 张 → 剩 %d 张；进程树 %.0f → %.0f MB"
              % (前张, 关, 后张, 前内存, 后内存))
        if 关 != 3 or 后张 != 前张 - 3:
            print("红：HTTP 看守没把该收的岗位详情页收掉")
            return 出口
        try:
            钉url2 = 页.url
        except Exception as e:
            钉url2 = "读不到: %s" % e
        if 聊天.tab_id not in list(浏览器.tab_ids) or "读不到" in 钉url2:
            print("红：HTTP 看守把在册的两张之一关掉了（钉住页=%s）" % 钉url2)
            return 出口
        print("绿：HTTP 看守同样按 id 保护，收页 %d 张、省 %.0f MB" % (关, 前内存 - 后内存))
        出口 = 0
        return 出口
    finally:
        if 管 is not None and getattr(管, "_instance", None) is not None:
            try:
                管.close()
            except Exception as e:
                print("收尾关浏览器失败（不影响结论）:", e)
        elif 实例 is not None:
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
