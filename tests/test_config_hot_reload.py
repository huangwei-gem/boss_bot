# -*- coding: utf-8 -*-
"""仪表盘配置缓存测试。

起因：2026-09-29 用脚本把 bot_config.json 里 11 行确认不可用的 AI 接口删掉后，
界面 /api/config 仍然返回 19 行——app._ensure_config() 只在第一次读盘，之后一直用
内存里那份。后果不只是"看不见"：界面上任何一次保存都会把内存里的 19 行写回磁盘，
把外部改动**复活**。属于"改了必须生效"那条红线。
"""
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def flask_app(tmp_path, monkeypatch):
    import boss_bot.unified_config as UC
    monkeypatch.setattr(UC, "BOT_CONFIG_FILE", tmp_path / "bot_config.json")
    monkeypatch.setattr(UC, "OVERRIDES_FILE", tmp_path / "config_overrides.json")
    monkeypatch.setattr(UC, "USER_PROFILE_FILE", tmp_path / "user_profile.json")
    flask_dir = str(Path(__file__).parent.parent / "flask-version")
    if flask_dir not in sys.path:
        sys.path.insert(0, flask_dir)
    from unittest.mock import patch
    with patch("app.UnifiedBotLoop"):
        import app as FLASK_APP
    monkeypatch.setattr(FLASK_APP, "_config", None)
    return FLASK_APP, tmp_path


def _write(path, names):
    """写一份只关心 ai.providers 的配置，并把 mtime 往后推 1 秒保证可比较"""
    body = {"ai": {"providers": [{"name": n, "api_key": "k",
                                  "api_base": "https://x/v1", "model": "m"}
                                 for n in names]}}
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns + 10 ** 9, st.st_mtime_ns + 10 ** 9))


class DashboardConfigReloadTest:
    def test_首次调用会读盘(self, flask_app):
        app, tmp = flask_app
        _write(tmp / "bot_config.json", ["A", "B"])
        assert [p.name for p in app._ensure_config().ai.providers] == ["A", "B"]

    def test_外部改过文件必须重读(self, flask_app):
        app, tmp = flask_app
        p = tmp / "bot_config.json"
        _write(p, ["A", "B"])
        assert len(app._ensure_config().ai.providers) == 2
        _write(p, ["A"])
        assert len(app._ensure_config().ai.providers) == 1, \
            "配置被外部改过却没重读：界面下一次保存会把旧内容写回去"

    def test_文件没变时不反复读盘(self, flask_app):
        app, tmp = flask_app
        _write(tmp / "bot_config.json", ["A"])
        first = app._ensure_config()
        assert app._ensure_config() is first, "每次请求都重读配置文件，白白丢性能"

    def test_读盘失败要保留旧配置(self, flask_app):
        app, tmp = flask_app
        p = tmp / "bot_config.json"
        _write(p, ["A"])
        good = app._ensure_config()
        p.write_text("{这不是 JSON", encoding="utf-8")
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns + 10 ** 9, st.st_mtime_ns + 10 ** 9))
        assert app._ensure_config() is good, "配置写坏时界面应该继续用上一份好的"
