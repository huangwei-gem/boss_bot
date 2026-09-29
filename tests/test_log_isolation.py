"""测试不许把日志写进仓库的 logs/（task #38 顺带查出）。

2026-09-29 排查"主号今天为什么 0 投递"时，logs/boss_bot.log 里 [账号1] 的 120 行
全是 pytest 留下的（"引擎初始化完成"×20、"🧪 演练模式 本应回复: 你好"、"已删除失效
Cookie 文件 a0.json"），真实现场被完全盖掉，我第一轮就被这批假线索带偏。
"""

import logging
import time
from logging.handlers import TimedRotatingFileHandler

from tests.conftest import REPO_LOG_DIR, TEST_LOG_DIR


def _size(path):
    return path.stat().st_size if path.exists() else 0


def test_仓库日志文件一个字都不许多():
    import boss_bot.greet_engine  # noqa: F401  它 import 时就挂上 logs/greet_engine.log

    before = {name: _size(REPO_LOG_DIR / name)
              for name in ("boss_bot.log", "greet_engine.log")}
    sentinel = "SENTINEL_日志隔离_%d" % int(time.time())

    root = logging.getLogger()
    probe = TimedRotatingFileHandler(str(REPO_LOG_DIR / "boss_bot.log"),
                                     when="midnight", encoding="utf-8")
    root.addHandler(probe)
    try:
        logging.getLogger("greet_engine_file").error(sentinel)
        logging.getLogger("boss_bot.main_loop").error(sentinel)
        for h in list(root.handlers) + list(
                logging.getLogger("greet_engine_file").handlers):
            h.flush()
    finally:
        root.removeHandler(probe)
        probe.close()

    for name, size in before.items():
        assert _size(REPO_LOG_DIR / name) == size, f"{name} 被测试写进去了"


def test_日志是真的被挪走而不是被丢掉():
    """只断言"没写进仓库"不够：handler 静默失败同样满足它"""
    sentinel = "SENTINEL_落盘确认_%d" % int(time.time())
    handler = logging.FileHandler(str(REPO_LOG_DIR / "greet_engine.log"), encoding="utf-8")
    logger = logging.getLogger("probe_redirect_logger")
    logger.handlers = [handler]
    logger.propagate = False
    logger.error(sentinel)
    handler.flush()

    assert str(TEST_LOG_DIR) in handler.baseFilename, \
        f"baseFilename 还是仓库路径: {handler.baseFilename}"
    target = TEST_LOG_DIR / "greet_engine.log"
    assert target.exists() and sentinel in target.read_text(encoding="utf-8")
    handler.close()
