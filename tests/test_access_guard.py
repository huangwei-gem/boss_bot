# -*- coding: utf-8 -*-
"""面板经隧道暴露到公网时，不能再把"连过来的是回环"当成"是自己人"。

2026-10-09 实测：本机 `curl -H 'CF-Connecting-IP: 8.8.8.8' http://127.0.0.1:5000/api/status`
回的是 200 —— cloudflared 就是这样连源站的（从 127.0.0.1 连过来，真实访客在头里）。
原来的鉴权只看 request.remote_addr，于是隧道那侧谁拿到 URL 都能点 /api/stop、
/api/cookies/delete。这个模块把判定拆成纯函数，好测也好读。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flask-version"))

from access_guard import 来源, 放行  # noqa: E402


def test_隧道流量按头里的真实来源算():
    真, 经代理 = 来源({"CF-Connecting-IP": "8.8.8.8"}, "127.0.0.1")
    assert (真, 经代理) == ("8.8.8.8", True)


def test_多级代理取第一段():
    真, 经代理 = 来源({"X-Forwarded-For": "1.1.1.1, 10.0.0.2"}, "127.0.0.1")
    assert 真 == "1.1.1.1" and 经代理 is True


def test_本机直连没带头还是本地():
    真, 经代理 = 来源({}, "127.0.0.1")
    assert (真, 经代理) == ("127.0.0.1", False)


def test_本机直连照旧免鉴权():
    assert 放行({}, "127.0.0.1", token="") is True


def test_隧道来的没token必须拒():
    """没设 api_token 时以前会放行——这正是那条洞"""
    assert 放行({"CF-Connecting-IP": "8.8.8.8"}, "127.0.0.1", token="") is False


def test_隧道来的带对token才放行():
    头 = {"CF-Connecting-IP": "8.8.8.8", "X-API-Token": "abc123"}
    assert 放行(头, "127.0.0.1", token="abc123") is True
    assert 放行(头, "127.0.0.1", token="别的") is False


def test_手机浏览器靠cookie不用每次带头():
    头 = {"CF-Connecting-IP": "8.8.8.8", "Cookie": "别的=x; boss_panel_token=abc123"}
    assert 放行(头, "127.0.0.1", token="abc123") is True


def test_局域网里的别的机器也算远程():
    """面板绑的是 0.0.0.0，同wifi下别的设备不该直接控制投递"""
    assert 放行({}, "192.168.1.23", token="") is False


def test_首页这种没数据的页面照常开放():
    assert 放行({}, "8.8.8.8", token="", 免鉴权=True) is True


def test_头里的伪造值不许是回环把自己洗白():
    """访客可以自己塞 X-Forwarded-For: 127.0.0.1，所以"经了代理"就必须看 token，
    不再因为算出来的值是回环而放行"""
    assert 放行({"X-Forwarded-For": "127.0.0.1"}, "127.0.0.1", token="") is False


def test_头名大小写不规范也认():
    """WSGI 会把 CF-Connecting-IP 还原成 Cf-Connecting-Ip，按精确大小写查就漏了这条洞"""
    assert 放行({"Cf-Connecting-Ip": "8.8.8.8"}, "127.0.0.1", token="") is False
    assert 放行({"x-api-token": "abc"}, "10.0.0.5", token="abc") is True
    assert 放行({"X-FORWARDED-FOR": "8.8.8.8", "Cookie": "boss_panel_token=abc"},
                "127.0.0.1", token="abc") is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
