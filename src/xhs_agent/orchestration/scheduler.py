"""APScheduler setup for periodic content pipeline runs.

Local: BlockingScheduler in a single process.
Server: same code; deployed under systemd or docker-compose.

DESIGN.md § 11.
"""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from xhs_agent.config import settings
from xhs_agent.observability.logger import get_logger
from xhs_agent.orchestration.pipeline import run_content_pipeline
from xhs_agent.publishers.telegram_push import send_plain

log = get_logger(__name__)


def build_scheduler() -> BlockingScheduler:
    sched = BlockingScheduler(timezone="UTC")
    for hour in settings.content_pipeline_hour_list:
        # Stagger 30 min after the hour so trend pipeline (V1) at HH:00 lands first
        trigger = CronTrigger(hour=hour, minute=30)
        sched.add_job(
            _run_with_telemetry,
            trigger=trigger,
            id=f"content_pipeline_{hour:02d}",
            name=f"content_pipeline_{hour:02d}",
            max_instances=1,
            coalesce=True,
        )
    sched.add_job(
        _cleanup_images,
        CronTrigger(hour=3, minute=0),
        id="cleanup_images",
        max_instances=1,
    )
    return sched


def _cleanup_images() -> None:
    import subprocess
    import sys
    subprocess.run(
        [sys.executable, "scripts/cleanup_old_images.py"],
        check=False,
    )


def _run_with_telemetry() -> None:
    """Wrap the pipeline run with structured logging and Telegram error alerts."""
    log.info("scheduled_pipeline_trigger")
    try:
        result = run_content_pipeline()
        log.info(
            "scheduled_pipeline_finished",
            run_id=result.run_id,
            generated=result.posts_generated,
            pushed=result.posts_pushed,
            errors=len(result.errors),
        )
        if result.errors:
            send_plain(
                f"⚠️ Pipeline run #{result.run_id} 完成但有错误：\n"
                + "\n".join(f"  • {e}" for e in result.errors[:5])
            )
    except Exception as exc:
        log.error("scheduled_pipeline_crashed", error=str(exc))
        send_plain(f"❌ Pipeline 崩溃：{exc}")


def run_forever() -> None:
    """Blocking entry point for the scheduler worker process."""
    sched = build_scheduler()
    log.info(
        "scheduler_start",
        hours=settings.content_pipeline_hour_list,
    )
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler_stop")
