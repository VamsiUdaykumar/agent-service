"""Static two-model price table (PRD §3.2). Output priced richer than input
per token, matching real-world model pricing shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    name: str
    input_per_1k: float
    output_per_1k: float


PRICE_TABLE: dict[str, ModelPrice] = {
    "stackai-mini": ModelPrice(name="stackai-mini", input_per_1k=0.0005, output_per_1k=0.0020),
    "stackai-pro": ModelPrice(name="stackai-pro", input_per_1k=0.0050, output_per_1k=0.0200),
}

MODEL_NAMES: tuple[str, ...] = tuple(PRICE_TABLE)


def cost_usd(model_name: str, tokens_in: int, tokens_out: int) -> float:
    price = PRICE_TABLE[model_name]
    return (tokens_in / 1000) * price.input_per_1k + (tokens_out / 1000) * price.output_per_1k
