# tests/test_boss_apply_check_env.py
"""check_env.py 只许"检查"，不许"顺手修好"。

最要紧的一条：探不到 bsk 时不能自己去 launch 浏览器 —— 那是约束①禁止的事，
也是把使用者推进"技能偷偷装了个驱动"的坑。
"""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "boss-apply" / "scripts" / "check_env.py"


def load_mod(monkeypatch, home):
    monkeypatch.setenv("BOSS_APPLY_HOME", str(home))
    spec = importlib.util.spec_from_file_location("check_env", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class 起了真命令(BaseException):
    """故意不接 `Exception`。

    check_env 里那句 `except Exception as e` 会把 AssertionError 咽成一行 FAIL 判据，
    测试看着还是绿的——这一条要炸得让它接不住，才知道自己漏了打桩。
    """


@pytest.fixture(autouse=True)
def 不许真起外部命令(monkeypatch):
    """这一支只管判据，不许真去跑 bsk。

    2026-10-26 全量回归里这个文件卡满 300 秒被记成 HANG：`test_没配置时指路example复制`
    两样都没打桩，机器上装着 bsk 就真起了 `bsk doctor`。那是个 npm CLI，会派孙进程，
    Windows 上 `subprocess.run(timeout=)` 超时 kill 完还要 `communicate()`，
    管道被孙进程攥着就再也不返回——DOCTOR_TIMEOUT=20 也拦不住这一种。
    """
    def 拦(*a, **k):
        raise 起了真命令("测试里不许真起外部命令: %r" % (a[:1],))
    monkeypatch.setattr(subprocess, "run", 拦)
    yield


def test_bsk不在就判MISS并给出处置(tmp_path, monkeypatch):
    mod = load_mod(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    got = [c for c in mod.run_checks() if c["name"] == "bsk_cli"][0]
    assert got["status"] == "MISS"
    assert "browser-skill" in got["action"], got      # 要能照着做，不能只说"没有"


def test_doctor不通判FAIL(tmp_path, monkeypatch):
    mod = load_mod(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "bsk")
    class R:
        returncode, stdout, stderr = 1, "", "no extension connected"
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: R())
    got = [c for c in mod.run_checks() if c["name"] == "bsk_doctor"][0]
    assert got["status"] == "FAIL"
    assert "no extension connected" in got["action"]


def test_没配置时指路example复制(tmp_path, monkeypatch):
    mod = load_mod(monkeypatch, tmp_path)
    # 这一支只管 rules 那一项；不打桩 which 就会真起一次 bsk（见上面那个 fixture）
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    got = [c for c in mod.run_checks() if c["name"] == "rules"][0]
    assert got["status"] == "MISS"
    assert "rules.example.json" in got["action"]


def test_真起外部命令会被当场拦住(tmp_path, monkeypatch):
    """门本身要能红：装了 bsk 的机器上漏打桩，得当场炸而不是等 300 秒超时"""
    mod = load_mod(monkeypatch, tmp_path)
    monkeypatch.setattr(mod.shutil, "which", lambda _: "bsk")
    with pytest.raises(起了真命令):
        mod.run_checks()


def test_目录可写且有配置时整体通过(tmp_path, monkeypatch):
    mod = load_mod(monkeypatch, tmp_path)
    (tmp_path / "rules.json").write_text('{"cities":["上海"]}', encoding="utf-8")
    monkeypatch.setattr(mod.shutil, "which", lambda _: "bsk")
    class R:
        returncode, stdout, stderr = 0, "ok", ""
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: R())
    checks = mod.run_checks()
    assert all(c["status"] == "OK" for c in checks), checks
    assert mod.exit_code(checks) == 0


def test_脚本里不许出现启动浏览器的动作():
    src = SCRIPT.read_text(encoding="utf-8")
    for banned in ("ChromiumOptions", "DrissionPage", "selenium", "playwright",
                   "subprocess.Popen"):
        assert banned not in src, banned


def test_bsk输出必须显式utf8解码():
    """Windows 默认 GBK：bsk 的 UTF-8 输出会被 reader 线程 UnicodeDecodeError 吃掉。"""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'encoding="utf-8"' in src, "subprocess.run 必须显式 encoding=utf-8"
