"""Set-diff helper for detecting state transitions between runs."""
from __future__ import annotations


def compute_delta(*, current: set[str], stored: set[str]) -> tuple[set[str], set[str]]:
    """Return (newly_active, newly_inactive).

    - `newly_active`   = in current but not in stored  (state transitioned to active)
    - `newly_inactive` = in stored but not in current  (state transitioned away)
    """
    return current - stored, stored - current
