---
name: boss-apply
description: >
  在 BOSS 直聘上按使用者自己的规则自动投递简历：搜岗位、读任职要求、判分、
  对通过阈值的岗位发招呼语，并把每个岗位的结果与失败原因落成可查记录。
  依赖 browser-skill（bsk CLI + 扩展）与浏览器里已登录的 BOSS 直聘账号。
  Use when 用户说"帮我投 BOSS 直聘/投简历/找岗位并打招呼"且要求控制投递量与频率。
license: MIT
allowed-tools: Bash, Read, Write
metadata:
  version: "0.1.0"
compatibility: 需要 bsk CLI 与 browser-skill 扩展；不装则自检判 MISS 并停止。
---

# BOSS 直聘自动投递（boss-apply）

一轮 = 一次有始有终的投递任务：使用者说"投 30 个"，跑完汇报并退出。
不挂定时任务，不在会话里长循环。

## 四条红线（任何一条不满足就停手，不要绕）

1. **只用使用者自己已登录的浏览器**。本 skill 不做注册、不做登录、**永不代填手机号与验证码**。
   判到未登录：停下，调 `bsk request-help`，让人工去扫码。
2. **每轮默认上限 20 条招呼，硬上限 50**。计数读 `state.py count`，不读自己的记忆——
   上下文被压缩之后会低估，而多投的代价是骚扰 HR。
3. **风控/封号风险由使用者自己承担**。开跑前打印一句：投递用的是你自己的账号。
4. **拿不到可信信息就不发**。JD 读不到、消息列表读不到、页面结构和文档不一样 → 记
   `failure_code` 并跳过，绝不"猜一个按钮点下去"。

## 前置自检（先跑这个，别直接开浏览器）

```bash
python scripts/check_env.py            # 不通会告诉你缺什么
python scripts/check_env.py --json
```

- `MISS bsk_cli` → 让使用者装 `bsk` CLI 与 browser-skill 扩展（弹窗变绿）。**不要自己起浏览器。**
- `MISS rules` → 让使用者把 `assets/rules.example.json` 复制成 `~/.boss-apply/rules.json` 再改。
- 招呼语为空时**停下来问**，不要拿任何默认文案替使用者决定他要说什么话。

## 配置与数据在哪

| 位置 | 内容 |
|------|------|
| `~/.boss-apply/rules.json` | 城市、关键词、判分阈值、招呼语、间隔、每轮上限 |
| `~/.boss-apply/state/chatted.json` | 已打招呼的岗位 URL，跨轮去重 |
| `~/.boss-apply/state/rounds-YYYY-MM.json` | 每条岗位一行记录（含 `failure_code`） |

换 `BOSS_APPLY_HOME` 就是换一整套配置与记录 —— 同一台机器多人各用各的号靠这个隔离。
**skill 目录里永远不放用户数据**，更新 skill 会整目录覆盖。

## 一轮怎么走

```
1 自检        check_env.py 通过 + 读 rules.json + 定下本轮 round_id（如 20260930-140500）
2 开会话      bsk session start  → 记下 4 位 session id，之后每条命令都带 --session <id>
3 登录核对    bsk navigate <BOSS 搜索页> → bsk observe；落登录页就交人工（红线 1）
4 取列表      每个关键词：滚动 screens_per_keyword 页 → bsk observe 取岗位卡片
5 逐个岗位    python scripts/state.py seen 判重 → 点卡片开详情 → 读任职要求
6 判分        你自己读 JD 与使用者画像打分：score + 一句话理由
              score < match_threshold → record skipped_low_score，下一个
              阈值-15 ≤ score < 阈值 → 追问自己"到底卡在哪一条硬性要求"，把答案记进 detail
7 打招呼      只对过阈值的岗位，两套机制都要认：
              a) 点「立即沟通」→ 出现输入框 → 填招呼语 → 点发送
              b) 弹出「已向BOSS发送消息 / 留在此页 / 继续沟通」→ 点「继续沟通」
                 （「留在此页」什么都不做，不能点），进会话核对气泡文案
                 与本轮招呼语一致才算成功；对不上就按本账号文案补发一条
              发之前查 count 与 remaining，到上限立刻停止发送（但仍把剩下的岗位判完）
8 落记录      每个岗位一条：python scripts/state.py record --json '{...}'
              发送成功即落库，不要等一轮跑完再补
9 收尾        bsk session stop <id>（出错路径也要 stop）
              python scripts/state.py summary --round <id> → 按这张表的 code 讲给人听
```

## 频率

- 两次发送之间随机等 `interval_seconds`（默认 6~14 秒）。**不许为了"补进度"连发**。
- 关键词之间等 3~8 秒再翻页。
- 单轮翻页数受 `screens_per_keyword` 限制，别越翻越深。

## 失败归因

每个岗位必须落到 `references/failure-codes.md` 里的某个 code。汇报格式：

```
本轮 20 投：发送 8 / 分数不够 6 / 岗位已沟通过 3 / 读不到 JD 2 / 卡在无沟通按钮 1
```

不要只说"失败了"。同一个 code 连出 3 次以上，停下来说明"页面可能变了"，
把 `detail` 原文给用户看，而不是换个按钮继续试。

## 细节在哪

- `references/boss-dom.md` — 选择器、两套打招呼机制的现场、URL 会骗人的两种情形
- `references/failure-codes.md` — 每个 code 的判据与处置
- `references/rules-format.md` — rules.json 字段与边界
