"""Phase 2 contracts (HANDOFF_PHASE2.md section 6): dummies validate, and the rules that matter bite.

The schema-level checks are covered by the dummy files validating. The tests here are for the
cross-field rules, because those are the ones that protect the integrity rules in the charter:
an attempt may not call itself feasible against its own constraint report, a benchmark may not put
one mission in two splits, a real vehicle may not carry uncited numbers, and a session log must be a
coherent stream.

Owner: Yash (contracts are gated).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from contracts.validate import ContractError, kind_for_path, validate, validate_file, validate_session_file

ROOT = Path(__file__).resolve().parents[2]
DUMMY = ROOT / "data" / "dummy"
LIBRARY_DIRS = ("rockets", "pads", "attempts", "sessions")


def _load(name: str) -> dict:
    return json.loads((DUMMY / name).read_text(encoding="utf-8"))


def _library_files():
    files = []
    for d in LIBRARY_DIRS:
        files += sorted(p for p in (ROOT / "data" / d).glob("*") if p.suffix in (".json", ".jsonl"))
    return files


# HANDOFF_PHASE2 section 7: test_contracts_validate
@pytest.mark.parametrize("path", _library_files() or [None], ids=lambda p: p.name if p else "none-yet")
def test_contracts_validate(path):
    if path is None:
        pytest.skip("data/rockets, data/pads, data/attempts and data/sessions are still empty")
    validate_file(path)


def test_folder_decides_the_kind_for_id_named_files():
    assert kind_for_path("data/rockets/falcon9_block5.json") == "vehicle"
    assert kind_for_path("data/pads/ksc_lc39a.json") == "pad"
    assert kind_for_path("data/attempts/att_000123.json") == "attempt"
    assert kind_for_path("data/attempts/att_000123.traj.json") == "mission_trajectory"
    assert kind_for_path("data/sessions/msn_crew_iss_0001.jsonl") == "session"
    # v1 names are untouched
    assert kind_for_path("data/results/trajectory_cargo_iss_w0.json") == "trajectory"
    assert kind_for_path("data/dummy/rocket_dummy.json") == "rocket"


# ------------------------------------------------------------------ attempt


def test_attempt_cannot_claim_feasible_against_its_own_report():
    a = _load("attempt_dummy.json")
    a["constraint_report"][0]["pass"] = False
    with pytest.raises(ContractError, match="feasible"):
        validate("attempt", a)


def test_attempt_infeasible_with_a_failed_constraint_is_fine():
    a = _load("attempt_dummy.json")
    a["constraint_report"][0]["pass"] = False
    a["feasible"] = False
    validate("attempt", a)


def test_attempt_unfinished_cannot_be_feasible():
    a = _load("attempt_dummy.json")
    a["status"] = "aborted"
    with pytest.raises(ContractError, match="finished"):
        validate("attempt", a)


def test_attempt_phases_must_not_overlap_or_run_backwards():
    a = _load("attempt_dummy.json")
    a["phases"][1]["t0_s"] = 100  # parking starts before ascent ends
    with pytest.raises(ContractError, match="overlaps"):
        validate("attempt", a)
    b = _load("attempt_dummy.json")
    b["phases"][0]["t1_s"] = -1
    with pytest.raises(ContractError):
        validate("attempt", b)


def test_attempt_plan_accepts_extra_parameters_but_types_the_known_ones():
    a = _load("attempt_dummy.json")
    a["plan"]["phasing_revs"] = 3  # parameterization is free (WP2-02/05)
    validate("attempt", a)
    a["plan"]["burns"][0]["dv_mps"] = [1.0, 2.0]
    with pytest.raises(ContractError, match="dv_mps"):
        validate("attempt", a)


def test_attempt_requires_versions_and_seed():
    for key in ("sim_version", "verifier_version", "seed", "agent"):
        a = _load("attempt_dummy.json")
        del a[key]
        with pytest.raises(ContractError, match=key):
            validate("attempt", a)


# ------------------------------------------------------------------ mission trajectory


def test_mission_trajectory_arrays_must_align():
    t = _load("mission_trajectory_dummy.json")
    t["phase"] = t["phase"][:-1]
    with pytest.raises(ContractError, match="phase"):
        validate("mission_trajectory", t)
    u = _load("mission_trajectory_dummy.json")
    u["rel_pos_m"] = u["rel_pos_m"][:-2]
    with pytest.raises(ContractError, match="rel_pos_m"):
        validate("mission_trajectory", u)


# ------------------------------------------------------------------ vehicle / pad


def test_real_vehicle_needs_cited_sources():
    v = _load("vehicle_dummy.json")
    v["kind"] = "real"
    with pytest.raises(ContractError, match="sources"):
        validate("vehicle", v)
    v["sources"] = [
        {"title": "User guide", "url": "https://example.org/guide.pdf", "retrieved_utc": "2026-11-11T05:00:00Z"}
    ]
    v["confidence"] = "approximate"
    validate("vehicle", v)


def test_pad_azimuth_range_is_a_pair_or_null():
    p = _load("pad_dummy.json")
    p["azimuth_range_deg"] = [35.0, 120.0]
    validate("pad", p)
    p["azimuth_range_deg"] = [35.0]
    with pytest.raises(ContractError):
        validate("pad", p)


# ------------------------------------------------------------------ benchmark


def test_benchmark_rejects_a_mission_in_two_splits():
    b = _load("benchmark_dummy.json")
    b["splits"]["test"].append(b["splits"]["train"][0])
    with pytest.raises(ContractError, match="leakage"):
        validate("benchmark", b)


def test_benchmark_best_known_must_belong_to_a_split():
    b = _load("benchmark_dummy.json")
    b["best_known"]["msn_unknown"] = {"cost": 1.0, "attempt_id": "att_x", "budget_sims": 10}
    with pytest.raises(ContractError, match="not in any split"):
        validate("benchmark", b)


# ------------------------------------------------------------------ mission request


def test_mission_request_rules():
    m = _load("mission_request_dummy.json")
    bad = copy.deepcopy(m)
    bad["payload"]["crew"] = 0
    with pytest.raises(ContractError, match="crew"):
        validate("mission_request", bad)
    bad = copy.deepcopy(m)
    bad["objective"]["w_fuel"] = bad["objective"]["w_time"] = 0
    with pytest.raises(ContractError, match="tie"):
        validate("mission_request", bad)
    bad = copy.deepcopy(m)
    bad["budget"]["max_attempts"] = 0  # charter addition 9: budgets are enforced
    with pytest.raises(ContractError, match="max_attempts"):
        validate("mission_request", bad)


# ------------------------------------------------------------------ session.jsonl


def _write_session(tmp_path, events):
    p = tmp_path / "session_test.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


def _dummy_events():
    return [
        json.loads(x) for x in (DUMMY / "session_dummy.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()
    ]


def test_session_dummy_covers_every_event_type():
    events = _dummy_events()
    schema = json.loads((ROOT / "contracts" / "session.schema.json").read_text(encoding="utf-8"))
    assert {e["type"] for e in events} == set(schema["properties"]["type"]["enum"])
    assert validate_session_file(DUMMY / "session_dummy.jsonl") == len(events)


def test_session_time_cannot_go_backwards(tmp_path):
    ev = _dummy_events()
    ev[5]["t_wall"] = 0.05
    with pytest.raises(ContractError, match="backwards"):
        validate_session_file(_write_session(tmp_path, ev))


def test_session_must_start_with_mission_started(tmp_path):
    with pytest.raises(ContractError, match="mission_started"):
        validate_session_file(_write_session(tmp_path, _dummy_events()[1:]))


def test_session_events_need_a_running_attempt(tmp_path):
    ev = [e for e in _dummy_events() if e["type"] != "attempt_started"]
    with pytest.raises(ContractError, match="not running|never started"):
        validate_session_file(_write_session(tmp_path, ev))


def test_session_agent_step_needs_kind_and_summary(tmp_path):
    ev = _dummy_events()
    step = next(e for e in ev if e["type"] == "agent_step")
    del step["summary"]
    with pytest.raises(ContractError, match="summary"):
        validate_session_file(_write_session(tmp_path, ev))


def test_session_nothing_after_mission_finished(tmp_path):
    ev = _dummy_events()
    ev.append({"schema_version": 1, "t_wall": 9.0, "type": "benchmark_update", "data": {}})
    with pytest.raises(ContractError, match="after mission_finished"):
        validate_session_file(_write_session(tmp_path, ev))
