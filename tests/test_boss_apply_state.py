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


def _fill(mod, n_sent, n_other=2, round_id="20260930-120000"):
    for i in range(n_sent):
        mod.record(round_id=round_id, url=f"u{i}", account="a", stage="greet",
                   failure_code="sent", job_name=f"j{i}", company="c")
    for i in range(n_other):
        mod.record(round_id=round_id, url=f"s{i}", account="a", stage="score",
                   failure_code="skipped_low_score", job_name="s", company="c")


def test_本轮已投条数只数发送成功(tmp_path, monkeypatch):
    mod = load_state(monkeypatch, tmp_path / "h")
    _fill(mod, 3, 5)
    assert mod.count_sent("20260930-120000") == 3


def test_汇总按failure_code分组(tmp_path, monkeypatch):
    mod = load_state(monkeypatch, tmp_path / "h")
    _fill(mod, 2, 3)
    s = mod.summary("20260930-120000")
    assert s["by_code"]["sent"] == 2
    assert s["by_code"]["skipped_low_score"] == 3
    assert s["total"] == 5, "total 含被跳过的，否则看不出「投得少是因为筛得严」"


def test_上限判定读的是文件而不是记忆(tmp_path, monkeypatch):
    """agent 上下文被压缩后会低估自己投了几个；count_sent 是唯一真相。"""
    mod = load_state(monkeypatch, tmp_path / "h")
    _fill(mod, 20)
    assert mod.remaining("20260930-120000", cap=25) == 5


def test_硬上限压过配置值(tmp_path, monkeypatch):
    mod = load_state(monkeypatch, tmp_path / "h")
    assert mod.remaining("R", cap=999, already=0) == 50, "HARD_CAP=50 是地板上的天花板"
