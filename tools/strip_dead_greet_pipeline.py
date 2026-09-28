"""一次性删除 GreetEngine 中不可达的旧打招呼流水线（AST 精确行范围）。

用法: python tools/strip_dead_greet_pipeline.py [--apply]
默认只报告；带 --apply 才写文件。写之前先备份内存快照并做语法自检。
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GE = ROOT / "boss_bot" / "greet_engine.py"
LIVE_HOSTS = [ROOT / "boss_bot" / "main_loop.py", ROOT / "flask-version" / "app.py",
              ROOT / "boss_bot" / "reply_engine.py", ROOT / "boss_bot" / "page_handler.py"]
TESTS = list((ROOT / "tests").glob("*.py"))

apply_changes = "--apply" in sys.argv
src = GE.read_text(encoding="utf-8")
lines = src.splitlines(keepends=True)
tree = ast.parse(src)
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "GreetEngine")

funcs = {f.name: f for f in cls.body if isinstance(f, ast.FunctionDef)}
edges = {name: set() for name in funcs}
for name, fn in funcs.items():
    body = ast.get_source_segment(src, fn) or ""
    edges[name] = {c for c in re.findall(r"self\.(\w+)\s*\(", body) if c in funcs}

roots = set()
for host in LIVE_HOSTS + TESTS:
    if host.exists():
        text = host.read_text(encoding="utf-8", errors="replace")
        roots |= {u for u in re.findall(r"(?:_greet_engine|engine|ge|greet_engine)\.(\w+)", text)
                  if u in funcs}

reachable = set()
def expand(name):
    if name in reachable or name not in funcs:
        return
    reachable.add(name)
    for c in edges[name]:
        expand(c)
for r in roots:
    expand(r)
expand("__init__")

dead = sorted(set(funcs) - reachable)
ranges = []
for name in dead:
    fn = funcs[name]
    start = fn.decorator_list[0].lineno - 1 if fn.decorator_list else fn.lineno - 1
    while start > 0 and lines[start - 1].strip().startswith("@"):
        start -= 1
    ranges.append((start, fn.end_lineno, name))
ranges.sort()

print(f"待删方法 {len(dead)} 个，共 {sum(b - a for a, b, _ in ranges)} 行（含装饰器）")
for a, b, name in ranges:
    print(f"  {name}: {a+1}-{b}")

if not apply_changes:
    print("\n未写文件（加 --apply 执行）")
    sys.exit(0)

out = []
skip = set()
for a, b, _ in ranges:
    for i in range(a, b):
        skip.add(i)
for i, line in enumerate(lines):
    if i not in skip:
        out.append(line)
new = "".join(out)
new = re.sub(r"\n{4,}", "\n\n\n", new)
ast.parse(new)  # 语法自检，失败则不落盘
GE.write_text(new, encoding="utf-8")
subprocess.run([sys.executable, "-c", "import boss_bot.greet_engine"], cwd=ROOT, check=True)
print(f"已写入：{len(lines)} → {len(new.splitlines())} 行")
