# -*- coding: utf-8 -*-
"""save() 不许把用户配置写成出厂设置。

2026-10-04 事故：一个加载失败的进程拿默认配置去 save()，10572 字节的
bot_config.json（2 个账号、12 个 AI 接口、17 条回复规则、简历信息）被 3644
字节的出厂默认覆盖，只剩 1 个账号。恢复靠的是当天早些时候手留的 .bak。
这里补两道闸：每次覆盖前先落一份带时间的备份；"账号变少 + AI 接口清空"这种
只有默认回退才会同时出现的组合，直接拒绝写盘。
"""
import json
from pathlib import Path

from boss_bot.unified_config import UnifiedConfig


def _load(path):
    return UnifiedConfig.load(
        config_path=str(path),
        profile_path=str(Path(path).parent / "none.json"),
        overrides_path=str(Path(path).parent / "none_overrides.json"),
    )


def _rich_disk_config(path):
    path.write_text(json.dumps({
        "ai": {"enabled": True, "providers": [
            {"name": "A", "api_key": "k1", "api_base": "https://a/v1", "model": "m1"},
            {"name": "B", "api_key": "k2", "api_base": "https://b/v1", "model": "m2"},
        ]},
        "accounts": [
            {"name": "主账号", "cookie_file": "zhipin_cookies.json",
             "jobs": [{"city": "长沙", "query": "数据分析"}]},
            {"name": "账号2", "cookie_file": "zhipin_cookies_1.json",
             "jobs": [{"city": "上海", "query": "数据挖掘"}]},
        ],
        "resume": {"school": "某某大学", "major": "统计学"},
    }, ensure_ascii=False), encoding="utf-8")


class BackupTest:
    def test_覆盖写之前先备份旧文件(self, tmp_path):
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        before = cfg_file.read_text(encoding="utf-8")

        cfg = _load(cfg_file)
        cfg.save(str(cfg_file))

        backups = sorted(tmp_path.glob("bot_config.json.bak-*"))
        assert backups, "改完必须留一份能回滚的旧文件"
        assert json.loads(backups[-1].read_text(encoding="utf-8")) == \
            json.loads(before)

    def test_首次保存没有旧文件也不报错(self, tmp_path):
        cfg = UnifiedConfig()
        cfg.save(str(tmp_path / "bot_config.json"))
        assert (tmp_path / "bot_config.json").exists()
        assert not list(tmp_path.glob("bot_config.json.bak-*"))


class DefaultFallbackGuardTest:
    def test_默认配置不许覆盖有账号有接口的文件(self, tmp_path):
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        before = json.loads(cfg_file.read_text(encoding="utf-8"))

        UnifiedConfig().save(str(cfg_file))

        assert json.loads(cfg_file.read_text(encoding="utf-8")) == before, \
            "加载失败的进程一保存就把用户配置写成出厂设置"

    def test_拒绝写盘要留下日志(self, tmp_path, caplog):
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        with caplog.at_level("WARNING"):
            UnifiedConfig().save(str(cfg_file))
        assert any("出厂设置" in r.message or "拒绝" in r.message for r in caplog.records)

    def test_只删账号照常允许(self, tmp_path):
        """用户真的在界面上删掉一个账号时不能被闸门拦住"""
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        cfg = _load(cfg_file)
        cfg.greet.accounts = cfg.greet.accounts[:1]
        cfg.save(str(cfg_file))
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert len(saved["accounts"]) == 1
        assert len(saved["ai"]["providers"]) == 2

    def test_只清空接口照常允许(self, tmp_path):
        """用户真的把 AI 接口删干净时同样不能被拦"""
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        cfg = _load(cfg_file)
        cfg.ai.providers = []
        cfg.ai.enabled = False
        cfg.save(str(cfg_file))
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved["ai"]["providers"] == []
        assert len(saved["accounts"]) == 2

    def test_改岗位口径不算缩水(self, tmp_path):
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        cfg = _load(cfg_file)
        for acc in cfg.greet.accounts:
            acc.jobs[0].city = "全国"
            acc.jobs[0].job_type = "兼职"
        cfg.save(str(cfg_file))
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert [j["city"] for a in saved["accounts"] for j in a["jobs"]] == ["全国", "全国"]
