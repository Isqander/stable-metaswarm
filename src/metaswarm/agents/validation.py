from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import cast

from pydantic import ValidationError

from .contract_models import (
    AgentPayload,
    BlindObservation,
    FindingDecision,
    GraphBreakdown,
    HandoffCutoff,
    HumanQuestion,
    ReviewDecisions,
    ReviewDispositions,
    ReviewObservations,
    ReviewReconciliation,
    VerificationPlan,
)
from .parse import PayloadParseError, canonical_json_bytes, extract_marked_payload

MAX_RETRY_ISSUES = 50
MAX_RETRY_FEEDBACK_BYTES = 16_384
_CLOSED_RESOLUTIONS = frozenset(
    {"verified_fixed", "accepted_reason", "policy_closed", "human_decision"}
)
_DISPOSITIONS = frozenset({"fixed", "rejected", "wont_fix"})


class AgentSchema(StrEnum):
    REVIEW_OBSERVATIONS = "review.observations.v1"
    REVIEW_RECONCILIATION = "review.reconciliation.v1"
    REVIEW_DISPOSITIONS = "review.dispositions.v1"
    REVIEW_DECISIONS = "review.decisions.v1"
    VERIFICATION_PLAN = "verification.plan.v1"
    GRAPH_BREAKDOWN = "graph.breakdown.v1"
    HANDOFF_CUTOFF = "handoff.cutoff.v1"
    HUMAN_QUESTION = "human.question.v1"


type IssuePath = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class ContractIssue:
    code: str
    path: IssuePath
    message: str


class AgentContractError(ValueError):
    def __init__(self, issues: Iterable[ContractIssue]) -> None:
        ordered = tuple(sorted(issues, key=_issue_sort_key))
        if not ordered:
            raise ValueError("AgentContractError requires at least one issue")
        self.issues = ordered
        super().__init__("; ".join(item.code for item in ordered))


class ValidationContextError(ValueError):
    pass


def _freeze_strings(values: Iterable[str], name: str) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValidationContextError(f"{name} must be a collection of strings")
    try:
        frozen = frozenset(values)
    except TypeError as error:
        raise ValidationContextError(f"{name} must be a collection of strings") from error
    if any(not isinstance(value, str) or not value for value in frozen):
        raise ValidationContextError(f"{name} must contain non-empty strings")
    return frozen


def _freeze_pairs(
    values: Iterable[tuple[str, str]],
    name: str,
) -> tuple[tuple[str, str], ...]:
    try:
        frozen = tuple(values)
    except TypeError as error:
        raise ValidationContextError(f"{name} must contain key/value pairs") from error
    if any(not isinstance(item, tuple) or len(item) != 2 for item in frozen):
        raise ValidationContextError(f"{name} must contain key/value pairs")
    if any(
        not isinstance(item[0], str) or not isinstance(item[1], str) or not item[0] or not item[1]
        for item in frozen
    ):
        raise ValidationContextError(f"{name} must contain non-empty strings")
    keys = tuple(item[0] for item in frozen)
    if len(keys) != len(set(keys)):
        raise ValidationContextError(f"{name} must contain unique key/value pairs")
    return frozen


@dataclass(frozen=True, slots=True)
class ObservationsContext:
    allowed_files: frozenset[str]
    max_observations: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "allowed_files", _freeze_strings(self.allowed_files, "allowed_files")
        )
        if type(self.max_observations) is not int or self.max_observations <= 0:
            raise ValidationContextError("max_observations must be a positive int")


@dataclass(frozen=True, slots=True)
class ReconciliationValidationContext:
    exposed_observation_ids: frozenset[str]
    open_finding_ids: frozenset[str]
    closed_finding_resolutions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "exposed_observation_ids",
            _freeze_strings(self.exposed_observation_ids, "exposed_observation_ids"),
        )
        object.__setattr__(
            self,
            "open_finding_ids",
            _freeze_strings(self.open_finding_ids, "open_finding_ids"),
        )
        object.__setattr__(
            self,
            "closed_finding_resolutions",
            _freeze_pairs(self.closed_finding_resolutions, "closed_finding_resolutions"),
        )
        closed_ids = {key for key, _value in self.closed_finding_resolutions}
        if any(
            resolution not in _CLOSED_RESOLUTIONS
            for _finding_id, resolution in self.closed_finding_resolutions
        ):
            raise ValidationContextError("closed findings contain an unknown resolution")
        if self.open_finding_ids.intersection(closed_ids):
            raise ValidationContextError("open and closed finding IDs must not overlap")


@dataclass(frozen=True, slots=True)
class DispositionsContext:
    expected_open_finding_ids: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_open_finding_ids",
            _freeze_strings(self.expected_open_finding_ids, "expected_open_finding_ids"),
        )


@dataclass(frozen=True, slots=True)
class DecisionsContext:
    expected_owner_finding_ids: frozenset[str]
    dispositions: tuple[tuple[str, str], ...]
    shown_observations: tuple[tuple[str, str], ...]
    allowed_files: frozenset[str]
    max_new_observations: int = 100

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_owner_finding_ids",
            _freeze_strings(self.expected_owner_finding_ids, "expected_owner_finding_ids"),
        )
        object.__setattr__(self, "dispositions", _freeze_pairs(self.dispositions, "dispositions"))
        object.__setattr__(
            self,
            "shown_observations",
            _freeze_pairs(self.shown_observations, "shown_observations"),
        )
        object.__setattr__(
            self, "allowed_files", _freeze_strings(self.allowed_files, "allowed_files")
        )
        if type(self.max_new_observations) is not int or self.max_new_observations <= 0:
            raise ValidationContextError("max_new_observations must be a positive int")
        if set(key for key, _value in self.dispositions) != set(self.expected_owner_finding_ids):
            raise ValidationContextError("dispositions must cover exactly the owner's findings")
        if any(disposition not in _DISPOSITIONS for _finding_id, disposition in self.dispositions):
            raise ValidationContextError("dispositions contain an unknown value")
        if set(key for key, _value in self.shown_observations) != set(
            self.expected_owner_finding_ids
        ):
            raise ValidationContextError(
                "shown_observations must cover exactly the owner's findings"
            )


@dataclass(frozen=True, slots=True)
class VerificationContext:
    allowed_cwd_roots: frozenset[str]
    allowed_executables: frozenset[str]
    denied_paths: frozenset[str]
    production_targets: frozenset[str]

    def __post_init__(self) -> None:
        for field_name in (
            "allowed_cwd_roots",
            "allowed_executables",
            "denied_paths",
            "production_targets",
        ):
            object.__setattr__(
                self, field_name, _freeze_strings(getattr(self, field_name), field_name)
            )


@dataclass(frozen=True, slots=True)
class GraphContext:
    pass


@dataclass(frozen=True, slots=True)
class CutoffContext:
    available_files: frozenset[str]
    available_shas: frozenset[str]
    planning_baseline_sha: str | None = None
    implementation_parent_sha: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "available_files",
            _freeze_strings(self.available_files, "available_files"),
        )
        object.__setattr__(
            self,
            "available_shas",
            _freeze_strings(self.available_shas, "available_shas"),
        )
        if (self.planning_baseline_sha is None) != (self.implementation_parent_sha is None):
            raise ValidationContextError("implementation SHA roles must be both set or both absent")
        for value in (self.planning_baseline_sha, self.implementation_parent_sha):
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 40
                or any(ch not in "0123456789abcdef" for ch in value)
            ):
                raise ValidationContextError(
                    "implementation SHA roles must be full lowercase SHA-1"
                )
            if value is not None and value not in self.available_shas:
                raise ValidationContextError(
                    "implementation SHA roles must exist in available_shas"
                )


@dataclass(frozen=True, slots=True)
class HumanQuestionContext:
    reason: str

    def __post_init__(self) -> None:
        if self.reason != "open_question":
            raise ValidationContextError("human.question.v1 is only valid for open_question")


type ValidationContext = (
    ObservationsContext
    | ReconciliationValidationContext
    | DispositionsContext
    | DecisionsContext
    | VerificationContext
    | GraphContext
    | CutoffContext
    | HumanQuestionContext
)


@dataclass(frozen=True, slots=True)
class VerificationPolicyRejection:
    code: str
    step_index: int
    detail: str


@dataclass(frozen=True, slots=True)
class ValidatedCutoff:
    payload: HandoffCutoff
    canonical_bytes: bytes
    sha256: str


type ValidatedPayload = AgentPayload | ValidatedCutoff


@dataclass(frozen=True, slots=True)
class ValidatedAgentResult:
    schema: AgentSchema
    payload: ValidatedPayload
    policy_rejections: tuple[VerificationPolicyRejection, ...] = ()


_MODEL_BY_SCHEMA = {
    AgentSchema.REVIEW_OBSERVATIONS: ReviewObservations,
    AgentSchema.REVIEW_RECONCILIATION: ReviewReconciliation,
    AgentSchema.REVIEW_DISPOSITIONS: ReviewDispositions,
    AgentSchema.REVIEW_DECISIONS: ReviewDecisions,
    AgentSchema.VERIFICATION_PLAN: VerificationPlan,
    AgentSchema.GRAPH_BREAKDOWN: GraphBreakdown,
    AgentSchema.HANDOFF_CUTOFF: HandoffCutoff,
    AgentSchema.HUMAN_QUESTION: HumanQuestion,
}

_CONTEXT_BY_SCHEMA = {
    AgentSchema.REVIEW_OBSERVATIONS: ObservationsContext,
    AgentSchema.REVIEW_RECONCILIATION: ReconciliationValidationContext,
    AgentSchema.REVIEW_DISPOSITIONS: DispositionsContext,
    AgentSchema.REVIEW_DECISIONS: DecisionsContext,
    AgentSchema.VERIFICATION_PLAN: VerificationContext,
    AgentSchema.GRAPH_BREAKDOWN: GraphContext,
    AgentSchema.HANDOFF_CUTOFF: CutoffContext,
    AgentSchema.HUMAN_QUESTION: HumanQuestionContext,
}


def schema_registry() -> tuple[AgentSchema, ...]:
    return tuple(AgentSchema)


def _path_sort_key(path: IssuePath) -> tuple[tuple[int, str], ...]:
    return tuple((0, f"{item:020d}") if isinstance(item, int) else (1, item) for item in path)


def _issue_sort_key(issue: ContractIssue) -> tuple[tuple[tuple[int, str], ...], str]:
    return (_path_sort_key(issue.path), issue.code)


def _extra_segment(value: object) -> str:
    raw = str(value).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return f"extra@sha256:{digest}:bytes={len(raw)}"


_PYDANTIC_CODES = {
    "extra_forbidden": ("extra_field", "unknown fields are not allowed"),
    "missing": ("missing_field", "required field is missing"),
    "literal_error": ("invalid_literal", "field has an unsupported value"),
    "string_type": ("invalid_type", "field has an invalid type"),
    "int_type": ("invalid_type", "field has an invalid type"),
    "float_type": ("invalid_type", "field has an invalid type"),
    "bool_type": ("invalid_type", "field has an invalid type"),
    "tuple_type": ("invalid_type", "field must be a JSON array"),
    "model_type": ("invalid_type", "field has an invalid object type"),
    "string_too_long": ("invalid_length", "field exceeds its length limit"),
    "string_too_short": ("invalid_length", "field must not be empty"),
    "greater_than": ("invalid_value", "numeric field is outside its allowed range"),
    "greater_than_equal": ("invalid_value", "numeric field is outside its allowed range"),
    "less_than_equal": ("invalid_value", "numeric field is outside its allowed range"),
    "string_pattern_mismatch": ("invalid_value", "field has an invalid format"),
    "value_error": ("invalid_value", "fields form an invalid combination"),
}


def _pydantic_issues(error: ValidationError) -> tuple[ContractIssue, ...]:
    issues: list[ContractIssue] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        error_type = cast(str, item["type"])
        raw_location = tuple(cast(tuple[str | int, ...], item.get("loc", ())))
        if error_type == "extra_forbidden" and raw_location:
            raw_location = (*raw_location[:-1], _extra_segment(raw_location[-1]))
        code, message = _PYDANTIC_CODES.get(
            error_type,
            ("invalid_model", "payload does not satisfy the contract model"),
        )
        issues.append(ContractIssue(code, raw_location, message))
    return tuple(issues)


def _issue(code: str, path: IssuePath, message: str) -> ContractIssue:
    return ContractIssue(code, path, message)


def _validate_observation_files(
    observations: tuple[BlindObservation, ...],
    allowed_files: frozenset[str],
    path: str,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for index, observation in enumerate(observations):
        if observation.file is not None and observation.file not in allowed_files:
            issues.append(
                _issue(
                    "unknown_file",
                    (path, index, "file"),
                    "file is absent from the revision snapshot",
                )
            )
    return issues


def _validate_observations(
    payload: ReviewObservations,
    context: ObservationsContext,
) -> list[ContractIssue]:
    issues = _validate_observation_files(
        payload.observations, context.allowed_files, "observations"
    )
    if len(payload.observations) > context.max_observations:
        issues.append(
            _issue("observation_limit", ("observations",), "observation limit is exceeded")
        )
    return issues


def _validate_reconciliation(
    payload: ReviewReconciliation,
    context: ReconciliationValidationContext,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    seen: set[str] = set()
    closed = dict(context.closed_finding_resolutions)
    for group_index, group in enumerate(payload.groups):
        for observation_index, observation_id in enumerate(group.observation_ids):
            item_path = ("groups", group_index, "observation_ids", observation_index)
            if observation_id not in context.exposed_observation_ids:
                issues.append(
                    _issue(
                        "unknown_observation",
                        item_path,
                        "observation is outside this reconciliation snapshot",
                    )
                )
            if observation_id in seen:
                issues.append(
                    _issue(
                        "duplicate_observation",
                        item_path,
                        "observation is classified more than once",
                    )
                )
            seen.add(observation_id)

        base = ("groups", group_index)
        if group.outcome == "new":
            if group.finding_id is not None:
                issues.append(
                    _issue(
                        "unexpected_finding",
                        (*base, "finding_id"),
                        "new outcome must not name a finding",
                    )
                )
            if group.title is None or not group.title.strip():
                issues.append(
                    _issue("missing_title", (*base, "title"), "new outcome requires a title")
                )
            if group.reason is not None:
                issues.append(
                    _issue(
                        "unexpected_reason",
                        (*base, "reason"),
                        "new outcome must not carry a reason",
                    )
                )
            continue
        if group.title is not None:
            issues.append(
                _issue(
                    "unexpected_title",
                    (*base, "title"),
                    "existing finding outcome must not carry a title",
                )
            )
        if group.finding_id is None:
            issues.append(
                _issue(
                    "missing_finding",
                    (*base, "finding_id"),
                    "existing finding outcome requires a finding ID",
                )
            )
            continue
        if group.outcome == "existing_open":
            if group.finding_id not in context.open_finding_ids:
                issues.append(
                    _issue(
                        "finding_scope",
                        (*base, "finding_id"),
                        "finding is not open in this snapshot",
                    )
                )
            if group.reason is not None:
                issues.append(
                    _issue(
                        "unexpected_reason",
                        (*base, "reason"),
                        "existing_open must not carry a reason",
                    )
                )
        elif group.outcome == "reaffirmed_closed":
            if closed.get(group.finding_id) != "accepted_reason":
                issues.append(
                    _issue(
                        "finding_scope",
                        (*base, "finding_id"),
                        "finding is not an accepted closed finding",
                    )
                )
            if group.reason is not None:
                issues.append(
                    _issue(
                        "unexpected_reason",
                        (*base, "reason"),
                        "reaffirmed_closed must not carry a reason",
                    )
                )
        else:
            if group.finding_id not in closed:
                issues.append(
                    _issue(
                        "finding_scope",
                        (*base, "finding_id"),
                        "finding is not closed in this snapshot",
                    )
                )
            if group.reason is None or not group.reason.strip():
                issues.append(
                    _issue("missing_reason", (*base, "reason"), "reopen_closed requires a reason")
                )

    missing = context.exposed_observation_ids.difference(seen)
    if missing:
        issues.append(
            _issue(
                "missing_observation", ("groups",), "not every exposed observation is classified"
            )
        )
    return issues


def _validate_dispositions(
    payload: ReviewDispositions,
    context: DispositionsContext,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    seen: set[str] = set()
    for index, item in enumerate(payload.dispositions):
        path = ("dispositions", index)
        if item.finding_id not in context.expected_open_finding_ids:
            issues.append(
                _issue("finding_scope", (*path, "finding_id"), "finding is outside the open set")
            )
        if item.finding_id in seen:
            issues.append(
                _issue(
                    "duplicate_finding",
                    (*path, "finding_id"),
                    "finding is dispositioned more than once",
                )
            )
        seen.add(item.finding_id)
        if item.disposition in {"rejected", "wont_fix"}:
            if item.reason is None or not item.reason.strip():
                issues.append(
                    _issue(
                        "missing_reason", (*path, "reason"), "this disposition requires a reason"
                    )
                )
        elif item.reason is not None:
            issues.append(
                _issue(
                    "unexpected_reason",
                    (*path, "reason"),
                    "fixed disposition must not carry a reason",
                )
            )
    if context.expected_open_finding_ids.difference(seen):
        issues.append(
            _issue("missing_finding", ("dispositions",), "not every open finding has a disposition")
        )
    return issues


def _decision_pair_is_valid(decision: FindingDecision, disposition: str) -> bool:
    if disposition == "fixed":
        return decision.decision in {"verified_fixed", "still_present"}
    return decision.decision in {"accepted_reason", "insists"}


def _validate_decisions(payload: ReviewDecisions, context: DecisionsContext) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    seen: set[str] = set()
    dispositions = dict(context.dispositions)
    shown = dict(context.shown_observations)
    for index, item in enumerate(payload.decisions):
        path = ("decisions", index)
        if item.finding_id not in context.expected_owner_finding_ids:
            issues.append(
                _issue("finding_owner", (*path, "finding_id"), "caller does not own this finding")
            )
        if item.finding_id in seen:
            issues.append(
                _issue(
                    "duplicate_finding", (*path, "finding_id"), "finding is decided more than once"
                )
            )
        seen.add(item.finding_id)
        disposition = dispositions.get(item.finding_id)
        if disposition is not None and not _decision_pair_is_valid(item, disposition):
            issues.append(
                _issue(
                    "decision_pair",
                    (*path, "decision"),
                    "decision is incompatible with the author disposition",
                )
            )
        if item.observation is not None:
            if item.decision not in {"still_present", "insists"}:
                issues.append(
                    _issue(
                        "closing_followup",
                        (*path, "observation"),
                        "closing decisions cannot carry a follow-up observation",
                    )
                )
            if item.observation.unchanged_from is not None:
                if item.observation.unchanged_from != shown.get(item.finding_id):
                    issues.append(
                        _issue(
                            "unknown_parent",
                            (*path, "observation", "unchanged_from"),
                            "unchanged_from is not the shown observation for this finding",
                        )
                    )
            if (
                item.observation.file is not None
                and item.observation.file not in context.allowed_files
            ):
                issues.append(
                    _issue(
                        "unknown_file",
                        (*path, "observation", "file"),
                        "file is absent from the revision snapshot",
                    )
                )
    if context.expected_owner_finding_ids.difference(seen):
        issues.append(
            _issue("missing_finding", ("decisions",), "not every owned finding has a decision")
        )
    issues.extend(
        _validate_observation_files(
            payload.new_observations, context.allowed_files, "new_observations"
        )
    )
    if len(payload.new_observations) > context.max_new_observations:
        issues.append(
            _issue("observation_limit", ("new_observations",), "new observation limit is exceeded")
        )
    return issues


_SHELL_TOKENS = ("&&", "||", "|", "$", ">", "<", ";", "`")


def _normal_relative_path(value: str) -> str | None:
    windows_path = PureWindowsPath(value)
    if windows_path.drive or windows_path.root:
        return None
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def _cwd_allowed(cwd: str, roots: frozenset[str]) -> bool:
    normalized = _normal_relative_path(cwd)
    if normalized is None:
        return False
    for raw_root in roots:
        root = _normal_relative_path(raw_root)
        if root is None:
            continue
        if root == "." or normalized == root or normalized.startswith(f"{root}/"):
            return True
    return False


def _verification_rejections(
    payload: VerificationPlan,
    context: VerificationContext,
) -> tuple[VerificationPolicyRejection, ...]:
    rejections: list[VerificationPolicyRejection] = []
    for index, step in enumerate(payload.steps):
        if not _cwd_allowed(step.cwd, context.allowed_cwd_roots):
            rejections.append(
                VerificationPolicyRejection("cwd_outside", index, "cwd is outside allowed roots")
            )
        if any(token in argument for argument in step.argv for token in _SHELL_TOKENS):
            rejections.append(
                VerificationPolicyRejection("shell_syntax", index, "argv contains shell syntax")
            )
        if step.argv[0] not in context.allowed_executables:
            rejections.append(
                VerificationPolicyRejection("executable", index, "executable is not allowed")
            )
        normalized_args = tuple(argument.replace("\\", "/") for argument in step.argv)
        if any(
            denied in argument for denied in context.denied_paths for argument in normalized_args
        ):
            rejections.append(
                VerificationPolicyRejection("path", index, "argv references a denied path")
            )
        if any(
            target in argument for target in context.production_targets for argument in step.argv
        ):
            rejections.append(
                VerificationPolicyRejection("target", index, "argv references a production target")
            )
    return tuple(rejections)


def _find_cycle(payload: GraphBreakdown) -> tuple[str, ...] | None:
    node_ids = {task.id for task in payload.tasks}
    adjacency = {node_id: [] for node_id in node_ids}
    for edge in payload.dependencies:
        if edge.parent in adjacency and edge.child in adjacency and edge.parent != edge.child:
            adjacency[edge.parent].append(edge.child)
    for values in adjacency.values():
        values.sort()
    state: dict[str, int] = {}
    for start_node in sorted(node_ids):
        if state.get(start_node, 0) != 0:
            continue
        path: list[str] = []
        path_index: dict[str, int] = {}
        stack: list[tuple[str, int]] = [(start_node, 0)]
        while stack:
            node, child_index = stack[-1]
            if state.get(node, 0) == 0:
                state[node] = 1
                path_index[node] = len(path)
                path.append(node)
            children = adjacency[node]
            if child_index == len(children):
                stack.pop()
                state[node] = 2
                path_index.pop(node)
                path.pop()
                continue
            child = children[child_index]
            stack[-1] = (node, child_index + 1)
            if state.get(child, 0) == 0:
                stack.append((child, 0))
            elif state.get(child) == 1:
                cycle_start = path_index[child]
                return tuple((*path[cycle_start:], child))
    return None


def _validate_graph(payload: GraphBreakdown) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    task_ids = tuple(item.id for item in payload.tasks)
    known = set(task_ids)
    if len(task_ids) != len(known):
        issues.append(_issue("duplicate_task", ("tasks",), "task IDs must be unique"))
    edges = tuple((item.parent, item.child) for item in payload.dependencies)
    if len(edges) != len(set(edges)):
        issues.append(
            _issue("duplicate_edge", ("dependencies",), "dependency edges must be unique")
        )
    for index, edge in enumerate(payload.dependencies):
        if edge.parent not in known or edge.child not in known:
            issues.append(
                _issue("dangling_edge", ("dependencies", index), "dependency endpoint is missing")
            )
        if edge.parent == edge.child:
            issues.append(
                _issue("self_edge", ("dependencies", index), "task cannot depend on itself")
            )
    cycle = _find_cycle(payload)
    if cycle is not None:
        task_indexes = {task.id: index for index, task in enumerate(payload.tasks)}
        cycle_path = " -> ".join(f"$.tasks[{task_indexes[node]}].id" for node in cycle)
        issues.append(_issue("cycle", ("dependencies",), f"dependency cycle: {cycle_path}"))
    return issues


def _validate_cutoff(payload: HandoffCutoff, context: CutoffContext) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for index, item in enumerate(payload.touched_files):
        if item.path not in context.available_files:
            issues.append(
                _issue(
                    "unknown_file",
                    ("touched_files", index, "path"),
                    "file is absent from the repository snapshot",
                )
            )
    for index, path in enumerate(payload.referenced_files):
        if path not in context.available_files:
            issues.append(
                _issue(
                    "unknown_file",
                    ("referenced_files", index),
                    "file is absent from the repository snapshot",
                )
            )
    for index, sha in enumerate(payload.referenced_shas):
        if sha not in context.available_shas:
            issues.append(
                _issue(
                    "unknown_sha",
                    ("referenced_shas", index),
                    "SHA is absent from the repository snapshot",
                )
            )

    expected_scope = (context.planning_baseline_sha, context.implementation_parent_sha)
    if expected_scope == (None, None):
        if payload.implementation_scope is not None:
            issues.append(
                _issue(
                    "unexpected_scope",
                    ("implementation_scope",),
                    "non-implementation cut-off requires null scope",
                )
            )
    elif payload.implementation_scope is None:
        issues.append(
            _issue(
                "missing_scope",
                ("implementation_scope",),
                "implementation cut-off requires named SHA roles",
            )
        )
    else:
        actual_scope = (
            payload.implementation_scope.planning_baseline_sha,
            payload.implementation_scope.implementation_parent_sha,
        )
        if actual_scope != expected_scope:
            issues.append(
                _issue(
                    "scope_mismatch",
                    ("implementation_scope",),
                    "implementation SHA roles do not match caller context",
                )
            )
        for field_name, sha in (
            ("planning_baseline_sha", payload.implementation_scope.planning_baseline_sha),
            ("implementation_parent_sha", payload.implementation_scope.implementation_parent_sha),
        ):
            if sha not in payload.referenced_shas:
                issues.append(
                    _issue(
                        "scope_reference",
                        ("implementation_scope", field_name),
                        "implementation SHA must be declared in referenced_shas",
                    )
                )
    return issues


def _validate_human_question(payload: HumanQuestion) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    keys: set[str] = set()
    for index, option in enumerate(payload.options):
        if not option.key.strip():
            issues.append(
                _issue(
                    "empty_option_key", ("options", index, "key"), "option key must not be blank"
                )
            )
        if option.key in keys:
            issues.append(
                _issue("duplicate_option", ("options", index, "key"), "option keys must be unique")
            )
        keys.add(option.key)
        if not option.label.strip():
            issues.append(
                _issue(
                    "empty_option_label",
                    ("options", index, "label"),
                    "option label must not be blank",
                )
            )
    return issues


def _context_for(schema: AgentSchema, context: ValidationContext) -> None:
    expected_type = _CONTEXT_BY_SCHEMA[schema]
    if type(context) is not expected_type:
        raise ValidationContextError(f"{schema.value} requires context {expected_type.__name__}")


def validate_agent_result(
    text: str,
    expected_schema: AgentSchema,
    context: ValidationContext,
) -> ValidatedAgentResult:
    if not isinstance(expected_schema, AgentSchema):
        raise ValidationContextError("expected_schema must be an AgentSchema")
    _context_for(expected_schema, context)
    try:
        raw = extract_marked_payload(text)
    except PayloadParseError as error:
        raise AgentContractError((ContractIssue(error.code, (), error.message),)) from error
    if raw.get("schema") != expected_schema.value:
        raise AgentContractError(
            (
                ContractIssue(
                    "schema_mismatch",
                    ("schema",),
                    "payload schema does not match the expected schema",
                ),
            )
        )
    model_type = _MODEL_BY_SCHEMA[expected_schema]
    try:
        payload = model_type.model_validate(raw)
    except ValidationError as error:
        raise AgentContractError(_pydantic_issues(error)) from error

    issues: list[ContractIssue] = []
    policy_rejections: tuple[VerificationPolicyRejection, ...] = ()
    validated_payload: ValidatedPayload = payload
    if expected_schema is AgentSchema.REVIEW_OBSERVATIONS:
        issues = _validate_observations(
            cast(ReviewObservations, payload), cast(ObservationsContext, context)
        )
    elif expected_schema is AgentSchema.REVIEW_RECONCILIATION:
        issues = _validate_reconciliation(
            cast(ReviewReconciliation, payload), cast(ReconciliationValidationContext, context)
        )
    elif expected_schema is AgentSchema.REVIEW_DISPOSITIONS:
        issues = _validate_dispositions(
            cast(ReviewDispositions, payload), cast(DispositionsContext, context)
        )
    elif expected_schema is AgentSchema.REVIEW_DECISIONS:
        issues = _validate_decisions(
            cast(ReviewDecisions, payload), cast(DecisionsContext, context)
        )
    elif expected_schema is AgentSchema.VERIFICATION_PLAN:
        policy_rejections = _verification_rejections(
            cast(VerificationPlan, payload), cast(VerificationContext, context)
        )
    elif expected_schema is AgentSchema.GRAPH_BREAKDOWN:
        issues = _validate_graph(cast(GraphBreakdown, payload))
    elif expected_schema is AgentSchema.HANDOFF_CUTOFF:
        cutoff = cast(HandoffCutoff, payload)
        issues = _validate_cutoff(cutoff, cast(CutoffContext, context))
        if not issues:
            canonical = canonical_json_bytes(cutoff.model_dump(mode="json", by_alias=True))
            validated_payload = ValidatedCutoff(
                cutoff, canonical, hashlib.sha256(canonical).hexdigest()
            )
    elif expected_schema is AgentSchema.HUMAN_QUESTION:
        issues = _validate_human_question(cast(HumanQuestion, payload))
    if issues:
        raise AgentContractError(issues)
    return ValidatedAgentResult(expected_schema, validated_payload, policy_rejections)


def _render_path(path: IssuePath) -> str:
    if not path:
        return "$"
    result = "$"
    for segment in path:
        if isinstance(segment, int):
            result += f"[{segment}]"
        else:
            result += f".{segment}"
    return result


def render_retry_feedback(issues: Iterable[ContractIssue]) -> str:
    ordered = tuple(sorted(issues, key=_issue_sort_key))
    header = "PREVIOUS ATTEMPT REJECTED\n"
    final = "Fix these and resend the full result. Partial results are not accepted.\n"
    lines = tuple(
        f"  - {_render_path(issue.path)}: {issue.code}: {issue.message}\n" for issue in ordered
    )
    selected: list[str] = []
    limit = min(len(lines), MAX_RETRY_ISSUES)
    for index in range(limit):
        prospective_count = len(selected) + 1
        omitted = len(lines) - prospective_count
        omitted_line = f"  - {omitted} additional issue(s) omitted.\n" if omitted else ""
        block = header + "".join((*selected, lines[index])) + omitted_line + final
        if len(block.encode("utf-8")) > MAX_RETRY_FEEDBACK_BYTES:
            break
        selected.append(lines[index])
    omitted = len(lines) - len(selected)
    omitted_line = f"  - {omitted} additional issue(s) omitted.\n" if omitted else ""
    result = header + "".join(selected) + omitted_line + final
    if len(result.encode("utf-8")) > MAX_RETRY_FEEDBACK_BYTES:
        raise ValueError("feedback envelope exceeds its byte budget")
    return result
