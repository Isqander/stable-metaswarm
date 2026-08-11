from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Severity = Literal["low", "medium", "high", "critical"]
NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
TitleText = Annotated[str, StringConstraints(min_length=1, max_length=120)]
BodyText = Annotated[str, StringConstraints(min_length=1, max_length=4000)]
EvidenceText = Annotated[str, StringConstraints(max_length=2000)]
Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]


def _array_to_tuple(value: object) -> object:
    if isinstance(value, list):
        return tuple(value)
    return value


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class SchemaContractModel(ContractModel):
    @property
    def schema(self) -> str:
        return cast(str, self.__dict__["schema_value"])


class ObservationFields(ContractModel):
    title: TitleText
    body: BodyText
    file: NonEmptyText | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    evidence: EvidenceText | None = None

    @model_validator(mode="after")
    def validate_location_and_title(self) -> ObservationFields:
        if "\n" in self.title or "\r" in self.title:
            raise ValueError("title must be one line")
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be provided together")
        if self.line_start is not None and self.line_end is not None:
            if self.file is None:
                raise ValueError("line range requires file")
            if self.line_end < self.line_start:
                raise ValueError("line_end must not precede line_start")
        return self


class BlindObservation(ObservationFields):
    severity_suggested: Severity


class FollowupObservation(ObservationFields):
    severity_suggested: Severity | None = None
    unchanged_from: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_severity_source(self) -> FollowupObservation:
        if (self.severity_suggested is None) == (self.unchanged_from is None):
            raise ValueError("exactly one severity source is required")
        return self


BlindObservations = Annotated[
    tuple[BlindObservation, ...],
    BeforeValidator(_array_to_tuple),
]


class ReviewObservations(SchemaContractModel):
    schema_value: Literal["review.observations.v1"] = Field(
        alias="schema", serialization_alias="schema"
    )
    observations: BlindObservations


class ReconciliationGroup(ContractModel):
    observation_ids: Annotated[tuple[NonEmptyText, ...], BeforeValidator(_array_to_tuple)]
    outcome: Literal["new", "existing_open", "reaffirmed_closed", "reopen_closed"]
    title: TitleText | None = None
    finding_id: NonEmptyText | None = None
    reason: NonEmptyText | None = None

    @model_validator(mode="after")
    def validate_nonempty_observations(self) -> ReconciliationGroup:
        if not self.observation_ids:
            raise ValueError("reconciliation group must contain observations")
        return self


class ReviewReconciliation(SchemaContractModel):
    schema_value: Literal["review.reconciliation.v1"] = Field(
        alias="schema", serialization_alias="schema"
    )
    groups: Annotated[tuple[ReconciliationGroup, ...], BeforeValidator(_array_to_tuple)]


class FindingDisposition(ContractModel):
    finding_id: NonEmptyText
    disposition: Literal["fixed", "rejected", "wont_fix"]
    reason: NonEmptyText | None = None


class ReviewDispositions(SchemaContractModel):
    schema_value: Literal["review.dispositions.v1"] = Field(
        alias="schema", serialization_alias="schema"
    )
    dispositions: Annotated[tuple[FindingDisposition, ...], BeforeValidator(_array_to_tuple)]


class FindingDecision(ContractModel):
    finding_id: NonEmptyText
    decision: Literal["verified_fixed", "still_present", "accepted_reason", "insists"]
    note: NonEmptyText | None = None
    observation: FollowupObservation | None = None


class ReviewDecisions(SchemaContractModel):
    schema_value: Literal["review.decisions.v1"] = Field(
        alias="schema", serialization_alias="schema"
    )
    decisions: Annotated[tuple[FindingDecision, ...], BeforeValidator(_array_to_tuple)]
    new_observations: BlindObservations


class VerificationStep(ContractModel):
    cwd: NonEmptyText
    argv: Annotated[tuple[NonEmptyText, ...], BeforeValidator(_array_to_tuple)]
    expect: Literal["exit_zero"]
    step_timeout_s: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_argv(self) -> VerificationStep:
        if not self.argv:
            raise ValueError("argv must not be empty")
        return self


class VerificationPlan(SchemaContractModel):
    schema_value: Literal["verification.plan.v1"] = Field(
        alias="schema", serialization_alias="schema"
    )
    steps: Annotated[tuple[VerificationStep, ...], BeforeValidator(_array_to_tuple)]
    rationale: NonEmptyText

    @model_validator(mode="after")
    def validate_plan(self) -> VerificationPlan:
        if not self.steps:
            raise ValueError("verification plan must contain a step")
        if not self.rationale.strip():
            raise ValueError("rationale must not be blank")
        return self


class GraphTask(ContractModel):
    id: NonEmptyText
    title: NonEmptyText
    body: NonEmptyText


class GraphDependency(ContractModel):
    parent: NonEmptyText
    child: NonEmptyText


class GraphBreakdown(SchemaContractModel):
    schema_value: Literal["graph.breakdown.v1"] = Field(
        alias="schema", serialization_alias="schema"
    )
    tasks: Annotated[tuple[GraphTask, ...], BeforeValidator(_array_to_tuple)]
    dependencies: Annotated[tuple[GraphDependency, ...], BeforeValidator(_array_to_tuple)]


class TouchedFile(ContractModel):
    path: NonEmptyText
    purpose: NonEmptyText


class ImplementationGitScope(ContractModel):
    planning_baseline_sha: Sha
    implementation_parent_sha: Sha


class HandoffCutoff(SchemaContractModel):
    schema_value: Literal["handoff.cutoff.v1"] = Field(alias="schema", serialization_alias="schema")
    task: NonEmptyText
    current_state: NonEmptyText
    next_action: NonEmptyText
    touched_files: Annotated[tuple[TouchedFile, ...], BeforeValidator(_array_to_tuple)]
    open_questions: Annotated[tuple[str, ...], BeforeValidator(_array_to_tuple)]
    acceptance_criteria: Annotated[tuple[NonEmptyText, ...], BeforeValidator(_array_to_tuple)]
    referenced_files: Annotated[tuple[NonEmptyText, ...], BeforeValidator(_array_to_tuple)]
    referenced_shas: Annotated[tuple[Sha, ...], BeforeValidator(_array_to_tuple)]
    implementation_scope: ImplementationGitScope | None

    @model_validator(mode="after")
    def validate_required_sections(self) -> HandoffCutoff:
        if not self.task.strip() or not self.current_state.strip() or not self.next_action.strip():
            raise ValueError("cut-off state fields must not be blank")
        if not self.touched_files:
            raise ValueError("touched_files must not be empty")
        if not self.acceptance_criteria:
            raise ValueError("acceptance_criteria must not be empty")
        return self


class HumanOption(ContractModel):
    key: NonEmptyText
    label: NonEmptyText
    confidence: float = Field(ge=0.0, le=1.0)


class HumanQuestion(SchemaContractModel):
    schema_value: Literal["human.question.v1"] = Field(alias="schema", serialization_alias="schema")
    question_text: NonEmptyText
    context: NonEmptyText
    options: Annotated[tuple[HumanOption, ...], BeforeValidator(_array_to_tuple)]

    @model_validator(mode="after")
    def validate_text(self) -> HumanQuestion:
        if not self.question_text.strip():
            raise ValueError("question_text must not be blank")
        return self


AgentPayload = (
    ReviewObservations
    | ReviewReconciliation
    | ReviewDispositions
    | ReviewDecisions
    | VerificationPlan
    | GraphBreakdown
    | HandoffCutoff
    | HumanQuestion
)

AGENT_MODEL_TYPES = (
    ReviewObservations,
    ReviewReconciliation,
    ReviewDispositions,
    ReviewDecisions,
    VerificationPlan,
    GraphBreakdown,
    HandoffCutoff,
    HumanQuestion,
)
