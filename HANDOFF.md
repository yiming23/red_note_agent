# HANDOFF

> 跨 session 进度交接文档。每次工作结束更新这里，下次开工先读这里。
> 设计权威：**[DESIGN_v5.md](./DESIGN_v5.md)**（v4 已 deprecated）
> 配置/启动：[README.md](./README.md)

---

## 📍 当前状态（2026-05-11，**S8 完成 / 折扣值不值激活**）

🎉 **S8 IsThereAnyDeal + SteamSpy Pool 6 完成。**

**新增文件**：
- `src/xhs_agent/domain/games/collectors/itad.py` — ITAD v2 API client；`ItadClient.enrich_entity()` in-place 填充 `historic_low_price / pct_above_historic_low / is_at_historic_low`
- `src/xhs_agent/visualization/card_price.py` — 价格对比卡（原价灰条 / 折后红条 / 史低绿条 + verdict 文字）

**修改文件**：
- `config/settings.py` — 新增 `itad_api_key` 字段（空时自动跳过 ITAD）
- `.env.example` — 新增 `ITAD_API_KEY=` 占位
- `domain/games/entity.py` — 新增 `historic_low_price / pct_above_historic_low / is_at_historic_low`
- `domain/games/collectors/steam.py` — Pool 6（SteamSpy top100in2weeks）+ ITAD enrichment（仅 discounted 游戏）
- `storage/models.py` — 新增 `PriceSnapshot` 表
- `storage/repositories.py` — 新增 `PriceSnapshotRepository`
- `alembic/versions/afdc526a7e43_s8_price_snapshots.py` — 迁移已 apply
- `prompt/assembler.py` — 价格历史 section 注入（有 ITAD 数据时自动生成）
- `agents/buy_or_wait.py` — 价格状态注入（史低 / 高于史低 X%）
- `visualization/renderer.py` — 新增 `price_card` dispatch
- `processors/page_builder.py` — Slot E：discounted 游戏自动加 price_card；新增 `_price_page()` 构造器
- `orchestration/pipeline.py` — 收集后持久化 price snapshots

**验证（2026-05-11）**：
- 71 个测试全部通过
- price card smoke render 成功

**激活条件**：在 `.env` 中填入 `ITAD_API_KEY=` 后自动生效；无 key 时 ITAD 静默跳过，折扣信息仍从 Steam 获取。

---

## 📍 当前状态（2026-05-11，**S7 完成 / MVP 全栈可用**）

🎉 **S7 可视化 MVP 完成。** Pipeline 现在输出 7 张 1080×1080 图片并以 Telegram media group 推送：

**新增文件**：
- `src/xhs_agent/visualization/__init__.py` — 入口，导出 `render_all_pages`
- `src/xhs_agent/visualization/base.py` — 共享样式常量 + PingFang 字体加载 + matplotlib rcParams
- `src/xhs_agent/visualization/card_cover.py` — Page 1：Pillow 封面卡（游戏名 + B级徽章 + 一句话结论）
- `src/xhs_agent/visualization/chart_positive_rate.py` — Page 3：好评率对比双色柱状图
- `src/xhs_agent/visualization/chart_theme_share.py` — Page 4：差评主题水平条形图（好评 vs 差评叠加）
- `src/xhs_agent/visualization/chart_playtime_distribution.py` — Page 5：游玩时长 4 桶横向堆叠条
- `src/xhs_agent/visualization/card_review.py` — Page 6：Pillow 评论气泡卡（4 条带 theme 标签）
- `src/xhs_agent/visualization/card_recommendation.py` — Page 7：Pillow A-E 评级全页卡
- `src/xhs_agent/visualization/renderer.py` — 调度所有渲染；失败时 warn 不崩溃

**修改文件**：
- `src/xhs_agent/orchestration/pipeline.py` — build_pages 后调 render_all_pages；send_candidate_with_images 替换 send_candidate
- `src/xhs_agent/publishers/telegram_push.py` — 新增 `send_candidate_with_images`（先 sendMediaGroup，再 sendMessage 带按钮）+ `_send_media_group_sync`

**字体**：系统自带 `/System/Library/Fonts/PingFang.ttc`，自动检测，无需手动安装

**测试验证（2026-05-11）**：
- 7 张图全部渲染成功，大小 16-61KB 每张
- CJK 文字在 Pillow 卡片和 matplotlib 图表上均正确显示
- 71 个测试全部通过

---

🎉 **S6 决策 + 合规 + 多页输出 完成**（上一 session）：
- Buy-or-Wait Analyst (Haiku) 给每篇候选输出 A-E 评级 + 适合谁/不适合谁/关键风险/等什么
- Compliance Guard 自动检测并替换绝对化语言，block 标题缺游戏名/hashtag 数量错误
- 每篇候选生成 7 页结构（cover/conclusion/trend/theme_share/playtime_dist/review_quotes/recommendation），存入 posts.pages
- posts 表扩展 11 个 v5 字段（content_type/pages/buy_rating/suitable_for 等）
- Telegram 候选消息现在含 A-E 评级摘要 + 折扣信息
- 71 个测试全部通过

**S5 以前完成的内容（见下）**：

🎉 **S5 Review 深度 完成。** 候选现在：
- 候选池从 ~30 扩展到 60-100 个游戏（top_concurrent + new_releases + top_sellers + specials）
- 有折扣信息（discount_pct / final_price），折扣游戏能触发 "折扣值不值" content_type
- 8 个信号检测器（新增 discount_event / hidden_gem / playtime_split）
- Review Miner 持久化到 DB，24h 缓存，重复跑不花 Haiku token
- Playtime buckets 聚合，数据注入进 prompt（短时差评 vs 长时好评分布）
- DailyReviewStats 每次 pipeline 自动聚合写表，为 S7 图表备料
- 6 个模板全部 v0_ready（折扣值不值 + 评论区反差 从占位变激活）
- 71 个测试全部通过

**S4.5 以前也已完成的内容**（见下）：

🎉 **v5 身份重置 + Review Miner mini 完成。** 候选已经能：
- 带《》标题 + 5-10 hashtag + 理性研究员口吻
- 在正文里给出**真实的主题占比百分比**（"约 27% 评论指向连接问题"等），不再用"出现频率相当高"这种模糊词
- 禁止自称"我"（如"我翻了"/"我看了"）—— 改用"近期评论中"/"数据显示"

**S4.5（mini Review Miner）落地的变化**：
- 新文件 `src/xhs_agent/agents/review_miner.py` — Haiku batch 调用，把 30 条评论分类到 14 类固定 taxonomy（来自 `tuning.review_themes`），输出 `ThemeSummary { themes: [ThemeStat], total_analyzed, cost_usd }`
- `entity.py` 加 `recent_review_pool: list[dict]` 字段，存最多 30 条评论的 {text, voted_up, playtime_minutes}
- Steam collector 现在同时填 `sample_recent_review_excerpts`（5 条直引）+ `recent_review_pool`（30 条分析池）
- `storage/models.py.LlmPurpose` 加 `REVIEW_MINING` 枚举值（无 schema 变更，向后兼容）
- `pipeline.py` 在 generate_content 前调一次 review_miner，把 theme_summary 传给 content_agent；超预算或失败时跳过（不阻塞写作）
- `prompt/assembler.py` user prompt 加 "评论主题数据" 段，用 ThemeSummary.format_for_prompt 把数据塞进去 + 6 条新硬性要求（明确禁止自称"我"、要求用百分比说话、不模糊词）
- `content_agent.py` + `assemble_content_prompt` 加 `theme_summary` 可选参数

**成本**：每候选多 ~$0.005（Haiku，1.5k in/500 out tokens），每 pipeline 跑约 $0.01-0.015 额外。

---

## 🐛 S4.5 已知问题 / 后续修法

**1. 候选池太窄 — 全是常驻巨头**
- 现状：`SteamCollector.collect()` 只迭代 `top_concurrent` → 永远是 CS2/Dota2/Apex/守望，没有真新闻
- 已有 patch：`signal_agent._infer_template_from_entity` 现在对老游戏 + 模糊数据返回 None，fallback 跳过这种 ambiguous candidate（不再硬塞错 content_type）。但这只是"不输出错的"，没"输出对的"
- **真修法（S5 必做，详见下方）**：把 `featured.new_releases` / `top_sellers` / `specials` 拼进 entity pool

**2. player_spike fallback 阈值偏松**
- 现状：`current / all_time_peak > 0.5` 触发，对老巨头几乎永远触发
- **修法**：tuning.yaml 加 `min_age_for_fallback` 限制 fallback 只在新游 (age < 90d) 上触发

**3. mini Review Miner 未持久化**
- 现状：每次重跑都重新分类 30 条评论
- **修法（S5）**：把 ThemeStat per-entity 存到 `review_themes` 表，跨 pipeline 复用

---

## 🛣 S5 详细任务清单（给 Claude Code 接手用）

**目标**：候选池扩展 + Review Miner DB 持久化 + 新增 3 个信号检测器 + 启用 playtime_contrast / discount_worth_checking 模板。

预计代码量 ~1500 行，~6-8 小时。**所有改动有具体文件路径和函数名指引。**

### S5.1 候选池扩展（最优先，2h）

📄 **`src/xhs_agent/domain/games/collectors/steam.py`**：

修改 `SteamCollector.collect(limit, language)`：当前只 iterate `top` (top concurrent)。改成：
1. 同时拿 `featured = self.get_featured()`
2. 收集三个 pool 的 appid（去重）：
   - top_concurrent[:limit]
   - featured.new_releases[:limit]
   - featured.top_sellers[:limit]
   - featured.specials[:limit]
3. 每个 appid 走同一套 enrichment（appdetails + appreviews + 主 entity 构造）
4. 给 entity 加新字段：`is_on_special: bool` + `discount_pct: int | None` + `original_price: float | None` + `final_price: float | None`（来自 `featured.specials` 字段）

📄 **`src/xhs_agent/domain/games/entity.py`**：
新增 `discount_pct`, `final_price`, `original_price`, `is_on_special` 字段（默认 None / False）。

📄 **`tests/fixtures/`**：
扩展 fixture json 包含 featured.specials sample。

**验证**：跑 pipeline 看 entity 数量从 ~30 涨到 ~80-100，且日志里出现新品 / 折扣游戏。

### S5.2 新信号检测器（1.5h）

📄 **`src/xhs_agent/domain/games/signals/discount_event.py`**（新文件）:
```python
class DiscountEventDetector(SignalDetector[GameEntity]):
    signal_type = "discount_event"
    def detect(self, entity):
        if not entity.is_on_special: return None
        if not entity.discount_pct or entity.discount_pct < 20: return None
        if not entity.recent_reviews_count or entity.recent_reviews_count < 50: return None
        severity = "urgent" if entity.discount_pct >= 50 else "normal"
        return SignalResult(..., signal_type="discount_event", ...)
```

📄 **`src/xhs_agent/domain/games/signals/hidden_gem_signal.py`**（新文件）:
```python
class HiddenGemDetector(SignalDetector[GameEntity]):
    signal_type = "hidden_gem"  # 注意：不要跟模板名 hidden_gem 混淆，可改 signal_type 为 "low_volume_high_rating"
    def detect(self, entity):
        if not (100 <= (entity.total_reviews or 0) <= 5000): return None
        if not entity.historical_positive_rate or entity.historical_positive_rate < 0.85: return None
        if not entity.game_age_days or entity.game_age_days < 180: return None
        return SignalResult(...)
```

📄 **`src/xhs_agent/domain/games/signals/__init__.py`**：注册新检测器。
📄 **`src/xhs_agent/domain/games/domain.py`**：把它们加进 detectors 列表。
📄 **`tuning.yaml` games.signals**：加 `discount_event` + `low_volume_high_rating` 阈值组。
📄 **`storage/models.py`**：`SignalType` enum 加 `DISCOUNT_EVENT` 和 `HIDDEN_GEM`/`LOW_VOLUME_HIGH_RATING`。Alembic migration。

📄 **`signals/player_spike.py`**：加 `min_age_for_fallback` 参数从 tuning 读，默认 90。Fallback 路径检查 age 是否 < 这个值；否则不触发。

### S5.3 Review Miner DB 持久化（1.5h）

📄 **`src/xhs_agent/storage/models.py`**：新增表：
```python
class ReviewTheme(Base):
    __tablename__ = "review_themes"
    id, appid, theme, count, negative_count, positive_count, share_pct,
    sample_quote, analyzed_at (timestamp), pipeline_run_id (FK)
```
+ alembic migration。

📄 **`src/xhs_agent/agents/review_miner.py`**：
- 加 `persist=True` 参数；调用 `extract_themes` 后写入 `review_themes` 表
- 加 `get_recent_themes(appid, max_age_hours=24) -> Optional[ThemeSummary]` 用于复用

📄 **`pipeline.py`**：调 review_miner 前先 `get_recent_themes`，命中缓存就跳过 LLM call。

### S5.4 Playtime buckets 聚合（1h）

📄 **`src/xhs_agent/processors/playtime_buckets.py`**（新文件）:
```python
def compute_buckets(entity) -> dict:
    """Aggregate entity.recent_review_pool by (playtime_bucket, voted_up)."""
    short_max = tuning.playtime_buckets.short_hours_max * 60  # minutes
    long_min = tuning.playtime_buckets.long_hours_min * 60
    buckets = {"short_neg": 0, "med_neg": 0, "long_neg": 0, "long_pos": 0, "other": 0}
    for r in entity.recent_review_pool:
        pt = r.get("playtime_minutes")
        if pt is None: 
            buckets["other"] += 1; continue
        voted = r.get("voted_up", False)
        if pt < short_max and not voted: buckets["short_neg"] += 1
        elif pt >= long_min and not voted: buckets["long_neg"] += 1
        elif pt >= long_min and voted: buckets["long_pos"] += 1
        elif not voted: buckets["med_neg"] += 1
        else: buckets["other"] += 1
    return buckets
```

📄 **`pipeline.py`**：在 review_miner 之后调，把 buckets 也传给 content_agent。
📄 **`prompt/assembler.py`**：注入 playtime buckets 数据到 user_message。

### S5.5 playtime_split 信号 + 启用 playtime_contrast 模板（30 min）

📄 **`signals/playtime_split.py`**（新文件）:
```python
class PlaytimeSplitDetector(SignalDetector[GameEntity]):
    signal_type = "playtime_split"
    def detect(self, entity):
        buckets = compute_buckets(entity)
        total = sum(buckets.values())
        if total < 15: return None  # 数据太少
        short_neg_share = buckets["short_neg"] / total
        long_pos_share = buckets["long_pos"] / total
        if short_neg_share >= 0.30 and long_pos_share >= 0.20:
            return SignalResult(..., signal_type="playtime_split", ...)
        return None
```

📄 **`domain/games/templates.py`**：`PLAYTIME_CONTRAST.v0_ready = True`。

### S5.6 DailyReviewStats 聚合（1h，可选 — 为后续图表服务）

📄 **`src/xhs_agent/storage/models.py`**：新增 `DailyReviewStats` 表（appid, date, pos_count, neg_count, daily_pos_rate, rolling_7d_pos_rate）。
📄 **`src/xhs_agent/processors/daily_aggregator.py`**（新文件）：每天跑一次 cron，把当日 review pool 聚合写表。
📄 **`orchestration/scheduler.py`**：注册新 cron job。

注意：S7 图表需要这个数据。如果 S5 时间紧可挪到 S6。

### S5.7 测试 + 端到端验证

📄 **`tests/unit/test_discount_event.py`**, **`test_hidden_gem_signal.py`**, **`test_playtime_buckets.py`**
📄 端到端：跑 pipeline，确认候选池扩展到 60+ 游戏，至少 1 个候选是新游 / 折扣 / 小众神作（非 CS2/Dota2）

### S5 验收标准

- [ ] 候选池 entity 数从 ~30 涨到 60+
- [ ] 至少 1 篇候选 content_type 是 "折扣值不值" 或 "小众神作"
- [ ] mini Review Miner 不再每次重跑（缓存命中率 > 0%）
- [ ] playtime_split 信号至少在 1 个游戏上触发
- [ ] 没有"老游戏 + player_spike → 新品爆款"错配
- [ ] Telegram 推送时显示 `discount_pct` 和 themes 占比（candidate message 模板加字段）

---

**S4 落地的关键变化**：
- 6 个 v5 content_type 模板（差评爆炸/口碑反转/小众神作/折扣值不值（占位）/评论区反差（占位）/新品爆款）
- persona.yaml 完全重写：理性消费分析员、banned_expressions 分类、preferred_phrasings、recommended_openings/comment_prompts、title_patterns 按组分
- 新模块：`utils/title_validator.py`（强制《游戏名》+ 16-30 字 + auto-fix）、`utils/hashtag_policy.py`（按 content_type 拼装 5-10 hashtag）
- `utils/prohibited_words.py` 重构：拆成 banned-phrase rewriter（style 软问题，自动改）+ illegal scanner（hard block，只有 piracy/adult/gambling）
- `tuning.yaml` 扩展：`hashtag`/`compliance`/`review_themes`/`playtime_buckets` 四个新 section
- `GameEntity` 加 `genres` + `short_description` 字段；Steam collector 现在解析 `appdetails.genres`
- prompt/assembler 重写：注入 banned/preferred/openings/comment_prompts，删去 meme 部分，加 v5 硬约束
- formatter 集成新 validators（title auto-fix + hashtag 拼装 + 银 phrase rewrite + 三类问题分别报告）
- Telegram candidate 消息升级：显示 content_type / 标题 ✓⚠️ / hashtag 数 ✓⚠️ / 自动替换报告
- 新脚本：`scripts/reset_posts.py`（清空 posts 表）
- 测试更新：`test_prohibited_words` 重写、`test_formatter` 重写、新增 `test_title_validator` + `test_hashtag_policy`

**全部文件通过 py_compile 语法检查**。等 user 跑端到端验证。

---

## 📍 历史状态归档：session 3 末 / v4 V0 完成

🎉 **v4 V0 端到端跑通了。** Steam→ 5 signals → Haiku 选题 → Sonnet 写文案 → Telegram 推送 → 文字反馈 rewrite 全链路 OK。两条候选 #5 #6 真实发到 Telegram（运行 #6 那次 cost=$0.015）。

**但用户上传了新设计文档（`ai_game_xhs_content_system_design.md`），定位重写。** v4 偏"内容博主"，v5 转向"真实玩家评论研究所"——数据可视化 + 评论 taxonomy + A-E 消费评级。新方向更有差异化、更有产品价值。

**S4 开工前已确认的 7 项核心决策**：
1. exemplar_posts 表保留，但 v5 不依赖（理性研究所风格不需要大量样本）
2. v4 的 5 个 templates 全部重写为 v5 的 6 个 content_type
3. Telegram 多图推送 = media group + 每图 caption（一条消息塞文字 + 一条 media group）
4. Review Miner 用 batch LLM 分类（一次 prompt 30-50 条评论）
5. 14 类 taxonomy 写进 tuning.yaml 让 user 可调
6. 用 IsThereAnyDeal API 拉价格历史（S8 P1）
7. **MVP 包含可视化图片**——matplotlib + Pillow，3 个核心图 + 评论卡片 + 封面 + 结论卡

**S4-S7 = v5 MVP 完整路径**。S7 跑通后，产品成型。

---

## ✅ 已完成（v4 V0 全部）

### Session 1（地基）
- DESIGN.md (v4) / README.md / HANDOFF.md
- pyproject + .env.example + .gitignore + alembic
- config/{settings.py, persona.yaml}
- storage/{db.py, models.py 8 张表, repositories.py}

### Session 2（observability + 数据采集 + 信号检测）
- observability/{logger.py, llm_tracker.py（dry-run + 自动记录 + 预算）}
- budget/guard.py
- domain/base.py (Collector / SignalDetector / Domain / Template ABC)
- domain/games/{entity.py, domain.py, templates.py 5 个模板}
- domain/games/collectors/{steam.py, steamspy.py}
- domain/games/signals/*.py（5 个检测器，含 V0 fallback）
- tests/unit/{test_signal_detectors.py, test_steam_collector_parsing.py}

### Session 3（agents + formatter + telegram + orchestration）
- prompt/assembler.py
- agents/{signal_agent, content_agent, rewrite_agent}.py
- utils/{formatter.py, prohibited_words.py}
- publishers/telegram_push.py
- orchestration/{pipeline.py, scheduler.py}
- scripts/{run_pipeline_once, run_telegram_bot, run_scheduler, log_post_result, inspect_db, add_exemplar}.py
- tests/unit/{test_formatter, test_agent_parsing, test_prohibited_words}.py

### Session 3.5（验证 + 中央 tuning）
- 修复 logger 配置（PrintLoggerFactory 跟 stdlib processors 不兼容）
- 修复 pytest pythonpath = ["src"]
- 把 content_agent / signal_agent 改用 Anthropic Tool Use（彻底消除 JSON 解析失败）
- 解决 budget ↔ observability 循环导入
- 加 fallback：Haiku 拒全部时强制 top-N 通过
- **新建中央 tuning.yaml + tuning.py**（5 个信号阈值 / 严格度模式 / 采集器参数全部从这里读）
- candidate Telegram 消息加 reply-to 说明

---

## 🚧 v5 MVP Session 拆分（下面开始）

### Session 4 — 身份重置（P0a，4-5h）

**目标**：把所有"小红书博主"语气 / 散落规则换成"真实玩家评论研究所"语气，让 V0 跑出的第一篇就是 v5 风格。

任务清单（依赖顺序）：
1. **清空 posts 表**（保留 schema，drop rows）
2. **重写 `config/persona.yaml`** — 理性消费分析员人设、固定开头/结尾句式、禁词换批
3. **重写 `domain/games/templates.py`** — 6 个 content_type（差评爆炸 / 口碑反转 / 小众神作 / 折扣值不值（占位）/ 评论区反差 / 新品爆款）
4. **新增 `utils/hashtag_policy.py`** — 按 content_type 拼装 5-10 个 hashtag
5. **新增 `utils/title_validator.py`** — 强制《游戏名》格式 + 字数 16-30
6. **改 `tuning.yaml`** — 加 `themes_taxonomy` 14 类（占位，S5 用）+ `compliance.banned_phrases_to_rewrite` 替换表
7. **改 `utils/prohibited_words.py`** → 升级成简单 banned-phrase 替换（v5 完整 Compliance Guard 在 S6 做）
8. **改 `prompt/assembler.py`** — 加标题/hashtag/语气硬约束注入 system prompt
9. **改 telegram_push** — 候选消息显示 content_type / hashtag 数 / 标题格式校验结果
10. 跑一次端到端，截图候选给 user 验证语气

### Session 5 — Review 深度（P0b，5-6h）

任务清单：
1. 新增 `agents/review_miner.py` — batch LLM 给评论打 theme 标签（14 类之一）+ 选代表性评论
2. 新增 `storage/models.py` 字段：`review_themes` 表 + `daily_review_stats` 表
3. alembic migration
4. 改 collector 让 playtime_at_review 进 entity（已采集，已有字段，但 pipeline 没存进 DB）
5. 新增 `processors/playtime_buckets.py` — 短 / 中 / 长时差评 + 长时好评分组聚合
6. 新增 `processors/daily_aggregator.py` — daily cron 写 daily_review_stats（每天的好评率 + 7d rolling）
7. 改 pipeline：Review Miner 跑在 signal_agent 之后
8. 改 prompt/assembler — content prompt 加 themes_summary + playtime_buckets 数据进上下文

### Session 6 — 决策 + 合规 + 多页输出（P0c，5-6h）

任务清单：
1. 新增 `agents/buy_or_wait.py` — Sonnet/Haiku 输出 {rating, suitable_for, not_suitable_for, key_risks, wait_for}
2. 新增 `agents/compliance_guard.py` — 标题游戏名 / hashtag / 评论引用 / 绝对化语言 / 大段复制 等检查
3. alembic：扩 `posts` 表加 `content_type / pages / cover_text / comment_prompt / buy_rating / suitable_for / not_suitable_for / key_risks / wait_for / themes_summary / playtime_buckets`
4. 改 `content_agent.py` → `xhs_editor.py`，输出 multi-page 结构（每页 title + body + chart_type）
5. 改 telegram_push — 推送 cover_text 卡 + 一句话结论 + buy_rating 摘要
6. 改 rewrite_agent — 支持基于 multi-page 重写

### Session 7 — 可视化 MVP（P0d，6-7h）⭐ V5 完成的核心

任务清单：
1. 下载 Noto Sans CJK SC 字体到 `assets/fonts/`，加进 git
2. 新增 `visualization/` 模块：
   - `base.py` — 共用 matplotlib 风格（颜色 / 字号 / dpi=300 / figsize 1080×1080）
   - `chart_positive_rate.py` — 好评率时间线
   - `chart_theme_share.py` — 差评主题占比横向条形图
   - `chart_playtime_distribution.py` — playtime × voted_up 堆叠柱
   - `card_review.py` — Pillow 文字卡，引用 1-3 条评论
   - `card_cover.py` — Pillow 封面海报
   - `card_recommendation.py` — Pillow A-E 评级 + 适合谁
3. 新增 `agents/data_viz_designer.py` — 根据 content_type + Review Miner 输出，决定 visual_plan
4. 改 pipeline — Data Viz Designer 跑在 XHS Editor 后；输出每页的 image_path
5. 改 telegram_push — sendMediaGroup 一次推 5-7 张图，每张 caption = 该页结论
6. 新增 `storage/models.py` 字段：`post_messages` 表（多 message_id ↔ post 映射）
7. 改 bot 端 `_find_target_post` 用 post_messages 反查

S7 跑通后 = v5 MVP 完成。

---

## 🎯 v5 完成后的状态

```
真实玩家评论研究所 v5 MVP
├── 数据采集：Steam top concurrent + featured + reviews（含 playtime_at_review）
├── 评论分析：Review Miner 14 类 taxonomy + playtime 分组 + 代表评论选取
├── 信号检测：6 个 content_type（差评爆炸 / 口碑反转 / 小众神作 / 折扣值不值（占位）/ 评论区反差 / 新品爆款）
├── 选题判断：Haiku Trend Scout
├── 决策评级：Sonnet Buy-or-Wait → A/B/C/D/E + 适合谁
├── 文案生成：Sonnet XHS Editor → 多页 + 标题《游戏名》+ 5-10 hashtags
├── 可视化：matplotlib + Pillow → 7 张 PNG（封面 + 时间线 + 主题占比 + 时长分布 + 评论卡 + 建议卡 + 结论卡）
├── 合规检查：Compliance Guard → 标题/hashtag/绝对化语言/引用长度
├── 推送：Telegram media group → 你直接保存图发小红书
├── 反馈循环：你 reply 任意消息 → rewrite_agent
└── 调度：APScheduler 每天 2-3 次自动跑
```

---

## ⏭️ V5 之后（P1 / P2）

| 阶段 | 内容 |
|---|---|
| **P1 / S8** | 折扣值不值（IsThereAnyDeal API） + 小众神作 + 口碑反转独立检测 |
| **P1 / S9** | 价格历史 + 在线人数历史 + 同类对比（Steam tags） |
| **P2 / V2** | 多平台舆情（Reddit / B 站 / NGA / 小黑盒） |
| **P2 / V3** | Performance Learner + Monetization Planner |
| **P2 / V4** | 个性化推荐 + 商业化数据产品 |

---

## ⚠️ S4 开工前用户待办

1. **Drop posts 表数据**（schema 保留）。可以 S4 第一步代码做，也可以你手动跑：
   ```bash
   uv run python -c "from xhs_agent.storage.db import session_scope; from xhs_agent.storage.models import Post; from sqlalchemy import delete
   with session_scope() as s: s.execute(delete(Post))"
   ```
2. 申请 IsThereAnyDeal API key（S8 才用，可以拖一拖）：https://docs.isthereanydeal.com/
3. 注意：v4 的 5 个 templates 会被替换，但代码会保留 prompt_versions 表里的历史记录（不删 row）
4. 注意：v4 的 meme_phrases / exemplar_posts 表保留，schema 不变

---

## 🗂 当前文件树（v4 完成 + v5 设计立稿后）

```
xhs-game-agent/
├── DESIGN.md (v4, 已 deprecated 但保留)
├── DESIGN_v5.md ⭐ 当前权威
├── README.md, HANDOFF.md
├── pyproject.toml + .env.example + .gitignore
├── alembic.ini + alembic/{env.py, script.py.mako, versions/d6df8f392d34_initial_schema.py}
├── xhs_agent.db (本地 SQLite)
├── src/xhs_agent/
│   ├── config/{settings.py, persona.yaml, tuning.yaml ⭐, tuning.py}
│   ├── storage/{db.py, models.py, repositories.py}
│   ├── observability/{logger.py, llm_tracker.py}
│   ├── budget/guard.py
│   ├── domain/base.py
│   ├── domain/games/
│   │   ├── domain.py, entity.py, templates.py
│   │   ├── collectors/{steam.py, steamspy.py}
│   │   └── signals/{negative_burst, positive_burst, player_spike, new_release_spike, review_surge}.py
│   ├── prompt/assembler.py
│   ├── agents/{signal_agent, content_agent, rewrite_agent}.py
│   ├── utils/{formatter.py, prohibited_words.py}
│   ├── publishers/telegram_push.py
│   ├── orchestration/{pipeline.py, scheduler.py}
│   └── trend/ (空，meme_phrases 表暂搁置)
├── scripts/{run_pipeline_once, run_telegram_bot, run_scheduler, log_post_result, inspect_db, add_exemplar}.py
└── tests/
    ├── conftest.py + fixtures/
    └── unit/{test_signal_detectors, test_steam_collector_parsing, test_formatter, test_agent_parsing, test_prohibited_words}.py
```

S4 之后会加：
- `src/xhs_agent/utils/{hashtag_policy.py, title_validator.py}`
- `src/xhs_agent/visualization/` (S7)
- `src/xhs_agent/agents/{review_miner.py, buy_or_wait.py, compliance_guard.py, data_viz_designer.py}`
- `src/xhs_agent/processors/{playtime_buckets.py, daily_aggregator.py}`
- `assets/fonts/NotoSansCJKsc-{Bold,Regular}.otf`
- 新表：`review_themes, daily_review_stats, price_snapshots, player_count_snapshots, post_messages`

---

## 📝 Session 历史

| Session | 日期 | 完成 | 累计代码行 |
|---|---|---|---|
| 1 | 2026-05-07 | 文档 + 脚手架 + config + storage | ~700 |
| 2 | 2026-05-08 | observability + budget + collectors + signals + tests | ~3900 |
| 3 | 2026-05-08 | prompt + agents + formatter + telegram + orchestration + scripts | ~6400 |
| 3.5 | 2026-05-10 | 真机调试 + tool use + 循环导入修复 + 中央 tuning.yaml + reply-to 提示 | ~6900 |
| 4 | 2026-05-10 | v5 身份重置 ✅ — persona / templates / hashtag / 标题约束 / banned-phrase 替换 + 3 个新模块 + 2 个新测试文件 | ~8200 |
| 4.5 | 2026-05-10 | Review Miner mini ✅ — Haiku batch 分类 30 条评论到 14 taxonomy，content_agent 用真实百分比写作；禁止自称"我"+ 多个 prompt 硬性要求 | ~8700 |
| 5 | 2026-05-11 | 候选池扩展 ✅ + 3 新信号 ✅ + Review Miner DB 持久化 ✅ + playtime buckets ✅ + daily aggregator ✅ + 6 个模板全 v0_ready ✅ | ~10200 |
| 6 | 2026-05-11 | Buy-or-Wait Analyst ✅ + Compliance Guard ✅ + 7 页结构 ✅ + posts 表 11 个 v5 字段 ✅ + alembic migration ✅ | ~12000 |
| 6 | 待开 | Buy-or-Wait + Compliance Guard + 多页输出 | 目标 ~10500 |
| 7 | 待开 | matplotlib 图表渲染 + Pillow 卡片 + Telegram media group | 目标 ~12500 |
