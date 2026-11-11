"""Post a MaxQ update to Slack from the command line or a git hook.

    python scripts/notify_slack.py run     <session-dir>            # a finished mission run
    python scripts/notify_slack.py status  --owner shiva --title T --body B [--needs "..."]
    python scripts/notify_slack.py commit  [--sha HEAD]             # the current commit
    python scripts/notify_slack.py alert   --kind K --summary S [--detail D]

Add `--dry-run` to any of them to print the exact payload and send nothing. That works with no
webhook configured at all, which is the right way to try this the first time:

    python scripts/notify_slack.py commit --dry-run

Exit status is 0 whenever the command itself was well-formed, even if delivery failed, so a git
hook or a CI step never blocks on Slack being unreachable. `--strict` reverses that when you
actually want to know.

Owner: Yash (lane: scripts/, notify/).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notify import NotifyError, SlackNotifier, messages  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _read_session(session_dir: Path) -> tuple[list[dict], dict[str, Any]]:
    """Read a run's session.jsonl. The live stream and the replay are the same bytes."""
    path = session_dir / "session.jsonl"
    if not path.exists():
        raise NotifyError(f"no session.jsonl in {session_dir}")
    events = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    started = next((e for e in events if e["type"] == "mission_started"), {})
    return events, started.get("data", {})


def cmd_run(args: argparse.Namespace, n: SlackNotifier) -> int:
    session_dir = Path(args.session_dir).resolve()
    events, started = _read_session(session_dir)

    finished = next((e for e in events if e["type"] == "mission_finished"), None)
    fin = finished["data"] if finished else {}
    attempt_events = [e["data"] for e in events if e["type"] == "attempt_finished"]
    feasible = [a for a in attempt_events if a.get("feasible")]
    best = min((a["cost"] for a in feasible), default=None)

    # The gap comes from the run's own benchmark_update, so the message cannot claim a benchmark
    # the run did not have.
    gaps = [e["data"].get("best_known_gap_pct") for e in events if e["type"] == "benchmark_update"]
    gap = gaps[-1] if gaps else None

    # Runs recorded before the 2026-11-12 labels fix wrote best_known_gap_pct: 0.0 with no
    # benchmark in existence, and declared no search. Those files are evidence and are never
    # rewritten -- but republishing their 0.0 into a channel would restate the exact claim the fix
    # removed, so the message reports it as unavailable and says why.
    pre_fix = "search" not in started
    if pre_fix:
        provenance = (
            "Recorded before the 2026-11-12 labels fix: the artefact stores "
            f"best_known_gap_pct {gap!r} and names no search. Shown as n/a because no benchmark "
            "exists (WP2-12)."
        )
        gap = None
    else:
        provenance = None

    mission_id = next((e.get("mission_id") for e in events if e.get("mission_id")), session_dir.name)
    msg = messages.run_finished(
        mission_id=str(mission_id),
        run={
            "status": "failed" if fin.get("stop_reason") == "error" else "finished",
            "stop_reason": fin.get("stop_reason"),
            "elapsed_s": events[-1].get("t_wall", 0.0) if events else 0.0,
        },
        attempts=len(attempt_events),
        feasible=len(feasible),
        best_cost=best,
        best_known_gap_pct=gap,
        search_label=started.get("search_label") or started.get("search"),
        provenance_note=provenance,
        sim_version=args.sim_version,
        verifier_version=args.verifier_version,
        url=args.url,
    )
    return 0 if n.post_or_log(msg).ok or not args.strict else 1


def cmd_status(args: argparse.Namespace, n: SlackNotifier) -> int:
    msg = messages.status_update(
        owner=args.owner, title=args.title, body=args.body, file=args.file, needs=args.needs
    )
    return 0 if n.post_or_log(msg).ok or not args.strict else 1


def _git(*a: str) -> str:
    """Read-only git, scoped to this repository.

    `-c safe.directory=ROOT` is passed per-invocation rather than written to the user's global
    config: on Windows a worktree often sits on a filesystem that records no ownership, and git
    then refuses every command. Scoping it to the repository we are already running inside, for
    one subprocess, is the narrow version of git's own suggested fix.
    """
    return subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", *a],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def cmd_commit(args: argparse.Namespace, n: SlackNotifier) -> int:
    sha = _git("rev-parse", args.sha)
    if not sha:
        raise NotifyError(f"git could not resolve {args.sha!r} (is this a repository?)")
    subject = _git("log", "-1", "--format=%s", sha)
    author = _git("log", "-1", "--format=%an", sha)
    branch = args.branch or _git("rev-parse", "--abbrev-ref", "HEAD")
    files = _git("show", "--name-only", "--format=", sha)
    msg = messages.commit_pushed(
        branch=branch,
        sha=sha,
        subject=subject,
        author=author,
        files=len([f for f in files.splitlines() if f.strip()]),
        url=f"{args.repo_url.rstrip('/')}/commit/{sha}" if args.repo_url else None,
    )
    return 0 if n.post_or_log(msg).ok or not args.strict else 1


def cmd_alert(args: argparse.Namespace, n: SlackNotifier) -> int:
    msg = messages.alert(
        kind=args.kind,
        summary=args.summary,
        detail=args.detail,
        where=args.where,
        mission_id=args.mission_id,
    )
    return 0 if n.post_or_log(msg).ok or not args.strict else 1


def build_parser() -> argparse.ArgumentParser:
    # The shared flags live on a parent parser so they are accepted on either side of the
    # subcommand. argparse otherwise rejects `notify_slack.py commit --dry-run`, which is the form
    # anyone will actually type.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true", help="print the payload, send nothing")
    common.add_argument(
        "--strict", action="store_true", help="exit non-zero when delivery fails (default: do not)"
    )

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser(
        "run", parents=[common], help="a finished mission run, read from its session.jsonl"
    )
    p.add_argument("session_dir")
    p.add_argument("--sim-version")
    p.add_argument("--verifier-version")
    p.add_argument("--url")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("status", parents=[common], help="a status/<owner>.md or docs/decisions.md entry")
    p.add_argument("--owner", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--needs", help="who is blocked on whom")
    p.add_argument("--file", default="status")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("commit", parents=[common], help="one commit, one line")
    p.add_argument("--sha", default="HEAD")
    p.add_argument("--branch")
    p.add_argument("--repo-url", default="https://github.com/firecodespace/MaxQ")
    p.set_defaults(fn=cmd_commit)

    p = sub.add_parser("alert", parents=[common], help="a contract violation or verifier problem")
    p.add_argument("--kind", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--detail", default="")
    p.add_argument("--where", default="")
    p.add_argument("--mission-id")
    p.set_defaults(fn=cmd_alert)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        n = SlackNotifier.from_env(dry_run=args.dry_run) if args.dry_run else SlackNotifier.from_env()
        return int(args.fn(args, n))
    except NotifyError as e:
        # Configuration problems are the user's to fix, so they are loud -- but redact() has
        # already run inside the module, so nothing here can print a live webhook.
        print(f"notify: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
