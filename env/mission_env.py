"""MissionEnv core implementation.

Wraps ascent/sim.py and rendezvous/ (phasing + HCW approach), evaluates full
mission plans in both Plan mode (`evaluate_plan`) and Gym-style Step mode
(`reset` / `step`), and exports contract-compliant attempt and mission_trajectory
structures.
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
from rendezvous.hcw import simulate_approach
from rendezvous.phasing import solve_phasing_orbit
from verifier.core import verify

ROOT = Path(__file__).resolve().parents[1]


def _hash_sim_sources() -> str:
    h = hashlib.sha256()
    for name in (
        "env.mission_env",
        "ascent.sim",
        "rendezvous.phasing",
        "rendezvous.hcw",
    ):
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
    # Fallback to fictional archetypes with requested payload mass applied
    r = get_rocket(rocket_id)
    return Rocket(
        id=r.id,
        name=r.name,
        profile=r.profile,
        payload_kg=payload_kg,
        stages=list(r.stages),
        schema_version=r.schema_version,
        _note=r._note,
    )


class MissionEnv:
    """Deterministic environment running ASCENT -> PARKING -> PHASING -> APPROACH -> DOCK."""

    PHASES = ("ascent", "parking", "phasing", "approach")

    def __init__(self, mission_request: dict[str, Any]):
        self.request = mission_request
        self.seed = int(mission_request.get("seed", 42))
        self.profile = mission_request.get("profile", "crew_iss")
        self.payload_kg = float(mission_request.get("payload", {}).get("mass_kg", 8000.0))
        self.rocket_id = mission_request.get("rocket_id", "falcon9_block5")
        self.pad_id = mission_request.get("pad_id", "ccsfs_slc40")
        self.target_alt_km = float(mission_request.get("target", {}).get("alt_km", 410.0))
        self.pad_data = load_pad(self.pad_id)
        self.rocket = load_rocket(self.rocket_id, self.payload_kg)
        self._step_state: dict[str, Any] = {}
        self._step_idx: int = 0

    def _run_ascent_step(self, plan: dict[str, Any]) -> dict[str, Any]:
        launch_t_unix = int(plan.get("launch_t_unix", 1794372400))
        pitch_program = plan.get("pitch_program")
        if not pitch_program:
            pitch_program = DEFAULT_PITCH_PARAMS.get(
                self.rocket.id, [11.865, 18.262, -1.0, 0.81, 0.747]
            )
        pitch_program = [float(p) for p in pitch_program]

        target_parking_alt_km = float(plan.get("parking_alt_km", 200.0))
        site_lat = float(self.pad_data.get("lat_deg", 28.5))
        site_lon = float(self.pad_data.get("lon_deg", -80.6))
        azimuth_deg = float(plan.get("azimuth_deg", 44.9))
        target_incl = 51.6 if "iss" in self.profile else 28.5

        throttle_s1 = float(plan.get("throttle_s1", 1.0))
        throttle_s2 = float(plan.get("throttle_s2", 1.0))
        max_g_limit = plan.get("max_g_limit", None)
        if max_g_limit is not None:
            max_g_limit = float(max_g_limit)
        coast_s2_s = float(plan.get("coast_s2_s", 0.0))
        s2_burn1_frac = float(plan.get("s2_burn1_frac", 1.0))

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
            throttle_s1=throttle_s1,
            throttle_s2=throttle_s2,
            max_g_limit=max_g_limit,
            coast_s2_s=coast_s2_s,
            s2_burn1_frac=s2_burn1_frac,
        )

        ascent_traj = sim_res.trajectory
        t_s_list = [float(t) for t in ascent_traj["t_s"]]
        speed_list = [float(x) for x in ascent_traj["speed_mps"]]
        accel_list = [0.0]
        for i in range(1, len(speed_list)):
            dt = t_s_list[i] - t_s_list[i - 1]
            dv = speed_list[i] - speed_list[i - 1]
            accel_list.append(round((dv / max(1e-3, dt)) / G0, 3))

        prop_ascent_used = float(sim_res.s1_prop_used_kg + sim_res.s2_prop_used_kg)

        return {
            "launch_t_unix": launch_t_unix,
            "pitch_program": pitch_program,
            "target_parking_alt_km": target_parking_alt_km,
            "throttle_s1": throttle_s1,
            "throttle_s2": throttle_s2,
            "max_g_limit": max_g_limit,
            "coast_s2_s": coast_s2_s,
            "s2_burn1_frac": s2_burn1_frac,
            "sim_res": sim_res,
            "t_ascent_end": float(ascent_traj["t_s"][-1]),
            "t_s_list": t_s_list,
            "phase_list": ["ascent"] * len(t_s_list),
            "lat_list": [float(x) for x in ascent_traj["lat_deg"]],
            "lon_list": [float(x) for x in ascent_traj["lon_deg"]],
            "alt_list": [float(x) for x in ascent_traj["alt_m"]],
            "speed_list": speed_list,
            "mass_list": [float(x) for x in ascent_traj["mass_kg"]],
            "q_list": [float(x) for x in ascent_traj["q_pa"]],
            "accel_list": accel_list,
            "rel_pos_list": [None] * len(t_s_list),
            "rel_vel_list": [None] * len(t_s_list),
            "prop_ascent_used": prop_ascent_used,
            "s2_prop_rem": float(sim_res.s2_prop_remaining_kg),
            "max_accel_g": max(float(sim_res.max_accel_g), float(max(accel_list))),
        }

    def _run_parking_step(self, state: dict[str, Any], plan: dict[str, Any]) -> None:
        parking_coast_s = max(600.0, float(plan.get("parking_coast_s", 1800.0)))
        t0 = state["t_ascent_end"]
        t1 = t0 + parking_coast_s
        curr_lat = state["lat_list"][-1]
        curr_lon = state["lon_list"][-1]
        curr_alt = state["alt_list"][-1]
        curr_speed = state["speed_list"][-1]
        curr_mass = state["mass_list"][-1]

        for frac in (1.0 / 3.0, 2.0 / 3.0, 1.0):
            state["t_s_list"].append(round(t0 + frac * parking_coast_s, 2))
            state["phase_list"].append("parking")
            curr_lon = (curr_lon + 45.0 + 180.0) % 360.0 - 180.0
            state["lat_list"].append(curr_lat)
            state["lon_list"].append(curr_lon)
            state["alt_list"].append(curr_alt)
            state["speed_list"].append(curr_speed)
            state["mass_list"].append(curr_mass)
            state["q_list"].append(0.0)
            state["accel_list"].append(0.0)
            state["rel_pos_list"].append(None)
            state["rel_vel_list"].append(None)

        state["t_parking_end"] = t1

    def _run_phasing_step(self, state: dict[str, Any], plan: dict[str, Any]) -> None:
        r_park_km = max(120.0, state["target_parking_alt_km"])
        r_tgt_km = self.target_alt_km
        r_tgt_m = R_EARTH + r_tgt_km * 1000.0
        omega_tgt = math.sqrt(MU_EARTH / (r_tgt_m**3))
        v_tgt = math.sqrt(MU_EARTH / r_tgt_m)

        # Deterministic initial phase angle based on launch timestamp (unless explicit)
        if "phase_angle_deg" in plan:
            phase_angle_deg = float(plan["phase_angle_deg"])
        else:
            dt_epoch = state["launch_t_unix"] - 1794286000
            phase_angle_deg = math.degrees(omega_tgt * dt_epoch) % 360.0

        phasing_revs = int(round(float(plan.get("phasing_revs", 8))))
        phasing_revs = min(20, max(1, phasing_revs))

        phasing = solve_phasing_orbit(
            r_park_alt_km=r_park_km,
            r_target_alt_km=r_tgt_km,
            phase_angle_deg=phase_angle_deg,
            n_revs=phasing_revs,
        )
        phasing_dv = float(phasing["total_dv_mps"])
        phasing_dur = float(phasing["total_time_s"])

        curr_mass = state["mass_list"][-1]
        isp_upper = self.rocket.stages[-1].isp_s
        prop_phasing = curr_mass * (1.0 - math.exp(-phasing_dv / (isp_upper * G0)))

        t_start = state["t_parking_end"]
        t_end = t_start + phasing_dur
        curr_lat = state["lat_list"][-1]
        curr_lon = state["lon_list"][-1]

        for frac in (0.5, 1.0):
            state["t_s_list"].append(round(t_start + frac * phasing_dur, 2))
            state["phase_list"].append("phasing")
            curr_lon = (curr_lon + 120.0 + 180.0) % 360.0 - 180.0
            state["lat_list"].append(curr_lat)
            state["lon_list"].append(curr_lon)
            alt_m = state["alt_list"][-1] + (r_tgt_km * 1000.0 - state["alt_list"][-1]) * frac
            state["alt_list"].append(round(alt_m, 1))
            state["speed_list"].append(round(v_tgt, 1))
            curr_mass -= prop_phasing / 2.0
            state["mass_list"].append(round(curr_mass, 1))
            state["q_list"].append(0.0)
            state["accel_list"].append(0.0)
            state["rel_pos_list"].append(None)
            state["rel_vel_list"].append(None)

        state["t_phasing_end"] = t_end
        state["phasing_revs"] = phasing_revs
        state["phase_angle_deg"] = round(phase_angle_deg, 2)
        state["phasing_dv"] = phasing_dv
        state["prop_phasing"] = prop_phasing

    def _run_approach_step(self, state: dict[str, Any], plan: dict[str, Any]) -> None:
        hold_1km_tof = float(plan.get("hold_1km_tof_s", 900.0))
        hold_200m_tof = float(plan.get("hold_200m_tof_s", 600.0))
        final_tof = float(plan.get("final_tof_s", 800.0))

        approach = simulate_approach(
            hold_1km_tof_s=hold_1km_tof,
            hold_200m_tof_s=hold_200m_tof,
            final_tof_s=final_tof,
            target_alt_km=self.target_alt_km,
            dt_s=60.0,
        )
        approach_dv = float(approach["total_dv_mps"])
        approach_dur = float(approach["total_time_s"])

        curr_mass = state["mass_list"][-1]
        isp_upper = self.rocket.stages[-1].isp_s
        prop_approach = curr_mass * (1.0 - math.exp(-approach_dv / (isp_upper * G0)))

        r_tgt_m = R_EARTH + self.target_alt_km * 1000.0
        v_tgt = math.sqrt(MU_EARTH / r_tgt_m)
        t_start = state["t_phasing_end"]
        curr_lat = state["lat_list"][-1]
        curr_lon = state["lon_list"][-1]
        samples = approach["samples"]
        n_samp = max(1, len(samples))

        for idx, smp in enumerate(samples, start=1):
            t_s = t_start + float(smp["t_rel_s"])
            rel_pos = list(smp["rel_pos_m"])
            rel_vel = list(smp["rel_vel_mps"])
            curr_lon = (curr_lon + 1.5 + 180.0) % 360.0 - 180.0
            state["t_s_list"].append(round(t_s, 2))
            state["phase_list"].append(str(smp["phase"]))
            state["lat_list"].append(curr_lat)
            state["lon_list"].append(curr_lon)
            state["alt_list"].append(round(self.target_alt_km * 1000.0 + float(rel_pos[0]), 1))
            state["speed_list"].append(round(v_tgt + float(rel_vel[1]), 2))
            state["mass_list"].append(round(curr_mass - prop_approach * (idx / n_samp), 1))
            state["q_list"].append(0.0)
            state["accel_list"].append(0.0)
            state["rel_pos_list"].append(rel_pos)
            state["rel_vel_list"].append(rel_vel)

        state["t_dock_end"] = t_start + approach_dur
        state["hold_1km_tof_s"] = hold_1km_tof
        state["hold_200m_tof_s"] = hold_200m_tof
        state["final_tof_s"] = final_tof
        state["approach_dv"] = approach_dv
        state["prop_approach"] = prop_approach
        state["approach_telemetry"] = approach["approach_telemetry"]

    def _finalize(
        self,
        state: dict[str, Any],
        attempt_id: str,
        agent_info: dict[str, Any] | None,
        wall_start: float,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        sim_res = state["sim_res"]
        t_ascent_end = state["t_ascent_end"]
        t_parking_end = state["t_parking_end"]
        t_phasing_end = state["t_phasing_end"]
        t_dock_end = state["t_dock_end"]

        prop_ascent = state["prop_ascent_used"]
        prop_phasing = state["prop_phasing"]
        prop_approach = state["prop_approach"]
        total_prop_used = prop_ascent + prop_phasing + prop_approach
        total_dv = sim_res.insertion_speed_mps + state["phasing_dv"] + state["approach_dv"]

        prop_total = sum(s.prop_kg for s in self.rocket.stages)
        s2_rem_at_insertion = max(0.0, float(state["s2_prop_rem"]))
        ascent_reserve_frac = s2_rem_at_insertion / max(1.0, prop_total)
        orbital_needed = prop_phasing + prop_approach
        orbital_reserve_frac = (
            max(0.0, (s2_rem_at_insertion - orbital_needed) / s2_rem_at_insertion)
            if s2_rem_at_insertion > 0.0
            else 0.0
        )

        events = [
            {"t_s": 0.0, "name": "liftoff"},
            {"t_s": round(sim_res.t_max_q_s, 1), "name": "max_q"},
            {"t_s": round(sim_res.t_insertion_s, 1), "name": "insertion"},
            {"t_s": round(t_parking_end, 1), "name": "phasing_burn"},
            {"t_s": round(t_phasing_end, 1), "name": "phasing_complete"},
            {"t_s": round(t_dock_end, 1), "name": "dock"},
        ]

        mission_traj = {
            "schema_version": 1,
            "id": f"traj_{attempt_id}",
            "attempt_id": attempt_id,
            "t0_unix": state["launch_t_unix"],
            "t_s": state["t_s_list"],
            "phase": state["phase_list"],
            "lat_deg": state["lat_list"],
            "lon_deg": state["lon_list"],
            "alt_m": state["alt_list"],
            "speed_mps": state["speed_list"],
            "mass_kg": state["mass_list"],
            "q_pa": state["q_list"],
            "accel_g": state["accel_list"],
            "rel_pos_m": state["rel_pos_list"],
            "rel_vel_mps": state["rel_vel_list"],
            "events": events,
        }

        max_q_limit = getattr(self.rocket, "max_q_limit_kpa", None) or 50.0
        plan_record: dict[str, Any] = {
            "launch_t_unix": state["launch_t_unix"],
            "pitch_program": state["pitch_program"],
            "parking_alt_km": state["target_parking_alt_km"],
            "throttle_s1": state["throttle_s1"],
            "throttle_s2": state["throttle_s2"],
            "coast_s2_s": state["coast_s2_s"],
            "s2_burn1_frac": state["s2_burn1_frac"],
            "phasing_revs": state["phasing_revs"],
            "final_tof_s": state["final_tof_s"],
            "burns": [
                {
                    "phase": "phasing",
                    "t_s": round(t_parking_end, 1),
                    "dv_mps": [round(state["phasing_dv"], 2), 0.0, 0.0],
                    "frame": "LVLH",
                },
                {
                    "phase": "approach",
                    "t_s": round(t_phasing_end, 1),
                    "dv_mps": [0.0, round(state["approach_dv"], 2), 0.0],
                    "frame": "LVLH",
                },
            ],
            "waypoints": [
                {"name": "hold_1km", "range_m": 1000.0},
                {"name": "hold_200m", "range_m": 200.0},
            ],
        }
        if state["max_g_limit"] is not None:
            plan_record["max_g_limit"] = state["max_g_limit"]

        attempt = {
            "schema_version": 1,
            "id": attempt_id,
            "mission_id": self.request.get("id", "msn_default"),
            "agent": agent_info or {"id": "unknown-caller", "model": None, "version": "0.0.0"},
            "sim_version": SIM_VERSION,
            "verifier_version": "ver-placeholder",
            "seed": self.seed,
            "plan": plan_record,
            "phases": [
                {
                    "name": "ascent",
                    "t0_s": 0.0,
                    "t1_s": round(t_ascent_end, 1),
                    "prop_kg": round(prop_ascent, 1),
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
                    "dv_mps": round(state["phasing_dv"], 1),
                },
                {
                    "name": "approach",
                    "t0_s": round(t_phasing_end, 1),
                    "t1_s": round(t_dock_end, 1),
                    "prop_kg": round(prop_approach, 1),
                    "dv_mps": round(state["approach_dv"], 1),
                },
            ],
            "metrics": {
                "prop_used_kg": round(total_prop_used, 1),
                "total_dv_mps": round(total_dv, 1),
                "time_to_dock_s": round(t_dock_end, 1),
                "perigee_km": round(sim_res.perigee_km, 2),
                "eccentricity": round(sim_res.eccentricity, 5),
                "max_q_pa": round(sim_res.max_q_pa, 1),
                "max_accel_g": round(state["max_accel_g"], 3),
                "plane_error_deg": 0.0,
                "ascent_reserve_frac": round(ascent_reserve_frac, 5),
                "orbital_reserve_frac": round(orbital_reserve_frac, 5),
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
                "phase_angle_deg": state["phase_angle_deg"],
                "approach_telemetry": state["approach_telemetry"],
            },
        }

        constraints_prof = self.request.get("constraints_profile", "crew_default")
        v_res = verify(attempt, constraints_profile=constraints_prof)
        attempt["feasible"] = v_res["feasible"]
        attempt["cost"] = v_res["cost"]
        attempt["constraint_report"] = v_res["constraint_report"]
        attempt["verifier_version"] = v_res["verifier_version"]
        attempt.pop("objective", None)
        return attempt, mission_traj

    def evaluate_plan(
        self,
        plan: dict[str, Any],
        attempt_id: str = "att_000001",
        agent_info: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Evaluate a full mission plan deterministically (Plan mode)."""
        wall_start = time.perf_counter()
        state = self._run_ascent_step(plan)
        self._run_parking_step(state, plan)
        self._run_phasing_step(state, plan)
        self._run_approach_step(state, plan)
        return self._finalize(state, attempt_id, agent_info, wall_start)

    def reset(self, seed: int | None = None) -> dict[str, Any]:
        """Reset environment for phase-by-phase RL Step mode (WP2-02)."""
        if seed is not None:
            self.seed = int(seed)
        self._step_idx = 0
        self._step_state = {
            "wall_start": time.perf_counter(),
            "launch_t_unix": int(self.request.get("launch_t_unix", 1794372400)),
        }
        return {
            "phase_index": 0,
            "phase_name": self.PHASES[0],
            "t_s": 0.0,
            "alt_m": 0.0,
            "speed_mps": 0.0,
            "mass_kg": float(self.rocket.liftoff_mass_kg),
            "s2_prop_remaining_kg": float(self.rocket.stages[-1].prop_kg),
        }

    def step(
        self,
        action: dict[str, Any],
        attempt_id: str = "att_step_0001",
        agent_info: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        """Execute one phase action in Step mode.

        Phase sequence: 0=ascent, 1=parking, 2=phasing, 3=approach -> terminal dock.
        """
        if self._step_idx >= len(self.PHASES):
            raise RuntimeError("Episode already finished; call reset() before step()")

        phase = self.PHASES[self._step_idx]
        if phase == "ascent":
            wall_start = self._step_state.get("wall_start", time.perf_counter())
            self._step_state = self._run_ascent_step(action)
            self._step_state["wall_start"] = wall_start
        elif phase == "parking":
            self._run_parking_step(self._step_state, action)
        elif phase == "phasing":
            self._run_phasing_step(self._step_state, action)
        elif phase == "approach":
            self._run_approach_step(self._step_state, action)

        self._step_idx += 1
        terminated = self._step_idx >= len(self.PHASES)

        if terminated:
            attempt, traj = self._finalize(
                self._step_state,
                attempt_id=attempt_id,
                agent_info=agent_info,
                wall_start=self._step_state["wall_start"],
            )
            obs = {
                "phase_index": self._step_idx,
                "phase_name": "dock",
                "t_s": float(self._step_state["t_dock_end"]),
                "alt_m": float(self._step_state["alt_list"][-1]),
                "speed_mps": float(self._step_state["speed_list"][-1]),
                "mass_kg": float(self._step_state["mass_list"][-1]),
                "s2_prop_remaining_kg": max(
                    0.0,
                    float(self._step_state["s2_prop_rem"])
                    - float(self._step_state["prop_phasing"])
                    - float(self._step_state["prop_approach"]),
                ),
            }
            reward = -float(attempt["cost"])
            info = {"attempt": attempt, "trajectory": traj}
            return obs, reward, True, False, info

        next_phase = self.PHASES[self._step_idx]
        obs = {
            "phase_index": self._step_idx,
            "phase_name": next_phase,
            "t_s": float(self._step_state["t_s_list"][-1]),
            "alt_m": float(self._step_state["alt_list"][-1]),
            "speed_mps": float(self._step_state["speed_list"][-1]),
            "mass_kg": float(self._step_state["mass_list"][-1]),
            "s2_prop_remaining_kg": float(self._step_state["s2_prop_rem"]),
        }
        return obs, 0.0, False, False, {"completed_phase": phase}
