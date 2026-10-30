# -*- coding: utf-8 -*-
"""AI 自定义筛选：他给一份要求文件，判分时逐条对照这份要求。

存哪：`data/ai_screen_doc.json`，全局一份。文件说的是"他要什么样子的活"，
两个号看的是同一个方向，按号各存一份只会出现"号 1 按新要求判、号 2 还按旧的"。
不塞进 bot_config.json：那份配置每次保存都整份重写、还留几十份备份，
几百 KB 的要求文档进去会把配置和备份一起撑大，人也不敢再手改配置。

谁能读：`GreetEngine._build_prompt` 每次判分现读（带 mtime 缓存），所以面板上
换了文件下一单就按新文件判，不用重启；`_make_cache_key` 把文档指纹算进缓存键，
不然同一岗位会命中旧文件下判出来的旧结论——那正是"改了不生效"。
"""

import hashlib
import io
import json
import threading
from pathlib import Path

from boss_bot.unified_config import resolve_path, write_json_atomic

文件名 = "ai_screen_doc.json"

# 文档再长也不把提示词撑爆：实测判分正文 1600 token 是预算下限，
# 要求文件给 2 万字已经够一整份岗位说明+个人要求，再长就该他自己精简。
最大字数 = 20000

_锁 = threading.RLock()
_缓存 = {"戳": None, "doc": None}


def _空() -> dict:
    return {"name": "", "text": "", "hash": "", "updated_at": 0, "chars": 0}


def 路径(目录: str = "") -> Path:
    return Path(resolve_path(目录 or Path("data"))) / 文件名


def 指纹(text: str) -> str:
    """文档判据：空文档的指纹是空串——"没有要求文件"本身也是一种口径。"""
    text = str(text or "").strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] if text else ""


def 读(目录: str = "") -> dict:
    try:
        with open(str(路径(目录)), "r", encoding="utf-8") as f:
            数据 = json.load(f)
    except (OSError, ValueError):
        return _空()
    if not isinstance(数据, dict):
        return _空()
    text = str(数据.get("text") or "")[:最大字数]
    if not text.strip():
        return _空()
    return {"name": str(数据.get("name") or "").strip(),
            "text": text,
            "hash": 指纹(text),
            "updated_at": float(数据.get("updated_at") or 0),
            "chars": len(text)}


def 存(name: str, text: str, 目录: str = "") -> dict:
    """存一份要求。空文档拒收：存进去等于"没有要求"，却会让界面显示已上传。"""
    text = str(text or "").strip()
    if not text:
        raise ValueError("文件里没读出文字（扫描版 PDF / 空文档都读不出），这份要求等于没给")
    截了 = len(text) > 最大字数
    if 截了:
        text = text[:最大字数]
    import time
    现在 = time.time()
    记 = {"name": str(name or "").strip() or "我的筛选要求",
          "text": text, "updated_at": 现在}
    write_json_atomic(路径(目录), 记)
    with _锁:
        _缓存["戳"] = None
        _缓存["doc"] = None
    回读 = 读(目录)
    if not 回读["text"]:
        raise ValueError("写进去了但从盘上读不回来，别当保存成功")
    回读["truncated"] = 截了
    return 回读


def 清空(目录: str = "") -> None:
    try:
        Path(路径(目录)).unlink()
    except FileNotFoundError:
        pass
    with _锁:
        _缓存["戳"] = None
        _缓存["doc"] = None


def 当前() -> dict:
    """判分线程与面板共用的那一份（按 mtime+size 缓存）。"""
    p = 路径()
    try:
        st = p.stat()
        戳 = (st.st_mtime_ns, st.st_size)
    except OSError:
        戳 = None
    with _锁:
        if _缓存["戳"] == 戳 and _缓存["doc"] is not None:
            return _缓存["doc"]
    doc = 读()
    with _锁:
        _缓存["戳"] = 戳
        _缓存["doc"] = doc
    return doc


def 当前指纹() -> str:
    return str(当前().get("hash") or "")


def 解析上传(文件名_: str, 内容: bytes) -> str:
    """把上传的文档读成纯文字。读不出就说读不出，不许回空串冒充"文件是空的"。"""
    后缀 = Path(str(文件名_ or "")).suffix.lower()
    if 后缀 == ".docx":
        return _读docx(内容)
    if 后缀 == ".pdf":
        return _读pdf(内容)
    if 后缀 == ".doc":
        raise ValueError("老版 .doc 读不了：另存成 .docx / pdf / txt 再传")
    文本 = _解码(内容)
    if not 文本.strip():
        raise ValueError("这个文件里没有文字")
    return 文本


def _解码(内容: bytes) -> str:
    for 编码 in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return 内容.decode(编码)
        except UnicodeDecodeError:
            continue
    return 内容.decode("utf-8", errors="replace")


def _读docx(内容: bytes) -> str:
    try:
        import docx
    except ImportError:
        raise ValueError("没装 python-docx，读不了 docx；换成 txt 或直接把要求粘进框里")
    try:
        文档 = docx.Document(io.BytesIO(内容))
    except Exception as e:
        raise ValueError("这个 docx 打不开（%s）" % (str(e) or type(e).__name__))
    段 = [str(p.text or "").strip() for p in 文档.paragraphs]
    for 表 in 文档.tables:
        for 行 in 表.rows:
            段.append(" | ".join(str(c.text or "").strip() for c in 行.cells))
    文本 = "\n".join(x for x in 段 if x)
    if not 文本.strip():
        raise ValueError("这个 docx 里没有文字（可能整份都是图片）")
    return 文本


def _读pdf(内容: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("没装 pypdf，读不了 pdf；换成 txt/docx 或直接把要求粘进框里")
    try:
        读 = PdfReader(io.BytesIO(内容))
        文本 = "\n".join(str(页.extract_text() or "") for 页 in 读.pages)
    except Exception as e:
        raise ValueError("这个 pdf 打不开（%s）" % (str(e) or type(e).__name__))
    if not 文本.strip():
        raise ValueError("这个 pdf 读不出文字（多半是扫描版），换成 docx/txt 再传")
    return 文本
