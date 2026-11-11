"""CMA-ES search over ascent plan parameters, with MissionEnv in the loop.

This is real CMA-ES, from the pinned `cma` package, via `optimize.search_core.cma_minimize`. The
name is load-bearing: see AGENTS.md charter items 11 and 12. What was previously in this file was a
fixed-sigma Gaussian sampler, and it now lives, correctly named, in `optimize/fixed_sigma_es.py`.

What CMA-ES buys over that baseline is adaptation. It estimates the step size and the full
covariance matrix from the samples that scored well, so the search distribution stretches along
whatever direction the landscape rewards and shrinks where it does not. Each generation's sigma and
covariance condition number are emitted into the session log, so "it adapts" is visible in the
replay rather than asserted here.

What it does NOT buy, and what no search can:
  - The simulator's own error against published payload figures is in the tens of percent. A better
    search finds a better optimum *of this model*, which is not the same as a better mission.
  - There is still no best-known benchmark (WP2-12), so `best_known_gap_pct` is null.
  - There is still no approach or docking phase (WP2-03), so `time_to_dock_s` is the end of
    phasing. Half the objective measures something other than what its name says.
  - This searches six ascent parameters. It is not a mission planner, and there is no agent.

Bounds note: this uses wider pitch bounds than the baseline did. The baseline inherited
`ascent/optimize.py`'s bounds, which were fitted around the three fictional archetypes, and inside
them no real vehicle could satisfy C01 at all (reproduce with `python scripts/probe_feasibility.py`).
Searching real vehicles inside bounds that exclude every feasible solution would measure the
bounds, not the optimiser.

Owner: Yash (lane: optimize/, by Yash's direction -- see status/yash.md).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from env.mission_env import MissionEnv
from optimize.search_core import cma_minimize

ROOT = Path(__file__).resolve().parents[1]

AGENT_ID = "search-cma-es"
AGENT_VERSION = "0.1.0"

#: [t_start_s, p_meco_deg, p_final_deg, alpha1, alpha2, parking_alt_km].
#: Pitch bounds are the wide ones from scripts/probe_feasibility.py, inside which real vehicles can
#: actually reach orbit; letting p_final go well below the horizon is the part that matters.
LOWER = [4.0, 0.0, -25.0, 0.3, 0.3, 160.0]
UPPER = [30.0, 45.0, 25.0, 1.8, 1.8, 400.0]
X0 = [11.215, 19.623, -0.962, 0.782, 0.822, 200.0]
#: CMA-ES searches a normalised space, so one sigma0 covers parameters whose natural scales differ
#: by orders of magnitude (degrees against kilometres).
SIGMA0 = 0.25
SCALES = [4.0, 8.0, 8.0, 0.3, 0.3, 40.0]


def optimize_mission(
    mission_request: dict[str, Any],
    max_attempts: int = 10,
    seed: int = 42,
    session_log_path: Path | str | None = None,
    save_attempts: bool = True,
) -> dict[str, Any]:
    """Run CMA-ES over the ascent plan parameters for one mission request.

    Parameters
    ----------
    mission_request : dict
        A valid mission_request object.
    max_attempts : int
        Simulation budget, in evaluations. Enforced here as well as by the server's subprocess
        wall-clock kill, per charter item 9.
    seed : int
        Recorded in every attempt. Same mission + seed + versions must give the same result.
    session_log_path : Path | str | None
        Where to stream session.jsonl. The live stream and the replay are these same bytes.
    save_attempts : bool
        Write attempt.json / .traj.json files under ROOT/data/attempts.

    Returns
    -------
    dict
        The best attempt found, by cost. Infeasible attempts always lose to feasible ones; that
        ordering is the verifier's, not this module's.
    """
    env = MissionEnv(mission_request)
    mission_id = mission_request.get("id", "msn_0001")

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
    ) -> None:
        if not session_file:
            return
        evt: dict[str, Any] = {
            "schema_version": 1,
            "t_wall": round(time.perf_counter() - t_start, 4),
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

    emit_event(
        "mission_started",
        {
            "agent": AGENT_ID,
            "mode": "live",
            "search": "cma-es",
            "search_label": "CMA-ES (cma==4.5.0): adapts step size and full covariance",
            "max_attempts": max_attempts,
            "seed": seed,
        },
    )

    attempts_dir = ROOT / "data" / "attempts"
    if save_attempts:
        attempts_dir.mkdir(parents=True, exist_ok=True)

    #: The current CMA-ES search distribution, updated once per generation and reported on every
    #: sample. Generation 0 is the initial isotropic distribution, before any adaptation.
    dist: dict[str, Any] = {"generation": 0, "sigma": SIGMA0, "cov_condition": 1.0}

    state: dict[str, Any] = {
        "n": 0,
        "best_attempt": None,
        "best_cost": float("inf"),
        "feasible": 0,
        "last": None,
    }

    def evaluate(x: np.ndarray) -> float:
        i = int(state["n"])
        state["n"] = i + 1
        attempt_id = f"att_{mission_id}_{i + 1:04d}"

        emit_event("attempt_started", {}, attempt_id=attempt_id)

        plan = {
            "launch_t_unix": 1794372400,
            "pitch_program": [round(float(v), 3) for v in x[:5]],
            "parking_alt_km": round(float(x[5]), 1),
            "burns": [],
            "waypoints": [],
        }

        emit_event(
            "agent_step",
            data={
                "note": "CMA-ES sample from the current search distribution",
                "key_numbers": {
                    "parking_alt_km": plan["parking_alt_km"],
                    "generation": dist["generation"],
                    "sigma": round(float(dist["sigma"]), 6),
                    "cov_condition": round(float(dist["cov_condition"]), 4),
                },
            },
            attempt_id=attempt_id,
            kind="note",
            summary=(
                f"CMA-ES gen {dist['generation']} (sigma {dist['sigma']:.4g}, covariance "
                f"condition {dist['cov_condition']:.4g}) sampled pitch program "
                f"{plan['pitch_program']} parking {plan['parking_alt_km']}km "
                f"(search only: no agent reasoning)"
            ),
        )

        attempt, traj = env.evaluate_plan(
            plan=plan,
            attempt_id=attempt_id,
            agent_info={"id": AGENT_ID, "model": None, "version": AGENT_VERSION},
        )
        cost = float(attempt["cost"])
        if attempt["feasible"]:
            state["feasible"] = int(state["feasible"]) + 1

        emit_event(
            "attempt_finished",
            {
                "feasible": attempt["feasible"],
                "cost": cost,
                "prop_used_kg": attempt["metrics"]["prop_used_kg"],
                "time_to_dock_s": attempt["metrics"]["time_to_dock_s"],
            },
            attempt_id=attempt_id,
        )

        state["last"] = attempt
        if cost < float(state["best_cost"]):
            state["best_cost"] = cost
            state["best_attempt"] = attempt
            emit_event(
                "benchmark_update",
                {
                    "best_cost": cost,
                    "best_attempt_id": attempt_id,
                    # No best-known benchmark exists yet (WP2-12). A missing measurement is null,
                    # not 0.0 -- a zeroed gap reads as "already matches the best known solution".
                    "best_known_gap_pct": None,
                },
            )

        if save_attempts:
            with open(attempts_dir / f"{attempt_id}.json", "w", encoding="utf-8") as f:
                json.dump(attempt, f, indent=2)
            with open(attempts_dir / f"{attempt_id}.traj.json", "w", encoding="utf-8") as f:
                json.dump(traj, f, indent=2)

        return cost

    def on_generation(g: dict[str, Any]) -> None:
        # Record the distribution the NEXT generation's samples will be drawn from. It rides along
        # on each attempt's own agent_step rather than becoming an event of its own, because a
        # generation is not an attempt and session.schema.json rightly requires agent_step to name
        # a running one. Carrying it per attempt is also more informative: every sample says which
        # distribution produced it, so the adaptation is legible attempt by attempt in the replay.
        dist.update(
            generation=int(g["generation"]),
            sigma=float(g["sigma"]),
            cov_condition=float(g["cov_condition"]),
        )

    scales = np.asarray(SCALES, dtype=float)
    result = cma_minimize(
        f=lambda xn: evaluate(np.asarray(xn) * scales),
        x0=np.asarray(X0, dtype=float) / scales,
        sigma0=SIGMA0,
        budget=max_attempts,
        seed=seed,
        lower=np.asarray(LOWER, dtype=float) / scales,
        upper=np.asarray(UPPER, dtype=float) / scales,
        on_generation=on_generation,
    )

    emit_event(
        "mission_finished",
        {
            "attempts": result.evaluations,
            "feasible_attempts": state["feasible"],
            "stop_reason": "budget_exhausted",
            "generations": len(result.generations),
        },
    )

    if session_file:
        session_file.close()

    return state["best_attempt"] or state["last"]
