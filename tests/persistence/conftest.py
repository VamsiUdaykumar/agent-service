from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.persistence.sqlite_repository import SqliteRepository


@pytest.fixture
async def repo(tmp_path: Path) -> AsyncIterator[SqliteRepository]:
    db_path = tmp_path / "test.db"
    repository = await SqliteRepository.connect(str(db_path))
    try:
        yield repository
    finally:
        await repository.close()
