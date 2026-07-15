from app.runner.rng import derive_seed, make_rng


def test_derive_seed_is_deterministic() -> None:
    assert derive_seed("researcher", 42, "hello") == derive_seed("researcher", 42, "hello")


def test_derive_seed_distinguishes_each_recipe_field() -> None:
    base = derive_seed("researcher", 42, "hello")
    assert derive_seed("simple", 42, "hello") != base
    assert derive_seed("researcher", 43, "hello") != base
    assert derive_seed("researcher", 42, "goodbye") != base


def test_derive_seed_does_not_confuse_field_boundaries() -> None:
    # Naive string concatenation would collide these; the delimiter must not.
    a = derive_seed("agent", 1, "23")
    b = derive_seed("agent", 12, "3")
    assert a != b


def test_make_rng_produces_identical_draw_sequences_for_same_recipe() -> None:
    rng1 = make_rng("flaky", 7, "task")
    rng2 = make_rng("flaky", 7, "task")
    draws1 = [rng1.random() for _ in range(20)]
    draws2 = [rng2.random() for _ in range(20)]
    assert draws1 == draws2


def test_make_rng_differs_across_seeds() -> None:
    rng1 = make_rng("flaky", 7, "task")
    rng2 = make_rng("flaky", 8, "task")
    assert rng1.random() != rng2.random()
