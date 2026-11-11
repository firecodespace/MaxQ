"""Build the Slack messages, from artefacts that are already contract-valid.

Four message kinds, matching what Yash asked to see: a finished mission run, a status or decisions
entry, a commit, and a contract or verifier alert.

These take `attempt`, `session` and `run` objects as the rest of MaxQ already produces them, so
there is no new contract and nothing to keep in sync. A Slack message is outbound presentation, not
data crossing a module boundary, which is what `contracts/` exists to police.

The charter applies here as much as in the UI, and more easily forgotten because a Slack message
gets screenshotted and forwarded out of context:
  - item 7: simulator output is labelled "SIM - reduced fidelity";
  - item 12: a metric with no source is "n/a", never 0;
  - item 11: the search is named as whatever the run itself declared, not as what we wish it were.

So `run_finished` refuses to state a cost without saying what it is a cost *of*. That is the whole
point: a number in a channel outlives the conversation that qualified it.

Owner: Yash (lane: notify/).
"""

from __future__ import annotations

from typing import Any

MAX_TEXT = 2900  # Slack truncates a section's text at 3000 characters.

SIM_LABEL = "SIM - reduced fidelity"


def _trunc(s: str, n: int = MAX_TEXT) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _trunc(text)}}


def _context(*lines: str) -> dict[str, Any]:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": _trunc(ln, 500)} for ln in lines if ln],
    }


def _header(text: str) -> dict[str, Any]:
    # plain_text only, and Slack hard-limits it to 150 characters.
    return {"type": "header", "text": {"type": "plain_text", "text": _trunc(text, 148)}}


def _fmt_gap(gap: float | None) -> str:
    """Charter item 12. A missing benchmark is n/a; 0% would read as 'already matches best known'."""
    return "n/a (no benchmark yet)" if gap is None else f"{gap:+.2f}%"


def run_finished(
    *,
    mission_id: str,
    run: dict[str, Any],
    attempts: int,
    feasible: int,
    best_cost: float | None,
    best_known_gap_pct: float | None = None,
    search_label: str | None = None,
    provenance_note: str | None = None,
    sim_version: str | None = None,
    verifier_version: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """A finished mission run, with every caveat the UI shows attached to it."""
    status = run.get("status", "finished")
    stop = run.get("stop_reason") or "unknown"
    icon = {"finished": ":white_check_mark:", "failed": ":x:", "aborted": ":black_square_for_stop:"}.get(
        status, ":grey_question:"
    )
    rate = f"{feasible}/{attempts}" if attempts else "0/0"
    cost = f"`{best_cost:.3f}`" if best_cost is not None else "none (no feasible attempt)"

    blocks = [
        _header(f"Run {status}: {mission_id}"),
        _section(
            f"{icon} *{rate}* attempts feasible · best cost {cost}\n"
            f"Stopped: `{stop}` · elapsed {run.get('elapsed_s', 0):.1f}s"
        ),
        _section(
            f"*Gap to best known:* {_fmt_gap(best_known_gap_pct)}\n"
            f"*Search:* {search_label or 'unknown'}"
        ),
    ]

    # The caveat that keeps a forwarded screenshot honest. Worth repeating every single time,
    # because this is the number people will quote.
    caveats = (
        f"{SIM_LABEL}. Cost is this simulator's, whose payload-to-LEO error against published "
        "figures is tens of percent. `time_to_dock_s` is the end of phasing: there is no approach "
        "or docking phase yet."
    )
    versions = " · ".join(v for v in (sim_version, verifier_version) if v)
    blocks.append(_context(caveats, provenance_note or "", versions))
    if url:
        blocks.append(_context(f"<{url}|open the run>"))

    return {
        "text": f"Run {status}: {mission_id} - {rate} feasible, best cost "
        f"{best_cost if best_cost is not None else 'none'} ({SIM_LABEL})",
        "blocks": blocks,
    }


def status_update(
    *, owner: str, title: str, body: str, file: str = "status", needs: str | None = None
) -> dict[str, Any]:
    """A new entry in `status/<owner>.md` or `docs/decisions.md`.

    This is the one that replaces a human relaying findings by hand, so it carries `needs` -- who
    is blocked on whom -- rather than leaving that implicit in prose nobody reads to the end.
    """
    blocks = [_header(f"{owner}: {title}"), _section(body)]
    if needs:
        blocks.append(_section(f":handshake: *Needs:* {needs}"))
    blocks.append(_context(f"from `{file}`"))
    return {"text": f"{owner}: {title}", "blocks": blocks}


def commit_pushed(
    *, branch: str, sha: str, subject: str, author: str, files: int | None = None, url: str | None = None
) -> dict[str, Any]:
    """One commit. Deliberately one line: a channel that reposts whole diffs stops being read."""
    tail = f" · {files} file{'s' if files != 1 else ''}" if files is not None else ""
    line = f"`{sha[:8]}` on `{branch}` — {subject}"
    if url:
        line = f"<{url}|`{sha[:8]}`> on `{branch}` — {subject}"
    return {
        "text": f"{sha[:8]} on {branch}: {subject}",
        "blocks": [_section(line), _context(f"{author}{tail}")],
    }


def alert(
    *, kind: str, summary: str, detail: str = "", where: str = "", mission_id: str | None = None
) -> dict[str, Any]:
    """A contract violation, a failed verifier run, or a verifier_version change.

    Low volume by intent. If this ever becomes chatty, the fix is upstream, not a filter here.
    """
    blocks = [_header(f":rotating_light: {kind}"), _section(f"*{summary}*")]
    if detail:
        blocks.append(_section(f"```{_trunc(detail, 2800)}```"))
    tail = " · ".join(x for x in (where, f"mission `{mission_id}`" if mission_id else "") if x)
    if tail:
        blocks.append(_context(tail))
    return {"text": f"{kind}: {summary}", "blocks": blocks}
