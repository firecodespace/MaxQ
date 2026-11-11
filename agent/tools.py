"""The agent tool layer (WP2-11, HANDOFF section 8): the only things an agent may do.

Six tools, named as the handoff names them so the names stay stable for a model's prompt:
`propose_plan`, `run_sim`, `get_constraints`, `query_memory`, `run_optimizer`,
`commit_attempt`. Every call is logged with its arguments and its result, and the log IS the
decision trace -- there is no second, prettier narrative.

Why the tool layer is the security boundary, and what it actually guarantees
---------------------------------------------------------------------------
Charter addition 1: the verifier and the benchmarks are ground truth and are READ-ONLY to any
agent and to any code path an agent controls at runtime. That is enforced here by
construction: no tool writes anywhere except `data/attempts/`, and `commit_attempt` -- the one
tool that writes at all -- resolves its path and refuses anything outside that directory.
There is no tool that takes a module name, evaluates code, or writes an arbitrary file, so an
agent driving this layer has no path to `verifier/` or `benchmarks/` at all.

What that does NOT claim: an LLM planner is a process that can only emit tool calls, so the
boundary holds for it. Python code written inside `agent/` could obviously import anything.
`test_agent_cannot_write_verifier` asserts the shape of this layer rather than sandboxing the
interpreter, and the honest statement is that the guarantee is "the agent's only interface
cannot do it", not "the process cannot do it".

The verifier is reached only through `verify()`, which returns a verdict and holds no
mutable state. Memory is reached read-only unless the orchestrator is explicitly told to
remember, and memory is a hint, never evidence (charter addition 3).

Owner: Yash (lane: agent/).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from agent.budget import Budget
from env.mission_env import SIM_VERSION, MissionEnv
from memory.store import Store
from optimize.cma_optimizer import (
    LOWER,
    PARAM_NAMES,
    UPPER,
    X0,
    optimize_mission,
    plan_from_x,
    x_from_plan,
)
from verifier import VERIFIER_VERSION
from verifier.limits import get_profile

ROOT = Path(__file__).resolve().parents[1]

#: The ONLY directory any tool may write to. Resolved and compared, not string-matched.
ATTEMPTS_DIR = (ROOT / "data" / "attempts").resolve()

TOOL_NAMES = (
    "get_constraints",
    "query_memory",
    "propose_plan",
    "run_sim",
    "run_optimizer",
    "commit_attempt",
)

#: Tool argument schemas, in the shape an LLM tool-use API wants. Declared here so that when
#: a model is chosen (RED, Yash's) the prompt side is already pinned to this layer and cannot
#: drift from it.
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_constraints": {
        "description": "The hard constraints and limits this mission is judged against. Free.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "query_memory": {
        "description": (
            "Similar past missions from experience memory, nearest first. A hint, not "
            "evidence: the verifier still judges every attempt from scratch. Free."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "k": {"type": "integer", "minimum": 1, "maximum": 20},
                "feasible_only": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    "propose_plan": {
        "description": (
            "Build a mission plan from search parameters and check it is inside the hard "
            "bounds. Runs no simulation and costs no budget."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                name: {"type": "number"} for name in PARAM_NAMES
            },
            "additionalProperties": False,
        },
    },
    "run_sim": {
        "description": (
            "Fly one plan and have the verifier judge it. Costs ONE simulation from the "
            "budget. Returns the constraint report, the metrics and the cost."
        ),
        "parameters": {
            "type": "object",
            "properties": {"plan": {"type": "object"}},
            "required": ["plan"],
            "additionalProperties": False,
        },
    },
    "run_optimizer": {
        "description": (
            "Run CMA-ES over the plan parameters for a number of simulations. Costs that "
            "many from the budget, or fewer if less remains. Returns the best attempt found."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sims": {"type": "integer", "minimum": 1},
                "warm_start": {
                    "type": "string",
                    "enum": ["cold", "centre", "bound"],
                },
            },
            "required": ["sims"],
            "additionalProperties": False,
        },
    },
    "commit_attempt": {
        "description": (
            "Record one already-simulated attempt as this mission's answer. Writes only "
            "under data/attempts/. Costs no budget. Cannot change the verdict."
        ),
        "parameters": {
            "type": "object",
            "properties": {"attempt_id": {"type": "string"}},
            "required": ["attempt_id"],
            "additionalProperties": False,
        },
    },
}


class ToolError(RuntimeError):
    """A tool was called with something it must refuse. Not fatal: the agent sees it."""


class Toolbox:
    """The tool layer bound to one mission, one budget and one trace.

    Construct it once per mission. `call(name, **kwargs)` is the single entry point, so every
    tool invocation goes through the same logging and the same refusals -- there is no way to
    call a tool without it appearing in the trace.
    """

    def __init__(
        self,
        mission_request: dict[str, Any],
        *,
        budget: Budget | None = None,
        memory: Store | None = None,
        on_event: Any = None,
        save_attempts: bool = False,
    ) -> None:
        self.request = dict(mission_request)
        self.mission_id = str(self.request.get("id", "msn_0001"))
        self.budget = budget or Budget.from_request(self.request)
        self.memory = memory
        self.save_attempts = save_attempts
        self._on_event = on_event
        self.env = MissionEnv(self.request)
        self.trace: list[dict[str, Any]] = []
        self.attempts: dict[str, dict[str, Any]] = {}
        self.committed: str | None = None
        self._n = 0

    # -- the single entry point -------------------------------------------

    def call(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """Invoke a tool by name. Everything goes through here, including refusals."""
        if name not in TOOL_NAMES:
            raise ToolError(f"unknown tool {name!r}; the layer offers {list(TOOL_NAMES)}")
        self._emit("agent_step", kind="tool_call", summary=f"{name}()",
                   data={"tool": name, "arguments": _jsonable(kwargs)})
        t0 = time.perf_counter()
        try:
            result = getattr(self, f"_t_{name}")(**kwargs)
            ok, error = True, None
        except (ToolError, TypeError) as exc:
            result, ok, error = {}, False, f"{type(exc).__name__}: {exc}"
        entry = {
            "tool": name,
            "arguments": _jsonable(kwargs),
            "ok": ok,
            "error": error,
            "result": _jsonable(_summarise(name, result)),
            "wall_s": round(time.perf_counter() - t0, 4),
            "budget": self.budget.snapshot(),
        }
        self.trace.append(entry)
        self._emit(
            "agent_step", kind="tool_result",
            summary=(f"{name} -> ok" if ok else f"{name} -> refused: {error}"),
            data=entry,
        )
        if not ok:
            raise ToolError(error or f"{name} failed")
        return result

    def decide(self, summary: str, data: dict[str, Any] | None = None) -> None:
        """Record a decision the agent made from what the tools returned.

        A DECISION, not reasoning (charter addition 8): what was chosen and on what number.
        The trace is for someone auditing the run, not a transcript of an inner monologue.
        """
        entry = {"decision": summary, "data": _jsonable(data or {})}
        self.trace.append(entry)
        self._emit("agent_step", kind="decision", summary=summary, data=entry)

    # -- the tools --------------------------------------------------------

    def _t_get_constraints(self) -> dict[str, Any]:
        limits = get_profile(self.request["constraints_profile"])
        return {
            "constraints_profile": self.request["constraints_profile"],
            "limits": {
                k: v for k, v in vars(limits).items() if not k.startswith("_")
            },
            "verifier_version": VERIFIER_VERSION,
            "sim_version": SIM_VERSION,
            "_note": (
                "Illustrative limits (HANDOFF section 5.1), not flight-certified values. "
                "Read-only: no tool can change them."
            ),
        }

    def _t_query_memory(self, k: int = 5, feasible_only: bool = True) -> dict[str, Any]:
        if self.memory is None:
            return {
                "neighbours": [],
                "_note": "no experience memory is attached to this run (a cold agent)",
            }
        hits = self.memory.search(
            self.request,
            k=int(k),
            sim_version=SIM_VERSION,
            verifier_version=VERIFIER_VERSION,
            profile=self.request["profile"],
            rocket_id=self.request["rocket_id"],
            feasible=True if feasible_only else None,
        )
        out = [
            {
                "mission_id": h["record"]["payload"]["mission_id"],
                "attempt_id": h["record"]["payload"]["attempt_id"],
                "similarity": h["similarity"],
                "cost": h["record"]["payload"]["cost"],
                "feasible": h["record"]["payload"]["feasible"],
                "failure_modes": h["record"]["payload"]["failure_modes"],
                "plan": h["record"]["payload"]["plan"],
            }
            for h in hits
        ]
        if out:
            self._emit(
                "memory_hit",
                kind="memory_hit",
                summary=f"{len(out)} similar mission(s), best similarity {out[0]['similarity']:.4f}",
                data={"neighbours": [{k2: v for k2, v in n.items() if k2 != "plan"} for n in out]},
            )
        return {
            "neighbours": out,
            "_note": (
                "Filtered to the current sim_version and verifier_version: a plan that was "
                "feasible under different physics is not a hint. A hint, never evidence."
            ),
        }

    def _t_propose_plan(self, **params: Any) -> dict[str, Any]:
        missing = [n for n in PARAM_NAMES if n not in params]
        if missing:
            raise ToolError(
                f"propose_plan needs all {len(PARAM_NAMES)} parameters; missing {missing}. "
                f"A partially specified plan would be completed with defaults and look like a "
                f"considered one."
            )
        x = np.asarray([float(params[n]) for n in PARAM_NAMES], dtype=np.float64)
        lo, hi = np.asarray(LOWER, dtype=float), np.asarray(UPPER, dtype=float)
        outside = [
            f"{PARAM_NAMES[i]}={x[i]:g} outside [{lo[i]:g}, {hi[i]:g}]"
            for i in range(len(x))
            if x[i] < lo[i] or x[i] > hi[i]
        ]
        clipped = np.clip(x, lo, hi)
        plan = plan_from_x(clipped)
        self._emit("agent_step", kind="plan", summary="proposed a plan", data={"plan": plan})
        return {
            "plan": plan,
            "clipped": bool(outside),
            "out_of_bounds": outside,
            "_note": (
                "Clipped into the hard bounds rather than refused, so a near-miss proposal "
                "still costs nothing and the agent is told what moved."
                if outside
                else "within bounds"
            ),
        }

    def _t_run_sim(self, plan: dict[str, Any]) -> dict[str, Any]:
        self.budget.spend(1)
        self._n += 1
        attempt_id = f"att_{self.mission_id}_{self._n:04d}"
        self._emit("attempt_started", data={"plan": plan}, attempt_id=attempt_id)
        attempt, trajectory = self.env.evaluate_plan(plan, attempt_id=attempt_id)
        self.attempts[attempt_id] = attempt
        self._emit(
            "attempt_finished",
            data={
                "feasible": attempt["feasible"],
                "cost": attempt["cost"],
                "metrics": attempt["metrics"],
            },
            attempt_id=attempt_id,
            summary=(
                f"{'feasible' if attempt['feasible'] else 'infeasible'}, cost "
                f"{attempt['cost']:.6f}"
            ),
        )
        if self.save_attempts:
            self._write_attempt(attempt, trajectory)
        return {
            "attempt_id": attempt_id,
            "feasible": attempt["feasible"],
            "cost": attempt["cost"],
            "metrics": attempt["metrics"],
            "constraint_report": attempt["constraint_report"],
            "verifier_version": attempt["verifier_version"],
        }

    def _t_run_optimizer(self, sims: int, warm_start: str = "centre") -> dict[str, Any]:
        wanted = int(sims)
        if wanted < 1:
            raise ToolError(f"run_optimizer needs at least 1 simulation, got {wanted}")
        affordable = self.budget.affordable(wanted)
        if affordable < 1:
            self.budget.spend(1)  # raises BudgetExhausted with the full reason
        self.budget.spend(affordable)
        best = optimize_mission(
            mission_request=self.request,
            max_attempts=affordable,
            seed=int(self.request.get("seed", 42)),
            session_log_path=None,
            save_attempts=False,
            memory=self.memory,
            warm_start_strategy=warm_start if self.memory is not None else "cold",
            remember=False,
        )
        self.attempts[best["id"]] = best
        return {
            "attempt_id": best["id"],
            "feasible": best["feasible"],
            "cost": best["cost"],
            "metrics": best["metrics"],
            "sims_requested": wanted,
            "sims_run": affordable,
            "_note": (
                f"budget allowed {affordable} of the {wanted} requested"
                if affordable != wanted
                else "ran the full request"
            ),
        }

    def _t_commit_attempt(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.attempts.get(str(attempt_id))
        if attempt is None:
            raise ToolError(
                f"{attempt_id!r} was not produced by this run. commit_attempt records an "
                f"attempt the simulator and verifier already judged; it cannot introduce one."
            )
        self.committed = str(attempt_id)
        self.decide(
            f"committed {attempt_id} as the mission answer",
            {
                "feasible": attempt["feasible"],
                "cost": attempt["cost"],
                "verifier_version": attempt["verifier_version"],
            },
        )
        return {
            "attempt_id": attempt_id,
            "feasible": attempt["feasible"],
            "cost": attempt["cost"],
            "_note": "the verdict is the verifier's and was not re-computed here",
        }

    # -- the one write path, confined ------------------------------------

    def _write_attempt(self, attempt: dict[str, Any], trajectory: dict[str, Any]) -> None:
        """Write an attempt under data/attempts/, and nowhere else.

        The path is built from the attempt id, resolved, and checked to be inside
        ATTEMPTS_DIR. An id containing `..` or an absolute path therefore cannot escape --
        which is the only filesystem write any agent-driven code path has.
        """
        ATTEMPTS_DIR.mkdir(parents=True, exist_ok=True)
        for suffix, payload in ((".json", attempt), (".traj.json", trajectory)):
            path = (ATTEMPTS_DIR / f"{attempt['id']}{suffix}").resolve()
            if ATTEMPTS_DIR not in path.parents:
                raise ToolError(
                    f"refusing to write outside data/attempts/: {path}. The verifier and the "
                    f"benchmarks are read-only to any agent (charter addition 1)."
                )
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    # -- events ------------------------------------------------------------

    def _emit(
        self,
        event_type: str,
        *,
        data: dict[str, Any] | None = None,
        attempt_id: str | None = None,
        kind: str | None = None,
        summary: str | None = None,
    ) -> None:
        if self._on_event is None:
            return
        evt: dict[str, Any] = {
            "schema_version": 1,
            "t_wall": round(self.budget.elapsed_s, 4),
            "type": event_type,
            "mission_id": self.mission_id,
        }
        if attempt_id:
            evt["attempt_id"] = attempt_id
        if kind:
            evt["kind"] = kind
        if summary:
            evt["summary"] = summary
        if data is not None:
            evt["data"] = _jsonable(data)
        self._on_event(evt)


def _summarise(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Keep the trace readable: drop the bulky parts of a tool result, keep the verdicts.

    A trace with a full constraint report per attempt becomes unreadable at a hundred
    attempts, and the attempt files hold the detail. What stays is what a reviewer needs to
    follow the decisions.
    """
    if name == "run_sim":
        return {
            k: result.get(k) for k in ("attempt_id", "feasible", "cost")
        } | {
            "violated": [
                c["id"]
                for c in result.get("constraint_report", [])
                if c.get("evaluated", True) and not c["pass"]
            ]
        }
    if name == "query_memory":
        return {
            "neighbours": [
                {k: n[k] for k in ("mission_id", "similarity", "cost", "feasible")}
                for n in result.get("neighbours", [])
            ]
        }
    if name == "propose_plan":
        return {"clipped": result.get("clipped"), "out_of_bounds": result.get("out_of_bounds")}
    return result


def _jsonable(obj: Any) -> Any:
    """Make numpy types JSON-serialisable, so the trace can always be written."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return [_jsonable(v) for v in obj.tolist()]
    return obj


__all__ = [
    "LOWER",
    "PARAM_NAMES",
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "UPPER",
    "X0",
    "ToolError",
    "Toolbox",
    "x_from_plan",
]
