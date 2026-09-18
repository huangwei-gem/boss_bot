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

logger = logging.getLogger("browser_launcher")

# 平台检测
_IS_MACOS = platform.system().lower() == "darwin"
_IS_WINDOWS = platform.system().lower() == "windows"


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

    # 可能的路径列表
    possible_paths = [
        # 项目根目录（browser_launcher.py 的上上级）
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "cloakbrowser-windows-x64", "chrome.exe"
        ),
        # 当前工作目录
        os.path.join(os.getcwd(), "cloakbrowser-windows-x64", "chrome.exe"),
        # browser_launcher.py 同级目录的上级
        os.path.join(os.path.dirname(__file__), "..", "cloakbrowser-windows-x64", "chrome.exe"),
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

    优先级：用户偏好 > 系统默认 > 配置指定 > 按优先级兜底

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

    # 2. 系统默认浏览器
    default = _detect_default_browser()
    if default and default in available:
        return (available[default], default)

    # 3. 配置指定的浏览器类型
    if browser_type in available:
        return (available[browser_type], browser_type)

    # 4. 按优先级兜底: portable > chrome > edge > chromium
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

    def run_js(self, script, *args):
        """执行 JavaScript"""
        return self._get_active().run_js(script, *args)

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
        """关闭当前标签页"""
        if self._page is not None:
            self._page.close_current_tab()
        elif self._tab is not None:
            self._tab.close()

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

    if _IS_MACOS:
        return _launch_macos(
            chrome_path=chrome_path,
            headless=headless,
            user_agent=user_agent,
            proxy=proxy,
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            port=port or _find_free_port(),
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
        )


def _launch_macos(
    chrome_path: str,
    headless: bool,
    user_agent: str,
    proxy: str,
    viewport_width: int,
    viewport_height: int,
    port: int,
) -> BrowserInstance:
    """macOS 启动 Chrome（手动启动 + Chromium 连接）

    绕过 DrissionPage 在 macOS arm64 上的 WebSocket bug：
    手动启动 Chrome 子进程，然后通过 WebSocket 地址连接。
    """

    # 构建启动参数
    user_data_dir = os.path.join(
        tempfile.gettempdir(), f"boss_bot_chrome_{port}"
    )
    os.makedirs(user_data_dir, exist_ok=True)

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
        f'--user-data-dir={user_data_dir}',
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


def _launch_windows(
    chrome_path: str,
    headless: bool,
    user_agent: str,
    proxy: str,
    viewport_width: int,
    viewport_height: int,
    port: int = 0,
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
    co.set_argument(f'--window-size={viewport_width},{viewport_height}')

    # 设置调试端口（多账号时每个账号使用不同端口）
    if port > 0:
        co.set_argument(f'--remote-debugging-port={port}')

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

    def __init__(self, config=None, account_index=0, port=None):
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
        """
        self._config = config
        self._account_index = account_index
        self._instance: Optional[BrowserInstance] = None
        self._search_tab = None   # 搜索/打招呼标签页
        self._chat_tab = None     # 聊天回复标签页
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
        self._user_data_dir = getattr(config, 'user_data_dir', "") if config else ""

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
        )

        # 尝试加载 Cookie
        if self._cookie_file:
            self.load_cookies(self._cookie_file)

        return self._instance

    def get_search_page(self) -> BrowserInstance:
        """获取搜索/打招呼页面 tab（包装为 BrowserInstance）

        如果尚未创建搜索标签页，则创建一个并导航到搜索页。
        打招呼引擎使用此标签页进行职位搜索和打招呼操作。

        Returns:
            BrowserInstance 包装的搜索标签页实例
        """
        if self._instance is None:
            self.launch()

        if self._search_tab is None:
            raw_tab = self._instance.new_tab(self.BOSS_SEARCH_URL)
            # 包装为 BrowserInstance，复用底层 chromium/browser 对象
            self._search_tab = BrowserInstance(
                chrome_page=raw_tab if not _IS_MACOS else None,
                chromium=self._instance._get_browser() if _IS_MACOS else None,
                tab=raw_tab if _IS_MACOS else None,
            )
            logger.info("已创建搜索/打招呼标签页")

        return self._search_tab

    def get_chat_page(self) -> BrowserInstance:
        """获取聊天回复页面 tab（包装为 BrowserInstance）

        如果尚未创建聊天标签页，则创建一个并导航到聊天页。
        回复引擎使用此标签页进行聊天消息回复操作。

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
            logger.info("已创建聊天回复标签页")

        return self._chat_tab

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
