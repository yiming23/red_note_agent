"""Content templates for the games domain — v5 / 真实玩家评论研究所.

DESIGN_v5.md § 4 — 6 content_types replacing v4's 5 templates.

Each template now describes a "multi-page article shape" — the `structure`
list maps roughly to the page sequence in the v5 multi-page output.

Mapping from v4 names (for reference):
    negative_review_burst → negative_review_burst (kept name, repositioned)
    hidden_gem            → hidden_gem (kept name)
    comeback_game         → comeback_game (kept name)
    new_release_heat      → new_release_heat (kept name)
    player_spike_event    → REMOVED (player_spike now feeds 差评爆炸 or 口碑反转
                                     depending on what reviews say)
    (new)                 → discount_worth_checking (placeholder, S8 will wire signal)
    (new)                 → playtime_contrast (placeholder, S5 will wire signal)
"""

from __future__ import annotations

from xhs_agent.domain.base import Template

# ────────────────────────────────────────────────────────────
# 1. 差评爆炸 (negative_review_burst) — V0 触发: negative_burst signal
# ────────────────────────────────────────────────────────────

NEGATIVE_REVIEW_BURST = Template(
    name="negative_review_burst",
    content_type="差评爆炸",
    matches_signals=["negative_burst"],
    hook="《{game_name}》差评不是慢慢来的，是突然爆了——这是数据告诉我的",
    structure=[
        "一句话结论：现在该不该原价冲",
        "好评率时间线：从哪天开始掉",
        "差评数量爆发证据：不是单个人抱怨",
        "差评主题占比：玩家主要在骂哪几点",
        "代表性玩家原话：2-3 条短引用",
        "适合谁 / 不适合谁",
        "购买建议：A/B/C/D/E 评级 + 等什么",
    ],
    tone=(
        "理性消费分析员的口吻。先承认游戏不是一无是处，再用数据点出问题。"
        "不要说'垃圾游戏'，要说'目前首发状态不适合普通玩家原价冲'。"
        "数据驱动，但每段都服务消费判断，不要堆数据。"
    ),
    notes=(
        "重点 hook：'玩家主要不是骂玩法 / 差评其实集中在 X 个点'。"
        "如果差评集中在可修复问题（优化/服务器），建议 B（等大补丁）。"
        "如果集中在核心设计问题，建议 D 或 E。"
    ),
)

# ────────────────────────────────────────────────────────────
# 2. 口碑反转 (comeback_game) — V0 触发: positive_burst (age > 180d)
# ────────────────────────────────────────────────────────────

COMEBACK_GAME = Template(
    name="comeback_game",
    content_type="口碑反转",
    matches_signals=["positive_burst", "review_surge"],
    hook="《{game_name}》最近评论变化挺明显，如果只看总评可能会误判",
    structure=[
        "一句话结论：现在是否值得回坑或入坑",
        "总评 vs 最近评论对比：分裂感",
        "好评率时间线：从哪天开始反转",
        "早期 vs 近期差评主题对比：开发组改了什么",
        "在线人数变化（如果有）",
        "代表性玩家原话：1 条早期差评 + 1 条近期好评",
        "购买建议：A/B/C/D + 适合谁",
    ],
    tone=(
        "带点'故事感'，但仍然冷静。承认游戏过去翻车过，"
        "用数据展示现在变好了。不要假装一直关注，承认是后知后觉。"
        "时间感 ('X 年前 vs 现在') 是天然钩子。"
    ),
    notes=(
        "如果是大补丁带回口碑，强调'更新了什么'。"
        "如果是季节性 / 联机活动带回，强调'现在能玩到什么内容'。"
    ),
)

# ────────────────────────────────────────────────────────────
# 3. 小众神作 (hidden_gem) — V0 触发: positive_burst（评论数低的场景）
#    完整触发逻辑要等 S8 新增 hidden_gem signal detector
# ────────────────────────────────────────────────────────────

HIDDEN_GEM = Template(
    name="hidden_gem",
    content_type="小众神作",
    matches_signals=["positive_burst", "hidden_gem"],
    hook="《{game_name}》评论不多，但好评率很夸张",
    structure=[
        "一句话结论：适合什么类型玩家 + 是否值得入",
        "评论数 × 好评率定位：在同类里的位置",
        "正面主题占比：玩家在夸什么",
        "游玩时长分布：核心粉占比",
        "缺点总结：必须承认的不足",
        "代表性玩家原话：2 条好评",
        "购买建议 + 适合谁",
    ],
    tone=(
        "真诚推荐，像朋友安利但不夸张。承认它有缺点。"
        "强调'小众'不是缺点而是特点。"
    ),
    notes="如果是独立游戏，可以提开发组规模、价格、玩法独特性。",
    v0_ready=True,  # 暂用 positive_burst，S8 替换为 hidden_gem signal
)

# ────────────────────────────────────────────────────────────
# 4. 折扣值不值 (discount_worth_checking) — 占位，S8 启用
# ────────────────────────────────────────────────────────────

DISCOUNT_WORTH_CHECKING = Template(
    name="discount_worth_checking",
    content_type="折扣值不值",
    matches_signals=["discount_event"],
    hook="《{game_name}》史低了，但我看完评论不建议所有人买",
    structure=[
        "一句话结论：折扣价是否值得入",
        "价格历史 / 现在是否史低",
        "最近口碑：好评率时间线",
        "差评主题：风险点是什么",
        "同类对比（如果可获取）",
        "代表性玩家原话",
        "购买建议：A/B/C/D/E + 等更低折扣？",
    ],
    tone="像理性消费分析员。承认价格诱人，但点出评论风险。",
    notes="S5 启用 discount_event 信号。S8 接 IsThereAnyDeal API 加价格历史图。",
    v0_ready=True,
)

# ────────────────────────────────────────────────────────────
# 5. 评论区反差 (playtime_contrast) — 占位，S5 启用
# ────────────────────────────────────────────────────────────

PLAYTIME_CONTRAST = Template(
    name="playtime_contrast",
    content_type="评论区反差",
    matches_signals=["playtime_split"],
    hook="《{game_name}》玩了 {n} 小时还给差评，玩家到底在骂什么",
    structure=[
        "一句话结论：这游戏的问题在哪个阶段暴露",
        "游玩时长 × 好评/差评分布：核心反差图",
        "长时差评主题：玩久了的人在骂什么",
        "短时差评主题：刚进来的人在骂什么",
        "长时好评主题：核心玩家为什么爱",
        "代表性玩家原话：长时差评 + 长时好评 各 1 条",
        "我的判断：适合短期还是长期",
    ],
    tone=(
        "强反差感，但保持客观。'不是打开就烂，而是 X 阶段开始劝退'是核心 hook。"
    ),
    notes="S5 启用，依赖 playtime_split 信号 + playtime buckets 数据。",
    v0_ready=True,
)

# ────────────────────────────────────────────────────────────
# 6. 新品爆款 (new_release_heat) — V0 触发: new_release_spike
# ────────────────────────────────────────────────────────────

NEW_RELEASE_HEAT = Template(
    name="new_release_heat",
    content_type="新品爆款",
    matches_signals=["new_release_spike"],
    hook="《{game_name}》是这周的 Steam 新游黑马，但我没那么乐观",
    structure=[
        "一句话结论：是否值得 day-one 入",
        "热度证据：发售 N 天 + 在线人数 + 销量榜位置",
        "玩家说什么：好评/差评分布",
        "玩法亮点：核心体验",
        "已知问题（首发常见的 bug / 优化）",
        "代表性玩家原话",
        "购买建议：A/B/C + 适合谁",
    ],
    tone=(
        "客观描述热度 + 主观判断值不值得。绝对不要变成游戏厂商通稿。"
        "保持一点'你冷静一下'的距离感，但不无脑泼冷水。"
    ),
    notes="如果是大厂 3A 翻车，hook 是'宣传 vs 真实玩家反馈'的反差。",
)

# ────────────────────────────────────────────────────────────
# 全部模板列表 — domain.py 引用
# ────────────────────────────────────────────────────────────

ALL_TEMPLATES = [
    NEGATIVE_REVIEW_BURST,
    COMEBACK_GAME,
    HIDDEN_GEM,
    DISCOUNT_WORTH_CHECKING,
    PLAYTIME_CONTRAST,
    NEW_RELEASE_HEAT,
]

# V0 实际可用模板（v0_ready=True） — signal_agent 选模板时优先这些
V0_READY_TEMPLATES = [t for t in ALL_TEMPLATES if t.v0_ready]
