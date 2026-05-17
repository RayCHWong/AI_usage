from __future__ import annotations

import os

from history_loader import load_entries

BURN_RATE_THRESH_NORMAL = 50.0    # tokens/min
BURN_RATE_THRESH_ACTIVE = 250.0
BURN_RATE_THRESH_HEAVY = 1000.0

GROUP_NAMES = ["Idle", "Normal", "Active", "Heavy"]


class UsageRateTracker:
    def __init__(self, forced_group: int | None = None, mock: bool = False) -> None:
        self.forced_group = forced_group
        self.mock = mock

    def sample(self, session_pct: float) -> None:
        _ = session_pct

    def group(self) -> int:
        forced_group = self._forced_group()
        if forced_group is not None:
            return forced_group
        if self.mock:
            return 0

        entries = load_entries(hours_back=1)
        if not entries:
            return 0

        total_tokens = sum(entry.total_tokens for entry in entries)
        elapsed_seconds = (entries[-1].timestamp - entries[0].timestamp).total_seconds()
        elapsed_minutes = max(elapsed_seconds / 60.0, 1.0)
        burn_rate = total_tokens / min(elapsed_minutes, 60.0)

        if burn_rate < BURN_RATE_THRESH_NORMAL:
            return 0
        if burn_rate < BURN_RATE_THRESH_ACTIVE:
            return 1
        if burn_rate < BURN_RATE_THRESH_HEAVY:
            return 2
        return 3

    def _forced_group(self) -> int | None:
        if self.forced_group is not None:
            return self.forced_group

        raw_value = os.environ.get("USAG_FORCE_GROUP")
        if raw_value is None:
            return None

        try:
            group = int(raw_value)
        except ValueError:
            return None

        if 0 <= group < len(GROUP_NAMES):
            return group
        return None
