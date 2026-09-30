"""临时：用当前代码起一个只读配置副本的面板，跑完就收。"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import e2e_account_scope_ui as E  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="boss_panel_"))
proc = None
try:
    E.seed(tmp)
    proc = E.start_server(tmp, 5059)
    print("面板已起在", E.URL, "配置副本:", tmp, flush=True)
    rc = subprocess.call([sys.executable, "-X utf8", str(ROOT / "tools" / "e2e_dashboard.py")],
                         env=dict(__import__("os").environ,
                                  BOSS_PANEL_URL=E.URL,
                                  BOSS_CONFIG_FILE=str(tmp / "bot_config.json")),
                         cwd=str(ROOT))
    print("dashboard 退出码:", rc)
finally:
    if proc:
        proc.kill()
    shutil.rmtree(tmp, ignore_errors=True)
