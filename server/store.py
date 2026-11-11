"""Missions, runs and their budgets (WP2-06).

A mission is a validated `mission_request`. A run is one execution of it: a subprocess writing a
session file and attempt files into its own directory. The store owns both, and owns the budget:
`max_attempts` goes to the optimizer, `max_wall_s` is enforced here by terminating the process,
because charter addition 9 asks for budgets enforced in code rather than hoped for.

Everything a run produces lives under `<root>/data/sessions/<run_id>/`, so runs never overwrite one
another and a test can point `root` at a temporary directory.

Owner: Yash (lane: server/).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts.validate import ContractError, validate

REPO_ROOT = Path(__file__).resolve().parents[1]
TERMINATE_GRACE_S = 5.0


@dataclass
class Run:
    """One execution of a mission."""

    id: str
    mission_id: str
    session_path: Path
    out_dir: Path
    started_at: float
    max_wall_s: float
    max_attempts: int
    process: subprocess.Popen | None = None
    status: str = "running"  # running | finished | aborted | failed
    stop_reason: str | None = None
    finished_at: float | None = None

    @property
    def elapsed_s(self) -> float:
        return (self.finished_at or time.time()) - self.started_at

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "mission_id": self.mission_id,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "elapsed_s": round(self.elapsed_s, 3),
            "max_wall_s": self.max_wall_s,
            "max_attempts": self.max_attempts,
            "session_id": self.id,
        }


@dataclass
class Mission:
    id: str
    request: dict[str, Any]
    created_at: float
    runs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request": self.request,
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.created_at)),
            "runs": list(self.runs),
        }


class MissionStore:
    """In-memory missions with on-disk sessions and attempts.

    Deliberately not a database: a mission is cheap to re-create from its request, and the artefacts
    that matter -- the session file and the attempt files -- are already contract-shaped files on
    disk, which is what the replay path and the benchmark runner read.
    """

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or REPO_ROOT)
        self.sessions_dir = self.root / "data" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.missions: dict[str, Mission] = {}
        self.runs: dict[str, Run] = {}

    # ---------------------------------------------------------------- missions

    def create_mission(self, request: dict[str, Any]) -> Mission:
        validate("mission_request", request)  # raises ContractError, named field by field
        mid = request["id"]
        if mid in self.missions:
            raise ValueError(f"mission {mid!r} already exists")
        m = Mission(id=mid, request=request, created_at=time.time())
        self.missions[mid] = m
        return m

    def get_mission(self, mission_id: str) -> Mission | None:
        return self.missions.get(mission_id)

    def list_missions(self) -> list[Mission]:
        return sorted(self.missions.values(), key=lambda m: m.created_at)

    # ---------------------------------------------------------------- runs

    def launch(self, mission_id: str, *, max_attempts: int | None = None) -> Run:
        m = self.missions[mission_id]
        if any(self.runs[r].is_running() for r in m.runs):
            raise ValueError(f"mission {mission_id!r} already has a run in progress")

        budget = m.request.get("budget", {})
        run_id = f"{mission_id}__run{len(m.runs) + 1:02d}"
        out_dir = self.sessions_dir / run_id
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)

        request_path = out_dir / "mission_request.json"
        request_path.write_text(json.dumps(m.request, indent=2) + "\n", encoding="utf-8")
        session_path = out_dir / "session.jsonl"
        session_path.touch()

        attempts = int(max_attempts or budget.get("max_attempts", 10))
        run = Run(
            id=run_id,
            mission_id=mission_id,
            session_path=session_path,
            out_dir=out_dir,
            started_at=time.time(),
            max_wall_s=float(budget.get("max_wall_s", 600.0)),
            max_attempts=attempts,
        )
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        run.process = subprocess.Popen(
            [
                sys.executable, "-m", "server.run_mission",
                "--request", str(request_path),
                "--session", str(session_path),
                "--out-dir", str(out_dir),
                "--max-attempts", str(attempts),
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.runs[run_id] = run
        m.runs.append(run_id)
        return run

    def poll_run(self, run: Run) -> Run:
        """Refresh a run's status and enforce its wall-clock budget."""
        if run.status != "running":
            return run
        if run.is_running():
            if run.elapsed_s > run.max_wall_s:
                self.abort(run, reason="wall_clock_budget_exhausted")
            return run
        code = run.process.poll() if run.process else 0
        run.finished_at = time.time()
        run.status = "finished" if code == 0 else "failed"
        run.stop_reason = run.stop_reason or ("completed" if code == 0 else f"exit code {code}")
        return run

    def abort(self, run: Run, *, reason: str = "aborted") -> Run:
        if run.process is not None and run.is_running():
            run.process.terminate()
            try:
                run.process.wait(timeout=TERMINATE_GRACE_S)
            except subprocess.TimeoutExpired:
                run.process.kill()
                run.process.wait(timeout=TERMINATE_GRACE_S)
        run.finished_at = time.time()
        run.status = "aborted"
        run.stop_reason = reason
        return run

    def get_run(self, run_id: str) -> Run | None:
        run = self.runs.get(run_id)
        return self.poll_run(run) if run else None

    def latest_run(self, mission_id: str) -> Run | None:
        m = self.missions.get(mission_id)
        if not m or not m.runs:
            return None
        return self.get_run(m.runs[-1])

    def shutdown(self) -> None:
        for run in self.runs.values():
            if run.is_running():
                self.abort(run, reason="server_shutdown")

    # ---------------------------------------------------------------- attempts

    def attempts_dir(self, run: Run) -> Path:
        return run.out_dir / "data" / "attempts"

    def list_attempts(self, run: Run) -> list[dict[str, Any]]:
        """Attempt summaries for the attempts view, cheapest fields only."""
        out = []
        for p in sorted(self.attempts_dir(run).glob("*.json")):
            if p.name.endswith(".traj.json"):
                continue
            try:
                a = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                continue  # being written right now
            out.append({
                "id": a["id"],
                "mission_id": a["mission_id"],
                "feasible": a["feasible"],
                "cost": a["cost"],
                "status": a["status"],
                "wall_s": a["wall_s"],
                "metrics": a["metrics"],
                "failed_constraints": [c["id"] for c in a["constraint_report"] if not c["pass"]],
            })
        return sorted(out, key=lambda a: a["id"])

    def read_attempt(self, run: Run, attempt_id: str) -> dict[str, Any] | None:
        p = self.attempts_dir(run) / f"{attempt_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def read_trajectory(self, run: Run, attempt_id: str) -> dict[str, Any] | None:
        p = self.attempts_dir(run) / f"{attempt_id}.traj.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    # ---------------------------------------------------------------- recorded sessions

    def list_sessions(self) -> list[dict[str, Any]]:
        """Every recorded session on disk, including ones from earlier server processes."""
        out = []
        for d in sorted(self.sessions_dir.iterdir()):
            sp = d / "session.jsonl"
            if not d.is_dir() or not sp.exists():
                continue
            run = self.runs.get(d.name)
            out.append({
                "id": d.name,
                "mission_id": (run.mission_id if run else d.name.split("__run")[0]),
                "bytes": sp.stat().st_size,
                "modified_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sp.stat().st_mtime)),
                "status": (self.poll_run(run).status if run else "recorded"),
            })
        return out

    def session_path(self, session_id: str) -> Path | None:
        p = self.sessions_dir / session_id / "session.jsonl"
        return p if p.exists() else None


__all__ = ["ContractError", "Mission", "MissionStore", "Run"]
