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


def test_发送成功才算投过(tmp_path, monkeypatch):
    mod = load_state(monkeypatch, tmp_path / "h")
    url = "https://www.zhipin.com/job_detail/abc.html"
    mod.record(round_id="R1", url=url, account="主账号", stage="greet",
               failure_code="sent", job_name="数据分析师", company="某公司")
    assert mod.seen(url, "主账号") is True


def test_被跳过不算投过(tmp_path, monkeypatch):
    """不匹配/读不到 JD 的岗位下一轮还要能再判一次，不能被去重表永久吃掉。"""
    mod = load_state(monkeypatch, tmp_path / "h")
    url = "https://www.zhipin.com/job_detail/skip.html"
    mod.record(round_id="R1", url=url, account="主账号", stage="score",
               failure_code="skipped_low_score", job_name="外包岗", company="X")
    assert mod.seen(url, "主账号") is False


def test_换号不共用去重表(tmp_path, monkeypatch):
    """同一个岗位两个号都投，HR 会收到两条一样的招呼语；跨号也要挡住。"""
    mod = load_state(monkeypatch, tmp_path / "h")
    url = "https://www.zhipin.com/job_detail/shared.html"
    mod.record(round_id="R1", url=url, account="主账号", stage="greet",
               failure_code="sent", job_name="数据分析", company="X")
    assert mod.seen(url, "账号2") is True, "同一浏览器 profile 下两个号看到的是同一个 HR"


def test_记录落在rounds文件里按月份分文件(tmp_path, monkeypatch):
    mod = load_state(monkeypatch, tmp_path / "h")
    mod.record(round_id="20260930-120000", url="u", account="a", stage="greet",
               failure_code="sent", job_name="n", company="c")
    files = sorted(p.name for p in (tmp_path / "h" / "state").iterdir())
    assert "rounds-2026-09.json" in files, files
