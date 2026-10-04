# -*- coding: utf-8 -*-
"""账号区前端这几条改动的源码锁。

都是"点了没反应"那一类：数据范围条开机不出现、点切换 chip 不高亮、新增账号选不中、
右侧还留着登录入口。这些在 JS 里没法单测跑出来（要真浏览器），所以按本仓库的既有做法
锁源码形状，改回去就红。真机点击另有一套（tests/e2e_account_scope_ui.py）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "flask-version" / "templates" / "index.html").read_text(encoding="utf-8")


def _body(fn_name):
    """取一个顶层 function 的源码块（按花括号配对，够锁形状用）"""
    m = re.search(r"\nfunction " + re.escape(fn_name) + r"\s*\(", HTML)
    assert m, f"界面上已经没有 {fn_name}() 了"
    i = HTML.index("{", m.start())
    depth = 0
    for j in range(i, len(HTML)):
        if HTML[j] == "{":
            depth += 1
        elif HTML[j] == "}":
            depth -= 1
            if depth == 0:
                return HTML[i:j + 1]
    raise AssertionError(f"{fn_name} 的花括号没闭合")


def test_点数据范围立刻重绘chip():
    """以前只有 renderMetrics 末尾或 status_update 会重绘：机器人没跑过时点了确实切了
    数据但视觉不动，用户就判成"切换无效" """
    assert "renderMetricsScope()" in _body("setDataScope")


def test_配置一到就画数据范围条():
    """开机 loadMetrics 与 loadConfig 并发，metrics 先到时账号列表还是空的，
    切换条要等 20 秒轮询才冒出来 """
    assert "renderMetricsScope()" in _body("loadConfig")


def test_loadConfig_把_promise_传出去():
    """不 return 的话调用方 .then 里读到的是旧的 accounts 长度 """
    assert "return fetch(" in _body("loadConfig")


def test_新增账号等配置落地后才选中():
    body = _body("addAccount")
    assert "loadConfig().then(" in body, f"还是同步算长度：{body[:200]}"
    after = body.split("loadConfig().then(", 1)[1]
    assert "config.accounts.length - 1" in after, "选中项算在新账号进列表之前"
    assert "activeAccountIdx = config.accounts.length - 1;" not in body.split(
        "loadConfig().then(", 1)[0], "长度赋值不能留在 then 外面"


def test_新增账号顺手切到它的数据范围():
    """新增完不切范围，用户还得自己去右侧点一下才明白归属 """
    assert "setDataScope(String(activeAccountIdx))" in _body("addAccount")


def test_右侧不再留登录入口():
    """口径：左侧只管登录，右侧数据范围只管查看 """
    assert 'id="btnConfirmLogin"' not in HTML
    assert 'onclick="confirmLogin(' not in HTML
    assert "function confirmLogin(" not in HTML, "入口撤了函数还留着，迟早又被接回去"


def test_左侧账号仍然能登录和检测():
    row = _body("renderAccounts")
    assert "loginAccount(" in row, "登录能力只能从左侧走，不能一起撤掉"
    assert "checkAccountCookie(" in row, "登录状态检测的点没了"
    assert "cookie-status-dot" in row


def test_看不准时给灰点而不是蒙颜色():
    """后端 uncertain 会回 valid=null，界面必须走"未知"分支 """
    row = _body("renderAccounts")
    assert "cookieStatusCache[i].valid === true" in row
    assert "cookieStatusCache[i].valid === false" in row


def test_脚本里没有裸中文注释行():
    """上一轮把一条注释写成两行，第二行丢了 //：中文在 JS 里是合法标识符，
    语法照样过，运行时 loadConfig 第一句就抛 ReferenceError——整个面板空着。
    pytest 全绿也发现不了，只有真浏览器会炸，所以在这里补一道静态锁。"""
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, re.S)
    assert scripts, "页面里没有内联脚本？"
    bad = []
    for src in scripts:
        for n, line in enumerate(src.replace("\r\n", "\n").split("\n"), 1):
            t = line.strip()
            if t and ("\u4e00" <= t[0] <= "\u9fff" or t[0] in "（、“"):
                bad.append(f"  行{n}: {t[:70]}")
    assert not bad, "这些行没有 // 前缀，会被当成表达式：\n" + "\n".join(bad)


# ── 多账号重做：点账号条切不过去、面板不标明改的是谁 ──

def test_点账号条本身就切数据范围():
    """用户点的就是左上那排账号条，右侧那条 chip 很少有人注意到：
    以前账号条没有 onclick，点账号2 什么都不会变，被当成"多账号没做好" """
    body = _body("renderAccounts")
    assert 'onclick="setDataScope(' in body
    # 登录按钮和 Cookie 点仍然只管自己，不能顺手切范围
    assert body.count("event.stopPropagation()") >= 2


def test_账号条高亮跟着数据范围走():
    assert "dataScope" in _body("renderAccounts")


def test_账号条轮询时只补变化不许整块重建():
    """metrics 每几秒调一次 renderAccounts，以前是 innerHTML 整块重写：
    用户手正压在某一行上，那一行在 mousedown 和 mouseup 之间被换成新节点，
    点下去什么也没发生——这正是"点账号2他切换不了"的另一半。
    行必须有 data-acc-idx 当身份，认得出老行就只改会动的几处。"""
    body = _body("renderAccounts")
    assert "data-acc-idx" in body, "账号行没有稳定身份，只能整块重建"
    # 容器（变量名 c）的 innerHTML 一旦被整体赋值，正在被点的那一行就换人了；
    # 新行借临时 holder 生成节点是允许的，所以只禁 c.innerHTML
    assert re.search(r"\bc\.innerHTML\s*=", body) is None, \
        "账号条还是整块 innerHTML 重写，轮询会吃掉用户那一下点击"
    assert "appendChild(" in body or "insertAdjacentHTML(" in body, \
        "新增的行要插进去，不能靠重建整条容器"


def test_指标只认最后一次请求():
    """切账号时两个 /api/metrics 同时在飞，先发的后回来会把上个号的数盖上来"""
    assert "_metricsSeq" in _body("loadMetrics")


def test_会话列表只认最后一次请求():
    assert "replyListSeq" in _body("loadReplyChatList")


def test_岗位列表标明正在编辑哪个账号():
    """数据范围是「全部账号」时左栏仍在编辑 activeAccountIdx，
    不写出来就像"账号2 的岗位没了" """
    assert "renderEditingScope()" in _body("updateSbarJobs")
    assert "renderEditingScope()" in _body("renderAccountConfig")


def test_岗位弹窗标题带账号():
    assert "editingAccountLabel()" in _body("showJobModal")


def test_未知状态不许渲染成已投递():
    """兜底分支以前一律 feStatus='success'：status 缺失/待确认的行
    会被显示成"已投递"，前端计数就比 BOSS 端多 """
    body = _body("toGreetRow")
    assert "feStatus = 'pending'" in body
    assert re.search(r"else\s*\{\s*feStatus = 'success'", body) is None
    assert "pending:" in HTML


def test_自进化弹窗按账号取数():
    """经验/快照按号分文件存，弹窗以前读的是旧全局文件，和两个号实际记录两套数"""
    assert "evolutionQs()" in _body("showEvolutionModal")
    assert "setEvolutionAccount" in _body("showEvolutionModal")


def test_并发序号变量都要先声明():
    """这类 `var seq = ++xxxSeq` 的守卫，计数器漏了声明就是 ReferenceError：
    函数第一件事就抛，整块面板（回复记录列表）一条都不显示，而且控制台外看不出原因。"""
    used = set(re.findall(r"[*+/!-]{2,}\s*([A-Za-z_$][\w$]*Seq\b)", HTML))
    used |= set(re.findall(r"\b([A-Za-z_$][\w$]*Seq)\s*[*+/!-]{2,}", HTML))
    declared = set(re.findall(r"\b(?:var|let|const)\s+([A-Za-z_$][\w$]*Seq)\b", HTML))
    assert used, "一个并发序号都没找到，说明守卫被撤了"
    missing = sorted(used - declared)
    assert not missing, f"未声明的序号计数器：{missing}"


def test_招呼语没配时打招呼区顶上前提示():
    """整号没填招呼语时一轮下来全是「未配置招呼语，跳过」：日志一片跳过、
    记录区只显示状态列一个词，用户的第一反应是"日志和记录对不上"。
    就绪状态必须由 metrics 报出来并顶上前说清楚。"""
    assert 'id="greetAlert"' in HTML
    assert "renderGreetAlert()" in _body("loadMetrics")
    body = _body("renderGreetAlert")
    assert "greeting_ready" in body
    assert "gotoGreetEditor" in body


def test_切数据范围要跟着重算提示():
    assert "renderGreetAlert()" in _body("setDataScope")


def test_数据范围条轮询时不许整块重建():
    """右侧「全部账号 / 账号1 / 账号2」那条也是每轮 metrics 重画一次：
    用户手压着 chip 点下去，chip 中途被换成新节点就等于没点。"""
    body = _body("renderMetricsScope")
    assert "data-scope" in body, "chip 没有稳定身份，只能整块重建"
    assert re.search(r"\bel\." + "innerHTML\\s*=", body) is None, \
        "数据范围条还是整块重写，会吃掉切账号那一下点击"


def test_招呼语提示条轮询时不许整块重建():
    """提示条每轮 metrics 重画一次，「去填写」按钮跟着被换成新节点：
    用户手压着点下去，正好落在重建的那一瞬，等于没点。
    骨架留在 HTML 里，函数只改名单文本和按钮指向的账号。"""
    body = _body("renderGreetAlert")
    assert re.search(r"\bbox\.innerHTML\s*=", body) is None, \
        "提示条还是整块 innerHTML 重写，会吃掉「去填写」那一下点击"
    assert 'id="greetAlertNames"' in HTML and 'id="greetAlertGo"' in HTML, \
        "提示条的固定骨架没写在页面里"


def test_去填写要选中那个号并聚焦输入框():
    body = _body("gotoGreetEditor")
    assert "activeAccountIdx = Number(idx)" in body
    assert "renderAccountConfig()" in body
    assert "getElementById('accGreeting')" in body


def test_socket_客户端走本地文件不靠_public_CDN():
    """实测：cdn.socket.io 不通时，页面第一句 `const socket = io({...})` 直接抛，
    整个主脚本没跑完——config/dataScope 全在 TDZ 里，界面上包括"点账号2"在内的
    任何按钮都不响应。第三方脚本必须本地化。"""
    assert 'src="https://cdn.socket.io' not in HTML
    assert '/static/vendor/socket.io.min.js' in HTML
    vendored = ROOT / "flask-version" / "static" / "vendor" / "socket.io.min.js"
    assert vendored.is_file() and vendored.stat().st_size > 10000, "本地 socket.io 客户端不在"


def test_取不到_io_时页面照常能用():
    """本地文件也可能被误删/被拦：拿不到 io 就退化成空实现，
    实时推送没了照样有 20 秒轮询，不许再把整块脚本带死。
    按内容找那块脚本：head 里还有一段主题首帧脚本，它不再恰好是第一段。"""
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", HTML, re.S)
    boot = [s for s in scripts if "typeof io" in s]
    assert boot, "页面里没有 socket 兜底了"
    head = boot[0][:900]
    assert "typeof io === 'function'" in head, "socket 初始化没有兜底"
    assert re.search(r"const socket = io\(", head) is None
