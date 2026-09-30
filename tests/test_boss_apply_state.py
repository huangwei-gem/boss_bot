# tests/test_boss_apply_state.py
"""boss-apply/scripts/state.py 的行为锁。

用户数据必须在 skill 目录外：更新 skill 会整目录覆盖，数据混进去迟早丢。
BOSS_APPLY_HOME 让同一台机器上两个人各用各的号互不干扰。
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "boss-apply" / "scripts" / "state.py"


def load_state(monkeypatch, home):
    monkeypatch.setenv("BOSS_APPLY_HOME", str(home))
    spec = importlib.util.spec_from_file_location("boss_apply_state", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_默认落在boss_apply目录(monkeypatch, tmp_path):
    monkeypatch.delenv("BOSS_APPLY_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
    mod = load_state(monkeypatch, tmp_path / "fakehome" / ".boss-apply")
    assert mod.home() == tmp_path / "fakehome" / ".boss-apply"


def test_环境变量换目录(tmp_path, monkeypatch):
    other = tmp_path / "user2"
    mod = load_state(monkeypatch, other)
    assert mod.home() == other
    assert mod.state_dir() == other / "state"
    assert mod.state_dir().is_dir(), "取目录就要顺带建出来，别让调用方各自 mkdir"


def test_用户数据不许落在skill目录里():
    inside = (ROOT / "boss-apply" / "state").exists()
    assert not inside, "skill 目录里不能出现 state/，更新时会被整目录覆盖"
