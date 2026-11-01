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
