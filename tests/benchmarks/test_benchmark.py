"""WP2-12 benchmark suite, protocol, bounds and Hall of Fame.

What these tests are for: a benchmark's value is entirely in whether its rules were fixed
before its results and whether its absences are visible. So the tests here are mostly about
integrity rather than arithmetic -- the split cannot leak, the protocol cannot drift
unnoticed, a stale best-known cannot be compared against a fresh result, a missing number
cannot render as zero, and an agent cannot write the Hall of Fame.

Owner: Yash (lane: benchmarks/, tests/).
"""

from __future__ import annotations

import json

import pytest

from benchmarks import hof, lower_bound, protocol, runner, suite
from contracts.validate import validate

# The pinned protocol hash. Changing the suite, the splits, the budgets, the seeds or the
# convergence threshold changes this value and invalidates every recorded result, which is
# why it is RED (AGENTS.md section 3). If this test fails, that is the point: say what
# changed and why, get Yash's OK, re-run best-known, then update the constant.
PINNED_PROTOCOL_HASH = "sha256:b85b5c0b890fabc4b9e29f9316d28f457c5ae20254154eaae83924bc3c6cea6d"


# ---------------------------------------------------------------------------
# The suite


def test_the_suite_is_large_enough():
    """HANDOFF_PHASE2 WP2-12 asks for at least twelve missions."""
    assert len(suite.missions()) >= 12


def test_every_suite_mission_is_a_valid_mission_request():
    for m in suite.missions():
        validate("mission_request", m)


def test_mission_ids_are_unique():
    ids = suite.mission_ids()
    assert len(set(ids)) == len(ids)


def test_the_suite_is_identical_on_every_call():
    """A suite that varies between calls cannot be hashed, and nothing measured on it means
    anything."""
    first = json.dumps(suite.missions(), sort_keys=True)
    suite.missions.cache_clear()
    assert json.dumps(suite.missions(), sort_keys=True) == first


def test_crew_missions_fly_a_crew_rated_vehicle():
    for m in suite.missions():
        if m["profile"] == "crew_iss":
            vehicle = suite._vehicle(m["rocket_id"])
            assert vehicle["crew_rated"], f"{m['id']}: {m['rocket_id']} is not crew-rated"
            assert m["payload"]["crew"] >= 1


def test_payloads_come_from_published_figures_not_simulated_ones():
    """Sizing the suite off our own simulator's output would make it measure itself."""
    for m in suite.missions():
        vehicle = suite._vehicle(m["rocket_id"])
        published = float(vehicle["payload_to_leo_kg_published"])
        fraction = m["payload"]["mass_kg"] / published
        assert any(
            abs(fraction - f) < 1e-6 for f in suite.PAYLOAD_FRACTIONS.values()
        ), f"{m['id']}: payload is not a declared fraction of the published figure"


def test_the_suite_spans_more_than_one_vehicle_and_profile():
    """A benchmark of one vehicle on one profile measures one thing."""
    assert len({m["rocket_id"] for m in suite.missions()}) >= 3
    assert len({m["profile"] for m in suite.missions()}) >= 2


def test_the_suite_includes_the_vehicle_the_simulator_models_worst():
    """atlas_v_401 is at -25% against its published payload and its missions are the hardest
    to make feasible. A suite containing only missions we can already solve measures nothing,
    so its presence is deliberate and this test says so."""
    assert any(m["rocket_id"] == "atlas_v_401" for m in suite.missions())


def test_every_system_gets_the_same_budget():
    budgets = {
        (m["budget"]["max_attempts"], m["budget"]["max_wall_s"]) for m in suite.missions()
    }
    assert len(budgets) == 1, f"missions carry different budgets: {budgets}"


# ---------------------------------------------------------------------------
# The split


def test_the_splits_cover_every_mission_exactly_once():
    sp = protocol.splits()
    flat = [m for ids in sp.values() for m in ids]
    assert sorted(flat) == sorted(suite.mission_ids())
    assert len(set(flat)) == len(flat), "a mission is in two splits (leakage)"


def test_no_launch_window_appears_in_two_splits():
    """This is what makes "held out" mean something here: a system cannot have been tuned on
    the phasing geometry it is tested on."""
    windows = {
        name: {suite.mission_by_id(m)["window_id"] for m in ids}
        for name, ids in protocol.splits().items()
    }
    for a, b in (("train", "val"), ("train", "test"), ("val", "test")):
        overlap = windows[a] & windows[b]
        assert not overlap, f"windows {sorted(overlap)} are in both {a} and {b}"


def test_every_split_contains_every_vehicle_and_both_profiles():
    """Otherwise a system could score well on the test split by being good at one vehicle."""
    for name, ids in protocol.splits().items():
        ms = [suite.mission_by_id(m) for m in ids]
        assert len({m["rocket_id"] for m in ms}) == 3, f"{name}: not all vehicles"
        assert len({m["profile"] for m in ms}) == 2, f"{name}: not both profiles"


def test_every_split_spans_the_phase_circle():
    """A split whose windows all sit at a similar phase angle measures one geometry."""
    angles = _phase_angles()
    for name, ids in protocol.splits().items():
        got = sorted(angles[suite.mission_by_id(m)["window_id"]] for m in ids)
        assert max(got) - min(got) > 120.0, f"{name}: phase angles span only {got}"


def _phase_angles() -> dict[str, float]:
    path = suite.ROOT / "data" / "results" / "windows_cargo_iss_cape_canaveral.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {w["id"]: float(w["phase_angle_deg"]) for w in data["windows"]}


def test_the_declared_windows_are_the_ones_the_data_has():
    """The suite's comments quote phase angles; the window ids must actually exist."""
    angles = _phase_angles()
    for combo in suite.COMBINATIONS:
        for split, wid in combo["windows"].items():
            assert wid in angles, f"{combo['rocket_id']} {split}: window {wid} is not in the data"


# ---------------------------------------------------------------------------
# The protocol hash


def test_the_protocol_hash_is_pinned():
    """Guards against the protocol changing by accident.

    If this fails, something in the suite, splits, budgets, seeds or convergence threshold
    moved. Every recorded result was measured under the old one and is no longer comparable.
    That is a RED change: say what moved, get Yash's OK, re-run best-known, update the pin.
    """
    assert protocol.protocol_hash() == PINNED_PROTOCOL_HASH, (
        "The benchmark protocol changed. Recorded results are no longer comparable.\n"
        f"  was: {PINNED_PROTOCOL_HASH}\n  now: {protocol.protocol_hash()}"
    )


def test_the_hash_covers_the_missions_themselves_not_just_their_ids():
    """An id is a label. If a mission's payload changed underneath it, every recorded cost
    would change meaning while the id stayed the same."""
    before = protocol.protocol_hash()
    doc = protocol.protocol_document()
    doc["missions"][0]["payload"]["mass_kg"] += 1.0
    import hashlib

    blob = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    mutated = "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()
    assert mutated != before


def test_the_hash_is_stable_across_calls():
    assert protocol.protocol_hash() == protocol.protocol_hash()


# ---------------------------------------------------------------------------
# Gaps and convergence: an absent number is never zero


def test_a_missing_best_known_gives_a_null_gap_not_zero():
    """Charter addition 12. A 0% gap reads as "matches the best we know of"."""
    assert protocol.gap_pct(1.2, None) is None
    assert protocol.gap_pct(None, 1.2) is None
    assert protocol.gap_pct(None, None) is None


def test_a_gap_is_positive_when_a_system_is_worse_than_best_known():
    assert protocol.gap_pct(1.1, 1.0) == pytest.approx(10.0)
    assert protocol.gap_pct(1.0, 1.0) == pytest.approx(0.0)


def test_convergence_needs_both_closeness_and_several_seeds():
    """One lucky seed inside epsilon is not convergence (HANDOFF section 5.3)."""
    assert protocol.is_converged(1.0, protocol.MIN_CONVERGED_SEEDS) is True
    assert protocol.is_converged(1.0, protocol.MIN_CONVERGED_SEEDS - 1) is False
    assert protocol.is_converged(protocol.CONVERGENCE_EPS_PCT + 0.1, 5) is False
    assert protocol.is_converged(None, 5) is False


# ---------------------------------------------------------------------------
# The analytic lower bound


@pytest.mark.parametrize("m", suite.missions(), ids=lambda m: m["id"])
def test_the_lower_bound_is_positive_and_finite(m):
    b = lower_bound.for_mission(m)
    for key in ("dv_mps", "prop_kg", "cost"):
        assert b[key] > 0.0 and b[key] < float("inf"), f"{key} = {b[key]}"


def test_a_higher_latitude_pad_has_a_higher_ascent_floor():
    """Less free eastward velocity from Earth's rotation. mars_lp0a is at 37.84 N against
    28.5 N at the Cape, and the bound has to show it."""
    cape = lower_bound.ascent_dv_floor_mps(28.562)
    wallops = lower_bound.ascent_dv_floor_mps(37.843)
    assert wallops > cape


def test_the_lower_bound_uses_no_simulation():
    """It must be computable with the simulator broken -- that is what makes it a bound on
    the simulator's results rather than one of them."""
    import ascent.sim as sim

    original = sim.simulate_ascent
    sim.simulate_ascent = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the lower bound called the simulator")
    )
    try:
        lower_bound.for_mission(suite.missions()[0])
    finally:
        sim.simulate_ascent = original


def test_the_bound_sits_below_a_real_feasible_cost():
    """The floor's whole job. A measured feasible cost below it would mean a bug in the
    simulator, the verifier or the bound itself."""
    from optimize.cma_optimizer import optimize_mission

    m = dict(suite.mission_by_id("msn_bench_cargo_f9_w3_light"))
    m["seed"] = 11
    best = optimize_mission(
        mission_request=m, max_attempts=30, seed=11, session_log_path=None, save_attempts=False
    )
    if not best["feasible"]:
        pytest.skip("no feasible attempt in 30 evaluations; nothing to compare the floor with")
    floor = lower_bound.for_mission(m)["cost"]
    assert floor < best["cost"], (
        f"the analytic floor {floor:.6f} is not below a measured feasible cost "
        f"{best['cost']:.6f} -- one of them is wrong"
    )


# ---------------------------------------------------------------------------
# The Hall of Fame


def test_the_hall_of_fame_is_readable_without_a_capability():
    assert "entries" in hof.read()


def test_a_writer_cannot_be_constructed_directly():
    with pytest.raises(hof.HofError, match="cannot be constructed directly"):
        hof.Writer("sha256:anything")


def test_a_stale_protocol_hash_cannot_open_the_hall_of_fame():
    """Results from two protocols must not share one file."""
    with pytest.raises(hof.HofError, match="protocol hash mismatch"):
        hof.open_for_runner("sha256:not-the-live-protocol")


def test_an_infeasible_attempt_is_never_recorded_as_best_known():
    """An infeasible cost is a constraint penalty. Letting one in would make every gap
    computed against it meaningless."""
    w = hof.open_for_runner(protocol.protocol_hash())
    with pytest.raises(hof.HofError, match="infeasible"):
        w.record(
            "msn_bench_crew_f9_w0_light", cost=4231.0, attempt_id="att_x", budget_sims=1,
            seeds=[1], sim_version="s", verifier_version="v", feasible=False,
        )


def test_best_cost_of_an_unknown_mission_is_none_not_zero():
    assert hof.best_cost("msn_does_not_exist") is None


def test_a_best_known_from_another_simulator_is_reported_as_stale(tmp_path, monkeypatch):
    """Charter addition 4 has to bite here: a cost from a different simulator looks exactly
    like a cost from this one."""
    monkeypatch.setattr(hof, "HOF_PATH", tmp_path / "hof.json")
    w = hof.open_for_runner(protocol.protocol_hash())
    w.record(
        "msn_bench_crew_f9_w0_light", cost=1.2, attempt_id="att_old", budget_sims=10,
        seeds=[11], sim_version="env-OLD", verifier_version="ver-OLD", feasible=True,
    )
    status = hof.verify_entries("env-NEW", "ver-NEW")
    assert status["stale"] == ["msn_bench_crew_f9_w0_light"]
    assert status["current"] == []


def test_a_better_result_supersedes_and_records_what_it_beat(tmp_path, monkeypatch):
    monkeypatch.setattr(hof, "HOF_PATH", tmp_path / "hof.json")
    w = hof.open_for_runner(protocol.protocol_hash())
    common = dict(
        attempt_id="att", budget_sims=10, seeds=[11], sim_version="s",
        verifier_version="v", feasible=True,
    )
    assert w.record("msn_a", cost=1.5, **common) is True
    assert w.record("msn_a", cost=1.9, **common) is False, "a worse result overwrote a better one"
    assert w.record("msn_a", cost=1.1, **common) is True
    entry = hof.read()["entries"]["msn_a"]
    assert entry["cost"] == 1.1
    assert entry["superseded"]["cost"] == 1.5
    assert entry["superseded"]["reason"] == "beaten"


# ---------------------------------------------------------------------------
# The runner's honesty about what it did not measure


def test_a_system_that_cannot_run_is_reported_as_not_run_with_a_reason():
    """Never a blank, never a zero. An empty cell in a benchmark table reads as a result."""
    for system, reason in runner.NOT_RUNNABLE.items():
        rep = runner.run_system(system, "val", progress=False)
        assert rep["status"] == "not_run"
        assert rep["reason"] == reason and len(reason) > 20
        assert rep["missions"] == {}


def test_every_protocol_system_is_either_runnable_or_has_a_stated_reason():
    for system in protocol.SYSTEMS:
        runnable = system not in runner.NOT_RUNNABLE
        assert runnable or runner.NOT_RUNNABLE[system]


def test_the_benchmark_file_validates_against_its_contract():
    doc = runner.build_benchmark_file(write=False)
    validate("benchmark", doc)
    assert doc["protocol_hash"] == protocol.protocol_hash()
    assert set(doc["splits"]) == {"train", "val", "test"}
    assert set(doc["lower_bound"]) == set(suite.mission_ids())


def test_the_benchmark_file_omits_best_known_measured_under_other_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(hof, "HOF_PATH", tmp_path / "hof.json")
    w = hof.open_for_runner(protocol.protocol_hash())
    w.record(
        "msn_bench_crew_f9_w0_light", cost=1.2, attempt_id="att_old", budget_sims=10,
        seeds=[11], sim_version="env-OLD", verifier_version="ver-OLD", feasible=True,
    )
    doc = runner.build_benchmark_file(write=False)
    assert "msn_bench_crew_f9_w0_light" not in doc["best_known"], (
        "a best-known cost from a different simulator was presented as current"
    )
    assert "omitted rather than silently compared" in doc["_note"]
