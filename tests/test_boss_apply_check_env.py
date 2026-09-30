# tests/test_boss_apply_check_env.py
"""check_env.py 只许"检查"，不许"顺手修好"。

最要紧的一条：探不到 bsk 时不能自己去 launch 浏览器 —— 那是约束①禁止的事，
也是把使用者推进"技能偷偷装了个驱动"的坑。
"""
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "boss-apply" / "scripts" / "check_env.py"


def load_mod(monkeypatch, home):
    monkeypatch.setenv("BOSS_APPLY_HOME", str(home))
    spec = importlib.util.spec_from_file_location("check_env", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
    got = [c for c in mod.run_checks() if c["name"] == "rules"][0]
    assert got["status"] == "MISS"
    assert "rules.example.json" in got["action"]


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
