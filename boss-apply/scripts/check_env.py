# boss-apply/scripts/check_env.py
# -*- coding: utf-8 -*-
"""前置自检：只检查，不修复，更不 launch 浏览器。

状态三档：OK 通过 / MISS 缺东西但能自己补（给出处置）/ FAIL 环境在但不通。
退出码 0 才允许开跑。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DOCTOR_TIMEOUT = 20


def home() -> Path:
    raw = os.environ.get("BOSS_APPLY_HOME") or str(Path.home() / ".boss-apply")
    return Path(raw).expanduser()


def _check(name, status, action):
    return {"name": name, "status": status, "action": action}


def run_checks() -> list:
    out = []
    if shutil.which("bsk"):
        out.append(_check("bsk_cli", "OK", ""))
        try:
            # Windows 默认 GBK，bsk 输出是 UTF-8：不显式给编码会把 stderr 炸成异常
            r = subprocess.run(["bsk", "doctor"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=DOCTOR_TIMEOUT)
        except Exception as e:                      # 超时/权限问题也算不通，但要说明是哪一种
            out.append(_check("bsk_doctor", "FAIL", f"bsk doctor 跑不起来：{e}"))
        else:
            if r.returncode == 0:
                out.append(_check("bsk_doctor", "OK", ""))
            else:
                why = (r.stderr or r.stdout or "未知输出").strip()[:200]
                out.append(_check("bsk_doctor", "FAIL",
                                  f"bsk doctor 报错，先在浏览器里加载 browser-skill 扩展并让弹窗变绿：{why}"))
    else:
        out.append(_check("bsk_cli", "MISS",
                          "没找到 bsk。装 browser-skill 的 CLI（Rust 版 bsk）并在 Chromium 里加载 "
                          "browser-skill 扩展，扩展弹窗显示绿色后再跑一次自检。本 skill 不会自己启动浏览器。"))

    try:
        h = home()
        h.mkdir(parents=True, exist_ok=True)
        probe = h / ".write-probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        out.append(_check("home_writable", "OK", ""))
    except OSError as e:
        out.append(_check("home_writable", "FAIL", f"{home()} 不可写：{e}"))

    if (home() / "rules.json").is_file():
        out.append(_check("rules", "OK", ""))
    else:
        out.append(_check("rules", "MISS",
                          f"没有 {home() / 'rules.json'}。把 skill 的 assets/rules.example.json "
                          f"复制过去再改，别改 skill 目录里的 example。"))
    return out


def exit_code(checks) -> int:
    return 0 if all(c["status"] == "OK" for c in checks) else 1


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    checks = run_checks()
    if "--json" in args:
        print(json.dumps({"ok": exit_code(checks) == 0, "checks": checks},
                         ensure_ascii=False, indent=1))
    else:
        for c in checks:
            line = f"{c['status']:4} {c['name']}"
            if c["action"]:
                line += f"：{c['action']}"
            print(line)
    return exit_code(checks)


if __name__ == "__main__":
    sys.exit(main())
