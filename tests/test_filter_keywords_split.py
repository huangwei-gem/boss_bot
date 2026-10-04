# -*- coding: utf-8 -*-
"""自定义筛选词要真的能用。

2026-10-04 现场：用户在面板里输入"主播，地推，销售"（中文逗号），前端只按英文
逗号切，落盘成了 ["主播，地推，销售"] 一个条目；否决词是按"整条出现在 JD 里"
比对的，所以这三个词一条都没生效——他以为设了门槛，其实什么都没拦。
"""
import json
from pathlib import Path

from boss_bot.unified_config import UnifiedConfig, normalize_filter_keywords


class NormalizeTest:
    def test_中文逗号顿号分号换行都算分隔符(self):
        got = normalize_filter_keywords("主播，地推、销售；客服,推广")
        assert got == ["主播", "地推", "销售", "客服", "推广"], got

    def test_列表里混着整串也要拆开(self):
        got = normalize_filter_keywords(["主播，地推，销售", "需坐班"])
        assert got == ["主播", "地推", "销售", "需坐班"], got

    def test_去空去重保序(self):
        got = normalize_filter_keywords(["  主播 ", "", "主播", "地推"])
        assert got == ["主播", "地推"], got

    def test_没有分隔符的长短语原样保留(self):
        phrase = "岗位实际是推广/地推"
        assert normalize_filter_keywords([phrase]) == [phrase]

    def test_空值不炸(self):
        assert normalize_filter_keywords(None) == []
        assert normalize_filter_keywords([]) == []
        assert normalize_filter_keywords("  ") == []


class LoadSideTest:
    def test_读盘就把脏条目拆开(self, tmp_path):
        """面板已经写坏过一次，读的时候必须自愈，不能等用户重新输入"""
        cfg_file = tmp_path / "bot_config.json"
        cfg_file.write_text(json.dumps({
            "ai": {"enabled": True, "custom_filter_keywords": ["主播，地推，销售"]},
        }, ensure_ascii=False), encoding="utf-8")
        cfg = UnifiedConfig.load(
            config_path=str(cfg_file),
            profile_path=str(tmp_path / "none.json"),
            overrides_path=str(tmp_path / "none_overrides.json"))
        assert cfg.ai.custom_filter_keywords == ["主播", "地推", "销售"]

    def test_存出去的还是拆好的(self, tmp_path):
        cfg_file = tmp_path / "bot_config.json"
        cfg_file.write_text(json.dumps({
            "ai": {"custom_filter_keywords": ["主播，地推"]},
        }, ensure_ascii=False), encoding="utf-8")
        cfg = UnifiedConfig.load(
            config_path=str(cfg_file),
            profile_path=str(tmp_path / "none.json"),
            overrides_path=str(tmp_path / "none_overrides.json"))
        cfg.save(str(cfg_file))
        saved = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert saved["ai"]["custom_filter_keywords"] == ["主播", "地推"]


class FrontendContractTest:
    def test_前端切词用的分隔符与后端一致(self):
        """JS 里那行 split 不许只认英文逗号"""
        html = (Path(__file__).resolve().parents[1] / "flask-version"
                / "templates" / "index.html").read_text(encoding="utf-8")
        lines = html.splitlines()
        at = [i for i, l in enumerate(lines)
              if "getElementById('customFilterKeywords')" in l]
        assert at, "找不到自定义筛选词的取值那一行"
        snippet = " ".join(lines[at[0]:at[0] + 3])
        assert "split(" in snippet, snippet[:200]
        assert "，" in snippet and "、" in snippet, f"前端只按英文逗号切：{snippet[:200]}"
