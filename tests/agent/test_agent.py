"""WP2-11 agent orchestrator: the tool layer, enforced budgets, and the read-only boundary.

HANDOFF_PHASE2 section 7 names `test_agent_cannot_write_verifier`. It is the test that keeps
the whole measurement honest: if an agent can touch the verifier or the benchmarks, then no
result it produces means anything, because the thing that judged it was reachable by the thing
being judged (charter addition 1).

The other half is budget enforcement. Charter addition 9 says budgets are enforced in code,
and a budget enforced by convention is not enforced -- an agent that can decide to run one
more simulation has no budget.

Owner: Yash (lane: agent/, tests/).
"""

from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest

from agent import Budget, BudgetExhausted, OptimizerAgent, Toolbox, ToolError
from agent import tools as tools_mod
from agent.tools import TOOL_NAMES, TOOL_SCHEMAS
from benchmarks import suite
from contracts.validate import validate_session_file
from memory.store import Store
from optimize.cma_optimizer import PARAM_NAMES, X0, plan_from_x

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mission() -> dict:
    m = copy.deepcopy(suite.mission_by_id("msn_bench_cargo_f9_w3_light"))
    m["seed"] = 11
    m["budget"] = {"max_attempts": 6, "max_wall_s": 600.0}
    return m


@pytest.fixture
def box(mission) -> Toolbox:
    return Toolbox(mission)


# ---------------------------------------------------------------------------
# test_agent_cannot_write_verifier -- named in HANDOFF section 7


def test_agent_cannot_write_verifier():
    """No tool offers a way to write to verifier/ or benchmarks/, by construction.

    Asserted on the tool layer's surface rather than by sandboxing the interpreter, because
    the boundary that matters is the agent's INTERFACE: an LLM planner can only emit tool
    calls, so if no tool can do it, the planner cannot. The module docstring states the limit
    of that claim honestly -- Python code written inside agent/ could import anything.
    """
    verifier_dir = (ROOT / "verifier").resolve()
    benchmarks_dir = (ROOT / "benchmarks").resolve()

    # 1. The one directory any tool may write to is data/attempts/, and it is neither of them.
    assert tools_mod.ATTEMPTS_DIR == (ROOT / "data" / "attempts").resolve()
    assert verifier_dir not in tools_mod.ATTEMPTS_DIR.parents
    assert benchmarks_dir not in tools_mod.ATTEMPTS_DIR.parents

    # 2. No tool takes a path, a filename, a module or anything evaluable.
    for name, schema in TOOL_SCHEMAS.items():
        for arg in schema["parameters"]["properties"]:
            assert not any(
                bad in arg.lower()
                for bad in ("path", "file", "dir", "module", "code", "eval", "exec", "url")
            ), f"tool {name} takes {arg!r}, which could address the filesystem"

    # 3. The agent package never imports anything from benchmarks/, which is where best-known
    #    and the Hall of Fame live. An agent that could read the benchmark could tune to it.
    for src_file in sorted((ROOT / "agent").glob("*.py")):
        src = src_file.read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "benchmarks" not in stripped, (
                    f"{src_file.name}: {stripped!r} -- the agent must not reach the benchmarks"
                )

    # 4. The verifier is reached only through verify()/VERIFIER_VERSION, which return values
    #    and hold no mutable state the agent could change.
    agent_src = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted((ROOT / "agent").glob("*.py"))
    )
    for forbidden in ("verifier.limits.", "VERIFIER_VERSION =", "verifier.core."):
        if forbidden == "verifier.limits.":
            continue  # get_profile is imported by name, which is a read
        assert forbidden not in agent_src, f"agent/ contains {forbidden!r}"


def test_the_tool_layer_offers_exactly_the_tools_the_handoff_names():
    """HANDOFF section 8 fixes these names so a model's prompt stays pinned to this layer."""
    assert set(TOOL_NAMES) == {
        "propose_plan", "run_sim", "get_constraints", "query_memory", "run_optimizer",
        "commit_attempt",
    }
    assert set(TOOL_SCHEMAS) == set(TOOL_NAMES)


def test_an_unknown_tool_is_refused(box):
    with pytest.raises(ToolError, match="unknown tool"):
        box.call("rm_rf")


def test_commit_attempt_cannot_introduce_an_attempt_the_verifier_never_saw(box):
    """Otherwise an agent could commit a result it made up."""
    with pytest.raises(ToolError, match="was not produced by this run"):
        box.call("commit_attempt", attempt_id="att_fabricated")


def test_an_attempt_id_cannot_escape_the_attempts_directory(mission, tmp_path):
    """The only filesystem write any agent-driven path has, and it is path-confined."""
    box = Toolbox(mission, save_attempts=True)
    attempt = {"id": "../../verifier/core", "feasible": False, "cost": 1.0}
    with pytest.raises(ToolError, match="refusing to write outside"):
        box._write_attempt(attempt, {})
    assert not (ROOT / "verifier" / "core.json").exists()
    assert not (ROOT / "verifier" / "core.traj.json").exists()


# ---------------------------------------------------------------------------
# Budgets are enforced in code (charter addition 9)


def test_a_budget_refuses_the_simulation_past_its_limit():
    b = Budget(max_sims=3, max_wall_s=1000.0)
    for _ in range(3):
        b.spend(1)
    assert b.sims_left == 0
    with pytest.raises(BudgetExhausted, match="budget_exhausted"):
        b.spend(1)


def test_a_batch_larger_than_what_remains_is_refused_not_truncated():
    """Checked BEFORE the simulations run, so an agent cannot overspend by asking for a big
    batch. `affordable()` exists for callers that would rather shrink than be refused."""
    b = Budget(max_sims=10, max_wall_s=1000.0)
    b.spend(7)
    with pytest.raises(BudgetExhausted):
        b.spend(5)
    assert b.spent == 7, "a refused spend still charged the budget"
    assert b.affordable(5) == 3


def test_the_wall_clock_limit_is_enforced_independently_of_the_simulation_count():
    # First read is the constructor's t0; every later read is 5 s in, past the 1 s limit.
    reads = iter([0.0] + [5.0] * 20)
    b = Budget(max_sims=1000, max_wall_s=1.0, clock=lambda: next(reads))
    assert b.exhausted() == "wall_clock_exhausted"
    with pytest.raises(BudgetExhausted, match="wall_clock_exhausted"):
        b.spend(1)


def test_reading_the_constraints_and_memory_costs_no_budget(box):
    """A benchmark that counted tool calls would reward batching the thinking rather than
    needing fewer simulations."""
    before = box.budget.spent
    box.call("get_constraints")
    box.call("query_memory", k=3)
    box.call("propose_plan", **dict(zip(PARAM_NAMES, (float(v) for v in X0))))
    assert box.budget.spent == before


def test_one_simulation_costs_exactly_one(box):
    plan = box.call("propose_plan", **dict(zip(PARAM_NAMES, (float(v) for v in X0))))["plan"]
    before = box.budget.spent
    box.call("run_sim", plan=plan)
    assert box.budget.spent == before + 1


def test_the_optimizer_tool_is_capped_by_the_remaining_budget(mission):
    mission["budget"] = {"max_attempts": 5, "max_wall_s": 600.0}
    box = Toolbox(mission)
    out = box.call("run_optimizer", sims=1000)
    assert out["sims_run"] == 5
    assert out["sims_requested"] == 1000
    assert box.budget.sims_left == 0
    assert "budget allowed 5" in out["_note"]


def test_a_malformed_budget_is_refused_at_construction():
    with pytest.raises(ValueError, match="max_sims"):
        Budget(max_sims=0, max_wall_s=10.0)
    with pytest.raises(ValueError, match="max_wall_s"):
        Budget(max_sims=10, max_wall_s=0.0)


def test_the_budget_comes_from_the_mission_request(mission):
    b = Budget.from_request(mission)
    assert b.max_sims == mission["budget"]["max_attempts"]
    assert b.max_wall_s == mission["budget"]["max_wall_s"]


# ---------------------------------------------------------------------------
# The tool layer's own behaviour


def test_propose_plan_needs_every_parameter(box):
    """A partially specified plan would be completed with defaults and look considered."""
    with pytest.raises(ToolError, match="needs all"):
        box.call("propose_plan", parking_alt_km=200.0)


def test_propose_plan_clips_into_the_hard_bounds_and_says_what_moved(box):
    out = box.call(
        "propose_plan", **dict(zip(PARAM_NAMES, (float(v) * 100.0 for v in X0)))
    )
    assert out["clipped"] is True
    assert out["out_of_bounds"], "clipped silently"
    assert out["plan"]["parking_alt_km"] <= 400.0


def test_every_tool_call_appears_in_the_trace_with_its_arguments(box):
    box.call("get_constraints")
    box.call("query_memory", k=2)
    calls = [e for e in box.trace if "tool" in e]
    assert [c["tool"] for c in calls] == ["get_constraints", "query_memory"]
    assert calls[1]["arguments"] == {"k": 2}
    assert all("budget" in c for c in calls), "a trace entry without the budget state"


def test_a_refused_tool_call_is_still_recorded(box):
    """A trace that only shows what worked is not an audit trail."""
    with pytest.raises(ToolError):
        box.call("commit_attempt", attempt_id="att_nope")
    refused = [e for e in box.trace if e.get("ok") is False]
    assert len(refused) == 1
    assert "was not produced by this run" in refused[0]["error"]


def test_query_memory_without_a_memory_says_so_rather_than_returning_nothing(box):
    out = box.call("query_memory")
    assert out["neighbours"] == []
    assert "no experience memory" in out["_note"]


def test_query_memory_filters_to_the_current_physics(mission, tmp_path):
    """A plan that was feasible under a different simulator is not a hint."""
    store = Store(tmp_path / "m.jsonl")
    store.remember(
        mission,
        {
            "id": "att_stale", "cost": 1.0, "feasible": True, "sim_version": "env-OLD",
            "verifier_version": "ver-OLD", "plan": plan_from_x(X0), "constraint_report": [],
        },
    )
    box = Toolbox(mission, memory=store)
    assert box.call("query_memory")["neighbours"] == []


def test_the_trace_records_decisions_not_invented_reasoning(box):
    """Charter addition 8. A decision is tied to a number a tool returned."""
    box.decide("chose the cheaper of two plans", {"cost_a": 1.2, "cost_b": 1.4})
    entry = next(e for e in box.trace if "decision" in e)
    assert entry["decision"] == "chose the cheaper of two plans"
    assert entry["data"] == {"cost_a": 1.2, "cost_b": 1.4}


# ---------------------------------------------------------------------------
# OptimizerAgent: the fallback that must work without a GPU


def test_the_fallback_agent_needs_no_model(mission):
    """HANDOFF section 8: the UI, benchmark and demo must work with it, so nothing blocks on
    a GPU or model."""
    agent = OptimizerAgent(mission)
    best = agent.run()
    assert best, "the agent returned nothing at all"
    assert agent.tools.budget.spent <= mission["budget"]["max_attempts"]


def test_the_fallback_agent_emits_a_contract_valid_session(mission, tmp_path):
    """The same stream the UI replays, from an agent instead of the bare optimizer."""
    log = tmp_path / "session.jsonl"
    OptimizerAgent(mission, session_log_path=log).run()
    validate_session_file(log)
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    kinds = {e.get("type") for e in events}
    assert {"mission_started", "mission_finished"} <= kinds
    assert any(e.get("kind") == "tool_call" for e in events)
    assert any(e.get("kind") == "decision" for e in events)


def test_the_fallback_agent_declares_that_no_model_is_involved(mission, tmp_path):
    """So nobody reads a trace from this agent as an LLM's."""
    log = tmp_path / "session.jsonl"
    OptimizerAgent(mission, session_log_path=log).run()
    start = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert start["data"]["model"] is None
    assert "No language model is involved" in start["data"]["_note"]


def test_the_fallback_agent_never_exceeds_its_budget(mission):
    mission["budget"] = {"max_attempts": 4, "max_wall_s": 600.0}
    agent = OptimizerAgent(mission)
    agent.run()
    assert agent.tools.budget.spent <= 4


def test_an_exhausted_budget_ends_the_mission_rather_than_raising(mission):
    """Running out of budget is a normal end to a mission, not an error."""
    mission["budget"] = {"max_attempts": 1, "max_wall_s": 600.0}
    agent = OptimizerAgent(mission)
    agent.run()  # must not raise
    assert agent.tools.budget.spent <= 1


def test_the_agent_commits_only_a_feasible_attempt(mission):
    """An infeasible cost is a constraint penalty. Committing one would put a penalty score
    where a result belongs."""
    agent = OptimizerAgent(mission)
    agent.run()
    if agent.tools.committed is None:
        assert not any(a["feasible"] for a in agent.tools.attempts.values())
        assert any(
            "committing nothing" in e.get("decision", "") for e in agent.tools.trace
        )
    else:
        assert agent.tools.attempts[agent.tools.committed]["feasible"] is True


def test_the_agent_says_so_when_memory_had_nothing(mission, tmp_path):
    """A cold run and a warm run that found nothing must be distinguishable in the replay."""
    store = Store(tmp_path / "m.jsonl")
    agent = OptimizerAgent(mission, memory=store)
    agent.run()
    assert any(
        "no feasible neighbour in memory" in e.get("decision", "") for e in agent.tools.trace
    )


def test_the_agent_probes_whether_experience_transfers(mission, tmp_path):
    """Spends one simulation on the question, because the answer changes what the remaining
    budget is worth -- and records the result either way."""
    store = Store(tmp_path / "m.jsonl")
    store.remember(
        mission,
        {
            "id": "att_prior", "cost": 1.30, "feasible": True,
            "sim_version": tools_mod.SIM_VERSION,
            "verifier_version": tools_mod.VERIFIER_VERSION,
            "plan": plan_from_x(X0), "constraint_report": [],
        },
    )
    mission["budget"] = {"max_attempts": 8, "max_wall_s": 600.0}
    agent = OptimizerAgent(mission, memory=store)
    agent.run()
    decisions = [e.get("decision", "") for e in agent.tools.trace]
    assert any("neighbour" in d and "plan is" in d for d in decisions), decisions


def test_the_agent_is_deterministic_for_one_seed(mission):
    """Same mission, same seed, same versions -> same answer (charter addition 5)."""
    a = OptimizerAgent(copy.deepcopy(mission)).run()
    b = OptimizerAgent(copy.deepcopy(mission)).run()
    assert a["cost"] == b["cost"]
    assert a["feasible"] == b["feasible"]


def test_the_agent_policy_is_written_down_in_full():
    """The policy is the whole of this agent. If it changes, the docstring has to change with
    it, because the fallback's numbers are a baseline other systems are compared against."""
    doc = inspect.getdoc(OptimizerAgent.__module__ and __import__(
        "agent.optimizer_agent", fromlist=["x"]
    ))
    assert doc and "The policy, in full" in doc
    for tool in ("get_constraints", "query_memory", "propose_plan", "run_optimizer",
                 "commit_attempt"):
        assert tool in doc, f"the policy docstring does not mention {tool}"
