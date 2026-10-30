# -*- coding: utf-8 -*-
"""岗位候选池：抓来的岗位先落到盘上，投哪条由人在面板上勾。

为什么要有这一层（2026-10-19 定的口径）：原来 `GreetEngine._parse_job_list()` 把卡片
解析成 dict 之后只存在 `self.jobs` 里，`main_loop._run_greet_round()` 拿到就当轮逐个
投出去 —— 他看不到"这一轮到底抓到了什么"，也没法说"这条我要、那条不要"。
判据（方向词表、否决词、JD 硬闸、AI 判分）都是现成的，缺的是一个能把岗位留下来、
能被改能被筛的地方。

三条规矩写在这里而不是散在调用方：
- **同一条岗位只认 URL**：一轮几十条、一天几十轮，不按 URL 认就会重复建行，
  界面被同一句话灌满，他根本没法勾；
- **他改过的东西机器不许重置**：状态、备注在再抓到同一条岗位时原样留着，
  只更新薪资这类页面字段和 seen 计数；
- **读坏不回写空**：这份文件是他攒的候选清单，半截 JSON 判成"池子空的"然后写空
  等于又丢一次数据（项目里在 Cookie 上栽过，见 README「断电/强杀之后数据还在」）。
"""

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from boss_bot.unified_config import account_file, resolve_path, write_json_atomic

# 状态是机器和他共同写的，所以只有这四个值；界面标签在 index.html 的 POOL_STATUS_LABEL
状态待选 = "new"
状态已选 = "selected"
状态已投 = "applied"
状态不投 = "rejected"
全部状态 = (状态待选, 状态已选, 状态已投, 状态不投)

池文件名 = "job_pool.json"
挪出文件名 = "job_pool_chatted_out.json"
坏证据目录名 = "pool_bad"

# 「没投出去」分两类，界面措辞和要不要放回队列都按这个分：
#   终局 —— BOSS 侧已有这条会话，这条岗位再怎么勾也投不出去 → 挪出池子；
#   暂时 —— 话术这一类（画像/招呼语/闸门），他改完配置下一轮就投得出去 → 放回待选。
# 老行没有「可重试」这个键（10-29 之前不写），按原因串认，认完盖上键。
_暂时性失败前缀 = ("话术不可用", "点了沟通但没成")


def 是可重试的失败(投递) -> bool:
    """这一行的「没投出去」是暂时的还是终局的；投成功的不算失败。"""
    d = 投递 or {}
    if d.get("成"):
        return False
    if "可重试" in d:
        return bool(d.get("可重试"))
    return str(d.get("原因") or "").startswith(_暂时性失败前缀)

# 面板线程和打招呼线程在同一个进程里读写同一份池文件，写是原子的、
# 但"读—改—写"这个夹缝会互相盖掉（他刚标的"不投"被一轮抓取写回去就是这个）
_锁 = threading.RLock()

# 再抓到同一条岗位时**可以**更新的页面字段。状态、备注、AI 结论不在这里：
# 那些是他或上一次判分的决定，不该被"又看见一次"冲掉
_可更新字段 = ("job_name", "salary", "company", "location", "experience",
               "education", "job_type", "query", "city", "url")
_不许被重置的字段 = ("id", "url", "status", "note", "captured_at", "pinned")


def _去噪(url: str) -> str:
    """去掉追踪参数和锚点，留下能认出"就是这条岗位"的那截

    BOSS 会在同一条岗位上挂不同的 `_security_check`、`ut`、`from`，整串比会
    把同一个岗位看成好几个；但只取 `job_detail/<id>.html` 那段更稳 —— 有些
    卡片带的查询串本身就是要紧信息（`_ll` 定位）。
    """
    u = (url or "").strip().split("#", 1)[0]
    if "/job_detail/" in u:
        头 = u.split("?", 1)[0]
        return 头 if 头.endswith(".html") else 头.split("/job_detail/", 1)[1].split("/")[0]
    return u.split("?", 1)[0]


def 行id(url: str) -> str:
    """池里这一行的稳定编号：只由 URL 的岗位身份算出来，抓取顺序不参与"""
    身份 = _去噪(url) or (url or "")
    return hashlib.sha1(身份.encode("utf-8")).hexdigest()[:16]


def _新行(job: dict, account_index: int, 现在: float) -> dict:
    位置 = (job.get("location") or job.get("company_location") or "").strip()
    return {
        "id": 行id(job.get("url", "")),
        "url": (job.get("url") or "").strip(),
        "job_name": (job.get("job_name") or job.get("title") or "").strip(),
        "salary": (job.get("salary") or "").strip(),
        "company": (job.get("company") or "").strip(),
        "location": 位置,
        "city": (job.get("city") or 位置).strip(),
        "query": (job.get("query") or "").strip(),
        "job_type": (job.get("job_type") or "").strip(),
        "experience": (job.get("experience") or "").strip(),
        "education": (job.get("education") or "").strip(),
        "account_index": account_index,
        "captured_at": 现在,
        "last_seen_at": 现在,
        "seen_count": 1,
        "status": 状态待选,
        "note": "",
        "pinned": False,
        "gate": {},          # 本地粗筛（不要钱）：方向命中 / 否决命中 / 结论
        "ai": {},            # AI 精筛：分 / 结论 / 理由 / 接口 / 判于
        "投递": {},          # 真投出去那一次的结局：成 / 原因 / 时间
        "勾选": {},          # 他在面板上动这一行的最后一次留痕：来源 / 动作 / 时间 / 首于
    }


def 岗位行成抓取(row: dict) -> dict:
    """把池里的一行还原成 `send_greeting(job)` 要的那种 dict

    投递出口只认抓取时那份字段名（job_name/company/url/query...），池里多出来的
    状态、AI 结论不能混进去——否则 send_greeting 里 `job.get("job_name")`
    以外的取值会被这些新键干扰，日志和落库也开始串。
    """
    出 = {字段: row.get(字段, "") for 字段 in _可更新字段}
    出["id"] = row.get("id", "")
    for 键 in ("_ai_suggested_greeting",):
        if row.get(键):
            出[键] = row[键]
    return 出


def _该不该藏(方向命中, 拦词: str) -> bool:
    """撞上否决词的这条，要不要从「只看本地没否过的」里藏掉。

    2026-10-29 岗位池口径②：盘上现算 20 条"方向命中又撞否决词"的，拦词是
    销售/住宿/坐班/包吃包住 这一类**修饰词**（「AI数据标注（提供住宿）」「数据分析专员-包吃包住」），
    以前一勾这个筛就整批消失，等于机器替他静默删掉了他点名方向的正主。
    现在只有硬拦族（intent.NO_EXEMPT：合伙人/老师/讲师/客服/主播——标题里那个词
    就是这份工本身）继续藏；方向确实命中的软词留在屏上，由界面标成"要人看一眼"。
    这条判据只有这一份：`_被粗筛否过`（筛）和 `要人看一眼`（标）都从这儿走。
    """
    拦 = str(拦词 or "").strip()
    if not 拦:
        return False
    from boss_bot.intent import NO_EXEMPT
    if 拦 in NO_EXEMPT:
        return True
    return 方向命中 is not True


def 要人看一眼(方向命中, 拦词: str) -> bool:
    """方向命中 + 只撞了软否决词 → 机器不删也不藏，交给人判。"""
    拦 = str(拦词 or "").strip()
    return bool(拦) and 方向命中 is True and not _该不该藏(True, 拦)


def _被粗筛否过(row: dict) -> bool:
    """本地粗筛有没有明确说过这条不合

    只有"明确否过"才算：没判过的行 gate 是空的，那是还没轮到粗筛或者抓取时没带 JD，
    不能当成"机器说他不要"——否则「只看像能投的」会把整池子筛空。
    """
    筛 = (row.get("gate") or {})
    if 筛.get("方向命中") is False:
        return True
    return _该不该藏(筛.get("方向命中"), 筛.get("否决命中"))


def 城市名(row: dict) -> str:
    """一行岗位的城市：BOSS 的 location 常是「长沙·岳麓区·麓谷」，取第一段。

    只按原串分组会把同一个长沙拆成几十个选项（每个商圈一条），他选不过来；
    city 本来就是「城市」那一格，location 只是更细的地址。
    """
    原 = str((row or {}).get("city") or (row or {}).get("location") or "").strip()
    for 分隔 in ("·", "|", "-", " "):
        if 分隔 in 原:
            原 = 原.split(分隔, 1)[0]
    return 原.strip()


def 城市清单(行s: list) -> list:
    """[城市, 条数]，按条数从多到少——界面那个下拉按这个顺序摆"""
    计数 = {}
    for 行 in 行s or []:
        城 = 城市名(行)
        if 城:
            计数[城] = 计数.get(城, 0) + 1
    return [{"city": 城, "count": 数}
            for 城, 数 in sorted(计数.items(), key=lambda x: (-x[1], x[0]))]


def 薪资折月(salary):
    """把 BOSS 那几种薪资写法折成「元/月」；折不出就返回 None，不许冒充过线。

    盘上 704 种写法实测就四类：「8-10K」「31-36元/时」「211-221元/天」「4111-4611元/月」，
    外加"面议"和反爬字体解出来的残缺值（"-23K"、带 ? 的那些，先由 薪资可信 判成读不出）。
    不折单位就没法比：「31-36元/时」的首数是 31，拿它去比"≥20K"会把时薪岗当成两万月薪放进来。
    折算口径写死在这里一份，界面上要照抄这句话讲给他：时薪按 8 小时×22 天、日薪按 22 天。
    """
    from boss_bot.greet_engine import 薪资可信      # 函数级 import：那份判据住在 greet_engine，反向 import 会成环
    文 = 薪资可信(salary)
    if not 文:
        return None
    数 = re.findall(r"\d+(?:\.\d+)?", 文)
    if not 数:
        return None                              # "面议"这种没数字的合法写法：不参与薪资筛
    低 = float(数[0])
    if "元/时" in 文 or "元/小时" in 文:
        return int(round(低 * 8 * 22))
    if "元/天" in 文 or "元/日" in 文:
        return int(round(低 * 22))
    if "万" in 文:
        return int(round(低 * 10000))
    if "千" in 文 or "k" in 文.lower():
        return int(round(低 * 1000))
    if "元/月" in 文:
        return int(round(低))
    return None                                  # 认不出的写法一律不参与：宁可筛不出，不许筛错


def 薪资筛得过(row: dict, 下限K) -> bool:
    """这一行的薪资够不够「≥ 下限K 千元/月」；读不出的一律不算够。"""
    try:
        下限 = float(下限K)
    except (TypeError, ValueError):
        return True
    if 下限 <= 0:
        return True
    月 = 薪资折月(row.get("salary"))
    return 月 is not None and 月 >= 下限 * 1000


def 抓到筛得过(row: dict, 天内) -> bool:
    """这一行是不是最近 天内 抓进来的；天内 为空或 0 = 不看这一条。"""
    try:
        天 = float(天内)
    except (TypeError, ValueError):
        return True
    if 天 <= 0:
        return True
    try:
        抓 = float(row.get("captured_at") or 0)
    except (TypeError, ValueError):
        return False
    return 抓 >= time.time() - 天 * 86400


def _AI档筛得过(row: dict, 档: str, 文档指纹: str = "") -> bool:
    """界面「AI 精筛」那一格：未判 / 判过 / 合 / 不合。

    「合」只认**当前这份要求文件**下判出来的合：换了一份要求，旧结论就跟新要求
    没关系了，不能继续冒充匹配。顶部那个「AI匹配」计数走的是同一句判据——
    两处各写一套的话，数字跟筛出来的行数会对不上。
    「不合」要的是机器真给了个否：接口异常那次落的是空结论（没盖文件指纹），
    把它算进「判成不合」就成了"AI 说这条不要"，其实它没看。
    """
    if not 档:
        return True
    ai = row.get("ai") or {}
    if 档 == "未判":
        return not ai.get("判于")
    if 档 == "判过":
        return bool(ai.get("判于"))
    if 档 == "合":
        指纹 = str(文档指纹 or "")
        return bool(指纹) and ai.get("合") is True \
            and str(ai.get("doc") or "") == 指纹
    if 档 == "不合":
        return ai.get("合") is False and not ai.get("接口异常")
    return True


def AI匹配数(行s, 文档指纹: str = "") -> int:
    """这份要求文件下判成「合」的行数——界面顶部那个「AI匹配」数字。

    跟表头「判成合」那一格走同一句判据：两处各写一套，就会出现
    "AI匹配 12 条、筛出来 10 行"，然后他得花时间去猜哪边在骗人。
    """
    return sum(1 for r in (行s or []) if _AI档筛得过(r, "合", 文档指纹))


def _粗筛档筛得过(row: dict, 档: str) -> bool:
    """界面「本地粗筛」那一格。判据跟 `_被粗筛否过`/`要人看一眼` 同一份，不另起一套。"""
    if not 档:
        return True
    筛 = row.get("gate") or {}
    if 档 == "没过过的":
        return not _被粗筛否过(row)
    if 档 == "被否过的":
        return _被粗筛否过(row)
    if 档 == "要人看一眼":
        return bool(筛.get("要人看一眼"))
    if 档 == "词表没读到":
        return bool(筛.get("词表没读到"))
    return True


def 筛行(行s, 状态: str = "", 关键字: str = "", 城市: str = "",
        岗位: str = "", 公司: str = "", 备注: str = "", 判分理由: str = "",
        薪资下限K=None, AI档: str = "", 粗筛档: str = "", 抓到天内=None,
        文档指纹: str = "") -> list:
    """按面板表头那几格挑行（纯函数）。

    单号列表和「全部账号」的合并视图共用这一份：两处各写一套的话，同一条岗位
    换个数据范围筛出来的结果就不一样。筛只是筛，一个字都不改。

    形参就是界面上那几格，一格一个名（`文档指纹` 不算格子，那是"当前按哪份要求
    判的"这个上下文）——加一格忘了接线，tests/test_job_pool_filters.py 会逐格比红。
    """
    出 = list(行s or [])
    if 状态:
        出 = [r for r in 出 if r.get("status") == 状态]
    for 字段, 词 in (("job_name", 岗位), ("company", 公司), ("note", 备注)):
        串 = str(词 or "").strip().lower()
        if 串:
            出 = [r for r in 出 if 串 in str(r.get(字段, "")).lower()]
    理 = str(判分理由 or "").strip().lower()
    if 理:
        出 = [r for r in 出 if 理 in str((r.get("ai") or {}).get("理由") or "").lower()]
    if 关键字:
        词 = 关键字.strip().lower()

        def _中(r):
            return (词 in str(r.get("job_name", "")).lower()
                    or 词 in str(r.get("company", "")).lower()
                    or 词 in str(r.get("note", "")).lower()
                    or 词 in str(r.get("city", "")).lower()
                    or 词 in str(r.get("location", "")).lower())

        出 = [r for r in 出 if _中(r)]
    if 城市:
        城 = 城市.strip()
        出 = [r for r in 出
              if 城 in str(r.get("city") or r.get("location") or "")]
    if 粗筛档:
        出 = [r for r in 出 if _粗筛档筛得过(r, 粗筛档)]
    if AI档:
        出 = [r for r in 出 if _AI档筛得过(r, AI档, 文档指纹)]
    出 = [r for r in 出 if 薪资筛得过(r, 薪资下限K)]
    出 = [r for r in 出 if 抓到筛得过(r, 抓到天内)]
    return 出


def 排序行(行s: list, 排序: str = "分降") -> list:
    """按界面那个「排序」摆好。单号的列表和「全部账号」的合并视图共用这一份：
    两处各写一套的话，同一条岗位换个数据范围顺序就变了。
    """
    if 排序 == "分降":
        # 没判过分的排后面：他打开界面该先看到机器已经筛出来的，而不是一堆空白
        行s.sort(key=lambda r: (-(float((r.get("ai") or {}).get("分") or 0)),
                                -float(r.get("last_seen_at") or 0)))
    elif 排序 == "新抓的在前":
        行s.sort(key=lambda r: -float(r.get("last_seen_at") or 0))
    return 行s


def 合计统计(各号统计) -> dict:
    """几个号的统计加起来，给「全部账号」那一行顶部的数字用。

    键跟单号统计一模一样（四个状态 + 总数 + 今日新增），多出来的键原样相加，
    这样前端那行统计条不用按范围改写法。
    """
    出 = {}
    for 份 in (各号统计 or []):
        for 键, 值 in (份 or {}).items():
            出[键] = int(出.get(键) or 0) + int(值 or 0)
    for 状态 in 全部状态:
        出.setdefault(状态, 0)
    出.setdefault("总数", 0)
    出.setdefault("今日新增", 0)
    return 出


def _不许测试碰真实池(路径) -> None:
    """pytest 里构造出一个指向仓库 data/ 的池，就地报错。

    实测 2026-10-29 12:54：tests/test_pool_policies_20261029.py 的 `_塞()` 漏了 `目录=tmp`，
    假岗位「数据分析师 / 公司0」就这样进了线上账号0 的池，同一条测试还把真岗位
    「影视数据标注」的 status 改成 applied、勾回写成假时间 10.0。面板一直在同一个
    文件上读写，这种串台一声不响，只在池里留下一行谁也说不清来历的记录。
    隔开来只有两条路：构造时给 `目录=tmp_path`，或 patch `boss_bot.job_pool.resolve_path`。
    """
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    真实 = os.path.realpath(str(Path(__file__).resolve().parents[1] / "data")).lower()
    这条 = os.path.realpath(str(路径)).lower()
    if 这条.startswith(真实 + os.sep):
        raise RuntimeError(
            "测试把岗位池指到了仓库真实的 data/：%s。给它 目录=tmp_path，"
            "或 patch boss_bot.job_pool.resolve_path——这个文件线上面板也在写。" % 路径)


class 岗位池:
    """一个号的岗位候选池（文件按号分开，和 greet_records 一个口径）"""

    def __init__(self, account_index: int = 0, 目录: str = ""):
        self.account_index = int(account_index or 0)
        基目录 = str(resolve_path(目录 or Path("data")))
        self.坏目录 = os.path.join(基目录, 坏证据目录名)
        self.路径 = account_file(os.path.join(基目录, 池文件名), self.account_index)
        self.挪出路径 = account_file(os.path.join(基目录, 挪出文件名), self.account_index)
        _不许测试碰真实池(self.路径)

    # ── 读写 ──

    def _读(self) -> list:
        """读池子。读不出来就是读不出来，并且把坏那份留成证据"""
        try:
            with open(str(self.路径), "r", encoding="utf-8") as f:
                数据 = json.load(f)
        except FileNotFoundError:
            return []
        except Exception as e:
            证据 = self._存坏证据()
            import logging
            logging.getLogger(__name__).warning(
                "岗位池 %s 读不出来（%s: %s），这份留了证据 %s，本轮按空池处理但不回写空覆盖",
                Path(self.路径).name, type(e).__name__, str(e) or "",
                Path(证据).name if 证据 else "没留下")
            return []
        if not isinstance(数据, list):
            证据 = self._存坏证据()
            import logging
            logging.getLogger(__name__).warning(
                "岗位池 %s 不是列表（顶层是 %s），已留证据 %s",
                Path(self.路径).name, type(数据).__name__,
                Path(证据).name if 证据 else "没留下")
            return []
        return [r for r in 数据 if isinstance(r, dict) and r.get("id")]

    def _存坏证据(self) -> str:
        """把读不出来的那份原样收进 pool_bad/，按内容哈希去重

        面板每十几秒轮一次列表；不去重的话一次损坏能攒出几百份一模一样的证据，
        把真有用的那份埋掉。
        """
        try:
            原样 = Path(self.路径).read_bytes()
        except OSError:
            return ""
        指纹 = hashlib.sha1(原样).hexdigest()[:12]
        os.makedirs(self.坏目录, exist_ok=True)
        目标 = os.path.join(self.坏目录, "%s.a%s.%s.json" % (
            Path(self.路径).stem, self.account_index, 指纹))
        if not os.path.exists(目标):
            try:
                Path(目标).write_bytes(原样)
            except OSError:
                return ""
        return 目标

    def _写(self, rows: list) -> None:
        write_json_atomic(self.路径, rows)

    # ── 查 ──

    def 列表(self, 状态: str = "", 关键字: str = "", 城市: str = "",
             排序: str = "分降") -> list:
        """读整池（或按 状态/关键字/城市 挑一批）；按列细筛走 `筛行`（接口那边在用）"""
        with _锁:
            行s = self._读()
        行s = 筛行(行s, 状态=状态, 关键字=关键字, 城市=城市)
        排序行(行s, 排序)
        return [dict(r) for r in 行s]

    def 城市统计(self) -> list:
        """整池的城市清单（不受当前筛选影响）：下拉里得能选到每个城市"""
        with _锁:
            行s = self._读()
        return 城市清单(行s)

    def 取(self, 编号: str) -> dict:
        with _锁:
            for r in self._读():
                if r.get("id") == 编号:
                    return dict(r)
        return {}

    def 统计(self) -> dict:
        """界面顶部那一行：各状态各几条、今天抓到几条"""
        with _锁:
            行s = self._读()
        今 = time.strftime("%Y-%m-%d")
        出 = {状态: 0 for 状态 in 全部状态}
        出["总数"] = len(行s)
        出["今日新增"] = sum(1 for r in 行s
                          if time.strftime("%Y-%m-%d",
                                           time.localtime(float(r.get("captured_at") or 0))) == 今)
        for r in 行s:
            if r.get("status") in 出:
                出[r["status"]] += 1
        return 出

    # ── 增 ──

    def 收(self, jobs) -> dict:
        """把这一轮抓到的岗位并进池子，返回 {新增, 见过, 无URL, 总数, 新增编号}

        「新增编号」是留给抓取腿的：精筛要挑"这轮新来的 + 以前没判成的"，
        只给个计数就得把整池再读一遍去猜哪些是新的。
        """
        结果 = {"新增": 0, "见过": 0, "无URL": 0, "总数": 0, "新增编号": []}
        现在 = time.time()
        待并 = [j for j in (jobs or []) if isinstance(j, dict)]
        if not 待并:
            return 结果
        with _锁:
            行s = self._读()
            位置 = {r.get("id"): i for i, r in enumerate(行s)}
            for job in 待并:
                url = str(job.get("url") or "").strip()
                if not url:
                    结果["无URL"] += 1
                    continue
                编号 = 行id(url)
                if 编号 in 位置:
                    已有 = 行s[位置[编号]]
                    for 字段 in _可更新字段:
                        if job.get(字段):
                            已有[字段] = job[字段]
                    已有["last_seen_at"] = 现在
                    已有["seen_count"] = int(已有.get("seen_count") or 0) + 1
                    结果["见过"] += 1
                else:
                    行s.append(_新行(job, self.account_index, 现在))
                    位置[编号] = len(行s) - 1
                    结果["新增"] += 1
                    结果["新增编号"].append(编号)
            self._写(行s)
            结果["总数"] = len(行s)
        return 结果

    def 增(self, job: dict) -> tuple:
        """手工补一条（他在一别处看到的岗位，粘个 URL 进来）"""
        if not str((job or {}).get("url") or "").strip():
            return False, "没有岗位链接，池里认不出重复、也没法投，没收"
        结果 = self.收([job])
        if not 结果["新增"] and 结果["见过"]:
            return True, "这条池里已经有了（同一个链接），给它记了一次"
        return True, "已加进池子"

    # ── 改 ──

    def 改(self, 编号: str, **字段) -> tuple:
        """改一行，并且从盘上读回来确认（写进去不算成，读得到才算）"""
        可改 = [k for k in 字段 if k in ("status", "note", "gate", "ai", "pinned", "投递", "勾选")]
        if not 可改:
            return False, "没给要改的字段（可改：status/note/gate/ai/pinned/投递/勾选）"
        if "status" in 字段 and 字段["status"] not in 全部状态:
            return False, ("状态只认 %s 这四种，给的是 %r"
                          % ("/".join(全部状态), 字段["status"]))
        with _锁:
            行s = self._读()
            目标 = next((r for r in 行s if r.get("id") == 编号), None)
            if 目标 is None:
                return False, "池里没有这条（编号 %s，可能已经被删了或是隔了一个号）" % 编号
            for 键 in 可改:
                值 = 字段[键]
                if isinstance(值, dict) and isinstance(目标.get(键), dict):
                    目标[键] = {**目标[键], **值}    # 判分结论要能单独补一刀，不许整块盖
                else:
                    目标[键] = 值
            self._写(行s)
            回读 = next((r for r in self._读() if r.get("id") == 编号), None)
        if 回读 is None:
            return False, "写完从盘上读不到这条了，别当改成功了"
        for 键 in 可改:
            if not isinstance(字段[键], dict) and 回读.get(键) != 字段[键]:
                return False, "回读到的 %s 和要写的不一样（%r）" % (键, 回读.get(键))
        return True, "已更新 %s" % "/".join(可改)

    def 标状态(self, 编号s, 状态: str, 留痕: dict = None) -> tuple:
        """成批改状态，并给每一行留下"谁、什么时候、把它勾成了什么"。

        2026-10-29 岗位池口径④：10-19 进池以来 selected/applied/投递/note 全是 0，
        没法回答"他到底勾过没有"——勾是只活在浏览器内存里的选中态，一点「投递选中的」
        才写盘，写完又只留一个 status，机器接手前后长得一模一样。留痕写进 勾选：
        来源（面板点击 / 打招呼线程）＋动作（人话）＋时间＋第一次动这一行的时间。
        """
        if 状态 not in 全部状态:
            return False, "状态只认 %s 这四种" % "/".join(全部状态)
        动作名 = {状态待选: "撤勾回待选", 状态已选: "勾成要投",
                 状态已投: "记成已投", 状态不投: "标成不投"}
        现在 = time.time()
        旧痕 = {r.get("id"): dict(r.get("勾选") or {}) for r in self.列表()}
        改了的 = []
        for n in (编号s or []):
            痕 = dict(留痕 or {})
            痕.setdefault("来源", "面板点击")
            痕.setdefault("动作", 动作名.get(状态, 状态))
            痕.setdefault("时间", 现在)
            痕.setdefault("首于", 旧痕.get(n, {}).get("首于") or 现在)
            if self.改(n, status=状态, 勾选=痕)[0]:
                改了的.append(n)
        没中 = [n for n in (编号s or []) if n not in 改了的]
        if not 改了的:
            return False, "一条都没改成（编号都找不到：%s）" % "、".join(没中[:5])
        说明 = "%d 条已标成 %s" % (len(改了的), 状态)
        if 没中:
            说明 += "，%d 条找不到（%s）" % (len(没中), "、".join(没中[:3]))
        return True, 说明

    def 重贴粗筛标(self, 标签函数) -> int:
        """按现在的词表把整池的 gate 四条标签重算一遍，返回改了几行。

        每轮抓取开头都进来一次（见 main_loop._重贴粗筛标）——只在"词表变了"那一轮
        动手，会让某一次读到坏表留下的旧标签永远没人修，10-22 就是这么漏了两行。
        这里一次读写整池：逐条走 `改()` 会给 1600 条岗位写 1600 遍文件。
        只动 gate 的 `否决命中/词表没读到/标题词表为空/方向命中/要人看一眼` —— status/note/pinned
        是他自己的决定、ai 是判分结论，"又判了一次粗筛"不许把它们冲掉；标签函数没判成
        （返回空）时连旧标签也不抹，免得"这次没读到账"被写成"这条干净"。
        """
        键s = ("否决命中", "词表没读到", "标题词表为空", "方向命中", "要人看一眼")
        with _锁:
            行s = self._读()
            改了 = 0
            for 行 in 行s:
                try:
                    新 = 标签函数(岗位行成抓取(行)) or {}
                except Exception:
                    continue
                if not any(k in 新 for k in 键s):
                    continue
                旧 = dict(行.get("gate") or {})
                for 键 in 键s:
                    if 键 in 新:
                        旧[键] = 新[键]
                    else:
                        旧.pop(键, None)
                if 旧 != (行.get("gate") or {}):
                    行["gate"] = 旧
                    改了 += 1
            if 改了:
                self._写(行s)
        return 改了

    # ── 删 ──

    def 删(self, 编号s) -> tuple:
        要删 = set(编号s or [])
        if not 要删:
            return False, "没给要删的编号"
        with _锁:
            行s = self._读()
            留下 = [r for r in 行s if r.get("id") not in 要删]
            少了 = len(行s) - len(留下)
            if 少了:
                self._写(留下)
            还在 = {r.get("id") for r in self._读()}
        真删 = [n for n in 要删 if n not in 还在]
        if not 真删:
            return False, "一条都没删掉（编号都不在池里）"
        if len(真删) != 少了:
            return True, "删了 %d 条，另有 %d 个编号本来就不在池里" % (len(真删), len(要删) - len(真删))
        return True, "删了 %d 条" % len(真删)

    def 放回可重试(self) -> tuple:
        """上一轮因"这一刻这句话不能发"被**机器**判成不投的行，放回待选并盖上「可重试」。

        这类原因住在配置里（画像、招呼语、发送闸门的词表）：他把画像补上「双一流」，
        那条判据当场就不成立了。留在「不投」里就是他说的"改了还是不生效"
        （2026-10-29 那行红字：01:38 判的，画像 12:39 改的，晚上还红着）。
        「BOSS 侧已有会话」那一类不在此列——那条走 挪走已沟通，永远投不出去。
        只认机器自己写的那次判定：他从下拉里挑「不投」是他的决定，池子的头一条规矩
        是他改过的东西机器不许重置。留痕是 10-29 才全端接上的，**没有留痕的老行**
        （线上那两行 01:38 判的红字就是 `勾选=null`）算机器判的——那时只有机器会写 投递。
        """
        with _锁:
            行s = self._读()
            改数 = 0
            for r in 行s:
                if r.get("status") != 状态不投 or not 是可重试的失败(r.get("投递")):
                    continue
                痕 = r.get("勾选") or {}
                if 痕.get("来源") == "面板点击" or 痕.get("动作") == "标成不投":
                    continue
                r["status"] = 状态待选
                投 = dict(r.get("投递") or {})
                投["可重试"] = True
                r["投递"] = 投
                改数 += 1
            if 改数:
                self._写(行s)
            return 改数, ("放回 %d 条：话术这一类的失败改了配置就重新可投" % 改数 if 改数 else "没有可放回的行")

    def 挪走已沟通(self, 身份库) -> tuple:
        """把"BOSS 侧早已有这条会话"的行挪出池子，一行不删，进 `job_pool_chatted_out.json`。

        为什么挪走而不是留着标成「不投」：这张表是他挑"这几条我要投"的地方，
        一条永远投不出去的行留在里面就是挡路。2026-10-29 现数：两号池 3276 行里
        有 328 行（10.0%）的 URL 已经在去重库里（机器以前招呼过），勾了只会被判
        「已沟通过」——他看到的那行红字就是这么来的。
        已投的行不动：那是他勾过、机器真投出去的账，要留在表里看得见。
        编号就是 `行id(url)`，所以这里只比 id，判据跟抓取腿、投递腿同一份。
        """
        if not 身份库:
            return 0, "没给去重库，一条没动"
        with _锁:
            行s = self._读()
            留, 走 = [], []
            for r in 行s:
                if r.get("status") != 状态已投 and r.get("id") in 身份库:
                    走.append(r)
                else:
                    留.append(r)
            if not 走:
                return 0, "池里没有已沟通的行"
            try:
                以前 = []
                if os.path.exists(str(self.挪出路径)):
                    with open(str(self.挪出路径), "r", encoding="utf-8") as f:
                        以前 = json.load(f) or []
            except Exception as e:
                # 那份是唯一的留档，读不出来就宁可不动：挪过去等于把它盖掉
                return 0, "挪出文件读不出来（%s），这条没动" % (str(e) or type(e).__name__)
            现在 = time.time()
            for r in 走:
                r["挪出原因"] = "BOSS 侧已有这条会话（去重库命中），不再当候选"
                r["挪出时刻"] = 现在
            write_json_atomic(str(self.挪出路径), 以前 + 走)
            self._写(留)
            return len(走), "挪走 %d 条已沟通的行（原件在 %s）" % (len(走), Path(self.挪出路径).name)
