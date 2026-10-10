# -*- coding: utf-8 -*-
"""平台差异的收口层。

为什么要单独立一个模块：Windows 上每一处"只有我能跑"的写法，在 macOS 上都不会
报错，只会**静默失效**——端口归属守卫查到空表就"不拦"（2026-09-29 主号连着
别人的空壳浏览器跑了一整天就是这么来的）、窗口收起拿不到句柄就什么都不做、
破解版浏览器查找在非 Windows 上直接 return ""，于是启动时告警"未使用破解版"
永远在响，而真正的原因（mac 版没装/放在哪儿）没人说。

所以每个原语都收一个 `平台` 参数：这台 Windows 机器上就能把 mac 分支的结论测出来。
不写 `if 平台 is None` 之外的隐式分支，也不在这里做任何"看起来跨平台"的兜底。
"""
import logging
import os
import platform
import subprocess

logger = logging.getLogger("platform_compat")

# ── 平台判定 ────────────────────────────────────────────────
_当前 = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(
    platform.system().lower(), "linux")

IS_WINDOWS = _当前 == "windows"
IS_MACOS = _当前 == "macos"


def _平台(平台=None):
    return 平台 or _当前


# ── 破解版浏览器（cloakbrowser）─────────────────────────────
# Windows 分发是裸 chrome.exe；mac 分发一般是 .app（真可执行在 Contents/MacOS/ 下），
# 也有 Chrome for Testing 那种带 chrome-mac-<arch>/ 的裸二进制形态。
# 目录名和 app 名都不由我们决定，所以按已知的几种分发形状列候选，而不是猜一个。
_分发子目录 = ("", "chrome-mac", "chrome-mac-arm64", "chrome-mac-x64")
_可执行名 = {
    "windows": ("chrome.exe",),
    "macos": ("Chromium", "Google Chrome for Testing", "Google Chrome",
              "chrome", "chromium"),
    "linux": ("chrome", "chromium"),
}
_APP名 = {
    "macos": ("Chromium.app", "Google Chrome for Testing.app", "Google Chrome.app"),
    "windows": (),
    "linux": (),
}


def 破解版可执行名(平台=None):
    return _可执行名[_平台(平台)]


def _候选路径(根, 平台):
    """列出这台机器上破解版可能在哪：目录 × 分发子目录 × 裸名/app 内名。"""
    for 目录名 in ("cloakbrowser", "cloakbrowser-windows-x64", "cloakbrowser-macos"):
        基 = os.path.join(根, 目录名)
        for 子 in _分发子目录:
            中 = os.path.join(基, 子) if 子 else 基
            for 名 in _可执行名[平台]:
                yield os.path.join(中, 名)
            for app in _APP名[平台]:
                # .app 里真能 exec 的是 Contents/MacOS/<可执行>，名字一般跟 app 去扩展名
                for 名 in _可执行名[平台]:
                    yield os.path.join(中, app, "Contents", "MacOS", 名)


def 破解版路径(根, 平台=None, 存在=os.path.isfile, 大小=os.path.getsize):
    """在项目根目录里找破解版浏览器的可执行文件，找不到回空串。

    回空串不抛异常是刻意的：调用方要的是"降级到系统浏览器 + 明确告警"，
    而抛异常会把浏览器启动本身堵死（收起窗口那条也是同一套哲学）。
    `存在`/`大小` 可注入，为的是在这台 Windows 上把 mac 的查找结果测出来。
    """
    平台 = _平台(平台)
    指定 = os.environ.get("BOSS_CLOAKBROWSER", "").strip().strip('"')
    if 指定 and 存在(指定) and 大小(指定) > 1_000_000:
        return 指定
    for 候选 in _候选路径(根, 平台):
        # 1MB 是"别把占位文件当成装好了"的最低门槛（Windows 分支原有的规矩）
        try:
            if 存在(候选) and 大小(候选) > 1_000_000:
                return os.path.normpath(候选)
        except OSError:
            continue
    return ""


def 破解版缺失说明(平台=None):
    """给告警用的一句话：mac 上没找到破解版时，光说"没找到"是不够的。"""
    平台 = _平台(平台)
    if 平台 != "macos":
        return ""
    return ("macOS 需要把破解版浏览器放进 cloakbrowser/（认 .app 的 "
            "Contents/MacOS/<可执行>，也认 chrome-mac-arm64/ 这类裸二进制），"
            "或者把 bot_config.json 的 browser_path / 环境变量 BOSS_CLOAKBROWSER "
            "指到那个可执行文件。BOSS 直聘有风控，用系统原版 Chrome 会被识别。")


# ── 进程归属（端口守卫的原料）──────────────────────────────
_PS表头 = "  PID  COMMAND\n"


def 解析ps进程表(文本):
    """把 `ps -axo pid=,command=` 的输出拆成 [{pid, port, user_data_dir, exe}]。

    不收没有 --remote-debugging-port 的进程：守卫只问"这个端口上挂的是谁的
    浏览器"，把整个进程表收进来只会增加误判面。mac/linux 上 ps 能看到**所有用户**
    的命令行（不需要 root），而 net_connections 不看 root 就只看得见自己的进程，
    所以这条才是 mac 上靠得住的查法。
    """
    from boss_bot.browser_launcher import parse_browser_cmdline

    记录 = []
    for 行 in (文本 or "").splitlines():
        行 = 行.strip()
        if not 行 or 行.upper().startswith("PID"):
            continue
        pid文本, _, 命令行 = 行.partition(" ")
        命令行 = 命令行.strip()
        if not 命令行 or "remote-debugging-port" not in 命令行:
            continue
        try:
            pid = int(pid文本)
        except ValueError:
            continue
        try:
            记录.append(parse_browser_cmdline(命令行, pid=pid))
        except (TypeError, ValueError):
            continue
    return 记录


def 跑进程表(平台=None, 跑=None):
    """拿到"所有带调试端口的进程"的原始文本；拿不到回空串（守卫退化成不拦）。

    `跑` 在调用时才取 subprocess.run（不在参数里绑死），否则测试打不进假的执行器，
    就只能靠真机控制台编码碰运气了。
    """
    平台 = _平台(平台)
    跑 = 跑 or subprocess.run
    if 平台 == "windows":
        命令 = ["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or Name='msedge.exe'\""
                " | ForEach-Object { if ($_.CommandLine -match 'remote-debugging-port')"
                " { [string]$_.ProcessId + '|' + $_.CommandLine } }"]
        分隔 = "|"
    else:
        命令 = ["ps", "-axo", "pid=,command="]
        分隔 = " "
    try:
        结果 = 跑(命令, capture_output=True, timeout=10)
    except Exception:
        return ""
    原始 = 结果.stdout or b""
    if isinstance(原始, bytes):
        # 不能无条件 text=True：Windows 控制台按 cp936 输出时 utf-8 解码会在读取线程里抛，
        # 表现成"一台机器上一个浏览器都查不到"→ 守卫静默失效（真机踩过）。
        for 编码 in ("utf-8", "gbk", "cp936"):
            try:
                文本 = 原始.decode(编码)
                break
            except UnicodeDecodeError:
                continue
        else:
            文本 = 原始.decode("utf-8", "replace")
    else:
        文本 = 原始
    if 平台 == "windows":
        return "\n".join(行.replace("|", " ", 1) for 行 in 文本.splitlines()
                         if "|" in 行)
    return 文本


# ── 报错文案里的处置命令（人要照着敲的）────────────────────
def 终止命令(pid, 平台=None):
    if _平台(平台) == "windows":
        return f"taskkill /PID {pid} /T /F"
    return f"kill -9 {pid}"


def 终止进程(pid, 平台=None, 跑=None):
    """真的结束一个进程（测试脚本收尾自己起的临时面板用）。

    Windows 走 taskkill /T 连子进程一起收；mac/linux 只有 kill -9 这一发，
    **子进程（浏览器）会留在桌面上**，所以调用方该通过 CDP 关的还是得先关。
    """
    pid = int(pid or 0)
    if pid <= 0:
        return False
    跑 = 跑 or subprocess.run
    平台 = _平台(平台)
    try:
        if 平台 == "windows":
            跑(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
        else:
            # POSIX 的 SIGKILL 恒为 9。不 import signal 拿常量是因为 Windows 上
            # 根本没有 SIGKILL —— 在这里会撞成 AttributeError，然后被下面的
            # except 吞成"没杀掉"，比 9 这个数字难查得多。
            os.kill(pid, 9)
        return True
    except Exception as e:
        logger.debug(f"终止 {pid} 失败: {e}")
        return False


# ── 路径比较（profile 归属判定）───────────────────────────
def 归一化路径键(路径, 平台=None):
    """把 --user-data-dir 的值折成可比较的键。

    不能直接用 os.path.normcase：它在非 Windows 上什么都不做，于是 mac 上比较
    靠的必须是"分隔符与尾斜杠统一"，同时**保留大小写**（APFS 默认区分大小写，
    把 account_0 和 Account_0 折成一个是错的判定）。
    """
    文本 = str(路径 or "").strip().strip('"')
    if not 文本:
        return ""
    文本 = 文本.replace("\\", "/").rstrip("/")
    if _平台(平台) == "windows":
        return 文本.lower()
    return 文本


# ── 剪贴板（多行汇报只能粘，微信里回车就是发送）─────────────
def 写剪贴板(文本, 平台=None, 跑=None):
    """把多行文本放进剪贴板，成功返回 True。

    Windows 走"落一个 UTF-8 临时文件 → PowerShell 读进去"：文本直接塞命令行会被
    引号和换行吃掉，覆盖 env 又让 powershell 找不到自己。mac 用 pbcopy 的标准输入，
    喂的是 UTF-8 字节，不经过 shell，所以没有引号问题。
    """
    import tempfile

    跑 = 跑 or subprocess.run
    平台 = _平台(平台)
    if 平台 == "windows":
        句柄, 路径 = tempfile.mkstemp(suffix=".txt", text=True)
        try:
            with os.fdopen(句柄, "w", encoding="utf-8") as f:
                f.write(文本)
            跑(["powershell", "-NoProfile", "-Command",
                 f"Get-Content -LiteralPath '{路径}' -Raw -Encoding UTF8 | Set-Clipboard"],
                check=True, capture_output=True)
            return True
        finally:
            try:
                os.unlink(路径)
            except OSError:
                pass
    if 平台 == "macos":
        跑(["pbcopy"], input=str(文本 or "").encode("utf-8"), check=True,
           capture_output=True)
        return True
    return False


# ── 浏览器进程名（内存/存活统计按名字筛进程用）──────────────
def 浏览器进程名(平台=None):
    """按平台给出 Chrome 系进程的 name 形态。

    Windows 上是 chrome.exe/msedge.exe；mac 上裸二进制叫什么基本由分发决定
    （破解版可能叫 Chromium，也可能叫 Google Chrome for Testing），
    所以给的是"够用的候选集"，调用方自己知道筛不到就是筛不到。
    """
    平台 = _平台(平台)
    if 平台 == "windows":
        return ("chrome.exe", "msedge.exe")
    return ("chrome", "chromium", "google chrome", "google chrome for testing",
            "google chrome canary", "microsoft edge")


# ── 窗口操作（mac 用 osascript，Windows 那套 ctypes 留在原处）──
def 构造苹果脚本(动作, pids):
    """生成 AppleScript：把指定 pid 的窗口前置或收进 Dock。

    权限缺失（辅助功能没授权）时 osascript 报 -1719/-25211，这条脚本把它按住不弹
    对话框：无人值守的循环里一旦弹系统授权框，面板就卡在那儿等人点。
    """
    pid列表 = ", ".join(str(int(p)) for p in (pids or ()))
    动作行 = {
        "前置": "set frontmost of theProc to true",
        "收起": "set visible of theProc to false",
    }[动作]
    return (
        'tell application "System Events"\n'
        f'  -- 目标进程 pid：{pid列表}\n'
        f'  set theProcs to (every process whose unix id is in {{{pid列表}}})\n'
        '  repeat with theProc in theProcs\n'
        '    try\n'
        f'      {动作行}\n'
        '    on error number errNum\n'
        '      -- -1719 = 辅助功能未授权；-25211 = 进程已退出。都不该把启动堵死\n'
        '      if errNum is not -1719 and errNum is not -25211 then error number errNum\n'
        '    end try\n'
        '  end repeat\n'
        'end tell'
    )
