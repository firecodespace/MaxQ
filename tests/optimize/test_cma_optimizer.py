"""Real CMA-ES: proof that it is real, and that it beats the baseline it replaced.

A rename is cheap. These tests exist so that "this is CMA-ES" is a measurement rather than a claim,
and so that the next person to touch `optimize/` cannot quietly swap the algorithm back out:

  1. sigma and the covariance matrix visibly change over generations (the baseline's cannot);
  2. on a rotated, ill-conditioned 6-D quadratic, CMA-ES converges where fixed-sigma stalls;
  3. the same seed gives the same result.

The comparison runs the *real* functions from `optimize.search_core`, the same ones the mission
path calls, rather than reimplementations of them here. A test against a copy would prove nothing
about what actually runs.

Owner: Yash (lane: optimize/, by Yash's direction -- see status/yash.md).
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from contracts.validate import validate, validate_session_file
from optimize.cma_optimizer import AGENT_ID, optimize_mission
from optimize.search_core import cma_minimize, fixed_sigma_minimize

# ---------------------------------------------------------------------------------------------
# A synthetic landscape with the property that defeats a non-adaptive search.
# ---------------------------------------------------------------------------------------------

DIM = 6
#: Condition number 1e6: the valley is a million times narrower in one direction than another.
CONDITION = 1.0e6


def _rotation(dim: int, seed: int) -> np.ndarray:
    """A fixed random orthonormal basis, so the valley does not lie along the coordinate axes."""
    q, r = np.linalg.qr(np.random.default_rng(seed).normal(size=(dim, dim)))
    return q * np.sign(np.diag(r))


ROT = _rotation(DIM, seed=7)
SCALE = np.array([CONDITION ** (i / (DIM - 1)) for i in range(DIM)])


def rotated_ill_conditioned_quadratic(x: np.ndarray) -> float:
    """The standard CMA-ES benchmark: a rotated ellipsoid.

    Axis-aligned scaling alone is not enough -- a search with per-coordinate step sizes could cope
    with that. Rotating it means the only way to make progress is to learn the *covariance*, which
    is exactly the capability that separates CMA-ES from the fixed-sigma baseline.
    """
    y = ROT @ np.asarray(x, dtype=float)
    return float(np.sum(SCALE * y**2))


BUDGET = 3000
X0 = [2.0] * DIM
LOWER = [-5.0] * DIM
UPPER = [5.0] * DIM


# ---------------------------------------------------------------------------------------------
# 1. Adaptation is visible
# ---------------------------------------------------------------------------------------------


def test_sigma_and_covariance_adapt_over_generations():
    """If neither the step size nor the covariance moves, it is not CMA-ES."""
    res = cma_minimize(
        rotated_ill_conditioned_quadratic, X0, 1.0, BUDGET, seed=1, lower=LOWER, upper=UPPER
    )
    assert len(res.generations) > 10, "too few generations to say anything about adaptation"

    sigmas = [g["sigma"] for g in res.generations]
    conds = [g["cov_condition"] for g in res.generations]

    # Step size: it must actually move, and on a quadratic it should end up smaller than it began.
    assert len(set(sigmas)) > 1, "sigma never changed: this is a fixed-sigma search"
    assert sigmas[-1] < sigmas[0]

    # Covariance: it starts isotropic (condition 1) and must become anisotropic to track a valley
    # with condition 1e6. This is the part the baseline has no mechanism for at all.
    assert conds[0] == pytest.approx(1.0, abs=0.5)
    assert max(conds) > 100.0, f"covariance stayed near-isotropic (max condition {max(conds):.3g})"


def test_the_baseline_has_no_adaptation_to_report():
    """The honest counterpart: fixed_sigma_minimize reports no generations because it has none."""
    res = fixed_sigma_minimize(
        rotated_ill_conditioned_quadratic,
        X0,
        [1.0] * DIM,
        BUDGET,
        seed=1,
        lower=LOWER,
        upper=UPPER,
    )
    assert res.generations == []


# ---------------------------------------------------------------------------------------------
# 2. It converges where the baseline stalls
# ---------------------------------------------------------------------------------------------


def test_cma_converges_where_fixed_sigma_stalls():
    """Same function, same budget, same seed, same bounds, same start. Only the algorithm differs."""
    cma_res = cma_minimize(
        rotated_ill_conditioned_quadratic, X0, 1.0, BUDGET, seed=1, lower=LOWER, upper=UPPER
    )
    base_res = fixed_sigma_minimize(
        rotated_ill_conditioned_quadratic,
        X0,
        [1.0] * DIM,
        BUDGET,
        seed=1,
        lower=LOWER,
        upper=UPPER,
    )

    # CMA-ES may finish early once its own convergence criteria fire; spending LESS budget and
    # still winning is a stronger result, not a weaker one, so this is a ceiling, not an equality.
    assert cma_res.evaluations <= BUDGET
    assert base_res.evaluations == BUDGET

    # Orders of magnitude, not a few percent. On a condition-1e6 valley the non-adaptive search
    # cannot take a step that is small enough to be safe and large enough to make progress.
    assert cma_res.f_best < base_res.f_best / 1000.0, (
        f"CMA-ES {cma_res.f_best:.6g} is not decisively better than "
        f"fixed-sigma {base_res.f_best:.6g}"
    )


def test_both_searches_agree_on_an_easy_sphere():
    """A guard on the comparison itself: the baseline is not simply broken.

    If fixed-sigma failed every function, the test above would prove nothing. On an isotropic
    sphere -- where there is no covariance to learn -- it must do perfectly respectably.
    """

    def sphere(x: np.ndarray) -> float:
        return float(np.sum(np.asarray(x) ** 2))

    base = fixed_sigma_minimize(
        sphere, X0, [0.3] * DIM, 600, seed=3, lower=LOWER, upper=UPPER
    )
    # An order of magnitude. The baseline is slow, not broken -- which is what makes the
    # ill-conditioned comparison above a statement about the landscape rather than about a bug.
    assert base.f_best < sphere(np.asarray(X0)) / 10.0


# ---------------------------------------------------------------------------------------------
# 3. Determinism
# ---------------------------------------------------------------------------------------------


def test_same_seed_gives_the_same_search():
    a = cma_minimize(
        rotated_ill_conditioned_quadratic, X0, 1.0, 400, seed=11, lower=LOWER, upper=UPPER
    )
    b = cma_minimize(
        rotated_ill_conditioned_quadratic, X0, 1.0, 400, seed=11, lower=LOWER, upper=UPPER
    )
    assert a.f_best == b.f_best
    assert np.array_equal(a.x_best, b.x_best)
    assert [g["sigma"] for g in a.generations] == [g["sigma"] for g in b.generations]


def test_different_seeds_give_different_searches():
    """Determinism must come from the seed, not from the search ignoring it."""
    a = cma_minimize(
        rotated_ill_conditioned_quadratic, X0, 1.0, 400, seed=11, lower=LOWER, upper=UPPER
    )
    b = cma_minimize(
        rotated_ill_conditioned_quadratic, X0, 1.0, 400, seed=12, lower=LOWER, upper=UPPER
    )
    assert a.f_best != b.f_best


def test_the_budget_is_in_evaluations_and_is_never_exceeded():
    """Charter item 9. A generation that would run past the budget is cut short, not rounded up."""
    for budget in (1, 7, 13, 50):
        calls = {"n": 0}

        def counted(x: np.ndarray) -> float:
            calls["n"] += 1
            return rotated_ill_conditioned_quadratic(x)

        res = cma_minimize(counted, X0, 1.0, budget, seed=5, lower=LOWER, upper=UPPER)
        assert calls["n"] == budget, f"budget {budget}: ran {calls['n']} simulations"
        assert res.evaluations == budget


# ---------------------------------------------------------------------------------------------
# The mission path
# ---------------------------------------------------------------------------------------------

REQUEST = {
    "schema_version": 1,
    "id": "msn_test_cma",
    "profile": "cargo_iss",
    "payload": {"mass_kg": 12000.0, "crew": 0},
    "rocket_id": "falcon9_block5",
    "pad_id": "ksc_lc39a",
    "target": {"norad_id": 25544},
    "window_id": "w0",
    "constraints_profile": "cargo_default",
    "objective": {
        "w_fuel": 1.0,
        "w_time": 0.5,
        "fuel_ref_kg": 500000.0,
        "time_ref_s": 86400.0,
    },
    "budget": {"max_attempts": 30, "max_wall_s": 300.0},
    "seed": 42,
}


def _run_mission(tmp_path, attempts=30, seed=42):
    log = tmp_path / "session.jsonl"
    best = optimize_mission(
        mission_request=dict(REQUEST),
        max_attempts=attempts,
        seed=seed,
        session_log_path=log,
        save_attempts=False,
    )
    events = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return best, log, events


def test_mission_run_emits_valid_artifacts(tmp_path):
    best, log, events = _run_mission(tmp_path)
    assert best is not None
    validate("attempt", best)
    validate_session_file(log)
    assert best["agent"]["id"] == AGENT_ID
    finished = next(e for e in events if e["type"] == "mission_finished")
    assert finished["data"]["attempts"] == 30


def test_mission_run_declares_cma_and_shows_its_adaptation(tmp_path):
    """The badge the UI draws comes from this event, so it has to be true here."""
    _, _, events = _run_mission(tmp_path)
    started = next(e for e in events if e["type"] == "mission_started")
    assert started["data"]["search"] == "cma-es"
    assert started["data"]["seed"] == 42

    # Every sample records the distribution it came from, so adaptation is legible attempt by
    # attempt rather than asserted once.
    steps = [e for e in events if e["type"] == "agent_step"]
    assert steps, "the replay carries no agent_step events"
    sigmas = [e["data"]["key_numbers"]["sigma"] for e in steps]
    conds = [e["data"]["key_numbers"]["cov_condition"] for e in steps]
    gens = [e["data"]["key_numbers"]["generation"] for e in steps]
    assert max(gens) >= 2, f"only {max(gens)} generation(s) completed: nothing to adapt over"
    assert len(set(sigmas)) > 1, "sigma never changed across the run: this is not CMA-ES"
    assert len(set(conds)) > 1, "the covariance never changed across the run"


def test_mission_run_still_reports_a_null_benchmark_gap(tmp_path):
    """A better search does not conjure a benchmark into existence (WP2-12)."""
    best, _, events = _run_mission(tmp_path)
    assert best["best_known_gap_pct"] is None
    for e in events:
        if e["type"] == "benchmark_update":
            assert e["data"]["best_known_gap_pct"] is None


def test_mission_run_is_reproducible(tmp_path):
    a, _, _ = _run_mission(tmp_path / "a")
    b, _, _ = _run_mission(tmp_path / "b")
    assert a["cost"] == b["cost"]
    assert a["plan"]["pitch_program"] == b["plan"]["pitch_program"]
