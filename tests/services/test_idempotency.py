"""M6.T5: idempotent create-run semantics at the service layer."""

import asyncio

import pytest

from app.persistence.errors import IdempotencyConflictError
from app.persistence.sqlite_repository import SqliteRepository
from app.services.run_service import RunService

FAST = 50_000.0


async def test_same_key_and_body_replays_the_same_run_no_second_execution(
    repo: SqliteRepository,
) -> None:
    service = RunService(repo, sim_speed=FAST)
    payload = dict(agent_id="agent-simple", input={"prompt": "x"}, seed=1, metadata=None)

    first = await service.create_run(idempotency_key="key-1", **payload)
    second = await service.create_run(idempotency_key="key-1", **payload)

    assert second.id == first.id

    page = await repo.list_runs()
    assert len(page.data) == 1  # only one run exists at all


async def test_same_key_different_body_raises_conflict(repo: SqliteRepository) -> None:
    service = RunService(repo, sim_speed=FAST)
    await service.create_run(
        idempotency_key="key-1", agent_id="agent-simple", input={"prompt": "x"}, seed=1,
        metadata=None,
    )

    with pytest.raises(IdempotencyConflictError):
        await service.create_run(
            idempotency_key="key-1", agent_id="agent-simple", input={"prompt": "different"},
            seed=1, metadata=None,
        )

    page = await repo.list_runs()
    assert len(page.data) == 1  # the conflicting call created nothing


async def test_concurrent_requests_with_the_same_new_key_create_exactly_one_run(
    repo: SqliteRepository,
) -> None:
    service = RunService(repo, sim_speed=FAST)
    payload = dict(agent_id="agent-simple", input={"prompt": "x"}, seed=1, metadata=None)

    results = await asyncio.gather(
        *(service.create_run(idempotency_key="shared-key", **payload) for _ in range(10))
    )

    run_ids = {r.id for r in results}
    assert len(run_ids) == 1  # every caller got the same run back

    page = await repo.list_runs()
    assert len(page.data) == 1  # and exactly one run was ever created


async def test_no_idempotency_key_creates_a_fresh_run_every_time(repo: SqliteRepository) -> None:
    service = RunService(repo, sim_speed=FAST)
    payload = dict(agent_id="agent-simple", input={"prompt": "x"}, seed=1, metadata=None)

    first = await service.create_run(**payload)
    second = await service.create_run(**payload)

    assert first.id != second.id
    page = await repo.list_runs()
    assert len(page.data) == 2


async def test_expired_key_allows_a_fresh_run_to_be_created(repo: SqliteRepository) -> None:
    # A TTL of 0 hours makes every existing key immediately "expired" on the
    # next lookup — exercising the lazy-expiry path (M6.T4) without waiting.
    service = RunService(repo, sim_speed=FAST, idempotency_key_ttl_hours=0)
    payload = dict(agent_id="agent-simple", input={"prompt": "x"}, seed=1, metadata=None)

    first = await service.create_run(idempotency_key="key-1", **payload)
    second = await service.create_run(idempotency_key="key-1", **payload)

    assert first.id != second.id  # the "expired" key didn't force a replay
    page = await repo.list_runs()
    assert len(page.data) == 2


async def test_idempotency_conflict_error_reports_the_key(repo: SqliteRepository) -> None:
    service = RunService(repo, sim_speed=FAST)
    await service.create_run(
        idempotency_key="key-1", agent_id="agent-simple", input={"prompt": "x"}, seed=1,
        metadata=None,
    )
    with pytest.raises(IdempotencyConflictError) as exc_info:
        await service.create_run(
            idempotency_key="key-1", agent_id="agent-simple", input={"prompt": "y"}, seed=1,
            metadata=None,
        )
    assert exc_info.value.key == "key-1"
