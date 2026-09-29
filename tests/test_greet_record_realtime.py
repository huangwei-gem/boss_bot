"""打招呼记录实时性（BOSS 已投递 → 前端立刻可见）。

引擎侧在点发送那一刻就落库+推送（见 test_greet_failure_reason.RecordOnSendTest）。
这里管前端：实时推来的行必须进入数据模型 allGreetRecords，并且和历史记录
走同一套状态判定。纯字符串扫描证明不了"切一下筛选那一行还在不在"，所以把
index.html 里的函数抽出来交给 node 真跑。
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

INDEX_HTML = Path("flask-version/templates/index.html")


def extract_fn(src, name):
    """按大括号配对取出一个 function 声明（这几个函数体内没有字符串花括号）。"""
    m = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", src)
    if not m:
        raise AssertionError(f"index.html 里找不到 {name}()")
    start = src.index("{", m.start())
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():i + 1]
    raise AssertionError(f"{name}() 大括号没配对")


# 实时推送的原始 payload（greet_engine._emit_greet_event + app.greet_event_callback）
PUSH = {
    "job_name": "运营实习生", "company": "某公司", "salary": "5-6K",
    "status": "skip", "is_skipped": True,
    "skip_reason": "AI判定不匹配: 岗位只接受长期实习生",
    "url": "https://www.zhipin.com/job_detail/abc.html",
    "timestamp": "2026-09-29 10:00:00", "time": "10:00:00",
    "account_index": 1, "account_name": "账号2",
}

JS_CASES = r'''
const out = {};
// 1. 实时行必须进数据模型，而不只是插一段 DOM——否则一动筛选就凭空消失
addGreetRecord(PUSH);
out.model_len_after_push = allGreetRecords.length;
out.model_first_ts = (allGreetRecords[0] || {}).timestamp || '';
out.model_date = extractDate((allGreetRecords[0] || {}).timestamp || '');
// 2. 同一个岗位再推一次不该变成两行
addGreetRecord(PUSH);
out.model_len_after_dup = allGreetRecords.length;
// 3. 实时推送和落库记录两种词表要判成同一个状态
out.push_ai_skip = toGreetRow(PUSH).status;
out.hist_ai_skip = toGreetRow({status: 'skipped', is_skipped: true,
                               skip_reason: 'AI判定不匹配: 岗位只接受长期实习生'}).status;
out.push_error = toGreetRow({status: 'error', is_skipped: true,
                             skip_reason: '发送异常: 连接断开'}).status;
out.push_success = toGreetRow({status: 'success', is_skipped: false,
                               greeting: '您好'}).status;
out.hist_applied = toGreetRow({status: 'applied', is_greeted: true}).status;
out.hist_already = toGreetRow({status: 'skipped', is_skipped: true,
                               skip_reason: '已沟通过'}).status;
out.push_account = allGreetRecords[0].account_index;
console.log(JSON.stringify(out));
'''


def run_js(html_source, payload):
    """在 node 里跑 index.html 抽出的那几个函数。"""
    if shutil.which("node") is None:
        raise unittest.SkipTest("需要 node 才能真跑前端函数")
    fns = "\n".join(extract_fn(html_source, n)
                    for n in ("toGreetRow", "greetRowKey", "addGreetRecord", "extractDate"))
    js = (
        "const PUSH = " + json.dumps(payload, ensure_ascii=False) + ";\n"
        "let allGreetRecords = [];\n"
        "let greetPendingRows = [];\n"
        "function applyGreetFilter(){/* 只验数据模型，DOM 渲染另有真机测试 */}\n"
        + fns + "\n" + JS_CASES
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(js)
        path = f.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    finally:
        Path(path).unlink(missing_ok=True)
    assert proc.returncode == 0, f"node 执行失败:\n{proc.stderr}"
    return json.loads(proc.stdout.strip())


class PushSurvivesServerRefreshTest(unittest.TestCase):
    """推送行不能被一份"比它早发出的服务端快照"冲掉。

    2026-09-29 真机抓到的：`loadGreetRecords` 直接 `allGreetRecords = 服务端列表`，
    而页面加载/轮询的那次 fetch 请求早于引擎落库；它落地时刚好把刚推上来的
    那一行整条替换掉——用户看到"闪一下又没了"，最长要等 20 秒轮询才回来。
    """

    SERVER_ROWS = [{"job_name": "数据分析师", "company": "老记录公司", "salary": "9-12K",
                    "status": "applied", "is_greeted": True,
                    "timestamp": "2026-09-29 09:00:00",
                    "url": "https://www.zhipin.com/job_detail/old.html",
                    "account_index": 1}]

    @classmethod
    def setUpClass(cls):
        cls.out = run_merge_js(INDEX_HTML.read_text(encoding="utf-8"), PUSH,
                               cls.SERVER_ROWS)

    def test_推送行进模型(self):
        self.assertEqual(self.out["after_push"], 1)

    def test_过期快照不能冲掉推送行(self):
        self.assertEqual(self.out["after_stale_refresh"], 2,
                         "服务端快照里没有的那一行被整份替换冲掉了")
        self.assertEqual(self.out["push_still_first"], PUSH["job_name"])

    def test_快照带上它之后不重复(self):
        self.assertEqual(self.out["after_confirmed_refresh"], 2,
                         "落库记录回来后推送行该并入，不能变成两行")

    def test_推送行不跨数据范围(self):
        self.assertEqual(self.out["other_scope_len"], 1,
                         "切到别的账号还把上一个号的推送行挂着")

    def test_久等不来就放弃(self):
        """服务端一直没有它（极端情况）时不能永远挂在页面上"""
        self.assertEqual(self.out["expired_len"], 1)


def run_merge_js(html_source, payload, server_rows):
    """在 node 里跑真实的 loadGreetRecords：fetch 换成受控的假实现"""
    if shutil.which("node") is None:
        raise unittest.SkipTest("需要 node 才能真跑前端函数")
    names = ("toGreetRow", "greetRowKey", "addGreetRecord", "extractDate",
             "loadGreetRecords")
    fns = "\n".join(extract_fn(html_source, n) for n in names)
    confirmed = server_rows + [{
        # 落库词表里的同一条：推送侧 status=skip / 落库侧 status=skipped+is_skipped，
        # 两边的 _key 必须一致，否则确认回来的那次刷新会变成两行
        "job_name": payload["job_name"], "company": payload["company"],
        "salary": payload["salary"], "status": "skipped", "is_skipped": True,
        "skip_reason": payload["skip_reason"],
        "timestamp": payload["timestamp"], "url": payload["url"],
        "account_index": payload["account_index"],
    }]
    js = """
const PUSH = %s;
const SERVER_ROWS = %s;
const CONFIRMED = %s;
let allGreetRecords = [];
let greetPendingRows = [];
let greetFeedSig = '';
let dataScope = 'all';
var window = {};
function applyGreetFilter(){}
function scopeQs(){ return ''; }
var fetch = function(){ return Promise.resolve({json: function(){
    return Promise.resolve({records: window.__rows || []}); }}); };
%s
(async function(){
  const out = {};
  const realNow = Date.now;
  const tick = () => new Promise(r => setTimeout(r, 5));

  addGreetRecord(PUSH);
  out.after_push = allGreetRecords.length;

  window.__rows = SERVER_ROWS;              // 快照里没有推送行
  loadGreetRecords(true); await tick();
  out.after_stale_refresh = allGreetRecords.length;
  out.push_still_first = (allGreetRecords[0] || {}).job_name;

  window.__rows = CONFIRMED;                // 下一次快照带上它了
  loadGreetRecords(true); await tick();
  out.after_confirmed_refresh = allGreetRecords.length;

  window.__rows = [{job_name: '别人的号', company: 'X', status: 'applied',
                    is_greeted: true, timestamp: '2026-09-29 09:30:00',
                    url: 'https://www.zhipin.com/job_detail/other.html',
                    account_index: 0}];
  dataScope = '0';                          // 切到主账号
  loadGreetRecords(true); await tick();
  out.other_scope_len = allGreetRecords.length;

  dataScope = 'all';
  Date.now = () => realNow() + 400000;      // 久等不来就该放手
  window.__rows = SERVER_ROWS;
  loadGreetRecords(true); await tick();
  out.expired_len = allGreetRecords.length;
  Date.now = realNow;

  console.log(JSON.stringify(out));
})();
""" % (json.dumps(payload, ensure_ascii=False),
       json.dumps(server_rows, ensure_ascii=False),
       json.dumps(confirmed, ensure_ascii=False), fns)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(js)
        path = f.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True,
                              timeout=30, encoding="utf-8")
    finally:
        Path(path).unlink(missing_ok=True)
    assert proc.returncode == 0, f"node 执行失败:\n{proc.stderr}"
    return json.loads(proc.stdout.strip())


class GreetRowModelTest(unittest.TestCase):
    """实时推送的行和历史记录共用一套行模型"""

    @classmethod
    def setUpClass(cls):
        cls.out = run_js(INDEX_HTML.read_text(encoding="utf-8"), PUSH)

    def test_实时行写入数据模型(self):
        self.assertEqual(self.out["model_len_after_push"], 1,
                         "推送的行只进了 DOM 没进 allGreetRecords，切筛选就没了")

    def test_重复推送不产生两行(self):
        self.assertEqual(self.out["model_len_after_dup"], 1)

    def test_实时行带可筛选的日期(self):
        """只给 HH:MM:SS 的行会被日期条件滤空，看起来就像没显示"""
        self.assertEqual(self.out["model_first_ts"][:10], "2026-09-29")
        self.assertEqual(self.out["model_date"], "2026-09-29")

    def test_账号跟着行走(self):
        self.assertEqual(self.out["push_account"], 1)

    def test_AI不匹配两种来源判定一致(self):
        self.assertEqual(self.out["push_ai_skip"], "ai_skip")
        self.assertEqual(self.out["hist_ai_skip"], "ai_skip")

    def test_失败已沟通已投递判定(self):
        self.assertEqual(self.out["push_error"], "error")
        self.assertEqual(self.out["hist_already"], "already")
        self.assertEqual(self.out["push_success"], "success")
        self.assertEqual(self.out["hist_applied"], "success")


class GreetPushContractTest(unittest.TestCase):
    """后端推给前端的字段要够前端用"""

    def test_推送带完整时间戳(self):
        src = Path("flask-version/app.py").read_text(encoding="utf-8")
        body = src[src.index("def greet_event_callback"):src.index("def reply_event_callback")]
        self.assertIn('"timestamp"', body,
                      "推送没带 timestamp，前端日期筛选会把实时行滤空")

    def test_断线重连后重拉记录(self):
        """断线期间引擎推的行情全丢了，重连必须按落库的数据补回来"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        block = html[html.index("socket.on('connect'"):html.index("socket.on('disconnect'")]
        self.assertIn("loadGreetRecords(", block)

    def test_记录有轮询兜底(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn("setInterval(function(){ try { loadGreetRecords(); }", html,
                      "推送一旦丢失就只能刷新页面才看得到")

    def test_不再有投递中的假行(self):
        """引擎从不推 pending/running，这套占位行只会让表里出现线上没有的状态"""
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertNotIn("greet-status-pending", html)
        self.assertNotIn("data-job-url", html)


if __name__ == "__main__":
    unittest.main()
