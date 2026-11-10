"""CMA-ES Baseline Optimizer over plan parameters.

Emits contract-valid attempt.json files and session.jsonl streaming logs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from env.mission_env import MissionEnv

ROOT = Path(__file__).resolve().parents[1]


def optimize_mission(
    mission_request: dict[str, Any],
    max_attempts: int = 10,
    seed: int = 42,
    session_log_path: Path | str | None = None,
    save_attempts: bool = True,
) -> dict[str, Any]:
    """Execute CMA-ES optimization for a mission request.

    Parameters
    ----------
    mission_request : dict
        A valid mission_request object.
    max_attempts : int
        Maximum simulation budget.
    seed : int
        Random seed for reproducibility.
    session_log_path : Path | str | None
        Path to output session.jsonl stream.
    save_attempts : bool
        If True, writes attempt.json files to data/attempts/.

    Returns
    -------
    dict
        Best attempt found.
    """
    env = MissionEnv(mission_request)
    mission_id = mission_request.get("id", "msn_0001")
    rng = np.random.default_rng(seed)

    t_start = time.perf_counter()
    session_file = None
    if session_log_path:
        p = Path(session_log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        session_file = open(p, "w", encoding="utf-8")

    def emit_event(
        event_type: str,
        data: dict[str, Any],
        attempt_id: str | None = None,
        kind: str | None = None,
        summary: str | None = None,
    ):
        if not session_file:
            return
        t_wall = round(time.perf_counter() - t_start, 4)
        evt: dict[str, Any] = {
            "schema_version": 1,
            "t_wall": t_wall,
            "type": event_type,
            "mission_id": mission_id,
            "data": data,
        }
        if attempt_id:
            evt["attempt_id"] = attempt_id
        if kind:
            evt["kind"] = kind
        if summary:
            evt["summary"] = summary
        session_file.write(json.dumps(evt) + "\n")
        session_file.flush()

    # 1. Mission Started
    emit_event("mission_started", {
        "agent": "optimizer-cma",
        "mode": "live",
        "max_attempts": max_attempts,
    })

    # Initial parameter guess: [t_start, p_meco, p_final, a1, a2, parking_alt_km]
    default_pitch = [11.215, 19.623, -0.962, 0.782, 0.822]
    x_mean = np.array(default_pitch + [200.0], dtype=float)
    sigma = np.array([1.5, 2.0, 0.5, 0.1, 0.1, 10.0], dtype=float)

    best_attempt: dict[str, Any] | None = None
    best_cost = float("inf")
    feasible_count = 0

    attempts_dir = ROOT / "data" / "attempts"
    if save_attempts:
        attempts_dir.mkdir(parents=True, exist_ok=True)

    for i in range(max_attempts):
        attempt_id = f"att_{mission_id}_{i+1:04d}"

        # Sample parameter candidate
        if i == 0:
            candidate = x_mean.copy()
        else:
            candidate = x_mean + rng.normal(0, sigma)
            # Clip bounds
            candidate[0] = max(5.0, min(25.0, candidate[0]))
            candidate[1] = max(5.0, min(40.0, candidate[1]))
            candidate[2] = max(-5.0, min(5.0, candidate[2]))
            candidate[3] = max(0.4, min(1.5, candidate[3]))
            candidate[4] = max(0.4, min(1.5, candidate[4]))
            candidate[5] = max(160.0, min(300.0, candidate[5]))

        emit_event("attempt_started", {}, attempt_id=attempt_id)

        plan = {
            "launch_t_unix": 1794372400,
            "pitch_program": [round(float(v), 3) for v in candidate[:5]],
            "parking_alt_km": round(float(candidate[5]), 1),
            "burns": [],
            "waypoints": [],
        }

        emit_event(
            "agent_step",
            data={
                "tool": "propose_plan",
                "key_numbers": {"parking_alt_km": plan["parking_alt_km"]},
            },
            attempt_id=attempt_id,
            kind="plan",
            summary=f"Propose pitch program {plan['pitch_program']} parking {plan['parking_alt_km']}km",
        )

        attempt, traj = env.evaluate_plan(
            plan=plan,
            attempt_id=attempt_id,
            agent_info={"id": "optimizer-cma", "model": None, "version": "0.1.0"},
        )

        cost = attempt["cost"]
        is_feas = attempt["feasible"]
        if is_feas:
            feasible_count += 1

        emit_event("attempt_finished", {
            "feasible": is_feas,
            "cost": cost,
            "prop_used_kg": attempt["metrics"]["prop_used_kg"],
            "time_to_dock_s": attempt["metrics"]["time_to_dock_s"],
        }, attempt_id=attempt_id)

        if cost < best_cost:
            best_cost = cost
            best_attempt = attempt
            # Update mean toward successful candidate
            x_mean = 0.8 * x_mean + 0.2 * candidate
            emit_event("benchmark_update", {
                "best_cost": cost,
                "best_attempt_id": attempt_id,
                "best_known_gap_pct": 0.0,
            })

        if save_attempts:
            att_path = attempts_dir / f"{attempt_id}.json"
            traj_path = attempts_dir / f"{attempt_id}.traj.json"
            with open(att_path, "w", encoding="utf-8") as f:
                json.dump(attempt, f, indent=2)
            with open(traj_path, "w", encoding="utf-8") as f:
                json.dump(traj, f, indent=2)

    emit_event("mission_finished", {
        "attempts": max_attempts,
        "feasible_attempts": feasible_count,
        "stop_reason": "budget_exhausted",
    })

    if session_file:
        session_file.close()

    return best_attempt or attempt
