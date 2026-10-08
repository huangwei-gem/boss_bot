# rules.json 字段

放在 `~/.boss-apply/rules.json`（或 `BOSS_APPLY_HOME` 指向的目录）。skill 目录里只有 example。

| 字段 | 含义 | 边界 |
|------|------|------|
| `cities` | 搜索城市，逐个跑 | 空数组=自检就判 rules 不通 |
| `keywords` | 搜索关键词 | 一轮里逐个关键词串行跑，不并发 |
| `match_threshold` | agent 判分通过线（0-100） | 低于此值不打招呼 |
| `reject_title_keywords` | 岗位类型词（查标题；标题没交代岗位是什么时也查 JD；不认否定式，合伙人/老师这一族只查标题） | 命中记 `skipped_hard_veto`，不发招呼语；缺省用脚本内置那张表 |
| `reject_keywords` | 条件否决词（标题与 JD 都查，认否定式） | 同上；"（线上）不坐班"这种否定说法不算命中 |
| `probe_band` | 边界带宽度：只追问 `阈值-band ≤ score < 阈值` | 0 = 关掉追问 |
| `greeting` | 招呼语文案（每轮固定一条） | 空字符串时 skill 必须停下来问，不能拿默认文案替使用者决定 |
| `interval_seconds` | 两次打招呼之间的随机间隔 `[min,max]` | min<5 会被抬到 5，并说明为什么 |
| `round_cap` | 本轮最多发多少条招呼 | 硬上限 50，超过按 50 处理并说明 |
| `screens_per_keyword` | 每个关键词翻几页 | 建议 ≤5，翻得深了列表质量明显下降 |

多人各用各的：换 `BOSS_APPLY_HOME` 就换一整套配置与记录。
