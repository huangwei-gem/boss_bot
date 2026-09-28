"""求职漏斗指标 — 累计 + 当日 + 分账号

为什么不直接数 greet_records/reply_records：
1. 两个记录文件都有 MAX_*_RECORDS=5000 的截断，"历史累计"会越用越少；
2. 记录按账号名过滤，账号重名或改名就对不上。
这里只存计数器，不存明细，文件永远只有几 KB。
"""

import json
import threading
from datetime import date
from pathlib import Path

from boss_bot.unified_config import BASE_DIR, write_json_atomic

METRICS_FILE = BASE_DIR / "data" / "metrics.json"

# 漏斗的三个计数点
FIELDS = ("greet_sent", "resume_sent", "interview")


def _blank() -> dict:
    return {f: 0 for f in FIELDS}


class MetricsStore:
    """线程安全的指标计数器。写失败只记日志，绝不影响主流程。"""

    def __init__(self, path=None, backfill: bool = False):
        self.path = Path(path) if path else METRICS_FILE
        self._lock = threading.Lock()
        self._data = self._load()
        self._roll_day()
        if backfill:
            self.backfill_from_records()

    # ---------- 历史灌入 ----------

    def backfill_from_records(self) -> dict:
        """首次使用时把 greet_records/reply_records 里的明细换算成累计计数。

        不做这一步，界面里的"累计投递"会是 0，而用户实际已经投了几百个 —
        指标看着就是坏的。只跑一次，靠 backfilled 标记挡住重复执行。
        """
        with self._lock:
            if self._data.get("backfilled"):
                return {"skipped": True}
            try:
                from boss_bot.reply_record import _get_greet_store, _get_reply_store
                greets = _get_greet_store().get_all()
                replies = _get_reply_store().get_all()
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"指标回填失败（不影响运行）: {e}")
                return {"skipped": True}
            if not greets and not replies:
                # 明细还没加载出来就写标记，会把累计值永久卡在 0
                return {"skipped": True}
            today = date.today().isoformat()
            for r in greets:
                if not getattr(r, "is_greeted", False):
                    continue
                idx = getattr(r, "account_index", 0) or 0
                self._bucket("all", idx)["greet_sent"] += 1
                if (r.timestamp or "").startswith(today):
                    self._bucket("today", idx)["greet_sent"] += 1
            seen_chats = set()
            for r in replies:
                idx = getattr(r, "account_index", 0) or 0
                sent = getattr(r, "reply_content", "") or ""
                if sent == "[简历已发送]":
                    self._bucket("all", idx)["resume_sent"] += 1
                    if (r.timestamp or "").startswith(today):
                        self._bucket("today", idx)["resume_sent"] += 1
                if getattr(r, "reply_intent", "") in ("invite_interview", "ask_interview"):
                    key = f"{idx}|{r.chat_name}"
                    if key in seen_chats or not r.chat_name:
                        continue
                    seen_chats.add(key)
                    day = (r.timestamp or "")[:10]
                    self._data["interview_chats"].setdefault(key, day)
                    self._bucket("all", idx)["interview"] += 1
                    if day == today:
                        self._bucket("today", idx)["interview"] += 1
            self._data["backfilled"] = today
            self._save()
            return {"skipped": False, "greet_records": len(greets),
                    "reply_records": len(replies)}

    # ---------- 读写 ----------

    def _load(self) -> dict:
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("date", date.today().isoformat())
                    data.setdefault("all", {})
                    data.setdefault("today", {})
                    data.setdefault("interview_chats", {})
                    return data
        except Exception:
            pass
        return {"date": date.today().isoformat(), "all": {},
                "today": {}, "interview_chats": {}}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, self._data)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"指标写入失败（不影响运行）: {e}")

    def _roll_day(self):
        """跨天把当日计数清零，累计不动。"""
        today = date.today().isoformat()
        if self._data.get("date") != today:
            self._data["date"] = today
            self._data["today"] = {}

    def _bucket(self, key: str, account_index: int) -> dict:
        bucket = self._data[key].setdefault(str(account_index), _blank())
        for f in FIELDS:
            bucket.setdefault(f, 0)
        return bucket

    # ---------- 记录 ----------

    def bump(self, account_index: int, field: str, n: int = 1):
        """累计 + 当日同时 +n。"""
        if field not in FIELDS:
            return
        with self._lock:
            self._roll_day()
            self._bucket("all", account_index)[field] += n
            self._bucket("today", account_index)[field] += n
            self._save()

    def add_interview(self, account_index: int, chat_name: str) -> bool:
        """面试数按会话去重 — 同一个 HR 反复问面试只算一次。

        Returns:
            True 表示这是个新会话（已计数），False 表示之前算过了
        """
        if not chat_name:
            return False
        key = f"{account_index}|{chat_name}"
        with self._lock:
            self._roll_day()
            chats = self._data["interview_chats"]
            if key in chats:
                return False
            chats[key] = date.today().isoformat()
            self._bucket("all", account_index)["interview"] += 1
            self._bucket("today", account_index)["interview"] += 1
            self._save()
            return True

    # ---------- 查询 ----------

    def snapshot(self, account_indexes: list = None) -> dict:
        """取指标快照。

        Args:
            account_indexes: 要统计的账号下标列表；None = 全部账号合计

        Returns:
            {"date": ..., "daily": {...}, "total": {...},
             "interview_chats_today": n, "interview_chats_total": n,
             "by_account": {"0": {"daily":..,"total":..}}}
        """
        with self._lock:
            self._roll_day()
            wanted = ([str(i) for i in account_indexes] if account_indexes is not None
                      else sorted(set(self._data["all"]) | set(self._data["today"])))
            daily, total = _blank(), _blank()
            by_account = {}
            for idx in wanted:
                d = self._bucket("today", int(idx))
                t = self._bucket("all", int(idx))
                by_account[idx] = {"daily": dict(d), "total": dict(t)}
                for f in FIELDS:
                    daily[f] += d[f]
                    total[f] += t[f]
            chats = self._data["interview_chats"]
            today = self._data["date"]
            wanted_int = set(wanted)
            chats_today = sum(1 for k, v in chats.items()
                              if v == today and k.split("|", 1)[0] in wanted_int)
            chats_total = sum(1 for k in chats
                              if k.split("|", 1)[0] in wanted_int)
            return {
                "date": today,
                "daily": daily,
                "total": total,
                "by_account": by_account,
                "interview_chats_today": chats_today,
                "interview_chats_total": chats_total,
            }


_store = None
_store_lock = threading.Lock()


def get_metrics(path=None, backfill: bool = True) -> MetricsStore:
    """全局单例（写入方用；测试可传 path 隔离）。

    backfill=True 时首次会把历史记录换算成累计值。
    Web 端读指标请直接 new 一个 MetricsStore()，拿到的才是磁盘上的最新值 —
    单例只在打招呼/回复线程里写，不会重读文件。
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = MetricsStore(path=path, backfill=backfill)
    return _store
