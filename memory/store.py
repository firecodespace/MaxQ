"""Experience memory: store past attempts, retrieve similar ones (WP2-10, HANDOFF section 9).

"Offline-capable experience memory" is the accurate name, and the one the handoff asks for.
It is not a flight computer, not a knowledge base, and not RAG -- it becomes RAG only if an
LLM consumes the retrieved cases as context (WP2-11). It is case-based reasoning: find
missions like this one, and start the search where they ended up.

Why this is exact search in numpy and not Qdrant
------------------------------------------------
HANDOFF section 9 suggests Qdrant in local on-disk mode. This implements the same interface
and the same filters with brute-force exact cosine search over a JSONL file, because:

* the corpus is a few thousand 14-dimensional vectors at most -- one benchmark split is 8
  missions x 120 attempts x 3 seeds, so exact search is sub-millisecond and an index would be
  slower, not faster;
* approximate search can silently return a worse neighbour, and the whole measurement we care
  about is whether a better neighbour makes the search faster. An approximation inside the
  thing being measured is a confound;
* the handoff itself notes that exact search is available and that quantization is lossy.

`Store` is the seam: a Qdrant-backed implementation drops in behind the same four methods.
That swap is a real decision with a new pinned dependency, so it is Yash's -- recorded in
`docs/decisions.md` rather than made quietly here. No untested Qdrant code is shipped
alongside, because untested code that claims to work is worse than code that is absent.

What is stored
--------------
One `memory_record` per attempt (contract 6.4): the `features-v1` vector plus a payload
carrying the mission, the plan, the cost, whether it was feasible, its failure modes, and the
simulator and verifier versions it was measured under. Failures are stored too, deliberately:
HANDOFF section 9 wants memory to steer away from known-bad regions, which needs the bad ones.

Owner: Yash (lane: memory/).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from contracts.validate import validate
from memory import features

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data" / "memory" / "experience.jsonl"


class MemoryError_(RuntimeError):
    """Raised when the store is asked for something it must refuse."""


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """An on-disk experience memory with exact cosine retrieval.

    The file is JSONL, one `memory_record` per line, so it can be read by anything, diffed,
    and inspected by a human. Nothing about the format is tied to this implementation.
    """

    def __init__(self, path: Path | str | None = None, *, autoload: bool = True) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self._records: list[dict[str, Any]] = []
        self._matrix: np.ndarray | None = None
        if autoload:
            self.load()

    # -- persistence ------------------------------------------------------

    def load(self) -> int:
        """Read the file, or start empty. Returns the number of records loaded."""
        self._records = []
        self._matrix = None
        if self.path.exists():
            for line_no, line in enumerate(
                self.path.read_text(encoding="utf-8").splitlines(), 1
            ):
                line = line.strip()
                if not line:
                    continue
                try:
                    self._records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise MemoryError_(
                        f"{self.path}:{line_no}: not valid JSON ({exc}). The store refuses to "
                        f"skip a corrupt line, because a silently shorter memory would look "
                        f"like a memory that simply had not learned much."
                    ) from exc
        self._rebuild()
        return len(self._records)

    def _rebuild(self) -> None:
        if not self._records:
            self._matrix = None
            return
        self._matrix = np.asarray(
            [r["vector"] for r in self._records], dtype=np.float64
        )

    def flush(self) -> None:
        """Write the whole store atomically, so an interrupted run cannot truncate it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".mem-", suffix=".jsonl")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                for rec in self._records:
                    fh.write(json.dumps(rec, sort_keys=True) + "\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def __len__(self) -> int:
        return len(self._records)

    # -- writing ----------------------------------------------------------

    def remember(
        self,
        mission_request: dict[str, Any],
        attempt: dict[str, Any],
        *,
        failure_modes: list[str] | None = None,
        flush: bool = True,
    ) -> dict[str, Any]:
        """Store one attempt as a memory record. Validated against contract 6.4 on the way in.

        Infeasible attempts ARE stored. A record of what did not work is what lets the search
        avoid that region next time, and a memory of successes only would make the store a
        highlight reel.
        """
        record = {
            "schema_version": 1,
            "id": f"mem_{attempt['id']}",
            "vector_spec": features.VECTOR_SPEC,
            "vector": [float(x) for x in features.encode(mission_request)],
            "payload": {
                "mission_id": str(mission_request["id"]),
                "attempt_id": str(attempt["id"]),
                "profile": mission_request["profile"],
                "rocket_id": mission_request["rocket_id"],
                "feasible": bool(attempt["feasible"]),
                "cost": float(attempt["cost"]),
                "plan": dict(attempt.get("plan") or {}),
                "failure_modes": list(
                    failure_modes
                    if failure_modes is not None
                    else failure_modes_of(attempt)
                ),
                "sim_version": str(attempt["sim_version"]),
                "verifier_version": str(attempt["verifier_version"]),
                "created_utc": _utc_now(),
            },
        }
        validate("memory_record", record)
        self._records.append(record)
        self._rebuild()
        if flush:
            self.flush()
        return record

    # -- reading ----------------------------------------------------------

    def search(
        self,
        mission_request: dict[str, Any],
        *,
        k: int = 5,
        sim_version: str | None = None,
        verifier_version: str | None = None,
        profile: str | None = None,
        rocket_id: str | None = None,
        feasible: bool | None = None,
        vector_spec: str | None = None,
    ) -> list[dict[str, Any]]:
        """The k nearest stored missions, by cosine similarity, after filtering.

        `sim_version` is the filter that matters most and the reason
        `test_memory_filters_stale` exists: a plan that was feasible under a different
        simulator is not a hint, it is a hint about different physics. Passing it drops every
        record measured under anything else.

        Records whose `vector_spec` differs from the one asked for are ALWAYS dropped, filter
        or no filter -- two vectors built under different specs are not comparable at all, so
        a distance between them is a meaningless number rather than a large one.
        """
        if not self._records or self._matrix is None:
            return []
        want_spec = vector_spec or features.VECTOR_SPEC
        query = features.encode(mission_request)

        keep: list[int] = []
        for i, rec in enumerate(self._records):
            if rec["vector_spec"] != want_spec:
                continue
            p = rec["payload"]
            if sim_version is not None and p["sim_version"] != sim_version:
                continue
            if verifier_version is not None and p["verifier_version"] != verifier_version:
                continue
            if profile is not None and p["profile"] != profile:
                continue
            if rocket_id is not None and p["rocket_id"] != rocket_id:
                continue
            if feasible is not None and bool(p["feasible"]) is not feasible:
                continue
            keep.append(i)
        if not keep:
            return []

        mat = self._matrix[keep]
        qn = float(np.linalg.norm(query))
        norms = np.linalg.norm(mat, axis=1)
        denom = norms * qn
        sims = np.where(denom > 0.0, mat @ query / np.where(denom > 0.0, denom, 1.0), 0.0)
        order = np.argsort(-sims)[: max(0, k)]
        return [
            {
                "similarity": round(float(sims[j]), 6),
                "distance": round(float(np.linalg.norm(mat[j] - query)), 6),
                "record": self._records[keep[j]],
            }
            for j in order
        ]

    def stats(self) -> dict[str, Any]:
        """A description of what is in here, for the UI and for reports."""
        specs: dict[str, int] = {}
        sims: dict[str, int] = {}
        feasible = 0
        for rec in self._records:
            specs[rec["vector_spec"]] = specs.get(rec["vector_spec"], 0) + 1
            sv = rec["payload"]["sim_version"]
            sims[sv] = sims.get(sv, 0) + 1
            feasible += bool(rec["payload"]["feasible"])
        return {
            "records": len(self._records),
            "feasible": feasible,
            "infeasible": len(self._records) - feasible,
            "vector_specs": specs,
            "sim_versions": sims,
            "path": str(self.path),
        }


def failure_modes_of(attempt: dict[str, Any]) -> list[str]:
    """The constraints this attempt actually violated, by name.

    Only constraints that were EVALUATED and failed. One that could not be checked is not a
    failure mode -- recording it as one would teach memory to avoid regions for a reason that
    was never measured.
    """
    out = []
    for cr in attempt.get("constraint_report") or []:
        evaluated = cr.get("evaluated")
        if evaluated is None:  # pre-`evaluated` attempt
            evaluated = not (
                cr.get("value") is None
                and "not evaluated" in (cr.get("note") or "").lower()
            )
        if evaluated and not cr["pass"]:
            out.append(f"{cr['id']}:{cr['name']}")
    return out
