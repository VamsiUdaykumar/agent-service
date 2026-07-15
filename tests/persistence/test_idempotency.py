import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.persistence.errors import IdempotencyConflictError
from app.persistence.sqlite_repository import SqliteRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FAR_PAST_CUTOFF = NOW - timedelta(hours=24)


def _run_kwargs(run_id: str, **overrides: object) -> dict:
    defaults: dict[str, object] = dict(
        run_id=run_id,
        agent_id="agent-simple",
        seed=1,
        input={"prompt": "x"},
        metadata=None,
        trace_id="a" * 32,
        created_at=NOW,
    )
    defaults.update(overrides)
    return defaults


async def test_fresh_key_creates_the_run(repo: SqliteRepository) -> None:
    record, outcome = await repo.create_run_idempotent(
        idempotency_key="key-1", request_hash="hash-a", ttl_cutoff=FAR_PAST_CUTOFF,
        **_run_kwargs("run-a"),
    )
    assert outcome == "created"
    assert record.id == "run-a"

    fetched = await repo.get_run("run-a")
    assert fetched is not None
    assert fetched.status.value == "pending"


async def test_live_existing_key_with_matching_hash_replays(repo: SqliteRepository) -> None:
    first, _ = await repo.create_run_idempotent(
        idempotency_key="key-1", request_hash="hash-a", ttl_cutoff=FAR_PAST_CUTOFF,
        **_run_kwargs("run-a"),
    )
    second, outcome = await repo.create_run_idempotent(
        idempotency_key="key-1", request_hash="hash-a", ttl_cutoff=FAR_PAST_CUTOFF,
        **_run_kwargs("run-b"),
    )
    assert outcome == "replayed"
    assert second.id == first.id
    assert await repo.get_run("run-b") is None  # the second run was never created


async def test_live_existing_key_with_different_hash_conflicts(repo: SqliteRepository) -> None:
    await repo.create_run_idempotent(
        idempotency_key="key-1", request_hash="hash-a", ttl_cutoff=FAR_PAST_CUTOFF,
        **_run_kwargs("run-a"),
    )
    with pytest.raises(IdempotencyConflictError):
        await repo.create_run_idempotent(
            idempotency_key="key-1", request_hash="hash-b", ttl_cutoff=FAR_PAST_CUTOFF,
            **_run_kwargs("run-b"),
        )
    assert await repo.get_run("run-b") is None


async def test_expired_existing_key_is_overwritten_and_creates_a_new_run(
    repo: SqliteRepository,
) -> None:
    old = NOW - timedelta(hours=25)
    await repo.create_run_idempotent(
        idempotency_key="key-1", request_hash="hash-old", ttl_cutoff=old - timedelta(hours=24),
        **_run_kwargs("run-old", created_at=old),
    )
    # A cutoff of NOW - 24h makes the 25h-old reservation expired.
    record, outcome = await repo.create_run_idempotent(
        idempotency_key="key-1", request_hash="hash-new", ttl_cutoff=NOW - timedelta(hours=24),
        **_run_kwargs("run-new"),
    )
    assert outcome == "created"
    assert record.id == "run-new"


async def test_concurrent_calls_with_the_same_new_key_create_exactly_one_run(
    repo: SqliteRepository,
) -> None:
    results = await asyncio.gather(
        *(
            repo.create_run_idempotent(
                idempotency_key="shared-key", request_hash="same-hash", ttl_cutoff=FAR_PAST_CUTOFF,
                **_run_kwargs(f"run-{i}"),
            )
            for i in range(10)
        )
    )
    winning_ids = {record.id for record, _ in results}
    assert len(winning_ids) == 1
    outcomes = [outcome for _, outcome in results]
    assert outcomes.count("created") == 1
    assert outcomes.count("replayed") == 9


async def test_reservation_pointing_at_a_nonexistent_run_is_reclaimed(
    repo: SqliteRepository,
) -> None:
    """Simulates the crash window this method's atomicity is designed to
    close: a dangling `idempotency_keys` row that points at a run which
    was never actually created (e.g. hand-inserted here to stand in for
    data written by a hypothetical prior crash/bug). A retry with the same
    key and request hash must NOT error — it should treat the dangling
    reservation as stale, reclaim it, and create the run itself.
    """
    async with repo._lock:  # noqa: SLF001 - deliberately bypassing the public API
        await repo._conn.execute(
            "INSERT INTO idempotency_keys (key, request_hash, run_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            ("key-1", "hash-a", "run-that-never-existed", NOW.isoformat()),
        )
        await repo._conn.commit()

    assert await repo.get_run("run-that-never-existed") is None

    record, outcome = await repo.create_run_idempotent(
        idempotency_key="key-1", request_hash="hash-a", ttl_cutoff=FAR_PAST_CUTOFF,
        **_run_kwargs("run-retry"),
    )

    assert outcome == "created"
    assert record.id == "run-retry"
    fetched = await repo.get_run("run-retry")
    assert fetched is not None
    assert fetched.status.value == "pending"

    # A subsequent retry now replays the reclaimed run, not the dangling one.
    again, outcome_again = await repo.create_run_idempotent(
        idempotency_key="key-1", request_hash="hash-a", ttl_cutoff=FAR_PAST_CUTOFF,
        **_run_kwargs("run-yet-another"),
    )
    assert outcome_again == "replayed"
    assert again.id == "run-retry"
