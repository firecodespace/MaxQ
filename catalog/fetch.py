"""WP-01 catalog pipeline: CelesTrak GP data (OMM JSON) -> cached raw files -> data/processed/catalog.parquet.

Run::

    python -m catalog.fetch                 # fetch (cached, polite) and build the parquet
    python -m catalog.fetch --offline       # no network: use cache, else the committed sample
    python -m catalog.fetch --make-sample   # rebuild data/sample/celestrak_sample.json from the cache

CelesTrak facts this module is built around (verified in their docs, 2026-11-10):
  * Query form: https://celestrak.org/NORAD/elements/gp.php?GROUP=<group>&FORMAT=<format>
    (format defaults to CSV since 2026-05-09, so we always pass FORMAT explicitly; we use JSON = OMM).
  * GP data only refreshes every 2 hours, and CelesTrak now enforces ONE download per update per
    query (HTTP 403 "GP data has not updated since your last successful download ...").
  * More than 50 HTTP 301/403/404 responses in 2 hours gets an IP firewalled, so we never retry on
    those, never follow redirects, and refuse to hit the network again for 2 h after any error.
  * The 5-digit catalog-number space ran out on 2026-07-11. Objects numbered >= 100000 have NO TLE, but the OMM
    (JSON) format carries them with the full SGP4 element set, so this catalog is complete. For those objects the
    contract's tle1/tle2 are null and consumers propagate from the OMM columns (catalog.omm.satrec_from_row).

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

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from catalog.omm import OmmError, is_missing, omm_to_row
from contracts.validate import SCHEMA_VERSION, validate

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw" / "celestrak"
SAMPLE_PATH = REPO_ROOT / "data" / "sample" / "celestrak_sample.json"
OUT_PATH = REPO_ROOT / "data" / "processed" / "catalog.parquet"

BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"
FORMAT = "json"
USER_AGENT = "MaxQ-catalog/0.1 (educational launch-planner project; respects CelesTrak 2h update policy)"
MIN_REFRESH = timedelta(hours=2)  # CelesTrak updates GP data once every 2 hours
TIMEOUT_S = 120

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
    "stations": 8,
    "gps-ops": 6,
    "geo": 6,
    "fengyun-1c-debris": 6,
    "iridium-33-debris": 4,
    "cosmos-2251-debris": 4,
    "active": 40,
}
SAMPLE_ALWAYS_INCLUDE = (25544,)  # ISS must be in the offline sample
SAMPLE_MIN_SIX_DIGIT = 6  # keep some objects >= 100000 in the sample so the OMM-only path is always tested

NOTE_CATALOG = (
    "Built from CelesTrak OMM (JSON): complete, including objects numbered >= 100000 (tle1/tle2 null for those; "
    "propagate from the OMM columns)."
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


def _looks_like_omm(body: str) -> bool:
    try:
        data = json.loads(body)
    except ValueError:
        return False
    return isinstance(data, list) and bool(data) and isinstance(data[0], dict) and "NORAD_CAT_ID" in data[0]


def fetch_group(
    group: str, raw_dir: Path = RAW_DIR, *, allow_stale: bool = False, now: datetime | None = None
) -> RawResult:
    """Return OMM JSON text for a CelesTrak GROUP, hitting the network at most once per 2 hours per group."""
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

    if status != 200 or not _looks_like_omm(body):
        _record_error(meta_path, meta, now, f"unexpected response (status {status}): {body[:200]!r}")
        raise FetchError(f"{group}: response from {url} is not OMM JSON: {body[:200]!r}")
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


def parse_omm(text: str) -> tuple[list[dict], int]:
    """Parse OMM JSON text into catalog rows (without `group`). Returns (rows, n_rejected).

    Rejected: records that are malformed or unphysical. Callers decide how many is too many.
    """
    try:
        records = json.loads(text)
    except ValueError as e:
        raise FetchError(f"OMM JSON does not parse: {e}") from None
    if not isinstance(records, list):
        raise FetchError("OMM JSON is not a list of records")
    rows, rejected = [], 0
    for rec in records:
        try:
            rows.append(omm_to_row(rec))
        except (OmmError, AttributeError, TypeError):
            rejected += 1
    return rows, rejected


# --------------------------------------------------------------------------- build the catalog

CATALOG_COLUMNS = [
    "norad_id", "name", "group", "tle1", "tle2", "epoch_utc", "incl_deg", "ecc", "mean_motion_rev_day",
    "raan_deg", "alt_km_approx", "intl_designator", "classification", "ephemeris_type", "elset_no",
    "rev_at_epoch", "argp_deg", "mean_anomaly_deg", "bstar", "mean_motion_dot", "mean_motion_ddot",
]  # fmt: skip


def build_catalog(sources: list[tuple[str, str, str]]) -> tuple[pd.DataFrame, dict]:
    """sources: [(group_label, celestrak_group, omm_json_text)] in priority order. Returns (df, stats)."""
    seen: dict[int, dict] = {}
    stats = {"rejected_records": 0, "duplicates_dropped": 0, "per_source": {}}
    for label, cgroup, text in sources:
        rows, rejected = parse_omm(text)
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
            f"{stats['rejected_records']} of {total} OMM records rejected (malformed); refusing to continue"
        )
    df = pd.DataFrame(list(seen.values()))[CATALOG_COLUMNS].sort_values("norad_id", ignore_index=True)
    df["norad_id"] = df["norad_id"].astype("int64")
    for c in ("ephemeris_type", "elset_no", "rev_at_epoch"):
        df[c] = df[c].astype("int64")
    stats["six_digit_objects"] = int((df["norad_id"] >= 100000).sum())
    return df, stats


def write_catalog(df: pd.DataFrame, path: Path, extra_meta: dict) -> None:
    """Validate against the contract, then write parquet with schema_version file metadata."""
    table = pa.Table.from_pandas(df, preserve_index=False)
    meta = {"maxq_schema_version": str(SCHEMA_VERSION), "maxq_note": NOTE_CATALOG}
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


def get_row(df: pd.DataFrame, norad_id: int) -> pd.Series:
    hit = df[df["norad_id"] == norad_id]
    if hit.empty:
        raise KeyError(f"NORAD {norad_id} not in catalog ({len(df)} objects)")
    return hit.iloc[0]


def get_tle(df: pd.DataFrame, norad_id: int) -> tuple[str, str, str]:
    """(name, tle1, tle2). Objects numbered >= 100000 have no TLE: use catalog.omm.satrec_from_row(get_row(...))."""
    r = get_row(df, norad_id)
    if is_missing(r["tle1"]):
        raise ValueError(
            f"NORAD {norad_id} has no TLE (catalog number >= 100000); use satrec_from_row(get_row(df, id))"
        )
    return r["name"], r["tle1"], r["tle2"]


# --------------------------------------------------------------------------- offline sample


def make_sample(raw_dir: Path = RAW_DIR, path: Path = SAMPLE_PATH) -> dict:
    """Write a small real-data sample (from the raw cache) to commit to git for offline use."""
    groups, fetched = {}, []
    six_kept = 0
    for label, cgroup in GROUPS:
        data_path, meta_path = _paths(cgroup, raw_dir)
        if not data_path.exists():
            raise FetchError(f"{cgroup}: no cached raw data in {raw_dir}; run `python -m catalog.fetch` first")
        fetched.append(_read_meta(meta_path)["fetched_utc"])
        records = json.loads(data_path.read_text(encoding="utf-8"))
        keep = records[: SAMPLE_PER_GROUP[cgroup]]
        extra = [r for r in records if int(r["NORAD_CAT_ID"]) in SAMPLE_ALWAYS_INCLUDE and r not in keep]
        if six_kept < SAMPLE_MIN_SIX_DIGIT:
            six = [r for r in records if int(r["NORAD_CAT_ID"]) >= 100000 and r not in keep + extra]
            take = six[: SAMPLE_MIN_SIX_DIGIT - six_kept]
            extra += take
            six_kept += len(take)
        six_kept += sum(int(r["NORAD_CAT_ID"]) >= 100000 for r in keep)
        groups[cgroup] = keep + extra
    sample = {
        "schema_version": 1,
        "_note": (
            "Small REAL CelesTrak OMM (JSON) snapshot for offline use and tests "
            "(not dummy data; it ages: epochs are as of fetched_utc). Includes some objects numbered >= 100000."
        ),
        "source": f"{BASE_URL}?GROUP=<group>&FORMAT={FORMAT}",
        "fetched_utc": max(fetched),
        "labels": {cgroup: label for label, cgroup in GROUPS},
        "groups": groups,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sample, indent=1) + "\n", encoding="utf-8")
    return sample


def sources_from_sample(path: Path = SAMPLE_PATH) -> tuple[list[tuple[str, str, str]], str]:
    if not path.exists():
        raise FetchError(f"offline sample {path} missing")
    s = json.loads(path.read_text(encoding="utf-8"))
    return [(label, cg, json.dumps(s["groups"][cg])) for label, cg in GROUPS], s["fetched_utc"]


# --------------------------------------------------------------------------- CLI


def run(offline: bool = False, allow_stale: bool = False, out: Path = OUT_PATH, raw_dir: Path = RAW_DIR) -> dict:
    sources, fetched_times = [], []
    if not offline:
        for label, cgroup in GROUPS:
            res = fetch_group(cgroup, raw_dir, allow_stale=allow_stale)
            tag = "STALE cache" if res.stale else ("cache" if res.from_cache else "fetched")
            print(f"  {cgroup:20s} {tag:12s} {res.fetched_utc}")
            sources.append((label, cgroup, res.text))
            fetched_times.append(res.fetched_utc)
        mode = "live/cache"
    else:
        have_cache = all(_paths(cg, raw_dir)[0].exists() for _, cg in GROUPS)
        if have_cache:
            for label, cgroup in GROUPS:
                sources.append((label, cgroup, _paths(cgroup, raw_dir)[0].read_text(encoding="utf-8")))
                fetched_times.append(_read_meta(_paths(cgroup, raw_dir)[1]).get("fetched_utc", "unknown"))
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
        "rejected_records": stats["rejected_records"],
        "duplicates_dropped": stats["duplicates_dropped"],
        "six_digit_objects": stats["six_digit_objects"],
    }
    write_catalog(df, out, meta)
    print(
        f"  wrote {out} ({len(df)} objects, {stats['six_digit_objects']} numbered >= 100000; "
        f"{stats['rejected_records']} rejected; {stats['duplicates_dropped']} duplicates dropped)"
    )
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
            print(f"wrote {SAMPLE_PATH} ({sum(len(v) for v in s['groups'].values())} objects)")
            return 0
        print("catalog fetch:")
        run(offline=args.offline, allow_stale=args.allow_stale, out=args.out)
        return 0
    except (FetchError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
