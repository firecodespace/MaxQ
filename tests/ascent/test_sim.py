"""Acceptance tests for WP-05 ascent simulation engine."""

import pytest

from ascent.rockets import ARCHETYPES, get_rocket
from ascent.sim import simulate_ascent
from contracts.validate import validate


@pytest.fixture(scope="module")
def baseline_simulation():
    """Run baseline simulation once for medlift_kerolox_a."""
    rocket = get_rocket("medlift_kerolox_a")
    return simulate_ascent(
        rocket=rocket,
        site_lat_deg=28.5,
        site_lon_deg=-80.6,
        azimuth_deg=44.9,
        target_incl_deg=51.6,
        trajectory_id="traj_medlift_a_w0",
        window_id="w0",
    )


def test_sim_reaches_orbit(baseline_simulation):
    """HANDOFF.md §7: perigee >= ~160 km, ≈ 7.8 km/s at insertion, small eccentricity."""
    res = baseline_simulation
    # Speed at insertion ≈ 7.8 km/s (accept 7.7 - 7.9 km/s)
    speed_kmps = res.insertion_speed_mps / 1000.0
    assert 7.70 <= speed_kmps <= 7.90, f"Speed at insertion: {speed_kmps:.2f} km/s"

    # Perigee >= ~160 km
    assert res.perigee_km >= 160.0, f"Perigee {res.perigee_km:.1f} km < 160 km"

    # Altitude at insertion ≈ 180-220 km
    alt_km = res.insertion_alt_m / 1000.0
    assert 180.0 <= alt_km <= 230.0, f"Altitude {alt_km:.1f} km outside expected range"

    # Small eccentricity (e < 0.01 for circular parking orbit)
    assert res.eccentricity < 0.01, f"Eccentricity {res.eccentricity:.5f} is not small"


def test_max_q_plausible(baseline_simulation):
    """HANDOFF.md §7: roughly 60-90 s (flag if outside 40-120 s)."""
    res = baseline_simulation
    assert 40.0 <= res.t_max_q_s <= 120.0, f"Max-Q time {res.t_max_q_s:.1f}s outside bounds"
    assert 60.0 <= res.t_max_q_s <= 90.0, f"Max-Q time {res.t_max_q_s:.1f}s not in 60-90s window"
    # Dynamic pressure at max_q should be realistic (typically 25-45 kPa for orbital launchers)
    assert 20000.0 <= res.max_q_pa <= 50000.0, f"Max-Q pressure {res.max_q_pa:.0f} Pa"


def test_losses_plausible(baseline_simulation):
    """HANDOFF.md §7: gravity + drag losses typically 1.3-2 km/s."""
    res = baseline_simulation
    total_loss_kmps = res.total_loss_mps / 1000.0
    assert 1.30 <= total_loss_kmps <= 2.00, f"Losses: {total_loss_kmps:.2f} km/s outside [1.3, 2.0]"
    # Drag loss is typically 20 - 150 m/s for streamlined launch vehicles
    assert 10.0 <= res.drag_loss_mps <= 250.0


def test_trajectory_contract_validates(baseline_simulation):
    """Generated trajectory must validate against contracts/trajectory.schema.json."""
    traj = baseline_simulation.trajectory
    validate("trajectory", traj)


def test_discrete_arrays_uniform_length(baseline_simulation):
    """All 1 Hz sampled arrays must have identical length."""
    traj = baseline_simulation.trajectory
    array_keys = ["t_s", "lat_deg", "lon_deg", "alt_m", "speed_mps", "q_pa", "mass_kg"]
    lengths = {k: len(traj[k]) for k in array_keys}
    assert len(set(lengths.values())) == 1, f"Array lengths mismatch: {lengths}"
    n_points = lengths["t_s"]
    assert n_points >= 400, f"Trajectory too short: {n_points} points"
    # Ensure 1 Hz sampling
    assert traj["t_s"][:5] == [0, 1, 2, 3, 4]


def test_event_ordering(baseline_simulation):
    """Events must be ordered: max_q < meco <= stage_sep <= insertion."""
    traj = baseline_simulation.trajectory
    event_dict = {e["name"]: e["t_s"] for e in traj["events"]}
    assert "max_q" in event_dict
    assert "meco" in event_dict
    assert "stage_sep" in event_dict
    assert "insertion" in event_dict

    assert event_dict["max_q"] < event_dict["meco"]
    assert event_dict["meco"] <= event_dict["stage_sep"]
    assert event_dict["stage_sep"] <= event_dict["insertion"]


@pytest.mark.parametrize("rocket_id", list(ARCHETYPES.keys()))
def test_all_archetypes_reach_parking_orbit(rocket_id):
    """All 3 archetypes must reach parking orbit with perigee >= 160 km."""
    rocket = get_rocket(rocket_id)
    res = simulate_ascent(
        rocket=rocket,
        site_lat_deg=28.5,
        site_lon_deg=-80.6,
        azimuth_deg=44.9,
        target_incl_deg=51.6,
        trajectory_id=f"traj_{rocket_id}_test",
    )
    validate("trajectory", res.trajectory)
    assert res.perigee_km >= 160.0, f"{rocket_id} perigee {res.perigee_km:.1f} < 160 km"
    assert res.eccentricity < 0.01, f"{rocket_id} ecc {res.eccentricity:.5f} >= 0.01"
    assert 7.70 <= res.insertion_speed_mps / 1000.0 <= 7.90


def test_throttling_caps_peak_accel_and_tracks_propellant():
    """Throttling / max_g_limit keeps peak acceleration within cap without losing Stage-2 bookkeeping."""
    rocket = get_rocket("crewlift_kerolox_b")
    uncapped = simulate_ascent(rocket=rocket)
    capped = simulate_ascent(rocket=rocket, max_g_limit=3.6)

    assert capped.max_accel_g <= 3.61
    assert uncapped.s1_prop_used_kg > 0.0
    assert capped.s2_prop_remaining_kg >= 0.0
    assert capped.perigee_km >= 160.0


def test_s2_coast_increases_insertion_time_deterministically():
    """Stage-2 multi-burn coast extends timeline cleanly and remains deterministic."""
    rocket = get_rocket("medlift_kerolox_a")
    r1 = simulate_ascent(rocket=rocket, coast_s2_s=60.0, s2_burn1_frac=0.5)
    r2 = simulate_ascent(rocket=rocket, coast_s2_s=60.0, s2_burn1_frac=0.5)
    no_coast = simulate_ascent(rocket=rocket, coast_s2_s=0.0, s2_burn1_frac=1.0)

    assert r1.t_insertion_s == r2.t_insertion_s
    assert r1.t_insertion_s > no_coast.t_insertion_s + 50.0
