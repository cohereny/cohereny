"""
api/v1/_activation.py

What has to hold before a block becomes `active`, by block type.

The composition root's half of the repository's `before_activation` seam
(CHY-160). Each check belongs to whoever knows what the block means — the
router for a routing rule, the learning context for a calibration anchor
(ADR-043) — and the repository may import neither (ADR-030). So the checks are
combined here and handed in, on every path that activates: approve, and create
and edit in a direct scope (ADR-042).

Each check passes block types it does not know untouched; calling both is
cheaper to read than a dispatch table that would need its own completeness test.
"""
from __future__ import annotations

from typing import Any

from api.v1._routing import check_rule_before_approval
from contexts.learning.service import check_anchor_before_approval


async def check_before_activation(conn: Any, row: Any) -> None:
    """Raise to refuse activating `row`. Run inside the activating transaction."""
    await check_rule_before_approval(conn, row)
    await check_anchor_before_approval(conn, row)
