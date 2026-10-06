# -*- coding: utf-8 -*-
"""无头 / 后台模式能不能过 BOSS 风控 —— 拿真号 Cookie 在隔离环境里实测。

为什么单独一个脚本：无头是"要么整条链都能跑，要么全废"的开关，
猜没有用，必须看 BOSS 到底给不给岗位列表、聊天侧栏渲不渲染。

隔离方式（红线）：
- 只用临时端口 + 临时 user-data-dir，绝不碰线上 9222/9223 那两个号的 profile；
- Cookie 文件只读，跑前跑后各算一次 sha256，必须一模一样；
- 只定位元素，不点「立即沟通」「继续沟通」「发送」，一次招呼都不会发出去。

用法：
    python tools/check_headless_boss.py --mode headless
    python tools/check_headless_boss.py --mode headful
    python tools/check_headless_boss.py --mode headless --account 1
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PYTHONUTF8", "1")

from boss_bot.browser_launcher import launch_browser  # noqa: E402
from boss_bot.unified_config import UnifiedConfig, resolve_path  # noqa: E402

SEARCH_URL = "https://www.zhipin.com/web/geek/job-recommend"
CHAT_URL = "https://www.zhipin.com/web/geek/chat"

# 浏览器自己报的家底：无头最容易在这些点上露馅。
# 必须写成带 return 的语句体：DrissionPage 的 run_js 传箭头函数只会拿到函数
# 对象本身（实测返回 null），探针等于没跑。
FINGERPRINT_JS = r"""
return {
  ua: navigator.userAgent,
  webdriver: navigator.webdriver,
  headless_ua: navigator.userAgent.indexOf('Headless') >= 0,
  plugins: navigator.plugins.length,
  has_chrome: !!window.chrome,
  languages: (navigator.languages || []).join(','),
  platform: navigator.platform,
  viewport: window.innerWidth + 'x' + window.innerHeight,
  dpr: window.devicePixelRatio,
  webgl: (() => {
    const c = document.createElement('canvas');
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    if (!gl) return 'no-webgl';
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : 'no-ext';
  })(),
}
"""

# BOSS 未登录/风控时都往这两个地址跳，混在一起会误判成"无头被拦"
LOGIN_MARKS = ("passport", "security.html", "login", "user/?ka")


def _sha256(path: Path) -> str:
    if not path.is_file():
        return "<缺失>"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _cookie_file(account_index: int) -> Path:
    cfg = UnifiedConfig()
    accounts = cfg.greet.accounts or []
    name = "zhipin_cookies.json"
    if account_index < len(accounts):
        name = accounts[account_index].cookie_file or name
    return Path(str(resolve_path(name)))


def _count(instance, selector: str) -> int:
    try:
        return len(instance.eles(selector, timeout=4) or [])
    except Exception:
        return 0


def _settle(instance, marks=LOGIN_MARKS, timeout=40):
    """BOSS 首屏常先弹一次 security.html 再自己跳回来，等它落定再看结局。

    返回 (最终 URL, 中途见过的风控地址)。不等就直接读 URL 会把
    "SPA 正在过安检" 误判成 "无头被风控拦死"。
    """
    security_seen = ""
    end = time.time() + timeout
    while time.time() < end:
        url = instance.url or ""
        if any(m in url for m in marks):
            security_seen = security_seen or url
            time.sleep(2)
            continue
        return url, security_seen
    return instance.url or "", security_seen


def probe(mode: str, account_index: int, port: int) -> dict:
    headless = mode == "headless"
    cookie_path = _cookie_file(account_index)
    sha_before = _sha256(cookie_path)

    profile = Path(tempfile.mkdtemp(prefix=f"boss_{mode}_"))
    report = {
        "mode": mode,
        "account_index": account_index,
        "cookie_file": cookie_path.name,
        "cookie_sha_before": sha_before,
        "profile": str(profile),
        "checks": {},
        "errors": [],
    }
    instance = None
    try:
        instance = launch_browser(
            headless=headless, port=port, user_data_dir=str(profile),
            viewport_width=1280, viewport_height=800,
        )
        if not instance.load_cookies(str(cookie_path)):
            report["errors"].append("Cookie 加载失败")

        instance.get(SEARCH_URL)
        time.sleep(5)
        url, security_seen = _settle(instance)
        report["checks"]["url"] = url
        report["checks"]["security_redirect"] = security_seen[:160]
        report["checks"]["fingerprint"] = instance.run_js(FINGERPRINT_JS)
        report["checks"]["job_cards"] = (
            _count(instance, ".job-card-wrap")
            or _count(instance, "li.job-card-box")
            or _count(instance, ".job-card-wrapper")
        )
        report["checks"]["login_wall"] = any(
            k in (instance.url or "") for k in ("login", "passport", "user/?ka")
        )

        # 岗位详情页能不能开（打招呼链路的必经一步，只开不点）
        detail_ok = ""
        try:
            cards = (instance.eles(".job-card-wrap", timeout=4)
                     or instance.eles("li.job-card-box", timeout=3))
            if cards:
                href = ""
                try:
                    a = cards[0].ele("tag:a") or cards[0]
                    href = a.attr("href") or ""
                except Exception:
                    href = ""
                if href.startswith("/"):
                    href = "https://www.zhipin.com" + href
                if href.startswith("http"):
                    instance.get(href)
                    time.sleep(4)
                    detail_ok = instance.url
        except Exception as e:
            report["errors"].append(f"岗位详情页打不开: {e}")
        report["checks"]["job_detail_url"] = detail_ok
        if detail_ok:
            report["checks"]["has_greet_btn"] = bool(
                _count(instance, ".job-btns") or _count(instance, ".btn-starttalk")
                or _count(instance, "tag:a")
            )

        # 聊天页：回复链路的必经页面，虚拟列表在无头下能不能渲染
        try:
            instance.get(CHAT_URL)
            time.sleep(6)
            report["checks"]["chat_url"] = instance.url
            report["checks"]["chat_rows"] = _count(instance, ".friend-content")
            report["checks"]["chat_bubbles"] = _count(instance, ".chat-message-item")
        except Exception as e:
            report["errors"].append(f"聊天页打不开: {e}")

        # 风控验证码迹象：BOSS 拦人时整页会变成 verify
        try:
            body = (instance.run_js("return document.body.innerText.slice(0,400)") or "")
        except Exception:
            body = ""
        report["checks"]["body_snippet"] = body[:200]
        report["checks"]["captcha_suspect"] = any(
            k in body for k in ("安全验证", "人机验证", "访问验证", "滑动")
        )
        return report
    finally:
        if instance is not None:
            try:
                instance.quit()
            except Exception:
                pass
        shutil.rmtree(str(profile), ignore_errors=True)
        report["cookie_sha_after"] = _sha256(cookie_path)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=("headless", "headful"), default="headless")
    p.add_argument("--account", type=int, default=0)
    p.add_argument("--port", type=int, default=0)
    a = p.parse_args()
    port = a.port or (9400 + a.account + (0 if a.mode == "headless" else 10))

    report = probe(a.mode, a.account, port)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    same = report.get("cookie_sha_before") == report.get("cookie_sha_after")
    print(("Cookie 文件未被改动 ✓" if same else "警告：Cookie 文件被改动了 ✗"))
    if not same or report["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
