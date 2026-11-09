"""Unit tests for QAOA quantum manifest solver (WP-11)."""

from __future__ import annotations

import numpy as np
import pytest

from contracts.validate import validate
from manifest.instance import build_instance
from manifest.solvers.cpsat import solve_cpsat
from manifest.solvers.qaoa import qubo_to_ising, solve_qaoa


def test_qubo_to_ising_mapping():
    """Verify exact algebraic conversion from QUBO to Ising spin Hamiltonian."""
    inst = build_instance(num_tasks=4, seed=42)
    h, J, offset, penalty = qubo_to_ising(inst)

    assert len(h) == 4
    assert J.shape == (4, 4)
    assert penalty > 0

    # For zero bitstring (all Z_i = +1), total energy should be 0.0
    zero_energy = np.sum(h) + np.sum(J) + offset
    assert abs(zero_energy) < 1e-6


def test_solve_qaoa_contract():
    """Verify QAOA output passes strict result contract validation."""
    inst = build_instance(num_tasks=4, seed=42)
    res = solve_qaoa(inst, p=1, seed=42, maxiter=25)

    validate("result", res)
    assert res["solver"] == "qaoa_p1"
    assert inst.is_conflict_free(res["selected"])
    assert res["score"] > 0


def test_solve_qaoa_vs_cpsat():
    """Verify QAOA finds high approximation ratio on 6-qubit instance."""
    inst = build_instance(num_tasks=6, seed=42)
    res_cp = solve_cpsat(inst, seed=42)
    res_q2 = solve_qaoa(inst, p=2, seed=42, maxiter=60)

    validate("result", res_q2)
    assert inst.is_conflict_free(res_q2["selected"])
    # QAOA p=2 should achieve >= 80% of CP-SAT optimum
    approx_ratio = res_q2["score"] / res_cp["score"]
    assert approx_ratio >= 0.80


def test_qaoa_qubit_limit_guard():
    """Verify simulator guards against intractably large qubit counts."""
    inst = build_instance(num_tasks=20, seed=42)
    with pytest.raises(ValueError, match="QAOA simulator limit exceeded"):
        solve_qaoa(inst, p=1)

