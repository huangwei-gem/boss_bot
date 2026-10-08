"""
BOSS 自动回复机器人 - 消息存储

将所有聊天消息保存为 JSON 文件，供前端展示和回复质量分析。
每个会话一个文件：messages/{chat_name}.json
内置 TTL 内存缓存，减少重复磁盘 IO。

完整对话消息存储：
- 每条消息包含：sender(HR/bot)、content、timestamp、job_name、is_mine
- 同时保存 HR 发来的消息和机器人回复的消息
- 提供 append_hr_message / append_bot_message 便捷方法
- 提供 get_full_dialog 获取完整对话历史（用于 AI 上下文）
"""

import json
import threading
import time
import re
from datetime import datetime
from pathlib import Path

from boss_bot.unified_config import write_json_atomic


def _safe_filename(name: str) -> str:
    return re.sub(r'[^\w\u4e00-\u9fff]', '_', name)[:50]


_JOB_NOISE = re.compile(r"(查看职位|查看详情|职位详情|了解更多|查看详情)")

# data-mid 是雪花 id（实测 15 位，390857683964420），同一会话内自上而下单调递增
_MID_RE = re.compile(r"^\d{6,}$")

# 打招呼算发送成功的状态（reply_record 里 skipped/failed 不算发出去过）
_GREET_SENT = ("applied", "completed")
_sent_cache: dict = {}


def _greet_records_path() -> Path:
    from boss_bot.reply_record import GREET_RECORDS_FILE
    return GREET_RECORDS_FILE


def _reply_records_path() -> Path:
    from boss_bot.reply_record import REPLY_RECORDS_FILE
    return REPLY_RECORDS_FILE


def _machine_sent_texts() -> dict:
    """按账号取「引擎发出去过的文本」→ 来源标签。

    招呼语只写 greet_records.json、回复只写 reply_records.json，都不写消息文件，
    所以线上抓回来的我方气泡没有自记条目可配对，只能拿这两份历史记录比对，
    才分得清哪条是机器发的、哪条是人自己敲的。
    两份文件都是 MB 级，按 (路径, mtime, size) 缓存，一次同步不重复读盘。
    """
    paths = (_greet_records_path(), _reply_records_path())
    stamp = []
    for p in paths:
        try:
            st = p.stat()
            stamp.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            stamp.append((str(p), 0, 0))
    key = tuple(stamp)
    cached = _sent_cache.get("v")
    if cached and cached[0] == key:
        return cached[1]

    grouped: dict = {}

    def put(acc, text, source):
        text = (text or "").strip()
        if text:
            grouped.setdefault(int(acc or 0), {}).setdefault(text, source)

    try:
        with open(paths[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        for rec in (data.get("records") or []):
            if rec.get("status") in _GREET_SENT:
                put(rec.get("account_index"), rec.get("actual_greeting_sent"), "greet")
    except Exception:
        pass
    try:
        with open(paths[1], "r", encoding="utf-8") as f:
            data = json.load(f)
        for rec in (data.get("records") or []):
            if not rec.get("is_skipped"):
                put(rec.get("account_index"), rec.get("reply_content"),
                    rec.get("reply_source") or "ai")
    except Exception:
        pass
    _sent_cache["v"] = (key, grouped)
    return grouped


class MessageStore:
    """消息存储（JSON 文件持久化 + 内存缓存，线程安全）

    会话身份 = 昵称 + 岗位：BOSS 侧栏只显示"陈女士"，而同时聊着两个岗位的
    陈女士是两段对话（实测账号0 的 34 个会话里有 4 组重名昵称）。只按昵称存
    会把两段对话合进一个文件，前端再怎么排都对不上线上。
    """

    CACHE_TTL = 30  # 缓存有效期（秒）

    def __init__(self, base_dir=None, account_index: int = 0):
        from boss_bot.config import BASE_DIR, TEST_MODE
        # 测试模式下隔离目录，避免 mock 测试数据污染前端真实消息列表
        dirname = "messages_test" if TEST_MODE else "messages"
        self.base_dir = Path(base_dir) if base_dir else Path(BASE_DIR) / dirname
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # 多账号：BOSS 只显示"杨女士"这类称呼，两个账号聊到同名 HR 时
        # 必须落到不同文件，否则互相覆盖对话历史
        self.account_index = account_index
        self._locks = {}
        self._global_lock = threading.Lock()
        # 内存缓存：chat_id -> (data dict, 时间戳)
        self._cache = {}
        # 跨账号读取时的 chat_id -> path 映射缓存
        self._alias_cache = {}

    @staticmethod
    def job_key(job_name: str) -> str:
        """身份尾串：去掉"查看职位"这类尾巴噪声，同一岗位两次读取稳定同键"""
        return _safe_filename(_JOB_NOISE.sub("", job_name or "").strip())[:24]

    @classmethod
    def chat_id(cls, chat_name: str, company: str = "", job_name: str = "") -> str:
        """会话身份 = 姓名 +（公司 或 岗位）

        优先公司：它就挂在侧栏那一行上（.name-box 的第二截），不需要"点开才知道是谁"。
        实测 34 行里只用姓名有 4 组重名，加上公司后 34/34 唯一。
        公司取不到时退到岗位名（详情头部读得到）。
        """
        name = (chat_name or "").strip()
        key = cls.job_key(company or job_name)
        return f"{name}#{key}" if key else name

    def _get_lock(self, key: str) -> threading.Lock:
        with self._global_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]

    @property
    def _prefix(self) -> str:
        """账号0 沿用原文件名，历史数据不用迁移；其余账号加 aN_ 前缀"""
        return "" if self.account_index <= 0 else f"a{self.account_index}_"

    def _path(self, chat_name: str, company: str = "", job_name: str = "") -> Path:
        return self.base_dir / f"{self._prefix}" \
                               f"{_safe_filename(self.chat_id(chat_name, company, job_name))}.json"

    def _own_files(self):
        """本账号的文件：文件名前缀必须正好等于本账号的前缀。

        账号0 的前缀是空的，glob 会把 a1_ 的文件一起吃进来；反过来账号1 的 glob
        也会把账号0 那些无前缀的文件当成自己的（实测 16 个 a1_ 存档就是这么被
        账号0 的对话覆盖过内容的）。所以两个方向都要按前缀筛，不能只筛一头。
        """
        for path in sorted(self.base_dir.glob("*.json")):
            if path.name.endswith(".meta.json"):
                continue
            m = re.match(r"^a(\d+)_", path.name)
            file_index = int(m.group(1)) if m else 0
            if file_index != self.account_index:
                continue
            yield path

    def _candidates(self, chat_name: str, company: str = "",
                    job_name: str = "") -> list:
        """按身份挑该读哪个文件，只在本账号的文件里找。

        同昵称是两段对话，给了身份尾串（公司/岗位）就必须对得上，否则两个
        "陈女士"会互相串，界面内容就跟 BOSS 对不上了。
        跨账号兜底（2026-10-04 删掉）看着是给 Web 端"全部账号"用的，实际是串号
        的入口：merge_messages 用这里返回的路径读、用 _path 写，于是账号1 同步一次
        就把账号0 的整段对话复制进自己的存档。面板列会话走 get_all_chats_detail
        （全目录扫描 + 每条标注 account_index），读详情按 ?account=N 建实例，
        都不需要在这里跨号。
        """
        want = self.job_key(company or job_name)
        exact, loose = [], []
        for path in self._own_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            if data.get("chat_name") != chat_name:
                continue
            have = self.job_key(data.get("company")
                                or data.get("job_name") or "")
            if want and have and have == want:
                exact.append(path)
            elif not want or not have:
                loose.append(path)
        return exact + loose

    def _read_path(self, chat_name: str, company: str = "",
                   job_name: str = "") -> Path:
        """读取用路径：先按 姓名+公司 找，再退到能对上身份的老文件。"""
        cid = self.chat_id(chat_name, company, job_name)
        own = self._path(chat_name, company, job_name)
        if own.exists():
            return own
        hit = self._alias_cache.get(cid)
        if hit and hit.exists():
            return hit
        found = self._candidates(chat_name, company, job_name)
        if found:
            self._alias_cache[cid] = found[0]
            return found[0]
        return own

    def _cache_valid(self, chat_name: str) -> bool:
        entry = self._cache.get(chat_name)
        if not entry:
            return False
        return (time.time() - entry[1]) < self.CACHE_TTL

    def _cache_get(self, chat_name: str):
        return self._cache[chat_name][0]

    def _cache_put(self, chat_name: str, data: dict):
        self._cache[chat_name] = (data, time.time())

    def save_messages(self, chat_name: str, messages: list,
                      job_name: str = "", company: str = ""):
        """保存一个会话的完整消息列表（覆盖式）"""
        cid = self.chat_id(chat_name, company, job_name)
        lock = self._get_lock(cid)
        with lock:
            path = self._path(chat_name, company, job_name)
            data = {
                "chat_name": chat_name,
                "chat_id": cid,
                "account_index": self.account_index,
                "company": company,
                "job_name": job_name,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "messages": messages,
            }
            try:
                write_json_atomic(path, data)
                self._cache_put(cid, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"保存消息失败: {e}")

    def append_message(self, chat_name: str, message: dict,
                       job_name: str = "", company: str = ""):
        """追加一条消息到会话文件"""
        cid = self.chat_id(chat_name, company, job_name)
        lock = self._get_lock(cid)
        with lock:
            path = self._path(chat_name, company, job_name)
            try:
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                else:
                    data = {
                        "chat_name": chat_name,
                        "chat_id": cid,
                        "account_index": self.account_index,
                        "company": company,
                        "job_name": job_name,
                        "updated_at": "",
                        "messages": [],
                    }
                data["messages"].append(self._norm_msg(message))
                data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if job_name:
                    data["job_name"] = job_name
                if company:
                    data["company"] = company
                write_json_atomic(path, data)
                self._cache_put(cid, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"追加消息失败: {e}")

    @staticmethod
    def _mid_of(msg: dict) -> str:
        """BOSS 给每条消息的 data-mid（旧字段名 msg_id 也认）"""
        return str(msg.get("mid") or msg.get("msg_id") or "").strip()

    def _norm_msg(self, msg: dict) -> dict:
        """统一字段：正文、方向、mid、kind。

        kind 决定前端怎么画，也是"和 BOSS 一致"的关键：
        - bubble：线上真实气泡
        - card：线上那张 .text-content 为空、innerText 有内容的卡片（简历/PK 分析/职位卡）
        - system：BOSS 自己的系统提示
        - action：我们自记的动作标记（如 [简历已发送]），线上并没有这条气泡
        把 card/action 当正文存，界面就会长出线上看不到的行。
        """
        out = dict(msg)
        text = (out.get("content") or out.get("text") or "").strip()
        # card_text 优先于 block：block 是线上整块原文（带分段时间标签），
        # 已归一化过的消息再读一次会因取 block 把刚去掉的时间标签带回来
        block = (out.get("card_text") or out.get("block") or "").strip()
        mid = self._mid_of(out)
        if mid:
            out["mid"] = mid
            out.pop("msg_id", None)
        if "is_mine" not in out:
            out["is_mine"] = out.get("sender") in ("me", "bot")
        if "isFriend" not in out:
            out["isFriend"] = not out["is_mine"]
        if "sender" not in out:
            out["sender"] = "me" if out["is_mine"] else "hr"

        kind = out.get("kind")
        msg_type = out.get("msg_type")
        if not kind:
            if out.get("is_action"):
                kind = "action"
            elif not text and block:
                kind = "card"
            elif msg_type in ("image", "resume", "file", "job", "card"):
                kind = "card"
            elif msg_type == "system" or out.get("is_system") or out.get("sender") == "system":
                kind = "system"
            else:
                kind = "bubble"
        out["kind"] = kind
        out["content"] = text
        out["text"] = text
        if kind in ("card", "system"):
            ct = text or block
            # 卡片的整块文本开头会把分段时间标签一起带进来（"08:56 你与该职位…PK…"），
            # 而时间已经单独存在 time 字段里，线上也只在分段处显示一次，不去掉就重复
            lbl = (out.get("time") or "").strip()
            if lbl and ct.startswith(lbl):
                ct = ct[len(lbl):].lstrip()
            out["card_text"] = ct
        return out

    @classmethod
    def _order_by_mid(cls, msgs: list) -> list:
        """按 data-mid 升序 —— 唯一可靠的顺序键。

        线上时间标签只在分段处出现（实测存量 306 条里 123 条没有 time），
        而且 "昨天 21:54"、"09-23 21:37" 这类文本解析不出时间戳、"HH:MM" 又会被
        当成"今天"，按它重排必然把昨天的消息排到今天之后。mid 是雪花 id，
        同一会话内自上而下单调递增（实测三组会话全部验证）。
        没有 mid 的（老数据、自记标记）沿用上一条有 mid 消息的位置，保持稳定。
        """
        first = 0
        for m in msgs:
            mid = cls._mid_of(m)
            if mid.isdigit():
                first = int(mid)
                break
        last = 0
        keyed = []
        for idx, m in enumerate(msgs):
            mid = cls._mid_of(m)
            if mid.isdigit():
                last = int(mid)
                keyed.append((last, idx))
            else:
                keyed.append(((last or first) if (last or first) else 0, idx))
        order = sorted(range(len(msgs)), key=lambda i: keyed[i])
        return [msgs[i] for i in order]

    @staticmethod
    def _msg_key(msg: dict) -> str:
        """构造消息去重键。

        优先用 BOSS 给每条消息的 data-mid（线上唯一且稳定）；没有 mid 的
        旧数据退回 content+time。HR 重复发同一句话时 content+time 会把两条
        合并成一条，导致界面和线上对不上。
        """
        mid = str(msg.get("mid") or "").strip()
        if mid:
            return f"mid|{mid}"
        # 卡片正文在 card_text/block 里、content 是空的：不带上它，两张不同的
        # 卡片键都是 "|”，后一张会在合并时被当成重复丢掉（简历卡片就是这么丢的）
        content = (msg.get("content") or msg.get("text")
                   or msg.get("card_text") or msg.get("block") or "").strip()
        t = (msg.get("time") or msg.get("timestamp") or "").strip()
        return f"{content}|{t}"

    def _reconcile_outgoing(self, msgs: list) -> list:
        """把「自记的 bot 那条」和「线上抓回来的同一条」并成一条，并保住归属。

        bot 发出去时我们自记一条（没有 mid），下次同步从页面抓回来的是带 mid 的
        item-myself，_norm_msg 只能把它标成 me。两条去重键不同（content|time 与
        mid|…），所以既去不掉、还把 AI 发的算成人工发的。

        判据是同一会话内正文完全相同：把线上 mid 回填到自记那条上（此后按 mid 去重，
        重复同步不再长第二条），丢弃抓取那条。对不上任何自记记录的抓取气泡才是人工。
        同文多条时按列表位置就近配对，避免"人工又敲了一遍同样的话"被错并。

        剩下的对不上号的再比一次历史记录：招呼语和早期回复都只写 greet_records /
        reply_records，不写消息文件，文本与其中"发出去过"的记录完全相同就判 bot
        （来源沿用记录里的，界面才分得清是招呼还是规则回复；存量数据也靠这条收敛）。
        """
        slots: dict = {}
        for i, m in enumerate(msgs):
            if m.get("sender") == "bot" and not m.get("mid") and (m.get("content") or "").strip():
                slots.setdefault(m["content"].strip(), []).append(i)
        drop = set()
        for i, m in enumerate(msgs):
            if not m.get("is_mine") or m.get("sender") != "me" or not m.get("mid"):
                continue
            c = (m.get("content") or "").strip()
            cand = slots.get(c)
            if not cand:
                continue
            j = min(cand, key=lambda k: abs(k - i))
            cand.remove(j)
            msgs[j]["mid"] = m["mid"]
            if not msgs[j].get("time") and m.get("time"):
                msgs[j]["time"] = m["time"]
            drop.add(i)
        greetings = _machine_sent_texts().get(self.account_index) or {}
        if greetings:
            for m in msgs:
                if m.get("is_mine") and m.get("sender") == "me" \
                   and not m.get("reply_source"):
                    source = greetings.get((m.get("content") or "").strip())
                    if source:
                        m["sender"] = "bot"
                        m["reply_source"] = source
        return [m for i, m in enumerate(msgs) if i not in drop]

    @staticmethod
    def _same_sequence(a: list, b: list) -> bool:
        """两条消息列表按去重键 + 归属逐条比对是否完全一致。"""
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            if (MessageStore._msg_key(x), x.get("sender"), x.get("content")) != \
               (MessageStore._msg_key(y), y.get("sender"), y.get("content")):
                return False
        return True

    def merge_messages(self, chat_name: str, new_messages: list,
                       job_name: str = "", company: str = "") -> int:
        """把页面读到的消息并进「姓名+公司」这一路会话。

        去重按 data-mid（线上每条都有、唯一且稳定）；没有 mid 的退到 content+time。
        顺序按 mid 升序，不再按时间标签重排 —— 时间线上只在分段处出现，
        "昨天 21:54" 这类还解析不成时间戳，重排就是把顺序搞乱的元凶。

        Args:
            chat_name: 聊天对象称呼（BOSS 侧栏显示的名字）
            new_messages: 页面读取的新消息列表
            job_name: 岗位名称（详情头部读到，公司缺失时当身份）
            company: 公司名（侧栏行上直接读到的身份判据）

        Returns:
            合并后的消息总数
        """
        if not new_messages and not self._read_path(chat_name, company, job_name).exists():
            return 0

        cid = self.chat_id(chat_name, company, job_name)
        lock = self._get_lock(cid)
        with lock:
            read_path = self._read_path(chat_name, company, job_name)
            data = None
            existing_msgs: list = []
            try:
                if read_path.exists():
                    with open(read_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    existing_msgs = data.get("messages", []) or []
            except Exception:
                data = None
            if data is None:
                data = {"chat_name": chat_name}

            seen_keys = set()
            merged = []
            for raw in list(existing_msgs) + list(new_messages):
                norm = self._norm_msg(raw)
                # 线上真的什么都没有才丢；卡片这类正文为空但有整块文本的要留下
                if not norm["text"] and not norm.get("card_text") and not norm.get("mid"):
                    continue
                key = self._msg_key(norm)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                merged.append(norm)

            merged = self._reconcile_outgoing(merged)
            before = self._order_by_mid(
                [self._norm_msg(m) for m in existing_msgs])
            merged = self._order_by_mid(merged)

            # 空同步且一条没变时不写盘：轮询每次都重写用户数据文件没必要
            if not new_messages and self._same_sequence(before, merged):
                return len(merged)

            data["chat_name"] = chat_name
            data["chat_id"] = cid
            data["account_index"] = self.account_index
            if job_name:
                data["job_name"] = job_name
            if company:
                data["company"] = company
            data["messages"] = merged
            data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            path = self._path(chat_name, company, job_name)
            try:
                write_json_atomic(path, data)
                self._cache_put(cid, data)
                import logging
                logging.getLogger(__name__).info(
                    f"[merge_messages] 会话={cid} "
                    f"已存={len(existing_msgs)} 本次读取={len(new_messages)} "
                    f"合并后={len(merged)}")
                return len(merged)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"合并消息失败: {e}")
                return len(self.get_messages(chat_name, job_name, company))

    def append_hr_message(self, chat_name: str, content: str,
                          job_name: str = "", timestamp: str = "",
                          extra: dict = None, company: str = "") -> dict:
        """追加一条 HR 发来的消息（左侧气泡）。

        Args:
            chat_name: 聊天对象名称
            content: 消息文本内容
            job_name: 岗位名称
            timestamp: 时间戳，为空则取当前时间
            extra: 额外字段（如 raw_time）

        Returns:
            构造的消息 dict
        """
        # 统一时间戳格式为 YYYY-MM-DD HH:MM:SS
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        time_short = ts[11:16] if len(ts) >= 16 else datetime.now().strftime("%H:%M")
        msg = {
            "sender": "hr",
            "content": content,
            "text": content,
            "is_mine": False,
            "kind": "bubble",
            "timestamp": ts,
            "time": time_short,
            "job_name": job_name,
        }
        if extra:
            msg.update(extra)
        self.append_message(chat_name, msg, job_name, company)
        return msg

    def append_bot_message(self, chat_name: str, content: str,
                           job_name: str = "", timestamp: str = "",
                           reply_source: str = "", action: str = "text",
                           extra: dict = None, company: str = "") -> dict:
        """追加一条机器人回复消息（右侧气泡）。

        Args:
            chat_name: 聊天对象名称
            content: 回复内容
            job_name: 岗位名称
            timestamp: 时间戳，为空则取当前时间
            reply_source: 回复来源（rule/intent/ai/default/skip）
            action: 动作类型（text/resume/skip）
            extra: 额外字段

        Returns:
            构造的消息 dict
        """
        # 统一时间戳格式为 YYYY-MM-DD HH:MM:SS
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 真的发出去的文字线上有对应气泡；[简历已发送] 这类是引擎自记的动作，
        # 标成 action 让前端画成居中小字，而不是伪装成一条线上气泡
        kind = "bubble" if action in ("text", "text_fallback") else "action"
        msg = {
            "sender": "bot",
            "content": content,
            "text": content,
            "is_mine": True,
            "kind": kind,
            "source": "bot",
            "reply_source": reply_source,
            "action": action,
            "timestamp": ts,
            "time": ts[11:16] if len(ts) >= 16 else datetime.now().strftime("%H:%M"),
            "job_name": job_name,
        }
        if extra:
            msg.update(extra)
        self.append_message(chat_name, msg, job_name, company)
        return msg

    def append_skip_record(self, chat_name: str, skip_reason: str,
                           job_name: str = "", received_message: str = "",
                           timestamp: str = "", company: str = "") -> dict:
        """追加一条跳过记录到会话（用于完整对话上下文）。

        Args:
            chat_name: 聊天对象名称
            skip_reason: 跳过原因
            job_name: 岗位名称
            received_message: 收到的消息（可能为空）
            timestamp: 时间戳

        Returns:
            构造的消息 dict
        """
        # 统一时间戳格式为 YYYY-MM-DD HH:MM:SS
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = {
            "sender": "system",
            "content": f"[跳过] {skip_reason}",
            "text": f"[跳过] {skip_reason}",
            "is_mine": False,
            "kind": "action",
            "is_skipped": True,
            "skip_reason": skip_reason,
            "received_message": received_message,
            "timestamp": ts,
            "time": ts[11:16] if len(ts) >= 16 else datetime.now().strftime("%H:%M"),
            "job_name": job_name,
        }
        self.append_message(chat_name, msg, job_name, company)
        return msg

    def get_messages(self, chat_name: str, job_name: str = "",
                     company: str = "") -> list:
        """读取一路会话的所有消息（优先走缓存）"""
        cid = self.chat_id(chat_name, company, job_name)
        if self._cache_valid(cid):
            return self._cache_get(cid).get("messages", [])
        path = self._read_path(chat_name, company, job_name)
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache_put(cid, data)
                return data.get("messages", [])
        except Exception:
            pass
        return []

    def get_full_dialog(self, chat_name: str, limit: int = 50,
                        job_name: str = "", company: str = "") -> list:
        """获取完整对话历史（用于 AI 上下文）。

        Args:
            chat_name: 聊天对象名称
            limit: 最多返回消息数
            job_name: 岗位名称（公司缺失时参与定位会话）
            company: 公司名（与称呼一起定位会话）

        Returns:
            消息列表，每条含 sender/content/timestamp/is_mine 等字段
        """
        msgs = self.get_messages(chat_name, job_name, company)
        if limit > 0 and len(msgs) > limit:
            return msgs[-limit:]
        return msgs

    def get_chat_list(self) -> list:
        """获取所有有消息记录的会话列表（含各账号的会话）

        同昵称不同岗位是两条会话，所以列表里同时给出 chat_id（身份）和
        chat_name（显示用），前端点进来按 chat_id 定位，才不会串到别人的对话里。
        """
        result = []
        for path in self.base_dir.glob("*.json"):
            if path.name.endswith(".meta.json"):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                msgs = data.get("messages", [])
                m = re.match(r"^a(\d+)_", path.name)
                chat_name = data.get("chat_name", path.stem)
                job_name = data.get("job_name", "")
                company = data.get("company", "")
                result.append({
                    "chat_name": chat_name,
                    "chat_id": data.get("chat_id")
                                 or self.chat_id(chat_name, company, job_name),
                    "company": company,
                    "job_name": job_name,
                    "account_index": data.get(
                        "account_index", int(m.group(1)) if m else 0),
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(msgs),
                    "last_message": msgs[-1] if msgs else None,
                })
            except Exception:
                pass
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result

    def get_chat_detail(self, chat_name: str, job_name: str = "",
                        company: str = "") -> dict:
        """获取一路会话的完整详情（含消息列表，优先走缓存）"""
        cid = self.chat_id(chat_name, company, job_name)
        if self._cache_valid(cid):
            return self._cache_get(cid)
        path = self._read_path(chat_name, company, job_name)
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._cache_put(cid, data)
                return data
        except Exception:
            pass
        return {"chat_name": chat_name, "chat_id": cid, "company": company,
                "job_name": job_name, "updated_at": "", "messages": []}

    def get_all_chats_detail(self) -> list:
        """获取所有会话的完整详情列表（用于前端聊天界面展示）。

        每个会话包含：
        - chat_name: 聊天对象称呼（BOSS 侧栏显示的名字）
        - chat_id: 会话身份（姓名+岗位，前端据此定位）
        - job_name: 岗位名称
        - updated_at: 最后更新时间
        - message_count: 消息总数
        - unread_count: 未读数（HR 发送且未读的消息数）
        - last_message: 最新消息内容
        - last_time: 最新消息时间
        - messages: 完整消息列表

        Returns:
            按最后更新时间倒序排列的会话详情列表
        """
        result = []
        for path in self.base_dir.glob("*.json"):
            if path.name.endswith(".meta.json"):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                msgs = data.get("messages", [])
                chat_name = data.get("chat_name", path.stem)
                job_name = data.get("job_name", "")
                company = data.get("company", "")
                m = re.match(r"^a(\d+)_", path.name)
                account_index = data.get(
                    "account_index", int(m.group(1)) if m else 0)
                # 计算未读数：sender=hr 且未读标记
                # 未读只认 BOSS 自己报的那个数（回复轮次扫侧栏时记进 unread_on_boss）。
                # 以前这里是"本地数 sender==hr 且没标已读的条数"，实测账号1 显示 53、
                # 账号2 显示 118，而 BOSS 自己说 13 / 1 —— 我们只存了点开的那 2~3 条，
                # 从没点开的会话里的 HR 消息永远算未读，越攒越离谱。宁可报"不知道"，
                # 也不给一个和线上对不上的数：没观察到就是 None，界面不画红点。
                unread_count = data.get("unread_on_boss")
                if unread_count is not None:
                    unread_count = int(unread_count)
                # 最新消息
                last_msg = msgs[-1] if msgs else None
                last_time = ""
                last_content = ""
                if last_msg:
                    last_time = last_msg.get("timestamp", "") or last_msg.get("time", "")
                    last_content = (last_msg.get("content", "") or last_msg.get("text", "")
                                    or last_msg.get("card_text", ""))
                result.append({
                    "chat_name": chat_name,
                    "chat_id": data.get("chat_id")
                                 or self.chat_id(chat_name, company, job_name),
                    "account_index": account_index,
                    "company": company,
                    "job_name": job_name,
                    "updated_at": data.get("updated_at", ""),
                    "message_count": len(msgs),
                    "unread_count": unread_count,
                    "pinned": bool(data.get("pinned")),
                    "last_message": last_content,
                    "last_time": last_time,
                    "messages": msgs,
                })
            except Exception:
                pass
        result.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return result

    def renormalize(self) -> int:
        """把已存的文件按当前归一化规则重写一遍（迁移用，不开浏览器）。

        归一化规则会变（比如卡片文本不再重复分段时间标签），存量文件就得跟着刷一次，
        否则界面上老会话还是按旧规则显示，和新采到的对不齐。
        """
        count = 0
        for path in self._own_files():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            msgs = data.get("messages", []) or []
            new_msgs = [self._norm_msg(m) for m in msgs]
            if new_msgs == msgs:
                continue
            data["messages"] = new_msgs
            write_json_atomic(path, data)
            count += 1
        return count

    def set_boss_unread(self, chat_name: str, count, job_name: str = "",
                        company: str = ""):
        """记下"上一次在 BOSS 侧栏上看到这一路有几个未读"。

        只在扫到线上真实标记时调用（回复轮次扫侧栏 = N，点进去读完 = 0）。
        面板自己的 mark_read 不动它：那是我们本地的已读，BOSS 那边没变。
        """
        cid = self.chat_id(chat_name, company, job_name)
        lock = self._get_lock(cid)
        with lock:
            path = self._read_path(chat_name, company, job_name)
            if not path.exists():
                return
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["unread_on_boss"] = int(count)
                write_json_atomic(path, data)
                self._cache_put(cid, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"记录线上未读数失败: {e}")

    def set_pinned(self, chat_name: str, pinned: bool, job_name: str = "",
                   company: str = ""):
        """记下"这一路在 BOSS 侧栏上是不是置顶的"。

        置顶 = 他标的"这一路我自己聊"（2026-10-08），所以这个标记必须落进存档：
        欠回复、主动跟进、补发简历和两个批量清剿工具走的都是存档，不看侧栏。
        只在值真的变了时才写文件——回复轮十秒一轮，没变就不碰磁盘。
        """
        cid = self.chat_id(chat_name, company, job_name)
        lock = self._get_lock(cid)
        with lock:
            path = self._read_path(chat_name, company, job_name)
            if not path.exists():
                return
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if bool(data.get("pinned")) == bool(pinned):
                    return
                data["pinned"] = bool(pinned)
                write_json_atomic(path, data)
                self._cache_put(cid, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"记录置顶标记失败: {e}")

    def mark_chat_read(self, chat_name: str, job_name: str = "",
                       company: str = ""):
        """标记一路会话的所有 HR 消息为已读。"""
        cid = self.chat_id(chat_name, company, job_name)
        lock = self._get_lock(cid)
        with lock:
            path = self._read_path(chat_name, company, job_name)
            try:
                if path.exists():
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for m in data.get("messages", []):
                        if m.get("sender") == "hr" or not m.get("is_mine", False):
                            m["is_read"] = True
                    data["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    write_json_atomic(path, data)
                    self._cache_put(cid, data)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"标记已读失败: {e}")
