"""WP-09: unsupervised clustering of the catalog by orbit shape, plus an altitude congestion profile -> clusters.json.

Method
  * Features (HANDOFF): inclination, ln(mean motion) (a proxy for altitude), eccentricity.
  * Scaling is PHYSICAL, not statistical: one scaled unit = 2 deg inclination, 2 % mean motion (~90 km in LEO),
    0.005 eccentricity. So DBSCAN's eps = 1 means "within 2 deg / 2 % / 0.005 of a neighbour", which keeps the
    result stable when the catalog changes (a StandardScaler would not).
  * DBSCAN (eps = 1, min_samples = 15): density-based, no cluster count to guess, noise is labelled -1. Chosen over
    HDBSCAN after an experiment on the real catalog: HDBSCAN was purer (0.98) but split the sky into 45-72 clusters and
    called ~20 % of objects noise; DBSCAN gives ~24 recognisable clusters and ~2 % noise.
  * 2D embedding for display: an "orbit map" (x = log10 altitude, y = inclination), not PCA/UMAP: with only three
    features the two most interpretable axes show the structure better than a projection, with no extra libraries.
  * Cluster names are derived from the data (dominant object-name prefix and orbit regime), never from a lookup.
  * Congestion: object count and volume density per 25 km altitude shell, 150-2150 km (shell volume 4 pi r^2 dh).

Agreement with the catalog's own group labels is reported in ``metrics`` (the catalog's `active` group is a catch-all
and overlaps constellations in orbit space, so it is excluded from the known-group checks). No randomness: same
catalog in, same clusters out.

Run:  python -m catalog.cluster [--offline] [--out data/results/clusters.json]

Owner: Yash (lane: catalog/).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN
from sklearn.metrics import completeness_score, homogeneity_score

from catalog.fetch import OUT_PATH, build_catalog, load_catalog, sources_from_sample
from contracts.validate import validate
from core.constants import R_E_M

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data" / "results" / "clusters.json"
R_E_KM = R_E_M / 1000.0

FEATURES = ["incl_deg", "log_mean_motion", "ecc"]
SCALES = np.array([2.0, 0.02, 0.005])  # physical size of one DBSCAN unit per feature
EPS = 1.0
MIN_SAMPLES = 15
KNOWN_GROUPS = ("starlink", "geo", "gps-ops", "debris", "stations")  # `active` is a catch-all: not a check target
CONGESTION_EDGES_KM = np.arange(150.0, 2150.1, 25.0)
GEO_ALT_KM = 35786.0


# --------------------------------------------------------------------------- clustering


def feature_matrix(df: pd.DataFrame) -> np.ndarray:
    """(n, 3) raw features in the units of FEATURES."""
    return np.column_stack([df["incl_deg"], np.log(df["mean_motion_rev_day"]), df["ecc"]])


def cluster_labels(x_raw: np.ndarray, eps: float = EPS, min_samples: int = MIN_SAMPLES) -> np.ndarray:
    """DBSCAN labels on physically scaled features; -1 = noise."""
    return DBSCAN(eps=eps, min_samples=min_samples).fit_predict(x_raw / SCALES)


def relabel_by_size(labels: np.ndarray) -> np.ndarray:
    """Stable labels: 0 = largest cluster, 1 = next, ...; noise stays -1."""
    counts = Counter(int(x) for x in labels if x != -1)
    order = {old: new for new, (old, _) in enumerate(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))}
    return np.array([order.get(int(x), -1) for x in labels])


# --------------------------------------------------------------------------- naming


def _prefix(name: str) -> str | None:
    m = re.match(r"[A-Z]{3,}", re.split(r"[^A-Z0-9]+", str(name).upper())[0] if str(name) else "")
    return m.group(0) if m else None


def regime(alt_km: float, incl_deg: float) -> str:
    if abs(alt_km - GEO_ALT_KM) < 400 and incl_deg < 25:
        return "GEO belt"
    if alt_km > 30000:
        return "High orbit"
    if alt_km >= 2000:
        return "MEO"
    if 96.0 <= incl_deg <= 101.0:
        return "Sun-synchronous LEO"
    return "LEO"


def cluster_name(sub: pd.DataFrame) -> str:
    alt, incl = float(sub["alt_km_approx"].median()), float(sub["incl_deg"].median())
    reg = regime(alt, incl)
    if reg == "GEO belt":
        return "GEO belt"
    pref = Counter(p for p in map(_prefix, sub["name"]) if p)
    if pref:
        top, n = pref.most_common(1)[0]
        if n / len(sub) >= 0.5:
            label = top if len(top) <= 3 else top.title()  # keep acronyms (GPS) upper-case
            return f"{label} · {incl:.0f}° · {alt:,.0f} km"
    return f"{reg} · {incl:.0f}° · {alt:,.0f} km"


# --------------------------------------------------------------------------- congestion and metrics


def congestion_profile(df: pd.DataFrame, edges: np.ndarray = CONGESTION_EDGES_KM) -> dict:
    counts, _ = np.histogram(df["alt_km_approx"].to_numpy(), bins=edges)
    mid = R_E_KM + (edges[:-1] + edges[1:]) / 2.0
    volume = 4.0 * math.pi * mid**2 * np.diff(edges)  # km^3 of each spherical shell
    density = counts / volume * 1e6
    return {
        "altitude_edges_km": [float(e) for e in edges],
        "counts": [int(c) for c in counts],
        "density_per_million_km3": [float(f"{d:.4g}") for d in density],
        "note": (
            "Objects per spherical shell by mean altitude (circular approximation); density = count / (4 pi r^2 dh). "
            "Objects outside 150-2150 km are not shown. Eccentric objects cross shells they are not counted in."
        ),
    }


def known_group_metrics(df: pd.DataFrame, labels: np.ndarray, names: dict[int, str]) -> dict:
    out = {}
    for g in KNOWN_GROUPS:
        m = (df["group"] == g).to_numpy()
        if not m.any():
            continue
        vc = pd.Series(labels[m]).value_counts()
        vc = vc[vc.index != -1]
        best = int(vc.index[0]) if len(vc) else -1
        out[g] = {
            "objects": int(m.sum()),
            "share_in_best_cluster": round(float(vc.iloc[0]) / m.sum(), 3) if len(vc) else 0.0,
            "best_cluster": best,
            "best_cluster_name": names.get(best, "noise"),
            "share_noise": round(float((labels[m] == -1).mean()), 3),
        }
    in_cluster = labels != -1
    sizes = pd.Series(labels[in_cluster]).value_counts()
    purity = sum((df.loc[labels == c, "group"].value_counts().iloc[0]) for c in sizes.index) / max(
        int(in_cluster.sum()), 1
    )
    return {
        "known_groups": out,
        "weighted_cluster_purity_vs_group": round(float(purity), 3),
        "homogeneity_vs_group": round(float(homogeneity_score(df.loc[in_cluster, "group"], labels[in_cluster])), 3),
        "completeness_vs_group": round(float(completeness_score(df.loc[in_cluster, "group"], labels[in_cluster])), 3),
        "label_caveat": "the catalog's `active` group is a catch-all that shares orbits with constellations, and "
        "constellations span several shells, so purity/completeness against it are lower bounds, not targets of 1.0",
    }


# --------------------------------------------------------------------------- build


def build_clusters(df: pd.DataFrame, catalog_mode: str = "unknown") -> dict:
    """clusters.json (contract) for a catalog DataFrame."""
    df = df.sort_values("norad_id").reset_index(drop=True)
    x_raw = feature_matrix(df)
    labels = relabel_by_size(cluster_labels(x_raw))
    alt = df["alt_km_approx"].to_numpy()

    clusters, names, used = [], {}, Counter()
    for lab in sorted(set(labels.tolist())):
        sub = df[labels == lab]
        feats = x_raw[labels == lab]
        if lab == -1:
            name = "Unclustered (noise)"
        else:
            name = cluster_name(sub)
            used[name] += 1
            if used[name] > 1:
                name = f"{name} #{used[name]}"
        names[lab] = name
        top_group = sub["group"].value_counts()
        clusters.append(
            {
                "label": int(lab),
                "name": name,
                "count": int(len(sub)),
                "centroid": {f: round(float(v), 4) for f, v in zip(FEATURES, feats.mean(axis=0), strict=True)},
                "alt_km_min": round(float(sub["alt_km_approx"].min()), 1),
                "alt_km_median": round(float(sub["alt_km_approx"].median()), 1),
                "alt_km_max": round(float(sub["alt_km_approx"].max()), 1),
                "incl_deg_median": round(float(sub["incl_deg"].median()), 2),
                "dominant_group": str(top_group.index[0]),
                "dominant_share": round(float(top_group.iloc[0]) / len(sub), 3),
            }
        )
    points = [
        {"norad_id": int(n), "cluster": int(c), "x": round(math.log10(max(a, 100.0)), 3), "y": round(float(i), 2)}
        for n, c, a, i in zip(df["norad_id"], labels, alt, df["incl_deg"], strict=True)
    ]
    n_clusters = len([c for c in clusters if c["label"] != -1])
    metrics = {
        "algorithm": "DBSCAN",
        "eps_scaled": EPS,
        "min_samples": MIN_SAMPLES,
        "scales_per_unit": dict(zip(FEATURES, SCALES.tolist(), strict=True)),
        "objects": int(len(df)),
        "clusters": n_clusters,
        "noise_fraction": round(float((labels == -1).mean()), 4),
        "catalog_mode": catalog_mode,
        "latest_epoch_utc": str(df["epoch_utc"].max()),
        **known_group_metrics(df, labels, names),
    }
    obj = {
        "schema_version": 1,
        "_note": (
            "REAL unsupervised clustering (DBSCAN) of the real catalog snapshot; cluster names are derived from the "
            "data (dominant name prefix + orbit regime). Mean-element orbits, TLE/OMM accuracy applies. "
            "Not an operational product."
        ),
        "features": FEATURES,
        "embedding": {
            "x": "log10 altitude (km, circular approximation)",
            "y": "inclination (deg)",
            "method": "orbit map (no projection)",
        },
        "metrics": metrics,
        "congestion": congestion_profile(df),
        "clusters": clusters,
        "points": points,
    }
    return validate("clusters", obj)


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cluster the catalog -> data/results/clusters.json")
    ap.add_argument("--offline", action="store_true", help="use the committed offline sample (tiny: mostly noise)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    try:
        if args.offline or not OUT_PATH.exists():
            if not args.offline:
                print("WARNING: catalog.parquet missing; using the tiny offline sample", file=sys.stderr)
            df, mode = build_catalog(sources_from_sample()[0])[0], "offline-sample"
        else:
            df, mode = load_catalog(OUT_PATH), "full-catalog"
        obj = build_clusters(df, mode)
    except Exception as e:  # noqa: BLE001 - CLI boundary
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(obj, separators=(",", ":")) + "\n", encoding="utf-8")
    m = obj["metrics"]
    print(f"{m['objects']} objects -> {m['clusters']} clusters, {m['noise_fraction']:.1%} noise ({mode})")
    for c in obj["clusters"][:12]:
        print(f"  {c['label']:>3} {c['count']:>6}  {c['name']}")
    print("known-group agreement:")
    for g, v in m["known_groups"].items():
        print(f"  {g:9s} {v['share_in_best_cluster']:.0%} of {v['objects']} in '{v['best_cluster_name']}'")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
