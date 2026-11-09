"""MaxQ manifest instance data structures and realistic instance generation.

Conforms to contracts/manifest_instance.schema.json.
Produces QUBO matrix formulation for SA and QAOA.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Task:
    id: int
    mission: str
    pad: str
    start_unix: int
    end_unix: int
    value: float
    window_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "mission": self.mission,
            "pad": self.pad,
            "start_unix": self.start_unix,
            "end_unix": self.end_unix,
            "value": round(float(self.value), 3),
        }
        if self.window_id is not None:
            d["window_id"] = str(self.window_id)
        return d


@dataclass
class ManifestInstance:
    id: str
    pads: list[str]
    missions: list[str]
    tasks: list[Task]
    conflicts: list[list[int]]  # pairs [i, j] with i < j
    schema_version: int = 1
    _note: str = "MaxQ manifest scheduling instance"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "_note": self._note,
            "id": self.id,
            "pads": list(self.pads),
            "missions": list(self.missions),
            "tasks": [t.to_dict() for t in self.tasks],
            "conflicts": [[int(a), int(b)] for a, b in self.conflicts],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(self.to_json())
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ManifestInstance:
        tasks = [
            Task(
                id=t["id"],
                mission=t["mission"],
                pad=t["pad"],
                start_unix=t["start_unix"],
                end_unix=t["end_unix"],
                value=t["value"],
                window_id=t.get("window_id"),
            )
            for t in d["tasks"]
        ]
        return cls(
            id=d["id"],
            pads=list(d["pads"]),
            missions=list(d["missions"]),
            tasks=tasks,
            conflicts=[list(c) for c in d["conflicts"]],
            schema_version=d.get("schema_version", 1),
            _note=d.get("_note", "MaxQ manifest scheduling instance"),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> ManifestInstance:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls.from_dict(d)

    def is_conflict_free(self, selected: list[int] | set[int]) -> bool:
        """Check if a selected subset of tasks is strictly conflict-free."""
        sel = set(selected)
        for u, v in self.conflicts:
            if u in sel and v in sel:
                return False
        return True

    def to_qubo(self, penalty: float | None = None) -> tuple[np.ndarray, float]:
        """Convert MWIS formulation to QUBO cost matrix Q (upper-triangular).

        Minimization objective:
            E(x) = x^T Q x
            where x in {0, 1}^N.
            Q_ii = -value_i
            Q_ij = penalty for all (i, j) in conflicts, i < j.
        Penalty must exceed max(value_i + value_j) to strictly forbid conflicts.
        """
        n = len(self.tasks)
        q = np.zeros((n, n), dtype=np.float64)
        values = np.array([t.value for t in self.tasks], dtype=np.float64)

        if penalty is None:
            max_val = float(np.max(values)) if n > 0 else 10.0
            penalty = max_val * 2.5 + 1.0

        for i, t in enumerate(self.tasks):
            q[i, i] = -t.value

        for u, v in self.conflicts:
            i, j = min(u, v), max(u, v)
            q[i, j] += penalty

        return q, float(penalty)


def _compute_file_hash(path: Path) -> str:
    """Compute first 8 characters of SHA256 hex digest for file provenance."""
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
        return h.hexdigest()[:8]
    except Exception:
        return "unknown"


def _load_available_windows() -> tuple[list[dict[str, Any]], list[str]]:
    """Load real windows from data/results or fallback to data/dummy, tracking provenance hashes."""
    results_dir = REPO_ROOT / "data" / "results"
    dummy_file = REPO_ROOT / "data" / "dummy" / "windows_dummy.json"

    window_files = sorted(results_dir.glob("windows_*.json"), key=lambda p: p.name)
    raw_windows: list[dict[str, Any]] = []
    file_hashes: list[str] = []

    if window_files:
        for p in window_files:
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                    profile = d.get("profile", "cargo_iss")
                    site_dict = d.get("site", {})
                    site_name = (
                        site_dict.get("name", "Cape Canaveral")
                        if isinstance(site_dict, dict)
                        else "Cape Canaveral"
                    )
                    f_hash = _compute_file_hash(p)
                    file_hashes.append(f"{p.name}:{f_hash}")
                    for w in d.get("windows", []):
                        w_copy = dict(w)
                        w_copy["profile"] = profile
                        w_copy["site_name"] = site_name
                        raw_windows.append(w_copy)
            except Exception:
                continue

    if not raw_windows and dummy_file.exists():
        with open(dummy_file, encoding="utf-8") as f:
            d = json.load(f)
            profile = d.get("profile", "cargo_iss")
            site_dict = d.get("site", {})
            site_name = (
                site_dict.get("name", "Cape Canaveral")
                if isinstance(site_dict, dict)
                else "Cape Canaveral"
            )
            f_hash = _compute_file_hash(dummy_file)
            file_hashes.append(f"{dummy_file.name}:{f_hash}")
            for w in d.get("windows", []):
                w_copy = dict(w)
                w_copy["profile"] = profile
                w_copy["site_name"] = site_name
                raw_windows.append(w_copy)

    return raw_windows, file_hashes


def _load_risk_screening() -> tuple[dict[Any, dict[str, Any]], list[str]]:
    """Load risk screening results mapped by (profile, window_id) and window_id, tracking hashes."""
    results_dir = REPO_ROOT / "data" / "results"
    dummy_file = REPO_ROOT / "data" / "dummy" / "risk_dummy.json"

    risk_files = sorted(results_dir.glob("risk_*.json"), key=lambda p: p.name)
    lookup: dict[Any, dict[str, Any]] = {}
    file_hashes: list[str] = []

    if risk_files:
        for p in risk_files:
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                    meta = d.get("metadata", {})
                    profile = meta.get("windows_profile", "cargo_iss")
                    f_hash = _compute_file_hash(p)
                    file_hashes.append(f"{p.name}:{f_hash}")
                    for r in d.get("results", []):
                        wid = r.get("window_id")
                        if wid:
                            lookup[(profile, wid)] = r
                            if wid not in lookup:
                                lookup[wid] = r
            except Exception:
                continue

    if not lookup and dummy_file.exists():
        with open(dummy_file, encoding="utf-8") as f:
            d = json.load(f)
            meta = d.get("metadata", {})
            profile = meta.get("windows_profile", "cargo_iss")
            f_hash = _compute_file_hash(dummy_file)
            file_hashes.append(f"{dummy_file.name}:{f_hash}")
            for r in d.get("results", []):
                wid = r.get("window_id")
                if wid:
                    lookup[(profile, wid)] = r
                    if wid not in lookup:
                        lookup[wid] = r

    return lookup, file_hashes


def build_instance(
    num_tasks: int,
    seed: int = 42,
    instance_id: str | None = None,
    pad_turnaround_hours: float = 36.0,
    target_separation_hours: float = 12.0,
) -> ManifestInstance:
    """Build a realistic scheduling instance with physical pad and target conflicts.

    Args:
        num_tasks: Number of candidate tasks to generate (~12, ~50, ~200, ~1000).
        seed: Random seed for deterministic generation.
        instance_id: Optional ID; defaults to 'manifest_<N>_tasks'.
        pad_turnaround_hours: Minimum hours required between launches on the same pad.
        target_separation_hours: Minimum hours between arrivals to the same target (ISS).
    """
    rng = random.Random(seed)
    raw_windows, window_hashes = _load_available_windows()
    risk_map, risk_hashes = _load_risk_screening()

    # Launch sites and pads
    pads = ["LC-39A", "SLC-40", "SLC-41", "VAFB-SLC-4E", "LC-36"]
    if num_tasks <= 15:
        active_pads = pads[:2]
    elif num_tasks <= 80:
        active_pads = pads[:3]
    elif num_tasks <= 300:
        active_pads = pads[:4]
    else:
        active_pads = pads

    missions = ["crew_iss", "cargo_iss", "geo_comm", "meo_gps"]

    # Site-specific and mission-specific allowed pads
    cape_pads = [p for p in active_pads if p in ("LC-39A", "SLC-40", "SLC-41", "LC-36")]
    vafb_pads = [p for p in active_pads if p == "VAFB-SLC-4E"]
    mission_pads_map: dict[str, list[str]] = {
        "crew_iss": [p for p in active_pads if p in ("LC-39A", "SLC-41")],
        "cargo_iss": [p for p in active_pads if p in ("LC-39A", "SLC-40", "SLC-41")],
        "geo_comm": [p for p in active_pads if p in ("SLC-40", "SLC-41", "LC-36")],
        "meo_gps": [p for p in active_pads if p in ("SLC-40", "SLC-41", "LC-36")],
    }

    # Base values reflecting real launch priorities
    mission_base_values = {
        "crew_iss": 12.0,
        "cargo_iss": 9.0,
        "geo_comm": 8.0,
        "meo_gps": 7.5,
    }

    # Reference epoch (matches windows_dummy / real windows)
    base_epoch = 1794267690  # ~2026-11-09T23:41:30Z

    tasks: list[Task] = []
    pad_turnaround_s = int(pad_turnaround_hours * 3600)
    target_sep_s = int(target_separation_hours * 3600)

    # Spread candidate launches across time proportional to num_tasks
    total_days = max(7, int(num_tasks / 2.5))
    day_span_s = total_days * 86400

    # Max available real window slots across 3 repeating cycles
    max_real_tasks = min(num_tasks, 147)

    # Build tasks
    for tid in range(num_tasks):
        if raw_windows and tid < max_real_tasks:
            w = raw_windows[tid % len(raw_windows)]
            window_id = f"{w.get('id', f'w{tid}')}_t{tid}"
            cycle_offset = (tid // len(raw_windows)) * (7 * 86400)
            t_launch = int(w.get("t_launch_unix", base_epoch)) + cycle_offset
            mission = str(w.get("profile", "cargo_iss"))
            site_name = str(w.get("site_name", "Cape Canaveral"))

            # Determine pad consistent with window site and mission
            allowed = [
                p for p in mission_pads_map.get(mission, active_pads)
                if p in (cape_pads if site_name == "Cape Canaveral" else vafb_pads)
            ]
            pad = rng.choice(allowed or cape_pads or active_pads)

            fast_rendezvous = bool(w.get("rendezvous_hours_fast") is not None)
            is_flyable = bool(w.get("flyable", True))

            orig_wid = w.get("id")
            risk_entry = risk_map.get((mission, orig_wid)) or risk_map.get(orig_wid)
            risk_status = risk_entry.get("status", "go") if risk_entry else "go"
        else:
            # Synthetic candidate opportunity within scheduling horizon
            time_offset = rng.randint(0, day_span_s)
            t_launch = base_epoch + time_offset
            window_id = f"syn_w{tid}"
            mission = rng.choice(missions)
            allowed = mission_pads_map.get(mission, active_pads)
            pad = rng.choice(allowed or active_pads)
            fast_rendezvous = rng.random() < 0.25
            is_flyable = rng.random() > 0.08
            risk_status = "go" if rng.random() > 0.1 else ("review" if rng.random() > 0.3 else "no-go")

        # Opportunity duration (typically 30-120 minutes)
        duration_s = rng.randint(1800, 7200)
        start_unix = t_launch
        end_unix = t_launch + duration_s

        # Compute task priority value
        val = mission_base_values.get(mission, 7.0)
        if fast_rendezvous:
            val += 2.0  # Fast rendezvous bonus
        if not is_flyable:
            val *= 0.25  # Heavily discount non-flyable geometry
        if risk_status == "review":
            val *= 0.85  # Review penalty
        elif risk_status == "no-go":
            val *= 0.30  # High risk collision discount (discounted x0.30, not excluded)

        # Add small unique noise so values are continuous
        val += rng.uniform(-0.4, 0.4)
        val = max(1.0, round(val, 2))

        tasks.append(
            Task(
                id=tid,
                mission=mission,
                pad=pad,
                start_unix=start_unix,
                end_unix=end_unix,
                value=val,
                window_id=window_id,
            )
        )

    # Sort tasks by start time for consistent evaluation
    tasks.sort(key=lambda t: t.start_unix)
    # Re-index ids sequentially 0..N-1 so contract constraints are clean
    reindexed_tasks = [
        Task(
            id=new_id,
            mission=t.mission,
            pad=t.pad,
            start_unix=t.start_unix,
            end_unix=t.end_unix,
            value=t.value,
            window_id=t.window_id,
        )
        for new_id, t in enumerate(tasks)
    ]

    # Detect conflicts
    # Conflict 1: Same pad and turnaround time overlap: |start_i - start_j| < pad_turnaround_s
    # Conflict 2: Same target (ISS docking missions: crew_iss and cargo_iss) within target_sep_s
    conflicts_set: set[tuple[int, int]] = set()

    for i in range(len(reindexed_tasks)):
        t_i = reindexed_tasks[i]
        for j in range(i + 1, len(reindexed_tasks)):
            t_j = reindexed_tasks[j]

            time_diff = abs(t_j.start_unix - t_i.start_unix)

            # Pad conflict
            if t_i.pad == t_j.pad and time_diff < pad_turnaround_s:
                conflicts_set.add((i, j))
                continue

            # ISS berthing / docking port conflict
            if (
                t_i.mission in ("crew_iss", "cargo_iss")
                and t_j.mission in ("crew_iss", "cargo_iss")
                and time_diff < target_sep_s
            ):
                conflicts_set.add((i, j))
                continue

            # Direct window overlap on any pad (e.g. range tracking capacity)
            if max(t_i.start_unix, t_j.start_unix) < min(t_i.end_unix, t_j.end_unix):
                conflicts_set.add((i, j))

    conflicts = sorted([[u, v] for u, v in conflicts_set])
    inst_id = instance_id or f"manifest_{num_tasks}_tasks"

    n_syn = sum(1 for t in reindexed_tasks if str(t.window_id or "").startswith("syn_"))
    n_real = len(reindexed_tasks) - n_syn
    syn_pct = (n_syn / len(reindexed_tasks)) if reindexed_tasks else 0.0
    inputs_summary = ", ".join(sorted(window_hashes + risk_hashes))

    _note = (
        f"Realistic launch manifest with {num_tasks} tasks "
        f"({n_real} from real windows, {n_syn} synthetic, {syn_pct:.0%}) "
        f"and {len(conflicts)} physical conflicts. "
        f"Inputs: {inputs_summary}. "
        f"Note: no-go risk windows are discounted (x0.30 value) rather than excluded."
    )

    return ManifestInstance(
        id=inst_id,
        pads=active_pads,
        missions=missions,
        tasks=reindexed_tasks,
        conflicts=conflicts,
        schema_version=1,
        _note=_note,
    )
