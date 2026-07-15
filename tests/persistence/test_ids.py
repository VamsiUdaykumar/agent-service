import string

from app.persistence.ids import new_run_id

_CROCKFORD_ALPHABET = set(string.ascii_uppercase + string.digits) - {"I", "L", "O", "U"}


def test_run_id_is_26_char_ulid() -> None:
    run_id = new_run_id()
    assert len(run_id) == 26
    assert set(run_id) <= _CROCKFORD_ALPHABET


def test_run_ids_are_unique() -> None:
    ids = {new_run_id() for _ in range(1000)}
    assert len(ids) == 1000
