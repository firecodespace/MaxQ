"""MissionEnv core implementation.

Wraps ascent/sim.py, evaluates full mission plans deterministically,
and exports contract-compliant attempt and mission_trajectory structures.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import time
from pathlib import Path
from typing import Any

from ascent.geo import G0, MU_EARTH, R_EARTH
from ascent.rockets import Rocket, Stage, get_rocket
from ascent.sim import DEFAULT_PITCH_PARAMS, simulate_ascent
from verifier.core import verify

ROOT = Path(__file__).resolve().parents[1]


def _hash_sim_sources() -> str:
    h = hashlib.sha256()
    for name in ("env.mission_env", "ascent.sim"):
        spec = importlib.util.find_spec(name)
        if spec and spec.origin:
            h.update(Path(spec.origin).read_bytes())
    return "env-" + h.hexdigest()[:12]


SIM_VERSION: str = _hash_sim_sources()


def load_pad(pad_id: str) -> dict[str, Any]:
    pad_file = ROOT / "data" / "pads" / f"{pad_id}.json"
    if pad_file.exists():
        with open(pad_file, encoding="utf-8") as f:
            return json.load(f)
    # Default Cape Canaveral pad fallback
    return {
        "id": pad_id,
        "name": "Cape Canaveral SLC-40",
        "lat_deg": 28.56185,
        "lon_deg": -80.57737,
    }


def load_rocket(rocket_id: str, payload_kg: float) -> Rocket:
    # First check data/rockets/
    rocket_file = ROOT / "data" / "rockets" / f"{rocket_id}.json"
    if rocket_file.exists():
        with open(rocket_file, encoding="utf-8") as f:
            data = json.load(f)
        stages = [
            Stage(
                dry_kg=s["dry_kg"],
                prop_kg=s["prop_kg"],
                thrust_n=s["thrust_n"],
                isp_s=s["isp_s"],
                cd=s.get("cd", 0.3),
                area_m2=s["area_m2"],
            )
            for s in data["stages"]
        ]
        return Rocket(
            id=data["id"],
            name=data["name"],
            profile=data.get("profile", "cargo_iss"),
            stages=stages,
            payload_kg=payload_kg,
        )
    # Fallback to fictional archetypes
    return get_rocket(rocket_id, payload_kg=payload_kg)


class MissionEnv:
    """Deterministic environment running ASCENT -> PARKING -> PHASING."""

    def __init__(self, mission_request: dict[str, Any]):
        self.request = mission_request
        self.seed = int(mission_request.get("seed", 42))
        self.profile = mission_request.get("profile", "crew_iss")
        self.payload_kg = float(mission_request.get("payload", {}).get("mass_kg", 8000.0))
        self.rocket_id = mission_request.get("rocket_id", "falcon9_block5")
        self.pad_id = mission_request.get("pad_id", "ccsfs_slc40")
        self.pad_data = load_pad(self.pad_id)
        self.rocket = load_rocket(self.rocket_id, self.payload_kg)

    def evaluate_plan(
        self,
        plan: dict[str, Any],
        attempt_id: str = "att_000001",
        agent_info: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Evaluate a full mission plan (Plan mode).

        Returns (attempt_dict, mission_trajectory_dict).
        """
        wall_start = time.perf_counter()

        launch_t_unix = int(plan.get("launch_t_unix", 1794372400))
        pitch_program = plan.get("pitch_program")
        if not pitch_program:
            pitch_program = DEFAULT_PITCH_PARAMS.get(
                self.rocket.id, [11.865, 18.262, -1.0, 0.81, 0.747]
            )

        target_parking_alt_km = float(plan.get("parking_alt_km", 200.0))
        site_lat = float(self.pad_data.get("lat_deg", 28.5))
        site_lon = float(self.pad_data.get("lon_deg", -80.6))
        azimuth_deg = float(plan.get("azimuth_deg", 44.9))
        target_incl = 51.6 if "iss" in self.profile else 28.5

        # 1. ASCENT SIMULATION
        sim_res = simulate_ascent(
            rocket=self.rocket,
            site_lat_deg=site_lat,
            site_lon_deg=site_lon,
            azimuth_deg=azimuth_deg,
            target_incl_deg=target_incl,
            pitch_params=pitch_program,
            t0_unix=launch_t_unix,
            seed=self.seed,
            target_parking_alt_m=target_parking_alt_km * 1000.0,
        )

        ascent_traj = sim_res.trajectory
        t_ascent_end = float(ascent_traj["t_s"][-1])
        t_s_list = [float(t) for t in ascent_traj["t_s"]]
        phase_list = ["ascent"] * len(t_s_list)
        lat_list = [float(x) for x in ascent_traj["lat_deg"]]
        lon_list = [float(x) for x in ascent_traj["lon_deg"]]
        alt_list = [float(x) for x in ascent_traj["alt_m"]]
        speed_list = [float(x) for x in ascent_traj["speed_mps"]]
        mass_list = [float(x) for x in ascent_traj["mass_kg"]]
        q_list = [float(x) for x in ascent_traj["q_pa"]]

        # Calculate acceleration (g) from speed changes
        accel_list = [0.0]
        for i in range(1, len(speed_list)):
            dt = t_s_list[i] - t_s_list[i - 1]
            dv = speed_list[i] - speed_list[i - 1]
            a_g = (dv / max(1e-3, dt)) / G0
            accel_list.append(round(a_g, 3))

        prop_ascent_used = float(self.rocket.liftoff_mass_kg - mass_list[-1])

        # 2. PARKING PHASE
        # Coast in parking orbit for ~1 orbit (approx 5400 s)
        t_parking_end = t_ascent_end + 5400.0
        # Add 3 discrete sampling points across parking
        parking_samples = [
            t_ascent_end + 1800.0,
            t_ascent_end + 3600.0,
            t_parking_end,
        ]
        curr_lat = lat_list[-1]
        curr_lon = lon_list[-1]
        curr_alt = alt_list[-1]
        curr_speed = speed_list[-1]
        curr_mass = mass_list[-1]

        for pt in parking_samples:
            t_s_list.append(pt)
            phase_list.append("parking")
            # Downrange movement approximation
            curr_lon = (curr_lon + 120.0 + 180.0) % 360.0 - 180.0
            lat_list.append(curr_lat)
            lon_list.append(curr_lon)
            alt_list.append(curr_alt)
            speed_list.append(curr_speed)
            mass_list.append(curr_mass)
            q_list.append(0.0)
            accel_list.append(0.0)

        # 3. PHASING PHASE
        # Analytical Hohmann burn to close ISS altitude (e.g. 200 km -> 410 km)
        # Hohmann dv: dv1 + dv2 ~ 121 m/s
        r1 = R_EARTH + curr_alt
        r2 = R_EARTH + 410000.0  # nominal ISS orbit
        v1 = math.sqrt(MU_EARTH / r1)
        dv1 = v1 * (math.sqrt(2 * r2 / (r1 + r2)) - 1.0)
        v2 = math.sqrt(MU_EARTH / r2)
        dv2 = v2 * (1.0 - math.sqrt(2 * r1 / (r1 + r2)))
        phasing_dv = abs(dv1) + abs(dv2)

        # Propellant consumed in phasing: rocket equation dm = m * (1 - exp(-dv/(Isp*g0)))
        isp_upper = self.rocket.stages[-1].isp_s
        prop_phasing = curr_mass * (1.0 - math.exp(-phasing_dv / (isp_upper * G0)))
        t_phasing_end = t_parking_end + 7200.0  # approx 2 hours transfer/phasing

        # Add phasing trajectory samples
        for pt in [t_parking_end + 3600.0, t_phasing_end]:
            t_s_list.append(pt)
            phase_list.append("phasing")
            curr_lon = (curr_lon + 120.0 + 180.0) % 360.0 - 180.0
            lat_list.append(curr_lat)
            lon_list.append(curr_lon)
            alt_list.append(410000.0)
            speed_list.append(v2)
            curr_mass -= prop_phasing / 2.0
            mass_list.append(curr_mass)
            q_list.append(0.0)
            accel_list.append(0.0)

        total_prop_used = prop_ascent_used + prop_phasing
        total_dv = sim_res.insertion_speed_mps + phasing_dv
        time_to_dock_s = t_phasing_end

        # Trajectory events
        events = [
            {"t_s": 0.0, "name": "liftoff"},
            {"t_s": round(sim_res.t_max_q_s, 1), "name": "max_q"},
            {"t_s": round(sim_res.t_insertion_s, 1), "name": "insertion"},
            {"t_s": round(t_parking_end, 1), "name": "phasing_burn"},
            {"t_s": round(t_phasing_end, 1), "name": "phasing_complete"},
        ]

        # Construct Mission Trajectory dictionary conforming to schema
        traj_id = f"traj_{attempt_id}"
        mission_traj = {
            "schema_version": 1,
            "id": traj_id,
            "attempt_id": attempt_id,
            "t0_unix": launch_t_unix,
            "t_s": t_s_list,
            "phase": phase_list,
            "lat_deg": lat_list,
            "lon_deg": lon_list,
            "alt_m": alt_list,
            "speed_mps": speed_list,
            "mass_kg": mass_list,
            "q_pa": q_list,
            "accel_g": accel_list,
            "rel_pos_m": [None] * len(t_s_list),
            "rel_vel_mps": [None] * len(t_s_list),
            "events": events,
        }

        # Check vehicle max-q limit if defined
        max_q_limit = getattr(self.rocket, "max_q_limit_kpa", None)
        if max_q_limit is None:
            max_q_limit = 50.0  # reasonable illustrative default

        prop_total = sum(s.prop_kg for s in self.rocket.stages)
        # Construct Attempt dictionary
        attempt = {
            "schema_version": 1,
            "id": attempt_id,
            "mission_id": self.request.get("id", "msn_default"),
            "agent": agent_info
            or {"id": "cma-es-optimizer", "model": None, "version": "0.1.0"},
            "sim_version": SIM_VERSION,
            "verifier_version": "ver-placeholder",
            "seed": self.seed,
            "plan": {
                "launch_t_unix": launch_t_unix,
                "pitch_program": pitch_program,
                "parking_alt_km": target_parking_alt_km,
                "burns": [
                    {
                        "phase": "phasing",
                        "t_s": round(t_parking_end, 1),
                        "dv_mps": [round(phasing_dv, 2), 0.0, 0.0],
                        "frame": "LVLH",
                    }
                ],
                "waypoints": [
                    {"name": "hold_1km", "range_m": 1000.0},
                    {"name": "hold_200m", "range_m": 200.0},
                ],
            },
            "phases": [
                {
                    "name": "ascent",
                    "t0_s": 0.0,
                    "t1_s": round(t_ascent_end, 1),
                    "prop_kg": round(prop_ascent_used, 1),
                    "dv_mps": round(sim_res.insertion_speed_mps, 1),
                },
                {
                    "name": "parking",
                    "t0_s": round(t_ascent_end, 1),
                    "t1_s": round(t_parking_end, 1),
                    "prop_kg": 0.0,
                    "dv_mps": 0.0,
                },
                {
                    "name": "phasing",
                    "t0_s": round(t_parking_end, 1),
                    "t1_s": round(t_phasing_end, 1),
                    "prop_kg": round(prop_phasing, 1),
                    "dv_mps": round(phasing_dv, 1),
                },
            ],
            "metrics": {
                "prop_used_kg": round(total_prop_used, 1),
                "total_dv_mps": round(total_dv, 1),
                "time_to_dock_s": round(time_to_dock_s, 1),
                "perigee_km": round(sim_res.perigee_km, 2),
                "eccentricity": round(sim_res.eccentricity, 5),
                "max_q_pa": round(sim_res.max_q_pa, 1),
                "max_accel_g": round(max(accel_list), 2),
                "plane_error_deg": 0.0,
                "ascent_reserve_frac": 0.05,
            },
            "constraint_report": [],
            "feasible": False,
            "cost": 0.0,
            "best_known_gap_pct": None,
            "trajectory_ref": f"data/attempts/{attempt_id}.traj.json",
            "wall_s": round(time.perf_counter() - wall_start, 4),
            "status": "finished",
            "objective": self.request.get(
                "objective",
                {
                    "w_fuel": 1.0,
                    "w_time": 0.5,
                    "fuel_ref_kg": 500000.0,
                    "time_ref_s": 86400.0,
                },
            ),
            "config": {
                "max_q_limit_kpa": max_q_limit,
                "plane_error_deg": 0.0,
                "risk_status": "go",
                "prop_total_kg": prop_total,
            },
        }

        # 4. VERIFICATION via verifier
        constraints_prof = self.request.get("constraints_profile", "crew_default")
        v_res = verify(attempt, constraints_profile=constraints_prof)
        attempt["feasible"] = v_res["feasible"]
        attempt["cost"] = v_res["cost"]
        attempt["constraint_report"] = v_res["constraint_report"]
        attempt["verifier_version"] = v_res["verifier_version"]

        # Remove internal helper keys not defined in attempt.schema.json
        attempt.pop("objective", None)

        return attempt, mission_traj
