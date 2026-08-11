from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type ReconciliationContext = Literal["discovery", "fix_check_new"]
type ReconciliationOutcome = Literal[
    "new",
    "existing_open",
    "reaffirmed_closed",
    "reopen_closed",
]
type ObservationLinkType = Literal[
    "first_seen",
    "recurrence",
    "reaffirmation",
    "reopening",
]
type HumanReopenChoice = Literal["reopen", "keep_closed"]
type PrimaryRoundOutcome = Literal["clean", "needs_revision"]


class ReconciliationInputError(ValueError):
    """The immutable reconciliation snapshot is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ReconciliationIssue:
    code: str
    group_index: int | None
    observation_ids: tuple[str, ...]
    finding_id: str | None
    message: str


class ReconciliationContractError(ValueError):
    """A proposal violates the semantic reconciliation contract."""

    def __init__(self, issues: tuple[ReconciliationIssue, ...]) -> None:
        if not issues:
            raise ValueError("reconciliation contract error requires at least one issue")
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


class HumanReopenResolutionError(ValueError):
    """Human answers do not cover the pending reopen requests exactly once."""


@dataclass(frozen=True, slots=True)
class RoundObservation:
    id: int
    public_id: str
    seq: int
    round_id: int
    lane_id: int
    lane_index: int
    severity: str
    title: str
    body: str
    file_path: str | None
    line_start: int | None
    line_end: int | None
    evidence: str | None


@dataclass(frozen=True, slots=True)
class OpenFindingRef:
    id: int
    public_id: str
    title: str
    last_resolution: str | None = None
    resolution_authority: str | None = None


@dataclass(frozen=True, slots=True)
class ClosedFindingRef:
    id: int
    public_id: str
    title: str
    last_resolution: str
    resolution_authority: str
    human_answer_id: int | None = None
    question_reason: str | None = None
    closing_snapshot: str | None = None
    escalation_severity: str | None = None


@dataclass(frozen=True, slots=True)
class ReconciliationInput:
    context: ReconciliationContext
    current_round_id: int
    observations: tuple[RoundObservation, ...]
    open_findings: tuple[OpenFindingRef, ...]
    closed_findings: tuple[ClosedFindingRef, ...]
    current_round_finding_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class ProposedGroup:
    observation_ids: tuple[str, ...]
    outcome: ReconciliationOutcome
    finding_id: str | None = None
    title: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcilerRequirements:
    role: Literal["reconciler"] = "reconciler"
    fresh_session: bool = True
    context_policy: Literal["explicit"] = "explicit"
    record_exposure: bool = True
    include_unclassified_observations: bool = True
    include_ledger: bool = True
    include_direct_followups: bool = False


RECONCILER_REQUIREMENTS = ReconcilerRequirements()


@dataclass(frozen=True, slots=True)
class FindingTarget:
    finding_id: int | None = None
    new_finding_index: int | None = None

    def __post_init__(self) -> None:
        if (self.finding_id is None) == (self.new_finding_index is None):
            raise ValueError("finding target requires exactly one target kind")


@dataclass(frozen=True, slots=True)
class NewFindingIntent:
    index: int
    observation_ids: tuple[int, ...]
    observation_public_ids: tuple[str, ...]
    title: str
    first_observation_id: int
    first_observation_public_id: str
    owner_lane_id: int
    owner_lane_index: int


@dataclass(frozen=True, slots=True)
class ObservationLinkIntent:
    observation_id: int
    observation_public_id: str
    target: FindingTarget
    link_type: ObservationLinkType
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FindingRoundIntent:
    target: FindingTarget
    owner_lane_id: int
    owner_lane_index: int
    entry_kind: Literal["post_check"] = "post_check"


@dataclass(frozen=True, slots=True)
class ReadyReconciliation:
    context: ReconciliationContext
    current_round_id: int
    new_findings: tuple[NewFindingIntent, ...]
    links: tuple[ObservationLinkIntent, ...]
    finding_rounds: tuple[FindingRoundIntent, ...]


@dataclass(frozen=True, slots=True)
class HumanReopenItem:
    observation_id: int
    observation_public_id: str
    reason: str
    lane_id: int
    lane_index: int


@dataclass(frozen=True, slots=True)
class HumanReopenRequest:
    finding_id: int
    finding_public_id: str
    items: tuple[HumanReopenItem, ...]
    original_human_answer_id: int
    original_question_reason: str
    closing_snapshot: str
    escalation_severity: str


@dataclass(frozen=True, slots=True)
class AwaitHumanReopen:
    context: ReconciliationContext
    current_round_id: int
    current_round_finding_ids: frozenset[int]
    observation_order: tuple[str, ...]
    new_findings: tuple[NewFindingIntent, ...]
    links: tuple[ObservationLinkIntent, ...]
    finding_rounds: tuple[FindingRoundIntent, ...]
    requests: tuple[HumanReopenRequest, ...]


@dataclass(frozen=True, slots=True)
class HumanReopenAnswer:
    finding_id: int
    choice: HumanReopenChoice


@dataclass(frozen=True, slots=True)
class AuthorWorkTarget:
    target: FindingTarget
    first_round_id: int | None


@dataclass(frozen=True, slots=True)
class FixCheckContribution:
    author_work: tuple[AuthorWorkTarget, ...]


@dataclass(frozen=True, slots=True)
class ReconcileFailedQuestion:
    reason: Literal["reconcile_failed"]
    context: ReconciliationContext
    current_round_id: int
    observations: tuple[RoundObservation, ...]
    open_findings: tuple[OpenFindingRef, ...]
    closed_findings: tuple[ClosedFindingRef, ...]
    validation_issues: tuple[ReconciliationIssue, ...]


__all__ = (
    "AuthorWorkTarget",
    "AwaitHumanReopen",
    "ClosedFindingRef",
    "FindingRoundIntent",
    "FindingTarget",
    "FixCheckContribution",
    "HumanReopenAnswer",
    "HumanReopenChoice",
    "HumanReopenItem",
    "HumanReopenRequest",
    "HumanReopenResolutionError",
    "NewFindingIntent",
    "ObservationLinkIntent",
    "ObservationLinkType",
    "OpenFindingRef",
    "PrimaryRoundOutcome",
    "ProposedGroup",
    "RECONCILER_REQUIREMENTS",
    "ReadyReconciliation",
    "ReconcileFailedQuestion",
    "ReconcilerRequirements",
    "ReconciliationContext",
    "ReconciliationContractError",
    "ReconciliationInput",
    "ReconciliationInputError",
    "ReconciliationIssue",
    "ReconciliationOutcome",
    "RoundObservation",
)
