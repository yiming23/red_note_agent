# 小红书游戏内容 Agent

半自动化游戏内容创作助手：采集真实数据 → LLM 判断+创作 → Telegram 推送候选 → 你审核反馈 → 你在小红书 App 手动发布。

> 详细架构见 [DESIGN.md](./DESIGN.md)，当前进度见 [HANDOFF.md](./HANDOFF.md)。

---

## 快速开始

### 1. 环境准备

需要 Python 3.11+。我们用 [uv](https://github.com/astral-sh/uv) 管理虚拟环境和依赖（比 pip 快很多，pyproject.toml 标准化）。

```bash
# 安装 uv（macOS）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 进入项目目录
cd "/Users/yiming/Documents/Claude/Projects/Red note content agent"

# 创建独立 venv 并安装依赖（uv 会自动建 .venv 目录）
uv sync

# 激活 venv（VS Code 会自动识别）
source .venv/bin/activate
```

VS Code 会自动检测到 `.venv` 并提示选用作 interpreter。也可以手动：`Cmd+Shift+P` → "Python: Select Interpreter" → 选 `.venv/bin/python`。

### 2. 配置 .env

```bash
cp .env.example .env
# 用任意编辑器打开 .env 填入凭证
```

填的内容详见下面"API 凭证申请指南"。

### 3. 初始化数据库

```bash
# 创建本地 SQLite 数据库 + 跑所有 migration
uv run alembic upgrade head
```

### 4. 跑测试 / 试运行

```bash
# 单元测试
uv run pytest tests/unit/

# 手动触发一次 content pipeline（不启动 scheduler / bot）
uv run python -m xhs_agent.cli run-content-pipeline

# 启动 Telegram bot（常驻）
uv run python scripts/run_telegram_bot.py

# 启动 scheduler（常驻，按时跑 trend / content pipeline）
uv run python scripts/run_scheduler.py
```

---

## API 凭证申请指南

下面这些必须的凭证你需要申请。等你申请好填进 `.env` 后，整个系统就能跑起来。

### 🟢 必需：Anthropic API Key（你已有）

直接填到 `.env` 的 `ANTHROPIC_API_KEY`。

### 🟢 必需：Telegram Bot Token + Chat ID

#### 创建专属 bot

1. 在 Telegram 里找 **@BotFather**
2. 发 `/newbot`
3. 起个名字（比如 `xhs_game_agent_bot`）
4. BotFather 会回你一个 token（形如 `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`）→ 填到 `.env` 的 `TELEGRAM_BOT_TOKEN`

#### 拿你自己的 chat_id

1. 用你自己的 Telegram 账号给刚建的 bot 发任意一条消息（比如 "hi"）
2. 在浏览器访问：
   ```
   https://api.telegram.org/bot<你的TOKEN>/getUpdates
   ```
3. 在返回 JSON 里找 `"chat":{"id": 123456789, ...}` → 这个数字就是你的 chat_id
4. 填到 `.env` 的 `TELEGRAM_CHAT_ID`

> 用你的私人账号，不要用群组（除非你想让其他人也看到候选）。

### 🟡 V1 需要：Reddit API（PRAW）

V0 不需要，V1 trend pipeline 启用时再申请。

1. 登录 reddit.com，访问 https://www.reddit.com/prefs/apps
2. 拉到底，点 **"create another app..."**
3. 选 **script** 类型（个人脚本）
4. 名字随意，redirect URI 填 `http://localhost:8080`
5. 创建后复制：
   - **client_id**：在 app 名字下方那串短字符
   - **client_secret**：标着 "secret" 的那串
6. 填到 `.env`：
   ```
   REDDIT_CLIENT_ID=...
   REDDIT_CLIENT_SECRET=...
   REDDIT_USER_AGENT=xhs_game_agent/0.1 by /u/your_username
   ```

### ⚪ 不需要 key 的源

- **Steam Web API** — 公开端点，无需 key
- **SteamSpy** — 无需 key
- **Bilibili** — 用非官方 API（`bilibili-api-python` 库），无需 key
- **NGA / 贴吧 / 虎扑** — 直接 HTML 解析，无需 key

### 🔵 V1 可选：fal.ai (图片生成)

V1 启用图片生成时再申请。https://fal.ai/dashboard/keys → 创建 API key → 填到 `.env` 的 `FAL_API_KEY`。

---

## 项目结构

参见 [DESIGN.md § 4](./DESIGN.md#4-目录结构)。

## 当前进度

参见 [HANDOFF.md](./HANDOFF.md)。

## 工作约定

- 改动前先读 [DESIGN.md § 14 关键不变量](./DESIGN.md#14-关键不变量写代码时务必遵守)
- 完成一个模块就 commit
- 每次工作 session 结尾更新 HANDOFF.md
- 数据库结构变更走 alembic migration，不直接改 models.py 后跑 create_all
