# -*- coding: utf-8 -*-
"""微信推送（公众号测试号模板消息）的回归。

不真发网络请求：_post/_get 全部替换掉，只验"发出去的东西长什么样"。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boss_bot import wechat_push as wp  # noqa: E402


class TestCreds:

    def test_没有凭据文件时说清缺文件(self, tmp_path):
        with pytest.raises(RuntimeError) as e:
            wp.load_creds(str(tmp_path / "nope.json"))
        assert "nope.json" in str(e.value)
        assert "未配置" in str(e.value)

    def test_缺模板ID时点名去网页新增(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"appid": "a", "secret": "s", "openid": "o"}),
                     encoding="utf-8")
        with pytest.raises(RuntimeError) as e:
            wp.load_creds(str(p))
        assert "template_id" in str(e.value)
        assert "新增测试模板" in str(e.value), "得告诉他这一步只能在网页上做"


class TestChunk:

    def test_不超长就一条(self):
        assert wp.chunk_text("第一行\n第二行") == ["第一行\n第二行"]

    def test_按行切且不丢字(self):
        body = "\n".join(f"{i}. 张三｜某某科技｜数据分析｜150元/天" for i in range(60))
        parts = wp.chunk_text(body, limit=120)
        assert all(len(p) <= 120 for p in parts)
        assert "\n".join(parts).splitlines() == body.splitlines()

    def test_分块标题不会被劈开(self):
        body = "【面试邀请】\n" + ("内容" * 200)
        parts = wp.chunk_text(body, limit=50)
        assert parts[0].startswith("【面试邀请】")

    def test_单行超长也能切开(self):
        long = "数" * 300
        parts = wp.chunk_text(long, limit=100)
        assert "".join(parts) == long
        assert all(len(p) <= 100 for p in parts)


class TestSend:

    def _capture(self, monkeypatch, replies=None):
        sent = []

        def fake_get(url):
            return {"access_token": "TOK", "expires_in": 7200}

        def fake_post(url, payload):
            sent.append((url, payload))
            return (replies or {}).get(len(sent),
                                       {"errcode": 0, "errmsg": "ok", "msgid": 1})

        monkeypatch.setattr(wp, "_get", fake_get)
        monkeypatch.setattr(wp, "_post", fake_post)
        monkeypatch.setattr(wp.time, "sleep", lambda s: None)
        return sent

    def test_载荷形状是模板消息那一套(self, monkeypatch, tmp_path):
        creds = {"appid": "a", "secret": "s", "openid": "OPEN", "template_id": "TPL"}
        sent = self._capture(monkeypatch)
        wp.send_text("面试邀请 3 条", creds=creds)
        url, payload = sent[0]
        assert "template/send" in url and "access_token=TOK" in url
        assert payload["touser"] == "OPEN"
        assert payload["template_id"] == "TPL"
        assert payload["data"]["first"]["value"] == "BOSS 汇报"
        assert payload["data"]["remark"]["value"] == "面试邀请 3 条"

    def test_长文自动分条并标序号(self, monkeypatch):
        creds = {"appid": "a", "secret": "s", "openid": "OPEN", "template_id": "TPL"}
        sent = self._capture(monkeypatch)
        body = "\n".join("第 %d 条联系方式：张三｜某某公司｜13800000000" % i
                         for i in range(80))
        results = wp.send_text(body, creds=creds)
        assert len(results) > 1
        assert results[0]["ok"] and results[-1]["ok"]
        firsts = [p["data"]["first"]["value"] for _, p in sent]
        assert firsts[0].endswith("(1/%d）" % len(results)) or "1/" in firsts[0]
        assert len(firsts) == len(results)

    def test_单条失败如实报出来(self, monkeypatch):
        creds = {"appid": "a", "secret": "s", "openid": "OPEN", "template_id": "TPL"}
        sent = self._capture(monkeypatch,
                             replies={1: {"errcode": 40003, "errmsg": "invalid openid"}})
        results = wp.send_text("只有一条", creds=creds)
        assert results[0]["ok"] is False
        assert results[0]["errcode"] == 40003


class TestTokenCache:

    def test_第二次不再重新取token(self, monkeypatch, tmp_path):
        calls = []

        def fake_get(url):
            calls.append(url)
            return {"access_token": "TOK", "expires_in": 7200}

        monkeypatch.setattr(wp, "_get", fake_get)
        cache = str(tmp_path / "tok.json")
        wp.get_token("a", "s", cache_path=cache, now=1000.0)
        wp.get_token("a", "s", cache_path=cache, now=1100.0)
        assert len(calls) == 1

    def test_快过期就重取(self, monkeypatch, tmp_path):
        calls = []

        def fake_get(url):
            calls.append(url)
            return {"access_token": "TOK", "expires_in": 7200}

        monkeypatch.setattr(wp, "_get", fake_get)
        cache = str(tmp_path / "tok.json")
        wp.get_token("a", "s", cache_path=cache, now=1000.0)
        wp.get_token("a", "s", cache_path=cache, now=1000.0 + 7200)
        assert len(calls) == 2


class Test不许写仓库里那份真缓存:
    """现测 2026-10-27：跑一趟 pytest 把 `data/wechat_token.json` 刷成了
    `{"token": "TOK"}`——那是本文件的假接口返回值，把线上那把 access_token 盖掉了。
    跟 10-19 那条 Cookie 备份是同一个坑：模块级默认参数在 def 那一刻焊死，
    conftest 再怎么换 `wp.TOKEN_CACHE` 都够不着。
    """

    def test_发送走的是隔开的那份缓存(self, monkeypatch, tmp_path):
        隔开的 = str(tmp_path / "隔开的.json")
        仓库那份 = ROOT / "data" / "wechat_token.json"
        改前 = 仓库那份.read_bytes() if 仓库那份.exists() else None
        monkeypatch.setattr(wp, "TOKEN_CACHE", 隔开的)
        monkeypatch.setattr(wp, "_get", lambda url: {"access_token": "TOK", "expires_in": 7200})
        wp.get_token("a", "s")            # 不传 cache_path：走默认那一条才算数
        assert Path(隔开的).is_file(), "没走 TOKEN_CACHE（默认值多半焊死在 def 上）"
        改后 = 仓库那份.read_bytes() if 仓库那份.exists() else None
        assert 改前 == 改后, "测试把仓库里那把真 token 盖掉了"

    def test_默认参数不许在_def_就焊死(self):
        源 = (ROOT / "boss_bot" / "wechat_push.py").read_text(encoding="utf-8")
        行 = [l for l in 源.splitlines() if l.strip().startswith("def get_token(")]
        assert 行, "找不到 get_token"
        assert "cache_path=None" in 行[0], \
            "默认值写成了模块常量＝测试换不掉，跑一趟就把线上缓存刷成假 token"

    def test_conftest_真的把这份缓存指走了(self):
        """光"用的时候现取路径"还不够——没人换那个模块属性，测试照样写仓库那份。
        这条按代码行判，注释里提到不算（10-19 那条 Cookie 备份用的是同一种门）。"""
        源 = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        代码 = "\n".join(l for l in 源.splitlines() if not l.strip().startswith("#"))
        assert "TOKEN_CACHE" in 代码, "微信 token 缓存没人隔——跑一趟就盖掉线上那把"
        指走 = [l for l in 代码.splitlines() if "WP.TOKEN_CACHE" in l and "mk测试临时目录" in l]
        assert 指走, "conftest 里只是提了一下 TOKEN_CACHE，没有真把它指到临时目录"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
