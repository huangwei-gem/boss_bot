# -*- coding: utf-8 -*-
"""面板默认口只能有一个字面量，README 里的地址也得跟着它。

起因（2026-10-28 用户原话）：「你以后把端口换成 5001 呗」。
换口这件事最怕的不是换，是只换一半：面板听 5001、一键启动器等 5000、三端一致性脚本
读 5000、README 教他 curl 5000——每一处都"看着对"，合起来就是"面板起来了但没人连得上"。
10-27 与 10-28 两轮实测都撞在 5000 上：占着那个口的是**另一个项目的 app.py**
（Loomy Workspace 里那个 TikTok web，PID 37940；`/api/status` 回的是
`{"errorCode":1006,"message":"not found"}`，首页标题是 TikTok），面板一绑就抛。

这里钉四件事：
1. 默认口是 5001，且这个字面量在整个仓库的代码里只许出现在 unified_config 一处；
2. 每个要用"线上面板那个口"的脚本必须从那一份取，不许自己算环境变量；
3. "实测脚本不许打线上面板"那几道红线守卫认的必须是**同一个数**——
   守卫里写死旧口的，换口之后就等于把红线撤了（指着 5001 的写请求会一路绿灯）；
4. README 里的 curl/访问地址跟着默认口，别教他连一个没在跑的口。
"""

import io
import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from boss_bot.unified_config import 面板端口, 面板默认端口

# 要读"线上面板那个口"的文件：每一个都必须从同一份取
用得到口的 = [
    "flask-version/app.py",
    "flask-version/access_guard.py",
    "tools/start_accounts.py",
    "tools/consistency_test/run_consistency_test.py",
    "tools/e2e_dashboard.py",
    "tools/verify_dashboard_ui.py",
    "tools/verify_three_way.py",
    "tools/public_panel.py",
    "tools/check_headless_boss.py",
    "tools/prune_greet_records.py",
    "tools/decline_offline_interviews.py",
    "tools/ui_screenshot_sweep.py",
    "tests/e2e_greet_records_ui.py",
    "tests/e2e_browser.py",
    "tests/e2e_api.py",
    "tests/e2e_contact_ledger_ui.py",
    "tests/e2e_job_pool_ui.py",
    # 10-29 根目录清理：这两份从根目录挪了家，门跟着换地址。
    # test_frontend.py 是早就废弃的实测壳，挪进 archive/ 之后不再扫（留档不是出口）。
    "tools/legacy/kill_port.py",
    "tools/start_panel.ps1",
]

扫代码 = ["boss_bot", "tools", "tests", "flask-version"]
跳目录 = {"__pycache__", "node_modules", "cloakbrowser", "browser_data"}

# 根目录那几个壳脚本不在上面任何目录里，但它们真的会连面板：
# 10-28 换口之后实测漏了三处（start_panel.ps1 探 5000、test_frontend.py 打开 5000、
# kill_port.py 的找空口起点写死 5000）——门只扫子目录，这三处就一直绿着。
# 10-29 把它们挪走之后改成"现扫根目录一层"：写死名单会在文件搬家之后静默扫不到，
# 而根目录再长出一个连面板的散件时，名单里不会有它。
根目录脚本后缀 = (".py", ".ps1", ".bat", ".cmd", ".sh")
# 显式点名：它们不在根目录那一层，但和根目录散件同样容易写死一个数。
另外要扫 = ["tools/start_panel.ps1"]


def 根目录脚本(根=ROOT):
    return sorted(p.name for p in Path(根).iterdir()
                  if p.is_file() and p.suffix.lower() in 根目录脚本后缀)


def 读(*段):
    return io.open(os.path.join(ROOT, *段), encoding="utf-8").read()


def 只留代码(文本):
    """注释和文档串里写"以前是 5000"是历史账，不算第二份判据；只看真会跑的字符。

    单独一行的三个引号也算开文档串：模块顶上那种"第一行只有引号"的写法，
    前一刻还被这函数当成代码，于是整篇 docstring 里的地址都跑进判据里来了。
    """
    出, 串中 = [], False
    for 线 in 文本.splitlines():
        条 = 线.strip()
        if 条.startswith("#"):
            continue
        n = 条.count('"""')
        if 串中:
            if n:
                串中 = False
            continue
        if n >= 1:                      # 单行 docstring（n 偶数）不开头，奇数才进串里
            串中 = n % 2 == 1
            continue
        出.append(线)
    return "\n".join(出)


def 犯禁(线):
    """仓库里任何一行真会跑的代码，提到面板口只能靠那个变量；再写一遍 5001/5000
    就是第二份判据，早晚有一天两边对不上（10-27 那次是监听口与探测口写死两遍）。

    两条形状都要有：光盯 `port = 5001` 这种相邻写法，变异实测漏掉两种——
    `default=5001)`（数字前隔着 `type=int, `）和 `os.environ.get("BOSS_PANEL_PORT") or 5001`
    （隔着 `") or `）。所以第二条码打在整行上：这一行既在谈端口、又真写了一个面板口形状
    （500x/501x）的数，就算第二份判据。临时副本面板用的 5056/5059/5077 那一档不在里面，
    它们是实测自己起的实例，不是"线上那个口"。
    """
    紧挨 = re.compile(r'(端口|PORT|port)\s*[:=]?\s*["\']?(500\d|501\d)\b|'
                      r'[:/](500\d|501\d)\b|localhost:(500\d|501\d)')
    谈端口 = re.compile(r'端口|[Pp]ort')
    面板口形状 = re.compile(r'\b(500\d|501\d)\b')
    # 第三条码打在"当前那个默认口"这个数字本身：整棵树里除了 unified_config 那一份，
    # 谁写出这个数就是在替面板第二次决定去哪连。前两条码都放过它——
    # `$探.Connect("127.0.0.1", 5001)` 那行没有"端口"字样，`起 = 5001` 连 IP 都没有，
    # 两条变异实测都能绿着过去（10-28 我自己写 ps1 壳时就是这么漏的）。
    就是那个口 = re.compile(r'\b%d\b' % 面板默认端口)
    return (紧挨.search(线) or 就是那个口.search(线)
            or (谈端口.search(线) and 面板口形状.search(线)))


def 扫根目录散件(根=None):
    """根目录那一层的 .py/.ps1/.bat/.cmd/.sh + 显式点名的子目录壳脚本，逐行查口。

    注释行（# / REM / ::）不算：那些是历史账。
    """
    出 = []
    名单 = set(根目录脚本(根 or ROOT))
    if 根 is None:
        名单 |= set(另外要扫)
    for 相对 in sorted(名单):
        文 = 读(相对) if 根 is None else io.open(
            os.path.join(str(根), 相对), encoding="utf-8").read()
        if 相对.endswith(".py"):
            文 = 只留代码(文)
        for 线 in 文.splitlines():
            条 = 线.strip()
            if 条.startswith("#") or 条.upper().startswith(("REM ", "::")):
                continue
            if 犯禁(线):
                出.append("%s: %s" % (相对, 条[:90]))
    return 出


def test_默认口是5001而且环境变量还能顶():
    assert 面板默认端口 == 5001, 面板默认端口
    assert 面板端口() == 5001
    os.environ["BOSS_PANEL_PORT"] = "5077"
    try:
        assert 面板端口() == 5077, "BOSS_PANEL_PORT 顶不动默认口，面板被别的项目占着就完全起不来"
    finally:
        del os.environ["BOSS_PANEL_PORT"]


def test_填坏了要退回默认而不是崩在启动路上():
    os.environ["BOSS_PANEL_PORT"] = "abc"
    try:
        assert 面板端口() == 面板默认端口
    finally:
        del os.environ["BOSS_PANEL_PORT"]


def test_端口字面量在代码里只许出现一处():
    """整棵代码树 + 根目录散件，面板口只许从 unified_config 那一份取。

    判据本体在 犯禁() 上（那里写着三条码各自的来由），这里只管扫全。
    """
    抄 = []
    for 目录 in 扫代码:
        for 根, 子, 文件们 in os.walk(os.path.join(ROOT, 目录)):
            子[:] = [d for d in 子 if d not in 跳目录]
            for 名 in 文件们:
                if not 名.endswith(".py"):
                    continue
                p = os.path.join(根, 名)
                相对 = os.path.relpath(p, ROOT).replace("\\", "/")
                if 相对 == "boss_bot/unified_config.py" or 相对.startswith("tests/test_panel_port"):
                    continue            # 唯一出处与这条门自己
                for 线 in 只留代码(读(相对)).splitlines():
                    if 犯禁(线):
                        抄.append("%s: %s" % (相对, 线.strip()[:90]))
    抄 += 扫根目录散件()
    assert not 抄, "面板口又被人各写一遍：\n" + "\n".join(抄)


def test_根目录长出一个写死口的散件门要当场红(tmp_path):
    """名单改成现扫根目录之后，这条要证明它真的会动：往一个假根目录塞一份
    `probe.py` 写死 5001，扫出来必须是它。少了这条，"根目录散件也扫"就又退回成
    一份写死的名单——10-29 那两个 FileNotFoundError 正是名单跟不上搬家。
    """
    (tmp_path / "probe.py").write_text("PANEL_PORT = 5001\n", encoding="utf-8")
    (tmp_path / "ok.sh").write_text("curl http://127.0.0.1:$PORT/api/status\n", encoding="utf-8")
    (tmp_path / "note.txt").write_text("port 5001 只是说说\n", encoding="utf-8")
    抄 = 扫根目录散件(tmp_path)
    assert 抄 == ["probe.py: PANEL_PORT = 5001"], 抄
    # 反向：同一个口用变量取的文件不许被抓
    (tmp_path / "probe.py").write_text("from boss_bot.unified_config import 面板端口\n"
                                       "s = 面板端口()\n", encoding="utf-8")
    assert 扫根目录散件(tmp_path) == []


def test_搬过家的散件要从名单里去掉():
    """留在名单里的旧路径会让这条门自己 FileNotFoundError（10-29 实测红过两条）——
    但更糟的是它红在"文件不存在"上，看着像扫描坏了，其实那个出口真的没人看了。
    """
    断 = [相对 for 相对 in 用得到口的 if not os.path.exists(os.path.join(ROOT, 相对))]
    assert not 断, "这些出口已经不在原地，名单得跟着改（或确认它真的不再是出口）：" + "、".join(断)
    assert "test_frontend.py" not in 用得到口的, "废弃的实测壳挪进 archive/ 了，别再当真出口扫"


def test_每个用线上口的脚本都从同一份取():
    没取 = []
    for 相对 in 用得到口的:
        源 = 只留代码(读(相对))
        if "面板端口" not in 源:
            没取.append(相对)
    assert not 没取, "这些脚本还在自己算端口（或写死了一个数）：" + "、".join(没取)


def test_红线守卫认的是现在那个口不是历史上的5000():
    """"实测不许打线上面板"这几道守卫要是把旧口写死，换口之后红线当场失效：
    指着 5001 的写请求会一路绿灯打在用户正在用的面板上。"""
    守卫 = {
        "tests/e2e_contact_ledger_ui.py": "实测禁止打线上面板",
        "tests/e2e_job_pool_ui.py": "实测禁止打线上面板或别的实测口",
        "tests/e2e_api.py": "拒绝运行",
        "tools/e2e_dashboard.py": "BOSS_E2E_ALLOW_LIVE",
        "tools/ui_screenshot_sweep.py": "BOSS_SWEEP_ALLOW_LIVE",
    }
    for 相对, 提示 in 守卫.items():
        源 = 只留代码(读(相对))
        assert 提示 in 源, "%s：红线提示不在了" % 相对
        # 一条 assert 常常跨两三行（判据写在上一行、话写在下一行），所以看提示行和它上面三行
        落点 = [i for i, l in enumerate(源.splitlines()) if 提示 in l]
        assert 落点, "%s：找不到那条守卫" % 相对
        窗口 = [源.splitlines()[i - k] for i in 落点 for k in range(4) if i - k >= 0]
        assert any("面板端口" in l for l in 窗口), \
            "%s：这条守卫没认现在那个口（%r）——换口等于把红线撤了" % (相对, 窗口[:4])


def test_READM里教的地址跟着默认口走():
    """文档写 5000 他就去 curl 5000，那里站着的是别的项目；他会说"面板坏了"。"""
    文 = 读("README.md")
    旧 = [l.strip()[:70] for l in 文.splitlines() if re.search(r'[:/]%d' % 面板默认端口, l) is None
          and re.search(r'localhost:500\d|127\.0\.0\.1:500\d|0\.0\.0\.0:500\d', l)]
    assert not 旧, "README 还在教他连旧口：\n" + "\n".join(旧)
    assert "localhost:%d" % 面板默认端口 in 文 or "0.0.0.0:%d" % 面板默认端口 in 文, \
        "README 里连现在这个口都没写"


def test_一键脚本的说明也不留旧口():
    文 = 读("start.bat") + 读("start.sh")
    旧 = [l.strip()[:80] for l in 文.splitlines() if re.search(r'\b500\d\b', l)]
    assert not 旧, "start.bat / start.sh 的说明里还写着旧口，改口之后就是误导：\n" + "\n".join(旧)
