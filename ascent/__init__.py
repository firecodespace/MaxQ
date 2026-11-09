"""MaxQ Ascent Simulation Package."""

from ascent.atmosphere import density, dynamic_pressure, pressure
from ascent.geo import (
    G0,
    MU_EARTH,
    OMEGA_EARTH,
    R_EARTH,
    circular_orbit_speed,
    earth_rotation_boost,
    launch_azimuth,
    spherical_forward,
    spherical_forward_array,
)
from ascent.optimize import OptimizationResult, optimize_ascent
from ascent.rockets import ARCHETYPES, Rocket, Stage, get_rocket, list_archetypes
from ascent.sim import DEFAULT_PITCH_PARAMS, SimResult, simulate_ascent

__all__ = [
    "ARCHETYPES",
    "DEFAULT_PITCH_PARAMS",
    "G0",
    "MU_EARTH",
    "OMEGA_EARTH",
    "OptimizationResult",
    "R_EARTH",
    "Rocket",
    "SimResult",
    "Stage",
    "circular_orbit_speed",
    "density",
    "dynamic_pressure",
    "earth_rotation_boost",
    "get_rocket",
    "launch_azimuth",
    "list_archetypes",
    "optimize_ascent",
    "pressure",
    "simulate_ascent",
    "spherical_forward",
    "spherical_forward_array",
]

