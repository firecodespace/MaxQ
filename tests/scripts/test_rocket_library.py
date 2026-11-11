"""The launch-vehicle and pad library (WP2-01): cited, contract-valid, and honestly validated.

HANDOFF_PHASE2 section 7 asks for `test_rocket_library_validation`: the simulated payload to LEO is
within a stated tolerance of the published figure, the tolerance is recorded per rocket, and misses
are reported honestly.

Re-flying every vehicle takes minutes, so the fast tests check the RECORDED validation block and,
importantly, that it is not stale: the block carries the hash of the simulator sources it was
produced with, so editing the ascent model invalidates it and the suite says so instead of letting
an old number stand. One vehicle is actually re-flown under the `slow` marker.

Owner: Yash (lane: data/rockets, data/pads, scripts/).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.validate import validate_file

ROOT = Path(__file__).resolve().parents[2]
ROCKETS = sorted((ROOT / "data" / "rockets").glob("*.json"))
PADS = sorted((ROOT / "data" / "pads").glob("*.json"))


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _real() -> list[dict]:
    return [v for v in map(_load, ROCKETS) if v["kind"] == "real"]


def test_library_is_not_empty():
    # HANDOFF_PHASE2 WP2-01: at least three real vehicles and three pads.
    real = _real()
    assert len(real) >= 3, f"only {len(real)} real vehicles in data/rockets"
    assert len(PADS) >= 3, f"only {len(PADS)} pads in data/pads"


@pytest.mark.parametrize("path", ROCKETS + PADS, ids=lambda p: p.name)
def test_library_file_validates(path):
    validate_file(path)


@pytest.mark.parametrize("v", _real(), ids=lambda v: v["id"])
def test_real_vehicle_is_cited(v):
    assert v["sources"], f"{v['id']}: a real vehicle needs cited sources"
    for s in v["sources"]:
        assert s["url"].startswith("http"), f"{v['id']}: source url {s['url']!r}"
        assert s["retrieved_utc"].endswith("Z"), f"{v['id']}: retrieved_utc must be UTC"
    assert v["confidence"] == "approximate", (
        f"{v['id']}: published rocket numbers are approximations of a model, never exact"
    )
    assert v["notes"].strip(), f"{v['id']}: notes must record assumptions and source conflicts"


@pytest.mark.parametrize("v", _real(), ids=lambda v: v["id"])
def test_stage_numbers_are_physical(v):
    assert len(v["stages"]) == 2, f"{v['id']}: the ascent simulator flies exactly two serial stages"
    for i, s in enumerate(v["stages"]):
        assert 150 <= s["isp_s"] <= 480, f"{v['id']} stage {i}: Isp {s['isp_s']} s is outside any chemical engine"
        assert s["prop_kg"] > s["dry_kg"], f"{v['id']} stage {i}: more structure than propellant"
        assert 1.0 <= s["area_m2"] <= 120.0, f"{v['id']} stage {i}: reference area {s['area_m2']} m2"
    # It has to be able to leave the pad with its published payload.
    g0 = 9.80665
    liftoff = sum(s["dry_kg"] + s["prop_kg"] for s in v["stages"]) + (v["payload_to_leo_kg_published"] or 0.0)
    twr = v["stages"][0]["thrust_n"] / (liftoff * g0)
    assert 1.05 <= twr <= 2.0, f"{v['id']}: lift-off thrust-to-weight {twr:.2f}"


@pytest.mark.parametrize("v", _real(), ids=lambda v: v["id"])
def test_rocket_library_validation(v):
    """The sim-vs-published comparison exists, is current, and either passes or is acknowledged.

    A vehicle the simulator cannot fly well is allowed to stay in the library -- the cited data is
    still right, and a better environment may fly it -- but it must say so in its own file. What is
    not allowed is a miss nobody recorded, or a number produced by a simulator that has since
    changed. `test_library_has_enough_flyable_vehicles` keeps the library usable despite this.
    """
    from scripts.validate_rocket_library import sim_version

    block = v.get("sim_validation")
    assert block, f"{v['id']}: no sim_validation block. Run `python scripts/validate_rocket_library.py`."
    assert block["sim_version"] == sim_version(), (
        f"{v['id']}: sim_validation was produced with {block['sim_version']}, but the ascent simulator "
        f"is now {sim_version()}. Re-run scripts/validate_rocket_library.py; a stale number is worse than none."
    )

    if block["pass"]:
        assert block["payload_to_leo_kg_sim"] is not None
        assert abs(block["error_pct"]) <= block["tolerance_pct"], (
            f"{v['id']}: marked pass with an error of {block['error_pct']:+.1f} % against a stated "
            f"tolerance of +/-{block['tolerance_pct']:.0f} %"
        )
    else:
        # An acknowledged miss: the file has to carry the reason, in the vehicle's own notes.
        assert "LIMITATION" in v["notes"] or "limitation" in v["notes"], (
            f"{v['id']}: sim_validation.pass is false but notes do not explain why. Measured "
            f"{block['payload_to_leo_kg_sim']} kg against a published "
            f"{v['payload_to_leo_kg_published']:,.0f} kg ({block['error_pct']:+.1f} %)."
        )


def test_library_has_enough_flyable_vehicles():
    """At least two vehicles the simulator flies within tolerance, one of them crew-capable."""
    real = _real()
    ok = [v for v in real if (v.get("sim_validation") or {}).get("pass")]
    assert len(ok) >= 2, (
        f"only {len(ok)} of {len(real)} real vehicles are within tolerance "
        f"({[v['id'] for v in ok]}); the library is not usable for missions"
    )
    assert any(v["crew_rated"] for v in ok), "no crew-capable vehicle the simulator can fly"


@pytest.mark.parametrize("p", list(map(_load, PADS)), ids=lambda p: p["id"])
def test_pad_refers_to_known_vehicles(p):
    known = {v["id"] for v in map(_load, ROCKETS)}
    unknown = set(p["supported_rocket_ids"]) - known
    assert not unknown, f"{p['id']}: unknown vehicles {sorted(unknown)}"
    assert p["sources"], f"{p['id']}: pad coordinates need a cited source"


def test_every_real_vehicle_has_a_pad():
    pads = list(map(_load, PADS))
    for v in _real():
        assert any(v["id"] in p["supported_rocket_ids"] for p in pads), f"{v['id']}: no pad supports it"


def test_v1_archetypes_are_still_available():
    """The three fictional archetypes stay as a fallback (HANDOFF_PHASE2 6.5)."""
    from ascent.rockets import list_archetypes

    assert {r.id for r in list_archetypes()} == {
        "medlift_kerolox_a",
        "crewlift_kerolox_b",
        "heavylift_hydrolox_c",
    }


@pytest.mark.slow
def test_validation_reproduces_for_one_vehicle():
    """Re-fly the smallest vehicle and check the recorded figure can actually be reproduced.

    Seeded, so the same simulator must give the same answer. This is the test that would catch the
    recorded numbers being hand-edited or copied from a different configuration.
    """
    from scripts.validate_rocket_library import main

    flyable = [v for v in _real() if (v.get("sim_validation") or {}).get("pass")]
    vid = min(flyable, key=lambda v: v["payload_to_leo_kg_published"])["id"]
    path = ROOT / "data" / "rockets" / f"{vid}.json"
    before = _load(path)["sim_validation"]

    # main() rewrites the vehicle file in place, which is what it is for -- but a test must not
    # leave committed measurement data modified. When the simulator has changed, the re-flown
    # number legitimately differs, and without this the failure also dirties the working tree and
    # the next person has to work out whether that edit was deliberate. Restore the bytes exactly.
    original = path.read_bytes()
    try:
        main(["--only", vid])
        after = _load(path)["sim_validation"]
    finally:
        path.write_bytes(original)
    assert after["payload_to_leo_kg_sim"] == before["payload_to_leo_kg_sim"], (
        f"{vid}: re-running the validation gave {after['payload_to_leo_kg_sim']} kg, but the file "
        f"records {before['payload_to_leo_kg_sim']} kg. The search is seeded, so this should be exact."
    )
