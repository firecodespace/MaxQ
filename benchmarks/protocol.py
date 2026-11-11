"""The benchmark protocol (WP2-12): the rules of the measurement, and their hash.

A benchmark is only worth anything if the rules were fixed before the results. This module is
those rules, and `protocol_hash()` is a sha256 over all of them: the suite's missions, the
split assignment, the budgets, the convergence threshold and the seeds. Any change to any of
them produces a different hash, so a results file recorded under one protocol can never be
compared with one recorded under another without the mismatch being visible.

Changing the protocol is RED (`AGENTS.md` section 3): it invalidates every recorded result.
`test_the_protocol_hash_is_pinned` holds the current value, so a change cannot happen by
accident -- the test fails and whoever changed it has to say so out loud and get Yash's OK.

The split
---------
Twenty-four missions, eight per split, assigned by launch window. The twelve windows used are
partitioned so **no window appears in two splits**, which is the part that makes "held out"
mean something: a system cannot have seen the test missions' phasing geometry during tuning.
Every split still contains all four (profile, vehicle, pad) combinations -- both profiles, all
three vehicles, both pads families -- and spans the phase circle, so no split is a measurement
of one easy case.

What the split does NOT protect against: a human reading test results and then changing the
search. That is a discipline, not a mechanism. The charter's rule stands -- never tune on the
test split, and use it once.

Owner: Yash (lane: benchmarks/). Read-only to any agent.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from benchmarks import suite

#: A mission counts as converged when a system is feasible and within this percentage of
#: best-known, across at least `MIN_CONVERGED_SEEDS` seeds (HANDOFF_PHASE2 section 5.3).
CONVERGENCE_EPS_PCT = 2.0
MIN_CONVERGED_SEEDS = 3

#: Seeds every system is run with on every mission. Three, because `MIN_CONVERGED_SEEDS` is
#: three and a convergence claim from a single lucky seed is not a claim.
SYSTEM_SEEDS = (11, 22, 33)

#: Best-known reference search: deliberately far more budget than any system is given, and
#: several more seeds. Best-known is "the best we have found", never "the optimum".
BEST_KNOWN_BUDGET_PER_SEED = 1500
BEST_KNOWN_SEEDS = (11, 22, 33, 42, 77, 101, 202)

#: The four systems in the ablation (HANDOFF_PHASE2 section 8). `llm_base` and `llm_ft` cannot
#: run until a model is chosen (RED, Yash's call), and the runner reports them as not-run
#: rather than inventing a row.
SYSTEMS = ("cma_cold", "cma_memory", "llm_base", "llm_ft")


def splits() -> dict[str, list[str]]:
    """The three split lists, built from the suite declaration rather than stored."""
    out: dict[str, list[str]] = {name: [] for name in suite.SPLIT_NAMES}
    for mid in suite.mission_ids():
        out[suite.split_of(mid)].append(mid)
    return out


def protocol_document() -> dict[str, Any]:
    """Everything the hash covers, in one canonical structure.

    Deliberately includes the full mission_request of every suite mission, not just its id.
    An id is a label; if a mission's payload, window or objective weights changed underneath
    it, every recorded cost would change meaning while the id stayed the same. Hashing the
    requests themselves makes that impossible to miss.
    """
    return {
        "suite_id": suite.SUITE_ID,
        "missions": [json.loads(json.dumps(m, sort_keys=True)) for m in suite.missions()],
        "splits": splits(),
        "budget": {
            "max_attempts": suite.BUDGET_MAX_ATTEMPTS,
            "max_wall_s": suite.BUDGET_MAX_WALL_S,
        },
        "mission_seed": suite.MISSION_SEED,
        "system_seeds": list(SYSTEM_SEEDS),
        "convergence": {
            "eps_pct": CONVERGENCE_EPS_PCT,
            "min_seeds": MIN_CONVERGED_SEEDS,
        },
        "best_known": {
            "budget_per_seed": BEST_KNOWN_BUDGET_PER_SEED,
            "seeds": list(BEST_KNOWN_SEEDS),
        },
        "systems": list(SYSTEMS),
    }


def protocol_hash() -> str:
    """sha256 of the canonical protocol document, as `sha256:<hex>`."""
    blob = json.dumps(protocol_document(), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def is_converged(gap_pct: float | None, feasible_seeds: int) -> bool:
    """Per HANDOFF section 5.3: within eps of best-known, feasible, across enough seeds."""
    if gap_pct is None:
        return False
    return gap_pct <= CONVERGENCE_EPS_PCT and feasible_seeds >= MIN_CONVERGED_SEEDS


def gap_pct(system_cost: float | None, best_known_cost: float | None) -> float | None:
    """Percentage by which a system's best feasible cost exceeds best-known.

    Returns None -- never 0.0 -- when either side is missing (charter addition 12). A gap of
    0% reads as "matches the best we know of", which is a strong claim and must not be what
    "we have no number" looks like.
    """
    if system_cost is None or best_known_cost is None or best_known_cost <= 0.0:
        return None
    return round(100.0 * (system_cost - best_known_cost) / best_known_cost, 4)
