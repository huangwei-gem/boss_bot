# -*- coding: utf-8 -*-
"""真机量：关掉"句柄自己那张标签页"会发生什么，以及哪一种救法在这台 DrissionPage 上真能用。

为什么反复量这个（第一版的结论是错的，留在这里当反例）：
  第一版 `rebind_search_tab()` 写的是 `page.set_tab(活页id)`，单测全绿。
  ① 这台 4.1.1.4 的 ChromiumPage **没有 set_tab**（实测 dir() 只有 tab/tab_id/
     tab_ids/get_tab/get_tabs/latest_tab/new_tab/activate_tab/close_tabs/tabs_count），
     照那一版上线，每次重绑都抛 AttributeError → 原样返回 None → 39 连败照旧。
  ② 我猜的坏法是"关掉别的标签页把活动指针带死"。实测否掉了：new_tab 之后
     句柄 tab_id 纹丝不动，关掉**别的**标签页后 `page.url` 照样读得到。
  → 真正的坏法只有一个方向：**关掉句柄自己钉住的那张页**。
     而 greet_engine 的④兜底遍历 `browser.tab_ids` 时只排除了回复引擎那张，
     自己那张搜索页也在候选里；命中输入框后把它注册成 `_greet_chat_tab`
     （greet_engine.py:3205），收尾 `close_greet_chat_tab()` 就把搜索页关了。
     时间线对得上：03:35:21「遍历 3 个标签页…在标签页 geek/chat 中找到输入框」
     → 03:35:52「已关闭打招呼引擎临时聊天标签页」→ 03:36:02 起 39 条断开。

这里量四件事（隔离实例：端口 9335 + 自己的 profile，只有本地 data: 页，
不上 BOSS、不登录、不碰任何发送路径）：
  A 关掉**别的**标签页 → 句柄好不好？（把第一版的错判据钉死）
  B 关掉**自己那张** → 句柄怎么坏、异常类型和消息长什么样；
  C 三种救法逐个试：new_tab 回来的 Tab 包成 BrowserInstance / get_tab(活页) /
    原对象继续用；顺带量 Tab 对象上 `_get_browser()`、`run_js`、`ele` 能不能撑起
    打招呼那一串调用——决定重绑到底该发什么对象。
用法：python -X utf8 tools/probe_tab_rebind.py
退出码：0=量清楚了（B 能复现坏，且 C 里有能用的救法）；1=没量出名堂；2=起不来浏览器。
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.browser_launcher import BrowserInstance, BrowserManager
from boss_bot.unified_config import UnifiedConfig

# 端口和 profile 抄 tools/verify_live_browser.py 那套已经跑通的隔离实例配方：
# 第一版用 %TEMP% 下的随机目录，cloakbrowser 直接连不上（BrowserConnectError，
# 而且异常消息是空的——和线上那条"断开恢复导航失败(...): "一模一样）。
端口 = 9335
ROOT = Path(__file__).resolve().parents[1]
隔离profile = ROOT / "browser_data" / "account_probe_rebind"
页A = "data:text/html,<title>A-is-search</title><h1>A"
页B = "data:text/html,<title>B-is-temp-chat</title><h1>B"
页C = "data:text/html,<title>C-is-reply</title><h1>C"


def 读(对象, 标签):
    try:
        return "好｜%-42s" % ((对象.url or "")[:40] or "(空)")
    except Exception as e:
        return "坏｜%s: %s" % (type(e).__name__, str(e)[:46] or "(空消息)")


def 试(标签, 动作):
    try:
        return "%-30s %s" % (标签, 读(动作(), 标签))
    except Exception as e:
        return "%-30s 抛 %s: %s" % (标签, type(e).__name__, str(e)[:40] or "(空消息)")


def main() -> int:
    cfg = UnifiedConfig.load()
    隔离profile.mkdir(parents=True, exist_ok=True)
    mgr = None
    try:
        try:
            mgr = BrowserManager(config=cfg.browser, account_index=9,
                                 port=端口, user_data_dir=str(隔离profile))
            mgr.launch()
        except Exception as e:
            print(f"起不来独立浏览器（端口 {端口}）: {type(e).__name__}: {str(e)[:120]}")
            return 2

        页 = mgr._instance.page                 # ChromiumPage（浏览器级）
        页.get(页A)
        自己的id = 页.tab_id
        别的 = 页.new_tab(页B)
        会话 = 页.new_tab(页C)
        mgr._chat_tab = BrowserInstance(chrome_page=会话)
        print(f"A0 句柄钉在 tab_id={自己的id[:8]}｜{试('初始', lambda: 页)}")

        别的.close()
        time.sleep(0.6)
        A = 页
        try:
            A读 = 读(A, "关别的页之后")
        except Exception as e:
            A读 = "坏 " + str(e)[:30]
        print(f"A1 关掉**别的**那张页之后句柄：{A读}｜tab_id 仍={页.tab_id[:8]}")

        # B：关句柄自己那张（= 线上 close_greet_chat_tab 关掉被误登记的搜索页）
        自己 = 页.get_tab(自己的id)
        自己.close()
        time.sleep(0.8)
        B读 = 试("B 句柄自己那张被关", lambda: 页)
        print(f"B  {B读}")

        # C：救法逐个试
        print("C1 " + 试("new_tab(搜索页) 回来的 Tab", lambda: 页.new_tab(页A)))
        候选 = 页.tab_ids
        print(f"C2 现在还剩 {len(候选)} 张：{[c[:8] for c in 候选]}")
        for tid in 候选:
            print(f"C3 get_tab({tid[:8]}) " + 试("  ", lambda t=tid: 页.get_tab(t)))

        # 把一张 Tab 包成 BrowserInstance，量打招呼那一串调用撑不撑得起
        活页 = 页.get_tab(候选[0])
        包装 = BrowserInstance(chrome_page=活页)
        撑 = {}
        撑["url"] = 试("包装.url", lambda: 包装.url)
        撑["run_js"] = 试("包装.run_js", lambda: 包装.run_js("1+1"))
        撑["ele"] = 试("包装.ele", lambda: 包装.ele("tag:h1", timeout=2))
        撑["_get_browser"] = 试("_get_browser", lambda: 包装._get_browser())
        try:
            b = 包装._get_browser()
            撑["browser.tab_ids"] = "好｜%d 张" % len(list(b.tab_ids))
        except Exception as e:
            撑["browser.tab_ids"] = "坏｜%s: %s" % (type(e).__name__, str(e)[:40] or "(空消息)")
        for k, v in 撑.items():
            print(f"C4 {k:18} {v}")

        复现了坏 = B读.startswith("B 句柄自己那张被关 坏")
        能救 = 撑["url"].startswith("好") and 撑["run_js"].startswith("好") \
            and 撑["browser.tab_ids"].startswith("好")
        if not 复现了坏:
            print("\n结论：关掉自己那张也不坏——那『页面断开』另有成因，别照着这个写 fix。")
            return 1
        if not 能救:
            print("\n结论：坏能复现，但包一张 Tab 撑不起打招呼的调用，重绑得换别的对象。")
            return 1
        print("\n结论：坏=关掉句柄自己那张页（和 03:35 的日志吻合）；"
              "能用的救法=拿一张活 Tab 包成 BrowserInstance（url/run_js/ele/"
              "_get_browser().tab_ids 全撑得住）。")
        return 0
    finally:
        if mgr is not None:
            try:
                mgr._instance.quit()
            except Exception:
                pass
        shutil.rmtree(隔离profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
