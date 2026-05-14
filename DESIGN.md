# 小红书游戏内容 Agent — 设计文档 v4

> ⚠️ **本文档已被 [DESIGN_v5.md](./DESIGN_v5.md) 取代（2026-05-10）。**
> v5 把产品定位从"内容博主"重写为"数据研究所"，新增多页 + 图表 + A-E 评级。
> 本 v4 文档作为历史档案保留，反映 V0 落地时的设计假设。
>
> ---
>
> 本文档是项目的**曾经的**唯一权威设计来源。代码、HANDOFF、README 都从这里派生。
> 任何架构决策变更先改这里，再改代码。

---

## 1. 项目目标

半自动化的"小红书游戏内容创作助手"：

- **采集**真实游戏数据（Steam 信号 + 中英文社区 trend）
- **判断**哪些信号值得写
- **生成**符合小红书风格的图文候选
- **推送**到 Telegram 让 Yiming 审核 / 反馈修改 / 最终决定发布
- **记录**发布后的互动数据，作为后续优化的依据

**关键边界：**
- 发布动作仍由 Yiming 在小红书 App 手动完成（不做发布自动化，规避平台风险）
- 创作的"风格"和"梗"必须能**自动从外部信号中发现**，不依赖手动维护静态字典

---

## 2. 设计原则

| 原则 | 含义 |
|------|------|
| **LLM 只在判断和创作节点介入** | 数据拉取、评分、去重、推送都用 Python 确定性逻辑，省钱+可控 |
| **风格/梗是 service 不是 config** | meme_phrases 表带生命周期；exemplar_posts 表存爆款样本；prompt 运行时拼装 |
| **Domain pack 轻抽象** | 接口预留（Collector/Signal/Template ABC），V0 只实现 game pack |
| **本地 / 服务器同代码** | SQLite ↔ PostgreSQL 走 SQLAlchemy；scheduler 用 APScheduler；polling Telegram |
| **小步可恢复** | 每模块可独立测试；HANDOFF.md 维持进度可见性；commit 频繁 |
| **预算硬保护** | 日 LLM 花费上限，超 80% 预警，超 100% 硬停 |

---

## 3. 架构总览

```
┌────────────────────┐    ┌──────────────────────┐    ┌────────────────────┐
│   采集层            │    │   处理层              │    │   Agent 层          │
│  ──────             │    │  ──────              │    │  ──────             │
│ Steam Official     │───▶│ signal_detector      │───▶│ signal_agent        │
│ SteamSpy           │    │ (5 维度评分)          │    │ (Haiku: 值不值得写) │
│ Reddit (PRAW) ★    │    │ watchlist_manager    │    │                     │
│ Bilibili (热门+弹幕)│    │ trend_extractor ★    │    │ content_agent       │
│ NGA / 贴吧 / 虎扑   │    │ (Haiku: 提梗)        │    │ (Sonnet: 写文案)     │
│ RSS (V1)           │    │ meme_lifecycle ★     │    │                     │
└────────────────────┘    │ deduplicator         │    │ rewrite_agent  ★    │
                          │ formatter (违禁词)   │    │ (Sonnet: 按反馈改)   │
                          └──────────────────────┘    └────────────────────┘
                                                              │
                                                              ▼
                          ┌──────────────────────────────────────────┐
                          │  Telegram Bot (双向)                      │
                          │  ─ 推送候选 (含 [✅发布] [❌跳过] 按钮)    │
                          │  ─ 接受文字回复 → 触发 rewrite_agent      │
                          └──────────────────────────────────────────┘
                                                              │
                                                              ▼
                                                ┌────────────────────────┐
                                                │  Yiming 在小红书 App    │
                                                │  手动复制粘贴发布       │
                                                └────────────────────────┘

★ = 相对 v3 文档新增 / 升级
```

### 数据流（一次完整 pipeline）

```
[trend pipeline / 每天 1 次]
  trend_collector 拉 Reddit + B 站 + NGA/贴吧/虎扑
    → trend_extractor (Haiku) 提取候选梗短语
    → 去重写入 meme_phrases (status: rising)
  meme_lifecycle 跑一次状态机（更新 frequency_score、流转 status）

[content pipeline / 每天 2-3 次]
  steam_collector 扫 top 100 在线榜 + 评论 + 新品榜
    → signal_detector 算 5 维度分数 → game_signals
    → watchlist_manager 更新 active_pool / watch_pool
  signal_agent (Haiku) 选出值得写的 top N，给出推荐角度
    → prompt_assembler 拼 prompt:
       base_persona + template + active_memes (top 5 rising) + few-shot exemplars (3 条)
    → content_agent (Sonnet) 生成标题 + 正文 + hashtag 建议
    → formatter 格式化 + 违禁词过滤
    → telegram_push 推送（带 inline 按钮 + 触发说明 + 数据源链接）
       posts 表写入 state=pending

[Telegram bot 常驻进程]
  收到按钮 ✅发布   → posts.state = approved
  收到按钮 ❌跳过   → posts.state = rejected
  收到文字回复     → rewrite_agent 用 [原文 + 反馈] 重写
                  → 推送新版本，posts.state = iterating
                  → review_iterations 数组追加一条
```

---

## 4. 目录结构

```
xhs-game-agent/
├── pyproject.toml             # uv 项目定义 + 依赖
├── .env.example
├── .gitignore
├── README.md
├── DESIGN.md                  # 本文档
├── HANDOFF.md                 # 跨 session 进度交接
│
├── src/xhs_agent/
│   ├── __init__.py
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py        # 环境变量 + 默认值（pydantic-settings）
│   │   └── persona.yaml       # 基础人设（静态，因为人设不该频繁变）
│   │
│   ├── domain/                # ★ Domain pack 抽象
│   │   ├── base.py            # Domain ABC / Collector ABC / Signal ABC
│   │   └── games/             # V0 唯一实现
│   │       ├── __init__.py
│   │       ├── domain.py      # GameDomain 类，注册自己的 collectors/signals/templates
│   │       ├── collectors/
│   │       │   ├── steam.py
│   │       │   ├── steamspy.py
│   │       │   ├── reddit_gaming.py
│   │       │   └── bilibili_gaming.py
│   │       ├── signals/
│   │       │   ├── negative_burst.py
│   │       │   ├── positive_burst.py
│   │       │   ├── player_spike.py
│   │       │   ├── new_release_spike.py
│   │       │   └── review_surge.py
│   │       └── templates.py   # negative_review_burst / hidden_gem / comeback / meme_reaction
│   │
│   ├── trend/                 # ★ 风格/梗自动发现服务
│   │   ├── collector.py       # 多源原始文本采集
│   │   ├── extractor.py       # LLM (Haiku) 提取候选梗
│   │   ├── lifecycle.py       # rising → peak → decaying → dead 状态机
│   │   └── exemplar_manager.py
│   │
│   ├── agents/
│   │   ├── signal_agent.py
│   │   ├── content_agent.py
│   │   └── rewrite_agent.py   # ★ 处理 Telegram 文字反馈
│   │
│   ├── prompt/
│   │   └── assembler.py       # ★ runtime 动态拼装 prompt
│   │
│   ├── publishers/
│   │   └── telegram_push.py   # 推送 + 接收回调（python-telegram-bot）
│   │
│   ├── storage/
│   │   ├── db.py              # SQLAlchemy engine + session
│   │   ├── models.py          # 所有 ORM 表
│   │   └── repositories.py    # 数据访问封装
│   │
│   ├── orchestration/
│   │   ├── pipeline.py        # 主 pipeline（trend / content）
│   │   └── scheduler.py       # APScheduler 装载
│   │
│   ├── observability/
│   │   ├── logger.py          # structlog
│   │   └── llm_tracker.py
│   │
│   ├── budget/
│   │   └── guard.py
│   │
│   └── utils/
│       ├── deduplicator.py
│       ├── formatter.py       # 小红书格式化
│       └── prohibited_words.py
│
├── alembic/                   # 数据库迁移
│   ├── env.py
│   └── versions/
│
├── scripts/                   # CLI 工具
│   ├── run_trend_pipeline.py
│   ├── run_content_pipeline.py
│   ├── run_telegram_bot.py    # 常驻 bot 进程
│   ├── add_exemplar.py        # 录入爆款样本（文字 / 截图路径）
│   ├── add_meme_manual.py     # 应急手动加梗
│   ├── log_post_result.py     # 发布后回填数据
│   └── inspect_db.py          # 调试：看 DB 当前状态
│
└── tests/
    ├── fixtures/              # 真实 API 响应样本（json）
    ├── unit/
    └── integration/
```

---

## 5. 数据库 Schema

```python
# 简化 ORM 字段定义；实际见 src/xhs_agent/storage/models.py

class Post(Base):
    id: int  # PK
    title: str
    content: str
    image_paths: JSON  # array
    hashtags: JSON     # array
    template_used: str

    # 信号来源
    domain: str                   # "games" / future: "movies", "books"
    trigger_entity_id: str        # AppID / TMDB ID 等
    trigger_entity_name: str
    trigger_signals: JSON         # 触发的信号类型列表
    source_urls: JSON

    # Prompt 溯源
    prompt_version_id: int  # FK → prompt_versions.id
    used_meme_phrases: JSON       # ★ 这次注入了哪些梗
    used_exemplar_ids: JSON       # ★ 用了哪些 few-shot

    # Telegram 状态
    state: enum                   # pending / iterating / approved / rejected
    telegram_message_id: int
    review_iterations: JSON       # ★ [{feedback, revised_content, ts}]

    # 生成元数据
    generated_at: datetime
    llm_cost_usd: float
    pipeline_run_id: int  # FK

    # 发布后（手动回填）
    published_at: datetime
    edit_notes: str
    views_24h, likes_24h, saves_24h, comments_24h: int
    views_7d, likes_7d, saves_7d, comments_7d: int
    engagement_rate: float
    notes: str


class GameSignal(Base):
    id: int
    appid: str
    game_name: str
    signal_type: enum             # negative_burst / positive_burst / player_spike / ...
    score: float
    severity: enum                # urgent / normal / low
    raw_data: JSON                # 触发时的原始数据快照
    detected_at: datetime


class Watchlist(Base):
    appid: str  # PK
    status: enum                  # active / watch / dead
    last_signal_at: datetime
    last_pulled_at: datetime
    consecutive_no_signal_count: int


class MemePhrase(Base):
    id: int
    phrase: str                   # 唯一索引（小写规范化）
    language: enum                # zh / en
    source_platform: str          # reddit / bilibili / nga / weibo / manual
    first_seen_at: datetime
    last_seen_at: datetime
    occurrence_count: int
    frequency_score: float        # 衰减加权
    status: enum                  # rising / peak / decaying / dead
    notes: str                    # LLM 提取时的解释


class ExemplarPost(Base):
    id: int
    source_platform: enum         # xiaohongshu / weibo / manual
    raw_content: str
    screenshot_path: str          # 图片在本地的相对路径
    domain: str                   # games / ...
    template_match: str           # negative_review_burst / hidden_gem / ...
    style_tags: JSON              # LLM 自动标注：["吐槽", "高密度梗", "反预期开头"]
    engagement_metrics: JSON      # 如果有：likes/saves/comments
    used_count: int               # 被注入 prompt 的次数
    added_at: datetime
    added_by: str                 # "yiming" / "auto"


class PromptVersion(Base):
    id: int
    template_name: str
    prompt_text: str              # 完整 prompt 模板
    variables: JSON               # 占位符列表
    created_at: datetime
    notes: str
    is_active: bool


class PipelineRun(Base):
    id: int
    pipeline_type: enum           # trend / content
    started_at: datetime
    finished_at: datetime
    status: enum                  # success / partial / failed
    posts_generated: int
    total_llm_cost_usd: float
    errors: JSON


class LlmCall(Base):
    id: int
    pipeline_run_id: int  # FK
    purpose: enum                 # signal_judgment / extraction / copywriting / rewrite / ...
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    prompt_hash: str              # debug 用
    started_at: datetime
    duration_ms: int
    success: bool
    error_message: str
```

---

## 6. 5 维度信号检测

(沿用 v3 设计，简述)

| # | 信号 | 触发条件 | 强触发 |
|---|------|---------|-------|
| 1 | 差评爆发 | 7d 好评率下跌 >15% AND 评论 >100 | 下跌 >30% OR 评论 >500 |
| 2 | 好评爆发（老游戏复活）| 7d 好评率上涨 >10% AND 评论 >200 AND 上架 >180 天 | — |
| 3 | 在线人数异常 | 今日峰值 >10000 AND velocity (今日/30d 平均) >2.0 | velocity >5.0 |
| 4 | 新品爆款 | 上架 <7 天 AND (热销榜 <20 OR 在线 >5000) | — |
| 5 | 评论数激增 | 24h 评论 / 30d 平均 >3.0 | — |

**综合优先级：** urgent (任一强触发 OR 2+ 信号同时) / normal (1 个中等信号) / low (弱信号，进观察池)

---

## 7. Trend 系统（核心差异化）

### 7.1 数据源

| 源 | 接入方式 | 拉取内容 | 频率 |
|---|---------|---------|------|
| Reddit | PRAW (官方 API) | r/gaming, r/shittygaming, r/outside, r/gaming_irl 热帖标题+top comments | 每天 1 次 |
| Bilibili | bilibili-api-python | 游戏分区 (rid=4) top 20 视频标题 + 最新一段弹幕 | 每天 1 次 |
| NGA | requests + 简单 HTML 解析 | 游戏综合区热帖标题 | 每天 1 次 |
| 贴吧 | requests + HTML 解析 | 原神吧、Steam吧、黑神话吧热帖标题 | 每天 1 次 |
| 虎扑 | requests + HTML 解析 | 游戏区热帖标题 | 每天 1 次 |
| 微博 | (V1) | — | — |

**B 站白名单（V1 待办）：** 支持配置 UP 主 mid 列表，优先抓白名单 UP 主最近 N 条视频，用其评论区/弹幕作为更精准的风格语料。

### 7.2 Trend Extractor (Haiku)

```
Input: 一批原始文本（数百条标题/弹幕拼接）
Prompt: "从下面文本里识别 5-10 个本周高频出现的、非通用的网络/游戏圈用语，
         排除常见词如'游戏''好玩''厉害'等。每个用一行返回：
         <短语> | <出现频率估计：高/中/低> | <50 字以内解释>"
Output: 解析为结构化 list，写入 meme_phrases
```

### 7.3 生命周期状态机

| 状态 | 进入条件 | 离开条件 |
|------|---------|---------|
| **rising** | 第一次出现 | 7 天内 occurrence > 阈值 → peak；7 天内未再出现 → decaying |
| **peak** | 持续高频 | 14 天 occurrence 增速放缓 → decaying |
| **decaying** | 衰减期 | 7 天再出现 → 重回 rising |
| **dead** | 30 天无更新 | 不再注入 prompt |

`frequency_score = sum(occurrence × decay_weight^days_ago)`

### 7.4 Exemplar Manager

存储爆款样本，支持两种来源：

- `scripts/add_exemplar.py "<文字内容>" --image=<截图路径> --template=<模板名>` （CLI 录入）
- 后续可扩展：bot 端 `/add_exemplar` 命令（V1 加）

每次 content_agent 跑时，按 template 类型抽 3 条 exemplars 注入 prompt 作 few-shot。

---

## 8. Prompt 拼装（动态）

```python
def build_content_prompt(template_name, signal_context):
    persona = load_persona_yaml()
    template = templates[template_name]
    active_memes = meme_repo.fetch_active(top_k=5, min_freshness=0.3)
    exemplars = exemplar_repo.fetch_by_template(template_name, k=3)

    return f"""
{persona}

## 这次的写作任务
模板：{template.name}
角度：{template.hook}
结构：{template.structure}
语气：{template.tone}

## 信号背景
{signal_context.summary}
数据：{signal_context.data_points}
玩家原话精选：{signal_context.player_quotes}

## 当前活跃的网络梗（注入参考，不是必须全用）
{format_memes(active_memes)}

## 同类爆款参考（你的语感校准用）
{format_exemplars(exemplars)}

## 输出要求
返回 JSON：{{"title": "...(≤20字)", "content": "...", "hashtags": ["#..."]}}
"""
```

每次调用都把这个完整 prompt 的 hash 记到 `llm_calls.prompt_hash`，方便 debug "为什么这条文案这样写"。

---

## 9. Telegram 交互

### 推送格式

```
🎮 候选 #142

【负面信号 / urgent】
游戏：星空 (Starfield)
触发：差评率 7 天下跌 23%，评论数 1.2k
数据源：https://store.steampowered.com/appreviews/...

──────────
标题：星空真要凉了？我看了 200 条差评后……
正文：（完整正文 ~300 字）
#星空 #Steam差评 #游戏吐槽
──────────
[📷 配图] 

[✅ 发布]   [❌ 跳过]
```

### 反馈处理

- 点 [✅ 发布] → `posts.state = approved`，bot 回 "已记录，去小红书发吧"
- 点 [❌ 跳过] → `posts.state = rejected`，bot 回 "好的"
- 普通文字回复 → 触发 rewrite_agent
  - rewrite_agent 输入：`{"original": post, "feedback": "再抽象点，加一句吐槽"}`
  - 输出：新的 title/content/hashtags
  - bot 推送新版本（同一个 post_id，但新的 telegram_message_id）
  - `review_iterations` 数组追加 `{feedback, revised_at, new_content}`
  - `posts.state = iterating`

### 状态绑定

- bot 默认绑定到 **最近一条 pending/iterating 帖子**
- 也支持 Telegram 的 reply_to_message：你 reply 某条历史推送，bot 通过 `message_id` 反查 post

---

## 10. LLM 预算

```python
DAILY_BUDGET_USD = 2.0

MODEL_STRATEGY = {
    "signal_judgment":  "claude-haiku-4-5",
    "trend_extraction": "claude-haiku-4-5",
    "translation":      "claude-haiku-4-5",
    "copywriting":      "claude-sonnet-4-6",
    "rewrite":          "claude-sonnet-4-6",
}
```

**估算（每天 2-3 候选）：**
- Trend extraction: ~$0.005/天
- Signal judgment: ~$0.001/候选 × 10 候选评估 = ~$0.01
- Copywriting: ~$0.015/候选 × 3 = ~$0.045
- Rewrite (假设每候选平均 1 次重写): ~$0.015 × 3 = ~$0.045
- **每天总计 < $0.15**

预算保护：`budget/guard.py` 在每次 LLM 调用前检查日累计，超 80% 邮件/日志预警，超 100% 抛 BudgetExceeded 异常。

---

## 11. 调度

| 任务 | 触发 | 实现 |
|-----|------|------|
| Trend pipeline | 每天 09:00 | APScheduler cron |
| Content pipeline | 每天 09:30, 17:30 | APScheduler cron（避开 trend 后 30 分钟，让 meme 库先更新）|
| Telegram bot | 常驻 | 单独进程，python-telegram-bot polling |
| 手动触发 | 任意 | `python -m xhs_agent.cli run-pipeline` |

本地：开两个进程（scheduler + bot）。
服务器：systemd unit 各一份，或 docker-compose 两服务。

---

## 12. 迭代路线

### V0（第 1-2 周）— 跑通端到端

- [ ] 项目脚手架 + 文档
- [ ] storage 层 + alembic
- [ ] config 层
- [ ] Steam collector + 5 信号检测
- [ ] signal_agent (Haiku)
- [ ] content_agent (Sonnet) — 静态 prompt 即可
- [ ] formatter
- [ ] Telegram push (含 inline 按钮)
- [ ] rewrite_agent + 文字反馈处理
- [ ] post tracker (posts 表 + 手动回填脚本)
- [ ] 预算守卫
- [ ] **目标：发出第一篇候选并迭代修改成功**

### V1（第 3-4 周）— 核心差异化

- [ ] Trend collector (Reddit + Bilibili + NGA + 贴吧 + 虎扑)
- [ ] Trend extractor (Haiku)
- [ ] meme_phrases 生命周期管理
- [ ] exemplar_posts CLI 录入
- [ ] Prompt 动态拼装替换静态 prompt
- [ ] 图片生成 (fal.ai FLUX)
- [ ] B 站白名单 UP 主功能

### V2（第 5-8 周）— 数据驱动 + 上服务器

- [ ] 部署到 VPS / Railway，PostgreSQL
- [ ] 基于 posts 表分析：哪类信号 → 哪类内容 → 高互动
- [ ] signal_agent 引入历史互动数据加权
- [ ] Domain pack 抽象正式启用（为下个领域铺路）

### V3+（第 3 月起）

- [ ] 第二个 domain pack
- [ ] Agent 自主建议新信号源
- [ ] 变现探索

---

## 13. 已知风险与对策

| 风险 | 对策 |
|------|------|
| 中文站点反爬（NGA/贴吧） | 加 UA + 请求间隔；失败时跳过单源不阻塞 pipeline |
| Reddit API 限流（60/min） | PRAW 自动 rate limit；trend pipeline 一天一次完全够 |
| Bilibili 风控 | 一天一次拉取；只拉 top 20 视频的最近一段弹幕；UA 轮换 |
| 小红书违禁词漏检 | formatter 用 句易网 词库 + 自维护补丁；Telegram 推送时高亮疑似词 |
| LLM 文案抽风 | rewrite_agent 兜底；连续 3 次失败标记 post.state=failed 并跳过 |
| API key 泄露 | .env 不进 git；.env.example 占位 |
| 上服务器后 schema 迁移 | alembic 管理；本地 dev 时也用 alembic 不直接 create_all |

---

## 14. 关键不变量（写代码时务必遵守）

1. **所有 LLM 调用必须经过 `llm_tracker`** — 不允许直接 import anthropic SDK 调用，必须走包装器
2. **所有数据源必须实现 `Collector` ABC** — 保证未来 domain pack 可插拔
3. **配置只从 `config/settings.py` 读** — 不允许散落 os.getenv
4. **数据库只通过 `repositories` 访问** — 业务层不直接写 SQL 或 ORM
5. **prompt 必须从 prompt_versions 表读** — 不允许硬编码 prompt 字符串到 agent 文件里
6. **每个 pipeline 必须创建 PipelineRun 记录** — 失败也要记录，便于回溯

---

## 15. 文档变更记录

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-05-07 | v4 | 初版：在 v3 基础上加入 trend service / exemplar bank / dynamic prompt / domain pack / Telegram 文字反馈循环 |
