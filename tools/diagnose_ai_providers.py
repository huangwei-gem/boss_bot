# -*- coding: utf-8 -*-
"""不可用 AI 接口的归因诊断：同一请求「走系统代理」vs「不走代理」各打一次。

为什么要这个：requests 在 Windows 上会读注册表的代理设置（不是只认环境变量），
所以只要系统代理开着，本项目的每一次 AI 请求都默认从 127.0.0.1:7897 出去。
"接口不可用"到底是接口本身的问题、还是代理出去的线路问题，必须同一个请求
两种网络各打一次才知道。

用法：
    python -X utf8 tools/diagnose_ai_providers.py                 # 只诊断体检判为不可用的
    python -X utf8 tools/diagnose_ai_providers.py --all           # 全部接口都打
    python -X utf8 tools/diagnose_ai_providers.py --name NVIDIA   # 名字过滤
退出码恒为 0（这是诊断，不是断言）。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import requests

TIMEOUT = 25


def current_proxies():
    """requests 实际会用的代理（含 Windows 注册表那一份）"""
    try:
        return dict(urllib.request.getproxies())
    except Exception as e:
        return {"error": str(e)}


def call(api_base, api_key, model, use_proxy, note=""):
    """对 {api_base}/chat/completions 发一次最小请求，返回状态与响应片段"""
    url = api_base.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": [{"role": "user", "content": "只回一个字：好"}],
            "max_tokens": 8, "temperature": 0}
    headers = {"Authorization": "Bearer " + api_key,
               "Content-Type": "application/json"}
    sess = requests.Session()
    sess.trust_env = bool(use_proxy)     # False = 忽略环境变量和注册表代理，直连
    proxies = None if not use_proxy else {}
    t0 = time.time()
    try:
        r = sess.post(url, json=body, headers=headers, timeout=TIMEOUT,
                      proxies=(proxies if use_proxy else {}))
        ms = int((time.time() - t0) * 1000)
        text = (r.text or "").replace("\n", " ")[:200]
        return {"tag": note or ("代理" if use_proxy else "直连"), "ok": r.status_code < 300,
                "status": r.status_code, "ms": ms, "body": text}
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return {"tag": note or ("代理" if use_proxy else "直连"), "ok": False,
                "status": type(e).__name__, "ms": ms, "body": str(e)[:200]}
    finally:
        sess.close()


def list_models(api_base, api_key, use_proxy):
    """GET {api_base}/models：官方模型清单里有没有我们填的那个 model"""
    url = api_base.rstrip("/") + "/models"
    sess = requests.Session()
    sess.trust_env = bool(use_proxy)
    try:
        r = sess.get(url, timeout=TIMEOUT,
                     headers={"Authorization": "Bearer " + api_key},
                     proxies=({} if use_proxy else {}))
        if r.status_code >= 300:
            return {"status": r.status_code, "body": r.text[:120], "ids": []}
        data = r.json()
        ids = [m.get("id") for m in (data.get("data") or data.get("models") or [])]
        return {"status": r.status_code, "ids": ids, "body": ""}
    except Exception as e:
        return {"status": type(e).__name__, "body": str(e)[:120], "ids": []}
    finally:
        sess.close()


def classify(res_proxy, res_direct, model, ids):
    """把两次结果归成一句人话的原因"""
    both_blocked = all(not r["ok"] for r in (res_proxy, res_direct))
    only_proxy = (not res_proxy["ok"]) and res_direct["ok"]
    only_direct = res_proxy["ok"] and (not res_direct["ok"])
    if res_direct["ok"]:
        return "代理/网络问题：直连能通，走代理失败" if only_proxy else "本来就能通（体检误判或已恢复）"
    if only_direct:
        return "反常：走代理能通、直连不通（本机出网被限制）"
    s = str(res_direct["status"])
    b = (res_direct["body"] or "") + (res_proxy["body"] or "")
    if s == "404":
        return "接口地址或模型名不对（404）" + (
            "；该服务可用模型见 /models" if ids else "")
    if "FreeTier" in b or "within OpenCode" in b:
        return "服务商策略限制：该免费额度只允许从 OpenCode 客户端内调用，第三方程序拿不到"
    if s == "403":
        return "鉴权/额度问题（403）：Key 无权限或额度用尽"
    if s == "401":
        return "Key 无效（401）"
    if "Model is unavailable" in b or s == "400":
        return "模型名不存在或已下线（400）"
    if both_blocked and "ProxyError" in (res_proxy["status"] + res_direct["status"]):
        return "代理本身不通"
    if "Timeout" in res_direct["status"] or "timed out" in res_direct["body"].lower():
        return "本机到该站点网络不通/超时（直连也是死路），不是代理造成"
    return f"未知：直连返回 {s} {res_direct['body'][:60]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="可用接口也一起打")
    ap.add_argument("--name", default="", help="只诊断名字含该串")
    ap.add_argument("--models", action="store_true", help="顺带拉一次官方模型清单")
    args = ap.parse_args()

    from boss_bot.unified_config import UnifiedConfig
    from boss_bot import ai_health

    proxies = current_proxies()
    print(f"[网络] requests 会读到的代理: {proxies or '（无）'}")
    print(f"[网络] 注册表 ProxyEnable 决定这一切；"
          f"直连对照组 = trust_env=False\n")

    # UnifiedConfig() 是空壳，接口清单要走 load()（和仪表盘同一份配置）
    cfg = UnifiedConfig.load()
    provs = list(cfg.ai.providers or [])
    health = {}
    hp = os.path.join("data", "ai_health.json")
    if os.path.exists(hp):
        health = json.load(open(hp, encoding="utf-8")).get("results", {})

    def bad(p):
        key = f"{p.api_base}|{p.model}|{p.name}"
        return health.get(key, {}).get("status") != "available"

    targets = [p for p in provs if (args.all or bad(p))]
    if args.name:
        targets = [p for p in targets if args.name.lower() in (p.name or "").lower()]
    print(f"[范围] 共 {len(provs)} 个接口，本次诊断 {len(targets)} 个\n")

    rows = []
    for p in targets:
        if not (p.api_key and p.api_base and p.model):
            print(f"── {p.name}: 配置不完整（api_base/model/key 有空），没发请求")
            rows.append({"name": p.name, "verdict": "配置不完整（空壳行）",
                         "proxy": None, "direct": None})
            continue
        r1 = call(p.api_base, p.api_key, p.model, True)
        r2 = call(p.api_base, p.api_key, p.model, False)
        ids = []
        if args.models:
            lm = list_models(p.api_base, p.api_key, False)
            ids = lm.get("ids") or []
            print(f"   /models 直连: {lm.get('status')}，清单 {len(ids)} 个"
                  + (f" 例:{ids[:6]}" if ids else f" {str(lm.get('body'))[:80]}"))
        verdict = classify(r1, r2, p.model, ids)
        print(f"── {p.name} [{p.model}] {p.api_base}")
        for r in (r1, r2):
            print(f"   {r['tag']:>4}: {'OK ' if r['ok'] else 'ERR'} "
                  f"status={r['status']} {r['ms']}ms {r['body'][:110]}")
        print(f"   归因: {verdict}")
        if ids and p.model not in ids:
            near = [i for i in ids if any(tok in str(i).lower()
                                          for tok in p.model.lower().split("-")[:2])]
            print(f"   模型名 {p.model!r} 不在官方清单；相近的有 {near[:8]}")
        rows.append({"name": p.name, "model": p.model, "base": p.api_base,
                     "proxy": r1, "direct": r2, "verdict": verdict})

    print("\n================ 汇总 ================")
    groups = {}
    for r in rows:
        groups.setdefault(r["verdict"], []).append(r["name"])
    for v, names in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(names):>2} 个 | {v}")
        print(f"       {names}")
    json.dump({"proxies": proxies, "rows": rows},
              open(os.path.join("tools", "ai_provider_diagnosis.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n明细：tools/ai_provider_diagnosis.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
