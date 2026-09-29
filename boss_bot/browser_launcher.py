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

# 实测承载 BOSS 直聘登录态的 Cookie 名（缺这些或过期即需要重新登录）
BOSS_AUTH_COOKIES = ("wt2", "zp_at", "bst", "wbg")

# Cookie 文件是用户唯一的登录会话凭据。覆盖写之前先留一份，只保留最近这么多份：
# 全留着会把几百 KB 的会话堆成垃圾山，但一份都没有就是 2026-09-29 那样——
# 判定误报"过期"直接把文件删了，人工连"当时里面到底是什么"都查不了。
COOKIE_BACKUP_KEEP = 5


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
) -> BrowserInstance:
    """启动浏览器（跨平台，支持 Chrome、Edge、Chromium）

    Args:
        headless: 是否无头模式
        user_agent: 自定义 User-Agent
        proxy: 代理地址
        viewport_width: 视口宽度
        viewport_height: 视口高度
        port: 调试端口（0 表示自动选择）
        chrome_path: 浏览器路径（空则自动检测）
        browser_type: 浏览器类型，"chrome" / "edge" / "chromium"

    Returns:
        BrowserInstance: 浏览器实例

    Raises:
        FileNotFoundError: 未找到任何浏览器
        RuntimeError: 浏览器启动失败
    """
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
        return _launch_macos(
            chrome_path=chrome_path,
            headless=headless,
            user_agent=user_agent,
            proxy=proxy,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            port=port or _find_free_port(),
            user_data_dir=user_data_dir,
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
) -> BrowserInstance:
    """Windows 启动 Chrome（使用原生 ChromiumPage）"""

    from DrissionPage import ChromiumPage, ChromiumOptions

    co = ChromiumOptions()
    co.set_browser_path(chrome_path)
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_argument('--disable-dev-shm-usage')
    co.set_argument('--no-first-run')
    co.set_argument('--no-default-browser-check')
    co.set_argument('--disable-features=DnsOverHttps')
    co.set_argument('--disable-blink-features=AutomationControlled')
    co.set_argument(f'--window-size={viewport_width},{viewport_height}')

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

    return BrowserInstance(chrome_page=page)


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
        )

        # 尝试加载 Cookie
        if self._cookie_file:
            self.load_cookies(self._cookie_file)

        return self._instance

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
                       headless: bool = True,
                       chrome_path: str = "",
                       browser_type: str = "chrome",
                       timeout: int = 20) -> dict:
    """检测 Cookie 是否有效（参考 auto_boss 项目的 check_login_status 方法）。

    使用 DrissionPage 启动浏览器，加载 Cookie 后访问 BOSS 直聘首页，
    检查以下条件判断登录状态：
    1. 检查 .user-info 元素是否存在（登录后显示）
    2. 检查 URL 是否跳转到登录页（包含 "login" 或 "user" 或 "passport"）
    3. 检查 .header-login-btn 文本是否为 "登录/注册"

    Args:
        cookie_file: Cookie 文件路径
        headless: 是否无头模式（默认 True）
        chrome_path: 浏览器路径（空则自动检测）
        browser_type: 浏览器类型
        timeout: 总超时时间（秒）

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
