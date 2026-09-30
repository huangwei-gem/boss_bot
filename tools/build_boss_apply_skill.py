# -*- coding: utf-8 -*-
"""把 boss-apply/ 打成 .skill（zip）。

上游 skill-creator 的 scripts/package_skill 在部分机器上不可用时用这个：
产物形状一致（zip，顶层是 <skill 名>/…），只依赖标准库。
"""
import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "boss-apply"
SKIP_DIRS = {"__pycache__", ".pytest_cache", "state", "logs"}


def build(out: Path) -> list:
    out.parent.mkdir(parents=True, exist_ok=True)
    written = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PKG.rglob("*")):
            if not p.is_file():
                continue
            if SKIP_DIRS & set(p.relative_to(PKG).parts[:-1]):
                continue
            if p.suffix in {".pyc", ".log"} or p.name == "rules.json":
                continue          # 用户数据与字节码不进包
            arc = str(PKG.relative_to(ROOT) / p.relative_to(PKG)).replace("\\", "/")
            z.write(p, arc)
            written.append(arc)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist" / "boss-apply.skill"))
    a = ap.parse_args()
    files = build(Path(a.out))
    print(f"打包 {len(files)} 个文件 → {a.out}")
    for f in files:
        print("  ", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
