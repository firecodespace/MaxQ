"""Subprocess entry point: run one mission and write its session and attempts (WP2-06).

    python -m server.run_mission --request <mission_request.json> --session <session.jsonl> \
                                 --out-dir <dir> [--max-attempts N]

A separate PROCESS, not a thread, and that is the whole point: HANDOFF_PHASE2 charter addition 9
says every agent run has a `max_wall_s` and that it is enforced in code. A Python thread cannot be
stopped from outside, so a thread-based runner could only ask the optimizer to stop and hope it
checked. A process can be terminated, so the budget is real.

Owner: Yash (lane: server/).
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--request", required=True, type=Path)
    ap.add_argument("--session", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path, help="where attempt.json files are written")
    ap.add_argument("--max-attempts", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args(argv)

    request = json.loads(args.request.read_text(encoding="utf-8"))
    args.session.parent.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "data" / "attempts").mkdir(parents=True, exist_ok=True)

    budget = request.get("budget", {})
    max_attempts = args.max_attempts or int(budget.get("max_attempts", 10))
    seed = args.seed if args.seed is not None else int(request.get("seed", 42))

    # optimize.fixed_sigma_es writes its attempt files under its own module-level ROOT. Point that at
    # this run's directory so a run cannot overwrite another run's attempts, and so tests do not
    # write into the repository. This is the one seam between the server and the search module.
    from optimize import fixed_sigma_es

    fixed_sigma_es.ROOT = args.out_dir

    from optimize.fixed_sigma_es import optimize_mission

    try:
        optimize_mission(
            mission_request=request,
            max_attempts=max_attempts,
            seed=seed,
            session_log_path=args.session,
            save_attempts=True,
        )
    except Exception:
        # The session file is the only channel back to the server, so a crash has to be written
        # there as a terminal event. Otherwise the UI waits for a run that will never report.
        detail = traceback.format_exc(limit=6)
        with open(args.session, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "t_wall": 0.0,
                        "type": "mission_finished",
                        "mission_id": request.get("id", "unknown"),
                        "summary": "run failed",
                        "data": {
                            "attempts": 0,
                            "feasible_attempts": 0,
                            "stop_reason": "error",
                            "error": detail.strip().splitlines()[-1][:400],
                        },
                    }
                )
                + "\n"
            )
        print(detail, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
