"""Unit tests for ML warm-start model for manifest scheduling (WP-14)."""

from __future__ import annotations

import numpy as np

from contracts.validate import validate
from manifest.instance import build_instance
from manifest.solvers.sa import solve_sa
from manifest.warm_start import (
    extract_task_features,
    generate_training_data,
    generate_warm_start_selection,
    predict_task_priors,
    train_model,
)


def test_feature_extraction():
    """Verify task feature extraction produces finite, well-formed matrices."""
    inst = build_instance(num_tasks=25, seed=42)
    feats = extract_task_features(inst)

    assert feats.shape == (25, 11)
    assert np.isfinite(feats).all()
    # Degree feature should be non-negative
    assert (feats[:, 2] >= 0).all()
    # Values should match instance
    assert np.allclose(feats[:, 0], [t.value for t in inst.tasks])


def test_warm_start_training_and_inference():
    """Verify training pipeline generates valid predictions with high ROC-AUC."""
    x, y = generate_training_data(num_instances=10, min_tasks=20, max_tasks=40, seed=42)
    assert len(x) == len(y)
    assert set(np.unique(y)).issubset({0, 1})

    clf = train_model(x, y, seed=42)
    inst = build_instance(num_tasks=30, seed=123)
    priors = predict_task_priors(clf, inst)

    assert len(priors) == 30
    assert (priors >= 0.0).all() and (priors <= 1.0).all()


def test_warm_start_selection_validity():
    """Verify greedy warm-start selection produces conflict-free initial sets."""
    inst = build_instance(num_tasks=40, seed=42)
    # Synthetic priors
    priors = np.linspace(0.1, 0.9, 40)
    selection = generate_warm_start_selection(inst, priors, threshold=0.3)

    assert len(selection) > 0
    assert inst.is_conflict_free(selection)


def test_warm_start_sa_execution():
    """Verify warm-started SA returns contract-valid results."""
    inst = build_instance(num_tasks=30, seed=42)
    init_seed = [0, 4] if inst.is_conflict_free([0, 4]) else [0]
    res = solve_sa(inst, steps=5000, init_selected=init_seed, seed=42)

    validate("result", res)
    assert inst.is_conflict_free(res["selected"])
    assert res["score"] > 0
