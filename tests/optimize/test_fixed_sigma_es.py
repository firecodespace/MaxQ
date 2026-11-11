"""Placeholder baseline search (WP2-05): acceptance plus the honesty guards.

The acceptance test is the original one: three attempts, contract-valid artefacts. The rest exist
because this module was previously named and labelled CMA-ES while implementing no part of it, and
emitted `best_known_gap_pct: 0.0` with no benchmark in existence. A name that does not match the
implementation is a defect, so it gets tests like any other.

Owner: Yash (lane: optimize/, by Yash's direction -- see status/yash.md).
"""

import json
from pathlib import Path

from contracts.validate import validate, validate_session_file
from optimize.fixed_sigma_es import AGENT_ID, optimize_mission

ROOT = Path(__file__).resolve().parents[2]

REQUEST = {
    "schema_version": 1,
    "id": "msn_test_opt",
    "profile": "crew_iss",
    "payload": {"mass_kg": 9500.0, "crew": 4},
    "rocket_id": "falcon9_block5",
    "pad_id": "ksc_lc39a",
    "target": {"norad_id": 25544},
    "window_id": "w0",
    "constraints_profile": "crew_default",
    "objective": {
        "w_fuel": 1.0,
        "w_time": 0.5,
        "fuel_ref_kg": 500000.0,
        "time_ref_s": 86400.0,
    },
    "budget": {"max_attempts": 3, "max_wall_s": 60.0},
    "seed": 42,
}


def _run(tmp_path, attempts=3):
    log = tmp_path / "session.jsonl"
    best = optimize_mission(
        mission_request=dict(REQUEST),
        max_attempts=attempts,
        seed=42,
        session_log_path=log,
        save_attempts=False,
    )
    events = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return best, log, events


def test_search_runs_and_emits_valid_artifacts(tmp_path):
    """Three attempts, contract-valid attempt and session log."""
    best, log, _ = _run(tmp_path)
    assert best is not None
    validate("attempt", best)
    assert log.exists()
    validate_session_file(log)


def test_the_agent_id_does_not_claim_cma(tmp_path):
    """The module implements no part of CMA-ES, so nothing it writes may say it does."""
    best, _, events = _run(tmp_path)
    assert "cma" not in AGENT_ID.lower()
    assert "cma" not in best["agent"]["id"].lower()
    blob = json.dumps(events).lower()
    assert "cma" not in blob, "a session event still advertises CMA"


def test_the_run_declares_itself_a_placeholder(tmp_path):
    """mission_started carries the label the UI shows, so the badge cannot drift from the code."""
    _, _, events = _run(tmp_path)
    started = next(e for e in events if e["type"] == "mission_started")
    assert started["data"]["search"] == "placeholder"
    assert "non-adaptive" in started["data"]["search_label"]


def test_gap_to_best_known_is_null_not_zero(tmp_path):
    """No benchmark exists (WP2-12). 0.0 would read as 'exactly matches best known'."""
    best, _, events = _run(tmp_path)
    assert best["best_known_gap_pct"] is None
    for e in events:
        if e["type"] == "benchmark_update":
            assert e["data"]["best_known_gap_pct"] is None


def test_agent_steps_are_notes_not_fabricated_tool_calls(tmp_path):
    """There is no agent. A `plan` step with a `tool` field would imply one reasoned and chose."""
    _, _, events = _run(tmp_path)
    steps = [e for e in events if e["type"] == "agent_step"]
    assert steps, "the run emitted no agent_step events"
    for e in steps:
        assert e["kind"] == "note"
        assert "tool" not in e["data"]
        assert "placeholder" in e["summary"].lower()


def test_same_seed_gives_the_same_result(tmp_path):
    a, _, _ = _run(tmp_path / "a")
    b, _, _ = _run(tmp_path / "b")
    assert a["cost"] == b["cost"]
    assert a["plan"]["pitch_program"] == b["plan"]["pitch_program"]
