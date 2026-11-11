"""The mission API (WP2-06): create, launch, stream live, replay, and stop at the budget.

HANDOFF_PHASE2 WP2-06 is done when "a live session and its replay produce the same UI". The test
for that is `test_live_stream_and_replay_carry_the_same_events`: it watches a real run over the
WebSocket, then fetches the recording of the same run, and requires the two to be identical event
for event. They are, because there is one artefact rather than two code paths -- but a design that
guarantees a property is exactly the kind that quietly stops guaranteeing it, so it is asserted.

These tests run a real optimizer subprocess against a real vehicle. Each is a handful of seconds, so
most are marked slow; the contract-shaped checks that need no run are fast.

Owner: Yash (lane: server/).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from contracts.validate import validate
from server import create_app
from server.session import validate_session_events

ROOT = Path(__file__).resolve().parents[2]


def _request(mission_id: str, *, attempts: int = 3, wall_s: float = 300.0) -> dict:
    """A mission a real vehicle can fly, with a small budget so tests stay quick."""
    return {
        "schema_version": 1,
        "id": mission_id,
        "profile": "crew_iss",
        "payload": {"mass_kg": 12000.0, "crew": 4},
        "rocket_id": "falcon9_block5",
        "pad_id": "ccsfs_slc40",
        "target": {"norad_id": 25544},
        "window_id": "w0",
        "constraints_profile": "crew_default",
        "objective": {"w_fuel": 1.0, "w_time": 0.5, "fuel_ref_kg": 500000.0, "time_ref_s": 86400.0},
        "budget": {"max_attempts": attempts, "max_wall_s": wall_s},
        "seed": 7,
    }


@pytest.fixture
def client(tmp_path):
    app = create_app(root=tmp_path)
    with TestClient(app) as c:
        yield c
    app.state.store.shutdown()


def _wait_for_run(client, mission_id: str, timeout_s: float = 180.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        run = client.get(f"/api/missions/{mission_id}/run").json()
        if run["status"] != "running":
            return run
        time.sleep(0.2)
    pytest.fail(f"run for {mission_id} did not finish within {timeout_s} s")


# ------------------------------------------------------------------ fast: shape and validation


def test_health_reports_the_versions_results_are_tied_to(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok"
    # Charter addition 4: everything records which simulator and verifier produced it.
    assert h["sim_version"].startswith("env-")
    assert h["verifier_version"].startswith("ver-")


def test_library_serves_cited_vehicles_with_their_measured_error(client):
    rockets = client.get("/api/library/rockets").json()
    assert rockets, "the wizard has no vehicles to offer"
    for v in rockets:
        validate("vehicle", v)
    real = [v for v in rockets if v["kind"] == "real"]
    assert real
    for v in real:
        assert v["sources"], f"{v['id']} has no cited source"
        # The card must be able to show how far the simulator is from the published figure.
        assert "sim_validation" in v, f"{v['id']} has no measured payload comparison"
        assert v["sim_validation"]["sim_version"]

    pads = client.get("/api/library/pads").json()
    assert pads
    for p in pads:
        validate("pad", p)


def test_library_windows_are_real_and_profile_scoped(client):
    w = client.get("/api/library/windows", params={"profile": "crew_iss"}).json()
    validate("windows", w)
    assert w["profile"] == "crew_iss"
    assert client.get("/api/library/windows", params={"profile": "nope"}).status_code == 404


def test_a_bad_mission_request_is_rejected_field_by_field(client):
    bad = _request("msn_bad_0001")
    bad["payload"]["crew"] = 0  # a crew mission with nobody aboard
    r = client.post("/api/missions", json=bad)
    assert r.status_code == 422
    assert any("crew" in f for f in r.json()["detail"]["fields"]), r.json()

    missing = _request("msn_bad_0002")
    del missing["budget"]
    r = client.post("/api/missions", json=missing)
    assert r.status_code == 422
    assert any("budget" in f for f in r.json()["detail"]["fields"])


def test_missions_are_created_listed_and_not_duplicated(client):
    req = _request("msn_api_0001")
    assert client.post("/api/missions", json=req).status_code == 201
    assert client.post("/api/missions", json=req).status_code == 409  # same id twice
    assert [m["id"] for m in client.get("/api/missions").json()] == ["msn_api_0001"]
    assert client.get("/api/missions/msn_api_0001").json()["latest_run"] is None
    assert client.get("/api/missions/nope").status_code == 404


def test_endpoints_that_need_a_run_say_so_before_one_exists(client):
    client.post("/api/missions", json=_request("msn_api_0002"))
    for path in ("run", "attempts", "events"):
        r = client.get(f"/api/missions/msn_api_0002/{path}")
        assert r.status_code == 409, f"{path} returned {r.status_code}"


def test_replay_of_the_committed_dummy_session(client, tmp_path):
    """The replay path works with no backend run at all, which is the public-demo mode."""
    sess = tmp_path / "data" / "sessions" / "dummy_run"
    sess.mkdir(parents=True)
    src = ROOT / "data" / "dummy" / "session_dummy.jsonl"
    (sess / "session.jsonl").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    listed = client.get("/api/sessions").json()
    assert [s["id"] for s in listed] == ["dummy_run"]

    events = client.get("/api/sessions/dummy_run/events").json()
    assert events[0]["type"] == "mission_started"
    assert events[-1]["type"] == "mission_finished"
    for ev in events:
        validate("session", ev)
    assert client.get("/api/sessions/nope/events").status_code == 404


def test_replay_stream_is_paced_and_complete(client, tmp_path):
    sess = tmp_path / "data" / "sessions" / "dummy_run"
    sess.mkdir(parents=True)
    (sess / "session.jsonl").write_text(
        (ROOT / "data" / "dummy" / "session_dummy.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with client.stream("GET", "/api/sessions/dummy_run/stream", params={"speed": 500}) as r:
        assert r.status_code == 200
        payloads = [json.loads(ln[6:]) for ln in r.iter_lines() if ln.startswith("data: ")]

    recorded = [json.loads(x) for x in (sess / "session.jsonl").read_text().splitlines() if x.strip()]
    assert payloads[-1]["type"] == "stream_closed"
    assert payloads[:-1] == recorded, "the paced replay changed the events it was replaying"


# ------------------------------------------------------------------ slow: a real run


@pytest.mark.slow
def test_launch_produces_contract_valid_attempts_and_session(client):
    mid = "msn_api_live_0001"
    client.post("/api/missions", json=_request(mid, attempts=3))
    assert client.post(f"/api/missions/{mid}/launch").status_code == 202
    assert client.post(f"/api/missions/{mid}/launch").status_code == 409  # one run at a time
    run = _wait_for_run(client, mid)
    assert run["status"] == "finished", run

    attempts = client.get(f"/api/missions/{mid}/attempts").json()
    assert len(attempts) == 3, attempts
    for summary in attempts:
        full = client.get(f"/api/missions/{mid}/attempts/{summary['id']}").json()
        validate("attempt", full)
        assert full["sim_version"] and full["verifier_version"] and full["seed"] is not None
        traj = client.get(f"/api/missions/{mid}/attempts/{summary['id']}/trajectory").json()
        validate("mission_trajectory", traj)
        assert traj["attempt_id"] == summary["id"]

    events = client.get(f"/api/missions/{mid}/events").json()
    assert events[0]["type"] == "mission_started"
    assert events[-1]["type"] == "mission_finished"
    for ev in events:
        validate("session", ev)


@pytest.mark.slow
def test_live_stream_and_replay_carry_the_same_events(client):
    """WP2-06's acceptance criterion, asserted rather than assumed."""
    mid = "msn_api_live_0002"
    client.post("/api/missions", json=_request(mid, attempts=2))
    client.post(f"/api/missions/{mid}/launch")

    live: list[dict] = []
    with client.websocket_connect(f"/api/missions/{mid}/stream") as ws:
        while True:
            ev = ws.receive_json()
            if ev.get("type") == "stream_closed":
                break
            live.append(ev)

    _wait_for_run(client, mid)
    session_id = client.get(f"/api/missions/{mid}/run").json()["session_id"]
    replay = client.get(f"/api/sessions/{session_id}/events").json()

    assert live, "the live stream carried nothing"
    assert live == replay, (
        "a live viewer and a replay viewer saw different things: "
        f"{len(live)} live events against {len(replay)} replayed"
    )
    assert live[0]["type"] == "mission_started"
    assert live[-1]["type"] == "mission_finished"


@pytest.mark.slow
def test_a_late_subscriber_still_sees_the_whole_run(client):
    """Connecting after the run has started must not lose the events already written."""
    mid = "msn_api_live_0003"
    client.post("/api/missions", json=_request(mid, attempts=2))
    client.post(f"/api/missions/{mid}/launch")
    _wait_for_run(client, mid)  # subscribe only once it is entirely over

    seen = []
    with client.websocket_connect(f"/api/missions/{mid}/stream") as ws:
        while True:
            ev = ws.receive_json()
            if ev.get("type") == "stream_closed":
                break
            seen.append(ev)
    assert seen[0]["type"] == "mission_started"
    assert seen[-1]["type"] == "mission_finished"


@pytest.mark.slow
def test_the_session_file_is_contract_valid_end_to_end(client):
    mid = "msn_api_live_0004"
    client.post("/api/missions", json=_request(mid, attempts=2))
    client.post(f"/api/missions/{mid}/launch")
    _wait_for_run(client, mid)
    path = client.app.state.store.latest_run(mid).session_path
    count, errors = validate_session_events(path)
    assert errors == [], errors
    assert count > 4


@pytest.mark.slow
def test_wall_clock_budget_terminates_the_run(client):
    """Charter addition 9: budgets are enforced in code, not asked for politely."""
    mid = "msn_api_budget_0001"
    # Enough attempts to run for a while, but a wall budget of one second.
    client.post("/api/missions", json=_request(mid, attempts=400, wall_s=1.0))
    client.post(f"/api/missions/{mid}/launch")

    run = _wait_for_run(client, mid, timeout_s=90)
    assert run["status"] == "aborted", run
    assert run["stop_reason"] == "wall_clock_budget_exhausted"
    assert run["elapsed_s"] < 60, f"took {run['elapsed_s']} s to honour a 1 s budget"
    # Whatever it managed to write must still be readable and valid.
    _, errors = validate_session_events(client.app.state.store.latest_run(mid).session_path)
    assert errors == [], errors


@pytest.mark.slow
def test_abort_stops_a_run_on_request(client):
    mid = "msn_api_abort_0001"
    client.post("/api/missions", json=_request(mid, attempts=400, wall_s=600))
    client.post(f"/api/missions/{mid}/launch")
    time.sleep(1.0)
    run = client.post(f"/api/missions/{mid}/abort").json()
    assert run["status"] == "aborted"
    assert not client.app.state.store.latest_run(mid).is_running()
