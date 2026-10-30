"""README 里写的跑法是 `python tests/e2e_xxx.py`（不带 -m）。

这些脚本以前在补 ROOT 进 sys.path 之前就 `from tests.conftest import ...`，
照文档跑会直接 ModuleNotFoundError: No module named 'tests.conftest'——
2026-10-29 跑全流程时踩了两次才改成 -m。这里锁住顺序，改回去就红。
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
脚本 = sorted((ROOT / "tests").glob("e2e_*.py")) + [
    ROOT / "tests" / "run_dashboard_on_temp_panel.py",
]


def 查顺序(源码: str) -> list:
    位置 = 源码.find("from tests.conftest import")
    if 位置 < 0:
        return []
    if not re.search(r"sys\.path\.insert\(0,\s*str\(", 源码[:位置]):
        return ["先 import tests.conftest，再补 ROOT 进 sys.path"]
    return []


def test_每个脚本照文档直接跑都能导到_tests_conftest():
    assert len(脚本) >= 13, f"只找到 {len(脚本)} 个脚本，glob 是不是坏了"
    for p in 脚本:
        问题 = 查顺序(p.read_text(encoding="utf-8"))
        assert not 问题, f"{p.name}: {问题}"


def test_这个门能失败():
    坏样本 = (
        "import sys\n"
        "from tests.conftest import x\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))\n"
    )
    assert 查顺序(坏样本), "反序的样本没被查出来，这门是假的"
