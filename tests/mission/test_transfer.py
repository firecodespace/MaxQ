"""WP2-15 analytic orbit raising and plane change, checked against known values.

Closed-form physics is the one kind of code where correctness can be asserted rather than
plausibility-checked, so these tests compare against published orbital figures and against
the special cases the general formula must reduce to. If the general case is right and both
degenerate cases fall out of it exactly, the formula is right.

Owner: Yash (lane: mission/, tests/).
"""

from __future__ import annotations

import math

import pytest

from core.constants import MU_M3_S2, R_E_M
from mission import transfer as T

CAPE_LAT_DEG = 28.5619


# ---------------------------------------------------------------------------
# The target orbits, derived rather than quoted


def test_the_geostationary_altitude_matches_the_published_figure():
    """35,786 km. Derived from one sidereal day and MU, so it cannot drift from the constant
    the rest of the project uses."""
    assert T.GEO_ALT_KM == pytest.approx(35786.0, abs=1.0)


def test_the_gps_altitude_matches_the_published_figure():
    """~20,180 km for a half-sidereal-day orbit."""
    assert T.GPS_ALT_KM == pytest.approx(20180.0, abs=10.0)


def test_a_geostationary_orbit_has_a_sidereal_day_period():
    assert T.orbital_period_s(T.GEO_RADIUS_M) == pytest.approx(T.SIDEREAL_DAY_S, rel=1e-9)


def test_circular_speed_at_geo_is_about_three_km_per_second():
    assert T.circular_speed_mps(T.GEO_RADIUS_M) == pytest.approx(3074.7, abs=1.0)


@pytest.mark.parametrize("profile,alt,incl", [
    ("geo_comm", 35786.0, 0.0),
    ("meo_gps", 20180.0, 55.0),
])
def test_target_for_profile(profile, alt, incl):
    got = T.target_for_profile(profile)
    assert got["alt_km"] == pytest.approx(alt, abs=10.0)
    assert got["incl_deg"] == pytest.approx(incl)


def test_a_rendezvous_profile_is_refused_by_the_transfer_planner():
    """crew_iss and cargo_iss go through rendezvous/phasing.py. Silently planning a transfer
    for them would produce numbers about physics that is not happening -- which is exactly
    what MissionEnv used to do in reverse for GEO."""
    for profile in ("crew_iss", "cargo_iss"):
        assert T.is_transfer_profile(profile) is False
        with pytest.raises(ValueError, match="not a transfer profile"):
            T.target_for_profile(profile)


# ---------------------------------------------------------------------------
# The transfer, against textbook numbers


def test_a_cape_to_geo_transfer_matches_the_textbook_budget():
    """Published figures for a Cape GTO: perigee kick ~2,440 m/s, apogee kick ~1,800 m/s.

    Tolerances are wide enough to be a real check and tight enough to catch a sign error, a
    wrong radius or a degrees/radians mistake -- each of which moves these by hundreds of m/s.
    """
    p = T.plan_transfer(200.0, T.GEO_ALT_KM, CAPE_LAT_DEG)
    assert p["dv1_mps"] == pytest.approx(2440.0, abs=40.0)
    assert p["dv2_mps"] == pytest.approx(1800.0, abs=60.0)
    assert p["total_dv_mps"] == pytest.approx(4240.0, abs=80.0)


def test_the_gto_coast_is_about_five_and_a_quarter_hours():
    """Half the period of the transfer ellipse. A real GTO coast to first apogee is ~5.3 h."""
    p = T.plan_transfer(200.0, T.GEO_ALT_KM, CAPE_LAT_DEG)
    assert p["coast_s"] / 3600.0 == pytest.approx(5.26, abs=0.1)


def test_the_combined_burn_beats_doing_the_two_separately():
    """The reason the plane change belongs in the apogee burn. Asserted across the range, not
    at one convenient point."""
    for di in (1.0, 5.0, 15.0, 28.5619, 45.0, 60.0):
        p = T.plan_transfer(200.0, T.GEO_ALT_KM, di)
        assert p["dv2_mps"] <= p["dv2_if_separate_mps"] + 1e-9, f"di={di}"
    # And the saving is large, not marginal: a quarter of the post-insertion budget.
    cape = T.plan_transfer(200.0, T.GEO_ALT_KM, CAPE_LAT_DEG)
    assert cape["dv2_if_separate_mps"] - cape["dv2_mps"] > 1000.0


def test_with_no_plane_change_the_apogee_burn_is_exactly_a_hohmann_circularisation():
    """First degenerate case: the general formula must reduce to |v_circ - v_apogee|."""
    p = T.plan_transfer(200.0, T.GEO_ALT_KM, 0.0)
    assert p["dv2_mps"] == pytest.approx(
        abs(p["v_target_mps"] - p["v_apogee_mps"]), abs=0.01
    )


def test_with_equal_speeds_the_combined_burn_is_exactly_a_pure_rotation():
    """Second degenerate case: 2*v*sin(di/2)."""
    v = 3074.66
    for di in (0.0, 10.0, 28.5619, 90.0):
        assert T.combined_plane_change_dv_mps(v, v, di) == pytest.approx(
            2.0 * v * math.sin(math.radians(di) / 2.0), rel=1e-9
        )


def test_the_transfer_agrees_with_vis_viva_on_both_apsides():
    """Independent derivation of the same two speeds, from the energy equation."""
    p = T.plan_transfer(200.0, T.GEO_ALT_KM, 0.0)
    r1 = R_E_M + 200.0 * 1000.0
    r2 = R_E_M + T.GEO_ALT_KM * 1000.0
    a = 0.5 * (r1 + r2)
    assert p["v_perigee_mps"] == pytest.approx(
        math.sqrt(MU_M3_S2 * (2.0 / r1 - 1.0 / a)), rel=1e-6
    )
    assert p["v_apogee_mps"] == pytest.approx(
        math.sqrt(MU_M3_S2 * (2.0 / r2 - 1.0 / a)), rel=1e-6
    )


def test_a_higher_target_costs_more_delta_v_and_more_time():
    low = T.plan_transfer(200.0, T.GPS_ALT_KM, 0.0)
    high = T.plan_transfer(200.0, T.GEO_ALT_KM, 0.0)
    assert high["total_dv_mps"] > low["total_dv_mps"]
    assert high["coast_s"] > low["coast_s"]


def test_a_bigger_plane_change_costs_more():
    previous = 0.0
    for di in (0.0, 10.0, 28.5619, 45.0):
        dv = T.plan_transfer(200.0, T.GEO_ALT_KM, di)["total_dv_mps"]
        assert dv >= previous
        previous = dv


def test_lowering_an_orbit_is_refused_rather_than_silently_wrong():
    """This module raises orbits. Asked to lower one it would return a negative dv1 and look
    like a result."""
    with pytest.raises(ValueError, match="does not lower them"):
        T.plan_transfer(35786.0, 200.0, 0.0)
    with pytest.raises(ValueError, match="does not lower them"):
        T.plan_transfer(400.0, 400.0, 0.0)


def test_every_transfer_result_is_labelled_approximate_and_says_what_is_missing():
    """Charter addition 7. These numbers get quoted; the caveats have to travel with them."""
    p = T.plan_transfer(200.0, T.GEO_ALT_KM, CAPE_LAT_DEG)
    assert p["confidence"] == "approximate"
    note = p["_note"].lower()
    for missing in ("finite-", "window", "impulsive"):
        assert missing in note, f"the note does not mention {missing!r}"


def test_the_gps_inclination_is_a_raise_not_a_removal():
    """Launching from the Cape at 28.56 deg to a 55 deg constellation means ADDING
    inclination. A module that assumed plane error was always the site latitude would get GPS
    backwards."""
    target = T.target_for_profile("meo_gps")
    assert target["incl_deg"] > CAPE_LAT_DEG
    needed = target["incl_deg"] - CAPE_LAT_DEG
    assert T.plan_transfer(200.0, T.GPS_ALT_KM, needed)["plane_change_deg"] == pytest.approx(
        needed, abs=1e-4
    )
