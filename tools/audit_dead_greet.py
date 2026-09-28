"""死代码核查：用 AST 证明 GreetEngine 里哪些方法从不可达入口出发。

不修改任何文件，只输出结论。判据：
  1. 外部引用表 = main_loop.py / flask-version/app.py / tests/ 里出现的 engine.<name>
  2. 内部调用图 = greet_engine.py 里 self.<name>( 的调用边
  3. 从"活根"（外部引用 + 模块级实例化）做可达性遍历，遍历不到的一律死码
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GE = ROOT / "boss_bot" / "greet_engine.py"
LIVE_HOSTS = [ROOT / "boss_bot" / "main_loop.py", ROOT / "flask-version" / "app.py",
              ROOT / "boss_bot" / "reply_engine.py", ROOT / "boss_bot" / "page_handler.py"]
TESTS = list((ROOT / "tests").glob("*.py"))

src = GE.read_text(encoding="utf-8")
tree = ast.parse(src)
cls = next(n for n in tree.body
           if isinstance(n, ast.ClassDef) and n.name == "GreetEngine")

methods = {n.name: (n.lineno, n.end_lineno) for n in cls.body if isinstance(n, ast.FunctionDef)}

# 内部调用边：self.<method>( 以及 <Class>.<method> / 直接函数名
edges = {name: set() for name in methods}
for fn in cls.body:
    if not isinstance(fn, ast.FunctionDef):
        continue
    body = ast.get_source_segment(src, fn) or ""
    for callee in set(re.findall(r"self\.(\w+)\s*\(", body)):
        if callee in methods:
            edges[fn.name].add(callee)

live_roots = set()
for host in LIVE_HOSTS + TESTS:
    if not host.exists():
        continue
    text = host.read_text(encoding="utf-8", errors="replace")
    for used in set(re.findall(r"(?:_greet_engine|engine|ge|greet_engine)\.(\w+)", text)):
        if used in methods:
            live_roots.add(used)

# 模块级/类级直接调用的函数（非方法）不参与；构造器链单独处理
reachable = set()

def expand(name):
    if name in reachable or name not in methods:
        return
    reachable.add(name)
    for c in edges.get(name, ()):
        expand(c)

for r in sorted(live_roots):
    expand(r)
expand("__init__")

dead = sorted(set(methods) - reachable)
print(f"方法总数 {len(methods)} | 活根 {len(live_roots)} | 可达 {len(reachable)} | 死码 {len(dead)}")
print("\n活根: " + ", ".join(sorted(live_roots)))
print("\n不可达方法:")
for d in dead:
    lo, hi = methods[d]
    print(f"  {d}: {lo}-{hi} ({hi - lo + 1} 行)")
print("\n死码总行数:", sum(methods[d][1] - methods[d][0] + 1 for d in dead))
print("\n内部调用边（仅展示死码之间）:")
for d in dead:
    if edges[d] & set(dead):
        print(f"  {d} -> {sorted(edges[d] & set(dead))}")
sys.exit(0)
