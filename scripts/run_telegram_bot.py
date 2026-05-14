"""Run the Telegram bot polling loop forever.

Usage:
    uv run python scripts/run_telegram_bot.py

Long-running. Best run in a separate terminal / tmux pane / supervisor.
"""

from __future__ import annotations

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from xhs_agent.publishers.telegram_push import run_bot_polling


if __name__ == "__main__":
    run_bot_polling()
