# -*- coding: utf-8 -*-
"""写坏与读坏都不许把用户数据弄没。

2026-10-19 早上：面板昨天 19:17 跟着整机一起没的（不是崩，是重启），今早起来
日志里两条 `[自进化] 加载进化数据失败: Expecting value: line 1 column 1 (char 0)`，
data/evolution_data.json 的 133109 字节全是 NUL。原因就在写法的顺序：
`open(path, "w")` 一开就把原文件截断，序列化还在内存里，机器一断电，磁盘上只剩
一片零。更要紧的是读的那一半——解析失败只记一句 ERROR 就当没事，内存里是空的，
下一次保存把空状态写回去，等于机器替他把 10-10 之后的进化数据又清了一遍。

Cookie 那条链 10-18 已经收成"写 .tmp → 回读 → os.replace"（见
test_cookie_save_honest.py），这套状态文件还留着裸写。这里钉三件事：
①序列化失败不许动原文件；②读不出来的原文件要先挪开留证据，不许原地被覆盖；
③这些写法本身不许再回到源码里。
"""

import io
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.self_evolve import SelfEvolveEngine
import boss_bot.unified_config as uc


class Test序列化失败不许毁掉已有内容:
    def _引擎(self, tmp_path):
        文件 = tmp_path / "evolution_data.json"
        条 = []
        eng = SelfEvolveEngine(config={}, data_file=str(文件), log_callback=条.append)
        return eng, 文件, 条

    def test_进化数据写坏了原文件还在(self, tmp_path):
        eng, 文件, _ = self._引擎(tmp_path)
        eng._strategy_adjustments = [{"规则": "别复述"}]
        eng.save_evolution_data()
        原样 = 文件.read_text(encoding="utf-8")
        assert json.loads(原样)["strategy_adjustments"] == [{"规则": "别复述"}]

        eng._strategy_adjustments = [object()]  # 序列化一定会中途抛
        eng.save_evolution_data()
        assert 文件.read_text(encoding="utf-8") == 原样, \
            "保存失败把已有进化数据截断了：裸 open(path,'w') 一开就没救了"

    def test_质量数据写坏了原文件还在(self, tmp_path):
        eng, 文件, _ = self._引擎(tmp_path)
        eng._rule_adjustments = [{"rule_key": "x"}]
        eng._save_quality_data()
        质量文件 = [p for p in tmp_path.iterdir() if p.name.startswith("self_evolve")]
        assert 质量文件, f"质量数据没落盘: {list(tmp_path.iterdir())}"
        原样 = 质量文件[0].read_text(encoding="utf-8")
        assert json.loads(原样)["rule_adjustments"] == [{"rule_key": "x"}]

        eng._rule_adjustments = [object()]
        eng._save_quality_data()
        assert 质量文件[0].read_text(encoding="utf-8") == 原样

    def test_主账号配置写坏了原配置还在(self, tmp_path, monkeypatch):
        目标 = tmp_path / "bot_config.json"
        monkeypatch.setattr(uc, "BOT_CONFIG_FILE", 目标)
        uc.save_config({"方向": "数据分析"})
        原样 = 目标.read_text(encoding="utf-8")
        with pytest.raises(TypeError):
            uc.save_config({"方向": object()})
        assert 目标.read_text(encoding="utf-8") == 原样, \
            "配置回写失败把 bot_config 截断了——10-10 那次全量脚本写空就是这个形状"

    def test_按号覆盖配置写坏了原文件还在(self, tmp_path, monkeypatch):
        目标 = tmp_path / "config_overrides.json"
        monkeypatch.setattr(uc, "OVERRIDES_FILE", 目标)
        uc.save_overrides({"user_profile": {"city": "长沙"}})
        原样 = 目标.read_text(encoding="utf-8")
        with pytest.raises(TypeError):
            uc.save_overrides({"user_profile": {"city": object()}})
        assert 目标.read_text(encoding="utf-8") == 原样


class Test读不出来的原文件要留证据:
    def _全NUL(self, 文件):
        文件.write_bytes(bytes([0]) * 4096)

    def test_坏文件挪开留一份原始字节(self, tmp_path):
        文件 = tmp_path / "evolution_data.json"
        self._全NUL(文件)
        条 = []
        SelfEvolveEngine(config={}, data_file=str(文件), log_callback=条.append)
        assert any("ERROR" in l and "进化数据" in l for l in 条), f"读坏没吭声: {条}"
        挪开 = [p for p in tmp_path.iterdir() if ".corrupt" in p.name]
        assert 挪开, f"坏文件没留证据，下一次保存会直接盖掉: {list(tmp_path.iterdir())}"
        assert 挪开[0].read_bytes() == bytes([0]) * 4096

    def test_之后保存不许把坏件覆盖掉(self, tmp_path):
        文件 = tmp_path / "evolution_data.json"
        self._全NUL(文件)
        eng = SelfEvolveEngine(config={}, data_file=str(文件), log_callback=lambda s: None)
        挪开 = [p for p in tmp_path.iterdir() if ".corrupt" in p.name][0]
        eng._strategy_adjustments = [{"新": "开始"}]
        eng.save_evolution_data()
        assert 挪开.read_bytes() == bytes([0]) * 4096, "留的证据被下一次保存吃了，等于没留"
        assert json.loads(文件.read_text(encoding="utf-8"))["strategy_adjustments"]

    def test_质量数据那份也一样要挪(self, tmp_path):
        文件 = tmp_path / "evolution_data.json"
        质量 = tmp_path / "self_evolve_data.json"
        self._全NUL(质量)
        SelfEvolveEngine(config={}, data_file=str(文件), log_callback=lambda s: None)
        挪开 = [p for p in tmp_path.iterdir() if ".corrupt" in p.name and "self_evolve" in p.name]
        assert 挪开, f"质量数据那份没挪: {sorted(p.name for p in tmp_path.iterdir())}"


class Test写法不许再退回去:
    def _源(self, 相对):
        return io.open(os.path.join(ROOT, 相对), encoding="utf-8").read()

    def test_状态文件不许裸开写(self):
        """裸 open(path,"w") + json.dump 会把原文件先截断——原子写只有一个入口。"""
        禁 = {
            "boss_bot/self_evolve.py": ['with open(self._data_file, "w"',
                                        'with open(self._quality_data_file, "w"',
                                        'with open(self._EVOLVE_RESULT_FILE, "w"'],
            "boss_bot/unified_config.py": ['with open(BOT_CONFIG_FILE, "w"',
                                           'with open(OVERRIDES_FILE, "w"'],
            "flask-version/app.py": ['with open(BOT_CONFIG_FILE, "w"',
                                     'with open(OVERRIDES_FILE, "w"',
                                     'with open(USER_PROFILE_FILE, "w"',
                                     'with open(_NOTIFICATIONS_FILE, "w"'],
        }
        for 文件, 片段表 in 禁.items():
            源 = self._源(文件)
            for 片段 in 片段表:
                assert 片段 not in 源, f"{文件} 还在裸写：{片段}"

    def test_原子写真的用上了(self):
        assert "write_json_atomic" in self._源("boss_bot/self_evolve.py"), \
            "self_evolve 里一个原子写都没有，光删裸写等于没写"



if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
