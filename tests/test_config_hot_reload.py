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


class 外部记账接口Test:
    """工具/脚本记的账必须经过在线面板，不能自己去写 reply_records.json。

    2026-10-07 清剿了 68 单"自己找上门又该拒的"会话，BOSS 里气泡发出去了，
    前端却一条拒绝记录都筛不到：面板进程把这份文件整份写回，工具在另一个进程
    里 append 的那 68 条被盖掉了。同一天会话存档没出事，因为那是分文件合并写的。
    """

    def test_接口追加的记录_GET_得到(self, flask_app, monkeypatch):
        app, tmp = flask_app
        import boss_bot.reply_record as RR
        monkeypatch.setattr(RR, "REPLY_RECORDS_FILE", tmp / "reply_records.json")
        monkeypatch.setattr(RR, "_reply_store", None)
        c = app.app.test_client()
        body = {"chat_name": "伍女士", "job_name": "长沙市快递员7-12K",
                "received_message": "我想要和您交换微信，您是否同意",
                "reply_content": "不好意思，我这边只找线上远程就能做的兼职",
                "reply_source": "family_filter", "reply_reason": "存量清剿：命中「快递」",
                "account_index": 0}
        assert c.post("/api/reply_records", json=body).status_code == 200
        rows = c.get("/api/reply_records").get_json()["records"]
        追 = [r for r in rows if r["chat_name"] == "伍女士"]
        assert len(追) == 1, 追
        assert 追[0]["reply_source"] == "family_filter"
        assert "存量清剿" in 追[0]["reply_reason"]

    def test_陌生来源不许写进来(self, flask_app, monkeypatch):
        """这口子开给工具补记账，不是给任意脚本往记录里灌数据的。"""
        app, tmp = flask_app
        import boss_bot.reply_record as RR
        monkeypatch.setattr(RR, "REPLY_RECORDS_FILE", tmp / "reply_records.json")
        monkeypatch.setattr(RR, "_reply_store", None)
        c = app.app.test_client()
        r = c.post("/api/reply_records", json={"chat_name": "某人",
                                              "reply_source": "ai"})
        assert r.status_code == 400, r.get_json()
        assert c.get("/api/reply_records").get_json()["total"] == 0
