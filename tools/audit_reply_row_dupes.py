# -*- coding: utf-8 -*-
"""只读：回复记录里到底有多少行是重复的、按哪一把键算的。

清数据之前必须先把"重复"说清楚：面板入库用的身份是 账号+会话+data-mid
（见 tests/test_reply_records_unique.py 那把锁），mid 缺失的老行只能按全文比。
两把键数出来不一样，混着报会把他要删的和不能删的算成一堆。

跑法：python tools/audit_reply_row_dupes.py
"""
import collections
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

D = json.load(io.open("data/reply_records.json", encoding="utf-8"))
行 = D["records"] if isinstance(D, dict) else D
print("回复记录 %d 行（文件里 total 字段=%s）" % (len(行), D.get("total") if isinstance(D, dict) else "-"))
print("字段样本:", sorted(k for k in 行[0] if k not in ("reply_content", "hr_message"))[:14])


def 分组(键函数, 名称):
    c = collections.Counter(键函数(r) for r in 行)
    重 = {k: v for k, v in c.items() if v > 1}
    多出来的 = sum(v - 1 for v in 重.values())
    print("\n按%s：%d 个键值，重复键 %d 个，多出的行 %d 条"
          % (名称, len(c), len(重), 多出来的))
    for k, v in sorted(重.items(), key=lambda x: -x[1])[:3]:
        print("   %d 份  %s" % (v, str(k)[:110]))
    return 多出来的


有mid = [r for r in 行 if r.get("mid")]
print("\n带 mid 的 %d 行 / 不带 mid 的 %d 行" % (len(有mid), len(行) - len(有mid)))

a = 分组(lambda r: (r.get("account_index"), r.get("chat_name"), r.get("mid")),
          "账号+会话+mid")
b = 分组(lambda r: (r.get("account_index"), r.get("chat_name"), r.get("mid"))
         if r.get("mid") else (r.get("account_index"), r.get("chat_name"),
                               r.get("time"), (r.get("reply_content") or "")[:80]),
         "有 mid 按 mid、没 mid 按 账号+会话+时间+正文前 80 字")

# 全文相同、时间不同的两份：那是同一句话被两个号/两次采集都记了一遍
按全文 = collections.Counter(
    (r.get("account_index"), r.get("chat_name"), (r.get("reply_content") or "").strip())
    for r in 行 if (r.get("reply_content") or "").strip())
同文 = {k: v for k, v in 按全文.items() if v > 1}
print("\n按账号+会话+回复全文（不看时间）：%d 个键有 2 份以上，多出行 %d 条"
      % (len(同文), sum(v - 1 for v in 同文.values())))
留痕 = [k for k, v in 同文.items() if v == 2]
print("   其中只出现两次的 %d 组——这种多半是「机器说过这句」再留一次痕，不能算脏"
      % len(留痕))
print("\n结论要分开写：a=%d（含无 mid 的老行互相撞）b=%d（面板那把锁口径）" % (a, b))
