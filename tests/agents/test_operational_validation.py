from __future__ import annotations

import hashlib
import json

import pytest

from metaswarm.agents import (
    AgentContractError,
    AgentSchema,
    ValidationContextError,
    render_retry_feedback,
    validate_agent_result,
)
from metaswarm.agents.parse import RESULT_BEGIN, RESULT_END
from metaswarm.agents.validation import (
    CutoffContext,
    GraphContext,
    HumanQuestionContext,
    ObservationsContext,
    ValidatedCutoff,
    VerificationContext,
)

BASELINE = "a" * 40
PARENT = "b" * 40


def _text(payload: dict[str, object], *, sort_keys: bool = False) -> str:
    return (
        f"{RESULT_BEGIN}{json.dumps(payload, ensure_ascii=False, sort_keys=sort_keys)}{RESULT_END}"
    )


def _error_codes(payload: dict[str, object], schema: AgentSchema, context: object) -> set[str]:
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(_text(payload), schema, context)  # type: ignore[arg-type]
    return {issue.code for issue in raised.value.issues}


def _verification_payload(**step_changes: object) -> dict[str, object]:
    step: dict[str, object] = {
        "cwd": ".",
        "argv": ["pytest", "-q"],
        "expect": "exit_zero",
        "step_timeout_s": 60,
    }
    step.update(step_changes)
    return {
        "schema": "verification.plan.v1",
        "steps": [step],
        "rationale": "Run the focused tests.",
    }


def _verification_context() -> VerificationContext:
    return VerificationContext(
        frozenset({".", "tests"}),
        frozenset({"pytest", "ruff"}),
        frozenset({"secrets.env"}),
        frozenset({"production"}),
    )


def test_verification_structure_is_strict_before_policy_validation() -> None:
    result = validate_agent_result(
        _text(_verification_payload()), AgentSchema.VERIFICATION_PLAN, _verification_context()
    )
    assert result.policy_rejections == ()

    cases = (
        ({"schema": "verification.plan.v1", "steps": [], "rationale": "Why"}, "invalid_value"),
        (
            {
                "schema": "verification.plan.v1",
                "steps": [_verification_payload()["steps"][0]],
                "rationale": " ",
            },
            "invalid_value",
        ),
        (_verification_payload(argv="pytest -q"), "invalid_type"),
        (_verification_payload(argv=[]), "invalid_value"),
        (_verification_payload(expect="exit_nonzero"), "invalid_literal"),
        (_verification_payload(step_timeout_s=0), "invalid_value"),
    )
    for payload, expected in cases:
        assert expected in _error_codes(
            payload, AgentSchema.VERIFICATION_PLAN, _verification_context()
        )


def test_all_five_verification_policy_rejections_preserve_the_typed_plan() -> None:
    payload = _verification_payload(
        cwd="../outside",
        argv=["bash", "secrets.env", "production", "&&"],
    )
    result = validate_agent_result(
        _text(payload), AgentSchema.VERIFICATION_PLAN, _verification_context()
    )
    assert result.payload.steps[0].argv[0] == "bash"  # type: ignore[union-attr]
    assert [item.code for item in result.policy_rejections] == [
        "cwd_outside",
        "shell_syntax",
        "executable",
        "path",
        "target",
    ]


@pytest.mark.parametrize(
    "shell_fragment",
    (
        "&&",
        "a & command",
        "|",
        "$(echo x)",
        "$HOME",
        "${HOME}",
        ">",
        ";",
        "`cmd`",
        "a\ncommand",
        "a\rcommand",
    ),
)
def test_each_forbidden_shell_form_is_visible(shell_fragment: str) -> None:
    payload = _verification_payload(argv=["pytest", shell_fragment])
    result = validate_agent_result(
        _text(payload), AgentSchema.VERIFICATION_PLAN, _verification_context()
    )
    assert [item.code for item in result.policy_rejections] == ["shell_syntax"]


@pytest.mark.parametrize("cwd", ("tests", "tests/agents"))
def test_cwd_inside_an_allowed_root_is_accepted(cwd: str) -> None:
    payload = _verification_payload(cwd=cwd)
    result = validate_agent_result(
        _text(payload), AgentSchema.VERIFICATION_PLAN, _verification_context()
    )
    assert result.policy_rejections == ()


def test_cwd_root_prefix_is_checked_on_a_path_boundary() -> None:
    context = VerificationContext(
        frozenset({"src"}),
        frozenset({"pytest"}),
        frozenset(),
        frozenset(),
    )
    accepted = validate_agent_result(
        _text(_verification_payload(cwd="src/package")),
        AgentSchema.VERIFICATION_PLAN,
        context,
    )
    rejected = validate_agent_result(
        _text(_verification_payload(cwd="srcfoo")),
        AgentSchema.VERIFICATION_PLAN,
        context,
    )
    assert accepted.policy_rejections == ()
    assert [item.code for item in rejected.policy_rejections] == ["cwd_outside"]


@pytest.mark.parametrize(
    "cwd",
    ("C:/outside", "C:\\outside", "C:outside", "\\outside", "tests\\..\\..\\outside"),
)
def test_windows_absolute_or_traversing_cwd_is_rejected(cwd: str) -> None:
    payload = _verification_payload(cwd=cwd)
    result = validate_agent_result(
        _text(payload), AgentSchema.VERIFICATION_PLAN, _verification_context()
    )
    assert [item.code for item in result.policy_rejections] == ["cwd_outside"]


def _graph(
    tasks: list[dict[str, str]] | None = None,
    dependencies: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "schema": "graph.breakdown.v1",
        "tasks": tasks
        if tasks is not None
        else [
            {"id": "A", "title": "A", "body": "First"},
            {"id": "B", "title": "B", "body": "Second"},
            {"id": "C", "title": "C", "body": "Third"},
        ],
        "dependencies": dependencies
        if dependencies is not None
        else [{"parent": "A", "child": "B"}, {"parent": "B", "child": "C"}],
    }


def test_graph_accepts_a_dag_independently_of_input_order() -> None:
    context = GraphContext()
    first = _graph()
    second = _graph(
        list(reversed(first["tasks"])),  # type: ignore[arg-type]
        list(reversed(first["dependencies"])),  # type: ignore[arg-type]
    )
    validate_agent_result(_text(first), AgentSchema.GRAPH_BREAKDOWN, context)
    validate_agent_result(_text(second), AgentSchema.GRAPH_BREAKDOWN, context)
    validate_agent_result(
        _text(
            _graph(
                tasks=[
                    {"id": "T1: store", "title": "Store", "body": "First"},
                    {"id": "T2 / domain", "title": "Domain", "body": "Second"},
                ],
                dependencies=[{"parent": "T1: store", "child": "T2 / domain"}],
            )
        ),
        AgentSchema.GRAPH_BREAKDOWN,
        context,
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        (
            _graph(tasks=[{"id": "A", "title": "A", "body": "First"}] * 2, dependencies=[]),
            "duplicate_task",
        ),
        (_graph(dependencies=[{"parent": "A", "child": "B"}] * 2), "duplicate_edge"),
        (_graph(dependencies=[{"parent": "A", "child": "X"}]), "dangling_edge"),
        (_graph(dependencies=[{"parent": "A", "child": "A"}]), "self_edge"),
    ),
)
def test_graph_rejects_each_structural_defect(payload: dict[str, object], expected: str) -> None:
    assert expected in _error_codes(payload, AgentSchema.GRAPH_BREAKDOWN, GraphContext())


def test_graph_cycle_reports_a_stable_concrete_path() -> None:
    payload = _graph(
        dependencies=[
            {"parent": "C", "child": "A"},
            {"parent": "B", "child": "C"},
            {"parent": "A", "child": "B"},
        ]
    )
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(_text(payload), AgentSchema.GRAPH_BREAKDOWN, GraphContext())
    issue = next(item for item in raised.value.issues if item.code == "cycle")
    assert issue.message == (
        "dependency cycle: $.tasks[0].id -> $.tasks[1].id -> $.tasks[2].id -> $.tasks[0].id"
    )


def test_graph_cycle_diagnostic_uses_paths_not_model_controlled_ids() -> None:
    secret_id = "private\ninjected diagnostic"
    payload = _graph(
        tasks=[
            {"id": secret_id, "title": "First", "body": "First"},
            {"id": "B", "title": "Second", "body": "Second"},
        ],
        dependencies=[
            {"parent": secret_id, "child": "B"},
            {"parent": "B", "child": secret_id},
        ],
    )
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(_text(payload), AgentSchema.GRAPH_BREAKDOWN, GraphContext())
    issue = next(item for item in raised.value.issues if item.code == "cycle")
    assert issue.message == ("dependency cycle: $.tasks[1].id -> $.tasks[0].id -> $.tasks[1].id")
    assert secret_id not in issue.message


def test_long_acyclic_graph_does_not_depend_on_python_recursion_limit() -> None:
    size = 2_000
    payload = _graph(
        tasks=[
            {"id": f"T{index}", "title": f"Task {index}", "body": "Body"} for index in range(size)
        ],
        dependencies=[
            {"parent": f"T{index}", "child": f"T{index + 1}"} for index in range(size - 1)
        ],
    )
    validate_agent_result(_text(payload), AgentSchema.GRAPH_BREAKDOWN, GraphContext())


def test_large_cycle_keeps_a_bounded_concrete_path_in_retry_feedback() -> None:
    size = 6_000
    payload = _graph(
        tasks=[
            {"id": f"T{index}", "title": f"Task {index}", "body": "Body"} for index in range(size)
        ],
        dependencies=[
            *({"parent": f"T{index}", "child": f"T{index + 1}"} for index in range(size - 1)),
            {"parent": f"T{size - 1}", "child": "T0"},
        ],
    )
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(_text(payload), AgentSchema.GRAPH_BREAKDOWN, GraphContext())
    assert len(raised.value.issues) == 1
    issue = raised.value.issues[0]
    assert issue.code == "cycle"
    assert "5969 path node(s) omitted" in issue.message
    assert "$.tasks[0].id -> $.tasks[1].id" in issue.message
    assert "$.tasks[5999].id -> $.tasks[0].id" in issue.message
    feedback = render_retry_feedback(raised.value.issues)
    assert ": cycle: dependency cycle:" in feedback
    assert "additional issue(s) omitted" not in feedback
    assert len(feedback.encode("utf-8")) <= 16_384


def _cutoff(scope: dict[str, str] | None = None) -> dict[str, object]:
    shas = [BASELINE] if scope is None else [BASELINE, PARENT]
    return {
        "schema": "handoff.cutoff.v1",
        "task": "Continue implementation.",
        "current_state": "Contracts are implemented.",
        "next_action": "Run the review.",
        "touched_files": [{"path": "src/contract.py", "purpose": "Contract"}],
        "open_questions": [],
        "acceptance_criteria": ["Tests pass."],
        "referenced_files": ["docs/design.md"],
        "referenced_shas": shas,
        "implementation_scope": scope,
    }


def _cutoff_context(*, implementation: bool) -> CutoffContext:
    return CutoffContext(
        frozenset({"src/contract.py", "docs/design.md"}),
        frozenset({BASELINE, PARENT}),
        BASELINE if implementation else None,
        PARENT if implementation else None,
    )


def test_cutoff_canonical_bytes_digest_and_both_context_forms() -> None:
    plain = _cutoff()
    plain_result = validate_agent_result(
        _text(plain), AgentSchema.HANDOFF_CUTOFF, _cutoff_context(implementation=False)
    )
    assert isinstance(plain_result.payload, ValidatedCutoff)
    assert (
        plain_result.payload.sha256
        == hashlib.sha256(plain_result.payload.canonical_bytes).hexdigest()
    )

    scope = {
        "planning_baseline_sha": BASELINE,
        "implementation_parent_sha": PARENT,
    }
    implementation = _cutoff(scope)
    first = validate_agent_result(
        _text(implementation), AgentSchema.HANDOFF_CUTOFF, _cutoff_context(implementation=True)
    )
    second = validate_agent_result(
        _text(implementation, sort_keys=True),
        AgentSchema.HANDOFF_CUTOFF,
        _cutoff_context(implementation=True),
    )
    assert isinstance(first.payload, ValidatedCutoff)
    assert isinstance(second.payload, ValidatedCutoff)
    assert first.payload.canonical_bytes == second.payload.canonical_bytes
    assert first.payload.sha256 == second.payload.sha256
    assert first.payload.payload.schema == "handoff.cutoff.v1"
    assert b'"schema":"handoff.cutoff.v1"' in first.payload.canonical_bytes
    assert b"schema_value" not in first.payload.canonical_bytes


@pytest.mark.parametrize(
    ("mutate", "implementation", "expected"),
    (
        (lambda value: value.update(task=""), False, "invalid_length"),
        (lambda value: value.update(current_state=""), False, "invalid_length"),
        (lambda value: value.update(next_action=""), False, "invalid_length"),
        (lambda value: value.update(touched_files=[]), False, "invalid_value"),
        (lambda value: value.update(acceptance_criteria=[]), False, "invalid_value"),
        (lambda value: value.pop("open_questions"), False, "missing_field"),
        (lambda value: value["touched_files"][0].update(path="missing.py"), False, "unknown_file"),
        (lambda value: value["referenced_files"].append("missing.md"), False, "unknown_file"),
        (lambda value: value["referenced_shas"].append("c" * 40), False, "unknown_sha"),
        (
            lambda value: value.update(
                implementation_scope={
                    "planning_baseline_sha": BASELINE,
                    "implementation_parent_sha": PARENT,
                }
            ),
            False,
            "unexpected_scope",
        ),
        (lambda value: value.update(implementation_scope=None), True, "missing_scope"),
        (
            lambda value: value.update(implementation_scope={"planning_baseline_sha": BASELINE}),
            True,
            "missing_field",
        ),
        (
            lambda value: value["implementation_scope"].update(
                planning_baseline_sha=PARENT, implementation_parent_sha=BASELINE
            ),
            True,
            "scope_mismatch",
        ),
        (lambda value: value["referenced_shas"].remove(PARENT), True, "scope_reference"),
    ),
)
def test_cutoff_rejects_invalid_sections_references_and_scope(
    mutate: object, implementation: bool, expected: str
) -> None:
    scope = (
        {"planning_baseline_sha": BASELINE, "implementation_parent_sha": PARENT}
        if implementation
        else None
    )
    payload = _cutoff(scope)
    mutate(payload)  # type: ignore[operator]
    assert expected in _error_codes(
        payload, AgentSchema.HANDOFF_CUTOFF, _cutoff_context(implementation=implementation)
    )


def _question(options: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema": "human.question.v1",
        "question_text": "Как продолжить?",
        "context": "Есть неоднозначность требований.",
        "options": [] if options is None else options,
    }


def test_open_question_accepts_empty_or_unique_options() -> None:
    context = HumanQuestionContext("open_question")
    validate_agent_result(_text(_question()), AgentSchema.HUMAN_QUESTION, context)
    payload = _question(
        [
            {"key": "A", "label": "Остановиться", "confidence": 0.0},
            {"key": "B", "label": "Продолжить", "confidence": 1.0},
        ]
    )
    validate_agent_result(_text(payload), AgentSchema.HUMAN_QUESTION, context)


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        (_question([{"key": " ", "label": "One", "confidence": 0.5}]), "empty_option_key"),
        (_question([{"key": "A", "label": " ", "confidence": 0.5}]), "empty_option_label"),
        (
            _question(
                [
                    {"key": "A", "label": "One", "confidence": 0.5},
                    {"key": "A", "label": "Two", "confidence": 0.5},
                ]
            ),
            "duplicate_option",
        ),
        (_question([{"key": "A", "label": "One", "confidence": 1.1}]), "invalid_value"),
        (_question([{"key": "A", "label": "One", "confidence": -0.1}]), "invalid_value"),
        ({**_question(), "question_text": " "}, "invalid_value"),
    ),
)
def test_open_question_rejects_invalid_text_and_options(
    payload: dict[str, object], expected: str
) -> None:
    assert expected in _error_codes(
        payload, AgentSchema.HUMAN_QUESTION, HumanQuestionContext("open_question")
    )


def test_non_open_question_reason_and_context_mismatch_are_caller_errors() -> None:
    with pytest.raises(ValidationContextError):
        HumanQuestionContext("dispute")
    with pytest.raises(ValidationContextError):
        validate_agent_result(
            _text(_question()),
            AgentSchema.HUMAN_QUESTION,
            ObservationsContext(frozenset()),
        )
    with pytest.raises(ValidationContextError):
        CutoffContext(
            frozenset(),
            frozenset({PARENT}),
            1,  # type: ignore[arg-type]
            PARENT,
        )


@pytest.mark.parametrize(
    ("baseline", "parent"),
    ((BASELINE, None), (None, PARENT)),
)
def test_cutoff_context_requires_both_sha_roles_or_neither(
    baseline: str | None, parent: str | None
) -> None:
    with pytest.raises(ValidationContextError):
        CutoffContext(
            frozenset(),
            frozenset({BASELINE, PARENT}),
            baseline,
            parent,
        )
