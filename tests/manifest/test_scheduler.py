"""Unit tests for MaxQ manifest scheduling package (WP-10)."""

from __future__ import annotations

from contracts.validate import validate
from manifest.instance import ManifestInstance, Task, build_instance
from manifest.solvers import solve_cpsat, solve_greedy, solve_manifest, solve_sa


def test_manifest_instance_contract():
    """Verify generated manifest instances pass strict contract validation."""
    for n in (12, 50):
        inst = build_instance(num_tasks=n, seed=42)
        assert len(inst.tasks) == n
        assert len(inst.conflicts) > 0
        validate("manifest_instance", inst.to_dict())


def test_manifest_conflict_logic():
    """Verify conflict detection and conflict-freedom validator."""
    t0 = Task(id=0, mission="cargo_iss", pad="LC-39A", start_unix=1000, end_unix=2000, value=8.0)
    t1 = Task(id=1, mission="crew_iss", pad="LC-39A", start_unix=3000, end_unix=4000, value=9.0)
    t2 = Task(id=2, mission="geo_comm", pad="SLC-40", start_unix=100000, end_unix=101000, value=7.0)

    # 0 and 1 conflict (same pad, start times 1000 vs 3000, less than 36h turnaround)
    inst = ManifestInstance(
        id="test_toy",
        pads=["LC-39A", "SLC-40"],
        missions=["cargo_iss", "crew_iss", "geo_comm"],
        tasks=[t0, t1, t2],
        conflicts=[[0, 1]],
    )

    validate("manifest_instance", inst.to_dict())
    assert not inst.is_conflict_free([0, 1])
    assert inst.is_conflict_free([0, 2])
    assert inst.is_conflict_free([1, 2])


def test_qubo_conversion():
    """Verify QUBO formulation penalizes conflicting selections."""
    inst = build_instance(num_tasks=12, seed=42)
    q_mat, penalty = inst.to_qubo()

    assert q_mat.shape == (12, 12)
    assert penalty > 0

    # Diagonal should be -value_i
    for i, t in enumerate(inst.tasks):
        assert abs(q_mat[i, i] - (-t.value)) < 1e-6

    # Test an edge in conflicts: picking both u and v should incur penalty
    u, v = inst.conflicts[0]
    i, j = min(u, v), max(u, v)
    assert q_mat[i, j] >= penalty


def test_greedy_solver_validity():
    """Verify greedy solver produces conflict-free and contract-valid schedules."""
    inst = build_instance(num_tasks=12, seed=42)
    res = solve_greedy(inst, seed=42)

    validate("result", res)
    assert inst.is_conflict_free(res["selected"])
    assert res["score"] > 0
    # Score matches sum of selected task values
    val_sum = sum(inst.tasks[i].value for i in res["selected"])
    assert abs(res["score"] - val_sum) < 1e-3


def test_sa_solver_validity_and_determinism():
    """Verify Simulated Annealing produces conflict-free schedules and is deterministic."""
    inst = build_instance(num_tasks=12, seed=42)
    res1 = solve_sa(inst, seed=42)
    res2 = solve_sa(inst, seed=42)

    validate("result", res1)
    assert inst.is_conflict_free(res1["selected"])
    assert res1["score"] > 0

    # Deterministic with fixed seed
    assert res1["selected"] == res2["selected"]
    assert res1["score"] == res2["score"]


def test_cpsat_solver_optimality():
    """Verify CP-SAT finds proven optimal solution matching or exceeding heuristics."""
    inst = build_instance(num_tasks=12, seed=42)
    res_cp = solve_cpsat(inst, seed=42)
    res_gr = solve_greedy(inst, seed=42)
    res_sa = solve_sa(inst, seed=42)

    validate("result", res_cp)
    assert inst.is_conflict_free(res_cp["selected"])
    assert res_cp["score"] >= res_gr["score"] - 1e-3
    assert res_cp["score"] >= res_sa["score"] - 1e-3
    assert len(res_cp["selected"]) > 0


def test_solver_dispatcher():
    """Verify dispatcher function solve_manifest."""
    inst = build_instance(num_tasks=12, seed=42)
    for s_name in ("greedy", "sa", "cpsat"):
        res = solve_manifest(inst, solver=s_name, seed=42)
        assert res["solver"] == s_name
        assert inst.is_conflict_free(res["selected"])

