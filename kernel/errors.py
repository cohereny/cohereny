"""
kernel/errors.py

What can go wrong in the kernel, expressed without HTTP (ADR-030, CHY-101).

`core/authz.py` raised `HTTPException` — three times. That put a status code
decision inside the rules and made the rules unusable outside a request: a CLI
that wanted to check a role got a 403 object it had no idea what to do with.
The boundary guard flags it as "HTTPException below the transport layer".

The mapping to status codes now lives in api/main.py, where HTTP exists. The
codes are unchanged; only the place where they are chosen has moved.
"""
from __future__ import annotations


class KernelError(Exception):
    """Base class, so the transport layer can catch the family in one handler."""


class AccessDenied(KernelError):
    """The caller is known and is not permitted to do this."""

    def __init__(self, required: str, actual: str) -> None:
        self.required = required
        self.actual = actual
        super().__init__(f"Role '{required}' or higher required (caller has '{actual}')")


class ActorGone(KernelError):
    """The actor row vanished between resolving it and locking it.

    Reachable only if someone deleted the row mid-request. Kept distinct from
    AccessDenied because the cause is different even though both end in 403 —
    a caller who reads "actor no longer exists" knows to stop retrying.
    """

    def __init__(self) -> None:
        super().__init__("Actor no longer exists")


class ActorStateInconsistent(KernelError):
    """An anonymized row still carries an idp_sub, which the schema forbids.

    Migration 010 has a CHECK against exactly this. If it is ever raised, the
    database is in a state its own constraints rule out — that is a corruption
    report, not a race, and it deserves its own status code (409) rather than
    being folded into a generic failure.
    """

    def __init__(self) -> None:
        super().__init__("Actor record for this subject is in an inconsistent state.")


class ActorNotFound(KernelError):
    """No actor row with this id.

    Distinct from ActorGone, which means the row vanished mid-request: this one
    means the caller named an id that was never there.
    """

    def __init__(self) -> None:
        super().__init__("Actor not found")


class LastAdminProtected(KernelError):
    """Refusing to anonymize the only remaining active admin.

    The original reason was "clearing idp_sub means no JWT can ever match that
    row again". Since CHY-33 that is no longer true — the person would be
    provisioned as a new actor on their next request, with the role their
    groups give them.

    The guard stays for a better reason. Whether the admin comes back depends
    on the group mapping in Authentik: a system outside this transaction, which
    may not be configured, may have removed them at the same moment, or may not
    send groups at all. Making "can anyone still administer this installation"
    depend on that is not a bet worth taking when refusing costs one 409 and a
    sentence of explanation. `cc actor set-role` is the way back.
    """

    def __init__(self) -> None:
        super().__init__(
            "Refusing to anonymize the last active admin — the way back depends "
            "on a group mapping outside this system. Appoint another admin first."
        )


class UnknownRole(KernelError):
    """A role name that is not one of the five.

    kernel/roles.py is the single vocabulary. Every caller that used to carry
    its own list — the CLI did — is a second answer to the same question.
    """

    def __init__(self, role: str) -> None:
        self.role = role
        super().__init__(f"Unknown role {role!r}")


class ActorAnonymized(KernelError):
    """Refusing to reactivate an erased identity.

    An anonymized actor keeps its row so the audit log stays consistent, but
    the subject is gone. Bringing it back through the administrative door would
    undo the erasure the row is evidence of (GDPR Art. 17, ADR-025). A new
    subject is the way forward.
    """

    def __init__(self, idp_sub: str) -> None:
        self.idp_sub = idp_sub
        super().__init__(
            f"Actor {idp_sub!r} exists but is anonymized — refusing to "
            f"reactivate an erased identity. Provision a new subject instead."
        )


class ScopeNotFound(KernelError):
    """No scope with this id.

    Lived in contexts/repository/errors.py until CHY-121, because composition
    was the first thing that noticed a missing scope. `scope_nodes` belongs to
    the kernel, so the error does too — and there is now one of it rather than
    two that mean the same.

    A dedicated type rather than LookupError, which was the first attempt:
    KeyError and IndexError both inherit from it, so `except LookupError` in
    the endpoints turned any missing dictionary key inside the composition into
    a 404 saying the scope was unknown. A programming error disguised as a
    routine "not found" is the worst kind — it looks like data and gets
    investigated as data.
    """

    def __init__(self, scope_id: object = None) -> None:
        self.scope_id = scope_id
        super().__init__(
            f"Scope not found: {scope_id}" if scope_id is not None else "Scope not found"
        )


class ParentScopeNotFound(KernelError):
    """The parent a new scope was to hang under does not exist.

    Distinct from ScopeNotFound so the message can say which of the two ids the
    caller got wrong.
    """

    def __init__(self, parent_id: object) -> None:
        self.parent_id = parent_id
        super().__init__(f"Parent scope {parent_id} does not exist.")


class ScopeLoosensRiskClass(KernelError):
    """A scope would classify direct model calls looser than its ancestors do.

    Refused for the reason a looser data class is (ScopeLoosensPrivacy): the
    strictest class on the chain applies, so the looser one would be stored,
    shown and have no effect (CHY-199).
    """

    def __init__(self, declared: str, inherited: str) -> None:
        self.declared = declared
        self.inherited = inherited
        super().__init__(
            f"This scope would classify direct model calls as {declared!r}, but a "
            f"scope above it classifies them as {inherited!r}. A scope may be "
            f"stricter than the scopes above it, never looser — theirs would still "
            f"apply, and the classification would say something untrue."
        )


class ScopeLoosensSampleRate(KernelError):
    """A scope would draw a smaller share of direct calls than its ancestors do.

    The same rule as the class (CHY-269): along the chain the highest share
    applies, so a lower one would be stored, shown and have no effect.
    """

    def __init__(self, declared: float, inherited: float) -> None:
        self.declared = declared
        self.inherited = inherited
        super().__init__(
            f"This scope would draw {declared} of direct calls for review, but a scope "
            f"above it draws {inherited}. A scope may check more than the scopes above "
            f"it, never less — theirs would still apply."
        )


class ScopeLoosensPrivacy(KernelError):
    """A new scope declares a looser data class than its parent holds.

    Refused rather than stored. The strictest class on the chain wins, so the
    looser value would be ineffective — and a value that is stored, shown, and
    has no effect tells whoever reads it something false about where the data
    in that scope may go (CHY-160).
    """

    def __init__(self, declared: str, inherited: str) -> None:
        self.declared = declared
        self.inherited = inherited
        super().__init__(
            f"This scope would declare its data as {declared!r}, but its parent "
            f"holds {inherited!r} data. A scope may be stricter than its parent, "
            f"never looser — the parent's class would still apply, and the "
            f"declaration would say something untrue."
        )


class MembershipExists(KernelError):
    """This actor already holds a role in this scope.

    UNIQUE (scope_id, actor_id) from migration 001 is the rule; this only
    translates it. Checking first would be a second round trip and a race.
    """

    def __init__(self, actor_id: object) -> None:
        self.actor_id = actor_id
        super().__init__(
            f"Actor {actor_id} already holds a role in this scope. "
            f"Changing it is not supported yet."
        )


class ReviewAreaNotFound(KernelError):
    """No area of this name on this scope (CHY-180).

    Members are added on the scope that defines the area, not on one below it
    that only inherits it: whoever maintains the competence order of the bank
    does so where it is written down.
    """

    def __init__(self, scope_id: object, name: str) -> None:
        self.scope_id = scope_id
        self.name = name
        super().__init__(f"Scope {scope_id} defines no review area {name!r}.")


class ReviewAreaExists(KernelError):
    """This scope already defines an area of this name. UNIQUE (scope_id, name)."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"This scope already defines the review area {name!r}.")


class AreaMembershipExists(KernelError):
    """The actor is already a current member of this area."""

    def __init__(self, actor_id: object, name: str) -> None:
        self.actor_id = actor_id
        self.name = name
        super().__init__(f"Actor {actor_id} already belongs to the review area {name!r}.")


class AreaMembershipNotFound(KernelError):
    """The actor is not a current member of this area — nothing to revoke.

    A 404 rather than a quiet success, as for a retention override: revoking
    somebody who does not belong usually means the wrong area was named.
    """

    def __init__(self, actor_id: object, name: str) -> None:
        self.actor_id = actor_id
        self.name = name
        super().__init__(f"Actor {actor_id} is not a current member of the review area {name!r}.")


class HighRiskPeriodTooShort(KernelError):
    """A high-risk workflow and a period under six months would meet (CHY-266).

    Art. 26(6) AI Act: whoever deploys a high-risk AI system keeps its logs
    for at least six months. Decided by Thomas, 10.11.2026: in a scope with an
    active workflow classified `high` — or not classified, which counts as
    `high` (ADR-050) — neither the run trace nor the run content may be kept
    for less than 183 days. Refused when a workflow would be activated into
    such a scope, and when a period would be shortened below it there. A
    conflict with the installation's state, so 409 — retrying changes nothing.

    `shortfalls` maps each scope to the classes that fall short and the days
    they would have.
    """

    def __init__(self, shortfalls: dict, minimum: int, *, activating: bool) -> None:
        self.shortfalls = shortfalls
        self.minimum = minimum
        parts = "; ".join(
            f"scope {scope_id}: " + ", ".join(f"{cls} {days} days" for cls, days in classes.items())
            for scope_id, classes in shortfalls.items()
        )
        if activating:
            message = (
                f"This workflow counts as high-risk (risk_class high, or none), and its scope "
                f"keeps logs for less than {minimum} days ({parts}). The AI Act asks a "
                f"deployer to keep them at least six months (Art. 26(6)): set run_trace and "
                f"run_content to at least {minimum} days there "
                f"(PUT /v1/admin/retention/{{class}}), then activate."
            )
        else:
            message = (
                f"An active workflow that counts as high-risk (risk_class high, or none) "
                f"would keep its logs for less than {minimum} days ({parts}). The AI Act asks "
                f"a deployer to keep them at least six months (Art. 26(6)). Keep at least "
                f"{minimum} days there, or retire the workflow first."
            )
        super().__init__(message)


class RetentionOverrideNotFound(KernelError):
    """The scope has no period of its own for this retention class (CHY-17).

    A 404 rather than a quiet success: removing an override that is not there
    usually means the wrong scope was named, and the inherited period the
    caller expected to restore is already the one that applies.
    """

    def __init__(self, retention_class: str, scope_id: object) -> None:
        self.retention_class = retention_class
        self.scope_id = scope_id
        super().__init__(
            f"Scope {scope_id} has no period of its own for {retention_class}; "
            "it already inherits one."
        )


class InvalidIdentifiers(KernelError, ValueError):
    """What a caller named for redaction cannot be used (CHY-63).

    Also a ValueError, so that a pydantic validator turns it into a 422 without
    a handler of its own; the kernel mapping says 422 as well, for any path
    that raises it outside a request model. The runner turns it into a failed
    node: a run whose list is malformed must not send its data out unredacted.
    """


class RedactionRuleNotApprovable(KernelError):
    """A redaction rule that cannot become active, with the reason (CHY-63).

    Raised where a person is approving and the fix is cheap: a pattern that
    does not compile, a shipped rule switched off below the top of the tree, a
    loosening that does not declare itself. 422, like a routing rule that
    cannot be approved.
    """

    def __init__(self, block_id: object, reason: str) -> None:
        self.block_id = block_id
        self.reason = reason
        super().__init__(f"Redaction rule {block_id} cannot be approved: {reason}")


class MalformedRedactionRule(KernelError):
    """An active redaction rule that cannot be read (CHY-63).

    Reached only when a row became active without the check at approval. The
    call is refused rather than made without the rule: a rule that was meant
    to protect something and is silently skipped protects nothing. 500, like a
    malformed routing rule -- the fault is in the stored configuration, not in
    the request.
    """

    def __init__(self, block_id: object, reason: str) -> None:
        self.block_id = block_id
        self.reason = reason
        super().__init__(f"Redaction rule {block_id} cannot be read: {reason}")
