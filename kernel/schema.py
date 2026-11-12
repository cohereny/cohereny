"""
kernel/schema.py

Transport shapes of the kernel (ADR-030, CHY-123).

Moved here from `domain/actor.py`. The kernel owns `actors`, so it owns the
shapes that describe them; `domain/` was a shared folder every zone reached
into, which is exactly the coupling ADR-030 dissolves.

`AuditLogEntry` is not here but in `kernel/audit.py`, next to the function that
writes the rows it describes — the audit log is one subject and reads better in
one file.

Two subjects live here, and both belong to the kernel: **actors** and
**scopes** (`domain/actor.py` with CHY-123, `domain/scope.py` with CHY-121).
They are one file rather than two because the layout has one `schema.py` per
zone — the same call as in `contexts/repository/`.

An actor is the internal identity of whoever acts. The identity provider knows
a subject; this row is what the audit log, the scope memberships and every
foreign key point at. The two are joined in `kernel/identity.py` and separated
again by an erasure (GDPR Art. 17), which clears the provider's subject and
keeps the row.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from kernel.roles import Role

# The roles come from domain/roles.py; they are not restated here.
#
# The old comment claimed this was "the single place to change when a role is
# added". It was not — the same alias, with the same list written out, also sat
# in domain/scope.py, and the ranking and the group mapping were two more
# copies. The consolidation in INFRA-92 caught the latter two and missed these,
# because it looked where roles were *used* rather than where they were
# *named*.
#
# Pydantic validates a StrEnum against its members, so nothing changes for
# callers: Role.ADMIN == "admin" is true.
ActorRole = Role

# What display_name is set to on erasure. A fixed string rather than something
# derived from the old name — a derivation would leak what it is meant to remove.
ANONYMIZED_DISPLAY_NAME = "[anonym]"


class Actor(BaseModel):
    """An actor as stored, including its anonymization state."""

    id: UUID
    idp_sub: str | None = Field(
        default=None,
        description="Authentik subject. NULL for local actors and anonymized ones.",
    )
    display_name: str
    role: ActorRole
    created_at: datetime
    is_anonymized: bool = False
    anonymized_at: datetime | None = None

    @model_validator(mode="after")
    def _anonymization_is_consistent(self) -> Actor:
        """Mirror the database CHECK constraints from migration 010.

        Duplicated deliberately. The database is the authority, but a model that
        can represent a state the database rejects turns a clear constraint
        violation into a confusing one — and lets a test construct an actor that
        could never exist.
        """
        if self.is_anonymized:
            if self.anonymized_at is None:
                raise ValueError(
                    "is_anonymized is set but anonymized_at is missing; the time of "
                    "erasure is evidence for the GDPR Art. 12(3) deadline"
                )
            if self.idp_sub is not None:
                raise ValueError(
                    "an anonymized actor must not carry an idp_sub — severing the "
                    "identity link is the point of the erasure"
                )
        elif self.anonymized_at is not None:
            raise ValueError(
                "anonymized_at is set but is_anonymized is False; an erasure cannot "
                "have a date and not have happened"
            )
        return self

    @property
    def is_identifiable(self) -> bool:
        """True when this actor can still be traced back to a person.

        A local actor without an idp_sub is not identifiable through the IdP, but
        its display_name may still name someone — so this asks about the IdP link
        specifically, not about the absence of all personal data.
        """
        return self.idp_sub is not None


class ActorOut(BaseModel):
    """Actor as returned by the API.

    `idp_sub` is deliberately absent: it is the IdP's identifier for a person and
    has no use outside authentication. Withholding it keeps the erasure surface
    small — there is no point anonymizing a column that was handed out earlier.
    """

    id: UUID
    display_name: str
    role: ActorRole
    created_at: datetime
    is_anonymized: bool
    anonymized_at: datetime | None = None

    @classmethod
    def from_actor(cls, actor: Actor) -> ActorOut:
        return cls(
            id=actor.id,
            display_name=actor.display_name,
            role=actor.role,
            created_at=actor.created_at,
            is_anonymized=actor.is_anonymized,
            anonymized_at=actor.anonymized_at,
        )


class AnonymizeResult(BaseModel):
    """Outcome of an erasure request.

    `already_anonymized` distinguishes "we did it just now" from "it was already
    done". Both are a success for the requester — repeating the request must not
    fail — but only the first is a state change worth writing to the audit log.
    """

    actor_id: UUID
    is_anonymized: bool = True
    anonymized_at: datetime
    already_anonymized: bool = False


# ── Scopes: the tree everything else is addressed in ────────────────────────


# ltree label convention, per ADR-008 (CHY-59): `{node_type}_{uuid}` with the
# UUID's hyphens turned into underscores, dots separating levels:
#
#     org_acme.team_it.project_study
#
# Deliberately stricter than what PostgreSQL accepts. ADR-008 justifies the
# underscore rule with "ltree labels are [A-Za-z0-9_] only" -- that stopped
# being true in PostgreSQL 16, which allows hyphens (verified 2026-08-07:
# 'org_acme.team-it'::ltree parses without complaint). The convention still
# stands, but for consistency now, not because the database enforces it.
# Validating here keeps one spelling of a scope path rather than two that
# silently address different rows.
_LABEL_RE = re.compile(r"^[A-Za-z0-9_]+$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")


def scope_label(node_type: str, node_id: UUID | str) -> str:
    """Build a single ltree label. Mirrors the SQL function cc_scope_label().

    Two implementations of one convention drift, and a drifted scope label is
    invisible: the lookup simply returns nothing. Both are covered by
    tests/test_scope_path.py.
    """
    return f"{node_type}_{str(node_id).replace('-', '_')}"

ScopeNodeType = Literal["org", "team", "workspace", "project", "sub_project", "personal"]

#: The largest value of a PostgreSQL INTEGER — the type of every version,
#: limit and counter column in the schema. Request fields that reach such a
#: column carry it as `le=` (CHY-165): without it an out-of-range value went
#: all the way to asyncpg, overflowed, and came back as a 500 for what was the
#: caller's mistake. Schemathesis found it with `page=2226…` on the first run.
#: One constant rather than the literal in six places, so the bound and its
#: reason are in one spot.
PG_INT_MAX = 2_147_483_647

#: Classes of data, low to high (CHY-160). The kernel owns the vocabulary
#: because two zones use it: a scope declares its class here, and the router
#: compares it with each model's ceiling (router/config.py imports this). It
#: lived in router/config.py alone until the scope needed it — a second copy
#: in the kernel would have been two lists that could drift.
PrivacyLevel = Literal["public", "internal", "confidential", "critical"]
PRIVACY_LEVELS: tuple[str, ...] = get_args(PrivacyLevel)

#: How risky direct model calls in a scope are (CHY-199, ADR-050), weakest
#: first. The three classes a workflow carries (migration 044); a scope where
#: nobody classified direct calls counts as `high`, as an unclassified workflow.
RiskClass = Literal["minimal", "limited", "high"]
RISK_CLASSES: tuple[str, ...] = get_args(RiskClass)

#: A share drawn for review is stored as NUMERIC(4, 3) — at a sample gate
#: (migration 045) and for direct calls (048): three decimal places, so the
#: smallest share there is is one in a thousand.
SMALLEST_SHARE = 0.001


def share_as_stored(rate: float | None) -> float | None:
    """A share drawn for review, as its column keeps it — or a ValueError.

    Refused rather than rounded: 0.0505 would be kept as 0.051 and nobody told,
    and a share small enough to round to 0 broke the CHECK — a 500 for direct
    calls (CHY-269), a misleading 422 at a sample gate (CHY-276). One rule for
    both places, so the two cannot drift. The lower bound itself is the
    caller's `ge=SMALLEST_SHARE`, which puts it into the contract too.
    """
    if rate is not None and round(rate, 3) != rate:
        msg = "a share has at most three decimal places, as it is stored (0.001 to 1)"
        raise ValueError(msg)
    return rate
# Second copy of the alias from domain/actor.py, list and all. Both are gone;
# the roles live in domain/roles.py (INFRA-92).
ActorRole = Role


class ScopeNode(BaseModel):
    """A node in the scope tree — represents an organizational or project level.

    scope_path is the full path from the root, stored as an ltree column in
    PostgreSQL (migration 009). Enables hierarchy queries without recursive
    CTEs: descendants via `scope_path <@ $1`, ancestors via `scope_path @> $1`.

    It is a dot-separated string, not a list -- that is what the driver returns
    for an ltree column, and splitting it here would mean joining it again for
    every query. Use .labels when the parts are needed.

    parent_id=None means root node (org level).
    """

    id: UUID
    scope_path: str
    node_type: ScopeNodeType
    name: str
    description: str | None = None
    parent_id: UUID | None = None
    #: Declared at this node, or None. Not the effective class — that is the
    #: strictest on the ancestor chain (kernel.scopes.effective_privacy_level).
    privacy_level: PrivacyLevel | None = None
    created_by: UUID | None = None
    created_at: datetime

    @field_validator("scope_path")
    @classmethod
    def _valid_ltree_path(cls, v: str) -> str:
        if not _PATH_RE.match(v):
            raise ValueError(
                f"scope_path {v!r} is not a valid ltree path per ADR-008 — "
                "dot-separated labels of [A-Za-z0-9_]. Build labels with scope_label()."
            )
        return v

    @property
    def labels(self) -> list[str]:
        """The path split into its levels, root first."""
        return self.scope_path.split(".")

    @property
    def depth(self) -> int:
        """Number of levels, matching ltree's nlevel()."""
        return len(self.labels)


class ScopeMembership(BaseModel):
    """Assignment of an actor (user or service) to a scope with a role.

    Roles control what an actor may do in this scope:
      admin       → create scopes, manage members, delete blocks
      architect   → create and version blocks
      reviewer    → approve/reject merge requests
      contributor → propose blocks (via MR)
      consumer    → read only (API calls and context composition)
    """

    id: UUID
    scope_id: UUID
    actor_id: UUID
    role: ActorRole
    granted_at: datetime
    granted_by: UUID | None = None


class ScopeCreate(BaseModel):
    """What a caller may say when creating a scope.

    Deliberately no `scope_path`. The ltree path is derived from `parent_id`
    plus the new node's own id (ADR-008, CHY-59) — a client-supplied path can
    dismantle the hierarchy with a typo, and a wrong path does not fail loudly:
    the lookup simply returns nothing, which reads as "no context here".
    """

    name: str
    node_type: ScopeNodeType
    description: str | None = None
    parent_id: UUID | None = None
    privacy_level: PrivacyLevel | None = Field(
        default=None,
        description="The class of data this scope holds. Omit it to inherit: the "
                    "effective class is the strictest on the ancestor chain, and "
                    "critical when nobody declared one. May not be looser than "
                    "the parent's effective class (CHY-160).",
    )

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        if not v.strip():
            msg = "name must not be blank"
            raise ValueError(msg)
        return v.strip()


class ScopeMemberCreate(BaseModel):
    """Grant an actor a role inside one scope.

    The role vocabulary is the one from domain/roles.py, and the CHECK on
    scope_memberships.role lists the same five values (INFRA-92). Since CHY-92 a
    membership grants something: the runs of the scope's workflows and of every
    scope below it become visible to the member (migration 035). The role
    inside the scope is recorded; what a person may *do* still follows their
    global role.
    """

    actor_id: UUID
    role: ActorRole


class MembershipOut(BaseModel):
    """One scope the caller belongs to, and the role recorded there."""

    scope_id: UUID
    role: ActorRole


class MeOut(BaseModel):
    """Who the caller is to Cohereny (CHY-92).

    `actor_id` is what an admin needs to add someone to a scope, and the API
    named it nowhere before: the identity provider knows a person by their
    subject, Cohereny by this id.
    """

    actor_id: UUID
    role: ActorRole
    is_service_account: bool
    memberships: list[MembershipOut]


# ── review areas (CHY-180, ADR-050) ───────────────────────────────────────────

#: The name of a review area, as a gate names it in `review_areas`. The same
#: pattern as the CHECKs of migration 043, for the same reason as a document
#: name: a definition is written by people and read by a reviewer.
REVIEW_AREA_NAME = r"^[a-z][a-z0-9_]{0,62}$"


class ReviewAreaCreate(BaseModel):
    """An area that votes at a gate, defined on a scope and valid below it.

    Front office, back office, credit committee: the names are the
    organisation's. A gate names the areas whose vote it needs; each casts one.
    """

    name: str = Field(pattern=REVIEW_AREA_NAME, examples=["marktfolge"])


class ReviewAreaMemberCreate(BaseModel):
    """Add an actor to a review area. The id comes from `GET /v1/me`."""

    actor_id: UUID


class ReviewAreaMember(BaseModel):
    """One person in a review area. A revoked membership keeps its row and its
    date: a vote cast under it stays provable, and no new vote is accepted."""

    actor_id: UUID
    granted_at: datetime
    granted_by: UUID | None = None
    revoked_at: datetime | None = None
    revoked_by: UUID | None = None


class ReviewArea(BaseModel):
    """A review area of a scope, with its members, revoked ones included."""

    id: UUID
    scope_id: UUID
    name: str
    created_at: datetime
    created_by: UUID | None = None
    members: list[ReviewAreaMember] = []


# ── retention (CHY-17, ADR-046) ───────────────────────────────────────────────

#: The retention classes that take a period today. The CHECK on
#: retention_policies.retention_class lists the same two values;
#: tests/test_retention.py holds the two together.
ConfigurableRetentionClass = Literal["run_content", "run_trace"]

#: The longest finite period: a hundred years. Not PG_INT_MAX — a value that
#: fits the column still breaks the purge, whose `now() - make_interval(days =>
#: keep_days)` leaves the timestamp range long before int32 ends. Schemathesis
#: found the unbounded field on 05.11.2026 (1,204,274,781,580 days → 500). Longer
#: than a century is what `null` is for.
MAX_KEEP_DAYS = 36_500


class RetentionPolicySet(BaseModel):
    """Set how long a retention class is kept — installation-wide or for one scope.

    `keep_days` is required and may be null: null keeps without limit, which a
    credit file can need. Leaving it out is a malformed request, not a way to
    say "unlimited" by accident.
    """

    scope_id: UUID | None = None
    keep_days: int | None = Field(..., ge=1, le=MAX_KEEP_DAYS)


class RetentionPolicy(BaseModel):
    """One stored period. `scope_id` null is the installation-wide one."""

    retention_class: ConfigurableRetentionClass
    scope_id: UUID | None
    keep_days: int | None
    set_by: UUID | None
    set_at: datetime


class RetentionTable(BaseModel):
    """A table of a retention class, and the columns that are its content."""

    table: str
    owner: str
    content_columns: list[str]


class RetentionClassOut(BaseModel):
    """One retention class as an operator or a supervisor reads it."""

    retention_class: str
    description: str
    #: What a purge does to it: clear the content columns, delete the rows, or
    #: nothing — with `reason` saying why nothing.
    action: Literal["clear", "delete", "none"]
    configurable: bool
    reason: str | None
    tables: list[RetentionTable]
    #: Stored periods: the installation-wide one first, then scope overrides.
    policies: list[RetentionPolicy]
    #: For the scope asked about: the period that applies there, and where it
    #: was declared (null: installation-wide). Absent without a scope.
    effective_keep_days: int | None = None
    effective_from_scope: UUID | None = None


class ExternalStoreOut(BaseModel):
    """Data Cohereny writes outside its own tables, and who keeps it how long."""

    name: str
    retention_class: str
    handled_by: str


class RetentionOverview(BaseModel):
    """Every retention class, every table in it, every period that is set."""

    scope_id: UUID | None
    classes: list[RetentionClassOut]
    external_stores: list[ExternalStoreOut]
    #: The newest `retention.purged` entry: when the purge last ran and what it
    #: removed. Null until it ran once — the periods are then set, not applied.
    last_purge_at: datetime | None = None
    last_purge_counts: dict[str, int] | None = None
