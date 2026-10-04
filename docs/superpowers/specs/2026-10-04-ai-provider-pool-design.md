# AI 服务商池与延迟加权调度 — 设计

**日期**：2026-10-04
**范围**：把"按配置顺序串行试接口"的容灾链，换成"谁快谁空闲谁上、慢就同时问第二个"的服务商池。
不做的事：不换 async 运行时、不改判分口径、不动浏览器侧。

## 1. 为什么现在慢

判分链 `AIAnalyzerChain.analyze_job`（boss_bot/greet_engine.py:698-770）是串行的：
按 `ai.providers` 的顺序一个个试，单岗位最多 `MAX_ATTEMPTS_PER_JOB` 次、总预算
`JOB_BUDGET_SECONDS` 秒，第一个接口不回来就一直等它。两个号同时跑时同一个 key 还会
互相撞 429（日志里 developer.amd.com.cn 一轮 4 次 429）。

实测（2026-10-04 11:3x，本机直连，同一个"只回两个字"的请求）：

| 服务商 | 协议/路由 | 判分 JSON | 延迟 |
|---|---|---|---|
| 端砚 deepseek-v4-flash-0731 | openai 直连 | 可解析 | 0.9-1.9s |
| 云知声 u2 | openai 直连 | 可解析 | 4.1s（首包 20s 冷启动） |
| 浦语 internvl-latest | openai 直连 | 可解析 | 6.6s |
| 浦语 intern-s2-preview-397b | openai 直连 | 可解析 | 13.4s |
| Atria Atria-Dawn-Preview | 代理 + anthropic 才通 | 未测 | 6.1s |
| 智云新疆 | 直连/代理 × 两种协议全 25s 超时 | — | 不可用 |

同一份判分，最快和最慢差 20 倍。串行链把选择权交给"配置里谁排在前面"，
所以顺序一改效果就变——这正是需要调度器的信号。

## 2. 方案

**A 只重排 + 并发闸门**：按实测延迟排序 providers，加一个全局在途上限。改动最小，
但一个岗位仍然只能等一个接口，慢接口偶尔排到就还是拖。

**B 服务商池 + 延迟加权 + 对冲请求（选这个）**：新建 `boss_bot/ai_pool.py`，
把"哪个接口、什么时候发、失败了怎么办"从判分逻辑里拿出来，判分/招呼语/回复三类
调用只提交任务、只等结果。

**C 全异步重写**：收益和 B 重叠，但要改所有调用点和面板的同步读路径，回归面不值。

## 3. B 的接口

```python
class ProviderSlot:
    """一个服务商一条通道。状态全部实测得来，不写死权重。"""
    provider: AIProvider            # name/api_base/api_key/model/timeout
    ewma_ms: float                  # 成功耗时的指数滑动平均（α=0.3）
    ok_rate: float                  # 成功率的指数滑动平均
    inflight: int                   # 正在飞的请求数
    cooldown_until: float           # 429/鉴权/额度类失败的冷却到期时间
    max_inflight: int = 2           # 单服务商并发上限

class AIPool:
    def __init__(self, providers, *, workers=6, hedge_after=8.0,
                 max_inflight_per_provider=2, shared_cooldown=None): ...
    def submit(self, task: AITask) -> AIFuture:
        """task 带 kind（judge/greeting/reply/probe）和优先级；立刻返回。"""
    def stats(self) -> list[dict]:
        """面板用：每个服务商的 ewma、在途、成功率、冷却剩余、本轮被选次数。"""
    def note_failure(self, slot, err): ...   # 沿用现有冷却分档（429=60s，鉴权=30min）
```

调度规则（`_pick`）：
1. 剔除 `not provider.is_valid()`、体检不可用、`cooldown_until > now`、`inflight >= max` 的 slot；
   一个都不剩时按原顺序硬试（和现在一致，避免"体检结果本身不可信"时全线停摆）。
2. 余下的按 `score = ok_rate / max(ewma_ms, 200)` 降序；同分轮转，避免只用一个。
3. `hedge_after` 秒内没回来 → 把同一请求再发给次优 slot，**先回来的赢**，
   后回来的结果只计入统计（`ewma`/`ok_rate`），不写进业务记录。
4. 全部失败 → 抛 `AllProvidersBusy`，由调用方按 `ai.fail_action` 处置（保持现有语义：
   default 放行、skip 不投），绝不静默吞成"AI 判定不匹配"。

## 4. 接入点

| 调用方 | 现在 | 之后 |
|---|---|---|
| `GreetEngine._ai_analyze`（判分） | `analyzer.analyze_job()` 串行 | `pool.submit(kind='judge')` |
| AI 现编招呼语 | 同一条链的另一次调用 | `pool.submit(kind='greeting')`，与判分同批排队 |
| `ReplyEngine._ask_ai`（回复） | 自己一套 openai 调用 + 容灾上限 | `pool.submit(kind='reply')`，优先级最高（对面是真人） |
| 判分复盘追问 `probe_rejection` | 固定回到原接口 | 保持"回到原 slot"，追问要问同一个人 |

`AIAnalyzerChain` 保留，但只负责"构造提示词 + 解析 + 归一化"，不再自己挑接口。
这样 `_normalize_result` 里"只看否决词"那套判据不受影响，缓存键也不变。

## 5. 配置

`ai` 段新增（全部有默认值，旧配置不用改就能跑）：

```
pool_workers: 6                 # 池子总并发
hedge_after_seconds: 8.0        # 对冲阈值；0 = 关闭对冲
max_inflight_per_provider: 2    # 单服务商在途上限
```

按账号覆盖仍然走 `accounts[i].settings.ai`（现有机制），所以"账号2 用得快接口、
主账号保守一点"这种调法不用改代码。

## 6. 面板

AI 设置区加一张"池子实况"表，随现有 8 秒轮询局部刷新（不整块重建，避免打断输入）：
服务商 / EWMA 延迟 / 在途 / 成功率 / 冷却剩余 / 本轮被选次数 / 本轮对冲次数。
保留现有的手动排序和勾选禁用——调度器只在这些可用项里决定顺序。

## 7. 错误处理与红线

- **不重复打扰 HR**：对冲只发生在"判分/生成招呼语"这类纯计算请求上；真正点发送的
  动作仍在 `send_greeting` 一处，且以先返回的结果为准。
- **额度**：对冲最坏情况让判分请求数翻倍，所以 `hedge_after` 默认 8s（只有明显慢的
  才触发），且 `max_inflight_per_provider` 限制对免费接口的压力；429 冷却沿用 60s。
- **登录态**：池子不碰 Cookie，冷却表继续全进程共享（两个号摊同一批 key）。
- **密钥**：`api_key` 只留在 `bot_config.json`（已 gitignore），日志和面板一律脱敏。

## 8. 测试

单测（假 slot，可控延迟/失败）：
1. 快接口优先；被冷却/在途满的 slot 不参与选择。
2. 超过 `hedge_after` 才发第二份，先返回者胜出，后返回者只进统计。
3. 全部失败时抛 `AllProvidersBusy`，`fail_action=skip` 时不投。
4. `hedge_after=0` 时行为退化为"按延迟顺序单发"，与今天等价。
5. 回复侧优先级高于判分（饥饿：判分排满时 reply 仍能插队）。
6. EWMA 只被成功样本拉动，失败不污染延迟估计。
真机（双号，演练模式）：跑 10 分钟，比"岗位/分钟"和兜底率，日志里要能看见
对冲发生了几次、赢了几次。

## 9. 落地顺序

1. `AIProvider` 加 `protocol`（openai/anthropic）与 `via_proxy` 字段 → 顺手把 Atria 接进来。
2. `ai_pool.py` + 判分链接入（含上面 1-4 的单测）。
3. 回复侧、招呼语接入 + 面板"池子实况"（含 5-6 的单测与真机点击测试）。
4. 双号真机验收：吞吐、兜底率、429 次数三张数字前后对比。

## 10. 两个默认值，可以一句话改

- **对冲默认开（8s）**：代价是最坏情况判分请求翻倍。想省额度就把
  `hedge_after_seconds` 设 0，只保留"按实测延迟挑快的"。
- **回复侧默认进池**：对面是真人，回得快比省一次请求重要；但同一会话的两次
  回复请求会串行化（沿用现有 `begin_event_ts` 语义），不会出现两条 AI 抢着回一句。
