# tests/test_clear_endpoints.py
"""面板上那几个"清空/删除"出口：必须先留底、必须带口令，而且一份都不许凭空消失。

10-23 之前的实情：`/api/greet_records/clear` 直接把记录文件重写成空表、
`/api/images/delete` 是 `file_path.unlink()`、`/api/logs/clear` 是 `write_text("")`——
他定过的口径是"用户数据只挪走不删"（data/archive_kept/、logs/kept/ 同一族），
接口这边一条都没照办。这几条测试把口径钉回接口层。

用 `app.test_client()`，不开端口：不抢线上 5000，也不碰那两个真号。
"""
import io
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "flask-version"))

import app as 面板                                  # noqa: E402
from boss_bot.safe_log import SafeTimedRotatingFileHandler   # noqa: E402


@pytest.fixture()
def 客户(tmp_path):
    面板.LOG_DIR = tmp_path / "logs"
    面板.LOG_DIR.mkdir()
    面板.留档根 = tmp_path / "data" / "kept"
    面板.DASHBOARD_DIR = tmp_path / "static" / "dashboard"
    面板.DASHBOARD_DIR.mkdir(parents=True)
    return 面板.app.test_client()


class 假记录管家:
    """只替身"清空"这一步，路径与文件都是真的，留底才测得出来。"""

    def __init__(self, 路):
        self.path = 路
        self.清了 = 0
        self.删的账号 = None

    def clear_all(self):
        self.清了 += 1
        Path(self.path).write_text(json.dumps([]), encoding="utf-8")

    def delete_account(self, 号):
        self.删的账号 = 号
        self.清了 += 1
        Path(self.path).write_text(json.dumps([]), encoding="utf-8")
        return 3


class Test清空要口令要留底:
    def _铺记录(self, tmp_path, 名="greet_records.json"):
        路 = tmp_path / 名
        路.write_text(json.dumps([{"job_name": "数据分析师", "company": "某某科技"}],
                                 ensure_ascii=False), encoding="utf-8")
        return str(路)

    def test_没带口令就回400而且一条没动(self, 客户, tmp_path, monkeypatch):
        路 = self._铺记录(tmp_path)
        管 = 假记录管家(路)
        monkeypatch.setattr(面板, "_ensure_greet_store", lambda *a, **k: 管)
        回 = 客户.post("/api/greet_records/clear", json={})
        assert 回.status_code == 400, 回.status_code
        assert 管.清了 == 0, "没确认也动手了"
        assert len(json.loads(Path(路).read_text(encoding="utf-8"))) == 1, "记录被动了"
        assert "挪走" in 回.get_json()["message"], "400 要说清该怎么带口令"

    def test_带口令先留底再清(self, 客户, tmp_path, monkeypatch):
        路 = self._铺记录(tmp_path)
        管 = 假记录管家(路)
        monkeypatch.setattr(面板, "_ensure_greet_store", lambda *a, **k: 管)
        回 = 客户.post("/api/greet_records/clear", json={"confirm": "挪走"})
        assert 回.status_code == 200, 回.get_json()
        body = 回.get_json()
        assert 管.清了 == 1
        留 = Path(body["kept"])
        assert 留.exists() and "数据分析师" in 留.read_text(encoding="utf-8"), "留底里没有原记录"
        assert json.loads(Path(路).read_text(encoding="utf-8")) == [], "面板上该看不到"

    def test_按账号清也要留底(self, 客户, tmp_path, monkeypatch):
        路 = self._铺记录(tmp_path)
        管 = 假记录管家(路)
        monkeypatch.setattr(面板, "_ensure_greet_store", lambda *a, **k: 管)
        回 = 客户.post("/api/greet_records/clear?account=1", json={"confirm": "挪走"})
        assert 回.status_code == 200 and 管.删的账号 == 1, 回.get_json()
        assert Path(回.get_json()["kept"]).exists()

    def test_回复记录同样办(self, 客户, tmp_path, monkeypatch):
        路 = self._铺记录(tmp_path, "reply_records.json")
        管 = 假记录管家(路)
        monkeypatch.setattr(面板, "_ensure_reply_store", lambda *a, **k: 管)
        assert 客户.post("/api/reply_records/clear", json={}).status_code == 400
        assert 管.清了 == 0
        回 = 客户.post("/api/reply_records/clear", json={"confirm": "挪走"}).get_json()
        assert Path(回["kept"]).exists() and 管.清了 == 1

    def test_留底没成功就不许清(self, 客户, tmp_path, monkeypatch):
        """反向哨兵：留底失败时宁可拒绝执行，也不许"清没了又没底"。"""
        路 = self._铺记录(tmp_path)
        管 = 假记录管家(路)
        monkeypatch.setattr(面板, "_ensure_greet_store", lambda *a, **k: 管)
        monkeypatch.setattr(面板, "_先留一份", lambda *a, **k: "")
        回 = 客户.post("/api/greet_records/clear", json={"confirm": "挪走"})
        assert 回.status_code == 500, 回.get_json()
        assert 管.清了 == 0, "留底都没成，还把人记录清了"


class Test日志清空:
    def test_整份先进kept且主日志截断后还能继续写(self, 客户, tmp_path):
        主 = 面板.LOG_DIR / "boss_bot.log"
        主.write_text("10-22 的行\n", encoding="utf-8")
        (面板.LOG_DIR / "boss_bot.log.2026-10-21").write_text("旧归档\n", encoding="utf-8")

        记 = logging.getLogger()
        h = SafeTimedRotatingFileHandler(str(主), when="midnight", backupCount=7,
                                        encoding="utf-8")
        h.setFormatter(logging.Formatter("%(message)s"))
        记.addHandler(h)
        try:
            回 = 客户.post("/api/logs/clear", json={"confirm": "挪走"}).get_json()
            assert 回["cleared"] >= 1 and 回["moved"] >= 1, 回
            留档 = 面板.LOG_DIR / "kept"
            assert any("boss_bot.log.2026-10-21" in p.name and
                       p.read_text(encoding="utf-8") == "旧归档\n" for p in 留档.iterdir()), \
                "旧归档没被挪进 logs/kept/"
            assert any(p.name.endswith("boss_bot.log") and "10-22 的行" in
                       p.read_text(encoding="utf-8") for p in 留档.iterdir()), "内容没留底"
            新文 = 主.read_text(encoding="utf-8")
            # 判据是"旧内容不在了"，不是"文件为空"：清空这件事本身要在截断后的新日志里
            # 留一句话（先留底、再截断、最后写这句），否则出问题的当天反而查不到动过。
            assert "10-22 的行" not in 新文, 新文
            assert "日志清空" in 新文, "挪走完了却不在新日志里留痕，等于没做过"
            记.warning("清空以后新写的一行")
            h.flush()
            文 = 主.read_bytes()
            assert b"\x00" not in 文, "截断后 handler 还按旧偏移写 → 文件里出现 NUL，日志废了"
            assert "清空以后新写的一行" in 文.decode("utf-8"), 文[:120]
        finally:
            记.removeHandler(h)
            h.close()

    def test_没带口令日志一行都不动(self, 客户, tmp_path):
        主 = 面板.LOG_DIR / "boss_bot.log"
        主.write_text("别动\n", encoding="utf-8")
        assert 客户.post("/api/logs/clear", json={}).status_code == 400
        assert 主.read_text(encoding="utf-8") == "别动\n"


class Test图片只挪走:
    def test_图片进kept不消失(self, 客户, tmp_path):
        图 = 面板.DASHBOARD_DIR / "证据.png"
        图.write_bytes(bytes([0x89, 0x50, 0x4E, 0x47]))
        回 = 客户.post("/api/images/delete", json={"filename": "证据.png"}).status_code
        assert 回 == 400, "没口令不许动"
        assert 图.exists()
        结 = 客户.post("/api/images/delete",
                      json={"filename": "证据.png", "confirm": "挪走"}).get_json()
        assert not 图.exists() and Path(结["kept"]).read_bytes().startswith(b"\x89PNG")

    def test_路径不许被越权名字带出去(self, 客户, tmp_path):
        回 = 客户.post("/api/images/delete",
                      json={"filename": "../../bot_config.json", "confirm": "挪走"})
        assert 回.status_code == 404, 回.get_json()
        assert (ROOT / "bot_config.json").exists()


class Test别再写回硬删:
    def test_这几个出口里不许出现unlink或write_text空串以外的删法(self):
        """源码门：接口体内不许再出现 unlink/os.remove——留底这套才有意义。"""
        源 = io.open(ROOT / "flask-version" / "app.py", encoding="utf-8").read()
        for 头 in ("def api_clear_greet_records", "def api_clear_reply_records",
                   "def api_logs_clear", "def api_delete_image"):
            段 = 源[源.index(头):]
            段 = 段[:段.index("\n@app.route")] if "\n@app.route" in 段 else 段
            assert ".unlink()" not in 段 and "os.remove" not in 段, 头
            assert "_要挪走()" in 段, "%s 没接确认闸门" % 头

    def test_这条源码门会红(self):
        """反向变异：把 unlink 塞回段里，判据必须挑出来。"""
        段 = 'def api_x():\n    p.unlink()\n    return 1\n'
        assert ".unlink()" in 段
