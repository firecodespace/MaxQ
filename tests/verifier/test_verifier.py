"""WP2-04 Verifier acceptance tests.

Acceptance tests per HANDOFF_PHASE2 §7:
  test_verifier_deterministic
  test_verifier_catches_violation
  test_cost_orders_correctly  (any feasible attempt beats any infeasible one)
"""

from __future__ import annotations

import pytest

from verifier import VERIFIER_VERSION, verify

# ---------------------------------------------------------------------------
# Fixtures


def _feasible_attempt() -> dict:
    """A minimal attempt that passes every evaluatable constraint."""
    return {
        "schema_version": 1,
        "id": "att_test_001",
        "mission_id": "msn_test",
        "agent": {"id": "test-agent", "model": None, "version": "0.0.1"},
        "sim_version": "env-0.0.1",
        "verifier_version": "ver-placeholder",
        "seed": 42,
        "plan": {
            "launch_t_unix": 1794372400,
            "pitch_program": [0.0, 0.0],
            "parking_alt_km": 200.0,
            "burns": [],
            "waypoints": [],
        },
        "phases": [
            {
                "name": "ascent",
                "t0_s": 0.0,
                "t1_s": 520.0,
                "prop_kg": 350000.0,
                # Extra fields for verifier (not in schema; allowed in attempt dict before writing)
                "perigee_km": 199.0,
                "eccentricity": 0.001,
            },
        ],
        "metrics": {
            "prop_used_kg": 350000.0,
            "total_dv_mps": 9400.0,
            "time_to_dock_s": 10800.0,  # 3 hours
            "max_q_pa": 30000.0,         # 30 kPa
            "max_accel_g": 3.5,
            "perigee_km": 199.0,
            "eccentricity": 0.001,
            "ascent_reserve_frac": 0.05,
            "plane_error_deg": 0.1,
        },
        "constraint_report": [],
        "feasible": False,
        "cost": 0.0,
        "best_known_gap_pct": None,
        "trajectory_ref": None,
        "wall_s": 1.5,
        "status": "finished",
        # Objective so cost is meaningful
        "objective": {
            "w_fuel": 1.0,
            "w_time": 0.5,
            "fuel_ref_kg": 500000.0,
            "time_ref_s": 86400.0,
        },
        # Config fields for constraints that need extra data
        "config": {
            "max_q_limit_kpa": 50.0,     # vehicle limit → C02 evaluatable
            "plane_error_deg": 0.1,
            "risk_status": "go",
            "prop_total_kg": 500000.0,
        },
    }


def _infeasible_attempt() -> dict:
    """A minimal attempt that violates C01 (perigee too low)."""
    a = _feasible_attempt()
    a["phases"][0]["perigee_km"] = 100.0   # below 160 km limit
    a["phases"][0]["eccentricity"] = 0.001
    a["metrics"]["perigee_km"] = 100.0
    return a


# ---------------------------------------------------------------------------
# test_verifier_deterministic


def test_verifier_deterministic():
    """Same attempt + same profile ⇒ identical constraint_report, feasible, cost."""
    attempt = _feasible_attempt()
    r1 = verify(attempt, "crew_default")
    r2 = verify(attempt, "crew_default")

    assert r1["feasible"] == r2["feasible"]
    assert r1["cost"] == r2["cost"]
    assert r1["verifier_version"] == r2["verifier_version"]
    assert len(r1["constraint_report"]) == len(r2["constraint_report"])
    for c1, c2 in zip(r1["constraint_report"], r2["constraint_report"]):
        assert c1 == c2, f"Non-deterministic: {c1} vs {c2}"


def test_verifier_version_is_string_and_stable():
    """VERIFIER_VERSION is a stable string that starts with 'ver-'."""
    assert isinstance(VERIFIER_VERSION, str)
    assert VERIFIER_VERSION.startswith("ver-")
    # Calling verify twice gives the same version.
    r1 = verify(_feasible_attempt())
    r2 = verify(_feasible_attempt())
    assert r1["verifier_version"] == r2["verifier_version"] == VERIFIER_VERSION


# ---------------------------------------------------------------------------
# test_verifier_catches_violation


def test_verifier_catches_c01_perigee_violation():
    """C01 fails when perigee < 160 km."""
    attempt = _infeasible_attempt()
    result = verify(attempt, "crew_default")

    assert result["feasible"] is False
    c01 = next(c for c in result["constraint_report"] if c["id"] == "C01")
    assert c01["pass"] is False
    assert c01["value"] < 160.0


def test_verifier_catches_c03_accel_violation():
    """C03 fails when max_accel_g exceeds crew limit (4 g)."""
    attempt = _feasible_attempt()
    attempt["metrics"]["max_accel_g"] = 5.5  # exceeds crew 4.0 g limit
    result = verify(attempt, "crew_default")

    assert result["feasible"] is False
    c03 = next(c for c in result["constraint_report"] if c["id"] == "C03")
    assert c03["pass"] is False


def test_verifier_catches_c04_plane_error_violation():
    """C04 fails when plane error exceeds crew limit (1.0 deg)."""
    attempt = _feasible_attempt()
    attempt["metrics"]["plane_error_deg"] = 2.0
    attempt["config"]["plane_error_deg"] = 2.0
    result = verify(attempt, "crew_default")

    assert result["feasible"] is False
    c04 = next(c for c in result["constraint_report"] if c["id"] == "C04")
    assert c04["pass"] is False


def test_verifier_catches_multiple_violations():
    """Multiple constraint violations all appear in the report."""
    attempt = _feasible_attempt()
    attempt["metrics"]["perigee_km"] = 100.0
    attempt["phases"][0]["perigee_km"] = 100.0
    attempt["metrics"]["max_accel_g"] = 7.0
    result = verify(attempt, "crew_default")

    assert result["feasible"] is False
    failed_ids = [c["id"] for c in result["constraint_report"] if not c["pass"]]
    assert "C01" in failed_ids
    assert "C03" in failed_ids


# ---------------------------------------------------------------------------
# Not-evaluated constraints


def test_c05_weather_not_evaluated_without_data():
    """C05 is not evaluated (pass=False) when no weather data is supplied."""
    attempt = _feasible_attempt()
    del attempt["config"]["risk_status"]   # still check C05 specifically
    result = verify(attempt, "crew_default")

    c05 = next(c for c in result["constraint_report"] if c["id"] == "C05")
    assert c05["pass"] is False
    assert "not evaluated" in c05.get("note", "").lower()
    assert c05["value"] is None


def test_c09_c10_not_evaluated_without_approach_data():
    """C09 and C10 report not-evaluated without approach telemetry."""
    attempt = _feasible_attempt()
    result = verify(attempt, "crew_default")

    for cid in ("C09", "C10"):
        cr = next(c for c in result["constraint_report"] if c["id"] == cid)
        assert cr["pass"] is False
        assert "not evaluated" in cr.get("note", "").lower()


# ---------------------------------------------------------------------------
# test_cost_orders_correctly


def test_cost_orders_correctly_feasible_beats_infeasible():
    """Any feasible attempt has a strictly lower cost than any infeasible attempt."""
    feasible = _feasible_attempt()
    infeasible = _infeasible_attempt()

    r_feas = verify(feasible, "crew_default")
    r_inf = verify(infeasible, "crew_default")

    assert r_feas["feasible"] is True, (
        f"Expected feasible attempt to pass; failed constraints: "
        f"{[c['id'] for c in r_feas['constraint_report'] if not c['pass']]}"
    )
    assert r_inf["feasible"] is False

    assert r_feas["cost"] < r_inf["cost"], (
        f"Feasible cost {r_feas['cost']} >= infeasible cost {r_inf['cost']}"
    )


def test_cost_orders_correctly_worse_feasible_still_beats_infeasible():
    """A very expensive feasible attempt (huge fuel use) still beats infeasible."""
    feasible = _feasible_attempt()
    # Massively inflate prop_used_kg to make feasible cost very high.
    feasible["metrics"]["prop_used_kg"] = 1e9
    r_feas = verify(feasible, "crew_default")

    infeasible = _infeasible_attempt()
    r_inf = verify(infeasible, "crew_default")

    assert r_feas["feasible"] is True
    assert r_inf["feasible"] is False
    assert r_feas["cost"] < r_inf["cost"], (
        f"Even expensive feasible cost {r_feas['cost']} should beat infeasible {r_inf['cost']}"
    )


def test_cost_orders_correctly_lower_fuel_is_better():
    """Among feasible attempts, lower fuel use gives lower cost (all else equal)."""
    a1 = _feasible_attempt()
    a2 = _feasible_attempt()
    a2["metrics"]["prop_used_kg"] = a1["metrics"]["prop_used_kg"] * 2.0

    r1 = verify(a1, "crew_default")
    r2 = verify(a2, "crew_default")

    assert r1["feasible"] is True
    assert r2["feasible"] is True
    assert r1["cost"] < r2["cost"]


def test_infeasible_cost_increases_with_magnitude():
    """A worse C01 violation (lower perigee) gives a higher cost."""
    a_mild = _feasible_attempt()
    a_mild["phases"][0]["perigee_km"] = 150.0
    a_mild["metrics"]["perigee_km"] = 150.0

    a_severe = _feasible_attempt()
    a_severe["phases"][0]["perigee_km"] = 50.0
    a_severe["metrics"]["perigee_km"] = 50.0

    r_mild = verify(a_mild, "crew_default")
    r_severe = verify(a_severe, "crew_default")

    assert r_mild["feasible"] is False
    assert r_severe["feasible"] is False
    assert r_severe["cost"] > r_mild["cost"]


# ---------------------------------------------------------------------------
# Profile variants


def test_crew_vs_cargo_accel_limit():
    """Crew profile has tighter accel limit (4 g) than cargo (6 g)."""
    attempt = _feasible_attempt()
    attempt["metrics"]["max_accel_g"] = 5.0  # over crew limit, under cargo

    r_crew = verify(attempt, "crew_default")
    r_cargo = verify(attempt, "cargo_default")

    c03_crew = next(c for c in r_crew["constraint_report"] if c["id"] == "C03")
    c03_cargo = next(c for c in r_cargo["constraint_report"] if c["id"] == "C03")

    assert c03_crew["pass"] is False   # 5.0 > 4.0
    assert c03_cargo["pass"] is True   # 5.0 <= 6.0


def test_unknown_profile_raises():
    """An unknown constraints_profile raises KeyError."""
    with pytest.raises(KeyError):
        verify(_feasible_attempt(), "not_a_real_profile")


# ---------------------------------------------------------------------------
# Full constraint-report shape


def test_constraint_report_has_all_ten_constraints():
    """Every verify call produces exactly 10 constraint records (C01–C10)."""
    result = verify(_feasible_attempt(), "crew_default")
    ids = [c["id"] for c in result["constraint_report"]]
    expected = [f"C{i:02d}" for i in range(1, 11)]
    assert ids == expected



# ---------------------------------------------------------------------------
# `evaluated`: "we could not check" is a different fact from "it failed"


def test_every_constraint_states_whether_it_was_evaluated():
    """A consumer must not have to parse prose to find out what was judged."""
    r = verify(_feasible_attempt())
    for cr in r["constraint_report"]:
        assert "evaluated" in cr, f"{cr['id']}: no `evaluated` flag"
        assert isinstance(cr["evaluated"], bool)


def test_an_unevaluated_constraint_is_marked_unevaluated_and_never_passed():
    """C05 has no weather input in this attempt, so it is unchecked -- not violated."""
    r = verify(_feasible_attempt())
    c05 = next(c for c in r["constraint_report"] if c["id"] == "C05")
    assert c05["evaluated"] is False
    assert c05["pass"] is False, "an unchecked constraint must never be reported as passed"
    assert c05["value"] is None
    assert "not evaluated" in (c05.get("note") or "").lower()


def test_an_unevaluated_constraint_does_not_block_feasibility():
    """Otherwise no attempt could ever be feasible without a weather feed."""
    r = verify(_feasible_attempt())
    unevaluated = [c["id"] for c in r["constraint_report"] if not c["evaluated"]]
    assert unevaluated, "this test is meaningless if everything was evaluated"
    assert r["feasible"] is True, (
        f"feasible went false purely because {unevaluated} could not be checked"
    )


def test_an_evaluated_failure_still_blocks_feasibility():
    """The flag must not become a way to wave a real violation through."""
    att = _feasible_attempt()
    att["metrics"]["max_accel_g"] = 99.0
    r = verify(att)
    c03 = next(c for c in r["constraint_report"] if c["id"] == "C03")
    assert c03["evaluated"] is True
    assert c03["pass"] is False
    assert r["feasible"] is False


def test_feasibility_is_exactly_the_evaluated_constraints():
    """The documented rule, asserted rather than trusted."""
    for att in (_feasible_attempt(), _infeasible_attempt()):
        r = verify(att)
        expected = all(c["pass"] for c in r["constraint_report"] if c["evaluated"])
        assert r["feasible"] is expected
