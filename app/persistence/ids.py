"""ULID generation — the one place run IDs are minted.

ULIDs are time-sortable, so the same ID doubles as identity and as the
pagination cursor for `list_runs` (PRD §3.3).
"""

from __future__ import annotations

from ulid import ULID


def new_run_id() -> str:
    return str(ULID())
