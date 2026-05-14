"""Delete xhs_viz_* temp dirs older than MAX_AGE_DAYS days."""
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

MAX_AGE_DAYS = 3


def cleanup() -> int:
    tmp = Path(tempfile.gettempdir())
    cutoff = datetime.now() - timedelta(days=MAX_AGE_DAYS)
    removed = 0
    for d in tmp.glob("xhs_viz_*"):
        if d.is_dir() and datetime.fromtimestamp(d.stat().st_mtime) < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    print(f"cleanup_images: removed {removed} dirs older than {MAX_AGE_DAYS}d")
    return removed


if __name__ == "__main__":
    cleanup()
