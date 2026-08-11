from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from metaswarm.domain.review.dedup import ObservationFingerprint, observation_dedup_key
from metaswarm.domain.review.reconcile_model import (
    RECONCILER_REQUIREMENTS,
    ClosedFindingRef,
    OpenFindingRef,
    ProposedGroup,
    ReconciliationInput,
    ReconciliationInputError,
    RoundObservation,
)
from metaswarm.domain.review.reconciliation import reconcile


def _observation(
    observation_id: int,
    *,
    public_id: str | None = None,
    round_id: int = 10,
    seq: int | None = None,
    lane_id: int | None = None,
    lane_index: int | None = None,
    evidence: str | None = None,
) -> RoundObservation:
    return RoundObservation(
        id=observation_id,
        public_id=public_id or f"O-{observation_id}",
        seq=observation_id if seq is None else seq,
        round_id=round_id,
        lane_id=lane_id or observation_id,
        lane_index=observation_id if lane_index is None else lane_index,
        severity="high",
        title="Café race",
        body="Body\nline",
        file_path="src/a.py",
        line_start=7,
        line_end=9,
        evidence=evidence,
    )


def _input(*observations: RoundObservation) -> ReconciliationInput:
    return ReconciliationInput(
        context="discovery",
        current_round_id=10,
        observations=tuple(observations),
        open_findings=(),
        closed_findings=(),
        current_round_finding_ids=frozenset(),
    )


@pytest.mark.parametrize(
    ("fingerprint", "expected"),
    (
        (
            ObservationFingerprint(
                "high",
                "Cafe\u0301\r\nRace",
                "Body\rLine",
                "src/a.py",
                7,
                9,
            ),
            "731435524d0ebfaf1e49f1990daa715ca03ffa751596215d3664a171a704ccc1",
        ),
        (
            ObservationFingerprint("medium", "Title", "Body", None, None, None),
            "52fb7f9b54dee6a5780b0f2d3d5e1afa373b9c294d93f3c6464a021e6c30311b",
        ),
    ),
    ids=("unicode-and-lines", "null-location"),
)
def test_observation_dedup_key_has_stable_golden_vectors(
    fingerprint: ObservationFingerprint,
    expected: str,
) -> None:
    assert observation_dedup_key(fingerprint) == expected


def test_dedup_key_normalizes_only_unicode_and_newlines() -> None:
    decomposed = ObservationFingerprint(
        "high", "Cafe\u0301\rTitle", "A\r\nB", "src/Cafe\u0301.py", 1, 2
    )
    canonical = ObservationFingerprint(
        "high", "Café\nTitle", "A\nB", "src/Café.py", 1, 2
    )
    assert observation_dedup_key(decomposed) == observation_dedup_key(canonical)

    variants = (
        ObservationFingerprint("High", "Café\nTitle", "A\nB", "src/Café.py", 1, 2),
        ObservationFingerprint("high", " Café\nTitle", "A\nB", "src/Café.py", 1, 2),
        ObservationFingerprint("high", "Café!\nTitle", "A\nB", "src/Café.py", 1, 2),
    )
    assert all(
        observation_dedup_key(item) != observation_dedup_key(canonical)
        for item in variants
    )


def test_null_file_is_not_an_empty_file_name() -> None:
    null_file = ObservationFingerprint("high", "Title", "Body", None, None, None)
    empty_file = ObservationFingerprint("high", "Title", "Body", "", None, None)
    assert observation_dedup_key(null_file) != observation_dedup_key(empty_file)


@pytest.mark.parametrize(
    "factory",
    (
        lambda: ObservationFingerprint("high", "Title", "Body", None, None, 1),
        lambda: ObservationFingerprint("high", "Title", "Body", "a.py", 2, 1),
        lambda: ObservationFingerprint("high", "\ud800", "Body", None, None, None),
    ),
    ids=("half-location", "reversed-location", "surrogate"),
)
def test_invalid_fingerprint_is_rejected(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_equal_diagnostic_keys_do_not_merge_raw_observations() -> None:
    first = _observation(1, lane_index=1, evidence="first evidence")
    second = _observation(2, lane_index=0, evidence="second evidence")
    first_key = observation_dedup_key(
        ObservationFingerprint(
            first.severity,
            first.title,
            first.body,
            first.file_path,
            first.line_start,
            first.line_end,
        )
    )
    second_key = observation_dedup_key(
        ObservationFingerprint(
            second.severity,
            second.title,
            second.body,
            second.file_path,
            second.line_start,
            second.line_end,
        )
    )
    assert first_key == second_key

    ready = reconcile(
        _input(second, first),
        (ProposedGroup(("O-2", "O-1"), "new", title="One explicit group"),),
    )
    assert len(ready.new_findings) == 1
    assert ready.new_findings[0].observation_ids == (1, 2)
    assert ready.new_findings[0].observation_public_ids == ("O-1", "O-2")
    assert ready.new_findings[0].owner_lane_index == 0
    assert tuple(link.observation_id for link in ready.links) == (1, 2)
    assert tuple(link.observation_public_id for link in ready.links) == ("O-1", "O-2")
    assert (first.evidence, second.evidence) == ("first evidence", "second evidence")


@pytest.mark.parametrize(
    ("value", "groups"),
    (
        (
            _input(_observation(1), _observation(2, round_id=11)),
            (ProposedGroup(("O-1", "O-2"), "new", title="Covered"),),
        ),
        (
            _input(_observation(1), _observation(1, public_id="O-2")),
            (ProposedGroup(("O-1", "O-2"), "new", title="Covered"),),
        ),
        (
            _input(_observation(1), _observation(2, public_id="O-1")),
            (ProposedGroup(("O-1",), "new", title="Covered"),),
        ),
        (
            ReconciliationInput(
                "discovery",
                10,
                (_observation(1),),
                (),
                (),
                frozenset({99}),
            ),
            (ProposedGroup(("O-1",), "new", title="Covered"),),
        ),
    ),
    ids=("mixed-round", "duplicate-internal-id", "duplicate-public-id", "foreign-round-target"),
)
def test_invalid_reconciliation_input_is_rejected_before_proposal(
    value: ReconciliationInput,
    groups: tuple[ProposedGroup, ...],
) -> None:
    with pytest.raises(ReconciliationInputError):
        reconcile(value, groups)


def test_open_and_closed_ledger_sets_must_not_overlap() -> None:
    value = ReconciliationInput(
        "discovery",
        10,
        (),
        (OpenFindingRef(101, "F-101", "Open"),),
        (ClosedFindingRef(101, "F-201", "Closed", "verified_fixed", "reviewer"),),
        frozenset(),
    )
    with pytest.raises(ReconciliationInputError):
        reconcile(value, ())


@pytest.mark.parametrize(
    "group",
    (
        ProposedGroup((), "new", title="Empty group"),
        ProposedGroup(["O-1"], "new", title="Mutable IDs"),  # type: ignore[arg-type]
    ),
    ids=("empty", "mutable-observation-ids"),
)
def test_invalid_proposal_shape_is_rejected_before_semantic_mapping(
    group: ProposedGroup,
) -> None:
    with pytest.raises(ReconciliationInputError):
        reconcile(_input(_observation(1)), (group,))


def test_reconciler_requirements_are_frozen_and_do_not_choose_a_profile() -> None:
    requirements = RECONCILER_REQUIREMENTS
    assert (
        requirements.role,
        requirements.fresh_session,
        requirements.context_policy,
        requirements.record_exposure,
    ) == ("reconciler", True, "explicit", True)
    assert requirements.include_unclassified_observations is True
    assert requirements.include_ledger is True
    assert requirements.include_direct_followups is False
    assert not hasattr(requirements, "profile_id")
    with pytest.raises(FrozenInstanceError):
        requirements.role = "reviewer"  # type: ignore[misc]


def test_t1_5_domain_imports_only_stdlib_and_its_own_modules() -> None:
    review_dir = Path(__file__).resolve().parents[3] / "src/metaswarm/domain/review"
    modules = ("dedup.py", "reconcile_model.py", "reconciliation.py")
    forbidden_absolute = {
        "asyncio",
        "metaswarm",
        "pathlib",
        "socket",
        "sqlite3",
        "subprocess",
    }
    for module in modules:
        tree = ast.parse((review_dir / module).read_text(encoding="utf-8"))
        for node in tree.body:
            value = None
            if isinstance(node, ast.Assign):
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                value = node.value
            assert not isinstance(value, (ast.List, ast.Dict, ast.Set))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".", 1)[0] not in forbidden_absolute
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    assert node.module.split(".", 1)[0] not in forbidden_absolute
                if node.level:
                    assert node.level == 1
                    assert node.module == "reconcile_model"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "input", "open", "print"}
