"""
api/v1/_routing.py

Putting a routing decision together (CHY-87 step 2b).

Three parts have to meet: the scope chain from the kernel, the stored rules
from the repository context, and the reading in `router.rules`. None of them
may know the other two — a context does not import a context (ADR-030), and
`router/` sits below the transport and cannot reach either.

So they meet here. This is the composition root doing its one job, the same
way `api/v1/workflows.py` builds the executor registry out of two contexts.
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from api.config import settings
from contexts.repository import service as repository
from kernel import scopes
from kernel.errors import ScopeNotFound
from router import rules
from router.config import RouterConfig
from router.errors import MalformedRoutingRule, RoutingRuleNotApprovable


def load_router_config(path: str) -> RouterConfig:
    """The model declaration at `path`, or the bundled one when it is empty.

    `ROUTING_CONFIG` names the file since CHY-189. A path that does not exist
    stops the start with the path in the message rather than falling back to
    the bundled file: silently routing against the reference installation's
    models would look like a working configuration and name models this
    deployment cannot reach.
    """
    return RouterConfig(Path(path)) if path else RouterConfig()


#: The deployment's model declaration, loaded once at import.
#:
#: One instance for every caller that routes — `/v1/complete` and the workflow
#: runner (CHY-145). Before this each kept its own, and two readings of the same
#: file are two chances to disagree after a hot edit. Loading at import is also
#: the point: a declaration that cannot be read stops the process at start,
#: not at the first request that needs it (router/config.py).
router_config = load_router_config(settings.routing_config)


async def route_for(
    pool, config: RouterConfig, scope_id: UUID, task_type: str
) -> rules.RoutingRule:
    """The rule that routes this task type, seen from this scope.

    Its `candidates` say which models serve the task, best first; its
    `max_tokens` how long an answer may be (CHY-166). One lookup for both, so
    the limit always belongs to the chain it is applied to. Named
    `candidates_for` until CHY-166, when it still returned only the chain.

    Raises `ScopeNotFound` for a scope that does not exist and `NoRouteForTask`
    when it exists and nobody decided — two different answers, because they
    call for two different fixes. Collapsing them would send whoever reads the
    error looking for a missing scope when the scope is fine.
    """
    chain = await scopes.ancestor_chain(pool, scope_id)
    if not chain:
        raise ScopeNotFound

    scope_order = [row["id"] for row in chain]
    stored = await repository.routing_rules_for(pool, scope_order)
    rule = rules.resolve(stored, scope_order, task_type, config)
    # The second enforcement point, and the one no path goes around: a rule may
    # reach `active` without the approve endpoint, and a scope may be
    # classified after its rules were approved (CHY-160).
    rules.check_ceiling(
        rule.candidates, scopes.effective_privacy_level(chain), config, scope_id
    )
    return rule


async def check_rule_before_approval(conn, row) -> None:
    """Refuse to approve a routing rule its own scope could never use.

    Passed to the repository's `transition` as `before_activation` and run
    inside its transaction, on the locked row (CHY-160). The first enforcement
    point, where a person is standing and the fix is cheap. Without it a rule
    naming a model below the scope's class would be approved, show as `active`,
    and fail at the first request — found by a caller rather than by the
    reviewer who could have caught it.

    Every other block type passes untouched. For a rule it checks what
    resolution checks: the chain does not descend (check_chain), and no
    candidate sits below the data (check_ceiling).
    """
    if row["block_type"] != "routing_rule":
        return
    # The same reading as at resolution, reported as the approver's to fix
    # (CHY-166). Unwrapped, a rule without max_tokens or with a descending
    # chain answered the reviewer with a 500.
    try:
        rule = rules.parse(row)
        rules.check_chain(rule, router_config)
    except MalformedRoutingRule as exc:
        raise RoutingRuleNotApprovable(row["block_id"], str(exc)) from exc
    chain = await scopes.ancestor_chain(conn, row["scope_id"])
    if not chain:
        raise ScopeNotFound
    rules.check_ceiling(
        rule.candidates, scopes.effective_privacy_level(chain),
        router_config, row["scope_id"],
    )
