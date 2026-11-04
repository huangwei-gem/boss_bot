"""测试会话级夹具 — 保证单元测试永远不碰用户的真实运行数据。

reply_records.json / greet_records.json 里是真实的打招呼与回复历史，
之前跑一次 pytest 就会往里追加记录，等于测试污染生产数据。
"""

import logging
import logging.handlers
import os
import tempfile
from boss_bot.unified_config import 新建临时目录
from pathlib import Path

import pytest

# ─────────────────────────────────────────────
# 文件日志重定向：测试一律不许写进仓库的 logs/
# ─────────────────────────────────────────────
# 2026-09-29 排查"主号今天为什么 0 投递"时，logs/boss_bot.log 里 [账号1] 的
# 120 行全是 pytest 留下的（"引擎初始化完成"×20、"🧪 演练模式 本应回复: 你好"、
# "已删除失效 Cookie 文件 a0.json"），真实现场被完全盖掉。
# 必须在这里改类的 __init__：handler 是 app.py / greet_engine.py 在 import 时挂的，
# 等到 fixture 再摘就已经来不及拦同一轮里的写入了。
REPO_LOG_DIR = (Path(__file__).resolve().parent.parent / "logs").resolve()

# 测试的临时目录一律落在**项目里**的 .scratch/pytest_tmp/，散场收掉。
# 以前直接 `str(新建临时目录("conftest_"))`：2026-10-26 数了一下，光 %TEMP% 里就攒了
# 2044 个 `boss_*` 目录——跑一次全量回归就是两百多个空壳，谁也不知道能不能删，
# 而用户要找的东西应该在他自己的项目文件夹里找得到。
测试临时根 = REPO_LOG_DIR.parent / ".scratch" / "pytest_tmp"
_本轮临时目录 = []
_本轮锁柄 = []          # 每个临时目录留一个被独占锁住的句柄，活着的会话就扫不掉


def _占住(目录: Path):
    """在自己的临时目录里留一把活锁（`.lock`，句柄不关、区域锁死）。

    另一条会话开跑时想扫这个目录，就撞在这把锁上——它跳过去，不把我正在写的
    目录连根拔掉。2026-10-27 实测就是这么串味的：分块全量还在后台跑，我另开一趟
    pytest，`_收干净([])` 把那边的 `logs_c0o9l2ut/` 删了，对面收集阶段直接
    `FileNotFoundError: ...\\logs_c0o9l2ut\\greet_engine.log`。
    """
    try:
        柄 = open(str(目录 / ".lock"), "w+b")
    except OSError:
        return
    if _试锁(柄):
        _本轮锁柄.append(柄)
        return
    柄.close()


def _试锁(柄) -> bool:
    """拿到这把锁回 True，被别人占着回 False；平台没有锁原语就回 True（照旧扫）。"""
    try:
        import msvcrt
    except ImportError:
        pass
    else:
        try:
            msvcrt.locking(柄.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    try:
        import fcntl
    except ImportError:
        return True
    try:
        fcntl.flock(柄.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _解锁(柄):
    try:
        import msvcrt
        msvcrt.locking(柄.fileno(), msvcrt.LK_UNLCK, 1)
        return
    except Exception:
        pass
    try:
        import fcntl
        fcntl.flock(柄.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


def _被活人占着(目录: Path) -> bool:
    """有 .lock 且抢不到 = 另一条会话正在用它；没有 .lock = 上一轮崩了的壳，照扫。"""
    锁 = 目录 / ".lock"
    if not 锁.exists():
        return False          # 这把锁装上之前的老目录没有.lock，该扫
    try:
        柄 = open(str(锁), "r+b")
    except OSError:
        return True
    try:
        if _试锁(柄):
            _解锁(柄)
            return False
        return True
    finally:
        柄.close()


def _收干净(目录们):
    """把这一轮建的临时目录收掉；上一个会话崩了留下的也一并扫（只扫 .scratch/pytest_tmp 里面）。

    别的会话正锁着的那些一个都不碰——扫掉它们等于把对面那趟测试打成一条看不懂的
    FileNotFoundError（见 _占住）。
    """
    import shutil
    for 目录 in 目录们:
        shutil.rmtree(str(目录), ignore_errors=True)
    if 测试临时根.is_dir():
        for 残留 in 测试临时根.iterdir():
            if 残留.is_dir() and not _被活人占着(残留):
                shutil.rmtree(str(残留), ignore_errors=True)


_收干净([])      # 上一轮崩了留下的壳子，开跑之前先扫掉（只碰这个目录里面、且不碰活着的）


def mk测试临时目录(前缀: str) -> Path:
    """建一个测试专用临时目录，落在项目里，并在会话结束时收掉。"""
    测试临时根.mkdir(parents=True, exist_ok=True)
    目录 = Path(tempfile.mkdtemp(prefix=前缀 + "_", dir=str(测试临时根)))
    _本轮临时目录.append(目录)
    _占住(目录)
    return 目录


def 端口有人听(port) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        s.close()


# Chrome 的受限端口（ERR_UNSAFE_PORT）：5060/5061 是 SIP，页面一个字都加载不出来。
# 临时面板必须避开，否则浏览器报"无法访问此网站"，看着像面板没起来。
_浏览器拒绝的口 = frozenset({5060, 5061})


def 挑空闲端口(起始: int, 用途: str = "") -> int:
    """临时面板的端口：默认口被占就往后找一个空口。

    Windows 上 werkzeug 开了 SO_REUSEADDR，两个进程能同时 LISTENING 同一个口；
    上一轮实测被强杀没走 finally 时，留下的临时面板会继续应答，这一轮就会连着它跑
    （它的临时目录早被回收，写盘全 500、读到的也不是这一轮复制的配置）。
    所以起面板之前先确认这个口真的没人听。
    """
    for port in range(int(起始), int(起始) + 20):
        if port in _浏览器拒绝的口 or 端口有人听(port):
            continue
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            s.close()
        return port
    raise RuntimeError(f"{起始}~{int(起始) + 19} 全被占{('（' + 用途 + '）') if 用途 else ''}")


TEST_LOG_DIR = mk测试临时目录("logs")

_orig_file_init = logging.FileHandler.__init__
_orig_timed_init = logging.handlers.TimedRotatingFileHandler.__init__


def _rerouted(filename):
    """仓库 logs/ 下的路径换到临时目录，其它路径原样保留"""
    f = os.path.normpath(str(filename))
    if os.path.dirname(f).lower() == str(REPO_LOG_DIR).lower():
        return str(TEST_LOG_DIR / os.path.basename(f))
    return filename


def _file_init(self, filename, *args, **kwargs):
    _orig_file_init(self, _rerouted(filename), *args, **kwargs)


def _timed_init(self, filename, *args, **kwargs):
    _orig_timed_init(self, _rerouted(filename), *args, **kwargs)


logging.FileHandler.__init__ = _file_init
logging.handlers.TimedRotatingFileHandler.__init__ = _timed_init


# ─────────────────────────────────────────────
# 配置文件重定向：测试一律不许写用户那份 bot_config.json
# ─────────────────────────────────────────────
# 2026-10-10 一天里这份配置被跑测试盖掉了三次（18:00、19:53、22:25 的
# bot_config.json.bak-* 成对出现，一对就是一次 pytest）。写盘的不是某个
# 粗心脚本，而是两条看起来人畜无害的回归：
#   tests/test_headless_switch.py:368/381 → mgr.switch_headless() →
#     apply_headless_config() → cfg.save()，而那个 cfg 是测试自己造的，
#     ai.providers 是空的；
#   面板 GET /api/config → _ensure_greeting_defaults() → cfg.save()，
#     用 TestClient 打这个接口同样落到仓库根目录。
# UnifiedConfig.save() 的路径取的是模块级 BOT_CONFIG_FILE，所以在 import
# 期就把这三个全局改指向临时目录，比在 fixture 里换更早——app.py / config.py
# 是 `from ... import BOT_CONFIG_FILE`，等 fixture 跑起来它们已经把真路径
# 抄进自己的全局了。内容先拷一份真的过去，测试读到的和以前一模一样，
# 只是写不再回仓库。
REPO_DIR = Path(__file__).resolve().parent.parent
TEST_CONFIG_DIR = mk测试临时目录("config")
for _名字 in ("bot_config.json", "config_overrides.json", "user_profile.json"):
    _真的 = REPO_DIR / _名字
    if _真的.exists():
        (TEST_CONFIG_DIR / _名字).write_bytes(_真的.read_bytes())


def _isolate_config_paths():
    import boss_bot.unified_config as UC

    UC.BOT_CONFIG_FILE = TEST_CONFIG_DIR / "bot_config.json"
    UC.OVERRIDES_FILE = TEST_CONFIG_DIR / "config_overrides.json"
    UC.USER_PROFILE_FILE = TEST_CONFIG_DIR / "user_profile.json"
    try:
        import boss_bot.config as CFG
        CFG.CONFIG_FILE = str(UC.BOT_CONFIG_FILE)
    except Exception:
        pass
    # app.py 可能已经被先跑的测试 import 过了，它自己那份全局也要换
    import sys
    for 模块 in (sys.modules.get("app"), sys.modules.get("flask_app")):
        if not 模块:
            continue
        for 属性 in ("BOT_CONFIG_FILE", "USER_PROFILE_FILE"):
            现值 = getattr(模块, 属性, None)
            if 现值 is not None:
                setattr(模块, 属性, TEST_CONFIG_DIR / Path(str(现值)).name)


_isolate_config_paths()


@pytest.fixture(autouse=True, scope="session")
def isolate_runtime_records(tmp_path_factory):
    """把记录存储的全局单例指向临时目录。"""
    from boss_bot import reply_record

    data_dir = tmp_path_factory.mktemp("runtime_records")
    reply_record._reply_store = reply_record.ReplyRecordStore(
        path=str(data_dir / "reply_records.json"))
    reply_record._greet_store = reply_record.GreetRecordStore(
        path=str(data_dir / "greet_records.json"))

    # 指标文件也要隔离：UnifiedBotLoop 构造时会拿真实路径的 metrics 单例，
    # 一旦它在测试里执行回填，就会把生产环境的累计值标记成"已回填"而永久停在 0
    from boss_bot import metrics
    original_file = metrics.METRICS_FILE
    metrics.METRICS_FILE = data_dir / "metrics.json"
    metrics._store = None

    # 聊天会话文件也要隔离：MessageStore 不传 base_dir 时默认落在仓库的 messages/，
    # 测试里一次 mark_read 就会改写真实对话记录（把 HR 消息标成已读）
    from boss_bot import config as CFG
    original_base = CFG.BASE_DIR
    CFG.BASE_DIR = data_dir / "runtime_base"

    yield

    reply_record._reply_store = None
    reply_record._greet_store = None
    metrics.METRICS_FILE = original_file
    metrics._store = None
    CFG.BASE_DIR = original_base


@pytest.fixture(autouse=True, scope="session")
def 仓库里的进化数据一条都不许写():
    """SelfEvolveEngine 的状态文件属于用户：测试里不传 data_file 的那种构造，
    默认路径要整个换到临时目录。

    2026-10-19 撞上的：引擎内部那份"质量数据"路径过去只跟着文件名换、不跟着目录换，
    于是 `_save_quality_data()` 会写进仓库的 data/self_evolve_data.json，
    一条测试就把 4 条真规则调整顶成了 1 条 fixture。目录那半已在 self_evolve.py
    改对；这里再把默认路径本身换掉，测试从结构上就够不着仓库那份文件。

    为什么不是"跑完比对一下有没有被动过"：线上面板随时会合法地写这两个文件
    （实测 08:53:07 它就写了一次），那种比法只会天天报假红。
    """
    import boss_bot.self_evolve as SE

    数据目录 = mk测试临时目录("evolution")
    原默认 = SE._EVOLUTION_DATA_FILE
    for 名字 in ("evolution_data.json", "self_evolve_data.json"):
        真的 = REPO_DIR / "data" / 名字
        if 真的.exists():
            (数据目录 / 名字).write_bytes(真的.read_bytes())
    SE._EVOLUTION_DATA_FILE = 数据目录 / "evolution_data.json"
    try:
        yield
        assert str(SE._EVOLUTION_DATA_FILE).startswith(str(数据目录)), (
            "进化数据的默认路径又指回仓库了：测试一跑就写用户的真记录")
    finally:
        SE._EVOLUTION_DATA_FILE = 原默认


_兼容层常量 = ("SALARY_REPLY", "INTERVIEW_TIME_REPLY", "JOB_CONTENT_REPLY",
               "GREETING_REPLY", "DEFAULT_REPLY", "RESUME_DUPLICATE_REPLY",
               "RESUME_UNAVAILABLE_REPLY", "USER_PROFILE", "REPLY_RULES",
               "IMPORTANCE_KEYWORDS")


@pytest.fixture(autouse=True)
def 小时账不留给下一条():
    """IP 级整页加载小时账是**进程级全局**（风控按出口 IP 记，两号共用一份，
    所以它本来就该是全局的）——但全局态在测试里就是跨文件的假象源。

    实测：加了这道闸之后 `pytest tests/` 里 `test_unified.py::GreetRoundDryRunTest::
    正常模式会发送` 单跑绿、全量红——前面某条测试装了上限并记了几发，
    后面那条打招呼轮被自己的额度闸拦在搜索之前，看起来像"发送逻辑坏了"。
    每条测试进出各还原一次，桩配置造的账不许漏给下一条。
    """
    from boss_bot import browser_launcher as BL

    原 = dict(BL._SHARED_PAGE_LOAD_STATE)
    原["小时表"] = list(原.get("小时表") or [])
    yield
    BL._SHARED_PAGE_LOAD_STATE.clear()
    BL._SHARED_PAGE_LOAD_STATE.update(原)


@pytest.fixture(autouse=True)
def 热重载不许把兼容层留在桩配置上():
    """`main_loop._hot_reload_config()` 会调 `config.刷新兼容层(self.config)`，
    把 boss_bot.config 那 10 个模块级常量整个换成传进来那份 cfg 的值。

    测试造的 loop 一般带一份只有两三个字的桩配置，跑完不复原，后面别的文件就
    读到 `REPLY_RULES = {}`——实测 `pytest tests/test_unified.py
    tests/test_greeting_rule_overfire.py` 就红一条（单跑绿），也就是"规则表还在不在"
    这种断言在跨文件跑的时候会看到假象。生产里动的正是同一批名字，所以桩配置的账
    必须只留在桩配置身上。
    """
    import boss_bot.config as CFG

    原 = {名: getattr(CFG, 名, None) for 名 in _兼容层常量}
    try:
        yield
    finally:
        for 名, 值 in 原.items():
            setattr(CFG, 名, 值)


@pytest.fixture(autouse=True, scope="session")
def 仓库里的登录Cookie一条都不许动():
    """2026-10-17 16:16 一条新写的反向哨兵测试把主账号的 zhipin_cookies.json 搬进了
    data/stale_cookies/：harness 把 `_cookie_file()` 直接 patch 成了仓库里那个真文件名，
    而 `_handle_login()` 判成登录墙的第一件事就是归档 Cookie。文件没丢（只归档不删除），
    但它要是没人管，下一次重启就变成"要人工重新登录"。

    这道门只看一件事：跑完一轮测试，仓库里那些 Cookie 文件必须还在原处。
    """
    原名 = sorted(p.name for p in REPO_DIR.glob("zhipin*.json"))
    yield
    还在 = {p.name for p in REPO_DIR.glob("zhipin*.json")}
    少了 = [n for n in 原名 if n not in 还在]
    assert not 少了, (
        f"测试把用户的登录 Cookie 文件弄走了：{少了}。"
        f"去 data/stale_cookies/ 按时间戳拷回来，再把那条测试的 _cookie_file 指到 tmp")


@pytest.fixture(autouse=True, scope="session")
def cookie备份目录也隔开():
    """备份目录同样是用户的：里面是按时间留着的历史登录凭据，轮换时会按数量删旧的。

    2026-10-19 14:26 我自己跑一趟 pytest，data/cookie_backups/ 就多了三条
    "上一轮的好会话"——那是 fixture 造的假 Cookie。它们本身没用，但会挤掉真备份，
    而真备份是唯一能在"会话被写坏"之后捞回登录态的东西。
    """
    import boss_bot.browser_launcher as BL

    临时目录 = mk测试临时目录("cookie_backups")
    原函数 = BL._cookie_backup_dir
    BL._cookie_backup_dir = lambda: str(临时目录)
    try:
        yield
    finally:
        BL._cookie_backup_dir = 原函数


@pytest.fixture(autouse=True, scope="session")
def 微信token缓存也隔开():
    """`data/wechat_token.json` 是线上那把 access_token，不是测试该写的地方。

    现测 2026-10-27：跑一趟全量 pytest 就把仓库里这份刷成 `{"token": "TOK"}`
    （`tests/test_wechat_push.py` 的假接口返回值），mtime 就在测试那几分钟。
    真 token 被假 token 盖掉，下一次真推送就要等它自然过期才 recover得回来。
    跟 10-19 那条 Cookie 备份同一个坑：模块级的默认参数在 def 那一刻就焊死了，
    所以 `wechat_push.get_token` 改成用的这一刻才取路径，这里才换得动。
    """
    import boss_bot.wechat_push as WP

    原 = WP.TOKEN_CACHE
    WP.TOKEN_CACHE = str(mk测试临时目录("wechat_token") / "wechat_token.json")
    try:
        yield
    finally:
        WP.TOKEN_CACHE = 原


def pytest_sessionfinish(session, exitstatus):
    """散场把这一轮的临时目录全收掉。

    2026-10-26 盘账：`%TEMP%` 里攒了 2044 个 `boss_*` 目录——每一趟 fixture 建一个、
    谁也不收，跑一次全量回归就新增两百多个。东西散在系统临时目录里，用户既找不到
    也不敢删，所以改成落在项目里的 `.scratch/pytest_tmp/` 并且会话一结束就扫干净。

    先松掉自己那几把锁再删：Windows 上目录里还压着一个开着的文件就删不干净，
    留下一堆"看起来没扫"的壳，下一个人照样不敢删。
    """
    for 柄 in _本轮锁柄:
        try:
            _解锁(柄)
            柄.close()
        except Exception:
            pass
    _本轮锁柄[:] = []
    _收干净(_本轮临时目录)
