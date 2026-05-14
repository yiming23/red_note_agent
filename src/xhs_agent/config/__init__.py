"""Configuration layer.

Two access patterns:
- `settings`   — environment / .env values (keys, db url, schedule times, ...)
- `tuning`     — domain knobs (signal thresholds, strictness, etc., from tuning.yaml)
"""

from xhs_agent.config.settings import get_settings, settings
from xhs_agent.config.tuning import get_tuning, reload_tuning, tuning

__all__ = ["settings", "get_settings", "tuning", "get_tuning", "reload_tuning"]
