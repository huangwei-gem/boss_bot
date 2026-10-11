# -*- coding: utf-8 -*-
"""个人画像到底以哪份文件为准。

面板「个人画像」那一页保存时只写 user_profile.json（flask-version/app.py:1457），
但加载顺序是 bot_config.json → user_profile.json → config_overrides.json，
而 config_overrides.json 里也存了一份 user_profile 快照（它是保存整份配置时
同步进去的）。快照后应用，于是画像页上改的字看不见效果——
2026-10-11 三端一致性测试就是这么炸出 9 条不一致的：
user_profile.json 写着 黄维 / 本科在读（统计学）/ 有数据标注经验，
生效值却是 求职者 / 本科学历 / 空经验。
经验被抹成空，AI 回「你有经验吗」时手上就没有可引用的东西，答非所问。
"""
import json

from boss_bot.unified_config import UnifiedConfig


def _加载(tmp_path, 画像, 快照):
    (tmp_path / "user_profile.json").write_text(
        json.dumps(画像, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config_overrides.json").write_text(
        json.dumps({"user_profile": 快照}, ensure_ascii=False), encoding="utf-8")
    return UnifiedConfig.load(
        config_path=str(tmp_path / "bot_config.json"),
        profile_path=str(tmp_path / "user_profile.json"),
        overrides_path=str(tmp_path / "config_overrides.json"),
    )


class 画像页保存要真生效Test:
    def test_画像文件赢过覆盖文件里的旧快照(self, tmp_path):
        cfg = _加载(tmp_path,
                    {"name": "黄维", "experience": "有数据标注经验", "education": "本科在读（统计学）"},
                    {"name": "求职者", "experience": "", "education": "本科学历"})
        assert cfg.user_profile.name == "黄维"
        assert cfg.user_profile.experience == "有数据标注经验"
        assert cfg.user_profile.education == "本科在读（统计学）"

    def test_快照仍然兜住画像文件没写的字段(self, tmp_path):
        """画像页不管的字段（历史遗留写在快照里）不能因为这次改动直接变空。"""
        cfg = _加载(tmp_path, {"name": "黄维"},
                    {"name": "求职者", "highlights": ["上手就能干"], "contact": "13800000000"})
        assert cfg.user_profile.name == "黄维"
        assert cfg.user_profile.highlights == ["上手就能干"]
        assert cfg.user_profile.contact == "13800000000"

    def test_空字符串不许把有值的画像抹掉(self, tmp_path):
        cfg = _加载(tmp_path, {"skills": ["数据标注", "SQL"], "salary_expectation": "面议"},
                    {"skills": [], "salary_expectation": ""})
        assert cfg.user_profile.skills == ["数据标注", "SQL"]
        assert cfg.user_profile.salary_expectation == "面议"

    def test_真机上那三份要对得上(self):
        """仓库里现在这三份文件必须给出同一个名字，不然面板和存档又是两套。"""
        from pathlib import Path
        根 = Path(__file__).resolve().parent.parent
        cfg = UnifiedConfig.load(
            config_path=str(根 / "bot_config.json"),
            profile_path=str(根 / "user_profile.json"),
            overrides_path=str(根 / "config_overrides.json"),
        )
        画像文件 = json.loads((根 / "user_profile.json").read_text(encoding="utf-8"))
        assert cfg.user_profile.name == 画像文件["name"], \
            "画像页保存的名字没生效：检查 config_overrides.json 里的 user_profile 快照"
