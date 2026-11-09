"""Download an object's REAL element-set history from Space-Track ONCE and store it locally (WP-15 input).

    # PowerShell (credentials live only in your shell session; they are never printed, logged or written):
    $env:SPACETRACK_USER = "you@example.com"; $env:SPACETRACK_PASS = "..."
    python scripts/fetch_spacetrack_history.py --norad 25544 --days 21

Rules this script follows (Space-Track documentation, checked 2026-11-10):
  * Free account required; credentials come ONLY from the environment variables SPACETRACK_USER / SPACETRACK_PASS.
  * Limit API queries to < 30 per minute and < 300 per hour. This script makes one login and ONE data query.
  * GP_HISTORY is "1 / lifetime": download an object's history once and store it; do not download it again.
    So the script REFUSES to run when the output file already exists (unless --force).
  * Do not use gp_history for current elements (use CelesTrak/GP): the query is bounded by --days.
  * Redistribution of basic SSA data (TLEs/OMMs) is approved "conditioned on appropriate citation": the output file
    carries the citation; keep it when sharing.

Owner: Yash (lane: scripts/). Agents never receive the credentials; the account holder runs this command.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from catalog.omm import OMM_REQUIRED  # noqa: E402

LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
QUERY_BASE = "https://www.space-track.org/basicspacedata/query/class/gp_history"
USER_AGENT = "MaxQ-research/0.1 (educational launch-planner project; single bounded gp_history request)"
CITATION = (
    "Source: Space-Track.org (USSPACECOM / 18th Space Defense Squadron), class gp_history, retrieved {when}. "
    "Redistributed under Space-Track's approval for basic SSA data conditioned on citation."
)
DEFAULT_OUT = REPO_ROOT / "data" / "sample" / "spacetrack_gp_history_{norad}.json"
TIMEOUT_S = 120


class FetchError(RuntimeError):
    pass


def build_query(norad: int, days: float) -> str:
    """The Space-Track query URL (REST path form). '>' is %3E and the space in 'EPOCH asc' is %20."""
    if not (isinstance(norad, int) and 0 < norad < 10**9):
        raise FetchError(f"invalid NORAD id {norad!r}")
    if not (0 < days <= 60):
        raise FetchError("--days must be in (0, 60]: gp_history is a once-per-lifetime download, keep it bounded")
    return f"{QUERY_BASE}/NORAD_CAT_ID/{norad}/EPOCH/%3Enow-{days:g}/orderby/EPOCH%20asc/format/json"


def credentials_from_env(env: dict[str, str] | None = None) -> tuple[str, str]:
    env = os.environ if env is None else env
    user, pw = env.get("SPACETRACK_USER", ""), env.get("SPACETRACK_PASS", "")
    if not user or not pw:
        raise FetchError(
            "set SPACETRACK_USER and SPACETRACK_PASS in your shell first "
            "(free account at https://www.space-track.org); "
            "they are read from the environment only and never printed or stored"
        )
    return user, pw


def validate_records(records: object, norad: int) -> list[dict]:
    """Return the OMM-field subset of each record; raise FetchError for anything that is not real GP history."""
    if not isinstance(records, list):
        # a failed login answers 200 with a small JSON object such as {"Login": "Failed"}
        raise FetchError(f"unexpected response (login failed or query rejected): {str(records)[:200]}")
    if len(records) < 3:
        raise FetchError(f"only {len(records)} element sets returned; need a history of at least 3")
    out = []
    for i, r in enumerate(records):
        missing = [k for k in OMM_REQUIRED if k not in r or r[k] in (None, "")]
        if missing:
            raise FetchError(f"record {i} lacks fields {missing}")
        if int(r["NORAD_CAT_ID"]) != norad:
            raise FetchError(f"record {i} is NORAD {r['NORAD_CAT_ID']}, expected {norad}")
        out.append({k: r[k] for k in OMM_REQUIRED})
    out.sort(key=lambda r: r["EPOCH"])
    return out


def default_opener():
    """urlopen-compatible callable that keeps the session cookie between the login and the query."""
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar())).open


def _call(open_fn, req, step: str, secret: str) -> str:
    """Perform one request; errors name the step and include Space-Track's own message (never the password)."""
    try:
        with open_fn(req, timeout=TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            said = e.read().decode("utf-8", "replace").replace(secret, "***")[:300]
        except Exception:  # noqa: BLE001 - the error body is a courtesy only
            said = ""
        raise FetchError(
            f"Space-Track answered HTTP {e.code} during {step}"
            + (f": {said!r}" if said else "")
            + ". Do not retry in a loop (rate limits apply)."
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(f"network error during {step}: {e}") from None


def request_history(norad: int, days: float, creds: tuple[str, str], opener=None) -> list[dict]:
    """Login (POST), then ONE data query (GET with a percent-encoded path): the standard two-step form."""
    open_fn = opener or default_opener()
    login = urllib.request.Request(
        LOGIN_URL,
        data=urllib.parse.urlencode({"identity": creds[0], "password": creds[1]}).encode(),
        headers={"User-Agent": USER_AGENT},
    )
    reply = _call(open_fn, login, "login", creds[1])
    if "failed" in reply.lower():
        raise FetchError(
            "login failed: check the email/password and that the Space-Track account is approved and activated"
        )
    query = urllib.request.Request(build_query(norad, days), headers={"User-Agent": USER_AGENT})
    payload = _call(open_fn, query, "history query", creds[1])
    try:
        return validate_records(json.loads(payload), norad)
    except json.JSONDecodeError:
        raise FetchError(f"response is not JSON: {payload[:200]!r}") from None


def write_history(records: list[dict], norad: int, days: float, out: Path) -> dict:
    when = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc = {
        "schema_version": 1,
        "_note": (
            f"REAL Space-Track gp_history for NORAD {norad}: {len(records)} element sets, epochs "
            f"{records[0]['EPOCH']} .. {records[-1]['EPOCH']}. " + CITATION.format(when=when)
        ),
        "data_source": "REAL",
        "source": "Space-Track.org gp_history",
        "citation": CITATION.format(when=when),
        "norad_id": norad,
        "retrieved_utc": when,
        "query": build_query(norad, days),
        "snapshots": records,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return doc


def main(argv: list[str] | None = None, env: dict[str, str] | None = None, opener=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--norad", type=int, default=25544)
    ap.add_argument("--days", type=float, default=21.0, help="history length ending now (default 21)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--force", action="store_true", help="re-download even if the file exists (violates the 1/lifetime rule)"
    )
    args = ap.parse_args(argv)
    out = args.out or Path(str(DEFAULT_OUT).format(norad=args.norad))
    try:
        if out.exists() and not args.force:
            raise FetchError(
                f"{out} already exists. Space-Track allows each object's history to be downloaded once; "
                "use the stored file (or --force if you really must)."
            )
        creds = credentials_from_env(env)
        records = request_history(args.norad, args.days, creds, opener)
        doc = write_history(records, args.norad, args.days, out)
    except FetchError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(
        f"saved {len(doc['snapshots'])} real element sets "
        f"({doc['snapshots'][0]['EPOCH']} .. {doc['snapshots'][-1]['EPOCH']}) -> {out}"
    )
    print("next: python -m catalog.residuals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
