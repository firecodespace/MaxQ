"""MaxQ Manifest Scheduling Package.

Implements launch manifest optimization using Greedy heuristics,
Simulated Annealing (QUBO), and OR-Tools CP-SAT.
"""

from manifest.instance import ManifestInstance, Task, build_instance
from manifest.solvers import solve_manifest

__all__ = ["ManifestInstance", "Task", "build_instance", "solve_manifest"]

