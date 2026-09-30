# boss-apply 投递闭环（子项目①）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出一个可分发的纯 skill `boss-apply/`，让"装了 browser-skill + 浏览器里登录着 BOSS 直聘"的人，对它说"帮我投 30 个"就能完成 搜岗位 → 读 JD → 判分 → 打招呼 → 落去重记录 的一轮闭环。

**Architecture:** 四层各一个接口：浏览器控制只走 `bsk` 命令（不写任何驱动）；判分由跑 skill 的 agent 自己完成（无外部 key）；状态由 `scripts/state.py` 读写 `~/.boss-apply/state/` 的 JSON；编排是 `SKILL.md` 里的步骤文本。本仓库只负责把这三样东西做对并可验证，真机那一遍由装了 bsk 的使用者跑。

**Tech Stack:** Python 3.11+ 标准库（脚本，零第三方依赖）、pytest、Markdown（SKILL.md + references）、`bsk` CLI（browser-skill，只作为被调用的外部工具，不在本仓库安装或测试）。

**Spec:** `docs/superpowers/specs/2026-09-30-boss-skill-apply-design.md`

---

## File Structure

| 文件 | 责任 | 新建/修改 |
|------|------|-----------|
| `boss-apply/scripts/state.py` | 记录、URL 去重、本轮计数与汇总（唯一写用户数据的入口） | Create |
| `boss-apply/scripts/check_env.py` | 前置自检：bsk 可用性、目录可写、配置存在；绝不 launch 浏览器 | Create |
| `boss-apply/SKILL.md` | 编排文本：何时用 / 自检 / 一轮生命周期 / 四条红线 / 失败归因入口 | Create |
| `boss-apply/references/boss-dom.md` | 真机核过的选择器与两套打招呼机制现场（与生产实现同源，有测试锁住） | Create |
| `boss-apply/references/failure-codes.md` | 每个 failure_code 的可观测判据与建议动作 | Create |
| `boss-apply/references/rules-format.md` | `rules.json` 字段说明与示例 | Create |
| `boss-apply/assets/rules.example.json` `greeting.example.txt` | 配置模板（skill 里只放 example，不放用户数据） | Create |
| `tests/test_boss_apply_state.py` | state.py 行为 | Create |
| `tests/test_boss_apply_check_env.py` | check_env.py 行为 | Create |
| `tests/test_boss_apply_package.py` | 包结构 / frontmatter 白名单 / 行数上限 / 无驱动依赖 / 文档同源 | Create |

**提交节奏：** 每个 Task 末尾一次 commit。用户要求过：commit 可以，push 必须他单独点头 —— 全程不 push。

---

## Task 1: state.py 的目录与 home 覆盖

**Files:**
- Create: `boss-apply/scripts/state.py`
- Test: `tests/test_boss_apply_state.py`

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_state.py -q`
Expected: FAIL / collection error — `FileNotFoundError` 或 `SCRIPT` 不存在（`state.py` 还没写）

- [ ] **Step 3: 写最小实现**

```python
# boss-apply/scripts/state.py
# -*- coding: utf-8 -*-
"""boss-apply 的记录 / 去重 / 计数。纯标准库，绝不联网、绝不碰浏览器。

为什么上限计数在这里而不是 agent 的上下文里：一轮跑到第 20 个岗位时上下文可能
已经被压缩，agent"以为"自己只投了 3 个，而多投的代价是骚扰 HR。数错方向只能是
"少投"，不能是"多投"。
"""
import os
from pathlib import Path


def home() -> Path:
    """使用者的一切都在这里，skill 目录只放代码与 example。"""
    raw = os.environ.get("BOSS_APPLY_HOME") or str(Path.home() / ".boss-apply")
    return Path(raw).expanduser()


def state_dir() -> Path:
    d = home() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_state.py -q`
Expected: `3 passed`

- [ ] **Step 5: 提交**

```bash
git add boss-apply/scripts/state.py tests/test_boss_apply_state.py
git commit -m "feat(boss-apply): 状态目录与 BOSS_APPLY_HOME 隔离"
```

---

## Task 2: state.py 的落库与 URL 去重

**Files:**
- Modify: `boss-apply/scripts/state.py`
- Test: `tests/test_boss_apply_state.py`

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_state.py -q -k "发送成功 or 被跳过 or 换号 or rounds"`
Expected: FAIL — `AttributeError: module 'boss_apply_state' has no attribute 'record'`

- [ ] **Step 3: 写实现**

追加到 `boss-apply/scripts/state.py`：

```python
import json
import time

SENT_CODE = "sent"
ROUND_KEEP_SECONDS = 3 * 86400      # rounds 文件保留 3 天，够跨会话查"这个岗位投过没"


def _rounds_file(round_id: str) -> Path:
    """round_id 形如 20260930-120000；按月分文件，一年 12 个文件而不是一个巨型 JSON。"""
    stamp = (round_id or "")[:6]
    month = f"{stamp[:4]}-{stamp[4:6]}" if len(stamp) == 6 else time.strftime("%Y-%m")
    return state_dir() / f"rounds-{month}.json"


def chatted_file() -> Path:
    return state_dir() / "chatted.json"


def _load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError):
        # 坏文件不能静默当没有：那样会把"已经投过"的岗位重新投一遍
        raise


def _key(url: str, account: str) -> str:
    # account 不参与键：去重的对象是"岗位 + HR"，同一个人换个号再发一遍也是骚扰。
    # 账号信息仍然记在值里，便于排查是谁投的。
    return (url or "").strip()


def record(round_id: str, url: str, account: str, stage: str, failure_code: str,
           job_name: str = "", company: str = "", detail: str = "",
           score=None, duration_ms=None) -> dict:
    """一个岗位一条记录。发送成功即落库，不等一轮跑完再写。"""
    row = {"round": round_id, "url": url, "account": account, "stage": stage,
           "failure_code": failure_code, "job_name": job_name, "company": company,
           "detail": detail, "score": score, "duration_ms": duration_ms,
           "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    rows = _load_json(_rounds_file(round_id), [])
    rows.append(row)
    _dump(_rounds_file(round_id), rows)
    if failure_code == SENT_CODE:
        chatted = _load_json(chatted_file(), {})
        chatted[_key(url, account)] = row
        _dump(chatted_file(), chatted)
    return row


def _dump(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)          # 半截文件比崩溃更难查，写盘一律原子替换


def seen(url: str, account: str = "") -> bool:
    return _key(url, account) in _load_json(chatted_file(), {})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_state.py -q`
Expected: `7 passed`

- [ ] **Step 5: 提交**

```bash
git add boss-apply/scripts/state.py tests/test_boss_apply_state.py
git commit -m "feat(boss-apply): 岗位记录与跨号去重（发送即落库、原子写盘）"
```

---

## Task 3: state.py 的本轮计数与汇总（上限判定不靠 agent 记忆）

**Files:**
- Modify: `boss-apply/scripts/state.py`
- Test: `tests/test_boss_apply_state.py`

- [ ] **Step 1: 写失败测试**

```python
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
    assert s["total"] == 5, "total 含被跳过的，否则看不出"投得少是因为筛得严""


def test_上限判定读的是文件而不是记忆(tmp_path, monkeypatch):
    """agent 上下文被压缩后会低估自己投了几个；count_sent 是唯一真相。"""
    mod = load_state(monkeypatch, tmp_path / "h")
    _fill(mod, 20)
    assert mod.remaining("20260930-120000", cap=25) == 5


def test_硬上限压过配置值(tmp_path, monkeypatch):
    mod = load_state(monkeypatch, tmp_path / "h")
    assert mod.remaining("R", cap=999, already=0) == 50, "HARD_CAP=50 是地板上的天花板"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_state.py -q -k "本轮 or 汇总 or 上限 or 硬上限"`
Expected: FAIL — `AttributeError: ... has no attribute 'count_sent'`

- [ ] **Step 3: 写实现**

```python
HARD_CAP = 50        # skill 内置天花板：BOSS 自己给单账号 150/天，不该把人往那个数推
DEFAULT_CAP = 20     # 每轮默认上限，可在 rules.json 调，但不能超过 HARD_CAP


def _rows_of(round_id: str):
    out = []
    for path in sorted(state_dir().glob("rounds-*.json")):
        for row in _load_json(path, []):
            if row.get("round") == round_id:
                out.append(row)
    return out


def count_sent(round_id: str) -> int:
    return sum(1 for r in _rows_of(round_id) if r.get("failure_code") == SENT_CODE)


def summary(round_id: str) -> dict:
    rows = _rows_of(round_id)
    by_code = {}
    for r in rows:
        code = str(r.get("failure_code") or "unknown")
        by_code[code] = by_code.get(code, 0) + 1
    return {"round": round_id, "total": len(rows), "by_code": by_code,
            "sent": by_code.get(SENT_CODE, 0)}


def remaining(round_id: str, cap: int, already=None) -> int:
    """还能投几个。cap 越界时夹到 HARD_CAP，而不是报错——报错会被忽略，夹住不会。"""
    try:
        cap_n = int(cap)
    except (TypeError, ValueError):
        cap_n = DEFAULT_CAP
    cap_n = max(0, min(HARD_CAP, cap_n))
    used = count_sent(round_id) if already is None else int(already)
    return max(0, cap_n - used)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_state.py -q`
Expected: `11 passed`

- [ ] **Step 5: 加命令行入口（agent 用 subprocess 或直接跑）**

```python
def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="state.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seen"); s.add_argument("--url", required=True)
    s.add_argument("--account", default="")
    r = sub.add_parser("record"); r.add_argument("--json", required=True)
    c = sub.add_parser("count"); c.add_argument("--round", required=True)
    m = sub.add_parser("summary"); m.add_argument("--round", required=True)
    a = p.parse_args(argv)
    if a.cmd == "seen":
        return 0 if seen(a.url, a.account) else 1
    if a.cmd == "record":
        row = record(**json.loads(a.json))
        print(json.dumps(row, ensure_ascii=False))
        return 0
    if a.cmd == "count":
        print(count_sent(a.round)); return 0
    print(json.dumps(summary(a.round), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

同时在文件头补 `import sys`（`main()` 用到）。

- [ ] **Step 6: 手工验一次命令行契约**

Run:
```bash
BOSS_APPLY_HOME=/tmp/ba1 python boss-apply/scripts/state.py record --json '{"round_id":"20260930-120000","url":"u1","account":"主账号","stage":"greet","failure_code":"sent","job_name":"数据分析","company":"X"}'
BOSS_APPLY_HOME=/tmp/ba1 python boss-apply/scripts/state.py count --round 20260930-120000
BOSS_APPLY_HOME=/tmp/ba1 python boss-apply/scripts/state.py summary --round 20260930-120000
BOSS_APPLY_HOME=/tmp/ba1 python boss-apply/scripts/state.py seen --url u1; echo "exit=$?"
```
Expected: 依次输出记录 JSON、`1`、含 `"by_code": {"sent": 1}` 的汇总、`exit=0`

- [ ] **Step 7: 提交**

```bash
git add boss-apply/scripts/state.py tests/test_boss_apply_state.py
git commit -m "feat(boss-apply): 本轮计数/汇总与 CLI 入口，上限判定不依赖 agent 记忆"
```

---

## Task 4: check_env.py 前置自检（不通就停手，绝不自己起浏览器）

**Files:**
- Create: `boss-apply/scripts/check_env.py`
- Test: `tests/test_boss_apply_check_env.py`

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_check_env.py -q`
Expected: FAIL — 文件不存在

- [ ] **Step 3: 写实现**

```python
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
            r = subprocess.run(["bsk", "doctor"], capture_output=True, text=True,
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_check_env.py -q`
Expected: `5 passed`

- [ ] **Step 5: 本机跑一次（这台机器没有 bsk，应该判 MISS 而不是崩）**

Run: `python boss-apply/scripts/check_env.py; echo "exit=$?"`
Expected: 首行 `MISS bsk_cli：没找到 bsk…`，`exit=1`

- [ ] **Step 6: 提交**

```bash
git add boss-apply/scripts/check_env.py tests/test_boss_apply_check_env.py
git commit -m "feat(boss-apply): 前置自检脚本，缺 bsk 只报处置不自己起浏览器"
```

---

## Task 5: 配置模板与字段说明

**Files:**
- Create: `boss-apply/assets/rules.example.json`, `boss-apply/assets/greeting.example.txt`, `boss-apply/references/rules-format.md`
- Test: `tests/test_boss_apply_package.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_boss_apply_package.py
"""包结构、frontmatter、文档同源性 —— skill 能不能被别人导入，全看这些。"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "boss-apply"


def test_example配置能解析且字段齐全():
    data = json.loads((PKG / "assets" / "rules.example.json").read_text(encoding="utf-8"))
    for key in ("cities", "keywords", "match_threshold", "greeting",
                "interval_seconds", "round_cap"):
        assert key in data, key
    assert data["round_cap"] <= 50, "example 自己就该在硬上限内，别教坏使用者"
    assert data["interval_seconds"][0] >= 5, "间隔低于 5 秒就是往风控上撞"


def test_招呼语example不含占位残留():
    text = (PKG / "assets" / "greeting.example.txt").read_text(encoding="utf-8")
    assert text.strip() and "TODO" not in text.upper()
```

文件头里的 `ast`、`sys` 本任务还不会用到，别当无用 import 删掉：Task 7 的失败码锁用
`ast` 解析 `state.py` 的 `FAILURE_CODES`，Task 9 的打包锁用 `sys.executable` 跑打包脚本。
后面所有任务都往这一个文件里追加用例，不再改文件头。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_package.py -q`
Expected: FAIL — 文件不存在

- [ ] **Step 3: 写模板**

`boss-apply/assets/rules.example.json`：

```json
{
  "cities": ["长沙"],
  "keywords": ["数据分析"],
  "match_threshold": 70,
  "probe_band": 15,
  "greeting": "您好，我是应届本科，应聘数据分析岗位，掌握 Excel、SQL 与 Python，学习能力强，希望有机会沟通。",
  "interval_seconds": [6, 14],
  "round_cap": 20,
  "screens_per_keyword": 3
}
```

`boss-apply/assets/greeting.example.txt`：

```
您好，我是应届本科，应聘数据分析岗位。在校系统学习数据分析方法，掌握 Excel、基础 SQL 与 Python，
具备数据思维和工作细致度，学习能力强，踏实肯干，希望有机会进一步沟通。
```

`boss-apply/references/rules-format.md`：

```markdown
# rules.json 字段

放在 `~/.boss-apply/rules.json`（或 `BOSS_APPLY_HOME` 指向的目录）。skill 目录里只有 example。

| 字段 | 含义 | 边界 |
|------|------|------|
| `cities` | 搜索城市，逐个跑 | 空数组=自检就判 rules 不通 |
| `keywords` | 搜索关键词 | 一轮里逐个关键词串行跑，不并发 |
| `match_threshold` | agent 判分通过线（0-100） | 低于此值不打招呼 |
| `probe_band` | 边界带宽度：只追问 `阈值-band ≤ score < 阈值` | 0 = 关掉追问 |
| `greeting` | 招呼语文案（每轮固定一条） | 空字符串时 skill 必须停下来问，不能拿默认文案替使用者决定 |
| `interval_seconds` | 两次打招呼之间的随机间隔 `[min,max]` | min<5 会被抬到 5，并说明为什么 |
| `round_cap` | 本轮最多发多少条招呼 | 硬上限 50，超过按 50 处理并说明 |
| `screens_per_keyword` | 每个关键词翻几页 | 建议 ≤5，翻得深了列表质量明显下降 |

多人各用各的：换 `BOSS_APPLY_HOME` 就换一整套配置与记录。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_package.py -q`
Expected: `2 passed`

- [ ] **Step 5: 提交**

```bash
git add boss-apply/assets boss-apply/references/rules-format.md tests/test_boss_apply_package.py
git commit -m "feat(boss-apply): rules 模板与字段说明（含频率与上限边界）"
```

---

## Task 6: boss-dom.md 与生产实现同源（防漂移的锁）

**Files:**
- Create: `boss-apply/references/boss-dom.md`
- Test: `tests/test_boss_apply_package.py`

- [ ] **Step 1: 写失败测试**

```python
def test_dom文档里每个选择器都能在生产实现里找到出处():
    """skill 里的 DOM 事实必须与本仓库真机核过的生产代码同源。

    比两个文件：打招呼/输入框/发送按钮的事实来自 greet_engine.py，会话条目
    `.friend-content` 只存在于 page_handler.py（回复侧），少一个就会误报。
    生产改了选择器而文档没跟着改时，测试就红 —— 以生产代码为准改文档。
    """
    doc = (PKG / "references" / "boss-dom.md").read_text(encoding="utf-8")
    src = "\n".join((ROOT / "boss_bot" / name).read_text(encoding="utf-8")
                    for name in ("greet_engine.py", "page_handler.py"))
    listed = [sel for line in doc.splitlines()
              if line.lstrip().startswith("- 选择器 ")
              for sel in re.findall(r"`([^`]+)`", line)]
    assert len(listed) >= 12, f"文档没按约定列选择器，只找到 {len(listed)} 条"
    for sel in listed:
        assert sel in src, f"boss-dom.md 写了 {sel}，生产代码里已经没有它了"
```

两个细节别改回去：取的是 `- 选择器` 行里**每一个**反引号片段（一行列三个选择器是本文件的主要
写法，只比第一个的话同行后两个就成了没锁的口头话）；`lstrip()` 是为了让缩进在二级列表里的
`- 选择器` 行同样受锁。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_package.py -q -k 选择器`
Expected: FAIL — 文件不存在

- [ ] **Step 3: 写文档**

```markdown
# BOSS 直聘页面现场（本仓库真机核过，2026-09-29/30）

本文件两种写法，含义不一样：

- `- 选择器 \`xxx\`` 开头的行 = xxx 在本仓库生产代码（`boss_bot/greet_engine.py`、
  `boss_bot/page_handler.py`）里逐字出现过，有测试逐条比对；生产改了这里没跟着改，测试会红。
- 写在正文里的选择器 = 只有真机观察，生产代码并不依赖它（靠按钮文本兜底），**不参与比对**。

别把正文里的选择器擅自升级成锁，也别为了让测试过去把锁降成正文。

## 岗位列表页（/web/geek/job-recommend 与搜索结果页）

- 选择器 `.job-card-wrap` — 岗位卡片容器；取不到时退回 `.job-card-wrapper`
- 卡片里的岗位名/公司/薪资/地点是平铺文本，岗位链接在 `a[href*="job_detail"]`
  （生产代码不拿它当选择器，用的是 `"job_detail" in url` 这类判断）
- 新列表页**卡片上没有**沟通按钮：「立即沟通」在右侧详情栏（观察到的类名
  `A.op-btn.op-btn-chat`），必须先点卡片把详情打开再找按钮；生产代码找它靠的是
  `.btn btn-startchat` 加文本兜底，所以这条不锁
- 老式详情页 `/job_detail/*.html` 仍直接暴露沟通按钮，类名里有
  `btn-startchat`（CSS 等价写法 `a.btn.btn-startchat`）

## 打招呼的两套机制

1. **抽屉式**：点「立即沟通」→ 页面内出现输入框 → 填文案 → 点发送
   - 选择器 `#chat-input`、`.chat-input`、`.input-area`
   - 选择器 `textarea[placeholder*="回复"]`、`textarea[placeholder*="输入"]`
   - 选择器 `[contenteditable=true]`
   - 选择器 `.btn-send`、`.btn-v2.btn-sure-v2.btn-send`、`.send-message`、`.chat-send`
2. **平台自动发**：点「立即沟通」后弹出「已向BOSS发送消息 / 留在此页 / 继续沟通」，
   页面里没有可输入的抽屉
   - 该点的是「继续沟通」；**「留在此页」是什么都不做，不能点**
   - 点完会话可能开在当前页抽屉里，也可能新开一个标签页
   - 选择器 `.message-item.item-myself`、`.text-content` — 核对是否真发出去只认这两个：
     我方气泡里的这段文本才是"我发出去的"，对侧气泡是 item-friend，不算
   - 若气泡文本与本账号文案不一致，说明平台发的是它自己的预设 → 需要补发本账号文案
   - 读不到消息列表时**不许盲发**（宁可少发也不能重复骚扰）

## 会话页 / 已投递判定

- 选择器 `.friend-content` — 会话条目（左侧列表 40 条一屏）
- 按钮文案出现「继续沟通」= 该岗位之前已经投递过，要跳过
- SPA 换页后 `@eN` 引用全部失效：每次导航/开弹窗之后必须重新 observe/snapshot

## 页面会骗人，URL 也会

- 未登录时 `/web/geek/chat` 会**先渲染再跳** `/web/user`：看一眼 URL 就下结论必错，
  要等 URL 稳定（连读两次一致）
- 落在登录页但浏览器里仍有未过期登录项 = 两路证据矛盾，判"看不准"，不能判"已过期"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_package.py -q -k 选择器`
Expected: `1 passed`（锁到 15 条：列表页 2 + 抽屉输入框/发送按钮 10 + 气泡 2 + 会话条目 1）。
若 FAIL，先分清是哪种：某条选择器在生产代码里真被换掉了 → 以 `greet_engine.py`/`page_handler.py`
为准改文档；只是把正文观察（`A.op-btn.op-btn-chat` 这类）误写成了 `- 选择器` 行 → 改回正文。
两种都不要反过来改生产代码。

- [ ] **Step 5: 提交**

```bash
git add boss-apply/references/boss-dom.md tests/test_boss_apply_package.py
git commit -m "feat(boss-apply): DOM 事实文档，与生产实现同源并加防漂移测试"
```

---

## Task 7: failure-codes.md 与 state.py 的枚举对齐

**Files:**
- Create: `boss-apply/references/failure-codes.md`
- Test: `tests/test_boss_apply_package.py`

- [ ] **Step 1: 写失败测试**

```python
def test_失败码文档与state的枚举完全一致():
    """文档里有代码没的码 → agent 记进去没人认得；代码有文档没的码 → 使用者查不到处置。"""
    doc = (PKG / "references" / "failure-codes.md").read_text(encoding="utf-8")
    doc_codes = set(re.findall(r"^\|\s*`([a-z_]+)`", doc, flags=re.M))
    src = (PKG / "scripts" / "state.py").read_text(encoding="utf-8")
    m = re.search(r"FAILURE_CODES = \(([^)]*)\)", src, flags=re.M)
    assert m, "state.py 里没声明 FAILURE_CODES 枚举"
    codes = {x.value for x in ast.parse(f"X=({m.group(1)})").body[0].value.elts}
    assert codes, "FAILURE_CODES 是空的"
    assert doc_codes == codes, f"文档多集 {doc_codes - codes} / 代码多集 {codes - doc_codes}"
    for code in sorted(codes):
        rows = [l for l in doc.splitlines() if l.strip().startswith(f"| `{code}`")]
        assert len(rows) == 1, f"{code} 在表里出现 {len(rows)} 次"
        cells = [c.strip() for c in rows[0].strip().strip("|").split("|")]
        assert len(cells) == 3 and all(len(c) > 3 for c in cells), \
            f"{code} 那一行三格没写满：{cells}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_package.py -q -k 失败码`
Expected: FAIL — 文档不存在，且 `state.py` 还没有 `FAILURE_CODES`

- [ ] **Step 3: 给 state.py 补枚举常量**

在 `boss-apply/scripts/state.py` 的 `SENT_CODE = "sent"` 上方加：

```python
FAILURE_CODES = (
    "sent", "skipped_low_score", "already_chatted", "no_chat_button",
    "auto_greet_dialog", "jd_unreadable", "not_logged_in", "wind_control",
    "interval_blocked", "error",
)
```

- [ ] **Step 4: 写文档**

```markdown
# 失败归因表

每条记录都带一个 `failure_code`。`state.py summary --round <id>` 按它分组，
一轮结束要把这张表里的"处置"念给人听，而不是只说"失败了"。

| code | 判据（可观测） | 处置 |
|------|----------------|------|
| `sent` | 自己的消息气泡 `.message-item.item-myself` 里出现了本轮招呼语文案 | 记成功，URL 进 `chatted.json` |
| `skipped_low_score` | agent 判分 `< match_threshold` | 不打招呼，不进去重表（下一轮可再判） |
| `already_chatted` | URL 命中 `chatted.json`，或按钮文案是「继续沟通」 | 跳过，避免同一 HR 收到两条一样的招呼 |
| `no_chat_button` | 点开卡片后右侧详情栏取不到「立即沟通」（观察类名 `A.op-btn.op-btn-chat`），老式详情页类名里也没有 `btn-startchat` | 落记录并跳过；连续 3 个都这样就该停下来怀疑页面变了 |
| `auto_greet_dialog` | 出现「已向BOSS发送消息 / 留在此页 / 继续沟通」弹窗 | 点「继续沟通」进会话核对；气泡文案不是本账号文案时补发，读不到列表就不许发 |
| `jd_unreadable` | 详情页任职要求取不到 | 不判分、不打招呼。宁可不投也不能盲投 |
| `not_logged_in` | 稳定后的 URL 落在 `/web/user` 或 `passport.`，**且**浏览器里没有未过期登录项 | 立刻停本轮，`bsk request-help` 交人工登录。永不代填手机号与验证码 |
| `wind_control` | 页面出现验证码/滑块/"访问过于频繁" | 立刻停本轮并交人工；不做任何自动重试，重试只让风控计数继续累加 |
| `interval_blocked` | 距上次发送不足 `interval_seconds` 下限 | 等到点再发，不允许"补进度"而连发 |
| `error` | 其它异常（bsk 命令失败、页面结构变了） | 记 `detail` 原文前 200 字，跳过这个岗位继续下一批；连续 5 个 error 就停轮 |

判"未登录"必须两路证据一致：只看 URL 会把"正在跳转的会话页"误判成登录墙，
进而把人家的登录态当过期处理。
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_package.py -q -k 失败码`
Expected: `1 passed`

- [ ] **Step 6: 提交**

```bash
git add boss-apply/scripts/state.py boss-apply/references/failure-codes.md tests/test_boss_apply_package.py
git commit -m "feat(boss-apply): 失败归因表与 failure_code 枚举对齐"
```

---

## Task 8: SKILL.md 编排文本与包级约束

**Files:**
- Create: `boss-apply/SKILL.md`
- Test: `tests/test_boss_apply_package.py`

- [ ] **Step 1: 写失败测试**

```python
def _frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    assert m, "SKILL.md 没有 frontmatter"
    return m.group(1)


def test_frontmatter只用白名单键():
    """多余键会被 skill-creator 的 quick_validate.py 判失败，别踩。"""
    keys = set(re.findall(r"^([a-zA-Z_-]+):", _frontmatter(
        (PKG / "SKILL.md").read_text(encoding="utf-8")), flags=re.M))
    allowed = {"name", "description", "license", "allowed-tools", "metadata", "compatibility"}
    assert keys <= allowed, f"frontmatter 出现非白名单键：{keys - allowed}"


def test_name与目录同名且是kebab_case():
    fm = _frontmatter((PKG / "SKILL.md").read_text(encoding="utf-8"))
    name = re.search(r"^name:\s*(.+)$", fm, flags=re.M).group(1).strip()
    assert name == PKG.name, f"frontmatter name={name}，目录名={PKG.name}"


def test_正文不超过500行():
    """细节属于 references，主文件只留决策与流程。"""
    body = (PKG / "SKILL.md").read_text(encoding="utf-8").splitlines()
    assert len(body) <= 500, f"SKILL.md 已经 {len(body)} 行，把细节挪进 references"


def test_四条红线都在文里():
    text = (PKG / "SKILL.md").read_text(encoding="utf-8")
    for must in ("已登录", "request-help", "硬上限 50", "风险由使用者自己承担"):
        assert must in text, must


def test_bsk会话生命周期写在流程里():
    text = (PKG / "SKILL.md").read_text(encoding="utf-8")
    assert "bsk session start" in text and "bsk session stop" in text
    assert "--session" in text, "漏了 --session 的命令会打到另一个会话上"


def test_不许出现自己实现浏览器的字样():
    """约束①：浏览器能力全部来自 browser-skill，出现下面任何字样都是设计漂移。"""
    text = (PKG / "SKILL.md").read_text(encoding="utf-8")
    for banned in ("DrissionPage", "selenium", "playwright", "pyppeteer",
                   "remote-debugging-port", "requests.get"):
        assert banned not in text, banned


def test_skill目录里没有用户数据():
    """更新 skill 会整目录覆盖，用户数据混进去就没了。"""
    junk = [p.name for p in PKG.rglob("*")
            if p.is_file() and (p.name.startswith("chatted")
                                or p.name == "rules.json"
                                or p.suffix == ".log")]
    assert not junk, f"这些文件不该在 skill 目录里：{junk}"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_package.py -q -k "frontmatter or 红线 or 会话 or 浏览器 or 用户数据 or 500"`
Expected: FAIL — `SKILL.md` 不存在

- [ ] **Step 3: 写 SKILL.md**

````markdown
---
name: boss-apply
description: >
  在 BOSS 直聘上按使用者自己的规则自动投递简历：搜岗位、读任职要求、判分、
  对通过阈值的岗位发招呼语，并把每个岗位的结果与失败原因落成可查记录。
  依赖 browser-skill（bsk CLI + 扩展）与浏览器里已登录的 BOSS 直聘账号。
  Use when 用户说"帮我投 BOSS 直聘/投简历/找岗位并打招呼"且要求控制投递量与频率。
license: MIT
allowed-tools: Bash, Read, Write
metadata:
  version: "0.1.0"
compatibility: 需要 bsk CLI 与 browser-skill 扩展；不装则自检判 MISS 并停止。
---

# BOSS 直聘自动投递（boss-apply）

一轮 = 一次有始有终的投递任务：使用者说"投 30 个"，跑完汇报并退出。
不挂定时任务，不在会话里长循环。

## 四条红线（任何一条不满足就停手，不要绕）

1. **只用使用者自己已登录的浏览器**。本 skill 不做注册、不做登录、**永不代填手机号与验证码**。
   判到未登录：停下，调 `bsk request-help`，让人工去扫码。
2. **每轮默认上限 20 条招呼，硬上限 50**。计数读 `state.py count`，不读自己的记忆——
   上下文被压缩之后会低估，而多投的代价是骚扰 HR。
3. **风控/封号风险由使用者自己承担**。开跑前打印一句：投递用的是你自己的账号。
4. **拿不到可信信息就不发**。JD 读不到、消息列表读不到、页面结构和文档不一样 → 记
   `failure_code` 并跳过，绝不"猜一个按钮点下去"。

## 前置自检（先跑这个，别直接开浏览器）

```bash
python scripts/check_env.py            # 不通会告诉你缺什么
python scripts/check_env.py --json
```

- `MISS bsk_cli` → 让使用者装 `bsk` CLI 与 browser-skill 扩展（弹窗变绿）。**不要自己起浏览器。**
- `MISS rules` → 让使用者把 `assets/rules.example.json` 复制成 `~/.boss-apply/rules.json` 再改。
- 招呼语为空时**停下来问**，不要拿任何默认文案替使用者决定他要说什么话。

## 配置与数据在哪

| 位置 | 内容 |
|------|------|
| `~/.boss-apply/rules.json` | 城市、关键词、判分阈值、招呼语、间隔、每轮上限 |
| `~/.boss-apply/state/chatted.json` | 已打招呼的岗位 URL，跨轮去重 |
| `~/.boss-apply/state/rounds-YYYY-MM.json` | 每条岗位一行记录（含 `failure_code`） |

换 `BOSS_APPLY_HOME` 就是换一整套配置与记录 —— 同一台机器多人各用各的号靠这个隔离。
**skill 目录里永远不放用户数据**，更新 skill 会整目录覆盖。

## 一轮怎么走

```
1 自检        check_env.py 通过 + 读 rules.json + 定下本轮 round_id（如 20260930-140500）
2 开会话      bsk session start  → 记下 4 位 session id，之后每条命令都带 --session <id>
3 登录核对    bsk navigate <BOSS 搜索页> → bsk observe；落登录页就交人工（红线 1）
4 取列表      每个关键词：滚动 screens_per_keyword 页 → bsk observe 取岗位卡片
5 逐个岗位    python scripts/state.py seen 判重 → 点卡片开详情 → 读任职要求
6 判分        你自己读 JD 与使用者画像打分：score + 一句话理由
              score < match_threshold → record skipped_low_score，下一个
              阈值-15 ≤ score < 阈值 → 追问自己"到底卡在哪一条硬性要求"，把答案记进 detail
7 打招呼      只对过阈值的岗位，两套机制都要认：
              a) 点「立即沟通」→ 出现输入框 → 填招呼语 → 点发送
              b) 弹出「已向BOSS发送消息 / 留在此页 / 继续沟通」→ 点「继续沟通」
                 （「留在此页」什么都不做，不能点），进会话核对气泡文案
                 与本轮招呼语一致才算成功；对不上就按本账号文案补发一条
              发之前查 count 与 remaining，到上限立刻停止发送（但仍把剩下的岗位判完）
8 落记录      每个岗位一条：python scripts/state.py record --json '{...}'
              发送成功即落库，不要等一轮跑完再补
9 收尾        bsk session stop <id>（出错路径也要 stop）
              python scripts/state.py summary --round <id> → 按这张表的 code 讲给人听
```

## 频率

- 两次发送之间随机等 `interval_seconds`（默认 6~14 秒）。**不许为了"补进度"连发**。
- 关键词之间等 3~8 秒再翻页。
- 单轮翻页数受 `screens_per_keyword` 限制，别越翻越深。

## 失败归因

每个岗位必须落到 `references/failure-codes.md` 里的某个 code。汇报格式：

```
本轮 20 投：发送 8 / 分数不够 6 / 岗位已沟通过 3 / 读不到 JD 2 / 卡在无沟通按钮 1
```

不要只说"失败了"。同一个 code 连出 3 次以上，停下来说明"页面可能变了"，
把 `detail` 原文给用户看，而不是换个按钮继续试。

## 细节在哪

- `references/boss-dom.md` — 选择器、两套打招呼机制的现场、URL 会骗人的两种情形
- `references/failure-codes.md` — 每个 code 的判据与处置
- `references/rules-format.md` — rules.json 字段与边界
````

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_package.py -q`
Expected: 全部 PASS（含 Task 5-7 的文档锁）。若 `test_四条红线都在文里` 失败，按断言信息补文本，不要放宽断言。

- [ ] **Step 5: 跑全量单测确认没碰坏仓库里其它测试**

Run: `python -m pytest tests/ -q`
Expected: `PASS`，且新增用例数比改动前多约 8 条

- [ ] **Step 6: 提交**

```bash
git add boss-apply/SKILL.md tests/test_boss_apply_package.py
git commit -m "feat(boss-apply): SKILL.md 编排文本（四条红线 + bsk 会话生命周期）"
```

---

## Task 9: 打成 .skill 包并核对内容

**Files:**
- Create: `tools/build_boss_apply_skill.py`
- Test: `tests/test_boss_apply_package.py`

- [ ] **Step 1: 写失败测试**

```python
def test_打包产物只含该含的东西(tmp_path):
    """导入方拿到的应该就是 skill 目录本身，不能混进仓库的 boss_bot/、docs/、用户配置。"""
    import subprocess
    out = tmp_path / "boss-apply.skill"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "build_boss_apply_skill.py"),
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout
    import zipfile
    names = set(zipfile.ZipFile(out).namelist())
    assert {"boss-apply/SKILL.md", "boss-apply/scripts/state.py",
            "boss-apply/scripts/check_env.py", "boss-apply/assets/rules.example.json",
            "boss-apply/references/boss-dom.md"} <= names, sorted(names)[:20]
    assert not [n for n in names if "rules.json" in n and "example" not in n]
    assert not [n for n in names if "/state/" in n or n.endswith(".log")]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_boss_apply_package.py -q -k 打包`
Expected: FAIL — `build_boss_apply_skill.py` 不存在

- [ ] **Step 3: 写打包脚本**

```python
# tools/build_boss_apply_skill.py
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_boss_apply_package.py -q -k 打包`
Expected: `1 passed`

- [ ] **Step 5: 真产一次并核对**

Run: `python tools/build_boss_apply_skill.py && ls -la dist/`
Expected: 打印 8 个左右文件路径，`dist/boss-apply.skill` 存在且小于 30KB

- [ ] **Step 6: 提交**

```bash
git add tools/build_boss_apply_skill.py tests/test_boss_apply_package.py
git commit -m "feat(boss-apply): .skill 打包脚本（用户数据与字节码不进包）"
```

---

## Task 10: README 收录与上手清单

**Files:**
- Modify: `README.md`
- Test: 无新增（复用 Task 8 的包级约束）

- [ ] **Step 1: 在 README 目录与正文加一节**

`## 判分复盘…` 一节之后插入：

```markdown
## boss-apply：可分发的 skill 版本（子项目①）

同一个 BOSS 直聘，另一种形态：**纯 skill、零安装依赖**，跑在别人自己的浏览器上。
它在 `~/.agents/skills/browser-skill` 之上做编排，**不实现任何浏览器驱动**。

- 源码：仓库根 `boss-apply/`（`SKILL.md` + `references/` + `scripts/`）
- 打包：`python tools/build_boss_apply_skill.py` → `dist/boss-apply.skill`
- 使用者前置：装 `bsk` CLI + browser-skill 扩展，浏览器里保持 BOSS 直聘已登录，
  然后 `python boss-apply/scripts/check_env.py` 自检通过
- 与主程序的关系：本仓库的 `boss_bot/` 仍是开发者自用（cloakbrowser + Flask 面板）；
  skill 面向别人，走使用者自己的真实浏览器与指纹

四条内置红线：只用使用者自己已登录的浏览器（永不代填手机号/验证码）、
每轮默认 20 条硬上限 50、风险由使用者自己认、拿不到可信信息就不发。

设计与边界见 `docs/superpowers/specs/2026-09-30-boss-skill-apply-design.md`。
**本仓库不做真机验证**：那条路要求 cloakbrowser，而 skill 必须跑在使用者的真实浏览器里。
DOM 事实由 `tests/test_boss_apply_package.py` 逐条比对生产代码锁住。
```

- [ ] **Step 2: 跑一次全量测试**

Run: `python -m pytest tests/ -q`
Expected: 全 PASS

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: README 收录 boss-apply skill 版本与验证边界"
```

---

## 交付后仍然空着的两块（别当成已完成）

1. **真机第一次**：必须由装了 `bsk` + browser-skill 扩展的人跑。本仓库的测试全绿只保证
   "自检会指出缺什么、记录与上限判定不会算错、文档选择器和生产同源"，不保证别人机器上零摩擦。
2. **子项目②③**：会话回复闭环、记录与界面。②依赖 ① 的记录形状（`failure_code`/`round`/`stage`
   已定稿），③依赖 ①② 的数据。各自单独走 spec → 计划。
