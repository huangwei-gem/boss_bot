# -*- coding: utf-8 -*-
"""面板的鉴权在真请求上走一遍：隧道流量不许再被当成本机。

起因是 2026-10-09 想把这个面板"上线"（cloudflared 隧道）：面板原来只看
request.remote_addr，而隧道永远从 127.0.0.1 连源站，真实访客在 CF-Connecting-IP 里。
实测过带这个头的本机请求 /api/status 回 200 —— 那等于把 /api/stop、
/api/cookies/delete 摊到公网。规则本身在 access_guard 有纯函数用例，
这一份管的是"真的挂在 Flask 上生效了没有"。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_config_hot_reload import flask_app  # noqa: F401,E402  跨文件复用夹具


def _配置(flask_app, token=""):
    app, tmp = flask_app
    (tmp / "bot_config.json").write_text(
        json.dumps({"api_token": token}, ensure_ascii=False), encoding="utf-8")
    return app, app.app.test_client()


def test_本机直连照旧不用凭据(flask_app):
    app, c = _配置(flask_app, token="")
    assert c.get("/api/status").status_code != 403


def test_隧道形状的来源没凭据要拒(flask_app):
    """回环 + CF-Connecting-IP，就是 cloudflared 打到源站的樣子"""
    app, c = _配置(flask_app, token="")
    r = c.get("/api/status", headers={"CF-Connecting-IP": "8.8.8.8"})
    assert r.status_code == 403


def test_设了口令之后隧道带对的头才通(flask_app):
    app, c = _配置(flask_app, token="t0k-abc")
    头 = {"CF-Connecting-IP": "8.8.8.8"}
    assert c.get("/api/status", headers=头).status_code == 403
    assert c.get("/api/status", headers=dict(头, **{"X-API-Token": "别家的"})).status_code == 403
    assert c.get("/api/status", headers=dict(头, **{"X-API-Token": "t0k-abc"})).status_code != 403


def test_手机浏览器输一次口令之后走cookie(flask_app):
    app, c = _配置(flask_app, token="t0k-abc")
    r = c.get("/?token=t0k-abc", headers={"CF-Connecting-IP": "8.8.8.8"})
    assert "boss_panel_token" in (r.headers.get("Set-Cookie") or "")
    assert c.get("/api/status", headers={"CF-Connecting-IP": "8.8.8.8"}).status_code != 403


def test_口令不对不许落cookie(flask_app):
    app, c = _配置(flask_app, token="t0k-abc")
    r = c.get("/?token=猜的", headers={"CF-Connecting-IP": "8.8.8.8"})
    assert "boss_panel_token" not in (r.headers.get("Set-Cookie") or "")


def test_危险接口在隧道来源下也要凭据(flask_app):
    """停投递这种动作接口不能因为是回环就放过"""
    app, c = _配置(flask_app, token="")
    assert c.post("/api/stop", headers={"CF-Connecting-IP": "8.8.8.8"}).status_code == 403


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
