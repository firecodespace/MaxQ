"""Tests for rocket archetypes and stage models."""

import pytest

from ascent.rockets import ARCHETYPES, Rocket, Stage, get_rocket, list_archetypes
from contracts.validate import validate


def test_archetypes_count():
    archetypes = list_archetypes()
    assert len(archetypes) >= 3
    assert "medlift_kerolox_a" in ARCHETYPES
    assert "crewlift_kerolox_b" in ARCHETYPES
    assert "heavylift_hydrolox_c" in ARCHETYPES


@pytest.mark.parametrize("rocket_id", list(ARCHETYPES.keys()))
def test_archetypes_validate_contract(rocket_id):
    rocket = get_rocket(rocket_id)
    d = rocket.to_dict()
    # Must validate against contracts/rocket.schema.json without errors
    validate("rocket", d)


@pytest.mark.parametrize("rocket_id", list(ARCHETYPES.keys()))
def test_liftoff_thrust_to_weight_ratio(rocket_id):
    rocket = get_rocket(rocket_id)
    first_stage = rocket.stages[0]
    weight_n = rocket.liftoff_mass_kg * 9.80665
    tw_ratio = first_stage.thrust_n / weight_n
    # Standard launch vehicles require T/W >= 1.2 for positive liftoff acceleration
    assert 1.2 <= tw_ratio <= 1.8, f"{rocket_id} liftoff T/W = {tw_ratio:.2f}"


@pytest.mark.parametrize("rocket_id", list(ARCHETYPES.keys()))
def test_archetype_ideal_delta_v(rocket_id):
    rocket = get_rocket(rocket_id)
    ideal_dv = rocket.ideal_delta_v()
    # To reach LEO, rocket requires at least 9000 m/s ideal delta-v
    assert ideal_dv >= 9500.0, f"{rocket_id} ideal dv {ideal_dv:.1f} m/s is insufficient"


def test_stage_burn_time_and_mass_flow():
    stage = Stage(
        dry_kg=1000.0,
        prop_kg=10000.0,
        thrust_n=200000.0,
        isp_s=300.0,
        cd=0.3,
        area_m2=5.0,
    )
    # mdot = 200000 / (300 * 9.80665) = 67.98 kg/s
    assert stage.mass_flow_rate_kgps == pytest.approx(67.98, rel=1e-2)
    # burn_time = 10000 / 67.98 = 147.1 s
    assert stage.burn_time_s == pytest.approx(147.1, rel=1e-2)


def test_roundtrip_dict_serialization():
    rocket = get_rocket("medlift_kerolox_a")
    data = rocket.to_dict()
    reconstructed = Rocket.from_dict(data)
    assert reconstructed.id == rocket.id
    assert reconstructed.liftoff_mass_kg == rocket.liftoff_mass_kg
    assert len(reconstructed.stages) == len(rocket.stages)
