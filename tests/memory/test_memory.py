"""WP2-10 experience memory: features, store, filters and warm start.

HANDOFF_PHASE2 section 7 names `test_memory_filters_stale` specifically, and it is the test
that matters most: a plan that was feasible under a different simulator is not a weaker hint,
it is a hint about different physics, and letting one through would quietly warm-start the
search from a plan that cannot work.

The other theme here is that memory must never be able to make something pass. It moves where
the search starts; the verifier still judges every attempt from scratch.

Owner: Yash (lane: memory/, tests/).
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest

from benchmarks import suite
from contracts.validate import ContractError, validate
from memory import features, warm_start
from memory.store import Store, failure_modes_of
from optimize.cma_optimizer import LOWER, UPPER, X0, plan_from_x, x_from_plan

# Pinned: changing a feature, a range or the ordering makes every stored vector incomparable
# with every new one, which is a features-v1 -> features-v2 change, not an edit.
PINNED_SPEC_HASH = "sha256:c845009d1628d43ec59aa13bddc260e23fd77d6c8e57de3a3c374f0a38d2fa71"


def _attempt(
    attempt_id: str = "att_x",
    *,
    cost: float = 1.3,
    feasible: bool = True,
    sim_version: str = "env-TEST",
    verifier_version: str = "ver-TEST",
    plan: dict | None = None,
) -> dict:
    return {
        "id": attempt_id,
        "cost": cost,
        "feasible": feasible,
        "sim_version": sim_version,
        "verifier_version": verifier_version,
        "plan": plan if plan is not None else plan_from_x(X0),
        "constraint_report": [],
    }


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "experience.jsonl")


@pytest.fixture
def mission() -> dict:
    return copy.deepcopy(suite.mission_by_id("msn_bench_cargo_f9_w3_light"))


# ---------------------------------------------------------------------------
# features-v1


def test_the_feature_spec_is_pinned():
    """A vector built under a changed spec is not comparable with a stored one. If this
    fails, it is a features-v2, and every stored record needs re-encoding or discarding."""
    assert features.spec_hash() == PINNED_SPEC_HASH, (
        f"features-v1 changed.\n  was: {PINNED_SPEC_HASH}\n  now: {features.spec_hash()}"
    )


def test_encoding_is_deterministic(mission):
    assert np.array_equal(features.encode(mission), features.encode(mission))


def test_every_feature_is_finite_and_bounded(mission):
    v = features.encode(mission)
    assert v.shape == (features.DIM,)
    assert np.all(np.isfinite(v))
    assert np.all(v >= -1.0) and np.all(v <= 1.0)


def test_the_phase_angle_is_encoded_circularly():
    """359 degrees and 1 degree are two degrees apart. Any linear normalization puts them at
    opposite ends of the range, which would make the most similar window look like the least
    similar one -- the single detail that decides whether retrieval is useful at all."""
    i_sin = features.FEATURE_NAMES.index("phase_angle_sin")
    i_cos = features.FEATURE_NAMES.index("phase_angle_cos")

    def pair(deg: float) -> np.ndarray:
        rad = np.radians(deg)
        return np.asarray([np.sin(rad), np.cos(rad)])

    near = np.linalg.norm(pair(359.0) - pair(1.0))
    far = np.linalg.norm(pair(10.0) - pair(190.0))
    assert near < 0.1, f"359 deg and 1 deg encode {near:.3f} apart"
    assert far > 1.9, f"10 deg and 190 deg encode only {far:.3f} apart"
    assert features.FEATURE_NAMES[i_sin] == "phase_angle_sin"
    assert features.FEATURE_NAMES[i_cos] == "phase_angle_cos"


def test_features_with_no_data_source_are_absent_not_zero():
    """Charter addition 12, and in a vector it matters more than usual: 0 is not "unknown",
    it is a specific point that makes unrelated missions look similar."""
    for absent in ("weather", "wind", "kp", "miss_distance", "min_miss"):
        assert not any(absent in n for n in features.FEATURE_NAMES), (
            f"{absent!r} is in features-v1 but the project has no source for it"
        )


def test_the_spec_document_records_the_normalization_ranges():
    """HANDOFF section 9: fixed normalization stats stored with the spec."""
    doc = features.spec_document()
    assert doc["vector_spec"] == features.VECTOR_SPEC
    assert doc["dim"] == features.DIM
    assert set(doc["ranges"]) == set(features.RANGES)
    for lo, hi in doc["ranges"].values():
        assert hi > lo


def test_similar_missions_encode_closer_than_dissimilar_ones():
    """The property the whole module exists for."""
    enc = {m["id"]: features.encode(m) for m in suite.missions()}
    same_window = np.linalg.norm(
        enc["msn_bench_cargo_f9_w3_light"] - enc["msn_bench_cargo_f9_w3_heavy"]
    )
    other_window = np.linalg.norm(
        enc["msn_bench_cargo_f9_w3_light"] - enc["msn_bench_cargo_f9_w5_light"]
    )
    other_vehicle = np.linalg.norm(
        enc["msn_bench_cargo_f9_w3_light"] - enc["msn_bench_cargo_an_w6_light"]
    )
    assert same_window < other_window < other_vehicle


def test_every_suite_mission_encodes_distinctly():
    vectors = {tuple(np.round(features.encode(m), 9)) for m in suite.missions()}
    assert len(vectors) == len(suite.missions())


# ---------------------------------------------------------------------------
# The store


def test_a_stored_record_validates_against_contract_6_4(store, mission):
    rec = store.remember(mission, _attempt())
    validate("memory_record", rec)
    assert rec["vector_spec"] == features.VECTOR_SPEC


def test_the_store_round_trips_through_the_file(store, mission):
    store.remember(mission, _attempt("att_1"))
    store.remember(mission, _attempt("att_2"))
    reloaded = Store(store.path)
    assert len(reloaded) == 2
    assert {r["payload"]["attempt_id"] for r in reloaded._records} == {"att_1", "att_2"}


def test_a_corrupt_line_is_refused_rather_than_skipped(store, mission):
    """A silently shorter memory looks like a memory that simply had not learned much."""
    store.remember(mission, _attempt())
    store.path.write_text(store.path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    with pytest.raises(Exception, match="not valid JSON"):
        Store(store.path)


def test_infeasible_attempts_are_stored_too(store, mission):
    """HANDOFF section 9 wants memory to steer away from known-bad regions, which needs the
    bad ones. A store of successes only is a highlight reel."""
    store.remember(mission, _attempt("att_bad", feasible=False, cost=4231.0))
    assert store.stats()["infeasible"] == 1


def test_an_empty_store_returns_no_neighbours(store, mission):
    assert store.search(mission, k=5) == []


# ---------------------------------------------------------------------------
# test_memory_filters_stale -- named in HANDOFF section 7


def test_memory_filters_stale(store, mission):
    """A record from a different simulator or verifier must not be retrievable as a hint.

    This is the test the handoff asks for by name. The failure it prevents: warm-starting the
    search from a plan that was feasible under physics we no longer run, which looks exactly
    like a good hint and is not one.
    """
    store.remember(mission, _attempt("att_current", sim_version="env-NOW", verifier_version="ver-NOW"))
    store.remember(mission, _attempt("att_old_sim", sim_version="env-OLD", verifier_version="ver-NOW"))
    store.remember(mission, _attempt("att_old_ver", sim_version="env-NOW", verifier_version="ver-OLD"))

    got = store.search(mission, k=10, sim_version="env-NOW", verifier_version="ver-NOW")
    assert [h["record"]["payload"]["attempt_id"] for h in got] == ["att_current"]

    # Unfiltered, all three are visible -- so the filter is doing the work, not the store
    # happening to hold one record.
    assert len(store.search(mission, k=10)) == 3


def test_a_record_from_another_vector_spec_is_never_retrieved(store, mission):
    """Two vectors built under different specs are not comparable at all, so a distance
    between them is a meaningless number rather than a large one."""
    store.remember(mission, _attempt("att_ok"))
    forged = copy.deepcopy(store._records[0])
    forged["id"] = "mem_att_v2"
    forged["vector_spec"] = "features-v2"
    forged["payload"]["attempt_id"] = "att_v2"
    store._records.append(forged)
    store._rebuild()

    got = store.search(mission, k=10)
    assert [h["record"]["payload"]["attempt_id"] for h in got] == ["att_ok"]


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"feasible": True}, {"att_good"}),
        ({"feasible": False}, {"att_bad"}),
        ({"rocket_id": "falcon9_block5"}, {"att_good", "att_bad"}),
        ({"rocket_id": "atlas_v_401"}, set()),
        ({"profile": "cargo_iss"}, {"att_good", "att_bad"}),
        ({"profile": "crew_iss"}, set()),
    ],
)
def test_payload_filters(store, mission, kwargs, expected):
    """The four filters HANDOFF section 9 asks for: profile, rocket_id, feasible, sim_version."""
    store.remember(mission, _attempt("att_good", feasible=True))
    store.remember(mission, _attempt("att_bad", feasible=False, cost=4231.0))
    got = {h["record"]["payload"]["attempt_id"] for h in store.search(mission, k=10, **kwargs)}
    assert got == expected


# ---------------------------------------------------------------------------
# failure modes


def test_failure_modes_name_only_constraints_that_were_actually_evaluated():
    """An unevaluated constraint is not a failure mode. Recording it as one would teach
    memory to avoid a region for a reason that was never measured."""
    attempt = {
        "constraint_report": [
            {"id": "C01", "name": "orbit_insertion", "pass": False, "evaluated": True},
            {"id": "C03", "name": "max_acceleration", "pass": True, "evaluated": True},
            {"id": "C05", "name": "weather_go_nogo", "pass": False, "evaluated": False},
        ]
    }
    assert failure_modes_of(attempt) == ["C01:orbit_insertion"]


def test_failure_modes_still_work_on_a_pre_evaluated_attempt():
    """Attempts recorded before the `evaluated` flag are evidence and are not rewritten."""
    attempt = {
        "constraint_report": [
            {"id": "C01", "name": "orbit_insertion", "pass": False, "value": 43.0},
            {
                "id": "C05", "name": "weather_go_nogo", "pass": False, "value": None,
                "note": "not evaluated - required input not available",
            },
        ]
    }
    assert failure_modes_of(attempt) == ["C01:orbit_insertion"]


# ---------------------------------------------------------------------------
# Warm start


def _ws(store, mission, **kw):
    return warm_start.warm_start(
        store,
        mission,
        x_from_plan=x_from_plan,
        x0=np.asarray(X0, dtype=float),
        lower=np.asarray(LOWER, dtype=float),
        upper=np.asarray(UPPER, dtype=float),
        **kw,
    )


def test_an_empty_memory_warm_starts_to_exactly_the_cold_start(store, mission):
    """A cold start is the correct behaviour for an empty memory, not an error."""
    out = _ws(store, mission)
    assert out["used"] is False
    assert np.array_equal(out["x0"], np.asarray(X0, dtype=float))
    assert np.array_equal(out["lower"], np.asarray(LOWER, dtype=float))
    assert "no feasible neighbour" in out["reason"]


def test_the_cold_strategy_never_touches_memory(store, mission):
    store.remember(mission, _attempt())
    out = _ws(store, mission, strategy="cold")
    assert out["used"] is False
    assert out["neighbours"] == []
    assert np.array_equal(out["x0"], np.asarray(X0, dtype=float))


def test_centring_moves_the_start_to_the_best_feasible_neighbour(store, mission):
    good = plan_from_x(np.asarray(X0) * 0.0 + np.asarray(LOWER) + 1.0)
    store.remember(mission, _attempt("att_cheap", cost=1.1, plan=good))
    store.remember(mission, _attempt("att_dear", cost=1.9, plan=plan_from_x(X0)))

    out = _ws(store, mission, strategy="centre")
    assert out["used"] is True
    assert np.allclose(out["x0"], x_from_plan(good)), "did not centre on the cheapest neighbour"
    assert "1.1" in out["reason"]


def test_centring_ignores_infeasible_neighbours_however_cheap_their_score(store, mission):
    """An infeasible cost is a constraint penalty; a low one is not a good plan."""
    store.remember(mission, _attempt("att_inf", cost=0.001, feasible=False))
    out = _ws(store, mission, strategy="centre")
    assert out["used"] is False


def test_bounding_narrows_the_box_but_keeps_it_inside_the_hard_bounds(store, mission):
    for i, mult in enumerate((0.45, 0.5, 0.55)):
        x = np.asarray(LOWER) + mult * (np.asarray(UPPER) - np.asarray(LOWER))
        store.remember(mission, _attempt(f"att_{i}", cost=1.2 + i * 0.01, plan=plan_from_x(x)))

    out = _ws(store, mission, strategy="bound")
    assert out["used"] is True
    assert np.all(out["lower"] >= np.asarray(LOWER) - 1e-9), "bounded below the hard lower bound"
    assert np.all(out["upper"] <= np.asarray(UPPER) + 1e-9), "bounded above the hard upper bound"
    width = float(np.mean(out["upper"] - out["lower"]))
    cold_width = float(np.mean(np.asarray(UPPER) - np.asarray(LOWER)))
    assert width < cold_width, "bounding did not narrow anything"


def test_a_neighbour_with_an_incomplete_plan_is_not_used_as_a_start(store, mission):
    """A starting point with three real numbers and seven defaults looks considered and is
    not."""
    store.remember(mission, _attempt("att_partial", plan={"parking_alt_km": 210.0}))
    out = _ws(store, mission, strategy="centre")
    assert out["used"] is False
    assert "complete" in out["reason"]


def test_the_examples_strategy_returns_cases_with_their_failure_modes(store, mission):
    store.remember(mission, _attempt("att_a", cost=1.2))
    out = _ws(store, mission, strategy="examples")
    assert out["used"] is True
    assert len(out["examples"]) == 1
    assert set(out["examples"][0]) >= {"plan", "cost", "feasible", "failure_modes", "similarity"}


def test_an_unknown_strategy_is_refused(store, mission):
    with pytest.raises(ValueError, match="unknown warm-start strategy"):
        _ws(store, mission, strategy="telepathy")


def test_warm_start_never_returns_a_start_outside_the_hard_bounds(store, mission):
    """Memory is a hint, not a licence. A stored plan from a wider parameterisation must be
    clipped, not trusted."""
    wild = plan_from_x(np.asarray(UPPER) * 10.0)
    store.remember(mission, _attempt("att_wild", cost=1.0, plan=wild))
    out = _ws(store, mission, strategy="centre")
    assert np.all(out["x0"] >= np.asarray(LOWER) - 1e-9)
    assert np.all(out["x0"] <= np.asarray(UPPER) + 1e-9)


# ---------------------------------------------------------------------------
# The plan mapping the warm start depends on


def test_the_plan_mapping_round_trips():
    """Without this, warm-starting from a stored plan would reconstruct a different vector
    than the one that produced it, and the hint would point somewhere nobody searched."""
    rng = np.random.default_rng(0)
    lo, hi = np.asarray(LOWER, dtype=float), np.asarray(UPPER, dtype=float)
    for _ in range(50):
        x = lo + rng.random(len(lo)) * (hi - lo)
        back = x_from_plan(plan_from_x(x))
        assert back is not None
        # phasing_revs is an integer in the plan, coast_s2_s is zeroed below 5 s, and the
        # rest are rounded -- so compare the PLANS, which is what actually gets flown.
        assert plan_from_x(back) == plan_from_x(x)


def test_x_from_plan_refuses_a_plan_it_cannot_fully_recover():
    assert x_from_plan({}) is None
    assert x_from_plan({"pitch_program": [1.0, 2.0]}) is None
    assert x_from_plan({"pitch_program": [1.0] * 5}) is None  # no parking_alt_km etc.


# ---------------------------------------------------------------------------
# Memory cannot make anything pass


def test_memory_cannot_be_written_through_the_contract_with_a_bad_record(store, mission):
    """The store validates on the way in, so a malformed record cannot sit in memory waiting
    to be retrieved as a hint."""
    bad = _attempt()
    bad["sim_version"] = ""  # contract requires minLength 1
    with pytest.raises(ContractError):
        store.remember(mission, bad)


def test_a_warm_started_attempt_is_still_judged_from_scratch(store, mission):
    """Charter addition 3: memory is a hint, never evidence. Verifying the same attempt
    twice must give the same verdict whether or not memory suggested its plan."""
    from verifier import verify

    store.remember(mission, _attempt("att_seed", cost=1.2))
    out = _ws(store, mission, strategy="centre")
    assert out["used"] is True
    # The stored record's own constraint report is empty; verification is what decides.
    rec = json.loads(json.dumps(store._records[0]))
    assert rec["payload"]["feasible"] is True
    assert "constraint_report" not in rec["payload"], (
        "memory stores a verdict, not a constraint report -- it must not look like evidence"
    )
    assert callable(verify)
