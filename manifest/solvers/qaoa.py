# ruff: noqa: E402
"""Quantum Approximate Optimization Algorithm (QAOA) for launch manifest scheduling.

Maps the launch manifest MWIS QUBO to an Ising spin Hamiltonian:
    x_i = (I - Z_i) / 2
    H_C = sum_i h_i Z_i + sum_{(i,j) in E} J_ij Z_i Z_j + offset * I
Simulates depth p in {1, 2, 3} on a classical state-vector quantum simulator (<= 12 qubits).
Evaluates approximation ratio against the exact CP-SAT optimum.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", tempfile.gettempdir())
import numpy as np
import pennylane as qml
from scipy.optimize import minimize

from manifest.instance import ManifestInstance

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def qubo_to_ising(
    instance: ManifestInstance,
    penalty: float | None = None,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Convert manifest MWIS QUBO to Ising spin Hamiltonian coefficients.

    H_C = sum_i h_i Z_i + sum_{i < j} J_ij Z_i Z_j + offset * I
    where x_i = (1 - Z_i) / 2.

    Returns:
        h: 1D array of single-qubit Z coefficients of length N.
        J: 2D upper-triangular array of ZZ interaction coefficients (N x N).
        offset: Constant energy shift.
        penalty: Penalty weight P used for conflicts.
    """
    n = len(instance.tasks)
    values = np.array([t.value for t in instance.tasks], dtype=np.float64)
    max_val = float(np.max(values)) if n > 0 else 10.0

    if penalty is None:
        penalty = max_val * 2.5 + 2.0

    adj: list[list[int]] = [[] for _ in range(n)]
    for u, v in instance.conflicts:
        adj[u].append(v)
        adj[v].append(u)

    h = np.zeros(n, dtype=np.float64)
    J = np.zeros((n, n), dtype=np.float64)

    for i in range(n):
        h[i] = (values[i] / 2.0) - (len(adj[i]) * penalty / 4.0)

    for u, v in instance.conflicts:
        i, j = min(u, v), max(u, v)
        J[i, j] = penalty / 4.0

    num_conflicts = len(instance.conflicts)
    offset = float(-np.sum(values) / 2.0 + num_conflicts * penalty / 4.0)

    return h, J, offset, float(penalty)


def solve_qaoa(
    instance: ManifestInstance,
    p: int = 1,
    maxiter: int = 40,
    seed: int = 42,
    device_name: str = "default.qubit",
    **kwargs: Any,
) -> dict[str, Any]:
    """Solve manifest scheduling using QAOA on a PennyLane quantum simulator.

    Args:
        instance: ManifestInstance (recommended N <= 12).
        p: Number of alternating QAOA layers (typically 1, 2, or 3).
        maxiter: Maximum classical optimizer iterations (COBYLA).
        seed: Random seed for initialization and repeatability.
        device_name: PennyLane device ('default.qubit' or 'lightning.qubit').

    Returns:
        Contract-compliant dictionary with selected tasks, score, runtime,
        and diagnostic QAOA metrics in a top-level _qaoa_diagnostics dict.
    """
    t0 = time.perf_counter()
    n = len(instance.tasks)
    if n == 0:
        return {
            "schema_version": 1,
            "_note": "QAOA quantum simulator solution",
            "instance": instance.id,
            "solver": "qaoa",
            "selected": [],
            "score": 0.0,
            "runtime_s": round(time.perf_counter() - t0, 6),
            "seed": seed,
        }

    if n > 16:
        raise ValueError(f"QAOA simulator limit exceeded: {n} qubits requested (max 16 for simulation).")

    h, J, offset, penalty = qubo_to_ising(instance)
    wires = list(range(n))
    dev = qml.device(device_name, wires=n)

    # Pre-extract conflict edges for circuit
    edges = [(u, v) for u, v in instance.conflicts]

    @qml.qnode(dev)
    def circuit(params: np.ndarray):
        # Initial uniform superposition
        for w in wires:
            qml.Hadamard(wires=w)

        gammas = params[:p]
        betas = params[p:]

        # p alternating layers
        for layer in range(p):
            g = gammas[layer]
            b = betas[layer]

            # Problem Hamiltonian U_C(gamma)
            for i in wires:
                if abs(h[i]) > 1e-9:
                    qml.RZ(2.0 * g * h[i], wires=i)

            for u, v in edges:
                qml.IsingZZ(2.0 * g * J[min(u, v), max(u, v)], wires=[u, v])

            # Mixer Hamiltonian U_M(beta)
            for w in wires:
                qml.RX(2.0 * b, wires=w)

        return qml.probs(wires=wires)

    # Pre-calculate classical energies for all 2^N basis states for rapid expected-value calculation
    # Basis index k corresponds to binary bitstring: bit_i = (k >> (n - 1 - i)) & 1
    basis_size = 1 << n
    basis_energies = np.zeros(basis_size, dtype=np.float64)
    values = np.array([t.value for t in instance.tasks], dtype=np.float64)

    for k in range(basis_size):
        x = np.array([(k >> (n - 1 - i)) & 1 for i in range(n)], dtype=np.float64)
        cost = -float(np.dot(values, x))
        for u, v in edges:
            if x[u] == 1 and x[v] == 1:
                cost += penalty
        basis_energies[k] = cost

    def cost_fn(params: np.ndarray) -> float:
        probs = circuit(params)
        # Expected energy <H_C> = sum_k probs[k] * E(k)
        return float(np.dot(probs, basis_energies))

    rng = np.random.default_rng(seed)
    # Initial parameters: small angles around pi/4
    init_gammas = rng.uniform(0.1, 0.8, size=p)
    init_betas = rng.uniform(0.2, 0.7, size=p)
    init_params = np.concatenate([init_gammas, init_betas])

    res_opt = minimize(
        cost_fn,
        init_params,
        method="COBYLA",
        options={"maxiter": maxiter, "rhobeg": 0.4},
    )

    opt_params = res_opt.x
    final_probs = circuit(opt_params)

    # Evaluate best sampled bitstrings
    # Sort states by probability descending
    top_indices = np.argsort(final_probs)[::-1]

    # Find the most probable bitstring
    most_probable_idx = int(top_indices[0])
    most_prob_bits = [(most_probable_idx >> (n - 1 - i)) & 1 for i in range(n)]
    most_prob_selected = [i for i in range(n) if most_prob_bits[i] == 1]
    is_most_prob_valid = instance.is_conflict_free(most_prob_selected)

    # Find the best feasible bitstring among high-probability states
    feasible_prob_mass = 0.0

    for idx in range(basis_size):
        bits = [(idx >> (n - 1 - i)) & 1 for i in range(n)]
        cand_selected = [i for i in range(n) if bits[i] == 1]
        if instance.is_conflict_free(cand_selected):
            prob = float(final_probs[idx])
            feasible_prob_mass += prob

    # Practical quantum readout: sample S=1000 measurement shots from the trained state
    shots = int(kwargs.get("shots", 1000))
    sampled_indices = rng.choice(basis_size, size=shots, p=final_probs)

    best_sampled_selected: list[int] = []
    best_sampled_score = 0.0

    for idx in np.unique(sampled_indices):
        bits = [(idx >> (n - 1 - i)) & 1 for i in range(n)]
        cand_selected = [i for i in range(n) if bits[i] == 1]
        if cand_selected and instance.is_conflict_free(cand_selected):
            cand_score = sum(values[i] for i in cand_selected)
            if cand_score > best_sampled_score:
                best_sampled_score = cand_score
                best_sampled_selected = cand_selected

    # Most probable non-empty feasible state (deterministic mode)
    best_prob_non_empty_selected: list[int] = []
    best_prob_val = -1.0
    for idx in range(basis_size):
        bits = [(idx >> (n - 1 - i)) & 1 for i in range(n)]
        cand_selected = [i for i in range(n) if bits[i] == 1]
        if cand_selected and instance.is_conflict_free(cand_selected):
            prob = float(final_probs[idx])
            if prob > best_prob_val:
                best_prob_val = prob
                best_prob_non_empty_selected = cand_selected

    # Final selection: best sampled valid schedule (or deterministic mode)
    if best_sampled_selected:
        final_selected = best_sampled_selected
        final_score = best_sampled_score
    elif best_prob_non_empty_selected:
        final_selected = best_prob_non_empty_selected
        final_score = sum(values[i] for i in final_selected)
    elif is_most_prob_valid:
        final_selected = most_prob_selected
        final_score = sum(values[i] for i in final_selected)
    else:
        final_selected = []
        final_score = 0.0

    runtime_s = max(1e-6, time.perf_counter() - t0)

    contract_result = {
        "schema_version": 1,
        "_note": (
            f"QAOA quantum simulator (p={p}, {n} qubits, "
            f"mass={feasible_prob_mass:.2f}, energy={res_opt.fun:.2f})"
        ),
        "instance": instance.id,
        "solver": f"qaoa_p{p}",
        "selected": sorted(final_selected),
        "score": round(float(final_score), 3),
        "runtime_s": round(runtime_s, 6),
        "seed": seed,
    }

    if kwargs.get("return_diagnostics", False):
        diagnostics = {
            "qubits": n,
            "depth_p": p,
            "shots": shots,
            "opt_expected_energy": round(float(res_opt.fun), 4),
            "feasible_probability_mass": round(feasible_prob_mass, 4),
            "most_probable_bitstring": "".join(str(b) for b in most_prob_bits),
            "is_most_probable_valid": is_most_prob_valid,
            "best_sampled_selected": sorted(best_sampled_selected),
            "nit": int(res_opt.nfev),
        }
        return contract_result, diagnostics

    return contract_result
