"""Rocket archetypes and stage models for ascent simulation.

Provides data models conforming to maxq/contracts/rocket.schema.json and three
fictional baseline archetypes:
1. MedLift-A (cargo launcher for ISS)
2. CrewLift-B (crew launcher for ISS)
3. HeavyLift-C (heavy launcher for high-energy GEO/MEO insertion)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ascent.geo import G0


@dataclass(frozen=True)
class Stage:
    """Individual rocket propulsion stage."""

    dry_kg: float
    prop_kg: float
    thrust_n: float
    isp_s: float
    cd: float
    area_m2: float

    @property
    def total_mass_kg(self) -> float:
        return self.dry_kg + self.prop_kg

    @property
    def mass_flow_rate_kgps(self) -> float:
        """Mass flow rate in kg/s: mdot = T / (Isp * g0)."""
        return self.thrust_n / (self.isp_s * G0)

    @property
    def burn_time_s(self) -> float:
        """Nominal full-propellant burn duration in seconds."""
        return self.prop_kg / self.mass_flow_rate_kgps

    def to_dict(self) -> dict[str, float]:
        return {
            "dry_kg": float(self.dry_kg),
            "prop_kg": float(self.prop_kg),
            "thrust_n": float(self.thrust_n),
            "isp_s": float(self.isp_s),
            "cd": float(self.cd),
            "area_m2": float(self.area_m2),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Stage:
        return cls(
            dry_kg=float(data["dry_kg"]),
            prop_kg=float(data["prop_kg"]),
            thrust_n=float(data["thrust_n"]),
            isp_s=float(data["isp_s"]),
            cd=float(data["cd"]),
            area_m2=float(data["area_m2"]),
        )


@dataclass
class Rocket:
    """Multi-stage launch vehicle model."""

    id: str
    name: str
    profile: str
    payload_kg: float
    stages: list[Stage] = field(default_factory=list)
    schema_version: int = 1
    _note: str | None = None

    @property
    def dry_mass_total_kg(self) -> float:
        return self.payload_kg + sum(s.dry_kg for s in self.stages)

    @property
    def liftoff_mass_kg(self) -> float:
        return self.payload_kg + sum(s.total_mass_kg for s in self.stages)

    def ideal_delta_v(self) -> float:
        """Ideal Tsiolkovsky delta-v across all stages (m/s)."""
        import math

        dv = 0.0
        m_current = self.liftoff_mass_kg
        for stage in self.stages:
            m_burnout = m_current - stage.prop_kg
            if m_burnout <= 0:
                continue
            dv += stage.isp_s * G0 * math.log(m_current / m_burnout)
            m_current = m_burnout - stage.dry_kg
        return dv

    def to_dict(self) -> dict[str, Any]:
        """Convert to contract-compliant dictionary."""
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "profile": self.profile,
            "payload_kg": float(self.payload_kg),
            "stages": [s.to_dict() for s in self.stages],
        }
        if self._note is not None:
            d["_note"] = self._note
        return d

    def to_json(self, path: Path | str, indent: int = 2) -> None:
        """Save to JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Rocket:
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            id=str(data["id"]),
            name=str(data["name"]),
            profile=str(data["profile"]),
            payload_kg=float(data["payload_kg"]),
            stages=[Stage.from_dict(s) for s in data["stages"]],
            _note=data.get("_note"),
        )

    @classmethod
    def from_json(cls, path: Path | str) -> Rocket:
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


# ---------------------------------------------------------------------------
# Fictional Archetypes (Tuned to reach baseline parking orbit)
# ---------------------------------------------------------------------------


def archetype_cargo() -> Rocket:
    """Cargo Launcher: MedLift-A for cargo_iss missions."""
    return Rocket(
        id="medlift_kerolox_a",
        name="MedLift-A (fictional archetype)",
        profile="cargo_iss",
        payload_kg=8000.0,
        stages=[
            Stage(
                dry_kg=24000.0,
                prop_kg=390000.0,
                thrust_n=7200000.0,
                isp_s=290.0,
                cd=0.30,
                area_m2=10.8,
            ),
            Stage(
                dry_kg=4200.0,
                prop_kg=95000.0,
                thrust_n=950000.0,
                isp_s=348.0,
                cd=0.30,
                area_m2=10.8,
            ),
        ],
    )


def archetype_crew() -> Rocket:
    """Crew Launcher: CrewLift-B for crew_iss missions."""
    return Rocket(
        id="crewlift_kerolox_b",
        name="CrewLift-B (fictional archetype)",
        profile="crew_iss",
        payload_kg=9500.0,
        stages=[
            Stage(
                dry_kg=25000.0,
                prop_kg=410000.0,
                thrust_n=7600000.0,
                isp_s=292.0,
                cd=0.32,
                area_m2=11.5,
            ),
            Stage(
                dry_kg=4500.0,
                prop_kg=102000.0,
                thrust_n=980000.0,
                isp_s=348.0,
                cd=0.32,
                area_m2=11.5,
            ),
        ],
    )


def archetype_heavy() -> Rocket:
    """Heavy Launcher: HeavyLift-C for geo_comm and meo_gps missions."""
    return Rocket(
        id="heavylift_hydrolox_c",
        name="HeavyLift-C (fictional archetype)",
        profile="geo_comm",
        payload_kg=20000.0,
        stages=[
            Stage(
                dry_kg=48000.0,
                prop_kg=820000.0,
                thrust_n=15000000.0,
                isp_s=300.0,
                cd=0.35,
                area_m2=22.0,
            ),
            Stage(
                dry_kg=11000.0,
                prop_kg=150000.0,
                thrust_n=1600000.0,
                isp_s=445.0,  # High-Isp hydrolox upper stage
                cd=0.35,
                area_m2=22.0,
            ),
        ],
    )


ARCHETYPES: dict[str, Rocket] = {
    "medlift_kerolox_a": archetype_cargo(),
    "crewlift_kerolox_b": archetype_crew(),
    "heavylift_hydrolox_c": archetype_heavy(),
}


def get_rocket(rocket_id: str) -> Rocket:
    """Get archetype by id."""
    if rocket_id in ARCHETYPES:
        return ARCHETYPES[rocket_id]
    raise KeyError(f"Unknown rocket archetype: {rocket_id}. Available: {list(ARCHETYPES.keys())}")


def list_archetypes() -> list[Rocket]:
    """List all available archetypes."""
    return list(ARCHETYPES.values())
