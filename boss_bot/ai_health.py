"""AI 接口体检 — 挨个发一条真实测试消息，只有真的回话了才算可用

配置里挂了 22 个接口，但 key 失效、模型改名、额度用光都看不出来，
运行时才会一个接一个报错。这里主动探一次，把结果落盘给前后端显示。

判定标准：能拿到非空的 content 才算 available。HTTP 200 但空回复也算不可用。
"""

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from boss_bot.unified_config import BASE_DIR, write_json_atomic

logger = logging.getLogger(__name__)

HEALTH_FILE = BASE_DIR / "data" / "ai_health.json"

# 测试消息要极短、必须回话、不依赖任何上下文
PROBE_MESSAGE = "只回复四个字：连接成功"
# 推理模型会先生成一大段 thinking 才输出正文，预算给小了 content 会是空的
PROBE_MAX_TOKENS = 200
PROBE_TIMEOUT = 45
# 逐个探测之间的间隔，避免同一 key 连续请求被限流
PROBE_INTERVAL = 0.5

STATUS_AVAILABLE = "available"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNTESTED = "untested"


def classify_error(text: str) -> str:
    """把异常文本归成一句人能看懂的原因。"""
    t = (text or "").lower()
    if any(k in t for k in ("401", "unauthorized", "invalid api key",
                            "invalid_api_key", "incorrect api key")):
        return "API Key 无效或已过期"
    if any(k in t for k in ("403", "forbidden", "permission")):
        return "无权限访问该模型"
    if any(k in t for k in ("404", "not found", "model", "does not exist")) \
            and "model" in t:
        return "模型名不存在"
    if "404" in t:
        return "接口地址(base_url)不对"
    if any(k in t for k in ("429", "rate limit", "quota", "insufficient")):
        return "额度用尽或被限流"
    if any(k in t for k in ("timeout", "timed out")):
        return "请求超时"
    if any(k in t for k in ("connection", "unreachable", "getaddrinfo",
                            "name or service not known", "ssl")):
        return "网络不通/域名解析失败"
    if any(k in t for k in ("500", "502", "503", "504", "server error")):
        return "服务端错误"
    return (text or "未知错误")[:120]


def probe_one(provider: dict, timeout: int = PROBE_TIMEOUT) -> dict:
    """真实调用一次某个接口。

    Args:
        provider: {"name","model","api_base","api_key"}，两种键名写法都认

    Returns:
        {"name","model","api_base","status","latency_ms","reply","error","reason"}
    """
    name = provider.get("name") or provider.get("api_base") or "未命名"
    api_key = provider.get("api_key") or provider.get("key") or ""
    api_base = provider.get("api_base") or provider.get("url") or ""
    model = provider.get("model") or ""
    out = {
        "name": name, "model": model, "api_base": api_base,
        "status": STATUS_UNAVAILABLE, "latency_ms": None,
        "reply": "", "error": "", "reason": "", "note": "",
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not api_key or not api_base or not model:
        out["reason"] = "配置不完整（缺 api_key / api_base / model）"
        return out
    start = time.time()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=api_base,
                        timeout=timeout, max_retries=0)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=PROBE_MAX_TOKENS,
            messages=[{"role": "user", "content": PROBE_MESSAGE}],
        )
        reply = ""
        in_reasoning = False
        try:
            msg = resp.choices[0].message
            reply = (msg.content or "").strip()
            if not reply:
                # 有的推理模型只把话放在 reasoning 字段里，也算接口是活的
                extra = (getattr(msg, "reasoning_content", None)
                         or getattr(msg, "reasoning", None) or "").strip()
                if extra:
                    reply, in_reasoning = extra, True
        except Exception:
            reply = ""
        out["latency_ms"] = int((time.time() - start) * 1000)
        if reply:
            out["status"] = STATUS_AVAILABLE
            out["reply"] = reply[:80]
            if in_reasoning:
                out["note"] = "正文为空，内容在 reasoning 字段"
        else:
            out["reason"] = "接口返回 200 但没有任何内容（把 max_tokens 提到 200 仍为空）"
    except Exception as e:
        out["latency_ms"] = int((time.time() - start) * 1000)
        out["error"] = str(e)[:300]
        out["reason"] = classify_error(str(e))
        logger.debug(f"AI 体检失败 [{name}]: {e}")
    return out


def probe_all(providers: list, indexes: list = None, timeout: int = PROBE_TIMEOUT,
              on_result=None) -> list:
    """逐个探测。

    Args:
        providers: 配置里的 AI 接口列表
        indexes: 只测这些下标；None = 全测
        on_result: 每测完一个回调 (index, provider, result)，供前端推进度
    """
    targets = list(indexes) if indexes is not None else list(range(len(providers)))
    results = []
    for i in targets:
        if not (0 <= i < len(providers)):
            continue
        res = probe_one(providers[i], timeout=timeout)
        results.append({"index": i, **res})
        if on_result:
            try:
                on_result(i, providers[i], res)
            except Exception as e:
                logger.debug(f"体检进度回调异常: {e}")
        time.sleep(PROBE_INTERVAL)
    return results


# ---------- 落盘 ----------

def load_health(path=None) -> dict:
    p = Path(path) if path else HEALTH_FILE
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.warning(f"读取 AI 体检结果失败: {e}")
    return {"results": {}, "summary": {}}


def save_health(data: dict, path=None):
    p = Path(path) if path else HEALTH_FILE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, data)
    except Exception as e:
        logger.warning(f"AI 体检结果写入失败: {e}")


def merge_results(existing: dict, results: list, providers: list) -> dict:
    """把本轮探测结果并进去（按接口名+地址认，配置顺序变了也不错位）。"""
    store = dict((existing or {}).get("results") or {})
    for r in results:
        key = provider_key(r)
        store[key] = r
    return {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "results": store,
        "summary": summarize(store),
    }


def provider_key(entry: dict) -> str:
    return f"{entry.get('api_base', '')}|{entry.get('model', '')}|{entry.get('name', '')}"


# ---------- 从真实调用里学 ----------
#
# 体检用的是 4 个字的极短提示词，有些接口 ping 得通、真岗位提示词却 30s 不回话。
# 这种接口体检永远标"可用"，容灾链每个岗位都要白等它 30~60s 然后"默认通过"，
# 等于悄悄不筛岗。所以把真实调用的超时也记进体检表，让 skip_unhealthy 能甩开它。

RUNTIME_STRIKES_TO_MARK = 2
_runtime_strikes: dict = {}
_runtime_lock = threading.Lock()


def report_runtime_result(provider, ok: bool, error: str = "", path=None):
    """把一次真实岗位分析的成败回写体检表。

    Args:
        provider: 有 name/model/api_base 属性的接口配置
        ok: 这次真调用是否拿到判断
        error: 失败原因文本
    """
    entry = {"name": getattr(provider, "name", "") or getattr(provider, "api_base", ""),
             "model": getattr(provider, "model", ""),
             "api_base": getattr(provider, "api_base", "")}
    key = provider_key(entry)
    timed_out = "超时" in (error or "") or "timeout" in (error or "").lower()
    # 截断是接口级稳定问题（thinking 吃满预算），和超时一样值得记 strike
    truncated = "截断" in (error or "")
    with _runtime_lock:
        data = load_health(path)
        store = dict(data.get("results") or {})
        cur = dict(store.get(key) or {})
        changed = False
        if ok:
            _runtime_strikes.pop(key, None)
            if cur.get("source") == "runtime":
                # 真调用成功了，撤掉运行时判的死刑，交回给下一次体检定夺
                store.pop(key, None)
                changed = True
        elif timed_out or truncated:
            kind = "超时" if timed_out else "正文被截断（max_tokens 不够）"
            strikes = _runtime_strikes.get(key, 0) + 1
            _runtime_strikes[key] = strikes
            if strikes >= RUNTIME_STRIKES_TO_MARK and cur.get("source") != "runtime":
                store[key] = dict(cur, **entry, status=STATUS_UNAVAILABLE,
                                  reason=f"真实岗位分析连续 {strikes} 次{kind}（短探活通过不算数）",
                                  error=(error or "")[:200],
                                  latency_ms=0, reply="",
                                  source="runtime",
                                  checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                changed = True
        if changed:
            save_health({"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                         "results": store, "summary": summarize(store)}, path=path)


def summarize(results: dict) -> dict:
    available = sum(1 for r in results.values()
                    if r.get("status") == STATUS_AVAILABLE)
    return {
        "total": len(results),
        "available": available,
        "unavailable": sum(1 for r in results.values()
                           if r.get("status") == STATUS_UNAVAILABLE),
        "avg_latency_ms": (
            int(sum(r["latency_ms"] for r in results.values()
                    if r.get("latency_ms")) /
            max(1, sum(1 for r in results.values() if r.get("latency_ms"))))
        ),
    }
