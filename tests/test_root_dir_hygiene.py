# -*- coding: utf-8 -*-
"""根目录只剩"入口 + 当前配置 + 凭据"——其余一律有去处。

2026-10-29 他两次问起同一件事：一键启动脚本在哪儿、根目录为什么这么多这种文件。
清点出来的数是：**133 个条目，其中 79 份是 bot_config.json.bak-*/clobbered-***（1.0 MB），
剩下的是 `_tmp*` 临时监控、`flask_server.log`/`reload.log`/`@AutomationLog.txt`、
`test.json`/`test2.json`（e2e 传的裸文件名被 resolve_path 拼到仓库根落下的）、
`login_and_save.py`/`login_and_test.py`/`test_frontend.py`（README 里一个字都没提的旧入口）。
`start.bat` 一直就在根目录——它是被这堆东西埋了。

所以这条门判三件事：
1. 根目录条目 ⊆ 白名单（新增一个就得在这里解释一句为什么）；
2. 备份/临时/日志那一族形状在根目录必须 0 条——**这一条要能真的红**（下面配了变异）；
3. 双击入口确实住根目录，并且它调的那条链存在（入口挪走 = 他双击不到）。

留档都在 `archive/root_20261029/`，一件都没删（跟 Cookie 备份、归档盒、日志留档一个口径）。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

留档目录 = "archive"
留档去处 = "%s/root_20261029" % 留档目录

# 根目录允许出现的条目。分成三组写，是为了让"为什么留"跟着名字一起留在代码里。
入口 = {"start.bat", "start.sh"}
门面 = {"README.md", "CONTRIBUTING.md", "LICENSE", "pytest.ini", "requirements.txt", ".gitignore"}
当前配置 = {"bot_config.json", "config_overrides.json", "user_profile.json"}
凭据 = {"zhipin_cookies.json", "zhipin_cookies_1.json", "zhipin_cookies_ref.json"}
# 这两份不在版本库里（.gitignore 挡着），也不是本项目产生的：winmm.dll 与系统那份哈希不同
# ＝被人改写过的 DLL，language.txt 是一份 UTF-16 字符串覆盖表。像是他自己电脑上别的东西留下的，
# 门不许把他自己的文件判成"该清理"，脚本也不许动它。
他的东西 = {"winmm.dll", "language.txt"}
代码与数据目录 = {
    "boss_bot", "flask-version", "tests", "tools", "boss-apply", "static", "docs", "panel-site",
    "dist",                       # boss-apply.skill 构建产物，README 指着它
    "data", "logs", "messages",   # 运行时数据
    "cloakbrowser", "browser_data",
    "config_backups",             # 10-29 起配置备份的新家（原来摊在根目录）
    留档目录,                     # 一次性清理的去处
}
工具缓存目录 = {".git", ".scratch", ".pytest_cache", ".qoder", ".arts", ".codeartsdoer",
               ".firecrawl", "__pycache__",
               # 会话里贴图时工具自己在仓库根落的那一份（11-04 那张「访问受限」截图就是它带进来的）。
               # 不是项目内容、也不该入库（.gitignore 里挡着），但删它等于删用户贴进来的原图，
               # 所以给它一个"是工具缓存"的名分，而不是把它塞进 archive/ 假装是我们清过的东西
               ".qoder-input-images"}
# InstaBox 那条执行层（把 bot 搬到云端 Linux 沙箱跑）是 Node 的：
# package.json / package-lock.json 必须住仓库根，`npm run box:*` 才在仓库根解析得到；
# node_modules 是 npm 自己按"从脚本目录往上找"放下的，装进 tools/instabox 反而会让
# 下次 npm install 又回根目录。`.instabox/` 是 SDK 的本地状态目录（api-key 句柄与
# stream-url 临时钥匙都写在这儿，boosbot.mjs 按这个路径读，已被 .gitignore 挡住）。
# 这四样都不是散件，但依赖清单还没进版本库——clone 完要能直接开干，得连着
# tools/instabox/ 一起提交（等他点头）。
node执行层 = {"package.json", "package-lock.json", "node_modules", ".instabox"}

允许 = 入口 | 门面 | 当前配置 | 凭据 | 他的东西 | 代码与数据目录 | 工具缓存目录 | node执行层

# 根目录里一条都不许出现的形状（白名单之外的第二道：形状判据，专门盯"又会长回来"的那些）
坏形状 = [
    r".*\.bak(-.*)?$",          # 配置备份
    r".*\.clobbered(-.*)?$",    # 被覆写事故留下的那份
    r"_tmp.*",                  # 临时监控脚本与它的日志
    r".*\.log$",                # 散日志（都该进 logs/ 或 .scratch/）
    r"test\d*\.json",           # e2e 裸文件名落下来的
    r"nul|con|prn|aux",         # Windows 保留名：留一个整个文件夹都删不掉
    r".*\.png",                 # 截图散件
]


def _根目录条目() -> list:
    return sorted(p.name for p in ROOT.iterdir())


def _坏形状命中(条目) -> list:
    return ["%s（撞 %s）" % (名, 形) for 名 in 条目
            for 形 in 坏形状 if re.fullmatch(形, 名, flags=re.IGNORECASE)]


class Test根目录形状:
    def test_条目都在白名单里(self):
        多 = [n for n in _根目录条目() if n not in 允许]
        assert not 多, ("根目录多出这些条目：%s。要么给它一个去处（archive/ 或 data/ 或 .scratch/），"
                        "要么在这里解释为什么必须留在根目录" % "、".join(多))

    def test_备份临时与散日志一条都不许在根目录(self):
        中 = _坏形状命中(_根目录条目())
        assert not 中, "根目录又长出这些：%s" % "、".join(中)

    def test_这一族形状各自都要被抓到(self):
        """门必须能真的失败：每种脏形状单独摆一条，一条没抓到就是这形判据是空的。"""
        for 名 in ("bot_config.json.bak-20261029-123456", "_tmp_monitor.log", "nul",
                   "test2.json", "bot_config.json.clobbered-20261004", "截图.png"):
            assert _坏形状命中([名]), "「%s」这种形状在根目录没被抓到" % 名

    def test_该留的不许被冤枉(self):
        """反向哨兵：入口、凭据、他那两份 DLL/字符串表都不在该清理之列。"""
        for 名 in ("start.bat", "start.sh", "README.md", "bot_config.json", "user_profile.json",
                   "zhipin_cookies.json", "winmm.dll", "language.txt", "data", "archive"):
            assert not _坏形状命中([名]), "「%s」是本项目的东西，形状判据把它挡住了" % 名

    def test_白名单不许把备份类文件放进来(self):
        漏 = [n for n in 允许 if any(re.fullmatch(形, n, flags=re.IGNORECASE) for 形 in 坏形状)]
        assert not 漏, "白名单里混进了坏形状条目：%s＝留了口子，根目录会长回去" % 漏

    def test_留档目录里那些件真的还在(self):
        """只挪不删：挪走时必须数得出来，79 份备份一份都不能少。"""
        去 = ROOT / 留档去处
        assert 去.is_dir(), "留档目录不在：%s" % 去
        备份 = list((去 / "config_backups").glob("bot_config.json.*"))
        assert len(备份) >= 79, "根目录当时是 79 份备份，留档只有 %d 份＝挪丢了" % len(备份)


class Test双击入口:
    def test_入口脚本住根目录(self):
        for 名 in ("start.bat", "start.sh"):
            assert (ROOT / 名).is_file(), "%s 不在根目录，他就双击不到" % 名

    def test_入口调的那条链存在(self):
        文 = (ROOT / "start.bat").read_text(encoding="utf-8", errors="replace")
        assert "tools\\start_accounts.py" in 文, "start.bat 不再调一键启动器了？入口是空壳"
        assert (ROOT / "tools" / "start_accounts.py").is_file()
        # REM 里可以提 kill_port（那是"为什么不调它"的说明），真正的命令行一句都不许有
        命令 = [行.strip() for 行 in 文.splitlines()
                if 行.strip() and not 行.strip().lower().startswith(("rem", "::"))]
        assert not [行 for 行 in 命令 if "kill_port" in 行], \
            "start.bat 里出现了强杀端口那一步（会连面板一起杀）：%s" % 命令

    def test_备份的新家就在配置旁边(self):
        from boss_bot.unified_config import 备份子目录, 备份旧配置
        assert 备份子目录 == "config_backups"
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            根 = Path(td)
            (根 / "bot_config.json").write_text('{"a": 1}', encoding="utf-8")
            备份旧配置(根 / "bot_config.json", {"a": 1})
            assert (根 / "config_backups").is_dir(), "备份没落到 config_backups/"
            assert not list(根.glob("*.bak-*")), "备份又落回配置文件旁边了"


def _装保留名工具():
    import importlib.util
    去 = ROOT / "tools" / "删保留名文件.py"
    spec = importlib.util.spec_from_file_location("删保留名文件", str(去))
    模块 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(模块)
    return 模块


class Test保留名文件:
    """根目录那个 0 字节 `nul` 是 `2> nul`（Linux 习惯）在 Windows 上建出来的真文件。

    留着它，整个文件夹在资源管理器里都删不掉，所以清一次之外还要能再清：
    工具的判据与删除路径都要真跑一遍。
    """

    def test_根目录现在没有保留名(self):
        assert _装保留名工具().找(str(ROOT)) == [], "根目录又出现 Windows 保留名文件"

    def test_造一个出来必须找得到也删得掉(self, tmp_path):
        模块 = _装保留名工具()
        路径 = str(tmp_path / "nul")
        with open(模块.前缀 + 路径, "w") as f:
            f.write("")
        assert 模块.找(str(tmp_path)) == [路径], "造出来了却没被判据认出＝工具瞎了"
        assert 模块.删(str(tmp_path)) == [路径], "找到了却删不动"
        assert 模块.找(str(tmp_path)) == []

    def test_正常文件不许被当成保留名(self, tmp_path):
        模块 = _装保留名工具()
        for 名 in ("notes.txt", "data.json", "start.bat"):
            (tmp_path / 名).write_text("x", encoding="utf-8")
        assert 模块.找(str(tmp_path)) == []
