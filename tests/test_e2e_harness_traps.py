# -*- coding: utf-8 -*-
"""实测脚本和测试装配自己会骗人的几种写法（2026-10-20 一天踩到四条）。

起因：候选池双轨测试时 `e2e_refusal_labels_ui.py` 连着报"页面筛出 0 个会话"，
接口明明有 4 条。查下来产品没错——DrissionPage 的 run_js 是把整段代码塞进一个
函数里执行的，顶层写 `(function(){…})()` 这种表达式语句时返回值会被丢掉、拿到 None
（这一条本轮用真浏览器实测过：`return 1` 得 1，`(function(){return 1;})()` 得 None），
而脚本里又用 `or "[0, 0, []]"` 兜住 None，于是"一个字都没读到"被印成"页面算出来是 0"。
`e2e_account_scope_ui.py` 同期红的那几条是同一族另一种：数据范围 chip 取了全局
`.scope-chip`，可页面上"无头/有头"两颗按钮和判分复盘那组账号按钮同用这个类名。

同一天还牵出三条不相干但同类的"看着绿其实没测"：面板把配置路径**按值** import 进
本模块，沙箱顶掉 `uc.BOT_CONFIG_FILE` 也顶不动它，于是 `/api/rules` 在隔离实例里照样
写用户那一份；"跑完没动用户数据"的哈希清单少列了 `config_overrides.json`，脏的那份
留到今天；新写的一族测试类名没落在 `python_classes = *Test Test*` 上，整族一条没跑。

这族东西最坏的地方不是红，是**红得像是产品的错、或者绿得像是测过了**：照着"界面筛不到"
去改产品，而真相是测试没读到东西。所以门要长在代码里，并且每条都配一个反向哨兵——
拿改动前那份（HEAD）当真红样本，证明门不是摆设（HEAD 里压根没这段代码的，改用现文变异）。
"""
import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
实测 = sorted((ROOT / "tests").glob("e2e_*.py"))


def 历史(文件: str) -> str:
    """HEAD 里那份（本轮改动之前），只用来证明门会红。"""
    r = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:tests/" + 文件],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        pytest.skip("拿不到 HEAD 里那份（不在 git 仓库里跑）")
    return r.stdout


def 裸IIFE(文本: str):
    """run_js 的整段代码直接以 `(function…` 或 `!function…` 开头——返回值会被丢掉。

    `var 结果 = (function(){…})(); return 结果;` 这种是好的：外面那句 return
    才是 run_js 真正取到的东西。
    """
    坏 = []
    for m in re.finditer(r"run_js\(\s*[frbB]*[\"']{3}", 文本):
        头 = 文本[m.end():m.end() + 120].lstrip("\n \t")
        if 头.startswith("(function") or 头.startswith("( function") or 头.startswith("!function"):
            坏.append(头[:60].replace("\n", "\\n"))
    return 坏


def 编造兜底(文本: str):
    """`run_js(...) or "[0, 0, []]"`：读不到时现编一个"看着像结果"的默认值。"""
    return [x.group(0)[-46:] for x in
            re.finditer(r"\)\s*(?:or|\|\|)\s*[\"'][\[{]", 文本)]


def 全局chip(文本: str):
    """.scope-chip 在页面上有三家在用，选择器必须限定到数据范围那一组。"""
    行 = 文本.splitlines()
    坏 = []
    for i, 本行 in enumerate(行):
        if "scope-chip" not in 本行:
            continue
        if 本行.strip().startswith("#"):
            continue          # 注释里提这个类名是在讲为什么，不是在选元素
        窗口 = " ".join(行[max(0, i - 2):i + 3])
        if "metricsScope" in 窗口 or "data-scope" in 窗口:
            continue
        坏.append((i + 1, 本行.strip()[:72]))
    return 坏


class Test实测脚本不许把读不到印成结果:

    def test_现在没有一个脚本把IIFE直接交给run_js(self):
        报 = {p.name: 裸IIFE(p.read_text(encoding="utf-8")) for p in 实测}
        报 = {k: v for k, v in 报.items() if v}
        assert not 报, f"这些脚本的 run_js 拿不到返回值（DrissionPage 会丢）：{报}"

    def test_这条对改动前那份真的会红(self):
        assert 裸IIFE(历史("e2e_refusal_labels_ui.py")), \
            "改之前那版就是 `(function(){…})()` 直接交出去，门抓不到了"

    def test_没有为读不到的JS现编返回值(self):
        报 = {p.name: 编造兜底(p.read_text(encoding="utf-8")) for p in 实测}
        报 = {k: v for k, v in 报.items() if v}
        assert not 报, f"这些脚本用 or 兜了一个假返回值：{报}"

    def test_这条对改动前那份真的会红2(self):
        assert 编造兜底(历史("e2e_refusal_labels_ui.py")), \
            "改之前那版用 `or \"[0, 0, []]\"` 把 None 兜成了「页面自算 0 组」"


class Test实测起的临时面板要接住句柄:
    """起临时面板拿到的 Popen 句柄必须接住，否则 finally 里 `if proc:` 永远是假。

    10-28 实测：`e2e_contact_ledger_ui.py` 把 `E.start_server(...)` 的返回值丢了，
    每跑一趟就留下一个 Flask 实例——先攒到四个（5059/5062/5063/5064）才发现。
    端口和内存都白占，而且下一次跑会在别的口上再起一个，永不自愈。
    """

    def _丢了句柄(self, 文本: str):
        坏 = []
        for i, 行 in enumerate(文本.splitlines(), 1):
            s = 行.strip()
            if s.startswith("#"):
                continue
            # 只要这行不是 `xx = 起...(...)`（赋了名），而是直接把调用当语句，就是丢了
            if re.match(r"^(?:[A-Za-z_]\w*\.)?(?:start_server|起临时面板)\(", s):
                坏.append((i, s[:60]))
        return 坏

    def test_现有脚本都接住了(self):
        报 = {p.name: self._丢了句柄(p.read_text(encoding="utf-8")) for p in 实测}
        assert not {k: v for k, v in 报.items() if v}, \
            f"这些脚本起了面板却没接句柄，跑完不会停：{ {k: v for k, v in 报.items() if v} }"

    def test_这条对改动前那份真的会红(self):
        assert self._丢了句柄(历史("e2e_contact_ledger_ui.py")), \
            "改之前那行是 `E.start_server(tmp, PORT)` 直接当语句，门抓不到就等于没修"


class Test数据范围选择器要唯一:

    def test_面板上确实有几家在用这个类名(self):
        """前提哨兵：这门管的是"类名不唯一"，哪天只剩一家，前提就变了，得知道。"""
        模板 = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")
        assert 'class="scope-chip" id="modeHeadless"' in 模板, \
            "无头/有头按钮不再用 .scope-chip 了——上面那门的判据要跟着重看"

    def test_实测脚本不裸取全局scope_chip(self):
        报 = {}
        for p in 实测:
            坏 = 全局chip(p.read_text(encoding="utf-8"))
            if 坏:
                报[p.name] = 坏
        assert not 报, f"这些行取的是全局 .scope-chip，会连「无头/有头」和判分复盘那组一起数：{报}"

    def test_这条对改动前那份真的会红(self):
        assert 全局chip(历史("e2e_account_scope_ui.py")), \
            "改之前那版全局取 chip，实测把「chip 3 个 / 期望 3 个」这种假红印了出来"


class Test配置路径不许按值绑进面板:
    """app.py 里 `from boss_bot.unified_config import BOT_CONFIG_FILE` 这种按值绑定，
    会把"实测实例把路径顶到临时目录"这件事绕过去：顶了 uc 的模块属性，
    面板里那份名字还指着仓库根。于是沙箱实例照旧写用户真正的 bot_config.json。

    2026-10-20 实测：e2e_api 在配置已顶到临时目录的情况下，仍然把线上那份的
    importance_keywords（12 条）写成空、把薪资回复模板改成"测试模板"——
    写这两处的正是 /api/rules 与 /api/templates 里按值绑进来的那两个名字。
    """

    def _按值绑定的名字(self, 文本: str):
        """unified_config 里那几个路径常量被 import 成本模块名字，就是隐患。"""
        名 = ("BOT_CONFIG_FILE", "USER_PROFILE_FILE", "OVERRIDES_FILE")
        m = re.search(r"from boss_bot\.unified_config import \(([^)]*)\)", 文本, re.S)
        if not m:
            return []
        绑 = [n for n in 名 if re.search(r"[\s,(]" + n + r"\s*[,\)]", m.group(1))]
        直写 = re.findall(r"write_json_atomic\((?!uc\.)[A-Z_]*CONFIG_FILE", 文本)
        直读 = re.findall(r"open\((?!uc\.)[A-Z_]*(?:CONFIG|OVERRIDES|PROFILE)_FILE", 文本)
        return 绑 + 直写 + 直读

    def test_面板现在全部走现取的路径(self):
        文本 = (ROOT / "flask-version" / "app.py").read_text(encoding="utf-8")
        坏 = self._按值绑定的名字(文本)
        assert not 坏, f"app.py 里这几个配置路径又变成按值绑定了（沙箱顶不掉它）：{坏}"

    def test_这条对改动前那份真的会红(self):
        r = subprocess.run(["git", "-C", str(ROOT), "show", "HEAD:flask-version/app.py"],
                           capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            pytest.skip("拿不到 HEAD 里那份 app.py")
        assert self._按值绑定的名字(r.stdout), \
            "改之前那版就是按值绑定 + 直接 open/write，门抓不到就等于没修"


class Test实测脚本不许打线上面板:
    """会把配置写脏的那套（加账号/加 AI/改简历/清词表）只准打自己的沙箱。

    三端一致性那套是"读线上、写沙箱"，它读线上面板正是这份报告的意义所在，
    所以它单独立一条门：写助手默认值必须是沙箱。

    这条判据 10-28 起不再认"5000"这个数字：面板默认口换成了 5001（住在
    boss_bot/unified_config.py 一处），守卫要是还盯着旧口，
    指着 5001 的写请求就会一路绿灯打在用户正在用的面板上——红线等于被撤了。
    """

    会写配置的 = ("tests/e2e_api.py",)

    def _打线上面板的写请求(self, 文本: str):
        """判据打在"这台脚本的目标"上，不打在调用写法上：
        沙箱脚本也用 BASE_URL，但它那个值是跑起来现起的隔离实例（初值是空串）。

        红线要挡的是三件事：模块级把目标写成任何真地址（10-10 与 10-20 那两次），
        把端口数字拼进写请求的 URL，以及绕过同源那份自己算一个默认口。
        """
        写死 = re.findall(r'^BASE_URL\s*=\s*["\'][^"\']+["\']', 文本, re.M)
        硬拼 = [x.group(0) for x in re.finditer(
            r'requests\.(post|put|delete)\(\s*"[^"]*(5\d{3}|面板端口)[^"]*"', 文本)]
        自算 = re.findall(r'os\.environ\.get\("BOSS_PANEL_PORT"\)\s*or\s*5\d{3}', 文本)
        return 写死 + 硬拼 + 自算

    def test_现在没有脚本把写请求发给线上面板(self):
        报 = {}
        for 相对 in self.会写配置的:
            p = ROOT / 相对
            if not p.exists():
                continue
            坏 = self._打线上面板的写请求(p.read_text(encoding="utf-8"))
            if 坏:
                报[相对] = 坏
        assert not 报, f"这些脚本会把目标写死成用户那台线上面板（红线：只在隔离实例试）：{报}"

    def test_这条对改动前那份真的会红(self):
        assert self._打线上面板的写请求(历史("e2e_api.py")), \
            "改之前那版 BASE_URL 把目标直接写死成用户那台线上面板，所有 POST 都打在它上面"

    def test_写请求的助手不许回落到线上面板(self):
        """三端一致性那套是"读线上、写沙箱"，所以它的 api_post 只准认 SANDBOX。"""
        文本 = (ROOT / "tools" / "consistency_test" / "run_consistency_test.py").read_text(encoding="utf-8")
        assert "base or SANDBOX.get(\"url\")" in 文本, \
            "api_post 不再默认打沙箱了——它会悄悄回到 BASE_URL（线上那台）"

    def test_会写的脚本必须自己起隔离实例(self):
        for 相对 in self.会写配置的:
            p = ROOT / 相对
            if not p.exists():
                continue
            文本 = p.read_text(encoding="utf-8")
            assert ("起隔离实例" in 文本), f"{相对} 会写配置，但没有起隔离实例的这一手"


class Test用户数据守卫清单要盖住每一个配置出口:
    """「这一趟没动用户任何数据」那条门，管的文件必须等于会被写的文件。

    10-20 实测脚本把 bot_config / user_profile / config_overrides 三份都写了，
    我照守卫清单只恢复了 bot_config 那一份：config_overrides 里 importance_keywords
    被清成 []，「收到重要消息就暂停回复」从此悄悄不暂停（暂停原因永远匹配不上空词表），
    user_profile 的 age 被删。这两份一直脏到全量 pytest 里画像那条事实闸门才牵出来。
    清单是手写的，所以门要钉在「清单 ⊇ 真实配置出口」上，而不是钉在「清单非空」上。
    """

    脚本 = "tools/consistency_test/run_consistency_test.py"

    def _受管清单(self):
        """直接读脚本里那两份清单，不拿正则去刮源码。

        10-20 曾经用 `for 名 in (...)` 刮内联元组，脚本把清单拆成
        `用户配置文件 + 运行中会写` 两份之后刮不到了，就走 pytest.skip——
        一条静默跳过的门看着和通过一样，正是要防的那种假绿。
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location("一致性脚本_被测",
                                                      str(ROOT / self.脚本))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        for 名 in ("用户配置文件", "运行中会写"):
            assert hasattr(m, 名), f"一致性脚本里没有 {名}，哈希守卫的清单没人核了"
        return set(m.用户配置文件) | set(m.运行中会写)

    def test_每个配置出口都在哈希清单里(self):
        from boss_bot import unified_config as uc
        必须 = {uc.BOT_CONFIG_FILE.name, uc.USER_PROFILE_FILE.name, uc.OVERRIDES_FILE.name}
        实际 = self._受管清单()
        assert 必须 <= 实际, f"这三个配置文件会被写，但「跑后一个都没变」根本不看它们：{必须 - 实际}"

    def test_清单少一条时这条门真的会红(self):
        """反向变异：从清单里抽掉 config_overrides，子集断言必须翻。

        拿 HEAD 那份脚本当样本只会 skip（这轮才加的守卫），证明不了门会红，
        所以直接在现读的清单上挖一条。
        """
        from boss_bot import unified_config as uc
        实际 = self._受管清单()
        assert uc.OVERRIDES_FILE.name in 实际, "清单里本来就没有 config_overrides，变异没基线"
        挖掉 = 实际 - {uc.OVERRIDES_FILE.name}
        必须 = {uc.BOT_CONFIG_FILE.name, uc.USER_PROFILE_FILE.name, uc.OVERRIDES_FILE.name}
        assert not 必须 <= 挖掉, "删掉一条还过门——这条门是摆设"

    def test_写配置的脚本自己也要盯这三份文件(self):
        """沙箱顶路径失败时不抛错，只悄悄写真文件——所以 e2e_api 跑完要自己比哈希。"""
        from boss_bot import unified_config as uc
        文本 = (ROOT / "tests" / "e2e_api.py").read_text(encoding="utf-8")
        m = re.search(r"用户数据文件 = \(([^)]*)\)", 文本)
        assert m, "e2e_api 没有 用户数据文件 这份守卫清单了"
        实际 = set(re.findall(r"\"([^\"]+)\"", m.group(1)))
        必须 = {uc.BOT_CONFIG_FILE.name, uc.USER_PROFILE_FILE.name, uc.OVERRIDES_FILE.name}
        assert 必须 <= 实际, f"跑前跑后的哈希守卫漏了这几个：{必须 - 实际}"
        assert "record(\"这一趟没动用户那三份配置\"" in 文本, \
            "比了哈希但没记成一条结果——变了也不会出现在失败项里"

    def test_文件真被写了的时候这条门会红(self, tmp_path, monkeypatch):
        """门必须能真的失败：造一份会变的假配置目录给它比。"""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "e2e_api_被测", str(ROOT / "tests" / "e2e_api.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        monkeypatch.setattr(m, "PROJECT_ROOT", tmp_path)
        for 名 in m.用户数据文件:
            (tmp_path / 名).write_text("原样", encoding="utf-8")
        跑前 = m.快照用户数据()
        assert m.找变了(跑前, m.快照用户数据()) == [], "没写也算变——这门会天天假红"
        (tmp_path / "config_overrides.json").write_text("被写脏了", encoding="utf-8")
        变了 = m.找变了(跑前, m.快照用户数据())
        assert len(变了) == 1 and "config_overrides" in 变了[0], \
            f"只有 config_overrides 被改了，守卫必须只报它：{变了}"


class Test测试类名要落在收集规则上:
    """pytest.ini 写的是 `python_classes = *Test Test*`——普通类名字两头都不带 Test 就整族不跑。

    10-20 新写的「覆盖层热重载」两条测试写完直接绿了 9 条，因为它们根本没被收集：
    类叫 `覆盖层也要会热重载`。这种红不了也测不到，只会让人以为全绿。
    （`unittest.TestCase` 的子类不受这条规则约束，pytest 照收，所以判据要先放过它们。）
    """

    def _收不到的类(self, 目录):
        坏 = []
        for p in sorted(Path(目录).glob("test_*.py")):
            树 = ast.parse(p.read_text(encoding="utf-8"))
            for n in 树.body:
                if not isinstance(n, ast.ClassDef):
                    continue
                if any("TestCase" in ast.unparse(b) for b in n.bases):
                    continue
                有测试 = any(isinstance(s, ast.FunctionDef)
                             and s.name.startswith("test_") for s in n.body)
                if 有测试 and not (n.name.startswith("Test") or n.name.endswith("Test")):
                    坏.append(f"{p.name}:{n.lineno} class {n.name}")
        return 坏

    def test_现在没有收不到的测试类(self):
        坏 = self._收不到的类(ROOT / "tests")
        assert not 坏, f"这些类里的 test_ 方法一条都不会跑：{坏}"

    def test_这条对刚踩的那个名字真的会红(self):
        """反向变异：把改名回来的那个类还原成当时那个名字，必须被点名。"""
        文本 = (ROOT / "tests" / "test_config_hot_reload.py").read_text(encoding="utf-8")
        假设没改 = 文本.replace("class 覆盖层热重载Test:", "class 覆盖层也要会热重载:", 1)
        assert 假设没改 != 文本, "类名变了，这条变异样本做不出来——先确认测试还在不在"
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            Path(d, "test_config_hot_reload.py").write_text(假设没改, encoding="utf-8")
            点名 = self._收不到的类(d)
        assert any("覆盖层也要会热重载" in x for x in 点名), \
            f"名字不合收集规则却没被点名——这条门是摆设：{点名}"




