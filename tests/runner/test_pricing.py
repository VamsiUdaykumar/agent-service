import pytest

from app.runner.pricing import MODEL_NAMES, PRICE_TABLE, cost_usd


def test_two_models_in_the_price_table() -> None:
    assert len(PRICE_TABLE) == 2
    assert set(MODEL_NAMES) == set(PRICE_TABLE)


def test_output_priced_richer_than_input_for_every_model() -> None:
    for price in PRICE_TABLE.values():
        assert price.output_per_1k > price.input_per_1k


def test_cost_usd_scales_linearly_with_tokens() -> None:
    name = MODEL_NAMES[0]
    assert cost_usd(name, 0, 0) == 0.0
    assert cost_usd(name, 2000, 0) == pytest.approx(2 * cost_usd(name, 1000, 0))
    assert cost_usd(name, 0, 2000) == pytest.approx(2 * cost_usd(name, 0, 1000))
