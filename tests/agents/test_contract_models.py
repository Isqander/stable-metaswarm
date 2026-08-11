from __future__ import annotations

import ast
import copy
import json
from dataclasses import FrozenInstanceError, fields, is_dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from metaswarm.agents import (
    AgentContractError,
    AgentSchema,
    ValidationContextError,
    validate_agent_result,
)
from metaswarm.agents.parse import RESULT_BEGIN, RESULT_END
from metaswarm.agents.validation import (
    CutoffContext,
    DecisionsContext,
    DispositionsContext,
    GraphContext,
    HumanQuestionContext,
    ObservationsContext,
    ReconciliationValidationContext,
    VerificationContext,
    schema_registry,
)

SHA = "a" * 40


def _text(payload: dict[str, object]) -> str:
    return f"{RESULT_BEGIN}{json.dumps(payload, ensure_ascii=False)}{RESULT_END}"


def _samples() -> dict[AgentSchema, tuple[dict[str, object], object]]:
    return {
        AgentSchema.REVIEW_OBSERVATIONS: (
            {
                "schema": "review.observations.v1",
                "observations": [
                    {
                        "title": "Race in state flush",
                        "body": "Commit happens before the durable state update.",
                        "file": "src/service.py",
                        "line_start": 10,
                        "line_end": 12,
                        "evidence": "commit() precedes transaction close",
                        "severity_suggested": "high",
                    }
                ],
            },
            ObservationsContext(frozenset({"src/service.py"})),
        ),
        AgentSchema.REVIEW_RECONCILIATION: (
            {
                "schema": "review.reconciliation.v1",
                "groups": [
                    {"observation_ids": ["O-1"], "outcome": "new", "title": "New race"},
                    {
                        "observation_ids": ["O-2"],
                        "outcome": "reaffirmed_closed",
                        "finding_id": "F-2",
                    },
                ],
            },
            ReconciliationValidationContext(
                frozenset({"O-1", "O-2"}),
                frozenset({"F-1"}),
                (("F-2", "accepted_reason"),),
            ),
        ),
        AgentSchema.REVIEW_DISPOSITIONS: (
            {
                "schema": "review.dispositions.v1",
                "dispositions": [{"finding_id": "F-1", "disposition": "fixed"}],
            },
            DispositionsContext(frozenset({"F-1"})),
        ),
        AgentSchema.REVIEW_DECISIONS: (
            {
                "schema": "review.decisions.v1",
                "decisions": [{"finding_id": "F-1", "decision": "verified_fixed"}],
                "new_observations": [],
            },
            DecisionsContext(
                frozenset({"F-1"}),
                (("F-1", "fixed"),),
                (("F-1", "O-1"),),
                frozenset({"src/service.py"}),
            ),
        ),
        AgentSchema.VERIFICATION_PLAN: (
            {
                "schema": "verification.plan.v1",
                "steps": [{"cwd": ".", "argv": ["pytest", "-q"], "expect": "exit_zero"}],
                "rationale": "Run the project tests.",
            },
            VerificationContext(
                frozenset({"."}),
                frozenset({"pytest"}),
                frozenset({"prod.env"}),
                frozenset({"prod.example"}),
            ),
        ),
        AgentSchema.GRAPH_BREAKDOWN: (
            {
                "schema": "graph.breakdown.v1",
                "tasks": [
                    {"id": "T1", "title": "Store", "body": "Implement store."},
                    {"id": "T2", "title": "Domain", "body": "Implement domain."},
                ],
                "dependencies": [{"parent": "T1", "child": "T2"}],
            },
            GraphContext(),
        ),
        AgentSchema.HANDOFF_CUTOFF: (
            {
                "schema": "handoff.cutoff.v1",
                "task": "Continue contract implementation.",
                "current_state": "Parser is complete.",
                "next_action": "Implement validation.",
                "touched_files": [{"path": "src/service.py", "purpose": "Implementation"}],
                "open_questions": [],
                "acceptance_criteria": ["Tests pass."],
                "referenced_files": ["docs/design.md"],
                "referenced_shas": [SHA],
                "implementation_scope": None,
            },
            CutoffContext(frozenset({"src/service.py", "docs/design.md"}), frozenset({SHA})),
        ),
        AgentSchema.HUMAN_QUESTION: (
            {
                "schema": "human.question.v1",
                "question_text": "Как поступить с существующими токенами?",
                "context": "Решение меняет поведение клиентов.",
                "options": [{"key": "A", "label": "Инвалидировать", "confidence": 0.3}],
            },
            HumanQuestionContext("open_question"),
        ),
    }


def test_registry_is_exactly_the_eight_agent_authored_schemas() -> None:
    assert schema_registry() == tuple(AgentSchema)
    assert {item.value for item in AgentSchema} == {
        "review.observations.v1",
        "review.reconciliation.v1",
        "review.dispositions.v1",
        "review.decisions.v1",
        "verification.plan.v1",
        "graph.breakdown.v1",
        "handoff.cutoff.v1",
        "human.question.v1",
    }


@pytest.mark.parametrize("schema", tuple(AgentSchema))
def test_each_normative_sample_returns_a_typed_deeply_immutable_result(
    schema: AgentSchema,
) -> None:
    raw, context = _samples()[schema]
    result = validate_agent_result(_text(raw), schema, context)  # type: ignore[arg-type]
    assert result.schema is schema
    assert result.policy_rejections == ()
    assert not _reachable_mutable(result)
    models = [value for value in _walk(result.payload) if isinstance(value, BaseModel)]
    nested = models[-1]
    field_name = next(iter(type(nested).model_fields))
    with pytest.raises((FrozenInstanceError, ValidationError)):
        setattr(nested, field_name, getattr(nested, field_name))
    nested_tuple = next(value for value in _walk(result.payload) if isinstance(value, tuple))
    with pytest.raises(AttributeError):
        nested_tuple.append(None)  # type: ignore[attr-defined]
    with pytest.raises(AttributeError):
        result.policy_rejections.append(None)  # type: ignore[attr-defined]

    before = repr(result)
    source_list = next(value for value in _walk(raw) if isinstance(value, list))
    source_list.append({})
    assert repr(result) == before


def _walk(value: object, seen: set[int] | None = None) -> list[object]:
    if seen is None:
        seen = set()
    if id(value) in seen:
        return []
    seen.add(id(value))
    values = [value]
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            values.extend(_walk(getattr(value, name), seen))
    elif is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            values.extend(_walk(getattr(value, item.name), seen))
    elif isinstance(value, dict):
        for key, item in value.items():
            values.extend(_walk(key, seen))
            values.extend(_walk(item, seen))
    elif isinstance(value, (tuple, list, frozenset, set)):
        for item in value:
            values.extend(_walk(item, seen))
    return values


def _reachable_mutable(value: object, seen: set[int] | None = None) -> bool:
    if seen is None:
        seen = set()
    if id(value) in seen:
        return False
    seen.add(id(value))
    if isinstance(value, (list, dict, set)):
        return True
    if isinstance(value, BaseModel):
        return any(
            _reachable_mutable(getattr(value, name), seen) for name in type(value).model_fields
        )
    if is_dataclass(value) and not isinstance(value, type):
        return any(_reachable_mutable(getattr(value, item.name), seen) for item in fields(value))
    if isinstance(value, (tuple, frozenset)):
        return any(_reachable_mutable(item, seen) for item in value)
    return False


@pytest.mark.parametrize("schema", tuple(AgentSchema))
def test_extra_missing_and_coercible_types_are_rejected_for_each_schema(
    schema: AgentSchema,
) -> None:
    raw, context = _samples()[schema]

    extra = copy.deepcopy(raw)
    extra["unexpected"] = 1
    with pytest.raises(AgentContractError) as extra_error:
        validate_agent_result(_text(extra), schema, context)  # type: ignore[arg-type]
    assert {issue.code for issue in extra_error.value.issues} == {"extra_field"}

    missing = copy.deepcopy(raw)
    required = next(key for key in missing if key != "schema")
    del missing[required]
    with pytest.raises(AgentContractError) as missing_error:
        validate_agent_result(_text(missing), schema, context)  # type: ignore[arg-type]
    assert "missing_field" in {issue.code for issue in missing_error.value.issues}

    wrong = copy.deepcopy(raw)
    target = next(key for key in wrong if key != "schema")
    wrong[target] = 1 if not isinstance(wrong[target], int) else "1"
    with pytest.raises(AgentContractError):
        validate_agent_result(_text(wrong), schema, context)  # type: ignore[arg-type]


def test_expected_schema_is_checked_before_another_model_can_match() -> None:
    raw, context = _samples()[AgentSchema.REVIEW_OBSERVATIONS]
    raw["schema"] = "verification.result.v1"
    with pytest.raises(AgentContractError) as raised:
        validate_agent_result(_text(raw), AgentSchema.REVIEW_OBSERVATIONS, context)  # type: ignore[arg-type]
    assert [(item.code, item.path) for item in raised.value.issues] == [
        ("schema_mismatch", ("schema",))
    ]

    with pytest.raises(ValidationContextError):
        validate_agent_result(
            _text(raw),
            "review.observations.v1",  # type: ignore[arg-type]
            context,
        )


def test_contract_boundary_imports_only_pydantic_and_side_effect_free_stdlib() -> None:
    root = Path(__file__).parents[2] / "src" / "metaswarm" / "agents"
    allowed_roots = {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "math",
        "pathlib",
        "typing",
        "pydantic",
    }
    forbidden_calls = {"breakpoint", "eval", "exec", "open"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert {name.name.split(".")[0] for name in node.names} <= allowed_roots
            elif isinstance(node, ast.ImportFrom):
                assert node.level <= 1
                if node.level == 0 and node.module is not None:
                    assert node.module.split(".")[0] in allowed_roots
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls
