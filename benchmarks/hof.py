"""The Hall of Fame (WP2-12): best-known results, written only by the benchmark runner.

`benchmarks/hof.json` records the best verified feasible cost found for each suite mission.
HANDOFF section 5.3 is explicit that it "is updated only by the benchmark runner after
verification, never by an agent", and charter addition 1 makes `benchmarks/` read-only to any
agent and to any code path an agent controls at runtime.

How that is enforced, and how far the enforcement goes
------------------------------------------------------
`read()` is public. Writing needs a `Writer`, and `Writer` cannot be constructed without a
protocol hash that matches the live protocol -- so a stale or forked protocol cannot write
into the same file, and a caller that has not gone through `open_for_runner()` has no way in.

Said plainly, because a guard that is oversold is worse than no guard: this stops accidents
and makes tampering obvious. It is NOT a security boundary. Any code running in this process
could import this module and call `open_for_runner()` itself. What makes a forged entry
detectable rather than merely unlikely is that every entry carries `protocol_hash`,
`sim_version`, `verifier_version` and the `attempt_id` it came from, and `verify_entries()`
re-checks those against the live versions. An entry that cannot be reproduced is an entry
that gets thrown away.

The real boundary is the process one: agents run under `agent/`, the runner runs under
`benchmarks/`, and `test_agent_cannot_write_benchmarks` asserts the agent package has no path
to this module.

Owner: Yash (lane: benchmarks/).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

HOF_PATH = Path(__file__).resolve().parent / "hof.json"

SCHEMA_VERSION = 1


class HofError(RuntimeError):
    """Raised when the Hall of Fame is asked to do something it must refuse."""


def _empty() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "_note": (
            "Best-known results per benchmark mission. Written only by "
            "benchmarks/runner.py, after the verifier has passed the attempt. Every entry "
            "carries the protocol, simulator and verifier versions it was measured under; an "
            "entry whose versions do not match the current code is stale and is not "
            "comparable with a fresh result."
        ),
        "entries": {},
    }


def read() -> dict[str, Any]:
    """The current Hall of Fame, or an empty one. Always safe to call."""
    if not HOF_PATH.exists():
        return _empty()
    data = json.loads(HOF_PATH.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise HofError(
            f"{HOF_PATH.name}: schema_version {data.get('schema_version')!r}, expected "
            f"{SCHEMA_VERSION}. Refusing to read it as though it were the current format."
        )
    return data


def best_cost(mission_id: str) -> float | None:
    """The best-known cost for a mission, or None.

    None, never 0.0 (charter addition 12). A gap computed against 0.0 would read as
    "infinitely worse than best known" or "already matching it", depending on which way the
    arithmetic fell, and both are claims we have no basis for.
    """
    entry = read()["entries"].get(mission_id)
    return None if entry is None else float(entry["cost"])


def verify_entries(sim_version: str, verifier_version: str) -> dict[str, list[str]]:
    """Partition the recorded missions into those still comparable and those gone stale.

    A cost measured under a different simulator or verifier is not a worse number, it is a
    number about a different thing. The runner refuses to compute a gap against one.
    """
    current, stale = [], []
    for mid, entry in read()["entries"].items():
        same = (
            entry.get("sim_version") == sim_version
            and entry.get("verifier_version") == verifier_version
        )
        (current if same else stale).append(mid)
    return {"current": sorted(current), "stale": sorted(stale)}


class Writer:
    """The capability to update the Hall of Fame. Only `open_for_runner()` makes one."""

    __slots__ = ("_protocol_hash",)

    def __init__(self, protocol_hash: str, _guard: object = None) -> None:
        if _guard is not _GUARD:
            raise HofError(
                "A Hall of Fame Writer cannot be constructed directly. The Hall of Fame is "
                "written only by benchmarks/runner.py, via open_for_runner(); see "
                "HANDOFF_PHASE2 section 5.3 and charter addition 1."
            )
        self._protocol_hash = protocol_hash

    def record(
        self,
        mission_id: str,
        *,
        cost: float,
        attempt_id: str,
        budget_sims: int,
        seeds: list[int],
        sim_version: str,
        verifier_version: str,
        feasible: bool,
        metrics: dict[str, Any] | None = None,
    ) -> bool:
        """Record a result if it beats what is there. Returns True if the file changed.

        An infeasible attempt is never recorded whatever its cost: an infeasible cost is a
        penalty score, and letting one into the Hall of Fame would make every gap computed
        against it meaningless.
        """
        if not feasible:
            raise HofError(
                f"{mission_id}: refusing to record an infeasible attempt as best-known "
                f"(cost {cost} is a constraint penalty, not a mission cost)"
            )

        data = read()
        existing = data["entries"].get(mission_id)
        comparable = existing is not None and (
            existing.get("sim_version") == sim_version
            and existing.get("verifier_version") == verifier_version
        )
        if comparable and float(existing["cost"]) <= float(cost):
            return False

        data["entries"][mission_id] = {
            "cost": round(float(cost), 6),
            "attempt_id": attempt_id,
            "budget_sims": int(budget_sims),
            "seeds": [int(s) for s in seeds],
            "sim_version": sim_version,
            "verifier_version": verifier_version,
            "protocol_hash": self._protocol_hash,
            "metrics": metrics or {},
            "superseded": (
                None
                if existing is None
                else {
                    "cost": existing["cost"],
                    "sim_version": existing.get("sim_version"),
                    "verifier_version": existing.get("verifier_version"),
                    "reason": "beaten" if comparable else "measured under different versions",
                }
            ),
        }
        _write_atomic(data)
        return True


_GUARD = object()


def open_for_runner(protocol_hash: str) -> Writer:
    """Hand the benchmark runner its write capability.

    Requires the live protocol hash. A caller holding a stale one -- a half-updated checkout,
    a forked suite -- is refused rather than allowed to mix results from two protocols into
    one file.
    """
    from benchmarks.protocol import protocol_hash as live_hash

    live = live_hash()
    if protocol_hash != live:
        raise HofError(
            f"protocol hash mismatch: caller has {protocol_hash}, the protocol is {live}. "
            f"Results from two different protocols must not share one Hall of Fame."
        )
    return Writer(protocol_hash, _GUARD)


def _write_atomic(data: dict[str, Any]) -> None:
    """Write via a temporary file and replace, so an interrupted run cannot truncate it."""
    HOF_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(HOF_PATH.parent), prefix=".hof-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, HOF_PATH)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
