"""The runner's only source of randomness — seeded deterministically from the
recipe `(agent_id, seed, input)`. No wall-clock, no `os.urandom` anywhere
downstream of this module (PRD §2).
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any


def derive_seed(agent_id: str, seed: int, input: dict[str, Any]) -> int:
    """Stable hash of the recipe tuple -> a 64-bit int usable as a PRNG seed.

    `input` is a JSON object; it's serialized with sorted keys before
    hashing so key order never affects the derived seed — `{"a":1,"b":2}`
    and `{"b":2,"a":1}` must derive the same seed (PRD §2 determinism).
    """
    canonical_input = json.dumps(input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload = f"{agent_id}\x1f{seed}\x1f{canonical_input}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], byteorder="big")


def make_rng(agent_id: str, seed: int, input: dict[str, Any]) -> random.Random:
    return random.Random(derive_seed(agent_id, seed, input))
