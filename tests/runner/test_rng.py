from app.runner.rng import derive_seed, make_rng


def test_derive_seed_is_deterministic() -> None:
    assert derive_seed("agent-researcher", 42, {"prompt": "hello"}) == derive_seed(
        "agent-researcher", 42, {"prompt": "hello"}
    )


def test_derive_seed_distinguishes_each_recipe_field() -> None:
    base = derive_seed("agent-researcher", 42, {"prompt": "hello"})
    assert derive_seed("agent-simple", 42, {"prompt": "hello"}) != base
    assert derive_seed("agent-researcher", 43, {"prompt": "hello"}) != base
    assert derive_seed("agent-researcher", 42, {"prompt": "goodbye"}) != base


def test_derive_seed_does_not_confuse_field_boundaries() -> None:
    # Naive string concatenation would collide these; the delimiter must not.
    a = derive_seed("agent", 1, {"prompt": "23"})
    b = derive_seed("agent", 12, {"prompt": "3"})
    assert a != b


def test_derive_seed_is_independent_of_input_key_order() -> None:
    # {"a":1,"b":2} and {"b":2,"a":1} must derive the same seed — the
    # recipe hash canonicalizes the input object before hashing it.
    a = derive_seed("agent", 1, {"a": 1, "b": 2})
    b = derive_seed("agent", 1, {"b": 2, "a": 1})
    assert a == b


def test_derive_seed_is_independent_of_nested_key_order() -> None:
    a = derive_seed("agent", 1, {"outer": {"a": 1, "b": 2}})
    b = derive_seed("agent", 1, {"outer": {"b": 2, "a": 1}})
    assert a == b


def test_derive_seed_still_distinguishes_different_values_with_same_keys() -> None:
    a = derive_seed("agent", 1, {"a": 1, "b": 2})
    b = derive_seed("agent", 1, {"a": 2, "b": 1})
    assert a != b


def test_make_rng_produces_identical_draw_sequences_for_same_recipe() -> None:
    rng1 = make_rng("agent-flaky", 7, {"prompt": "task"})
    rng2 = make_rng("agent-flaky", 7, {"prompt": "task"})
    draws1 = [rng1.random() for _ in range(20)]
    draws2 = [rng2.random() for _ in range(20)]
    assert draws1 == draws2


def test_make_rng_differs_across_seeds() -> None:
    rng1 = make_rng("agent-flaky", 7, {"prompt": "task"})
    rng2 = make_rng("agent-flaky", 8, {"prompt": "task"})
    assert rng1.random() != rng2.random()


def test_make_rng_is_independent_of_input_key_order() -> None:
    rng1 = make_rng("agent-flaky", 7, {"a": 1, "b": 2})
    rng2 = make_rng("agent-flaky", 7, {"b": 2, "a": 1})
    assert rng1.random() == rng2.random()
