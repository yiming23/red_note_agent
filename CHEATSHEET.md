# Cheatsheet — xhs-game-agent

常用命令速查。**修改了脚本/依赖/DB schema 后记得更新这里。**

---

## 开发环境

```bash
# 安装/更新依赖（uv 自动同步 pyproject.toml）
uv sync

# 添加新依赖
uv add <package>

# 进入虚拟环境 shell（可选，也可直接用 uv run）
source .venv/bin/activate
```

---

## 测试

```bash
# 全部测试
uv run pytest

# 安静模式（只显示失败 + 最终统计）
uv run pytest -q

# 单个文件
uv run pytest tests/unit/test_signal_detectors.py

# 单个测试函数
uv run pytest tests/unit/test_signal_detectors.py::test_discount_event_triggered -v

# 带覆盖率
uv run pytest --cov=xhs_agent --cov-report=term-missing
```

---

## Pipeline

```bash
# 跑一次完整 pipeline（推 Telegram）
uv run python scripts/run_pipeline_once.py

# 干跑：不推 Telegram，只生成候选
uv run python scripts/run_pipeline_once.py --no-push

# 限制每个 collector 拉取数量（调试用，快）
uv run python scripts/run_pipeline_once.py --limit 5 --no-push

# 限制最多生成几篇候选
uv run python scripts/run_pipeline_once.py --max-candidates 1 --no-push

# 同时限制（最快速冒烟）
uv run python scripts/run_pipeline_once.py --limit 3 --max-candidates 1 --no-push
```

---

## 长期运行进程（各开一个终端/tmux pane）

```bash
# 内容 pipeline 定时调度（每天 9:30 / 17:30）
uv run python scripts/run_scheduler.py

# Telegram bot 轮询（接收 ✅/❌ 按钮 + 自由回复重写）
uv run python scripts/run_telegram_bot.py
```

---

## 数据库

```bash
# 查看 DB 概要（帖数/信号数/今日预算）
uv run python scripts/inspect_db.py

# 查看最近帖子
uv run python scripts/inspect_db.py --posts

# 查看最近信号
uv run python scripts/inspect_db.py --signals

# 查看今日 LLM 花费
uv run python scripts/inspect_db.py --budget

# 清空所有帖子（schema 保留），换 persona/模板后用
uv run python scripts/reset_posts.py          # 有交互确认
uv run python scripts/reset_posts.py --yes    # 跳过确认
```

### Alembic 迁移

```bash
# 应用所有未执行的 migration（第一次建库 / 拉新代码后）
uv run alembic upgrade head

# 查看当前 DB 版本
uv run alembic current

# 查看迁移历史
uv run alembic history --verbose

# 生成新 migration（修改了 models.py 后）
uv run alembic revision --autogenerate -m "描述变更"

# 回滚一步
uv run alembic downgrade -1
```

---

## 内容管理

```bash
# 添加爆款样本（style exemplar）
uv run python scripts/add_exemplar.py \
  --template hidden_gem \
  --content "帖子正文..." \
  --note "来源说明"

# 记录发布结果（发了之后手动更新）
uv run python scripts/log_post_result.py --id <post_id> --decision published
uv run python scripts/log_post_result.py --id <post_id> --period 24h \
  --likes 120 --saves 45 --comments 8
```

---

## 可视化（S7）

```bash
# 测试渲染 7 张图（需要真实 entity 数据，见 /tmp/test_viz.py）
uv run python /tmp/test_viz.py

# 查看渲染输出目录（路径每次随机，看上面命令的输出）
open /var/folders/.../xhs_test_viz_.../
```

---

## 代码质量

```bash
# Lint + 格式检查
uv run ruff check src/ tests/

# 自动修复
uv run ruff check --fix src/ tests/

# 类型检查
uv run mypy src/xhs_agent
```

---

## 环境变量（`.env`）

| 变量 | 说明 | 必须 |
|------|------|------|
| `ANTHROPIC_API_KEY` | Haiku/Sonnet API key | ✅ |
| `TELEGRAM_BOT_TOKEN` | Bot token | ✅ |
| `TELEGRAM_CHAT_ID` | 推送目标 chat | ✅ |
| `DATABASE_URL` | 默认 `sqlite:///./xhs_agent.db` | — |
| `DAILY_BUDGET_USD` | 每日 LLM 预算上限，默认 `2.0` | — |
| `TELEGRAM_PUSH_ENABLED` | `false` 可关掉推送，调试用 | — |
| `TELEGRAM_DRY_RUN` | `true` 只 log 不发 | — |
| `ENV` | `local` / `production` | — |

复制 `.env.example` 为 `.env` 后填入真实凭证。

---

## 快速冒烟流程（新机器 / 新 clone）

```bash
uv sync                              # 1. 装依赖
cp .env.example .env && vim .env     # 2. 填凭证
uv run alembic upgrade head          # 3. 建/更新 DB
uv run pytest -q                     # 4. 跑测试（应全绿）
uv run python scripts/run_pipeline_once.py --limit 3 --max-candidates 1 --no-push
                                     # 5. 冒烟跑（不推 Telegram）
```
