"""WP-09 acceptance: clustering and congestion. Owner: Yash.

Synthetic populations check that the method recovers known structure; the committed real output
(data/results/clusters.json) is checked against the catalog's own group labels.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from catalog.cluster import (
    CONGESTION_EDGES_KM,
    FEATURES,
    R_E_KM,
    build_clusters,
    cluster_labels,
    cluster_name,
    congestion_profile,
    feature_matrix,
    regime,
    relabel_by_size,
)
from contracts.validate import ContractError, validate
from mission.phasing import semi_major_axis_from_mean_motion

COMMITTED = Path(__file__).resolve().parents[2] / "data" / "results" / "clusters.json"
EPOCH = "2026-11-09T12:00:00.000000Z"


def alt_of(mm):
    return semi_major_axis_from_mean_motion(mm) / 1000.0 - R_E_KM


def population(seed=0):
    """Starlink-like shell, GEO belt, GPS-like ring, debris cloud, and scattered noise."""
    rng = np.random.default_rng(seed)
    rows, nid = [], 1000

    def add(n, name, group, incl, incl_s, mm, mm_s, ecc, ecc_s):
        nonlocal nid
        for i in range(n):
            m = float(rng.normal(mm, mm_s))
            nid += 1
            rows.append(
                {
                    "norad_id": nid,
                    "name": f"{name}-{i}",
                    "group": group,
                    "incl_deg": float(np.clip(rng.normal(incl, incl_s), 0, 180)),
                    "ecc": float(abs(rng.normal(ecc, ecc_s))),
                    "mean_motion_rev_day": m,
                    "alt_km_approx": alt_of(m),
                    "epoch_utc": EPOCH,
                }
            )

    add(300, "STARLINK", "starlink", 53.0, 0.3, 15.06, 0.04, 1e-4, 5e-5)
    add(60, "TELSTAR", "geo", 0.05, 0.03, 1.0027, 0.0003, 2e-4, 1e-4)
    add(30, "GPS", "gps-ops", 55.0, 0.2, 2.0056, 0.0005, 5e-3, 1e-3)
    add(120, "COSMOS 2251 DEB", "debris", 74.0, 0.4, 14.2, 0.1, 5e-3, 1.5e-3)
    for i in range(12):  # scattered one-offs
        m = float(rng.uniform(3, 16))
        nid += 1
        rows.append(
            {
                "norad_id": nid,
                "name": f"ODD-{i}",
                "group": "active",
                "incl_deg": float(rng.uniform(5, 170)),
                "ecc": float(rng.uniform(0.0, 0.7)),
                "mean_motion_rev_day": m,
                "alt_km_approx": alt_of(m),
                "epoch_utc": EPOCH,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def df():
    return population()


@pytest.fixture(scope="module")
def result(df):
    return build_clusters(df, "synthetic")


# ------------------------------------------------------------------ the method recovers known structure


def test_recovers_the_four_known_structures(result):
    assert result["metrics"]["clusters"] == 4
    names = {c["label"]: c["name"] for c in result["clusters"]}
    kg = result["metrics"]["known_groups"]
    for group, expect in (("starlink", "Starlink"), ("geo", "GEO belt"), ("gps-ops", "GPS"), ("debris", "Cosmos")):
        assert kg[group]["share_in_best_cluster"] >= 0.95, group
        assert names[kg[group]["best_cluster"]].startswith(expect), (group, names[kg[group]["best_cluster"]])
    assert result["metrics"]["weighted_cluster_purity_vs_group"] >= 0.97
    assert 0 < result["metrics"]["noise_fraction"] < 0.1  # scattered one-offs are noise, not forced into clusters


def test_labels_are_sorted_by_size_and_noise_is_minus_one(result):
    real = [c for c in result["clusters"] if c["label"] != -1]
    assert [c["label"] for c in real] == list(range(len(real)))
    assert [c["count"] for c in real] == sorted((c["count"] for c in real), reverse=True)
    assert any(c["label"] == -1 and c["name"] == "Unclustered (noise)" for c in result["clusters"])
    assert sum(c["count"] for c in result["clusters"]) == len(result["points"])


def test_scaling_is_physical_two_degrees_one_unit():
    """Equal density 1.5 deg apart merges into one cluster; 10 deg apart stays two clusters."""
    rng = np.random.default_rng(3)

    def blob(incl):
        n = 40
        return np.column_stack(
            [rng.normal(incl, 0.2, n), np.log(rng.normal(15.0, 0.01, n)), np.abs(rng.normal(1e-4, 1e-5, n))]
        )

    near = np.vstack([blob(50.0), blob(51.5)])
    far = np.vstack([blob(50.0), blob(60.0)])
    assert len(set(cluster_labels(near)) - {-1}) == 1
    assert len(set(cluster_labels(far)) - {-1}) == 2


def test_deterministic_and_independent_of_input_order(df):
    assert build_clusters(df, "x") == build_clusters(df.sample(frac=1.0, random_state=9), "x")


def test_relabel_by_size():
    assert relabel_by_size(np.array([5, 5, 5, 2, 2, -1, 9])).tolist() == [0, 0, 0, 1, 1, -1, 2]


def test_feature_matrix_units(df):
    x = feature_matrix(df)
    assert x.shape == (len(df), 3) and FEATURES == ["incl_deg", "log_mean_motion", "ecc"]
    np.testing.assert_allclose(x[:, 1], np.log(df["mean_motion_rev_day"]))


# ------------------------------------------------------------------ naming


@pytest.mark.parametrize(
    ("alt", "incl", "want"),
    [
        (35786, 0.05, "GEO belt"),
        (36150, 3, "GEO belt"),
        (35786, 30, "High orbit"),
        (20200, 55, "MEO"),
        (550, 97.6, "Sun-synchronous LEO"),
        (550, 53, "LEO"),
        (800, 86, "LEO"),
    ],
)
def test_regime(alt, incl, want):
    assert regime(alt, incl) == want


def test_cluster_name_uses_data_not_a_lookup():
    sub = pd.DataFrame(
        {"name": ["ONEWEB-0012"] * 8 + ["X"] * 2, "alt_km_approx": [1186.0] * 10, "incl_deg": [87.9] * 10}
    )
    assert cluster_name(sub) == "Oneweb · 88° · 1,186 km"
    mixed = pd.DataFrame(
        {"name": ["AAA 1", "BBB 2", "CCC 3", "DDD 4"], "alt_km_approx": [531.0] * 4, "incl_deg": [97.6] * 4}
    )
    assert cluster_name(mixed) == "Sun-synchronous LEO · 98° · 531 km"
    gps = pd.DataFrame({"name": ["GPS BIIR-2 (PRN 13)"] * 4, "alt_km_approx": [20182.0] * 4, "incl_deg": [55.0] * 4})
    assert cluster_name(gps).startswith("GPS ·")  # acronyms stay upper-case


# ------------------------------------------------------------------ congestion


def test_congestion_counts_and_density_formula():
    df = pd.DataFrame({"alt_km_approx": [551.0] * 100 + [1203.0] * 30 + [100.0] * 5 + [30000.0] * 7})
    cg = congestion_profile(df)
    e = np.array(cg["altitude_edges_km"])
    assert len(e) == len(cg["counts"]) + 1 == len(cg["density_per_million_km3"]) + 1
    assert e[0] == 150 and e[-1] == 2150 and np.allclose(np.diff(e), 25)
    i550, i1200 = int(np.searchsorted(e, 551.0) - 1), int(np.searchsorted(e, 1203.0) - 1)
    assert cg["counts"][i550] == 100 and cg["counts"][i1200] == 30
    assert sum(cg["counts"]) == 130  # objects outside 150-2150 km are excluded
    r = R_E_KM + (e[i550] + e[i550 + 1]) / 2
    assert cg["density_per_million_km3"][i550] == pytest.approx(100 / (4 * math.pi * r * r * 25) * 1e6, rel=1e-3)
    assert len(CONGESTION_EDGES_KM) == 81


def test_contract_rejects_inconsistent_congestion(result):
    bad = copy.deepcopy(result)
    bad["congestion"]["counts"].pop()
    with pytest.raises(ContractError, match="congestion"):
        validate("clusters", bad)
    bad = copy.deepcopy(result)
    bad["congestion"]["altitude_edges_km"][3] = bad["congestion"]["altitude_edges_km"][2]
    with pytest.raises(ContractError, match="strictly increasing"):
        validate("clusters", bad)


def test_output_validates_and_has_optional_fields(result):
    validate("clusters", result)
    assert result["embedding"]["y"].startswith("inclination") and result["metrics"]["algorithm"] == "DBSCAN"
    assert all(p["cluster"] >= -1 for p in result["points"])


# ------------------------------------------------------------------ the committed real output


@pytest.fixture(scope="module")
def real():
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


@pytest.mark.skipif(
    not COMMITTED.exists(), reason="run `python -m catalog.cluster` to create data/results/clusters.json"
)
class TestCommittedRealClusters:
    def test_valid_and_consistent(self, real):
        validate("clusters", real)
        m = real["metrics"]
        assert m["catalog_mode"] == "full-catalog" and m["objects"] == len(real["points"]) > 10000
        assert sum(c["count"] for c in real["clusters"]) == len(real["points"])
        assert len({p["norad_id"] for p in real["points"]}) == len(real["points"])

    def test_reasonable_number_of_clusters_and_noise(self, real):
        assert 12 <= real["metrics"]["clusters"] <= 60 and real["metrics"]["noise_fraction"] < 0.05

    def test_known_groups_separate(self, real):
        kg = real["metrics"]["known_groups"]
        assert kg["geo"]["share_in_best_cluster"] >= 0.9 and kg["geo"]["best_cluster_name"] == "GEO belt"
        assert kg["gps-ops"]["share_in_best_cluster"] >= 0.9
        assert real["metrics"]["weighted_cluster_purity_vs_group"] >= 0.7

    def test_expected_physical_structures_present(self, real):
        assert "GEO belt" in {c["name"] for c in real["clusters"]}
        starlink = [c for c in real["clusters"] if c["name"].startswith("Starlink")]
        assert len(starlink) >= 3 and sum(c["count"] for c in starlink) > 8000  # several shells
        assert any(abs(c["incl_deg_median"] - 53) < 1 for c in starlink)
        gps = [c for c in real["clusters"] if 19000 < c["alt_km_median"] < 21500 and 54 < c["incl_deg_median"] < 56]
        assert gps, "GPS-like MEO ring"

    def test_congestion_peaks_in_the_known_leo_shells(self, real):
        cg = real["congestion"]
        e = np.array(cg["altitude_edges_km"])
        peak = int(np.argmax(cg["density_per_million_km3"]))
        assert 300 <= e[peak] <= 700  # Starlink-era LEO is the most crowded region by volume density
        assert sum(cg["counts"]) > 15000
