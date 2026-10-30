# tests/test_safe_log_rotation.py
"""午夜轮转那条：改名失败要退避、要只说一遍、要照写不误，而且谁都不许删老日志。

钉的是 2026-10-23 00:00 的现场：`logs/boss_bot.log` 里 1947 行一模一样的
`日志轮转失败（PermissionError: [WinError 32]）`，连着 637、655 两串、毫秒递减——
那不是两个写者在抢，是**失败自述走 emit 又触发一次轮转判定**的递归，
加上 `rolloverAt` 在失败时不往前推，于是每条 record 都撞一次。
"""
import logging
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from boss_bot.safe_log import SafeTimedRotatingFileHandler, 轮转失败重试秒


def _建(tmp_path, 名="boss_bot.log"):
    路 = tmp_path / 名
    h = SafeTimedRotatingFileHandler(str(路), when="midnight", backupCount=7,
                                     encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))
    return h, 路


class 改名失败不许刷屏Test:
    def test_三百条记录只撞一次改名(self, tmp_path, monkeypatch):
        撞 = []

        def 会炸(self):
            撞.append(1)
            raise PermissionError(13, "[WinError 32] 另一个程序正在使用此文件")

        monkeypatch.setattr(TimedRotatingFileHandler, "doRollover", 会炸)
        h, 路 = _建(tmp_path)
        h.rolloverAt = time.time() - 1          # 到点了，该轮转
        for i in range(300):
            h.emit(logging.makeLogRecord({"msg": "INFO 第%d条" % i, "levelno": logging.INFO}))
        h.close()

        assert len(撞) == 1, "改名试了 %d 次：失败没退避，每条 record 都在撞" % len(撞)
        文 = 路.read_text(encoding="utf-8")
        assert 文.count("日志轮转失败") == 1, "同一场失败重复自述：%d 次" % 文.count("日志轮转失败")
        assert "INFO 第299条" in 文, "改名失败以后就不写了？当天日志必须有痕迹"

    def test_退避是真的往前推而不是原地(self, tmp_path, monkeypatch):
        撞 = []
        monkeypatch.setattr(TimedRotatingFileHandler, "doRollover",
                            lambda self: (撞.append(1), (_ for _ in ()).throw(OSError(32, "占用"))))
        h, _ = _建(tmp_path)
        h.rolloverAt = time.time() - 86400      # 过期一天，模拟跨午夜后才被发现的那场失败
        h.emit(logging.makeLogRecord({"msg": "x", "levelno": logging.INFO}))
        推后 = h.rolloverAt - time.time()
        h.close()
        assert 推后 > 0, "rolloverAt 还留在过去，下一条 record 又会撞"
        assert 轮转失败重试秒 - 5 <= 推后 <= 轮转失败重试秒 + 5, 推后

    def test_换个原因要再说一遍同一种不许刷屏(self, tmp_path):
        """去重按"原因+对象"，不许把新的失败也吞掉（否则真出事时一条都看不见）。"""
        h, 路 = _建(tmp_path)
        h._说一句失败(OSError(32, "另一个程序正在使用 boss_bot.log -> .2026-10-22"))
        h._说一句失败(OSError(32, "另一个程序正在使用 boss_bot.log -> .2026-10-22"))
        h._说一句失败(OSError(13, "权限不够 boss_bot.log -> .2026-10-23"))
        h.close()
        文 = 路.read_text(encoding="utf-8")
        assert 文.count("日志轮转失败") == 2, 文

    def test_没有流的时候不许炸(self, tmp_path):
        """自述写不进去是次要的，绝不能把 emit 带崩——腿还在跑，日志没了可以补。"""
        h, _ = _建(tmp_path)
        h.stream.close()
        h.stream = None
        h._说一句失败(OSError(32, "占用"))      # 不该抛
        h.close()


class 轮转成功不许删老日志Test:
    def _铺老日志(self, tmp_path):
        h, 路 = _建(tmp_path)
        铺 = []
        for i in range(1, 12):                 # 11 份 dated，比 backupCount=7 多
            f = tmp_path / ("boss_bot.log.2026-10-%02d" % i)
            f.write_text("老日志 %d" % i, encoding="utf-8")
            铺.append(f)
        return h, 路, 铺

    def test_标准库那套会删我们的门要拦住(self, tmp_path):
        """反向哨兵：先证明"不管的话确实会删"，否则这条门是恒真的废话。"""
        h, 路, 铺 = self._铺老日志(tmp_path)
        会被删 = TimedRotatingFileHandler.getFilesToDelete(h)   # 指名调用父类实现
        assert len(会被删) >= 4, "这份环境的父类已经不删了？那这条门要重写成别的判据"
        assert all(str(p) in 会被删 for p in 铺[:4]), 会被删
        assert h.getFilesToDelete() == [], "我们的实现还在依赖父类删除 = 用户数据没了"
        h.close()

    def test_真轮转一次老文件一个不少(self, tmp_path):
        h, 路, 铺 = self._铺老日志(tmp_path)
        h.rolloverAt = time.time() - 1
        h.emit(logging.makeLogRecord({"msg": "该轮转了", "levelno": logging.INFO}))
        h.close()
        assert all(p.exists() for p in 铺), "dated 老日志被 backupCount 清掉了——只许挪走"
        assert 路.read_text(encoding="utf-8").strip().endswith("该轮转了"), "轮转把这一条吞了"


class 面板挂的那份要是这个类Test:
    def test_app_的主日志handler用safe类且midnight(self):
        """app.py 里挂错类就等于这条门全白写——线上跑的是那份配置。"""
        源 = (Path(__file__).resolve().parents[1] / "flask-version" / "app.py").read_text(
            encoding="utf-8")
        assert "SafeTimedRotatingFileHandler(" in 源, "面板没在用 safe 那个 handler"
        assert 'when="midnight"' in 源, "面板的主日志没按午夜轮转，那 safe 类管的是别的东西"
