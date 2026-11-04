# -*- coding: utf-8 -*-
"""一键启动：缺料自检 → （面板没跑就自己拉一个）→ 把配置里的号拉起来 → 回读状态确认真的在跑。

为什么要有这个文件：start.bat 只做了一半。面板起来以后两个号还是停着的，
求职者还得自己在界面上点两次"启动"——而"点了没反应/点了两次"恰恰是最常报的现场
（10-16 05:23、10-18 11:23 都是号没起来但没人知道）。这里把"起来"和**"起来之后
确认真的在跑"**收进一次调用：不看 POST 返回的那句"已启动"就下结论，
要等 /api/status 里这个号真的 running，或者照实说出它卡在哪儿。

为什么连"起面板"也在这儿（--launch）：cmd 读带中文的 UTF-8 批处理会走偏
（2026-10-23 实测：`set PYTHONUTF8=1` 被当成 `YTHONUTF8=1` 执行，REM 注释里的半截中文
被当成命令跑，`goto` 落错标签）。所以 start.bat/start.sh 只剩几行 ASCII，
所有判断和中文提示都这份 Python 说了算——一个出口，两端口径不会各说各话。

只读优先的三条：
- 不发任何清空/删除类请求；只 POST /api/accounts/<i>/start。
- 回环地址免鉴权（access_guard 那条），所以本地一键不需要 token；
  真要经隧道控制面板，把 --token 传进来或让 bot_config.json 的 api_token 生效。
- 面板已经在跑时它照样能用（就是"补起号"这一步），而且**绝不去杀面板那个口的端口**：
  旧的 start.bat 第 [4/6] 步是 `python kill_port.py 5000`，那玩意"强力终止占用指定端口的
  进程"，面板自己已经有单实例守卫，判断端口有没有人应答就够了。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from boss_bot.unified_config import 面板端口 as _面板端口  # 端口只有一个出处

# 这一份脚本从头到尾在往外印中文和 ✓（11-04 实测：把它输出重定向进文件就当场
# `UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'` 崩在半路——
# Windows 上 stdout 不是控制台时 Python 用 ANSI 代码页编码，而 `start.bat` 里那句
# `set PYTHONUTF8=1` 只在双击那条路上有）。谁从别处调它（脚本、计划任务、日志重定向）
# 都会撞上，而崩的位置在半路：面板已经起来了，号却没交上去，看的人只看到一句 traceback。
# 这里自己把三个流拧成 UTF-8，不再靠调用方记得带环境变量。
for _流 in (sys.stdout, sys.stderr):
    try:
        _流.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # 老 Python 或被换成了非文本流：不拦，脚本照常跑

默认等待秒 = 180      # 冷启动：浏览器 24~70 秒 + 复验最多 90 秒，留点余量
等面板秒 = 240        # 实测这台机器 app.py 从进程起来到应答要 60 秒上下
轮询秒 = 5


def _读配置(root):
    """从 bot_config.json 取"该起哪几个号"，判据只用他写下的原文，不补全。"""
    文件 = Path(root) / "bot_config.json"
    try:
        配 = json.loads(文件.read_text(encoding="utf-8"))
    except Exception as e:
        print("✗ 读不到 %s（%s: %s）" % (文件, type(e).__name__, str(e)[:60]))
        return None, ""
    号 = 配.get("accounts")
    if not isinstance(号, list) or not 号:
        print("✗ bot_config.json 里没有 accounts 这一段，不知道该起哪个号。")
        print("  在界面上加一个账号，或照现有那份的写法补上 name 字段。")
        return None, ""
    return [str(a.get("name") or "账号%d" % i) for i, a in enumerate(号)], str(
        配.get("api_token") or "")


def _请求(base, 路径, 方法="GET", token="", 等秒=10):
    头 = {"Content-Type": "application/json"}
    if token:
        头["X-API-Token"] = token
    要求 = urllib.request.Request(base + 路径, headers=头, method=方法)
    with urllib.request.urlopen(要求, timeout=等秒) as 响:
        return json.loads(响.read().decode("utf-8") or "{}")


def 等面板(base, 等秒, token):
    """问 /api/status 直到有人答。答"404/连不上"都算没人。"""
    截止 = time.time() + 等秒
    第一次 = True
    while time.time() < 截止:
        try:
            _请求(base, "/api/status", token=token, 等秒=4)
            return True
        except urllib.error.HTTPError:
            return True        # 有服务在答（哪怕这个路径不对），面板是起来的
        except Exception as e:
            if 第一次:
                print("   等面板就绪…（%s）" % type(e).__name__)
                第一次 = False
            time.sleep(2)
    return False


def 拉起来(base, 数量, token, 等秒):
    """POST start 之后**必须回读**：界面那句"已启动"只说明请求收到了。

    已经在跑的号不再发第二次 start——面板那边 `if self._running: return` 不会
    把号弄坏，但日志里会留下一条「主循环已在运行中」，看着像有人重复点过。
    """
    名s = {}
    try:
        for a in (_请求(base, "/api/status", token=token).get("accounts") or []):
            if a.get("running"):
                名s[a.get("index")] = a
    except Exception:
        pass
    for i in range(数量):
        if i in 名s:
            print("· 号%d 已经在跑，不重复发 start" % i)
            continue
        try:
            回 = _请求(base, "/api/accounts/%d/start" % i, 方法="POST", token=token)
            print("→ 号%d 已交面板：%s" % (i, 回.get("message") or 回.get("status") or "无回执"))
        except Exception as e:
            print("✗ 号%d 起不动（%s: %s）——面板可能没在跑，或者端口不对"
                  % (i, type(e).__name__, str(e)[:70]))

    截止 = time.time() + 等秒
    还差 = [i for i in range(数量) if i not in 名s]
    while 还差 and time.time() < 截止:
        time.sleep(轮询秒)
        try:
            状态 = _请求(base, "/api/status", token=token)
        except Exception:
            continue        # 面板刚起那会儿 /api/status 会有一小段读不到，别当成掉线
        for a in (状态.get("accounts") or []):
            idx = a.get("index")
            if idx not in 还差:
                continue
            名s[idx] = a
            if a.get("running"):
                还差.remove(idx)
        # 一眼都没读到账号也别说"没起来"：冷启动时 /api/status 的 accounts 是空的，
        # 要等控制类接口把 manager 建出来才填（这条骗过我好几次）
    return 名s


def 汇报(名s, 数量, 等够了):
    坏了 = 0
    for i in range(数量):
        a = 名s.get(i)
        if not a:
            print("✗ 号%d 在 /api/status 里读不到——面板没把它建出来，去看 logs/boss_bot.log 最后几行" % i)
            坏了 += 1
            continue
        名 = a.get("name") or ("号%d" % i)
        if a.get("running"):
            if a.get("needs_login"):
                print("⚠ %s：在跑，但**要你先动一下**（%s）——窗口已经摆出来了就照它做"
                      % (名, a.get("login_reason") or "等登录"))
            else:
                print("✓ %s：在跑（phase=%s）" % (名, a.get("phase") or "-"))
        else:
            坏了 += 1
            print("✗ %s：没在跑。面板说 phase=%s reason=%s"
                  % (名, a.get("phase") or "-", a.get("login_reason") or "-"))
    if not 等够了:
        print("（等满 %d 秒还有号没报到，上面这几行是截止那一刻的状态）" % 默认等待秒)
    return 坏了


def 自检(root):
    """缺料要说清缺什么、去哪儿拿——不许"看起来起来了"再让它半路炸。"""
    缺 = []
    if not (Path(root) / "flask-version" / "app.py").exists():
        缺.append("flask-version/app.py 不在：这份副本不完整，git clone 全了再跑")
    浏览器 = Path(root) / "cloakbrowser"
    可执行 = [p for p in (浏览器 / "chrome.exe", 浏览器 / "Google Chrome for Testing.app",
                        浏览器 / "chrome") if p.exists()]
    if not 浏览器.is_dir():
        缺.append("cloakbrowser/ 不在：这是项目自带的浏览器，仓库里超 100MB 没入库，"
                  "要单独拿那一份放到 %s" % 浏览器)
    elif not 可执行 and os.name == "nt":
        缺.append("cloakbrowser/ 里没找到 chrome.exe：那份浏览器没解压完整")
    for 模 in ("flask", "flask_socketio", "DrissionPage", "openai", "openpyxl"):
        try:
            __import__(模)
        except Exception:
            缺.append("Python 包 %s 没装：pip install -r requirements.txt" % 模)
    if not (Path(root) / "bot_config.json").exists():
        缺.append("bot_config.json 不在：先跑一次 python -c \"from boss_bot.unified_config "
                  "import UnifiedConfig; UnifiedConfig.load().save()\"")
    return 缺


def 面板已在跑(base, token=""):
    """问一次就算，不排队等：这一问决定要不要另起一个面板。"""
    try:
        _请求(base, "/api/status", token=token, 等秒=3)
        return True
    except urllib.error.HTTPError:
        return True        # 有人在家，哪怕这个路径不对
    except Exception:
        return False


def _后台拉号(base, 号数, token, 等秒):
    """面板冷启动到应答实测 60 秒上下，所以拉号必须先等它开口再动手。"""
    if not 等面板(base, 等面板秒, token):
        print("✗ %d 秒内 %s 没人应答：面板没起来，上面面板窗口的红字或 logs/panel_io.log 有原因"
              % (等面板秒, base))
        return
    名s = 拉起来(base, 号数, token, 等秒)
    汇报(名s, 号数, bool(名s))


def 一键(root, port, token_cli, 等秒):
    """start.bat / start.sh 的唯一入口：自检 → 该不该起面板 → 起号 → 回读。"""
    print("[1/4] 缺料自检（Python 包 / 自带浏览器 / 配置）...")
    缺 = 自检(root)
    for 一 in 缺:
        print("✗", 一)
    if 缺:
        print("  补齐上面带 ✗ 的几行，再双击 start.bat（或 ./start.sh）。")
        return 1
    print("✓ 依赖、浏览器、配置都在")

    号, 盘上token = _读配置(root)
    if 号 is None:
        return 1
    token = token_cli if token_cli is not None else 盘上token
    base = "http://127.0.0.1:%d" % port
    print("[2/4] 要起的号：%s" % "、".join(号))

    if 面板已在跑(base, token):
        print("· %d 上已经有面板在跑——不再起第二个，也不去动它的端口。" % port)
        print("  这一步只补一件事：把还没跑的号交面板，并回读每个号的真实状态。")
        名s = 拉起来(base, len(号), token, 等秒)
        坏了 = 汇报(名s, len(号), bool(名s))
        print("访问地址: http://localhost:%d" % port)
        return 1 if 坏了 else 0

    面板入口 = Path(root) / "flask-version" / "app.py"
    if not 面板入口.exists():
        print("✗ 未找到 %s，项目结构不完整（git clone 全了再跑）" % 面板入口)
        return 1
    print("· %d 空着，这个窗口就是面板：关窗口或按 Ctrl+C 就停。" % port)
    print("[3/4] 另开一条腿等面板就绪，然后把号拉起来…")
    threading.Thread(target=_后台拉号, args=(base, len(号), token, 等秒), daemon=True).start()
    print("[4/4] 启动面板（flask-version\\app.py）…")
    子 = subprocess.Popen([sys.executable, "app.py"], cwd=str(面板入口.parent))
    try:
        收场 = 子.wait()
    except KeyboardInterrupt:
        # Ctrl+C 是发给整个窗口的，先让面板自己收；10 秒还挂着才替它收尾（只碰这一个 PID）
        try:
            收场 = 子.wait(timeout=10)
        except subprocess.TimeoutExpired:
            print("\n面板没接住 Ctrl+C，这里终止它自己起的 PID %d" % 子.pid)
            子.terminate()
            收场 = 0
    if 收场 in (0, 1):
        # 退出码 1 有两种完全不同的样子：他按了 Ctrl+C（正常收场），
        # 和面板自己"拒绝起第二个"（app.py 里那句 sys.exit(1) 是故意的，
        # 就是不让脚本把让路读成起好了）。光看码分不开，就去问一口：
        # 有人应答说明让路是真的，这个进程白跑一趟；没人应答才是正常停了。
        if 收场 == 1 and 面板已在跑(base, token):
            print("✗ 面板主动让路了：%d 上已经有一个在跑，刚才那次 start 没建成第二个。"
                  "要重起请先在界面上停，或找到正在跑的那个 python app.py。" % port)
            return 1
        print("服务已停止")
        return 0
    print("✗ 面板退出码 %s，看上面的报错" % 收场)
    return 1


def main():
    p = argparse.ArgumentParser(description="等面板就绪并把配置里的号拉起来（回读才算数）")
    p.add_argument("--root", default=str(ROOT))
    # 默认口从 unified_config 那一份读（10-28 起是 5001）。这里再自己算一遍
    # BOSS_PANEL_PORT，就会有一天"面板听 5001、启动器还在等 5000"——等不到应答的
    # 假死根本看不出是端口不一致
    p.add_argument("--port", type=int, default=_面板端口())
    p.add_argument("--等秒", "--wait", type=int, default=默认等待秒)
    p.add_argument("--token", default=None, help="非回环访问时的口令，默认读 bot_config.json")
    # 中文名照旧能用，同时收 ASCII 别名：控制台代码页不是 65001 时，命令行里的中文会被
    # 切成乱码传给 argparse（10-23 实测：`--自检` 变成 `--鑷\ue045\ue5c5`，argparse 报
    # unrecognized arguments，双击启动卡在第 2 步、面板根本没起来）。start.bat 现在只发 ASCII。
    p.add_argument("--只读", "--readonly", dest="只读", action="store_true",
                   help="只看状态，不发 start")
    p.add_argument("--自检", "--selfcheck", dest="自检", action="store_true",
                   help="只查缺料，不连面板")
    p.add_argument("--launch", "--一键", dest="launch", action="store_true",
                   help="一键全流程：自检 + 该起面板就起 + 起号 + 回读（start.bat 只调这个）")
    参 = p.parse_args()

    if 参.launch:
        return 一键(参.root, 参.port, 参.token, 参.等秒)

    if 参.自检:
        缺 = 自检(参.root)
        for 一 in 缺:
            print("✗", 一)
        if not 缺:
            print("✓ 依赖、浏览器、配置都在")
        return 1 if 缺 else 0

    号, 盘上token = _读配置(参.root)
    if 号 is None:
        return 1
    token = 参.token if 参.token is not None else 盘上token
    base = "http://127.0.0.1:%d" % 参.port
    print("· 要起的号：%s" % "、".join(号))
    if not 等面板(base, 等面板秒, token):
        print("✗ %d 秒内 %s 没人应答：面板没起来。看 flask-version 那个窗口的红字"
              "（面板自己会把标准输出和报错抄一份进 logs/panel_io.log）" % (等面板秒, base))
        return 1
    if 参.只读:
        状态 = _请求(base, "/api/status", token=token)
        名s = {a.get("index"): a for a in (状态.get("accounts") or [])}
        print("（只读）当前状态：")
        汇报(名s, len(号), True)
        return 0
    名s = 拉起来(base, len(号), token, 参.等秒)
    return 1 if 汇报(名s, len(号), bool(名s)) else 0


if __name__ == "__main__":
    sys.exit(main())
