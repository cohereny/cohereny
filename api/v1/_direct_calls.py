"""
api/v1/_direct_calls.py

Whether /v1/complete answers in a scope, and the sentence when it does not
(CHY-199, ADR-050).

/v1/complete returns the model's answer to the caller as it is: no gate stands
between the model and whoever uses the answer. ADR-050 allows that for one
level only, `sample`, where a share of the outputs is checked afterwards. So a
direct call is allowed where the minimum review is `sample`, and the minimum is
what it is for a workflow: the stricter of what the scope's data class and the
risk class ask (contexts/orchestration/votes.py). The risk class of direct calls
is set per scope (decided by Thomas, 10.11.2026); none set counts as `high`.

`sample` means a share is checked afterwards, for direct calls as for a
workflow's gate (CHY-269): the share is set with the classification, and a
drawn call goes into the review queue. Where no share is set, /v1/complete
refuses — a missing share may not read as "no check".

One module for the rule, used by the endpoint that refuses and the one that
reports, so the two cannot disagree about a scope.

Called by:
  - api/v1/complete.py   (refuses a direct call)
  - api/v1/scopes.py     (reports and sets the classification)
"""
from __future__ import annotations

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from contexts.orchestration import votes
from kernel.schema import SMALLEST_SHARE, PrivacyLevel, RiskClass, share_as_stored
from kernel.scopes import DirectCallRisk

#: The one class that lets a direct call through, and so the one with a share.
MINIMAL = "minimal"


class DirectCallRiskClassSet(BaseModel):
    """How risky direct model calls in this scope are."""

    risk_class: RiskClass | None = Field(
        ...,
        description=(
            "minimal, limited or high, as a workflow's risk class. null takes this "
            "scope's own setting back, so it inherits from the scopes above. A "
            "class looser than theirs is refused."
        ),
    )
    sample_rate: float | None = Field(
        default=None, ge=SMALLEST_SHARE, le=1,
        description=(
            "With minimal, and required there: the share of direct calls drawn into "
            "the review queue and judged afterwards (CHY-269), as review_sample_rate "
            "at a sample gate. From 0.001 to 1, at most three decimal places. Refused "
            "with any other class. A share below the one a scope above draws is refused."
        ),
    )

    @field_validator("sample_rate")
    @classmethod
    def _as_stored(cls, rate: float | None) -> float | None:
        """As the column keeps it (kernel.schema.share_as_stored); found by the
        contract fuzzer, 10.11.2026, when a share rounded to 0 was a 500."""
        return share_as_stored(rate)

    @model_validator(mode="after")
    def _share_goes_with_minimal(self) -> Self:
        """`minimal` is the class that lets a direct call through unchecked, so
        it is the one that needs a share; any other class lets none through,
        and a share there would promise a check that never happens."""
        if self.risk_class == MINIMAL and self.sample_rate is None:
            msg = "minimal needs a sample_rate: the share of direct calls judged afterwards"
            raise ValueError(msg)
        if self.risk_class != MINIMAL and self.sample_rate is not None:
            msg = "sample_rate goes with minimal only; no other class lets a direct call through"
            raise ValueError(msg)
        return self


class DirectCallClassification(BaseModel):
    """Whether /v1/complete answers in a scope, and what decides it."""

    scope_id: UUID
    risk_class: RiskClass | None = Field(
        description="This scope's own setting; null when it inherits.")
    effective_risk_class: RiskClass | None = Field(
        description="The strictest along the scope's chain; null when nobody "
                    "classified direct calls, which counts as high.")
    data_class: PrivacyLevel = Field(
        description="The scope's effective data class (ADR-041).")
    review_level: Literal["sample", "approver", "four_eyes"] = Field(
        description="The least review an answer here needs: the stricter of what "
                    "the data class and the risk class ask (ADR-050).")
    sample_rate: float | None = Field(
        description="This scope's own share of direct calls drawn for review; null "
                    "when it inherits.")
    effective_sample_rate: float | None = Field(
        description="The highest share along the chain; null when no scope set one, "
                    "and then /v1/complete does not answer (CHY-269).")
    direct_calls: bool = Field(
        description="True only where review_level is sample and a share is set. "
                    "/v1/complete has no gate, so it answers only there, and a "
                    "share of its answers is judged afterwards.")


def floor(risk: DirectCallRisk) -> votes.Minimum:
    """The minimum review for a direct call in this scope."""
    return votes.minimum(risk.data_class, risk.effective)


def classification(risk: DirectCallRisk) -> DirectCallClassification:
    level = floor(risk).level
    return DirectCallClassification(
        scope_id=risk.scope_id,
        risk_class=risk.own,
        effective_risk_class=risk.effective,
        data_class=risk.data_class,
        review_level=level,
        sample_rate=_number(risk.own_sample_rate),
        effective_sample_rate=_number(risk.sample_rate),
        direct_calls=refusal(risk) is None,
    )


def _number(rate) -> float | None:
    return None if rate is None else float(rate)


def refusal(risk: DirectCallRisk) -> str | None:
    """The sentence /v1/complete answers with, or None where it may answer.

    It names everything that asks for more than `sample` — the data class, the
    risk class, or both — and not only what sets the level: the way out
    differs. A risk class can be set by an architect; a data class describes
    the data and is not a switch. So classifying is offered only where the data
    class alone would allow a direct call, or the advice would lead nowhere.
    """
    minimum = floor(risk)
    if minimum.level == votes.SAMPLE:
        if risk.sample_rate is not None:
            return None
        # Only a classification from before migration 048 gets here: since then
        # minimal comes with a share. Refused, not answered unchecked (CHY-269).
        return (
            f"Direct calls in scope {risk.scope_id} are classified minimal, but no "
            f"share of them is drawn for review: set one with the classification "
            f"(PUT /v1/scopes/{risk.scope_id}/direct-call-risk-class, sample_rate). "
            f"At sample review a share of the answers is judged afterwards, and "
            f"/v1/complete answers only where it is (ADR-050)."
        )
    by_data = votes.BY_DATA_CLASS.get(risk.data_class, votes.FOUR_EYES)
    by_risk = votes.BY_RISK_CLASS.get(risk.effective, votes.FOUR_EYES)
    reasons = []
    if by_data != votes.SAMPLE:
        reasons.append(f"its data class {risk.data_class} asks for {by_data}")
    if by_risk != votes.SAMPLE:
        reasons.append(
            f"direct calls here are classified {risk.effective}, which asks for {by_risk}"
            if risk.effective
            else f"nobody classified direct calls here, which counts as high and asks for {by_risk}"
        )
    way_out = "Run a workflow with a review gate instead"
    if by_data == votes.SAMPLE:
        way_out += (", or have an architect classify direct calls in this scope "
                    f"(PUT /v1/scopes/{risk.scope_id}/direct-call-risk-class)")
    return (
        f"An answer in scope {risk.scope_id} needs {minimum.level} review before it "
        f"is used: {' and '.join(reasons)} (ADR-050). /v1/complete returns the answer "
        f"unchecked, so it answers only where sample review is enough. {way_out}."
    )
