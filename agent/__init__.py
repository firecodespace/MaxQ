"""Agent orchestrator and tool layer (WP2-11).

Two halves. `Toolbox` is the only interface an agent has to the mission, and `OptimizerAgent`
is the deterministic fallback that drives it with a fixed policy and no model -- required by
HANDOFF section 8 so that the UI, the benchmark and the demo never depend on a GPU.

The LLM planner is not here: choosing an open-weight model and checking its licence is Yash's
decision (RED). When it lands it drives the same `Toolbox`, and `agent/tools.py` already
declares the tool schemas in the shape a tool-use API wants, so the prompt side cannot drift
from the implementation.

Nothing in this package can write to `verifier/` or `benchmarks/` -- see the module docstring
in `agent/tools.py` for what that guarantee is and what it is not.
"""

from agent.budget import Budget, BudgetExhausted
from agent.optimizer_agent import AGENT_ID, AGENT_VERSION, OptimizerAgent, run_mission
from agent.tools import TOOL_NAMES, TOOL_SCHEMAS, Toolbox, ToolError

__all__ = [
    "AGENT_ID",
    "AGENT_VERSION",
    "TOOL_NAMES",
    "TOOL_SCHEMAS",
    "Budget",
    "BudgetExhausted",
    "OptimizerAgent",
    "ToolError",
    "Toolbox",
    "run_mission",
]
