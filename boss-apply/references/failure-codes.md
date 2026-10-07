# 失败归因表

每条记录都带一个 `failure_code`。`state.py summary --round <id>` 按它分组，
一轮结束要把这张表里的"处置"念给人听，而不是只说"失败了"。

| code | 判据（可观测） | 处置 |
|------|----------------|------|
| `sent` | 自己的消息气泡 `.message-item.item-myself` 里出现了本轮招呼语文案 | 记成功，URL 进 `chatted.json` |
| `skipped_hard_veto` | `python scripts/veto.py check --title ... --jd ...` 返回 `veto=true`（普工/主播/快递/保洁/销售这一类，或写着坐班包吃住到场） | 不发招呼语，不判分，不进去重表；这类不是分低，是压根不该主动沟通 |
| `skipped_low_score` | agent 判分 `< match_threshold` | 不打招呼，不进去重表（下一轮可再判） |
| `already_chatted` | URL 命中 `chatted.json`，或按钮文案是「继续沟通」 | 跳过，避免同一 HR 收到两条一样的招呼 |
| `no_chat_button` | 点开卡片后右侧详情栏取不到「立即沟通」（观察类名 `A.op-btn.op-btn-chat`），老式详情页类名里也没有 `btn-startchat` | 落记录并跳过；连续 3 个都这样就该停下来怀疑页面变了 |
| `auto_greet_dialog` | 出现「已向BOSS发送消息 / 留在此页 / 继续沟通」弹窗 | 点「继续沟通」进会话核对；气泡文案不是本账号文案时补发，读不到列表就不许发 |
| `jd_unreadable` | 详情页任职要求取不到 | 不判分、不打招呼。宁可不投也不能盲投 |
| `not_logged_in` | 稳定后的 URL 落在 `/web/user` 或 `passport.`，**且**浏览器里没有未过期登录项 | 立刻停本轮，`bsk request-help` 交人工登录。永不代填手机号与验证码 |
| `wind_control` | 页面出现验证码/滑块/"访问过于频繁" | 立刻停本轮并交人工；不做任何自动重试，重试只让风控计数继续累加 |
| `interval_blocked` | 距上次发送不足 `interval_seconds` 下限 | 等到点再发，不允许"补进度"而连发 |
| `error` | 其它异常（bsk 命令失败、页面结构变了） | 记 `detail` 原文前 200 字，跳过这个岗位继续下一批；连续 5 个 error 就停轮 |

判"未登录"必须两路证据一致：只看 URL 会把"正在跳转的会话页"误判成登录墙，
进而把人家的登录态当过期处理。
