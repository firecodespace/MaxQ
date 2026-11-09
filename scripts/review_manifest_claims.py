# ruff: noqa: E501  (long report lines are intentional in this diagnostic script)
"""Reproduce the three evidence checks behind docs/reviews/shiva-wp10-11-14.md. Read-only: writes nothing.

    python scripts/review_manifest_claims.py

1. How much of each committed manifest instance is built from real windows vs synthetic?
2. QAOA control: best feasible of N uniformly random bitstrings vs the committed QAOA result (12-task instance).
3. WP-14: ROC-AUC with the shipped row-level split vs an instance-level split, vs ranking by task value alone.

Owner: Yash (lane: scripts/).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
RESULTS = REPO_ROOT / "data" / "results"


def check_instances() -> None:
    print("1. real vs synthetic tasks in the committed instances")
    for n in (12, 50, 200, 1000):
        inst = json.loads((RESULTS / f"manifest_instance_{n}.json").read_text(encoding="utf-8"))
        wid = [t.get("window_id", "") for t in inst["tasks"]]
        syn = sum(w.startswith("syn_") for w in wid)
        print(
            f"   {n:>4} tasks: {len(wid) - syn} from real windows, {syn} synthetic ({syn / len(wid):.0%}); note: {inst['_note']!r}"
        )


def check_qaoa_control(shots: int = 1000, trials: int = 200) -> None:
    from manifest.instance import ManifestInstance

    inst = ManifestInstance.from_dict(json.loads((RESULTS / "manifest_instance_12.json").read_text(encoding="utf-8")))
    vals = np.array([t.value for t in inst.tasks])
    n = len(vals)
    conf = np.zeros((n, n), bool)
    for a, b in inst.conflicts:
        conf[a, b] = conf[b, a] = True
    opt = json.loads((RESULTS / "result_manifest_12_tasks_cpsat.json").read_text(encoding="utf-8"))["score"]
    q = {
        p: json.loads((RESULTS / f"result_manifest_12_tasks_qaoa_{p}.json").read_text(encoding="utf-8"))["score"]
        for p in ("p1", "p2")
    }
    rng = np.random.default_rng(0)
    ratios = []
    for _ in range(trials):
        best = 0.0
        for _ in range(shots):
            idx = np.flatnonzero(rng.integers(0, 2, n).astype(bool))
            if len(idx) and not conf[np.ix_(idx, idx)].any():
                best = max(best, float(vals[idx].sum()))
        ratios.append(best / opt)
    r = np.array(ratios)
    print("2. QAOA vs a random-sampling control (12-task instance)")
    print(
        f"   CP-SAT optimum {opt:.2f}; QAOA p=1 {q['p1'] / opt:.1%}, p=2 {q['p2'] / opt:.1%} (best feasible of its measurement shots)"
    )
    print(
        f"   uniform random bitstrings, best feasible of {shots}: mean {r.mean():.1%}, min {r.min():.1%}, optimal in {np.mean(r >= 0.9999):.0%} of trials"
    )


def check_warm_start_split() -> None:
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import GroupShuffleSplit, train_test_split

    from manifest.instance import build_instance
    from manifest.solvers.cpsat import solve_cpsat
    from manifest.warm_start import FEATURE_NAMES, extract_task_features, train_model

    rng = np.random.default_rng(42)
    xs, ys, gs = [], [], []
    for idx in range(22):  # same recipe as warm_start.run_warm_start_experiment
        s, n = int(rng.integers(1, 100000)), int(rng.integers(35, 66))
        inst = build_instance(num_tasks=n, seed=s, instance_id=f"train_{idx}")
        sel = set(solve_cpsat(inst, time_limit_s=15.0, seed=s)["selected"])
        xs.append(extract_task_features(inst))
        ys.append(np.array([1 if i in sel else 0 for i in range(n)]))
        gs.append(np.full(n, idx))
    x, y, g = np.vstack(xs), np.concatenate(ys), np.concatenate(gs)
    xa, xb, ya, yb = train_test_split(x, y, test_size=0.25, random_state=42, stratify=y)
    p = train_model(xa, ya, seed=42).predict_proba(xb)[:, 1]
    print("3. WP-14 model quality")
    print(
        f"   row-level split (as shipped):       ROC-AUC {roc_auc_score(yb, p):.3f}, PR-AUC {average_precision_score(yb, p):.3f}"
    )
    res = []
    for seed in range(8):
        tr, te = next(GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=seed).split(x, y, g))
        p = train_model(x[tr], y[tr], seed=42).predict_proba(x[te])[:, 1]
        res.append((roc_auc_score(y[te], p), average_precision_score(y[te], p)))
    res = np.array(res)
    print(
        f"   instance-level split (8 partitions): ROC-AUC {res[:, 0].mean():.3f} (min {res[:, 0].min():.3f}, max {res[:, 0].max():.3f}), PR-AUC {res[:, 1].mean():.3f}"
    )
    print(f"   baseline, rank by task value alone:  ROC-AUC {roc_auc_score(y, x[:, FEATURE_NAMES.index('value')]):.3f}")


if __name__ == "__main__":
    check_instances()
    check_qaoa_control()
    check_warm_start_split()
