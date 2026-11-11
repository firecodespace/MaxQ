"""`OptimizerAgent`: the deterministic fallback orchestrator (WP2-11, HANDOFF section 8).

HANDOFF section 8 requires that "the UI, benchmark, and public demo must work with it, so
nothing blocks on a GPU or model". So this agent uses the same tool layer and emits the same
session events as an LLM planner would, with no model anywhere: its policy is a fixed
sequence of rules over what the tools return.

It is not pretending to be an LLM. There is no generated prose and no invented reasoning --
every line in its trace is a decision tied to a number a tool returned (charter addition 8:
decision traces, not raw reasoning). Calling it an "agent" is accurate in the sense the
handoff uses: something that chooses which tool to call next from what it has seen.

The policy, in full
-------------------
1. `get_constraints` -- read what the mission is judged against. Free.
2. `query_memory` -- look for similar past missions. Free.
3. If a feasible neighbour exists, `propose_plan` from it and `run_sim` it. One simulation to
   find out whether experience transfers, which is cheap and is the thing worth knowing.
4. `run_optimizer` with whatever budget remains, warm-started when memory had something.
5. `commit_attempt` on the best feasible attempt seen, or report honestly that there was
   none. A cheap infeasible attempt is never committed: an infeasible cost is a constraint
   penalty, not a mission cost.

Deliberately simple. The point of this agent is to be the baseline an LLM planner has to beat
on `sims-to-first-feasible`, not to be clever itself. If it were tuned, the comparison would
measure the tuning.

Owner: Yash (lane: agent/).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from agent.budget import Budget, BudgetExhausted
from agent.tools import PARAM_NAMES, Toolbox, ToolError, x_from_plan
from env.mission_env import SIM_VERSION
from memory.store import Store
from verifier import VERIFIER_VERSION

AGENT_ID = "optimizer-agent"
AGENT_VERSION = "0.1.0"

#: Simulations held back for the single memory-transfer probe in step 3, so that an agent with
#: a tiny budget still spends almost all of it on the search.
PROBE_SIMS = 1


class OptimizerAgent:
    """A tool-using agent with a fixed policy and no model."""

    def __init__(
        self,
        mission_request: dict[str, Any],
        *,
        memory: Store | None = None,
        session_log_path: Path | str | None = None,
        save_attempts: bool = False,
        warm_start_strategy: str = "centre",
        budget: Budget | None = None,
    ) -> None:
        self.request = dict(mission_request)
        self.warm_start_strategy = warm_start_strategy
        self._session_file = None
        if session_log_path:
            p = Path(session_log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._session_file = open(p, "w", encoding="utf-8")
        self.tools = Toolbox(
            self.request,
            budget=budget,
            memory=memory,
            on_event=self._write_event,
            save_attempts=save_attempts,
        )

    # -- session stream ---------------------------------------------------

    def _write_event(self, event: dict[str, Any]) -> None:
        if self._session_file is None:
            return
        self._session_file.write(json.dumps(event) + "\n")
        self._session_file.flush()

    def _emit(self, event_type: str, **kw: Any) -> None:
        self.tools._emit(event_type, **kw)

    # -- the run ----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Run the mission and return the best attempt, or the last one if none was feasible.

        Never raises on an exhausted budget: running out is a normal end to a mission, and the
        agent finishes with whatever it found. A tool refusal is recorded in the trace and the
        policy moves on -- an agent that stopped dead on a refused tool call would be less
        useful than one that tried the next thing.
        """
        t0 = time.perf_counter()
        self._emit(
            "mission_started",
            data={
                "agent": AGENT_ID,
                "agent_version": AGENT_VERSION,
                "model": None,
                "mode": "live",
                "search": "cma-es",
                "search_label": "CMA-ES (cma==4.5.0) driven by a fixed-policy tool-using agent",
                "max_attempts": self.tools.budget.max_sims,
                "max_wall_s": self.tools.budget.max_wall_s,
                "seed": self.request.get("seed"),
                "memory": self.tools.memory is not None,
                "sim_version": SIM_VERSION,
                "verifier_version": VERIFIER_VERSION,
                "_note": (
                    "No language model is involved. This is the deterministic fallback agent "
                    "the handoff requires so that the UI, benchmark and demo never depend on "
                    "a GPU."
                ),
            },
        )

        stop_reason = "completed"
        try:
            self.tools.call("get_constraints")
            neighbours = self._look_for_experience()
            self._probe_experience(neighbours)
            self._search()
        except BudgetExhausted as exc:
            stop_reason = exc.reason
            self.tools.decide(
                f"stopped: {exc.reason}",
                {"sims_spent": exc.spent, "sims_limit": exc.limit,
                 "elapsed_s": round(exc.elapsed_s, 3)},
            )
        except ToolError as exc:
            stop_reason = "tool_error"
            self.tools.decide(f"stopped after a tool refusal: {exc}", {})

        best = self._commit_best()
        self._emit(
            "mission_finished",
            data={
                "attempts": self.tools.budget.spent,
                "feasible_attempts": sum(
                    1 for a in self.tools.attempts.values() if a["feasible"]
                ),
                "stop_reason": stop_reason,
                "committed": self.tools.committed,
                "wall_s": round(time.perf_counter() - t0, 3),
            },
            summary=(
                f"{self.tools.budget.spent} simulation(s), "
                f"{'committed ' + self.tools.committed if self.tools.committed else 'nothing committed'}"
            ),
        )
        if self._session_file is not None:
            self._session_file.close()
            self._session_file = None
        return best

    # -- the policy -------------------------------------------------------

    def _look_for_experience(self) -> list[dict[str, Any]]:
        try:
            result = self.tools.call("query_memory", k=5, feasible_only=True)
        except ToolError:
            return []
        hits = result.get("neighbours", [])
        if hits:
            self.tools.decide(
                f"{len(hits)} feasible neighbour(s) in memory, best similarity "
                f"{hits[0]['similarity']:.4f} at cost {hits[0]['cost']:.6f}",
                {"best_neighbour": hits[0]["mission_id"]},
            )
        else:
            self.tools.decide(
                "no feasible neighbour in memory for this profile and vehicle; cold start",
                {},
            )
        return hits

    def _probe_experience(self, neighbours: list[dict[str, Any]]) -> None:
        """Spend one simulation finding out whether the best neighbour's plan transfers.

        Worth a single simulation because the answer changes what the remaining budget is
        worth: a plan that transfers means the search starts feasible, and one that does not
        is a fact the trace should carry rather than an assumption.
        """
        if not neighbours or self.tools.budget.sims_left <= PROBE_SIMS:
            return
        best = min(neighbours, key=lambda n: n["cost"])
        x = x_from_plan(best["plan"])
        if x is None:
            self.tools.decide(
                f"neighbour {best['mission_id']} has no complete plan to reuse; skipping the "
                f"transfer probe",
                {},
            )
            return
        try:
            proposed = self.tools.call(
                "propose_plan", **dict(zip(PARAM_NAMES, (float(v) for v in x)))
            )
            outcome = self.tools.call("run_sim", plan=proposed["plan"])
        except (ToolError, BudgetExhausted):
            raise
        self.tools.decide(
            (
                f"the best neighbour's plan is {'feasible' if outcome['feasible'] else 'infeasible'} "
                f"on this mission at cost {outcome['cost']:.6f}"
            ),
            {
                "neighbour": best["mission_id"],
                "transferred": outcome["feasible"],
                "attempt_id": outcome["attempt_id"],
            },
        )

    def _search(self) -> None:
        remaining = self.tools.budget.sims_left
        if remaining < 1:
            return
        strategy = self.warm_start_strategy if self.tools.memory is not None else "cold"
        self.tools.decide(
            f"spending the remaining {remaining} simulation(s) on CMA-ES, warm start "
            f"{strategy!r}",
            {"sims_left": remaining},
        )
        self.tools.call("run_optimizer", sims=remaining, warm_start=strategy)

    def _commit_best(self) -> dict[str, Any]:
        """Commit the best FEASIBLE attempt, or say plainly that there was none."""
        feasible = [a for a in self.tools.attempts.values() if a["feasible"]]
        if feasible:
            best = min(feasible, key=lambda a: a["cost"])
            try:
                self.tools.call("commit_attempt", attempt_id=best["id"])
            except ToolError:
                pass
            return best
        self.tools.decide(
            "no feasible attempt was found within the budget; committing nothing",
            {
                "attempts": len(self.tools.attempts),
                "_note": (
                    "The cheapest infeasible attempt is NOT committed. An infeasible cost is "
                    "a constraint penalty, not a mission cost, and committing one would put a "
                    "penalty score where a result belongs."
                ),
            },
        )
        attempts = list(self.tools.attempts.values())
        return min(attempts, key=lambda a: a["cost"]) if attempts else {}


def run_mission(
    mission_request: dict[str, Any],
    *,
    memory: Store | None = None,
    session_log_path: Path | str | None = None,
    save_attempts: bool = False,
    warm_start_strategy: str = "centre",
) -> dict[str, Any]:
    """Convenience entry point matching `optimize_mission`'s shape."""
    return OptimizerAgent(
        mission_request,
        memory=memory,
        session_log_path=session_log_path,
        save_attempts=save_attempts,
        warm_start_strategy=warm_start_strategy,
    ).run()
