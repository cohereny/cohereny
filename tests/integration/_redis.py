"""Redis, as far as a block write touches it."""
from __future__ import annotations


class Generations:
    """Stands in for Redis where the repository only bumps scope generations.

    The one command a block write sends (compose.bump_scope_generation). The
    service swallows a failing Redis on purpose, so passing None would make a
    missing invalidation indistinguishable from a working one — this records it.
    """

    def __init__(self) -> None:
        self.bumped: list[str] = []

    async def incr(self, key: str) -> int:
        self.bumped.append(key)
        return self.bumped.count(key)
