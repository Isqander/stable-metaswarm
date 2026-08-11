from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from itertools import product
from pathlib import Path

import pytest

import metaswarm.domain.review as review_domain
from metaswarm.domain.review import (
    AskHuman,
    CloseClean,
    CycleInvariantError,
    EscalatingDispute,
    EscalatingDisputes,
    InvalidCampaignTransition,
    StartAuthorRevision,
    StartReviewCheck,
    decide_after_revision,
    next_campaign_state,
)

CAMPAIGN_STATES = (
    "discovery",
    "reconciliation",
    "fix_cycle",
    "closed_clean",
    "closed_escalated",
    "closed_cancelled",
)
CAMPAIGN_EVENTS = (
    "discovery_completed",
    "reconciliation_has_findings",
    "reconciliation_clean",
    "check_needs_revision",
    "human_gate_opened",
    "human_extra_revision",
    "check_clean",
    "human_finalized",
    "cancelled",
)
ALLOWED_TRANSITIONS = (
    ("discovery", "discovery_completed", "reconciliation"),
    ("discovery", "cancelled", "closed_cancelled"),
    ("reconciliation", "reconciliation_has_findings", "fix_cycle"),
    ("reconciliation", "reconciliation_clean", "closed_clean"),
    ("reconciliation", "cancelled", "closed_cancelled"),
    ("fix_cycle", "check_needs_revision", "fix_cycle"),
    ("fix_cycle", "human_gate_opened", "fix_cycle"),
    ("fix_cycle", "human_extra_revision", "fix_cycle"),
    ("fix_cycle", "check_clean", "closed_clean"),
    ("fix_cycle", "human_finalized", "closed_escalated"),
    ("fix_cycle", "cancelled", "closed_cancelled"),
)
ALLOWED_KEYS = frozenset((current, event) for current, event, _ in ALLOWED_TRANSITIONS)
FORBIDDEN_TRANSITIONS = tuple(
    (current, event)
    for current, event in product(CAMPAIGN_STATES, CAMPAIGN_EVENTS)
    if (current, event) not in ALLOWED_KEYS
)


def _inspect_review_source(
    source: str,
    source_name: str,
) -> tuple[set[str], list[str], list[str]]:
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    escaping_relative_imports: list[str] = []
    mutable_globals: list[str] = []
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign):
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            value = node.value
        if isinstance(value, (ast.List, ast.Dict, ast.Set)):
            mutable_globals.append(f"{source_name}:{node.lineno}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 2:
                escaping_relative_imports.append(f"{source_name}:{node.lineno}")
            elif node.level == 0 and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
    return imported_roots, escaping_relative_imports, mutable_globals


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    ALLOWED_TRANSITIONS,
    ids=lambda value: str(value),
)
def test_allowed_campaign_transition_matrix(
    current: str,
    event: str,
    expected: str,
) -> None:
    assert next_campaign_state(current, event) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("current", "event"),
    FORBIDDEN_TRANSITIONS,
    ids=lambda value: str(value),
)
def test_every_other_known_campaign_transition_is_rejected(
    current: str,
    event: str,
) -> None:
    with pytest.raises(InvalidCampaignTransition) as raised:
        next_campaign_state(current, event)  # type: ignore[arg-type]
    assert (raised.value.current, raised.value.event) == (current, event)


@pytest.mark.parametrize(
    ("current", "event"),
    (("unknown", "cancelled"), ("fix_cycle", "unknown"), (None, "check_clean")),
)
def test_unknown_campaign_state_or_event_is_rejected(current: object, event: object) -> None:
    with pytest.raises(InvalidCampaignTransition) as raised:
        next_campaign_state(current, event)  # type: ignore[arg-type]
    assert (raised.value.current, raised.value.event) == (current, event)


def test_event_transitions_collapse_to_nine_normative_pairs_without_initialization() -> None:
    pairs = {(current, expected) for current, _, expected in ALLOWED_TRANSITIONS}
    assert pairs == {
        ("discovery", "reconciliation"),
        ("discovery", "closed_cancelled"),
        ("reconciliation", "fix_cycle"),
        ("reconciliation", "closed_clean"),
        ("reconciliation", "closed_cancelled"),
        ("fix_cycle", "fix_cycle"),
        ("fix_cycle", "closed_clean"),
        ("fix_cycle", "closed_escalated"),
        ("fix_cycle", "closed_cancelled"),
    }
    assert all(expected != "discovery" for _, _, expected in ALLOWED_TRANSITIONS)


@pytest.mark.parametrize(
    "state",
    (
        "discovery",
        "reconciliation",
        "closed_clean",
        "closed_escalated",
        "closed_cancelled",
        "unknown",
    ),
)
def test_review_check_starts_only_from_fix_cycle(state: str) -> None:
    with pytest.raises(InvalidCampaignTransition) as raised:
        decide_after_revision(state)  # type: ignore[arg-type]
    assert (raised.value.current, raised.value.event) == (state, "start_review_check")


def test_review_check_after_revision_is_a_frozen_value_without_campaign_event() -> None:
    action = decide_after_revision("fix_cycle")
    assert action == StartReviewCheck()
    with pytest.raises((FrozenInstanceError, TypeError)):
        action.unexpected = True  # type: ignore[misc]


def test_action_classes_have_only_their_fixed_combinations() -> None:
    dispute_snapshot = EscalatingDisputes(
        (EscalatingDispute(7, "critical", "high", "policy-v1"),)
    )

    clean = CloseClean()
    revision = StartAuthorRevision()
    dispute = AskHuman("dispute", dispute_snapshot)
    cap = AskHuman("cap_exhausted_same")

    assert (clean.round_result, clean.campaign_event) == ("clean", "check_clean")
    assert (revision.round_result, revision.campaign_event) == (
        "needs_revision",
        "check_needs_revision",
    )
    assert (dispute.round_result, dispute.campaign_event, dispute.snapshot) == (
        "escalated",
        "human_gate_opened",
        dispute_snapshot,
    )
    assert cap.snapshot is None
    assert next_campaign_state("fix_cycle", dispute.campaign_event) == "fix_cycle"

    with pytest.raises(FrozenInstanceError):
        clean.round_result = "escalated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        dispute.reason = "cap_exhausted_new"  # type: ignore[misc]


@pytest.mark.parametrize(
    "factory",
    (
        lambda: CloseClean(round_result="escalated"),  # type: ignore[arg-type]
        lambda: CloseClean(campaign_event="human_gate_opened"),  # type: ignore[arg-type]
        lambda: StartAuthorRevision(round_result="clean"),  # type: ignore[arg-type]
        lambda: StartAuthorRevision(campaign_event="check_clean"),  # type: ignore[arg-type]
        lambda: AskHuman("dispute"),
        lambda: AskHuman(
            "cap_exhausted_same",
            EscalatingDisputes((EscalatingDispute(1, "high", "high", "v1"),)),
        ),
        lambda: AskHuman("unknown"),  # type: ignore[arg-type]
        lambda: AskHuman("cap_exhausted_new", round_result="clean"),  # type: ignore[arg-type]
        lambda: AskHuman("cap_exhausted_new", campaign_event="check_clean"),  # type: ignore[arg-type]
    ),
)
def test_incompatible_action_combinations_are_rejected(factory: object) -> None:
    with pytest.raises(CycleInvariantError):
        factory()  # type: ignore[operator]


def test_review_package_exports_only_the_t1_4_contract() -> None:
    assert set(review_domain.__all__) == {
        "AfterCheckDecision",
        "AskHuman",
        "CampaignEvent",
        "CampaignState",
        "CheckFacts",
        "CloseClean",
        "CycleInvariantError",
        "EscalatingDispute",
        "EscalatingDisputes",
        "InvalidCampaignTransition",
        "OpenFinding",
        "ReviewCounters",
        "ReviewStopReason",
        "RoundResult",
        "StartAuthorRevision",
        "StartReviewCheck",
        "decide_after_check",
        "decide_after_revision",
        "next_campaign_state",
    }


def test_review_domain_imports_only_declared_stdlib_and_its_own_modules() -> None:
    review_dir = Path(__file__).resolve().parents[3] / "src/metaswarm/domain/review"
    imported_roots: set[str] = set()
    escaping_relative_imports: list[str] = []
    mutable_globals: list[str] = []
    for path in review_dir.glob("*.py"):
        roots, relative_escapes, globals_ = _inspect_review_source(
            path.read_text(encoding="utf-8"),
            path.name,
        )
        imported_roots.update(roots)
        escaping_relative_imports.extend(relative_escapes)
        mutable_globals.extend(globals_)
    assert imported_roots <= {
        "__future__",
        "dataclasses",
        "hashlib",
        "json",
        "typing",
        "unicodedata",
    }
    assert imported_roots.isdisjoint(
        {"asyncio", "metaswarm", "sqlite3", "subprocess", "socket", "pathlib"}
    )
    assert escaping_relative_imports == []
    assert mutable_globals == []


def test_review_import_guard_detects_relative_escape() -> None:
    allowed = _inspect_review_source("from .model import CheckFacts\n", "allowed.py")
    escaped = _inspect_review_source("from ...store import Database\n", "escaped.py")

    assert allowed[1] == []
    assert escaped[1] == ["escaped.py:1"]
