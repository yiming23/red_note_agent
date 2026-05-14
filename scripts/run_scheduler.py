"""Run the APScheduler loop forever.

Usage:
    uv run python scripts/run_scheduler.py

Long-running. Best run in a separate terminal / tmux pane / supervisor.
"""

from __future__ import annotations

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from xhs_agent.orchestration.scheduler import run_forever


if __name__ == "__main__":
    run_forever()
