"""Budget enforcement (WP2-11, charter addition 9: budgets are enforced in code).

Two independent limits, both from the mission_request: how many simulations an agent may run
and how long it may take. Either one exhausting stops the agent.

The point of putting this in its own module with its own tests is that a budget enforced by
convention is not enforced. An agent that can decide to run one more simulation has no budget;
`Budget.spend()` raises, and the only way past it is to not call the tool.
"""

from __future__ import annotations

import time
from typing import Any


class BudgetExhausted(RuntimeError):
    """Raised when an agent asks for a simulation it cannot afford.

    Not an error in the agent's logic -- running out of budget is a normal, expected end to a
    mission, and the orchestrator catches this and finishes with whatever it found.
    """

    def __init__(self, reason: str, spent: int, limit: int, elapsed_s: float) -> None:
        super().__init__(reason)
        self.reason = reason
        self.spent = spent
        self.limit = limit
        self.elapsed_s = elapsed_s


class Budget:
    """A simulation and wall-clock budget, counted down in code.

    `max_sims` counts SIMULATIONS, not tool calls. Reading the constraints or querying memory
    is free; only `run_sim` and `run_optimizer` cost anything, because the simulator is the
    expensive and the comparable thing. A benchmark that counted tool calls would reward an
    agent for batching its thinking rather than for needing fewer simulations.
    """

    def __init__(
        self,
        max_sims: int,
        max_wall_s: float,
        *,
        clock: Any = time.monotonic,
    ) -> None:
        if max_sims < 1:
            raise ValueError(f"max_sims must be at least 1, got {max_sims}")
        if max_wall_s <= 0.0:
            raise ValueError(f"max_wall_s must be positive, got {max_wall_s}")
        self.max_sims = int(max_sims)
        self.max_wall_s = float(max_wall_s)
        self._clock = clock
        self._t0 = clock()
        self.spent = 0

    @classmethod
    def from_request(cls, mission_request: dict[str, Any], **kw: Any) -> Budget:
        b = mission_request["budget"]
        return cls(int(b["max_attempts"]), float(b["max_wall_s"]), **kw)

    # -- state ------------------------------------------------------------

    @property
    def elapsed_s(self) -> float:
        return float(self._clock() - self._t0)

    @property
    def sims_left(self) -> int:
        return max(0, self.max_sims - self.spent)

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.max_wall_s - self.elapsed_s)

    def exhausted(self) -> str | None:
        """The reason the budget is gone, or None. Checking never consumes anything."""
        if self.sims_left <= 0:
            return "budget_exhausted"
        if self.seconds_left <= 0.0:
            return "wall_clock_exhausted"
        return None

    # -- spending ---------------------------------------------------------

    def spend(self, n: int = 1) -> None:
        """Charge `n` simulations, or raise.

        Checked BEFORE the simulations run, so an agent cannot overspend by asking for a
        batch larger than what is left. `affordable()` is there for callers that would rather
        shrink their request than be refused.
        """
        if n < 1:
            raise ValueError(f"cannot spend {n} simulations")
        reason = self.exhausted()
        if reason is None and n > self.sims_left:
            reason = "budget_exhausted"
        if reason is not None:
            raise BudgetExhausted(
                f"{reason}: asked for {n} simulation(s) with {self.sims_left} of "
                f"{self.max_sims} left and {self.seconds_left:.1f} s of "
                f"{self.max_wall_s:.1f} s remaining",
                spent=self.spent,
                limit=self.max_sims,
                elapsed_s=self.elapsed_s,
            )
        self.spent += n

    def affordable(self, wanted: int) -> int:
        """How many of `wanted` simulations could be run now. Zero when exhausted."""
        if self.exhausted() is not None:
            return 0
        return min(int(wanted), self.sims_left)

    def snapshot(self) -> dict[str, Any]:
        return {
            "sims_spent": self.spent,
            "sims_limit": self.max_sims,
            "sims_left": self.sims_left,
            "elapsed_s": round(self.elapsed_s, 3),
            "wall_limit_s": self.max_wall_s,
            "exhausted": self.exhausted(),
        }
