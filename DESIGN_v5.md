# 真实玩家评论研究所 — 设计文档 v5

> **本文档取代 [DESIGN.md (v4)](./DESIGN.md)**，是项目当前的权威设计来源。
> v4 仍保留作为历史档案，但 v5 之后的所有决策以本文档为准。
> 设计基础：用户上传的 `ai_game_xhs_content_system_design.md`。

---

## 0. 重要变化（v4 → v5）

| 维度 | v4 | v5 |
|---|---|---|
| **产品定位** | 小红书游戏内容博主 | **真实玩家评论研究所**（数据驱动消费决策） |
| **核心护城河** | 自动梗 + style 学习 | **数据可视化 + 评论 taxonomy + 消费决策评级** |
| **博主人设** | 抽象、放肆、用梗 | **理性消费分析员**（不夸不黑，给结论） |
| **标题约束** | ≤20 字，无名字要求 | **必须含《游戏名》** + 数据感 + 消费判断 |
| **Hashtag** | 3-5 个自由 | **5-10 个**，含固定 set + 内容类型 set + 游戏名/类型 |
| **输出结构** | 单块文本 | **多页（7-9 页），每页带图表 + 怎么读 + 结论** |
| **梗 / trend 系统** | V1 核心 | **降级**（理性研究所不靠时髦梗，meme_phrases 表暂搁置） |
| **每篇必含** | 钩子 + 玩家原话 | **A/B/C/D/E 消费等级 + 适合谁 / 不适合谁 / 关键风险** |

---

## 1. 账号定位（v5 新增）

**名称（首选）**：真实玩家评论研究所

**简介**：
> 我用 AI 看 Steam 评论和公开数据。每天研究一款游戏：值不值得买、该不该等折扣、差评到底在骂什么。

**人设固定表达**：
> 我不是代替你玩游戏，我是替你读完上千条真实玩家评论，并用数据看这款游戏现在值不值得买。

**风格原则**：
- ✅ 真实评论、数据支撑、明确结论、适合谁/不适合谁、不无脑吹、不无脑骂、观点直接但不过度攻击
- ❌ "垃圾游戏"、"年度最烂"、"千万别买"、"必买神作"、"闭眼入"
- 📝 推荐替代："现在不建议普通玩家原价冲" / "更适合等首个大补丁" / "如果你在意优化，可以先观望"

---

## 2. 标题与 Hashtag 硬约束

### 标题
- **必须包含《游戏名》**
- 必须有冲突点、消费判断、数据感
- 字数 16-30（v4 上限是 20，因 v5 要加书名号 + 名字，放宽）

**格式参考**：
```
《{游戏名}》发售后差评暴增，我看了玩家主要在骂什么
《{游戏名}》史低了，但我看完评论不建议所有人买
《{游戏名}》玩了 80 小时还给差评，玩家到底在骂什么
```

### Hashtag
**每篇 5-10 个**：

| 类别 | 数量 | 来源 |
|---|---|---|
| 固定通用 | 3-4 | `#Steam游戏 #游戏值不值得买 #真实玩家评论 #游戏评论研究所` 等 |
| 内容类型 | 2-3 | 按 content_type 选（详见下表） |
| 游戏元数据 | 2-3 | `#{游戏名}` + `#{游戏类型 from steam_tags}` |

**内容类型对应 hashtag 组**：
- **差评爆炸**：`#差评如潮 #首发翻车 #游戏避坑 #Steam差评`
- **口碑反转**：`#口碑反转 #游戏更新 #现在值得玩吗`
- **小众神作**：`#小众游戏 #独立游戏 #冷门神作`
- **折扣值不值**：`#Steam折扣 #史低游戏 #愿望单 #游戏省钱`
- **评论区反差**：`#Steam评论区 #玩家真实评价 #玩很久还差评`

---

## 3. 7-Agent 架构（v5 重写）

### MVP（Sessions 4-7）

```
1. Trend Scout 热点侦察员           ← 重构自现有 signal_agent
2. Review Miner 评论挖掘员           ← 新建
3. Buy-or-Wait Analyst 购买建议分析员 ← 新建
4. Data Viz Designer 可视化设计员    ← 新建（先 spec，后渲染）
5. Contradiction Finder 反差猎人     ← 集成进 Trend Scout 输出
6. XHS Editor 小红书编辑             ← 重构自现有 content_agent，输出多页
7. Compliance Guard 风险检查员       ← 新建（替代当前 prohibited_words + formatter 部分）
```

### 后续（V2+）

```
8. Monetization Planner 变现规划员（30+ 篇后才接）
9. Performance Learner 表现复盘员（积累互动数据后才接）
```

### Agent 之间的数据契约

```
┌──────────────────┐  candidate_games[]
│ Trend Scout      │─────────────────────┐
└──────────────────┘                     │
                                         ▼
┌──────────────────┐  themes / playtime / ┌──────────────────┐
│ Review Miner     │◀─ representative_reviews ─│ pipeline 调度  │
└──────────────────┘                     │
        │                                │
        ▼ review_summary                 │
┌──────────────────┐                     │
│ Buy-or-Wait      │─ rating + suitable_for ─┤
└──────────────────┘                     │
        │                                │
        ▼                                │
┌──────────────────┐ visual_plan         │
│ Data Viz Designer├──────────────────────┤
└──────────────────┘                     │
                                         ▼
                              ┌──────────────────┐
                              │ XHS Editor       │
                              └──────────────────┘
                                         │
                                         ▼  multi-page post
                              ┌──────────────────┐
                              │ Compliance Guard │
                              └──────────────────┘
                                         │ approved post
                                         ▼
                              ┌──────────────────┐
                              │ Telegram push    │  ←  media group + 多图 caption
                              └──────────────────┘
```

---

## 4. 6 个 Content Type（替换 v4 的 5 个模板）

| Content Type | 触发信号 | 核心反差 |
|---|---|---|
| 差评爆炸 | negative_burst | 高热度新游 vs 差评爆 |
| 口碑反转 | positive_burst (+ 老游戏) | 曾经凉了 vs 最近真香 |
| 小众神作 | 评论数低 + 好评率高（新信号） | 不出名 vs 评价极高 |
| 折扣值不值 | 价格史低 + 评论争议（新信号，P1） | 价格诱人 vs 评论区有坑 |
| 评论区反差 | 短时差评高 vs 长时好评高（新信号） | 玩很久还差评 等情况 |
| 新品爆款 | new_release_spike | 上架不久就爆 |

---

## 5. Review Miner — 14 类固定 Taxonomy（v5 新增）

```yaml
review_themes:
  - 性能/优化
  - 服务器/联机
  - Bug/崩溃
  - 价格/内容量
  - 玩法/手感
  - 剧情/美术
  - 平衡性
  - 中文/本地化
  - DLC/商业化
  - 反作弊/账号
  - 新手引导/UI
  - 后期重复
  - 更新方向
  - 其他
```

放在 `tuning.yaml` 下，user 可以增删改。

**打标方式**：batch LLM 调用（一次 prompt 塞 30-50 条评论，输出 JSON 数组），每条评论返回 1-2 个 theme。

---

## 6. Playtime 分组（v5 新增）

```yaml
playtime_buckets:
  short_negative:  { max_hours: 2,   voted_up: false }   # 短时差评 — 首发体验劝退
  medium_negative: { min_hours: 2, max_hours: 10, voted_up: false }
  long_negative:   { min_hours: 20, voted_up: false }    # 长时差评 — 后期内容暴雷
  long_positive:   { min_hours: 20, voted_up: true }     # 长时好评 — 核心玩家爱
```

来源：Steam appreviews 的 `playtime_at_review` 字段（已经在 collector 里采但未用）。

---

## 7. Buy-or-Wait 评级体系（v5 新增）

```yaml
buy_recommendation:
  rating: A | B | C | D | E
  one_sentence: "..."
  suitable_for: [...]
  not_suitable_for: [...]
  key_risks: [...]
  wait_for: "..."   # 等什么 (e.g., "首个大补丁" / "20-30% 折扣")
```

| 等级 | 含义 |
|---|---|
| A | 现在可入 |
| B | 等首个大补丁 |
| C | 等 20-30% 折扣 |
| D | 只推荐粉丝 / 特定玩家 |
| E | 暂时避雷 |

---

## 8. 多页输出结构（v5 新增）

每个 Post 现在是多页的。`posts.pages` 字段（JSON array）：

```python
[
    {
        "page": 1,
        "type": "cover",
        "title": "《Game Name》差评暴增 / 玩家主要骂这 3 点",
        "body": null,
        "chart_type": "cover_card",     # Pillow 拼大字
        "data": {...},
        "image_path": "data/generated_images/post_42/page_1.png"
    },
    {
        "page": 2,
        "type": "conclusion",
        "title": "一句话结论",
        "body": "它不是完全不能玩，但首发状态很劝退。如果你不是核心粉丝，不建议现在原价冲。",
        "chart_type": "text_card",
        ...
    },
    {
        "page": 3,
        "type": "trend",
        "title": "好评率是从这天开始掉的",
        "body": "最近评论已经明显变差。",
        "chart_type": "positive_rate_timeline",
        "data": {...},
        "image_path": "data/generated_images/post_42/page_3.png"
    },
    ...
]
```

**MVP 7 页模板**：
1. 封面（Pillow 文字海报）
2. 一句话结论（纯文字卡片）
3. 好评率时间线（matplotlib 折线）
4. 差评主题占比（matplotlib 横向条形图）
5. 游玩时长 × 好评/差评分布（matplotlib 堆叠柱）
6. 代表性评论卡片（Pillow 文字卡，2-3 条评论原文）
7. 购买建议 + 评论区引导（Pillow 文字卡，A-E 评级 + 适合谁/不适合谁）

---

## 9. 6 个图表 spec（3 MVP + 3 P1）

### MVP（Session 7 实现）

| 图 | 类型 | 字段 | 渲染 |
|---|---|---|---|
| **好评率时间线** | 折线 + 滚动平均 | `date, daily_pos_rate, rolling_7d_pos_rate, event_marker` | matplotlib |
| **差评主题占比** | 横向条形 | `theme, share` | matplotlib |
| **游玩时长 × 好评差评** | 堆叠柱 | `playtime_bucket, voted_up, count` | matplotlib |
| **代表性评论卡片** | 文字卡 | `voted_up, playtime, theme, quote, my_summary` | Pillow |
| **封面** | 文字海报 | `game_name, hook_line_1, hook_line_2` | Pillow |
| **结论 / 建议** | 文字卡 | `rating, one_sentence, suitable_for, not_suitable_for` | Pillow |

### P1（Session 8+）

| 图 | 类型 | 字段 |
|---|---|---|
| 每日好评/差评数 | 堆叠柱 | `date, pos_count, neg_count` |
| 价格历史 | 折线 | `date, price, is_historical_low` |
| 在线人数变化 | 折线 | `date, peak_players` |

### 视觉规范（全图通用）

```yaml
viewport: 1080×1080 (小红书首图比例)
font: assets/NotoSansCJKsc-Bold.otf + assets/NotoSansCJKsc-Regular.otf
colors:
  positive: "#3FB950"     # 绿色
  negative: "#E5484D"     # 红色
  neutral:  "#8B949E"     # 灰色
  price:    "#F0B72F"     # 金黄
  online:   "#388BFD"     # 蓝色
  bg:       "#FFFFFF"
title_font_size: 64
axis_label_font_size: 32
annotation_font_size: 28
```

每张图必须包含：图标题（中文短句）+ 图表主体 + 一句"怎么读"+ 一句结论。

---

## 10. Compliance Guard 规则（v5 新增 / 替换 prohibited_words）

**检查项**：

| 检查 | 失败处理 |
|---|---|
| 标题是否含《游戏名》 | block，必须修 |
| Hashtag 数量 5-10 + 含固定 set | block |
| 包含"垃圾/必买/千万别买/年度最烂"等绝对化语言 | 自动替换为中性表达 |
| 评论原文引用 > 100 字 | warn，建议缩减 |
| 出现"所有玩家/全部玩家在骂" | 替换为"最近差评比较集中" |
| 出现未经确认的开发者指控 | warn |
| 出现剧透关键词 | warn |
| 购买建议是否避免绝对化 | 检查 wait_for 表达 |

**替换表**（写进 `tuning.yaml`）：

```yaml
compliance:
  banned_phrases_to_rewrite:
    "垃圾游戏":     "目前首发状态不适合普通玩家原价冲"
    "千万别买":     "如果你在意优化和稳定性，建议先观望"
    "必买神作":     "如果你喜欢这个类型，值得放进愿望单"
    "所有玩家都在骂": "最近差评比较集中，主要集中在几个问题上"
    "全部玩家都觉得": "最近差评比较集中，部分玩家反映"
    "闭眼入":       "如果你喜欢这个类型，可以考虑"
    "年度最烂":     "目前首发状态争议较大"
```

---

## 11. Telegram 推送（v5 升级）

**形态**：media group + 每图 caption 写该页内容。

```
Bot 推送时：
1. 先发 1 条文字简报（候选 #N + 信号摘要 + A/B/C/D/E 评级 + Steam 链接 + 反馈说明 + 2 按钮）
2. 再发 1 条 media group，包含 7 张图（cover + 6 页 PNG）
   - 每张图的 caption = 该页的"图标题 + 一句结论"
   - 你直接从 Telegram 双击保存图，发到小红书
3. 按钮挂在第 1 条文字消息上（[✅ 发布] [❌ 跳过]）
4. 你 reply 到任何一条消息（文字 / 图），bot 都映射回这个候选 → 触发 rewrite
```

> **多图 reply 映射**：所有 7 张图的 `message_id` 都会跟 post_id 关联（新表 `post_messages`），bot 收到 reply 可以 query 反查。

---

## 12. 数据源新增

| 数据 | 来源 | Session |
|---|---|---|
| `playtime_at_review` per 评论 | Steam appreviews（已采，未用） | S5 启用 |
| `daily_positive_rate` 时间线 | 自建 DailyReviewStats 表 + cron 聚合 | S5 |
| 评论主题打标 | LLM batch classify（mini 版 S4.5 已上） | S5 持久化到 DB |
| **新品榜 / 热销榜 / 折扣榜 作为独立 entity** | Steam `featuredcategories` (已拉，未充分用) | **S5（重要）** |
| **优惠节信息** | Steam featured.specials 字段（已拉） | S5 启用 |
| 价格历史 | **IsThereAnyDeal API**（user 已确认申请 key） | S8 P1 |
| 在线人数历史 | 自建 daily snapshot | S8 P1 |
| 多平台舆情（Reddit / B 站 / NGA） | V2 范围 | P2 |

### 12.1 ⚠️ 候选池问题（S5 必修）

**现状**：`SteamCollector.collect()` 只迭代 `top_concurrent`（Top 100 在线榜）作为 entity，
然后用 `featured.top_sellers` / `featured.new_releases` **仅作为 flag**（is_top_seller / is_new_release）。

**结果**：候选全是 CS2/Dota2/Apex/守望 等常驻巨头。这些游戏：
- 评论稳定，没有 negative_burst / positive_burst
- 在线人数高但 player_spike via fallback 只测出 "current vs all_time_peak ratio"，
  对它们是噪声而非新闻
- Haiku 正确拒绝所有，代码 fallback 强制批准 → 错配 content_type

**S5 修法**：让 `SteamCollector.collect()` 拼合三个池子：
1. Top 30 concurrent — 现有
2. Top 30 new_releases — 新增
3. Top 30 top_sellers — 新增（去重；不在 1+2 里的）
4. Top 30 specials — 新增（折扣中的游戏，标 `discount_pct` 字段）

去重后大概 60-80 个独特 entity，覆盖：新品爆款 / 老游戏复活 / 折扣值不值 / 评论争议都有素材。

### 12.2 信号检测器扩展（S5 同步）

基于扩展后的 entity pool，新增/调整信号：

| 新信号 | 触发条件 | 对应 content_type |
|---|---|---|
| `discount_event` | `entity.discount_pct >= 20` AND `entity.recent_reviews_count >= 50` | 折扣值不值 |
| `hidden_gem_signal` | `total_reviews >= 100 AND <= 5000` AND `historical_positive_rate >= 0.85` AND age > 180 | 小众神作 |
| `playtime_split` | （S5 Review Miner 输出后才能算）短时差评 >= 50% AND 长时好评 >= 50% | 评论区反差 |

`player_spike` fallback 阈值应该 **age < 90 才触发**（避免常驻巨头）— S5 调 tuning.yaml。

---

## 13. 数据库变更（v5）

### 新增表

```sql
-- 每天每游戏一条评论统计快照
CREATE TABLE daily_review_stats (
    id INTEGER PK,
    appid TEXT NOT NULL,
    date DATE NOT NULL,
    pos_count INTEGER,
    neg_count INTEGER,
    total_count INTEGER,
    daily_pos_rate REAL,
    rolling_7d_pos_rate REAL,
    UNIQUE(appid, date)
);

-- 每条评论的 theme 标注（Review Miner 输出）
CREATE TABLE review_themes (
    id INTEGER PK,
    appid TEXT NOT NULL,
    review_id TEXT NOT NULL,
    theme TEXT NOT NULL,         -- 14 类之一
    voted_up BOOLEAN,
    playtime_at_review_min INTEGER,
    timestamp_created TIMESTAMP,
    review_text TEXT,            -- 截断 < 500 字
    UNIQUE(appid, review_id, theme)
);

-- 价格历史（P1）
CREATE TABLE price_snapshots (
    id INTEGER PK,
    appid TEXT NOT NULL,
    date DATE NOT NULL,
    price_cents INTEGER,
    discount_pct INTEGER,
    is_historical_low BOOLEAN,
    UNIQUE(appid, date)
);

-- 在线人数历史（P1）
CREATE TABLE player_count_snapshots (
    id INTEGER PK,
    appid TEXT NOT NULL,
    snapshot_at TIMESTAMP NOT NULL,
    current_players INTEGER,
    peak_24h INTEGER
);

-- 多图推送的 message_id 映射
CREATE TABLE post_messages (
    id INTEGER PK,
    post_id INTEGER FK,
    telegram_message_id INTEGER,
    page_number INTEGER NULL,    -- NULL = 主文字消息
    sent_at TIMESTAMP
);
```

### posts 表新增字段

```sql
ALTER TABLE posts ADD COLUMN content_type TEXT;        -- 差评爆炸 / 口碑反转 / ...
ALTER TABLE posts ADD COLUMN pages JSON;               -- 多页结构
ALTER TABLE posts ADD COLUMN cover_text TEXT;          -- 封面文字
ALTER TABLE posts ADD COLUMN comment_prompt TEXT;      -- 评论区引导句
ALTER TABLE posts ADD COLUMN buy_rating TEXT;          -- A / B / C / D / E
ALTER TABLE posts ADD COLUMN suitable_for JSON;        -- ["..."] list
ALTER TABLE posts ADD COLUMN not_suitable_for JSON;
ALTER TABLE posts ADD COLUMN key_risks JSON;
ALTER TABLE posts ADD COLUMN wait_for TEXT;
ALTER TABLE posts ADD COLUMN themes_summary JSON;      -- {theme: share, ...}
ALTER TABLE posts ADD COLUMN playtime_buckets JSON;    -- {short_neg: n, ...}
```

### 保留但不再主用

- `meme_phrases` — schema 保留，V2 想重启梗系统时复用
- `exemplar_posts` — schema 保留，作 **可选** 风格参考（user 觉得理性研究所需要的 examples 比 meme 风格少）
- 5 个旧 templates 整批替换（不删表，删掉 prompt_versions 里旧记录即可）

---

## 14. Session 拆分（MVP = S4-S7）

| Session | 主题 | 内容 | 估时 | 验证 |
|---|---|---|---|---|
| **S4** | **身份重置 (P0a)** | persona 重写 / templates 替换 / hashtag policy / 标题硬约束 / 旧 prohibited_words 改 banned_phrases / 清空 posts 表 | 4-5h | 跑一次 pipeline，候选语气 = 研究员，标题带《》，hashtag 5-10 |
| **S5** | **Review 深度 (P0b)** | Review Miner agent + 14 类 taxonomy + playtime bucket 聚合 + DailyReviewStats 表 + daily aggregation cron | 5-6h | inspect_db 能看主题占比 + playtime 分组 |
| **S6** | **决策 + 合规 (P0c)** | Buy-or-Wait Analyst + Compliance Guard + 多页 schema + Post 字段扩展 + XHS Editor 重构输出多页 | 5-6h | 候选包含 A-E 评级 + 多页结构 |
| **S7** | **可视化 (P0d)** | matplotlib chart renderer + Pillow 文字卡 + Data Viz Designer agent + Noto 字体打包 + Telegram media group 推送 | 6-7h | 真实 PNG 推到 Telegram |

### P1（S8+）

| Session | 主题 |
|---|---|
| S8 | 折扣值不值（IsThereAnyDeal + 价格历史折线图） |
| S9 | 在线人数历史（SteamCharts）+ 价格历史折线图（ITAD）+ 同类游戏对比（SteamSpy） |

### P2（V2 多平台观点挖掘）

**设计原则：外部平台数据是"观点矿山"，不是数据补充。**

V2 的核心定位是：从 Reddit / 小黑盒 / NGA 等平台挖掘高质量玩家观点和讨论帖，
把最有价值的视角"洗稿"为我们 post 的叙事基石，再用数据图表验证和支撑结论。
流程：优质外部观点 → LLM 提炼核心论点 → 与 Steam 数据交叉印证 → 生成有据可查的内容。

**评论语言维度：** Steam 评论保留多语言，语言分布本身是信号：
- 中文评论占比高 → 国内玩家关注度高，适合直接引用中文原话
- 英文为主 → 需要角度转化；可以用"外网玩家普遍认为……"作为内容视角
- 两者都有且观点分歧 → 中外玩家差异本身是选题角度

**数据源优先级（无需 key 优先）：**
| 平台 | 接入方式 | 内容价值 |
|------|---------|---------|
| Reddit | API（已有 .env 字段）| 英文深度讨论、攻略、吐槽贴 |
| 小黑盒 | H5 接口（逆向，无需 key）| 中文玩家视角，贴近小红书受众 |
| NGA | RSS / 爬虫 | 硬核玩家，适合策略向内容 |

### P3（V3+）

- Performance Learner（需积累 30+ 篇互动数据后）
- Monetization Planner
- 个性化推荐

---

## 15. 关键不变量（v5 版）

继承自 v4 的同时新增：

1. ✅ LLM 调用必经 `llm_tracker.call_llm`
2. ✅ DB 必经 repositories
3. ✅ 配置从 `settings` 或 `tuning` 读，不散落 os.getenv
4. ✅ 每个 pipeline 创建 PipelineRun 记录
5. 🆕 **标题必须含《游戏名》**（Compliance Guard 强制）
6. 🆕 **Hashtag 必须 5-10 个含固定 set**
7. 🆕 **绝对化表达自动替换**（不出现"千万别买/必买/垃圾游戏"等）
8. 🆕 **每条评论原文引用 ≤ 100 字**
9. 🆕 **代表性评论必须标注（好/差评 + 时长 + 主题）**
10. 🆕 **每张图必须有：图标题 + 怎么读 + 结论**

---

## 16. 文档变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-05-07 | v3 | 用户初始计划（Telegram + Steam + meme） |
| 2026-05-07 | v4 | 引入 trend service / domain pack / 双向 Telegram |
| 2026-05-10 | v5 | **产品定位重写**：从内容博主转为数据研究所；7 agent 架构；多页输出 + 图表 MVP 必含；A-E 评级；14 taxonomy |
