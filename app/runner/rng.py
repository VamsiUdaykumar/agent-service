"""The runner's only source of randomness — seeded deterministically from the
recipe `(agent_id, seed, input)`. No wall-clock, no `os.urandom` anywhere
downstream of this module (PRD §2).
"""

from __future__ import annotations

import hashlib
import random


def derive_seed(agent_id: str, seed: int, input: str) -> int:
    """Stable hash of the recipe tuple -> a 64-bit int usable as a PRNG seed."""
    payload = f"{agent_id}\x1f{seed}\x1f{input}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], byteorder="big")


def make_rng(agent_id: str, seed: int, input: str) -> random.Random:
    return random.Random(derive_seed(agent_id, seed, input))
