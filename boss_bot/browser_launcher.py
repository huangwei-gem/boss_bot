"""跨平台统一浏览器管理器

合并 auto_boss 和 BOSS-auto-reply-bot 的浏览器启动器，提供：

- 跨平台支持（Windows/macOS/Linux）
- 便携版 Chrome 优先检测（cloakbrowser-windows-x64/）
- BrowserInstance 封装类（屏蔽 Windows/macOS 差异）
- detect_available_browsers() 检测系统浏览器
- set_preferred_browser() / get_preferred_browser() 用户偏好
- _find_best_browser_path() 自动查找最佳浏览器
- launch_browser() 统一启动接口
- BrowserManager 高层管理器（共享实例、多标签页、登录状态、Cookie 管理）

DrissionPage 在 macOS arm64 上的 bug 说明：
启动 Chrome 后，从 /json/version 获取的 browser_id 与实际 WebSocket URL 不匹配，
导致 WebSocket 握手返回 404。因此 macOS 采用手动启动 Chrome + Chromium 连接的方式绕过。
"""

import os
import re
import sys
import time
import json
import subprocess
import platform
import shutil
import socket
import tempfile
import logging
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from typing import Optional

from boss_bot.unified_config import resolve_path

logger = logging.getLogger("browser_launcher")

# 平台检测
_IS_MACOS = platform.system().lower() == "darwin"
_IS_WINDOWS = platform.system().lower() == "windows"

# 本进程起过的浏览器：端口 → 是否无头。cloakbrowser 会伪装 UA，问浏览器问不出
# "是不是无头"，只能自己记（界面/监控显示真实模式用的就是这份）。
LAUNCHED_MODES: dict = {}

# 实测承载 BOSS 直聘登录态的 Cookie 名（缺这些或过期即需要重新登录）
BOSS_AUTH_COOKIES = ("wt2", "zp_at", "bst", "wbg")

# Cookie 文件是用户唯一的登录会话凭据。覆盖写之前先留一份，只保留最近这么多份：
# 全留着会把几百 KB 的会话堆成垃圾山，但一份都没有就是 2026-09-29 那样——
# 判定误报"过期"直接把文件删了，人工连"当时里面到底是什么"都查不了。
COOKIE_BACKUP_KEEP = 5

# ──────────────────────────────────────────────────────────────
# 后台运行（窗口不前置、不抢焦点）
# ──────────────────────────────────────────────────────────────
# Chrome 把"最小化/被遮挡"的窗口当后台标签页省电：定时器降到每分钟一次、
# requestAnimationFrame 直接停。自动化在这种窗口上表现为"元素点得到但等不到
# 渲染"，所以一旦允许后台就必须同时关掉这三档节流。
BACKGROUND_FLAGS = (
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-background-timer-throttling',
)

# SW_SHOWMINNOACTIVE：最小化但不把焦点从这个窗口抢走（SW_MINIMIZE 会，
# 于是"收到后台"变成了"每启动一次就把用户正在打的字打断"）
SW_SHOWMINNOACTIVE = 7
# SW_RESTORE：从任务栏/最小化里回到桌面。要人工扫码登录时用它把窗口捞回来。
SW_RESTORE = 9
CHROME_WINDOW_CLASS = "Chrome_WidgetWin_1"


def current_foreground_window() -> int:
    """当前拿到焦点的窗口句柄（0 = 拿不到）。非 Windows 恒为 0。

    启动浏览器前后各读一次，两次相同才算"没有把窗口顶到最前面"。
    """
    if not _IS_WINDOWS:
        return 0
    try:
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def is_browser_window(class_name: str, visible: bool, iconic: bool,
                      pid: int, owner_pids, include_iconic: bool = False) -> bool:
    """这个顶层窗口是不是"还挂在桌面上、属于我们这个浏览器"。

    Chrome_WidgetWin_1 同时是隐藏的消息窗口和 DevTools 窗口的类名，所以
    必须再要 visible；已经最小化（iconic）的不重复处理。

    include_iconic 是给"把窗口捞回桌面"用的：那条"已经最小化就跳过"是收起
    动作的省工判断，捞回来时最小化的恰恰就是目标，照它筛会一个都挑不出来
    （12:26 那次日志里的"0 个窗口"就是这么来的）。
    """
    return bool(class_name == CHROME_WINDOW_CLASS and visible
                and (include_iconic or not iconic)
                and pid and pid in set(owner_pids or ()))


def pick_browser_windows(windows, owner_pids, include_iconic: bool = False) -> list:
    """从 [(hwnd, 类名, 可见, 已最小化, pid)] 里挑出要收起来/捞回来的窗口句柄。"""
    return [w[0] for w in windows
            if is_browser_window(w[1], w[2], w[3], w[4], owner_pids, include_iconic)]


def _enumerate_top_windows() -> list:
    """列出桌面上所有顶层窗口：[(hwnd, 类名, 可见, 已最小化, 所属 pid)]。

    拿不到（非 Windows、ctypes 异常）就返回空表 —— 后台化是加分项，
    绝不能因为收不了窗口而把浏览器启动本身堵死。
    """
    if not _IS_WINDOWS:
        return []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
    except Exception:
        return []

    out = []
    buf = ctypes.create_unicode_buffer(256)
    proc_id = wintypes.DWORD()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _collect(hwnd, _lparam):
        try:
            user32.GetClassNameW(hwnd, buf, 256)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            out.append((int(hwnd), buf.value, bool(user32.IsWindowVisible(hwnd)),
                        bool(user32.IsIconic(hwnd)), int(proc_id.value)))
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(_collect, 0)
    except Exception:
        return []
    return out


def minimize_browser_windows(pid: int, timeout: float = 8.0) -> int:
    """把这个浏览器的窗口收进任务栏，焦点留在原处不动。返回处理了几个窗口。"""
    if not _IS_WINDOWS or not pid:
        return 0
    import ctypes
    user32 = ctypes.windll.user32

    handles = pick_browser_windows(_enumerate_top_windows(), (pid,))
    for hwnd in handles:
        try:
            user32.ShowWindowAsync(ctypes.c_void_p(hwnd), SW_SHOWMINNOACTIVE)
        except Exception:
            continue
    if not handles:
        return 0
    # 窗口是异步收起来的，等它真 minimize 再返回；等不到也只当"没挡住流程"
    end = time.time() + timeout
    while time.time() < end:
        if all(user32.IsIconic(ctypes.c_void_p(h)) for h in handles):
            return len(handles)
        time.sleep(0.2)
    return len(handles)


def restore_browser_windows(pid: int, user32=None, timeout: float = 6.0) -> int:
    """把这个浏览器进程的窗口从任务栏/最小化里捞回桌面并前置。返回处理了几个窗口。

    只治"有窗口但看不见"。无头模式下桌面上压根就没有窗口，这个函数救不了，
    那种形态要先把浏览器重开成有头 —— 见 BrowserManager.show_login_window。

    跨进程的 ShowWindowAsync 偶尔不生效（投递线程正忙、或者前台权限被系统挡下），
    所以要回看一眼：还缩着就再补一次同步 ShowWindow，最后统一前置一次。
    不这么兜的话面板会报"窗口已弹出"，用户桌面上却还是任务栏里那一条。
    """
    if not _IS_WINDOWS or not pid:
        return 0
    import ctypes
    真系统调用 = user32 is None
    user32 = ctypes.windll.user32 if 真系统调用 else user32
    # 真的时候包成句柄对象（64 位下裸 int 会被截断），测试传假对象时原样给
    包 = (lambda h: ctypes.c_void_p(h)) if 真系统调用 else (lambda h: h)
    handles = pick_browser_windows(_enumerate_top_windows(), (pid,),
                                   include_iconic=True)
    if not handles:
        return 0

    def 来一下(名, hwnd, *参数):
        try:
            getattr(user32, 名)(包(hwnd), *参数)
        except Exception:
            pass

    for hwnd in handles:
        来一下("ShowWindowAsync", hwnd, SW_RESTORE)

    end = time.time() + timeout
    while time.time() < end:
        try:
            还缩着 = [h for h in handles if user32.IsIconic(包(h))]
        except Exception:
            还缩着 = []
        if not 还缩着:
            break
        for hwnd in 还缩着:
            来一下("ShowWindow", hwnd, SW_RESTORE)
        time.sleep(0.4)

    for hwnd in handles:
        来一下("SetForegroundWindow", hwnd)
    return len(handles)



def _cookie_backup_dir() -> str:
    return str(resolve_path(Path("data") / "cookie_backups"))


def backup_cookie_file(path: str, backup_dir: str = None,
                       keep: int = COOKIE_BACKUP_KEEP) -> str:
    """把现有 Cookie 文件复制进备份目录，返回备份路径；原文件不动。"""
    src = Path(str(path))
    if not src.is_file():
        return ""
    dst_dir = Path(backup_dir or _cookie_backup_dir())
    dst_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    # 序号补零并且"绝不复用已删除的号"：靠 while dest.exists() 找空位的话，
    # 一份备份被轮换删掉后，下一次会顶回那个最靠前的名字，按名字排序轮换时就
    # 把最新那份当最旧的删了（第一版就是这么错的）。
    seqs = []
    prefix = src.stem + "_"
    for p in dst_dir.glob(f"{src.stem}_*{src.suffix}"):
        tail = p.stem[len(prefix):].rsplit("_", 1)[-1]
        if tail.isdigit():
            seqs.append(int(tail))
    dest = dst_dir / f"{prefix}{stamp}_{(max(seqs) + 1) if seqs else 0:04d}{src.suffix}"
    shutil.copy2(str(src), str(dest))
    for old in sorted(dst_dir.glob(f"{src.stem}_*{src.suffix}"))[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass
    return str(dest)


# ──────────────────────────────────────────────────────────────
# 便携版浏览器检测
# ──────────────────────────────────────────────────────────────

def _get_portable_browser_path() -> str:
    """获取项目内置便携浏览器的路径

    查找位置（按优先级）：
    1. 项目根目录下的 cloakbrowser-windows-x64/chrome.exe
    2. 当前工作目录下的 cloakbrowser-windows-x64/chrome.exe
    3. browser_launcher.py 同级目录的上级的 cloakbrowser-windows-x64/chrome.exe

    Returns:
        便携浏览器路径，找不到返回空字符串
    """
    if not _IS_WINDOWS:
        return ""

    # 可能的路径列表（cloakbrowser 优先，兼容旧目录名）
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    possible_paths = [
        # 项目根目录/cloakbrowser/chrome.exe（新目录名）
        os.path.join(_project_root, "cloakbrowser", "chrome.exe"),
        # 项目根目录/cloakbrowser-windows-x64/chrome.exe（旧目录名）
        os.path.join(_project_root, "cloakbrowser-windows-x64", "chrome.exe"),
        # 当前工作目录
        os.path.join(os.getcwd(), "cloakbrowser", "chrome.exe"),
        os.path.join(os.getcwd(), "cloakbrowser-windows-x64", "chrome.exe"),
    ]

    for portable_path in possible_paths:
        portable_path = os.path.normpath(portable_path)
        if os.path.isfile(portable_path):
            size = os.path.getsize(portable_path)
            # 确保是真正的 Chrome（>1MB），不是空文件或占位文件
            if size > 1_000_000:
                return portable_path

    return ""


# ──────────────────────────────────────────────────────────────
# 浏览器路径查找
# ──────────────────────────────────────────────────────────────

def _find_chrome_path() -> str:
    """自动查找 Chrome/Chromium 可执行文件路径（跨平台）

    Returns:
        浏览器路径，找不到返回空字符串
    """
    if _IS_MACOS:
        mac_paths = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Chromium.app/Contents/MacOS/Chromium',
            '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary',
        ]
        for p in mac_paths:
            if os.path.isfile(p):
                return p
        # 尝试 which
        for name in ('google-chrome', 'chromium', 'chrome'):
            path = shutil.which(name)
            if path:
                return path

    elif _IS_WINDOWS:
        # 优先使用项目内置便携浏览器
        portable = _get_portable_browser_path()
        if portable:
            return portable

        win_paths = [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),
            os.path.expandvars(r'%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe'),
        ]
        for p in win_paths:
            if os.path.isfile(p):
                return p
        path = shutil.which('chrome') or shutil.which('chrome.exe')
        if path:
            return path

    else:  # Linux
        for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
            path = shutil.which(name)
            if path:
                return path

    return ""


def _find_edge_path() -> str:
    """自动查找 Microsoft Edge 可执行文件路径（跨平台）

    Returns:
        浏览器路径，找不到返回空字符串
    """
    if _IS_MACOS:
        mac_paths = [
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            '/Applications/Microsoft Edge Canary.app/Contents/MacOS/Microsoft Edge Canary',
        ]
        for p in mac_paths:
            if os.path.isfile(p):
                return p
        path = shutil.which('microsoft-edge') or shutil.which('msedge')
        if path:
            return path

    elif _IS_WINDOWS:
        win_paths = [
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe'),
            os.path.expandvars(r'%PROGRAMFILES%\Microsoft\Edge\Application\msedge.exe'),
            os.path.expandvars(r'%PROGRAMFILES(X86)%\Microsoft\Edge\Application\msedge.exe'),
        ]
        for p in win_paths:
            if os.path.isfile(p):
                return p
        path = shutil.which('msedge') or shutil.which('msedge.exe')
        if path:
            return path

    else:  # Linux
        for name in ('microsoft-edge', 'microsoft-edge-stable', 'msedge'):
            path = shutil.which(name)
            if path:
                return path

    return ""


# ──────────────────────────────────────────────────────────────
# 系统浏览器检测
# ──────────────────────────────────────────────────────────────

def detect_available_browsers() -> dict:
    """检测系统上安装了哪些浏览器

    Returns:
        {名称: 路径} 字典，名称包括 "portable", "chrome", "edge", "chromium"
    """
    found = {}

    if _IS_MACOS:
        checks = {
            "chrome": '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            "edge": '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            "chromium": '/Applications/Chromium.app/Contents/MacOS/Chromium',
        }
        for name, path in checks.items():
            if os.path.isfile(path):
                found[name] = path
        # 尝试 which 兜底
        for name, cmd in (("chrome", "google-chrome"), ("edge", "microsoft-edge"), ("chromium", "chromium")):
            if name not in found:
                path = shutil.which(cmd)
                if path:
                    found[name] = path

    elif _IS_WINDOWS:
        # 优先检测项目内置便携浏览器
        portable = _get_portable_browser_path()
        if portable:
            found["portable"] = portable

        checks = {
            "chrome": [
                r'C:\Program Files\Google\Chrome\Application\chrome.exe',
                r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
                os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
            ],
            "edge": [
                r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            ],
            "chromium": [],
        }
        for name, paths in checks.items():
            for p in paths:
                if p and os.path.isfile(p):
                    found[name] = p
                    break
        # which 兜底
        if not found:
            for name, cmd in (("chrome", "chrome"), ("edge", "msedge")):
                p = shutil.which(cmd)
                if p:
                    found[name] = p

    else:  # Linux
        for name, cmds in (
            ("chrome", ("google-chrome", "google-chrome-stable")),
            ("edge", ("microsoft-edge", "microsoft-edge-stable")),
            ("chromium", ("chromium", "chromium-browser")),
        ):
            for cmd in cmds:
                path = shutil.which(cmd)
                if path:
                    found[name] = path
                    break

    return found


# ──────────────────────────────────────────────────────────────
# 用户偏好管理
# ──────────────────────────────────────────────────────────────

# 用户手动选择的浏览器（运行时缓存）
_preferred_browser: str = ""


def set_preferred_browser(name: str):
    """设置用户偏好的浏览器

    Args:
        name: 浏览器名称，如 "chrome", "edge", "chromium", "portable"
    """
    global _preferred_browser
    _preferred_browser = name.lower().strip() if name else ""


def get_preferred_browser() -> str:
    """获取用户偏好的浏览器

    Returns:
        浏览器名称，未设置返回空字符串
    """
    return _preferred_browser


# ──────────────────────────────────────────────────────────────
# 系统默认浏览器检测
# ──────────────────────────────────────────────────────────────

def _detect_default_browser() -> str:
    """检测系统默认浏览器

    采用各平台原生 API 进行精确检测：
    - macOS: 读取 LaunchServices plist 文件
    - Windows: 读取注册表 ProgId
    - Linux: 使用 xdg-mime 查询

    Returns:
        "chrome" / "edge" / "chromium" / ""（检测失败时返回空字符串）
    """
    try:
        if _IS_MACOS:
            # 读取 LaunchServices plist 获取默认 http 处理程序
            import plistlib
            plist_path = os.path.expanduser(
                "~/Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
            )
            if not os.path.isfile(plist_path):
                plist_path = os.path.expanduser(
                    "~/Library/Preferences/com.apple.LaunchServices.plist"
                )
            if os.path.isfile(plist_path):
                with open(plist_path, "rb") as f:
                    plist = plistlib.load(f)
                handlers = plist.get("LSHandlers", [])
                for h in handlers:
                    if h.get("LSHandlerURLScheme") == "http":
                        bundle_id = h.get("LSHandlerAllRolesAllTypes", "").lower()
                        if "chrome" in bundle_id:
                            return "chrome"
                        if "edge" in bundle_id:
                            return "edge"
                        if "chromium" in bundle_id:
                            return "chromium"
                        break

        elif _IS_WINDOWS:
            # 读注册表获取默认浏览器 ProgId
            try:
                import winreg
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice"
                )
                progid, _ = winreg.QueryValueEx(key, "ProgId")
                winreg.CloseKey(key)
                progid = progid.lower()
                if "chrome" in progid:
                    return "chrome"
                if "edge" in progid:
                    return "edge"
                if "chromium" in progid:
                    return "chromium"
            except Exception:
                pass

        else:
            # Linux: xdg-mime 查询默认浏览器
            try:
                result = subprocess.run(
                    ["xdg-mime", "query", "default", "x-scheme-handler/http"],
                    capture_output=True, text=True, timeout=5
                )
                desktop = result.stdout.strip().lower()
                if "chrome" in desktop:
                    return "chrome"
                if "edge" in desktop:
                    return "edge"
                if "chromium" in desktop:
                    return "chromium"
            except Exception:
                pass

    except Exception:
        pass

    return ""


# ──────────────────────────────────────────────────────────────
# 最佳浏览器查找
# ──────────────────────────────────────────────────────────────

def _find_best_browser_path(browser_type: str = "chrome") -> tuple:
    """自动查找最佳浏览器路径

    BOSS 直聘有反爬风控，原版 Chrome/Edge 会被 navigator.webdriver 等检测点识别，
    因此项目内置的便携破解版（cloakbrowser/）优先级高于系统默认浏览器。

    优先级：用户显式偏好 > 内置破解版 > 配置指定 > 系统默认 > 兜底

    Args:
        browser_type: 配置指定的浏览器类型，如 "chrome", "edge", "chromium"

    Returns:
        (browser_path, browser_type) 元组，找不到返回 ("", "")
    """
    available = detect_available_browsers()
    if not available:
        return ("", "")

    # 1. 用户手动选择的偏好浏览器
    if _preferred_browser and _preferred_browser in available:
        return (available[_preferred_browser], _preferred_browser)

    # 2. 项目内置破解版（反检测），高于系统默认浏览器
    if "portable" in available:
        return (available["portable"], "portable")

    # 3. 配置指定的浏览器类型
    if browser_type in available:
        return (available[browser_type], browser_type)

    # 4. 系统默认浏览器
    default = _detect_default_browser()
    if default and default in available:
        return (available[default], default)

    # 5. 按优先级兜底: portable > chrome > edge > chromium
    for key in ("portable", "chrome", "edge", "chromium"):
        if key in available:
            return (available[key], key)

    return ("", "")


# ──────────────────────────────────────────────────────────────
# 网络工具
# ──────────────────────────────────────────────────────────────

def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """检查端口是否开放"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def _wait_for_port(host: str, port: int, timeout: float = 15.0) -> bool:
    """等待端口开放"""
    start = time.time()
    while time.time() - start < timeout:
        if _is_port_open(host, port):
            return True
        time.sleep(0.5)
    return False


def _get_ws_url(host: str, port: int, retries: int = 5) -> str:
    """从 /json/version 获取 WebSocket URL"""
    for attempt in range(retries):
        try:
            resp = urlopen(f'http://{host}:{port}/json/version', timeout=3)
            data = json.loads(resp.read())
            ws_url = data.get('webSocketDebuggerUrl', '')
            if ws_url:
                return ws_url
        except (URLError, OSError, json.JSONDecodeError) as e:
            logger.debug(f"获取 ws_url 第{attempt+1}次失败: {e}")
            time.sleep(1)
    return ""


def _owner_ttl_sec() -> float:
    return 60.0


_OWNER_CACHE: dict = {}      # port -> (时间戳, 是不是我们的)


def port_owner_pid(port: int) -> Optional[int]:
    """这个调试端口上是哪个进程在听（拿不到返回 None）。"""
    try:
        import psutil
    except ImportError:
        return None
    try:
        for c in psutil.net_connections(kind="tcp"):
            if (c.status == "LISTEN" and c.laddr and int(c.laddr.port) == int(port)
                    and c.pid):
                return int(c.pid)
    except Exception as e:
        logger.debug(f"查 {port} 的监听进程失败: {e}")
    return None


def port_is_ours(port: int) -> bool:
    """端口上的浏览器是不是本项目起的——按进程命令行里的项目路径判。

    为什么要问这个：9223 实测被另一个项目（boss-auto-apply 的 cloakbrowser，
    profile 在 Temp/DrissionPage/userData/9223）占着，而 /json/version 照样答
    "Chrome/146 在跑"。只看端点会把"我们的号"报成在跑，而它其实停着；
    挂浏览器的那点活儿还会做到别人的窗口上。
    查不到进程就当真是在跑的：拿"我看不清"去定罪，会把好日子说成故障。
    """
    import time as _t
    hit = _OWNER_CACHE.get(int(port))
    if hit and _t.time() - hit[0] < _owner_ttl_sec():
        return hit[1]
    pid = port_owner_pid(port)
    if pid is None:
        ours = True
    else:
        try:
            import psutil
            cmd = " ".join(psutil.Process(pid).cmdline()).lower()
        except Exception:
            ours = True
        else:
            ours = str(Path(__file__).resolve().parent.parent).lower() in cmd
    _OWNER_CACHE[int(port)] = (_t.time(), ours)
    return ours


def browser_mode(port: int, host: str = "127.0.0.1") -> dict:
    """这个调试端口上的浏览器：在不在跑、是不是无头、按什么判的。

    两个坑，都是实测撞出来的：
    1) 界面上原来只有"无头"开关，说的是**配置**；浏览器是启动那一刻定型的，
       改完没重启、或者那个端口上根本是别人的浏览器（9223 被别的项目占过），
       光看开关一律看不出来——所以我原来只能去翻进程命令行。
    2) **不能拿 UA 判无头**：cloakbrowser 是反指纹的，`--headless=new` 起来之后
       UA 里的 HeadlessChrome 被抹平了（实测 9222 进程命令行 headless=YES，
       而 /json/version 报的是普通 Chrome/146 UA）。所以无头与否以我们自己
       启动时记下的那份为准，UA 只用来发现"端口上是别人起的浏览器"。
    """
    out = {"running": False, "headless": None, "port": port,
           "browser": "", "依据": "", "与启动记录不符": False, "本项目": True}
    try:
        with urlopen(f'http://{host}:{port}/json/version', timeout=3) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.debug(f"读 {port} 的浏览器形态失败（多半没在跑）: {e}")
        out["headless"] = LAUNCHED_MODES.get(port)          # 端口没答，但记过就是起过
        out["依据"] = "启动记录（端口未响应）"
        return out
    ua = str(data.get("User-Agent") or "")
    browser = str(data.get("Browser") or "")
    launched = LAUNCHED_MODES.get(port)
    if not port_is_ours(port):
        out["browser"] = browser
        out["本项目"] = False
        out["headless"] = None
        out["依据"] = "端口上的浏览器不是本项目的（这一号其实没在跑）"
        return out
    out["running"] = True
    out["browser"] = browser
    if launched is None:
        # 不是我们起的：只能报 UA 看到的，并说明依据
        out["headless"] = "Headless" in ua or "Headless" in browser
        out["依据"] = "UA（不是我们启动的浏览器）"
    else:
        out["headless"] = bool(launched)
        out["依据"] = "本进程启动记录"
        out["与启动记录不符"] = ("Headless" in ua) and not launched
    return out


def _find_free_port() -> int:
    """找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


# ──────────────────────────────────────────────────────────────
# BrowserInstance 封装类
# ──────────────────────────────────────────────────────────────

class BrowserInstance:
    """浏览器实例封装

    提供统一的 API，屏蔽 Windows/macOS 差异。
    暴露 ChromiumPage 或 tab 的常用方法。

    Windows 模式：使用 ChromiumPage（DrissionPage 原生）
    macOS 模式：手动启动 Chrome 子进程 + Chromium 连接（绕过 WebSocket bug）
    """

    def __init__(self, chrome_page=None, chromium=None, tab=None, process=None):
        self._page = chrome_page      # Windows: ChromiumPage
        self._chromium = chromium     # macOS: Chromium
        self._tab = tab               # macOS: 当前 tab
        self._process = process       # macOS: Chrome 子进程

    def _get_active(self):
        """返回当前活动的页面对象"""
        if self._page is not None:
            return self._page
        return self._tab

    def _get_browser(self):
        """获取 Chromium 浏览器对象（用于 CDP 调用）"""
        if self._page is not None and hasattr(self._page, 'browser'):
            return self._page.browser
        return self._chromium

    # ── 常用方法代理 ──

    def ele(self, selector, timeout=None):
        """查找单个元素"""
        obj = self._get_active()
        if timeout is not None:
            return obj.ele(selector, timeout=timeout)
        return obj.ele(selector)

    def eles(self, selector, timeout=None):
        """查找多个元素"""
        obj = self._get_active()
        if timeout is not None:
            return obj.eles(selector, timeout=timeout)
        return obj.eles(selector)

    def get(self, url):
        """导航到指定 URL"""
        return self._get_active().get(url)

    @property
    def url(self):
        """当前页面 URL"""
        return self._get_active().url

    @property
    def title(self):
        """当前页面标题"""
        return self._get_active().title

    @property
    def scroll(self):
        """滚动控制对象"""
        return self._get_active().scroll

    def run_js(self, script, *args, as_expr: bool = False):
        """执行 JavaScript。

        as_expr 必须转发给底层：DrissionPage 的 run_js 把它定义成关键字参数，
        这里不收的话 `run_js(js, as_expr=True)` 会直接 TypeError，
        调用方的 try/except 把异常吞掉，于是验证码探针从来没真正跑过。
        """
        return self._get_active().run_js(script, *args, as_expr=as_expr)

    @property
    def set(self):
        """设置对象"""
        return self._get_active().set

    def cookies(self, as_dict=False):
        """获取当前页面的 cookies"""
        obj = self._get_active()
        # DrissionPage >= 4.1 新版签名: cookies(all_domains=False, all_info=False)
        try:
            return obj.cookies(as_dict=as_dict)
        except TypeError:
            # 新版不支持 as_dict，手动转换
            raw = obj.cookies()
            if as_dict:
                return {c.get('name', ''): c.get('value', '') for c in raw}
            return raw

    @property
    def listen(self):
        """监听对象"""
        return self._get_active().listen

    def refresh(self):
        """刷新当前页面"""
        return self._get_active().refresh()

    def close_current_tab(self):
        """关闭本实例包装的那个标签页（精确关闭，不影响其它标签页）。

        DrissionPage 4.1 的标签页对象只有 close()，没有 close_current_tab()；
        而浏览器级对象的 close() 会带走整个浏览器，所以按对象类型区分处理。
        """
        obj = self._page if self._page is not None else self._tab
        if obj is None:
            return
        if hasattr(obj, "get_tabs"):
            logger.warning("close_current_tab 作用在浏览器级对象上，已跳过以避免关闭整个浏览器")
            return
        obj.close()

    def quit(self):
        """关闭浏览器"""
        try:
            if self._page is not None:
                self._page.quit()
            elif self._chromium is not None:
                self._chromium.quit()
        except Exception as e:
            logger.warning(f"关闭浏览器异常: {e}")
        finally:
            # 确保子进程被终止
            if self._process is not None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass

    @property
    def current_tab(self):
        """返回当前 tab（macOS 模式下可用）"""
        return self._tab

    @property
    def browser(self):
        """返回浏览器对象（macOS 模式下可用）"""
        return self._chromium

    @property
    def page(self):
        """返回当前活动页面"""
        return self._get_active()

    def new_tab(self, url=""):
        """新建 tab

        Returns:
            新建的 tab 对象
        """
        if self._chromium is not None:
            return self._chromium.new_tab(url)
        elif self._page is not None:
            return self._page.new_tab(url)
        return None

    # ── Cookie 管理（跨平台，使用 CDP） ──

    def save_cookies(self, filepath: str):
        """保存 cookies 到文件（跨平台，使用 CDP）

        使用 CDP Storage.getCookies 获取浏览器所有 cookies（包括 HttpOnly），
        而非 tab.cookies，因为需要跨域名获取所有 cookie。

        Args:
            filepath: 保存路径
        """
        try:
            cookies = self._get_all_cookies()
            filepath = str(resolve_path(filepath))
            if filepath in ("", "."):
                raise ValueError("未指定 Cookie 保存路径")
            try:
                kept = backup_cookie_file(filepath)
                if kept:
                    logger.info(f"覆盖前已备份旧 Cookie 到 {Path(kept).name}")
            except OSError as e:
                # 备份失败不拦登录写入，但必须说清楚：这份文件是唯一凭据
                logger.warning(f"Cookie 备份失败，本次将直接覆盖且没有底: {e}")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            logger.info(f"已保存 {len(cookies)} 个 Cookie 到 {filepath}")
        except Exception as e:
            logger.error(f"save_cookies 失败: {e}")
            raise

    def load_cookies(self, filepath: str) -> bool:
        """从文件加载 cookies（跨平台，使用 CDP）

        使用 CDP Storage.setCookies 设置跨域名 cookie。

        Args:
            filepath: cookie 文件路径

        Returns:
            加载成功返回 True，文件不存在或加载失败返回 False
        """
        filepath = str(resolve_path(filepath))
        if filepath in ("", "."):
            return False
        if not os.path.exists(filepath):
            return False
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            if not cookies:
                return False
            self._set_cookies(cookies)
            logger.info(f"已从 {filepath} 加载 {len(cookies)} 个 Cookie")
            return True
        except Exception as e:
            logger.error(f"load_cookies 失败: {e}")
            return False

    def _get_all_cookies(self) -> list:
        """获取浏览器所有 cookies（包括 HttpOnly）

        使用 CDP Storage.getCookies 而非 tab.cookies，
        因为需要跨域名获取所有 cookie。
        """
        browser = self._get_browser()
        if browser is not None:
            cks = browser._run_cdp('Storage.getCookies')['cookies']
            return list(cks)
        return list(self._get_active().cookies(all_info=True))

    def _set_cookies(self, cookies: list):
        """设置 cookies

        使用 CDP Storage.setCookies 而非 tab.set.cookies，
        因为需要设置跨域名 cookie。
        """
        browser = self._get_browser()
        if browser is not None:
            browser._run_cdp('Storage.setCookies', cookies=cookies)
        else:
            # 兜底：用 document.cookie
            for c in cookies:
                name = c.get("name", "")
                value = c.get("value", "")
                domain = c.get("domain", "")
                path = c.get("path", "/")
                if domain:
                    self._get_active().run_js(
                        f"document.cookie = '{name}={value}; domain={domain}; path={path};'"
                    )


# ──────────────────────────────────────────────────────────────
# 浏览器启动
# ──────────────────────────────────────────────────────────────

def launch_browser(
    headless: bool = False,
    user_agent: str = "",
    proxy: str = "",
    viewport_width: int = 1280,
    viewport_height: int = 800,
    port: int = 0,
    chrome_path: str = "",
    browser_type: str = "chrome",
    user_data_dir: str = "",
    background: bool = True,
    extra_args: tuple = (),
) -> BrowserInstance:
    """启动浏览器（跨平台，支持 Chrome、Edge、Chromium）

    显式给了端口时，把"这次是不是无头启动的"记进 LAUNCHED_MODES（见下方赋值）：
    cloakbrowser 会抹掉 UA 里的 HeadlessChrome，界面上要显示真实形态只能靠自己这份记录。

    Args:
        headless: 是否无头模式
        user_agent: 自定义 User-Agent
        proxy: 代理地址
        viewport_width: 视口宽度
        viewport_height: 视口高度
        port: 调试端口（0 表示自动选择）
        chrome_path: 浏览器路径（空则自动检测）
        browser_type: 浏览器类型，"chrome" / "edge" / "chromium"
        background: 有头模式下把窗口直接收进任务栏，不前置、不抢用户焦点
        extra_args: 追加的 Chrome 启动参数（只用于内存参数的 A/B 实测）

    Returns:
        BrowserInstance: 浏览器实例

    Raises:
        FileNotFoundError: 未找到任何浏览器
        RuntimeError: 浏览器启动失败
    """
    if port:
        LAUNCHED_MODES[int(port)] = bool(headless)
    browser_type = (browser_type or "chrome").lower().strip()

    # 如果用户没有手动指定路径，自动查找最佳浏览器
    if not chrome_path:
        chrome_path, browser_type = _find_best_browser_path(browser_type)

    if not chrome_path or not os.path.isfile(chrome_path):
        # fallback: 尝试任何可用的浏览器
        available = detect_available_browsers()
        if available:
            browser_type, chrome_path = next(iter(available.items()))
            logger.warning(f"指定浏览器不可用，自动切换到: {browser_type} ({chrome_path})")
        else:
            raise FileNotFoundError(
                f"未找到任何浏览器。请安装 Google Chrome 或 Microsoft Edge。\n"
                f"当前平台: {platform.system()} {platform.machine()}"
            )

    logger.info(f"浏览器路径 ({browser_type}): {chrome_path}")
    if browser_type != "portable":
        logger.warning(
            "未使用项目内置破解版浏览器，BOSS 直聘风控可能拦截本次会话；"
            "请将 cloakbrowser/chrome.exe 放到项目根目录，或在配置中指定其路径。"
        )

    if _IS_MACOS:
        if background and not headless:
            logger.info("macOS 没有不抢焦点的最小化口子，后台开关在这里不生效")
        return _launch_macos(
            chrome_path=chrome_path,
            headless=headless,
            user_agent=user_agent,
            proxy=proxy,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            port=port or _find_free_port(),
            user_data_dir=user_data_dir,
            extra_args=extra_args,
        )
    else:
        return _launch_windows(
            chrome_path=chrome_path,
            headless=headless,
            user_agent=user_agent,
            proxy=proxy,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            port=port,
            user_data_dir=user_data_dir,
            background=background,
            extra_args=extra_args,
        )


def _launch_macos(
    chrome_path: str,
    headless: bool,
    user_agent: str,
    proxy: str,
    viewport_width: int,
    viewport_height: int,
    port: int,
    user_data_dir: str = "",
    extra_args: tuple = (),
) -> BrowserInstance:
    """macOS 启动 Chrome（手动启动 + Chromium 连接）

    绕过 DrissionPage 在 macOS arm64 上的 WebSocket bug：
    手动启动 Chrome 子进程，然后通过 WebSocket 地址连接。
    """

    # 构建启动参数 — 优先使用传入的 user_data_dir，否则使用临时目录
    if user_data_dir:
        user_data_dir_path = user_data_dir
    else:
        user_data_dir_path = os.path.join(
            tempfile.gettempdir(), f"boss_bot_chrome_{port}"
        )
    os.makedirs(user_data_dir_path, exist_ok=True)

    args = [
        f'--remote-debugging-port={port}',
        '--no-sandbox',
        '--disable-gpu',
        '--disable-dev-shm-usage',
        '--disable-extensions',
        '--disable-background-networking',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-features=DnsOverHttps',
        f'--user-data-dir={user_data_dir_path}',
        '--remote-allow-origins=*',  # 允许所有来源（Chrome 111+ 需要）
        f'--window-size={viewport_width},{viewport_height}',
    ]

    if headless:
        args.append('--headless=new')

    args.extend(extra_args)

    if user_agent:
        args.append(f'--user-agent={user_agent}')

    if proxy:
        args.append(f'--proxy-server={proxy}')

    logger.info(f"macOS: 启动 Chrome (port={port})...")
    logger.debug(f"启动参数: {args}")

    # 启动 Chrome 子进程
    proc = subprocess.Popen(
        [chrome_path] + args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 等待 Chrome 就绪
    if not _wait_for_port('127.0.0.1', port, timeout=15):
        proc.kill()
        raise RuntimeError(
            f"Chrome 启动失败（端口 {port} 未响应）。\n"
            f"请检查 Chrome 版本是否兼容。"
        )

    # 获取 WebSocket URL
    ws_url = _get_ws_url('127.0.0.1', port)
    if not ws_url:
        proc.kill()
        raise RuntimeError("无法获取 Chrome WebSocket URL")

    logger.info(f"Chrome WebSocket: {ws_url}")

    # 连接
    from DrissionPage._base.chromium import Chromium
    from DrissionPage import ChromiumOptions

    co = ChromiumOptions()
    co.ws_address = ws_url

    try:
        chromium = Chromium(co)
    except Exception as e:
        proc.kill()
        raise RuntimeError(f"连接 Chrome 失败: {e}")

    # 创建初始 tab
    tab = chromium.new_tab()

    logger.info(f"macOS: Chrome 连接成功 (PID={proc.pid})")

    return BrowserInstance(chromium=chromium, tab=tab, process=proc)


# ─────────────────────────────────────────────
# 端口占用守卫
# ─────────────────────────────────────────────
# DrissionPage 的 set_local_port 语义是"这个端口上已经有浏览器就直接连它"，
# 所以一次性脚本留下的孤儿浏览器会让账号连着它的空壳跑。2026-09-29 主号整天
# 0 条投递就是这个：9222 上挂着一个 profile 为 Temp\DrissionPage\userData\9222
# 的孤儿（没登录），主号一连上去就撞手机号+短信验证的登录墙。
DEFAULT_DEBUG_PORT = 9222


def parse_browser_cmdline(cmdline: str, pid: int = 0) -> dict:
    """从浏览器命令行里读出调试端口和 user-data-dir。

    没写端口的按 DrissionPage 默认的 9222 算——事故源头正是既不设端口也不设
    profile 的一次性脚本。
    """
    cmdline = cmdline or ""
    m = re.search(r"--remote-debugging-port=(\d+)", cmdline)
    d = re.search(r'--user-data-dir="?([^"\s]+)', cmdline)
    if cmdline.startswith('"'):
        exe = cmdline.split('"')[1]
    else:
        parts = cmdline.split()
        exe = parts[0] if parts else ""
    return {
        "pid": pid,
        "port": int(m.group(1)) if m else DEFAULT_DEBUG_PORT,
        "user_data_dir": d.group(1).strip('"') if d else "",
        "exe": exe,
    }


def same_profile(a: str, b: str) -> bool:
    """比较两个 --user-data-dir：Windows 路径大小写不敏感、分隔符混用、尾斜杠随意"""
    if not a or not b:
        return False
    return (os.path.normcase(os.path.normpath(str(a).strip().strip('"')))
            == os.path.normcase(os.path.normpath(str(b).strip().strip('"'))))


def _browser_owners() -> list:
    """机器上所有带调试端口的浏览器进程：[{pid, port, user_data_dir, exe}]。

    只在 Windows 上查（事故环境就是 Windows）。查不到就返回空表：守卫退化成
    "不拦"，绝不能因为拿不到诊断信息反而把正常启动堵死。
    """
    if not sys.platform.startswith("win"):
        return []
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or Name='msedge.exe'\""
          " | ForEach-Object { if ($_.CommandLine -match 'remote-debugging-port')"
          " { [string]$_.ProcessId + '|' + $_.CommandLine } }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=10)
        raw = r.stdout or b""
    except Exception:
        return []
    # 不能开 text=True：控制台按 cp936 输出时 utf-8 解码会在读取线程里抛，
    # 结果是"查不到任何浏览器"→ 守卫静默失效（真机踩过一次，主号就是这么
    # 连着空壳浏览器跑了一整天）。先按 utf-8 严解，不行再退到 GBK/cp936。
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            out = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        out = raw.decode("utf-8", "replace")
    records = []
    for line in out.splitlines():
        pid_text, _, cmdline = line.partition("|")
        if not cmdline.strip():
            continue
        try:
            rec = parse_browser_cmdline(cmdline, pid=int(pid_text.strip()))
        except (TypeError, ValueError):
            continue
        records.append(rec)
    return records


def assert_port_is_free_for(port: int, user_data_dir: str, owners=None) -> None:
    """端口上挂着别的 profile 的浏览器时直接拒绝，绝不静默连上去。

    不带 profile（老 CLI）时无从判定，保持原行为。
    """
    if not user_data_dir:
        return
    if owners is None:
        owners = _browser_owners()
    for owner in owners:
        if int(owner.get("port") or 0) != int(port):
            continue
        if same_profile(owner.get("user_data_dir", ""), user_data_dir):
            return          # 自己账号留下的浏览器，复用正是想要的
        found = owner.get("user_data_dir") or "（没写 --user-data-dir，DrissionPage 临时目录）"
        raise RuntimeError(
            f"端口 {port} 上挂着一个不是本账号的浏览器："
            f"PID={owner.get('pid')} profile={found}，而本账号要用 {user_data_dir}。"
            f" DrissionPage 会直接连端口上已有的浏览器，继续下去等于拿一个没登录的"
            f"空壳跑，BOSS 立刻弹登录墙。\n"
            f"确认那个浏览器没人在用之后执行：taskkill /PID {owner.get('pid')} /T /F"
        )


def _launch_windows(
    chrome_path: str,
    headless: bool,
    user_agent: str,
    proxy: str,
    viewport_width: int,
    viewport_height: int,
    port: int = 0,
    user_data_dir: str = "",
    background: bool = True,
    extra_args: tuple = (),
) -> BrowserInstance:
    """Windows 启动 Chrome（使用原生 ChromiumPage）"""

    from DrissionPage import ChromiumPage, ChromiumOptions

    co = ChromiumOptions()
    co.set_browser_path(chrome_path)
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    # --disable-gpu 在 --headless=new 下并没有真的去掉 GPU 子进程：实测每个号还挂着
    # 一个 346 MB 的 gpu-process。--in-process-gpu 把它折进浏览器主进程，
    # tools/measure_browser_memory.py 两轮 A/B：2643 → 2383 MB/实例（两个号合计省 ~520 MB）。
    # 渲染进程上限保持 2：收到 1 测下来 renderer 总量没降（1251→1300），
    # 省不到内存反而多一个"一个标签页崩了另一个陪葬"的连坐风险。
    # 别再顺手关掉软件光栅化（software rasterizer）：它和 --disable-gpu 一起会把 WebGL 打死，
    # 实测 navigator 报 no-webgl，而 WebGL 串正是 BOSS 风控要读的家底（省的那点不如别暴露自己）。
    co.set_argument('--in-process-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.set_argument('--disable-features=DnsOverHttps,BackForwardCache,Translate,MediaRouter,OptimizationHints')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument(f'--window-size={viewport_width},{viewport_height}')
    # 内存：两个号的 cloakbrowser 实测占 3.0 GB（面板本身只有 90 MB），
    # 大头都在浏览器进程架构上，所以这里能省的是"别多开进程、别留死页面"。
    # - BackForwardCache 会把上一页整个留在内存里，而打招呼是同一个标签页
    #   在几十个 job_detail 之间来回跳，等于攒了一叠再也用不上的岗位页；
    # - renderer-process-limit 让同站的聊天页和岗位页挤一个渲染进程；
    # - 后面几个 disable 关掉的是崩溃上报/组件更新/同步这类后台服务进程。
    co.set_argument('--renderer-process-limit=2')
    co.set_argument('--disable-breakpad')
    co.set_argument('--disable-component-update')
    co.set_argument('--disable-domain-reliability')
    co.set_argument('--disable-sync')
    co.set_argument('--metrics-recording-only')
    # 后台跑（最小化 / 被别的窗口挡住）时 Chrome 会把这个页面当"看不见的标签页"，
    # 定时器降到每分钟一次、动画帧直接停；打招呼等的就是页面里的延时渲染，
    # 所以窗口收起来之前必须先把这三档节流关掉。
    for _bg_flag in BACKGROUND_FLAGS:
        co.set_argument(_bg_flag)
    for _flag in extra_args:
        co.set_argument(_flag)

    # 先确认端口上没有别人的浏览器，再动本账号的 profile 目录
    if port > 0 and _is_port_open("127.0.0.1", port):
        assert_port_is_free_for(port, user_data_dir)

    # 设置用户数据目录（多账号隔离）
    if user_data_dir:
        os.makedirs(user_data_dir, exist_ok=True)
        co.set_argument(f'--user-data-dir={user_data_dir}')

    # 设置调试端口（多账号时每个账号使用不同端口）。
    # 必须走 set_local_port：DrissionPage 用 co.address 决定连哪个端口，
    # 只加 --remote-debugging-port 参数的话 DrissionPage 仍按默认 9222 启动，
    # 表现为所有账号挤同一个端口、多账号互相抢占浏览器。
    if port > 0:
        co.set_local_port(port)

    if headless:
        co.set_argument('--headless=new')

    if user_agent:
        co.set_user_agent(user_agent)

    if proxy:
        co.set_proxy(proxy)

    page = ChromiumPage(co)
    logger.info(f"Windows: ChromiumPage 启动成功 (port={port})")

    # 无头本来就没有窗口；有头才需要"启动即收起"，省得每轮投递都把用户的
    # 输入法焦点抢走（面板一天启动两次浏览器，两个号 = 一天两次打断）。
    if background and not headless:
        _minimize_new_window(page)

    return BrowserInstance(chrome_page=page)


def _minimize_new_window(page) -> None:
    """把刚启动的浏览器窗口收进任务栏。

    窗口挂在浏览器主进程上，进程号直接问浏览器自己（CDP SystemInfo），
    复用一个已经在跑的浏览器也问得出同一个号。
    """
    try:
        pid = int(page.browser.process_id or 0)
    except Exception:
        pid = 0
    if not pid:
        logger.warning("拿不到浏览器进程号，窗口只能留在桌面上（不影响功能）")
        return
    n = minimize_browser_windows(pid)
    logger.info(f"浏览器窗口已收进任务栏 (PID={pid}, {n} 个窗口)"
                if n else f"没找到需要收起的窗口 (PID={pid})")


# ──────────────────────────────────────────────────────────────
# BrowserManager 高层管理器
# ──────────────────────────────────────────────────────────────

class BrowserManager:
    """浏览器高层管理器

    在 BrowserInstance 之上提供更高层的管理能力：
    - 共享浏览器实例（打招呼和回复共用同一个浏览器）
    - 多标签页管理（一个标签页用于打招呼/搜索，一个用于聊天回复）
    - 统一的登录状态管理
    - Cookie 保存/加载

    使用方式：
        manager = BrowserManager(config)
        instance = manager.launch()
        search_page = manager.get_search_page()
        chat_page = manager.get_chat_page()
        manager.save_cookies("cookies.json")
        manager.close()
    """

    # BOSS 直聘相关 URL
    BOSS_LOGIN_URL = "https://www.zhipin.com/web/user/?ka=header-login"
    BOSS_SEARCH_URL = "https://www.zhipin.com/web/geek/job-recommend"
    BOSS_CHAT_URL = "https://www.zhipin.com/web/geek/chat"

    def __init__(self, config=None, account_index=0, port=None, user_data_dir=None):
        """初始化浏览器管理器

        Args:
            config: 配置对象，需包含以下属性（均可选）：
                - headless: bool - 是否无头模式
                - background: bool - 有头模式下是否把窗口收进任务栏
                - user_agent: str - 自定义 UA
                - proxy: str - 代理地址
                - viewport_width: int - 视口宽度
                - viewport_height: int - 视口高度
                - chrome_path: str - 浏览器路径
                - browser_type: str - 浏览器类型
                - cookie_file: str - Cookie 文件路径
                - user_data_dir: str - 用户数据目录
            account_index: 账号索引，用于分配独立调试端口
            port: 指定调试端口，None 时自动分配（9222 + account_index）
            user_data_dir: 指定用户数据目录，优先于 config 中的配置
        """
        self._config = config
        self._account_index = account_index
        self._instance: Optional[BrowserInstance] = None
        self._search_tab = None   # 搜索/打招呼标签页
        self._chat_tab = None     # 聊天回复标签页（回复引擎专用，长期存在）
        # 打招呼引擎专用：点"沟通"按钮后BOSS会打开新标签页，用完即关。
        # 与 _chat_tab 严格区分，避免抢占回复引擎的聊天标签页。
        self._greet_chat_tab = None
        self._is_logged_in = False

        # 每个账号使用不同的调试端口，避免端口冲突
        self._debug_port = port if port is not None else (9222 + account_index)

        # 从 config 提取参数，兼容 None 和对象两种情况
        self._headless = getattr(config, 'headless', False) if config else False
        # 缺省后台运行：投递一天要启动两次浏览器，每次都前置就会打断用户
        self._background = getattr(config, 'background', True) if config else True
        # 缺省按"后台"走：这个浏览器一天要启动两次，前置一次就打断一次用户
        self._background = getattr(config, 'background', True) if config else True
        self._user_agent = getattr(config, 'user_agent', "") if config else ""
        self._proxy = getattr(config, 'proxy', "") if config else ""
        self._viewport_width = getattr(config, 'viewport_width', 1280) if config else 1280
        self._viewport_height = getattr(config, 'viewport_height', 800) if config else 800
        self._chrome_path = getattr(config, 'chrome_path', "") if config else ""
        self._browser_type = getattr(config, 'browser_type', "chrome") if config else "chrome"
        self._cookie_file = getattr(config, 'cookie_file', "") if config else ""
        # 优先使用传入的 user_data_dir，其次从 config 获取；
        # 两种来源都必须再按账号分一层，见 _profile_dir_for 的说明
        if user_data_dir:
            base_dir = user_data_dir
        elif config:
            base_dir = getattr(config, 'user_data_dir', "")
        else:
            base_dir = ""
        self._user_data_dir = self._profile_dir_for(base_dir)

    def _profile_dir_for(self, base: str) -> str:
        """把任意来源的用户目录算成"这个账号专属"的那一份。

        留空会让 Chrome 退回默认用户目录，两个账号于是共用同一份 profile：
        cookie 互相顶掉、第二个浏览器开出来是空白的、还会和第一个抢调试端口
        （2026-09-29 主账号就是 "浏览器连接失败 127.0.0.1:9222" 直接退出）。
        已经按账号命名好的（browser_data/account_1）原样保留，不再套一层。
        """
        from boss_bot.unified_config import BASE_DIR

        root = Path(base) if base else Path(str(BASE_DIR)) / "browser_data"
        if root.name.startswith("account_"):
            root = root.parent / f"account_{self._account_index}"
        else:
            root = root / f"account_{self._account_index}"
        return str(root)

    def launch(self) -> BrowserInstance:
        """启动浏览器并返回浏览器实例

        如果已经启动则返回现有实例。
        启动后会自动尝试加载 Cookie（如果配置了 cookie_file）。

        Returns:
            BrowserInstance: 浏览器实例
        """
        if self._instance is not None:
            return self._instance

        self._instance = launch_browser(
            headless=self._headless,
            user_agent=self._user_agent,
            proxy=self._proxy,
            viewport_width=self._viewport_width,
            viewport_height=self._viewport_height,
            port=self._debug_port,
            chrome_path=self._chrome_path,
            browser_type=self._browser_type,
            user_data_dir=self._user_data_dir,
            background=self._background,
        )

        # 尝试加载 Cookie
        if self._cookie_file:
            self.load_cookies(self._cookie_file)

        return self._instance

    def _window_pid(self) -> int:
        """这个浏览器挂在哪个进程上（窗口归属按进程号认）。"""
        inst = self._instance
        for obj in (getattr(inst, "_page", None), getattr(inst, "_chromium", None)):
            if obj is None:
                continue
            for 取法 in (lambda: int(getattr(obj, "browser").process_id or 0),
                        lambda: int(getattr(obj, "process_id") or 0)):
                try:
                    pid = 取法()
                except Exception:
                    pid = 0
                if pid:
                    return pid
        return 0

    def show_login_window(self, login_url: str = "", navigate: bool = True) -> dict:
        """把"真能扫码登录的那个窗口"摆到用户眼前。

        面板上那句「请在浏览器中登录后点击我已登录」在无头形态下是做不到的：
        桌面上压根没有窗口（2026-10-09 用户截图正是这个）。所以先问这次是不是
        无头起来的 —— 形态只看 LAUNCHED_MODES，不能拿 UA 判，cloakbrowser 会把
        UA 里的 HeadlessChrome 抹平。是无头就按同一端口、同一用户目录重开成有头，
        再把登录页导航上去；本来有头（只是收进了任务栏）就把窗口捞回来。

        重开会把配置里的无头/后台原样还回去：这一次例外是为了登录，
        不是改用户的常驻形态。
        """
        登录页 = login_url or self.BOSS_LOGIN_URL
        # 形态问端口，别问自己手里那个对象：浏览器半路没了的话，_instance 还留着
        # 一个连不上的旧句柄，只按 LAUNCHED_MODES 判会以为"有头、不用重开"，
        # 然后对着死句柄导航，报出来一句空错（12:33 实测就是这样）
        try:
            形态 = browser_mode(int(self._debug_port or 0)) or {}
        except Exception:
            形态 = {}
        要重开 = (self._instance is None
                 or not 形态.get("running")
                 or 形态.get("headless") is True)
        结果 = {"relaunched": bool(要重开), "windows": 0, "url_shown": "", "error": ""}
        try:
            if 要重开:
                if self._instance is not None:
                    self.close()
                    time.sleep(2)
                原无头, 原后台 = self._headless, self._background
                self._headless, self._background = False, False
                try:
                    self.launch()
                finally:
                    self._headless, self._background = 原无头, 原后台
                # close() 把 _search_tab 置空了，不重建的话下一轮打招呼拿的是旧句柄
                # （11:03 重连现场那个"第二个浏览器打开后无内容"就是漏了这一步）
                self.get_search_page()
            if self._instance is None:
                结果["error"] = "浏览器没起来（端口或用户目录被别的 Chrome 占了）"
                return 结果
            结果["windows"] = restore_browser_windows(self._window_pid())
            # "看不准是不是掉登录"那一类不许把会话页导航走：本来就登录着的时候
            # 跳去登录页，回复侧随后就读到登录墙，自己把自己绊停。
            # 但重开过就没得选——新浏览器里是空白页，不摆登录页等于没窗口可用。
            if navigate or 要重开:
                self._instance.get(登录页)
                结果["url_shown"] = 登录页
        except Exception as e:
            结果["error"] = f"{type(e).__name__}: {e}"
        return 结果

    def get_search_page(self) -> BrowserInstance:
        """获取搜索/打招呼页面 tab（包装为 BrowserInstance）

        复用浏览器初始标签页作为搜索页，避免创建多余标签页。
        打招呼引擎使用此标签页进行职位搜索和打招呼操作。

        Returns:
            BrowserInstance 包装的搜索标签页实例
        """
        if self._instance is None:
            self.launch()

        if self._search_tab is None:
            # 复用初始标签页，导航到搜索 URL
            self._instance.get(self.BOSS_SEARCH_URL)
            self._search_tab = self._instance
            logger.info("已复用初始标签页作为搜索/打招呼页")

        return self._search_tab

    def get_chat_page(self) -> BrowserInstance:
        """获取聊天回复页面 tab（包装为 BrowserInstance）— 回复引擎专用

        如果尚未创建聊天标签页，则创建一个并导航到聊天页。
        回复引擎使用此标签页进行聊天消息回复操作。

        重要：此方法是回复引擎专用，不会被打招呼引擎抢占。
        打招呼引擎点"沟通"按钮后打开的新标签页请使用 get_greet_chat_tab() 获取，
        用完后调用 close_greet_chat_tab() 关闭，避免与回复引擎的 _chat_tab 混淆。

        Returns:
            BrowserInstance 包装的聊天标签页实例
        """
        if self._instance is None:
            self.launch()

        if self._chat_tab is None:
            raw_tab = self._instance.new_tab(self.BOSS_CHAT_URL)
            # 包装为 BrowserInstance，复用底层 chromium/browser 对象
            self._chat_tab = BrowserInstance(
                chrome_page=raw_tab if not _IS_MACOS else None,
                chromium=self._instance._get_browser() if _IS_MACOS else None,
                tab=raw_tab if _IS_MACOS else None,
            )
            logger.info("已创建回复引擎专用聊天标签页 (_chat_tab)")

        return self._chat_tab

    def get_greet_chat_tab(self, url: str = "") -> BrowserInstance:
        """获取打招呼引擎专用的临时聊天标签页 — 用完必须关闭

        打招呼引擎点"沟通"按钮后，BOSS 直聘会打开新标签页进行聊天。
        此方法创建/复用一个独立的临时标签页供打招呼引擎使用，
        严格与回复引擎的 _chat_tab 区分，避免抢占。

        使用流程：
            tab = browser_manager.get_greet_chat_tab()
            # 在 tab 中输入并发送打招呼消息
            browser_manager.close_greet_chat_tab()  # 用完必须关闭！

        Args:
            url: 初始导航 URL，为空则不导航

        Returns:
            BrowserInstance 包装的临时聊天标签页实例
        """
        if self._instance is None:
            self.launch()

        if self._greet_chat_tab is None:
            raw_tab = self._instance.new_tab(url) if url else self._instance.new_tab()
            self._greet_chat_tab = BrowserInstance(
                chrome_page=raw_tab if not _IS_MACOS else None,
                chromium=self._instance._get_browser() if _IS_MACOS else None,
                tab=raw_tab if _IS_MACOS else None,
            )
            logger.info("已创建打招呼引擎临时聊天标签页 (_greet_chat_tab)")
        elif url:
            try:
                self._greet_chat_tab.get(url)
            except Exception as e:
                logger.warning(f"导航打招呼临时标签页失败: {e}")

        return self._greet_chat_tab

    def close_greet_chat_tab(self):
        """关闭打招呼引擎专用的临时聊天标签页

        打招呼引擎发送完消息后必须调用此方法，避免新标签页累积。
        关闭后 _greet_chat_tab 置为 None，下次调用 get_greet_chat_tab() 会创建新标签页。
        """
        if self._greet_chat_tab is None:
            return
        try:
            self._greet_chat_tab.close_current_tab()
            logger.info("已关闭打招呼引擎临时聊天标签页")
        except Exception as e:
            logger.warning(f"关闭打招呼临时标签页异常: {e}")
        finally:
            self._greet_chat_tab = None

    def get_greet_chat_raw_tab(self):
        """获取打招呼临时标签页的底层 tab 对象（DrissionPage 原生）

        用于打招呼引擎需要在原生 tab 上执行操作的场景。
        如果 _greet_chat_tab 不存在则返回 None。

        Returns:
            DrissionPage tab 对象或 None
        """
        if self._greet_chat_tab is None:
            return None
        # BrowserInstance 内部 _page (Windows) 或 _tab (macOS) 即为原生 tab
        if _IS_MACOS:
            return self._greet_chat_tab._tab
        return self._greet_chat_tab._page

    def get_reply_tab_id(self):
        """获取回复引擎专用 _chat_tab 的 tab_id，供打招呼引擎排除用。

        打招呼引擎在遍历所有标签页查找输入框时，必须排除回复引擎的 _chat_tab，
        避免在回复引擎的聊天标签页上发送打招呼消息（严重BUG）。

        Returns:
            tab_id 字符串或 None（_chat_tab 不存在或无法获取 tab_id 时）
        """
        if self._chat_tab is None:
            return None
        try:
            raw = self._chat_tab._page or self._chat_tab._tab
            if raw is not None:
                return getattr(raw, 'tab_id', None) or getattr(raw, '_tab_id', None)
        except Exception:
            pass
        return None

    def check_login(self) -> bool:
        """检查登录状态

        通过访问 BOSS 直聘页面并检测登录相关元素来判断是否已登录。
        如果加载了有效的 Cookie，则认为已登录。

        Returns:
            已登录返回 True，未登录返回 False
        """
        if self._instance is None:
            return False

        try:
            # 先尝试通过 Cookie 判断
            if self._cookie_file and os.path.exists(self._cookie_file):
                # Cookie 文件存在，尝试加载并验证
                self._instance.get(self.BOSS_SEARCH_URL)
                time.sleep(2)

                # 检查页面是否跳转到登录页
                current_url = self._instance.url
                if "login" in current_url or "user" in current_url:
                    self._is_logged_in = False
                    return False

                # 检查是否有登录后的用户元素
                try:
                    user_ele = self._instance.ele('.user-info', timeout=3)
                    if user_ele:
                        self._is_logged_in = True
                        return True
                except Exception:
                    pass

                # 检查是否有打招呼按钮（登录后才会出现）
                try:
                    greet_btn = self._instance.ele('.btn-greet', timeout=3)
                    if greet_btn:
                        self._is_logged_in = True
                        return True
                except Exception:
                    pass

            # 没有 Cookie 文件，直接检查当前页面状态
            self._instance.get(self.BOSS_SEARCH_URL)
            time.sleep(2)

            current_url = self._instance.url
            if "login" in current_url or "user" in current_url:
                self._is_logged_in = False
                return False

            self._is_logged_in = True
            return True

        except Exception as e:
            logger.warning(f"检查登录状态异常: {e}")
            self._is_logged_in = False
            return False

    def save_cookies(self, filepath: str = ""):
        """保存 Cookie 到文件

        Args:
            filepath: 保存路径，为空则使用配置中的 cookie_file
        """
        if self._instance is None:
            raise RuntimeError("浏览器尚未启动，无法保存 Cookie")

        path = filepath or self._cookie_file
        if not path:
            raise ValueError("未指定 Cookie 保存路径")

        self._instance.save_cookies(path)

    def load_cookies(self, filepath: str = "") -> bool:
        """从文件加载 Cookie

        Args:
            filepath: Cookie 文件路径，为空则使用配置中的 cookie_file

        Returns:
            加载成功返回 True，失败返回 False
        """
        if self._instance is None:
            raise RuntimeError("浏览器尚未启动，无法加载 Cookie")

        path = filepath or self._cookie_file
        if not path:
            return False

        return self._instance.load_cookies(path)

    def get_instance(self) -> Optional[BrowserInstance]:
        """获取当前浏览器实例

        Returns:
            BrowserInstance 或 None（未启动时）
        """
        return self._instance

    def close(self):
        """关闭浏览器

        清理所有标签页并关闭浏览器实例。
        """
        self._search_tab = None
        self._chat_tab = None
        self._greet_chat_tab = None
        self._is_logged_in = False

        if self._instance is not None:
            self._instance.quit()
            self._instance = None
            logger.info("浏览器已关闭")

    def __enter__(self):
        """上下文管理器入口"""
        self.launch()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.close()
        return False

# ──────────────────────────────────────────────────────────────
# Cookie 有效性检测（参考 auto_boss.check_login_status）
# ──────────────────────────────────────────────────────────────

def check_cookie_valid(cookie_file: str,
                       headless: bool = False,
                       chrome_path: str = "",
                       browser_type: str = "chrome",
                       timeout: int = 20,
                       port: int = 0,
                       user_data_dir: str = "",
                       background: bool = True) -> dict:
    """检测 Cookie 是否有效（参考 auto_boss 项目的 check_login_status 方法）。

    使用 DrissionPage 启动浏览器，加载 Cookie 后访问 BOSS 直聘首页，
    检查以下条件判断登录状态：
    1. 检查 .user-info 元素是否存在（登录后显示）
    2. 检查 URL 是否跳转到登录页（包含 "login" 或 "user" 或 "passport"）
    3. 检查 .header-login-btn 文本是否为 "登录/注册"

    Args:
        cookie_file: Cookie 文件路径
        headless: 是否无头模式（调用方按生产配置传，判定才代表真跑得通）
        chrome_path: 浏览器路径（空则自动检测）
        browser_type: 浏览器类型
        timeout: 总超时时间（秒）
        port: 专用调试端口。留 0 会让 DrissionPage 退回默认 9222，
            也就是直接连进主账号正在用的浏览器里翻页面
        user_data_dir: 一次性 profile 目录（同上，不能拿账号在用的那份）
        background: 有头时把窗口收进任务栏，别打断用户

    Returns:
        dict:
        {
            "valid": bool,           # Cookie 是否有效
            "logged_in": bool,       # 是否已登录
            "reason": str,           # 失效原因（valid=False 时有值）
            "current_url": str,      # 检测时的页面 URL
            "checks": dict,          # 各项检查结果
        }
    """
    result = {
        "valid": False,
        "logged_in": False,
        "reason": "",
        "current_url": "",
        "checks": {
            "user_info_found": False,
            "url_is_login_page": False,
            "header_btn_is_login": False,
        },
    }

    # 1. 检查 Cookie 文件是否存在
    if not cookie_file or not os.path.exists(cookie_file):
        result["reason"] = f"Cookie 文件不存在: {cookie_file}"
        return result

    # 2. 检查 Cookie 文件是否非空
    try:
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if not cookies:
            result["reason"] = "Cookie 文件为空"
            return result
    except Exception as e:
        result["reason"] = f"Cookie 文件解析失败: {e}"
        return result

    # 3. 启动浏览器检测
    instance = None
    try:
        instance = launch_browser(
            headless=headless,
            chrome_path=chrome_path,
            browser_type=browser_type,
            port=port,
            user_data_dir=user_data_dir,
            background=background,
        )

        # 先访问 BOSS 直聘首页，再加载 Cookie
        instance.get("https://www.zhipin.com/")
        time.sleep(1)

        # 加载 Cookie
        try:
            instance.load_cookies(cookie_file)
        except Exception as e:
            result["reason"] = f"加载 Cookie 失败: {e}"
            return result

        # 刷新页面使 Cookie 生效
        instance.get("https://www.zhipin.com/web/geek/job-recommend")
        time.sleep(2)

        # 获取当前 URL
        try:
            current_url = instance.url or ""
        except Exception:
            current_url = ""
        result["current_url"] = current_url

        # 检查 1: URL 是否跳转到登录页
        url_lower = current_url.lower()
        is_login_url = any(kw in url_lower for kw in ("login", "passport", "user/?ka"))
        result["checks"]["url_is_login_page"] = is_login_url
        if is_login_url:
            result["reason"] = f"URL 跳转到登录页: {current_url}"
            return result

        # 检查 2: .user-info 元素是否存在（登录后显示）
        try:
            user_ele = instance.ele(".user-info", timeout=3)
            if user_ele:
                result["checks"]["user_info_found"] = True
                result["valid"] = True
                result["logged_in"] = True
                return result
        except Exception:
            pass

        # 检查 3: .header-login-btn 文本是否为 "登录/注册"
        try:
            login_btn = instance.ele(".header-login-btn", timeout=3)
            if login_btn:
                btn_text = ""
                try:
                    btn_text = login_btn.text or ""
                except Exception:
                    pass
                if "登录/注册" in btn_text or "登录" in btn_text:
                    result["checks"]["header_btn_is_login"] = True
                    result["reason"] = f"登录按钮文本为: {btn_text}"
                    return result
                else:
                    # 按钮文本不是"登录/注册"，说明已登录
                    result["valid"] = True
                    result["logged_in"] = True
                    return result
        except Exception:
            pass

        # 检查 4: .user-nav 元素（auto_boss 也用此选择器）
        try:
            user_nav = instance.ele(".user-nav", timeout=3)
            if user_nav:
                nav_text = ""
                try:
                    nav_text = user_nav.text or ""
                except Exception:
                    pass
                if nav_text.strip() and "登录/注册" not in nav_text:
                    result["valid"] = True
                    result["logged_in"] = True
                    return result
        except Exception:
            pass

        # 所有检查都未明确判断，默认无效
        result["reason"] = "未找到登录态元素，Cookie 可能已失效"
        return result

    except Exception as e:
        result["reason"] = f"检测过程异常: {e}"
        return result
    finally:
        if instance is not None:
            try:
                instance.quit()
            except Exception:
                pass


def check_cookie_valid_simple(cookie_file: str) -> dict:
    """简单检测 Cookie 是否有效（不启动浏览器，仅检查文件和关键字段）。

    用于快速预检，避免每次都启动浏览器。

    Args:
        cookie_file: Cookie 文件路径

    Returns:
        dict: 同 check_cookie_valid 返回结构
    """
    result = {
        "valid": False,
        "logged_in": False,
        "reason": "",
        "current_url": "",
        "checks": {
            "file_exists": False,
            "has_auth_cookie": False,   # 是否含 BOSS 登录字段
            "expired": False,           # 登录字段是否全部过期
            "expires_in_days": None,    # 最短剩余天数
            "cookie_names": [],
        },
    }

    if not cookie_file or not os.path.exists(cookie_file):
        result["reason"] = f"Cookie 文件不存在: {cookie_file}"
        return result

    result["checks"]["file_exists"] = True

    try:
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        if not cookies:
            result["reason"] = "Cookie 文件为空"
            return result

        now = time.time()
        auth_left = []          # 登录类 Cookie 各自的剩余有效秒数
        found_names = []
        for c in cookies:
            if not isinstance(c, dict):
                continue
            name = c.get("name", "")
            if name not in BOSS_AUTH_COOKIES:
                continue
            found_names.append(name)
            expires = c.get("expires") or c.get("expirationDate") or -1
            try:
                expires = float(expires)
            except (TypeError, ValueError):
                expires = -1
            # expires < 0 表示会话级 Cookie（关浏览器即失效），无法判断时长
            auth_left.append(expires - now if expires > 0 else -1.0)

        result["checks"]["has_auth_cookie"] = bool(found_names)
        result["checks"]["cookie_names"] = found_names

        if not found_names:
            result["reason"] = (
                f"Cookie 文件缺少登录字段（需要 {'/'.join(BOSS_AUTH_COOKIES)} 之一），"
                f"共 {len(cookies)} 条 Cookie"
            )
            return result

        # 只要还有一条登录 Cookie 没过期就认为可能可用；全部过期则明确报失效
        usable = [s for s in auth_left if s == -1.0 or s > 0]
        if not usable:
            oldest = max(auth_left)
            result["checks"]["expired"] = True
            result["reason"] = f"登录 Cookie 已于 {-int(oldest / 86400)} 天前过期，请重新登录"
            return result

        timed = [s for s in usable if s > 0]
        days_left = round(min(timed) / 86400, 1) if timed else None
        result["valid"] = True
        result["logged_in"] = True
        result["checks"]["expires_in_days"] = days_left
        result["reason"] = (
            f"登录 Cookie 齐全（{', '.join(found_names)}）"
            + (f"，最短剩余 {days_left} 天" if days_left is not None else "，含会话级 Cookie")
        )
        return result
    except Exception as e:
        result["reason"] = f"Cookie 文件解析失败: {e}"
        return result
