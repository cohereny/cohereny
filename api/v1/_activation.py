"""
api/v1/_activation.py

What has to hold before a block becomes `active`, by block type.

The composition root's half of the repository's `before_activation` seam
(CHY-160). Each check belongs to whoever knows what the block means — the
router for a routing rule, the learning context for a calibration anchor
(ADR-043), the kernel for a redaction rule (CHY-63) — and the repository may
import none of them (ADR-030). So the checks are
combined here and handed in, on every path that activates: approve, and create
and edit in a direct scope (ADR-042).

Each check passes block types it does not know untouched; calling all three is
cheaper to read than a dispatch table that would need its own completeness test.

The second seam, `routes_below_critical`, answers one question for four eyes
(CHY-163, ADR-045): does this block send data to a model below `critical`?
Only the router can say, so the answer is handed in the same way.
"""
from __future__ import annotations

from typing import Any

from api.v1._redaction import check_redaction_rule_before_activation
from api.v1._routing import check_rule_before_approval, rule_routes_below_critical
from contexts.learning.service import check_anchor_before_approval
from contexts.repository.service import ActivationHooks


async def check_before_activation(conn: Any, row: Any) -> None:
    """Raise to refuse activating `row`. Run inside the activating transaction."""
    await check_rule_before_approval(conn, row)
    await check_anchor_before_approval(conn, row)
    await check_redaction_rule_before_activation(conn, row)


def routes_below_critical(row: Any) -> bool:
    """Whether approving `row` lets data reach a model below `critical`.

    Only routing rules route; an anchor is measured against models, but the
    benchmark checks each one against the scope's class on its own (ADR-043).
    """
    return rule_routes_below_critical(row)


#: Both, as the repository takes them — handed to every path that activates.
HOOKS = ActivationHooks(before_activation=check_before_activation,
                        routes_below_critical=routes_below_critical)
