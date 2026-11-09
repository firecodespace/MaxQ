"""WP-01 catalog pipeline: CelesTrak GP data -> cached raw files -> data/processed/catalog.parquet.

Run::

    python -m catalog.fetch                 # fetch (cached, polite) and build the parquet
    python -m catalog.fetch --offline       # no network: use cache, else the committed sample
    python -m catalog.fetch --make-sample   # rebuild data/sample/celestrak_sample.json from the cache

CelesTrak facts this module is built around (verified in their docs, 2026-11-10):
  * Query form: https://celestrak.org/NORAD/elements/gp.php?GROUP=<group>&FORMAT=<format>
    (format defaults to CSV since 2026-05-09, so we always pass FORMAT explicitly; we use 3LE).
  * GP data only refreshes every 2 hours, and CelesTrak now enforces ONE download per update per
    query (HTTP 403 "GP data has not updated since your last successful download ...").
  * More than 50 HTTP 301/403/404 responses in 2 hours gets an IP firewalled, so we never retry on
    those, never follow redirects, and refuse to hit the network again for 2 h after any error.
  * The 5-digit catalog-number space ran out on 2026-07-11. Objects with catalog numbers >= 100000
    are NOT available in TLE/3LE format, so this catalog (whose contract carries tle1/tle2) cannot
    contain them. The limitation is recorded in the parquet metadata and docs/decisions.md.

Owner: Yash (lane: catalog/).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from contracts.validate import SCHEMA_VERSION, validate
from core.constants import MU_M3_S2, R_E_M

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw" / "celestrak"
SAMPLE_PATH = REPO_ROOT / "data" / "sample" / "celestrak_sample.json"
OUT_PATH = REPO_ROOT / "data" / "processed" / "catalog.parquet"

BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"
FORMAT = "3le"
USER_AGENT = "MaxQ-catalog/0.1 (educational launch-planner project; respects CelesTrak 2h update policy)"
MIN_REFRESH = timedelta(hours=2)  # CelesTrak updates GP data once every 2 hours
TIMEOUT_S = 60

# (label written to the `group` column, CelesTrak GROUP name). First label wins when an object is in
# several groups, so list the most specific first. `active` is last and is split into `starlink` by
# name (CelesTrak asks users not to download both `active` and `starlink`; starlink is a subset).
GROUPS: list[tuple[str, str]] = [
    ("stations", "stations"),
    ("gps-ops", "gps-ops"),
    ("geo", "geo"),
    ("debris", "fengyun-1c-debris"),
    ("debris", "iridium-33-debris"),
    ("debris", "cosmos-2251-debris"),
    ("active", "active"),
]
# Small groups used for the committed offline sample.
SAMPLE_PER_GROUP = {
    "stations": 5,
    "gps-ops": 6,
    "geo": 6,
    "fengyun-1c-debris": 6,
    "iridium-33-debris": 4,
    "cosmos-2251-debris": 4,
    "active": 40,
}
SAMPLE_ALWAYS_INCLUDE = (25544,)  # ISS must be in the offline sample

NOTE_6DIGIT = (
    "CelesTrak TLE/3LE excludes objects with catalog numbers >= 100000 (5-digit space exhausted 2026-07-11); "
    "such objects are absent from this catalog."
)


class FetchError(RuntimeError):
    """Network or policy problem; message says what to do."""


# --------------------------------------------------------------------------- cached, polite fetching


@dataclass
class RawResult:
    text: str
    fetched_utc: str
    url: str
    from_cache: bool
    stale: bool = False


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _paths(group: str, raw_dir: Path) -> tuple[Path, Path]:
    return raw_dir / f"{group}.{FORMAT}", raw_dir / f"{group}.{FORMAT}.meta.json"


def _read_meta(meta_path: Path) -> dict:
    return json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # a 301 counts against CelesTrak's error budget: surface it instead
        return None


def fetch_group(
    group: str, raw_dir: Path = RAW_DIR, *, allow_stale: bool = False, now: datetime | None = None
) -> RawResult:
    """Return 3LE text for a CelesTrak GROUP, hitting the network at most once per 2 hours per group."""
    now = now or _utcnow()
    data_path, meta_path = _paths(group, raw_dir)
    meta = _read_meta(meta_path)
    url = f"{BASE_URL}?GROUP={group}&FORMAT={FORMAT}"

    def cached(stale=False) -> RawResult:
        return RawResult(data_path.read_text(encoding="utf-8"), meta["fetched_utc"], url, True, stale)

    if data_path.exists() and meta.get("fetched_utc"):
        age = now - datetime.fromisoformat(meta["fetched_utc"].replace("Z", "+00:00"))
        if age < MIN_REFRESH:
            return cached()
    if meta.get("last_error_utc"):
        since = now - datetime.fromisoformat(meta["last_error_utc"].replace("Z", "+00:00"))
        if since < MIN_REFRESH:
            if allow_stale and data_path.exists():
                return cached(stale=True)
            raise FetchError(
                f"{group}: last request failed at {meta['last_error_utc']} ({meta.get('last_error')}); not retrying "
                f"for 2 h to protect against CelesTrak's firewall. Use --offline or --allow-stale."
            )

    raw_dir.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")[:300]
        if e.code == 403 and "has not updated" in body_txt and data_path.exists():
            # Expected under the one-download-per-update policy: our cache is already the latest data.
            meta["fetched_utc"] = meta.get("fetched_utc")
            return cached()
        _record_error(meta_path, meta, now, f"HTTP {e.code}: {body_txt}")
        raise FetchError(
            f"{group}: HTTP {e.code} from {url}\n  {body_txt}\n  Do not retry immediately: repeated 301/403/404 "
            f"responses get IPs firewalled. Re-run with --offline, or after 2 h."
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _record_error(meta_path, meta, now, f"network: {e}")
        if allow_stale and data_path.exists():
            return cached(stale=True)
        raise FetchError(f"{group}: network error for {url}: {e}. Use --offline or --allow-stale.") from None

    if status != 200 or "\n1 " not in "\n" + body:
        _record_error(meta_path, meta, now, f"unexpected response (status {status}): {body[:200]!r}")
        raise FetchError(f"{group}: response from {url} is not 3LE data: {body[:200]!r}")
    data_path.write_text(body, encoding="utf-8")
    meta = {
        "url": url,
        "fetched_utc": _iso(now),
        "bytes": len(body.encode("utf-8")),
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return RawResult(body, meta["fetched_utc"], url, False)


def _record_error(meta_path: Path, meta: dict, now: datetime, msg: str) -> None:
    meta = dict(meta, last_error_utc=_iso(now), last_error=msg)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- parsing


def _checksum_ok(line: str) -> bool:
    return (
        len(line) == 69
        and line[68].isdigit()
        and (sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68]) % 10 == int(line[68]))
    )


def _epoch_utc(l1: str) -> str:
    yy = int(l1[18:20])
    year = 2000 + yy if yy < 57 else 1900 + yy
    day = float(l1[20:32])
    dt = datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=day - 1.0)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_3le(text: str) -> tuple[list[dict], int]:
    """Parse 3LE text into catalog rows (without `group`). Returns (rows, n_rejected).

    Rejected: malformed lines, bad checksums, mismatched catalog numbers. Callers decide how many is too many.
    """
    lines = [ln.rstrip("\r\n") for ln in text.splitlines() if ln.strip()]
    rows, rejected = [], 0
    i = 0
    while i < len(lines):
        if lines[i].startswith("1 ") and i + 1 < len(lines):  # tolerate name-less 2LE
            name, l1, l2, i = "", lines[i], lines[i + 1], i + 2
        elif i + 2 < len(lines):
            name, l1, l2, i = lines[i].strip(), lines[i + 1], lines[i + 2], i + 3
        else:
            rejected += 1
            break
        try:
            if not (l1.startswith("1 ") and l2.startswith("2 ") and _checksum_ok(l1) and _checksum_ok(l2)):
                raise ValueError("format/checksum")
            norad = int(l1[2:7])
            if int(l2[2:7]) != norad:
                raise ValueError("catalog number mismatch")
            mean_motion = float(l2[52:63])
            a_m = (MU_M3_S2 / (mean_motion * 2 * np.pi / 86400.0) ** 2) ** (1 / 3)
            rows.append(
                {
                    "norad_id": norad,
                    "name": name,
                    "tle1": l1,
                    "tle2": l2,
                    "epoch_utc": _epoch_utc(l1),
                    "incl_deg": float(l2[8:16]),
                    "ecc": float("0." + l2[26:33].strip()),
                    "mean_motion_rev_day": mean_motion,
                    "raan_deg": float(l2[17:25]),
                    "alt_km_approx": round((a_m - R_E_M) / 1000.0, 3),
                }
            )
        except (ValueError, IndexError):
            rejected += 1
    return rows, rejected


# --------------------------------------------------------------------------- build the catalog


def build_catalog(sources: list[tuple[str, str, str]]) -> tuple[pd.DataFrame, dict]:
    """sources: [(group_label, celestrak_group, text)] in priority order. Returns (df, stats)."""
    seen: dict[int, dict] = {}
    stats = {"rejected_records": 0, "duplicates_dropped": 0, "per_source": {}}
    for label, cgroup, text in sources:
        rows, rejected = parse_3le(text)
        stats["rejected_records"] += rejected
        stats["per_source"][cgroup] = len(rows)
        for r in rows:
            if r["norad_id"] in seen:
                stats["duplicates_dropped"] += 1
                continue
            r["group"] = "starlink" if label == "active" and r["name"].upper().startswith("STARLINK") else label
            seen[r["norad_id"]] = r
    if not seen:
        raise FetchError("no catalog rows parsed from any source")
    total = sum(stats["per_source"].values()) + stats["rejected_records"]
    if stats["rejected_records"] > max(3, 0.001 * total):
        raise FetchError(
            f"{stats['rejected_records']} of {total} TLE records rejected (bad format/checksum); refusing to continue"
        )
    cols = [
        "norad_id",
        "name",
        "group",
        "tle1",
        "tle2",
        "epoch_utc",
        "incl_deg",
        "ecc",
        "mean_motion_rev_day",
        "raan_deg",
        "alt_km_approx",
    ]
    df = pd.DataFrame(list(seen.values()))[cols].sort_values("norad_id", ignore_index=True)
    df["norad_id"] = df["norad_id"].astype("int64")
    return df, stats


def write_catalog(df: pd.DataFrame, path: Path, extra_meta: dict) -> None:
    """Validate against the contract, then write parquet with schema_version file metadata."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    meta = {"maxq_schema_version": str(SCHEMA_VERSION), "maxq_note": NOTE_6DIGIT}
    meta.update({f"maxq_{k}": str(v) for k, v in extra_meta.items()})
    table = table.replace_schema_metadata(meta)
    validate("catalog", table)  # fail loudly before anything lands on disk
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


def load_catalog(path: Path = OUT_PATH) -> pd.DataFrame:
    """Read and validate data/processed/catalog.parquet."""
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} not found. Build it with: python -m catalog.fetch [--offline]")
    table = pq.read_table(path)
    validate("catalog", table)
    return table.to_pandas()


def get_tle(df: pd.DataFrame, norad_id: int) -> tuple[str, str, str]:
    """(name, tle1, tle2) for an object; raises KeyError with a clear message if absent."""
    hit = df[df["norad_id"] == norad_id]
    if hit.empty:
        raise KeyError(f"NORAD {norad_id} not in catalog ({len(df)} objects)")
    r = hit.iloc[0]
    return r["name"], r["tle1"], r["tle2"]


# --------------------------------------------------------------------------- offline sample


def make_sample(raw_dir: Path = RAW_DIR, path: Path = SAMPLE_PATH) -> dict:
    """Write a small real-data sample (from the raw cache) to commit to git for offline use."""
    groups, fetched = {}, []
    for label, cgroup in GROUPS:
        data_path, meta_path = _paths(cgroup, raw_dir)
        if not data_path.exists():
            raise FetchError(f"{cgroup}: no cached raw data in {raw_dir}; run `python -m catalog.fetch` first")
        meta = _read_meta(meta_path)
        fetched.append(meta["fetched_utc"])
        rows, _ = parse_3le(data_path.read_text(encoding="utf-8"))
        keep = rows[: SAMPLE_PER_GROUP[cgroup]]
        extra = [r for r in rows if r["norad_id"] in SAMPLE_ALWAYS_INCLUDE and r not in keep]
        groups[cgroup] = "".join(f"{r['name']}\n{r['tle1']}\n{r['tle2']}\n" for r in keep + extra)
    sample = {
        "schema_version": 1,
        "_note": (
            "Small REAL CelesTrak 3LE snapshot for offline use and tests "
            "(not dummy data; it ages: epochs are as of fetched_utc)."
        ),
        "source": f"{BASE_URL}?GROUP=<group>&FORMAT={FORMAT}",
        "fetched_utc": max(fetched),
        "labels": {cgroup: label for label, cgroup in GROUPS},
        "groups": groups,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sample, indent=2) + "\n", encoding="utf-8")
    return sample


def sources_from_sample(path: Path = SAMPLE_PATH) -> tuple[list[tuple[str, str, str]], str]:
    if not path.exists():
        raise FetchError(f"offline sample {path} missing")
    s = json.loads(path.read_text(encoding="utf-8"))
    return [(label, cg, s["groups"][cg]) for label, cg in GROUPS], s["fetched_utc"]


# --------------------------------------------------------------------------- CLI


def run(offline: bool = False, allow_stale: bool = False, out: Path = OUT_PATH, raw_dir: Path = RAW_DIR) -> dict:
    sources, fetched_times, origin = [], [], []
    if not offline:
        for label, cgroup in GROUPS:
            res = fetch_group(cgroup, raw_dir, allow_stale=allow_stale)
            tag = "STALE cache" if res.stale else ("cache" if res.from_cache else "fetched")
            print(f"  {cgroup:20s} {tag:12s} {res.fetched_utc}")
            sources.append((label, cgroup, res.text))
            fetched_times.append(res.fetched_utc)
            origin.append(tag)
        mode = "live/cache"
    else:
        have_cache = all(_paths(cg, raw_dir)[0].exists() for _, cg in GROUPS)
        if have_cache:
            for label, cgroup in GROUPS:
                res = RawResult(
                    _paths(cgroup, raw_dir)[0].read_text(encoding="utf-8"),
                    _read_meta(_paths(cgroup, raw_dir)[1]).get("fetched_utc", "unknown"),
                    "",
                    True,
                )
                sources.append((label, cgroup, res.text))
                fetched_times.append(res.fetched_utc)
            mode = "offline/raw-cache"
        else:
            sources, t = sources_from_sample()
            fetched_times = [t]
            mode = "offline/committed-sample"
        print(f"  offline mode: using {mode}")
    df, stats = build_catalog(sources)
    meta = {
        "fetched_utc": max(fetched_times),
        "mode": mode,
        "groups": ",".join(cg for _, cg in GROUPS),
        **{"rejected_records": stats["rejected_records"], "duplicates_dropped": stats["duplicates_dropped"]},
    }
    write_catalog(df, out, meta)
    print(
        f"  wrote {out} ({len(df)} objects; {stats['rejected_records']} rejected; "
        f"{stats['duplicates_dropped']} duplicates dropped)"
    )
    print(f"  NOTE: {NOTE_6DIGIT}")
    return {"rows": len(df), **stats, "mode": mode}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true", help="never touch the network")
    ap.add_argument("--allow-stale", action="store_true", help="on network error, use older cached data (loudly)")
    ap.add_argument(
        "--make-sample", action="store_true", help="rebuild data/sample/celestrak_sample.json from the cache"
    )
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args(argv)
    try:
        if args.make_sample:
            s = make_sample()
            print(f"wrote {SAMPLE_PATH} ({sum(v.count(chr(10)) // 3 for v in s['groups'].values())} objects)")
            return 0
        print("catalog fetch:")
        run(offline=args.offline, allow_stale=args.allow_stale, out=args.out)
        return 0
    except (FetchError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
