# -*- coding: utf-8 -*-
"""跑测试、起浏览器、面板下载都不许在用户项目外面留下找不到的东西。

2026-10-26 盘账：
- `%TEMP%`（这台机器上是 `C:\\Users\\35796\\AppData\\Local\\Temp`）里攒了 **2044 个**
  `boss_test_*`/`boss_pool_fixture_*` 目录——每一趟 fixture 建一个、谁也不收；
- `C:\\tmp` 里躺着 **17 个** 2026-09-17 那轮留下的 Chrome 空壳 profile（128 MB）加
  79 个取证脚本和中间结果，全在用户的项目文件夹外面，他找不到也不敢删。

口径：临时东西落在项目里的 `.scratch/`（已 gitignore），会话结束就扫掉。
出口只有一个：`boss_bot.unified_config.新建临时目录`（生产）和
`tests.conftest.mk测试临时目录`（测试，散场还会收）。
"""
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

坏形状 = ("tempfile.mkdtemp", "tempfile.gettempdir", "TMPDIR")
# 允许提"系统临时目录"的只有建出口的那两处 + 本门自己（字面量就是判据）
允许出口 = {"boss_bot/unified_config.py", "tests/conftest.py"}
扫描目录 = ("boss_bot", "tests", "tools", "flask-version")


def _去掉注释(源: str) -> str:
    """只留能执行的部分，代码本身一个字都不改。

    这一支的门是拿判据去扫源码，而修好之后注释里还要留着"以前退到系统临时目录"
    这段依据——不剥注释，门会被自己的说明打红，那种门下一步就是被人整条删掉。
    按 token 拼回去那一版会把空格丢掉（`def api_archive_download` 拼成
    `defapi_archive_download`），切片就找不到函数了，所以这里是按列把注释剪掉。
    """
    import io
    import tokenize
    列 = {}
    行列表 = 源.splitlines(keepends=True)
    try:
        for 词 in tokenize.generate_tokens(io.StringIO(源).readline):
            if 词.type == tokenize.COMMENT:
                列.setdefault(词.start[0], 词.start[1])
    except Exception:
        return 源
    出 = []
    for 第行, 线 in enumerate(行列表, 1):
        起 = 列.get(第行)
        if 起 is not None:
            线 = 线[:起] + ("\n" if 线.endswith("\n") else "")
        出.append(线)
    return "".join(出)


def 扫(目录: str, 源: str) -> list:
    """返回这页源码里"往项目外面写"的那几条坏形状。"""
    代码 = _去掉注释(源)
    return [坏 for 坏 in 坏形状 if 坏 in 代码]


def _漏的文件() -> list:
    漏 = []
    for 目录 in 扫描目录:
        for p in sorted((ROOT / 目录).rglob("*.py")):
            if "__pycache__" in str(p):
                continue
            相 = str(p.relative_to(ROOT)).replace("\\", "/")
            if p.name == Path(__file__).name:
                continue        # 这一支自己写着那些坏样例的字面量，不算它自己头上
            if 相 in 允许出口:
                continue
            坏 = 扫(目录, p.read_text(encoding="utf-8"))
            if 坏:
                漏.append("%s: %s" % (相, 坏))
    return 漏


class 代码不许写到项目外面Test:

    def test_全部代码里没有自建系统临时目录(self):
        漏 = _漏的文件()
        assert not 漏, "这些还在项目外面建东西，改用 新建临时目录 / mk测试临时目录: %s" % 漏

    def test_这条门自己能真的红(self):
        """反向哨兵：拿一段样例喂扫描器，它必须认出坏形状。
        不验这一条，门哪天被改成一串匹配不上的字面量就会永远绿。"""
        样例 = 'import tempfile\n目录 = tempfile.mkdtemp(prefix="x_")\n'
        assert 扫("tests", 样例) == ["tempfile.mkdtemp"]
        assert 扫("tests", 'import tempfile\n目录 = 新建临时目录("x")\n') == []

    def test_mac启动退路落在项目里(self):
        from boss_bot import browser_launcher as BL
        函数源 = _去掉注释(inspect.getsource(BL._launch_macos))
        assert "browser_data" in 函数源, "没传 user_data_dir 时也要落在项目里的 browser_data"
        assert "gettempdir" not in 函数源

    def test_账号profile从哪个目录起都落在仓库根下(self):
        """2026-10-03 那趟留下 `flask-version/browser_data/account_0`，410 MB，
        里面还带一份 10-03 的 Cookies：那会儿 main_loop 的兜底是相对 cwd 的
        `Path("browser_data") / account_n`，而 start.bat 会先 cd 进 flask-version，
        profile 就跟着长在 flask-version 底下。现在这句过 resolve_path 锚在项目根，
        这条门把"换个 cwd 起进程也一样"钉住——不然下次又是一個 410 MB 的暗账。"""
        import os
        from boss_bot.unified_config import BASE_DIR, resolve_path
        锚 = "user_data_dir = str(resolve_path("
        源 = (ROOT / "boss_bot" / "main_loop.py").read_text(encoding="utf-8")
        assert 锚 in 源, "兜底不再过 resolve_path：cwd 一变，profile 就长到 flask-version 底下去"
        assert 'Path("browser_data")' in 源[源.index(锚):源.index(锚) + 200], \
            "那一格兜底没了，换成别的写法就得先看它锚在哪儿"
        原 = Path.cwd()
        try:
            os.chdir(str(ROOT / "flask-version"))
            落 = Path(str(resolve_path(Path("browser_data") / "account_0")))
        finally:
            os.chdir(str(原))
        assert 落 == Path(str(BASE_DIR)) / "browser_data" / "account_0", \
            "从 flask-version 起进程，profile 就不在仓库根了：%s" % 落

    def test_面板下载归档不再往外面落文件(self):
        """/api/archive/<date> 以前每下载一次就在系统临时目录留一个 tmpXXXX.json，
        谁也不知道它是谁家的、也没人收。现在直接在内存里拼好吐出去。"""
        源 = _去掉注释((ROOT / "flask-version" / "app.py").read_text(encoding="utf-8"))
        起 = 源.index("def api_archive_download")
        止 = 源.index("def api_archive_view", 起)      # 切到下一个接口，别在内层 except 就收
        段 = 源[起:止]
        assert "NamedTemporaryFile" not in 段 and "mkdtemp" not in 段, "下载又往外落文件了"
        assert "BytesIO" in 段, "导出没走内存出口"


class 临时目录要在项目里并且会被收掉Test:

    def test_生产出口建的目录就在仓库内(self):
        import shutil
        from boss_bot.unified_config import SCRATCH_DIR, 新建临时目录
        目录 = 新建临时目录("门")
        try:
            assert 目录.is_dir()
            assert SCRATCH_DIR in 目录.parents, "%s 不在 %s 里面" % (目录, SCRATCH_DIR)
            assert str(ROOT) in str(目录.resolve()), "临时目录跑到了项目外面"
        finally:
            shutil.rmtree(str(目录), ignore_errors=True)

    def test_测试出口建的目录就在仓库内(self):
        from tests.conftest import mk测试临时目录, 测试临时根
        import shutil
        目录 = mk测试临时目录("门")
        try:
            assert 目录.is_dir()
            assert 测试临时根 in 目录.parents, "%s 不在 %s 里面" % (目录, 测试临时根)
            assert str(ROOT) in str(目录.resolve()), "临时目录跑到了项目外面"
        finally:
            shutil.rmtree(str(目录), ignore_errors=True)

    def test_会话结束有收干净这一步(self):
        源 = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        assert re.search(r"def pytest_sessionfinish", 源), "散场没人收，目录会一直攒"
        assert "_收干净(_本轮临时目录)" in 源

    def test_上一个会话崩了留下的也会被扫(self):
        """门要能真的失败：这一条把"崩了的残留"造出来，看开跑时那一步还在不在"""
        import shutil
        from tests.conftest import 测试临时根
        残留 = 测试临时根 / "上一轮崩了留下的"
        残留.mkdir(parents=True, exist_ok=True)
        try:
            源 = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
            assert re.search(r"^_收干净\(\[\]\)", 源, re.M), \
                "只收自己这一轮的：崩掉那一轮的壳子会永远留在项目里"
        finally:
            shutil.rmtree(str(残留), ignore_errors=True)


    def test_活着的另一条会话的目录不许被扫掉(self):
        """2026-10-27 实测的串味：分块全量还在后台跑，我另开一趟 pytest，
        开跑那一步把对面的 `logs_c0o9l2ut/` 连根拔了，对面收集阶段直接
        FileNotFoundError。门要打在"锁着的扫不掉、没锁的照样扫"这一对上。
        """
        import shutil
        from tests.conftest import _收干净, _被活人占着, 测试临时根
        占着 = 测试临时根 / "别的会话正在写"
        崩壳 = 测试临时根 / "上一轮崩了留下的"
        for 目 in (占着, 崩壳):
            目.mkdir(parents=True, exist_ok=True)
            (目 / "有内容.txt").write_text("别删", encoding="utf-8")
        柄 = open(str(占着 / ".lock"), "w+b")
        import msvcrt
        msvcrt.locking(柄.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            assert _被活人占着(占着) is True, "锁没被认出来＝还是会去拔对面的目录"
            assert _被活人占着(崩壳) is False, "没有.lock 的崩壳要认成'可以扫'"
            _收干净([])
            assert 占着.is_dir(), "别的会话正在用的目录被扫掉了"
            assert not 崩壳.is_dir(), "崩了的壳没扫——2044 个空目录就是这么攒出来的"
        finally:
            msvcrt.locking(柄.fileno(), msvcrt.LK_UNLCK, 1)
            柄.close()
            for 目 in (占着, 崩壳):
                shutil.rmtree(str(目), ignore_errors=True)

    def test_建目录时真的占了锁_散场也真的松了(self):
        """按代码行判，注释里提到不算：装配缺一条，另一条会话就会被扫；
        散场不松锁，Windows 上自己那一轮的目录就删不掉。"""
        源 = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        代码 = [l for l in 源.splitlines() if not l.strip().startswith("#")]
        体 = "\n".join(代码)
        assert "def mk测试临时目录" in 体 and "_占住(目录)" in 体.split(
            "def mk测试临时目录", 1)[1].split("def ", 1)[0], "建的目录没占锁"
        assert "not _被活人占着(残留)" in 体, "清扫没问一句'有没有人正在用'"
        散场 = 体.split("def pytest_sessionfinish", 1)[1]
        assert "_解锁(柄)" in 散场 and "柄.close()" in 散场, "散场不松锁＝自己那批删不掉"
        assert 散场.index("_解锁(柄)") < 散场.index("_收干净(_本轮临时目录)"), \
            "先删后松锁，Windows 上会留下一堆没删掉的壳"


class 项目外面不留我们自己的东西Test:
    """一次性盘账的门。

    只认**我们自己的前缀**：`C:\\tmp` 里还有别的项目的文件（8 月、9 月的图和其它
    脚本），拿"内容里出现 zhipin"当判据迟早会误伤别人的东西，那种门天天假红。
    """

    我们的前缀 = ("boss_test_", "boss_pool_fixture_", "boss_bot_chrome_", "boss_chrome_",
                  "boss_cookie_check_", "mem_", "attach_probe_", "sweep_probe_")

    def _在外面的(self):
        在外面 = []
        for 根 in (Path("C:/tmp"), Path.home() / "AppData" / "Local" / "Temp"):
            if not 根.is_dir():
                continue
            在外面 += [str(p) for p in 根.iterdir()
                        if p.name.startswith(self.我们的前缀)]
        return 在外面

    def test_临时目录与空壳profile都收干净了(self):
        漏 = self._在外面的()
        assert not 漏, ("这些是本项目建的，散在外面找不到也删不掉，请清掉（前 8 条）: %s"
                        % 漏[:8])
