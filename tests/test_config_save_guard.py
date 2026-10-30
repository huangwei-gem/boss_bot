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


def _rich_disk_config(path, 额外岗位=0):
    岗位 = [{"city": "全国", "query": "数据标注"}, {"city": "全国", "query": "线上运营助理"},
            {"city": "全国", "query": "游戏代练"}][:额外岗位]
    path.write_text(json.dumps({
        "ai": {"enabled": True, "providers": [
            {"name": "A", "api_key": "k1", "api_base": "https://a/v1", "model": "m1"},
            {"name": "B", "api_key": "k2", "api_base": "https://b/v1", "model": "m2"},
        ], "target_job_keywords": ["数据分析", "数据标注"],
           "custom_scoring_prompt": "猎头代招降权"},
        "accounts": [
            {"name": "主账号", "cookie_file": "zhipin_cookies.json",
             "greeting_message": "您好～我是统计学本科，想找线上兼职数据分析方向。",
             "jobs": [{"city": "长沙", "query": "数据分析"}] + 岗位},
            {"name": "账号2", "cookie_file": "zhipin_cookies_1.json",
             "greeting_message": "您好～想找线上兼职数据标注方向。",
             "jobs": [{"city": "上海", "query": "数据挖掘"}] + 岗位},
        ],
        "resume": {"school": "某某大学", "major": "统计学",
                   "self_intro": "做过文本/图像/语音多模态标注"},
    }, ensure_ascii=False), encoding="utf-8")


class BackupTest:
    def test_覆盖写之前先备份旧文件(self, tmp_path):
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        before = cfg_file.read_text(encoding="utf-8")

        cfg = _load(cfg_file)
        cfg.save(str(cfg_file))

        backups = sorted((tmp_path / "config_backups").glob("bot_config.json.bak-*"))
        assert backups, "改完必须留一份能回滚的旧文件"
        assert json.loads(backups[-1].read_text(encoding="utf-8")) == \
            json.loads(before)

    def test_备份不许摊在配置文件同目录(self, tmp_path):
        """根目录那 79 份 bot_config.json.bak-* 就是这么攒出来的。"""
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        _load(cfg_file).save(str(cfg_file))
        assert not list(tmp_path.glob("*.bak-*")), "备份又落回配置文件旁边了"

    def test_同一秒里保存两次不许把前一份盖掉(self, tmp_path):
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file)
        _load(cfg_file).save(str(cfg_file))
        _load(cfg_file).save(str(cfg_file))
        份s = sorted((tmp_path / "config_backups").glob("bot_config.json.bak-*"))
        assert len(份s) == 2, "两次保存只留下一份备份＝有一份回滚点丢了"
        assert 份s[0].read_text(encoding="utf-8") != 份s[1].read_text(encoding="utf-8") or \
            份s[0].name != 份s[1].name

    def test_备份只留最近二十份超出的挪进_kept(self, tmp_path):
        from boss_bot.unified_config import 备份旧配置, 备份保留份数
        目录 = tmp_path / "config_backups"
        目录.mkdir()
        先前 = 备份保留份数 + 5
        for i in range(先前):
            (目录 / ("bot_config.json.bak-202610%02d-%06d" % (1 + i // 60, i * 111))).write_text(
                "{}", encoding="utf-8")
        备份旧配置(tmp_path / "bot_config.json", {"accounts": []})
        assert len(list(目录.glob("bot_config.json.bak-*"))) == 备份保留份数, "超了却没挪走"
        assert len(list((目录 / "kept").glob("bot_config.json.bak-*"))) == 先前 + 1 - 备份保留份数, \
            "挪走的必须留着不删"

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

    def test_账号数没变但接口简历岗位一起清空也要拒绝(self, tmp_path):
        """2026-10-10 晚上真被盖掉的那次，老闸门没响：账号还是 2 个。

        写盘的是 tests/test_headless_switch.py 里测试自己造的 config，
        它和磁盘上那份比：AI 接口 15→0、简历 7 段→全空、每号岗位 7/6 条→1 条、
        招呼语 132 字→55 字。账号数不变，"账号变少 + 接口清空"这一条组合就不成立，
        于是闸门形同虚设，一晚上跑了三次（18:00、19:53、22:25 的 .bak 成对）。
        """
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file, 额外岗位=3)
        before = json.loads(cfg_file.read_text(encoding="utf-8"))

        测试自造的 = UnifiedConfig()
        测试自造的.greet.accounts = _load(cfg_file).greet.accounts  # 账号数一模一样
        测试自造的.save(str(cfg_file))

        assert json.loads(cfg_file.read_text(encoding="utf-8")) == before, \
            "接口/简历/岗位同时清空，账号数却没变——这正是默认回退的形状"

    def test_单删几条岗位照常允许(self, tmp_path):
        """界面上删掉几个岗位只命中"岗位"一处，不能被新闸门误伤。"""
        cfg_file = tmp_path / "bot_config.json"
        _rich_disk_config(cfg_file, 额外岗位=3)
        cfg = _load(cfg_file)
        cfg.greet.accounts[0].jobs = cfg.greet.accounts[0].jobs[:1]
        cfg.save(str(cfg_file))
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert len(saved["accounts"][0]["jobs"]) == 1
        assert len(saved["ai"]["providers"]) == 2

