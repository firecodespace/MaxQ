"""WP-15: how wrong is SGP4 as an element set ages, and can a small learned correction reduce that error?

Evaluated ONLY on a real element-set history (Space-Track gp_history, fetched once with
scripts/fetch_spacetrack_history.py). A history that is not marked REAL is refused, so synthetic data can never
produce a headline number. An earlier version of this module used a generated "history" with a perfectly linear
drift; its 86.9 % result measured recovery of that generator and was withdrawn.

Protocol
  * Baseline snapshot i is propagated with SGP4 to the epoch t_j of every LATER snapshot j (age = t_j - t_i <= 7 d).
  * Truth proxy: snapshot j's own position at its epoch (a fresh fit, typically good to ~1 km). It is NOT ground truth;
    the correction can never look better than that floor.
  * Residual = truth - baseline, expressed in the baseline's Radial / In-track / Cross-track (RIC) frame.
  * Model: ridge regression on age, age^2 and the baseline's orbital phase (cos/sin u, cos/sin 2u), one per RIC axis.
  * Strict temporal split at the median snapshot epoch T: training pairs lie entirely before T (t_j < T), test pairs
    entirely after (t_i > T), so no snapshot is shared between train and test.
  * Pairs that span a suspected manoeuvre (a jump in mean motion between consecutive snapshots, e.g. an ISS reboost)
    are excluded and reported: no smooth model should be asked to predict a burn.
  * Uncertainty: cluster bootstrap over test baselines; a result is only called an improvement if the 90 % interval of
    the RMS reduction excludes zero. A simpler "along-track drift only" model is reported alongside.

Run:  python -m catalog.residuals [--history data/sample/spacetrack_gp_history_25544.json]

Owner: Yash (lane: catalog/); originally contributed by Shiva.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from sgp4 import omm as sgp4_omm
from sgp4.api import Satrec
from sklearn.linear_model import Ridge

from catalog.propagate import propagate_teme, sat_epoch_unix

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HISTORY = REPO_ROOT / "data" / "sample" / "spacetrack_gp_history_25544.json"
DEFAULT_OUT = REPO_ROOT / "data" / "results" / "residuals" / "sgp4_residuals.json"
DEFAULT_PLOT = REPO_ROOT / "data" / "results" / "sgp4_residuals_benchmark.png"
AGE_BINS_D = (0.0, 1.0, 2.0, 3.0, 5.0, 7.0)
JUMP_THRESHOLD_REV_DAY = 5e-4  # mean-motion step beyond the typical drift that we treat as a manoeuvre
MIN_PAIRS = 30


class HistoryError(RuntimeError):
    """The element-set history is missing, not real, or too short for a meaningful evaluation."""


# --------------------------------------------------------------------------- RIC frame


def teme_to_ric_matrix(r_teme: np.ndarray, v_teme: np.ndarray) -> np.ndarray:
    """3x3 orthonormal rotation(s) from TEME to the satellite RIC frame; ``v_ric = R @ v_teme``.

    R-hat = r/|r| (radial); C-hat = (r x v)/|r x v| (cross-track); I-hat = C-hat x R-hat (in-track).
    Accepts (3,) or (N, 3); returns (3, 3) or (N, 3, 3).
    """
    r = np.atleast_2d(r_teme)
    v = np.atleast_2d(v_teme)
    r_hat = r / np.maximum(np.linalg.norm(r, axis=-1, keepdims=True), 1e-12)
    h = np.cross(r, v)
    c_hat = h / np.maximum(np.linalg.norm(h, axis=-1, keepdims=True), 1e-12)
    i_hat = np.cross(c_hat, r_hat)
    rot = np.stack([r_hat, i_hat, c_hat], axis=-2)
    return rot[0] if np.ndim(r_teme) == 1 else rot


def teme_to_ric(delta_r_teme: np.ndarray, r_teme: np.ndarray, v_teme: np.ndarray) -> np.ndarray:
    rot = teme_to_ric_matrix(r_teme, v_teme)
    return rot @ delta_r_teme if delta_r_teme.ndim == 1 else np.einsum("nij,nj->ni", rot, delta_r_teme)


def ric_to_teme(delta_r_ric: np.ndarray, r_teme: np.ndarray, v_teme: np.ndarray) -> np.ndarray:
    rot = teme_to_ric_matrix(r_teme, v_teme)
    return rot.T @ delta_r_ric if delta_r_ric.ndim == 1 else np.einsum("nji,nj->ni", rot, delta_r_ric)


def arg_of_latitude(r_teme: np.ndarray, v_teme: np.ndarray) -> float:
    """Angle of the position from the ascending node, measured in the orbit plane (rad, 0..2pi)."""
    h = np.cross(r_teme, v_teme)
    h_hat = h / np.linalg.norm(h)
    node = np.cross([0.0, 0.0, 1.0], h_hat)
    node /= np.linalg.norm(node)
    return float(np.arctan2(r_teme @ np.cross(h_hat, node), r_teme @ node) % (2.0 * np.pi))


# --------------------------------------------------------------------------- history


@dataclass
class Snapshot:
    omm: dict
    sat: Satrec
    t_unix: float
    mean_motion: float


def load_history(path: str | Path = DEFAULT_HISTORY, allow_synthetic: bool = False) -> tuple[list[Snapshot], dict]:
    """Load snapshots from a history file. Refuses anything not marked ``data_source: REAL`` unless allowed."""
    path = Path(path)
    if not path.exists():
        raise HistoryError(
            f"{path} not found. Real history must be downloaded once from Space-Track with "
            "scripts/fetch_spacetrack_history.py (free account; credentials only in environment variables)."
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    source = doc.get("data_source") or ("SYNTHETIC" if "synthetic" in str(doc.get("_note", "")).lower() else "UNKNOWN")
    if source != "REAL" and not allow_synthetic:
        raise HistoryError(
            f"{path.name} is marked {source!r}, not REAL: refusing to compute accuracy figures from it "
            "(use allow_synthetic=True only for tests of the plumbing)"
        )
    snaps: dict[float, Snapshot] = {}
    for rec in doc["snapshots"]:
        sat = Satrec()
        sgp4_omm.initialize(sat, rec)
        t = sat_epoch_unix(sat)
        snaps[round(t, 3)] = Snapshot(rec, sat, t, float(rec["MEAN_MOTION"]))  # duplicate epochs: keep the last
    out = [snaps[k] for k in sorted(snaps)]
    if len(out) < 6:
        raise HistoryError(f"history has only {len(out)} distinct element sets; need at least 6")
    return out, {
        "data_source": source,
        "file": path.name,
        "citation": doc.get("citation"),
        "norad_id": doc.get("norad_id"),
    }


def detect_jumps(snaps: list[Snapshot], threshold: float = JUMP_THRESHOLD_REV_DAY) -> list[float]:
    """Epochs (unix s) of snapshots that follow a mean-motion step far from the typical step (suspected manoeuvres)."""
    n = np.array([s.mean_motion for s in snaps])
    steps = np.diff(n)
    typical = float(np.median(steps))
    return [snaps[k + 1].t_unix for k in np.flatnonzero(np.abs(steps - typical) > threshold)]


# --------------------------------------------------------------------------- pairs


@dataclass
class Pairs:
    base_idx: np.ndarray
    truth_idx: np.ndarray
    t_base: np.ndarray
    t_truth: np.ndarray
    age_d: np.ndarray
    phase: np.ndarray  # baseline arg of latitude at t_truth
    res_ric: np.ndarray  # (N, 3) truth - baseline in the baseline's RIC frame, km

    def __len__(self) -> int:
        return len(self.age_d)

    def take(self, mask: np.ndarray) -> Pairs:
        return Pairs(
            *(
                a[mask]
                for a in (
                    self.base_idx,
                    self.truth_idx,
                    self.t_base,
                    self.t_truth,
                    self.age_d,
                    self.phase,
                    self.res_ric,
                )
            )
        )


def build_pairs(
    snaps: list[Snapshot], max_age_d: float = 7.0, min_age_d: float = 1.0 / 24.0, exclude_jumps: bool = True
) -> Pairs:
    jumps = detect_jumps(snaps) if exclude_jumps else []
    rows = []
    for i, a in enumerate(snaps):
        for j in range(i + 1, len(snaps)):
            b = snaps[j]
            age = (b.t_unix - a.t_unix) / 86400.0
            if age > max_age_d:
                break
            if age < min_age_d or any(a.t_unix < tj <= b.t_unix for tj in jumps):
                continue
            r_base, v_base = propagate_teme(a.sat, b.t_unix)
            r_true, _ = propagate_teme(b.sat, b.t_unix)
            res = teme_to_ric(r_true[0] - r_base[0], r_base[0], v_base[0])
            rows.append((i, j, a.t_unix, b.t_unix, age, arg_of_latitude(r_base[0], v_base[0]), res))
    if not rows:
        raise HistoryError("no baseline/truth pairs could be built from this history")
    return Pairs(
        np.array([r[0] for r in rows]), np.array([r[1] for r in rows]), np.array([r[2] for r in rows]),
        np.array([r[3] for r in rows]), np.array([r[4] for r in rows]), np.array([r[5] for r in rows]),
        np.array([r[6] for r in rows]),
    )  # fmt: skip


# --------------------------------------------------------------------------- models


class ResidualModel:
    """Ridge regression of RIC residuals on element-set age and orbital phase.

    kind="full": age, age^2, cos u, sin u, cos 2u, sin 2u for each RIC axis.
    kind="drift_only": age, age^2 for the in-track axis only (radial and cross-track predicted as 0).
    """

    def __init__(self, kind: str = "full", alpha: float = 1e-2):
        if kind not in ("full", "drift_only"):
            raise ValueError(f"unknown model kind {kind!r}")
        self.kind, self.alpha = kind, alpha
        self.models: list[Ridge | None] = [None, None, None]

    def _x(self, age_d: np.ndarray, phase: np.ndarray) -> np.ndarray:
        cols = [age_d, age_d**2]
        if self.kind == "full":
            cols += [np.cos(phase), np.sin(phase), np.cos(2 * phase), np.sin(2 * phase)]
        return np.column_stack(cols)

    def fit(self, p: Pairs) -> ResidualModel:
        x = self._x(p.age_d, p.phase)
        axes = (0, 1, 2) if self.kind == "full" else (1,)
        for k in axes:
            self.models[k] = Ridge(alpha=self.alpha).fit(x, p.res_ric[:, k])
        return self

    def predict(self, p: Pairs) -> np.ndarray:
        x = self._x(p.age_d, p.phase)
        return np.column_stack([m.predict(x) if m is not None else np.zeros(len(p)) for m in self.models])


# --------------------------------------------------------------------------- evaluation


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else float("nan")


def split_pairs(pairs: Pairs, snaps: list[Snapshot]) -> tuple[Pairs, Pairs, float]:
    """Strict temporal split at the median snapshot epoch T: train t_truth < T, test t_base > T (nothing shared)."""
    split_t = float(np.median([s.t_unix for s in snaps]))
    train, test = pairs.take(pairs.t_truth < split_t), pairs.take(pairs.t_base > split_t)
    if len(train) < MIN_PAIRS or len(test) < MIN_PAIRS:
        raise HistoryError(
            f"history too short for a train/test split ({len(train)} train, {len(test)} test pairs; "
            f"need {MIN_PAIRS} each)"
        )
    return train, test, split_t


def _bootstrap_reduction(test: Pairs, pred: np.ndarray, n_boot: int = 500, seed: int = 0) -> tuple[float, float]:
    """90 % interval of the RMS reduction (%), resampling whole test baselines (a baseline's pairs are correlated)."""
    rng = np.random.default_rng(seed)
    groups = {b: np.flatnonzero(test.base_idx == b) for b in np.unique(test.base_idx)}
    keys = list(groups)
    err_u = np.linalg.norm(test.res_ric, axis=1)
    err_c = np.linalg.norm(test.res_ric - pred, axis=1)
    out = []
    for _ in range(n_boot):
        idx = np.concatenate([groups[k] for k in rng.choice(keys, size=len(keys), replace=True)])
        out.append((1.0 - _rms(err_c[idx]) / _rms(err_u[idx])) * 100.0)
    lo, hi = np.percentile(out, [5, 95])
    return float(lo), float(hi)


def evaluate(snaps: list[Snapshot], kinds: tuple[str, ...] = ("full", "drift_only")) -> dict[str, Any]:
    pairs = build_pairs(snaps)
    train, test, split_t = split_pairs(pairs, snaps)
    err_u = np.linalg.norm(test.res_ric, axis=1)
    result: dict[str, Any] = {
        "snapshots": len(snaps),
        "history_days": round((snaps[-1].t_unix - snaps[0].t_unix) / 86400.0, 2),
        "suspected_manoeuvres_epochs_unix": [round(t) for t in detect_jumps(snaps)],
        "pairs_train": len(train),
        "pairs_test": len(test),
        "split_time_utc": datetime.fromtimestamp(split_t, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uncorrected": {
            "rms_km": round(_rms(err_u), 3),
            "max_km": round(float(err_u.max()), 3),
            "intrack_rms_km": round(_rms(test.res_ric[:, 1]), 3),
        },
        "models": {},
    }
    for kind in kinds:
        model = ResidualModel(kind).fit(train)
        pred = model.predict(test)
        err_c = np.linalg.norm(test.res_ric - pred, axis=1)
        lo, hi = _bootstrap_reduction(test, pred)
        reduction = (1.0 - _rms(err_c) / _rms(err_u)) * 100.0
        bins = []
        for a, b in zip(AGE_BINS_D[:-1], AGE_BINS_D[1:], strict=True):
            m = (test.age_d > a) & (test.age_d <= b)
            if m.sum():
                bins.append(
                    {
                        "age_days": [a, b],
                        "n": int(m.sum()),
                        "rms_uncorrected_km": round(_rms(err_u[m]), 3),
                        "rms_corrected_km": round(_rms(err_c[m]), 3),
                    }
                )
        result["models"][kind] = {
            "rms_corrected_km": round(_rms(err_c), 3),
            "intrack_rms_corrected_km": round(_rms(test.res_ric[:, 1] - pred[:, 1]), 3),
            "rms_reduction_pct": round(reduction, 1),
            "rms_reduction_pct_90ci": [round(lo, 1), round(hi, 1)],
            "verdict": "improvement (90% CI above zero)"
            if lo > 0
            else "no significant improvement (90% CI includes zero)",
            "by_age": bins,
        }
    result["caveats"] = [
        "truth proxy is a later element set at its own epoch, not ground truth (its ~1 km accuracy is a floor)",
        "one object (as fetched); results do not transfer to other orbits",
        "pairs spanning suspected manoeuvres are excluded; a smooth model cannot predict a burn",
        "strict temporal split at the median epoch; bootstrap resamples whole test baselines",
    ]
    return result


def plot_results(result: dict[str, Any], out: Path) -> Path:
    import os
    import tempfile

    os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=140)
    any_kind = next(iter(result["models"].values()))
    mids = [np.mean(b["age_days"]) for b in any_kind["by_age"]]
    ax.plot(
        mids, [b["rms_uncorrected_km"] for b in any_kind["by_age"]], "o-", color="#ef476f", label="SGP4 uncorrected"
    )
    for kind, color in (("drift_only", "#ffd166"), ("full", "#4cc9f0")):
        if kind in result["models"]:
            m = result["models"][kind]
            ax.plot(
                mids,
                [b["rms_corrected_km"] for b in m["by_age"]],
                "o-",
                color=color,
                label=f"{kind} ({m['rms_reduction_pct']}% , 90% CI {m['rms_reduction_pct_90ci']})",
            )
    ax.set_xlabel("element-set age (days)")
    ax.set_ylabel("3D position error RMS (km)")
    ax.set_title(f"SGP4 error vs element-set age, held-out test pairs ({result['data_source']} data)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Evaluate SGP4 error growth and a learned correction on a REAL element-set history"
    )
    ap.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--plot", type=Path, default=DEFAULT_PLOT)
    ap.add_argument("--allow-synthetic", action="store_true", help="plumbing tests only; output is labelled SYNTHETIC")
    args = ap.parse_args(argv)
    try:
        snaps, meta = load_history(args.history, allow_synthetic=args.allow_synthetic)
        res = evaluate(snaps)
    except HistoryError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    res = {
        "data_source": meta["data_source"],
        "history_file": meta["file"],
        "citation": meta["citation"],
        "norad_id": meta["norad_id"],
        **res,
    }
    if meta["data_source"] != "REAL":
        res["_note"] = "SYNTHETIC input: these numbers are NOT accuracy results"
    try:
        plot_rel = plot_results(res, args.plot).relative_to(REPO_ROOT).as_posix()
    except ValueError:
        plot_rel = args.plot.name
    res["plot"] = plot_rel
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    u = res["uncorrected"]
    print(
        f"{res['data_source']} history: {res['snapshots']} element sets over {res['history_days']} d; "
        f"test pairs {res['pairs_test']}"
    )
    print(f"uncorrected: RMS {u['rms_km']} km (in-track {u['intrack_rms_km']} km)")
    for kind, m in res["models"].items():
        print(
            f"{kind:10s}: RMS {m['rms_corrected_km']} km, reduction {m['rms_reduction_pct']}% "
            f"(90% CI {m['rms_reduction_pct_90ci']}) -> {m['verdict']}"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
