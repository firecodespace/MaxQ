"""Launch sites. Coordinates are approximate (about +-0.05 deg) public values for the launch complexes.

``allow_descending`` is the per-site switch for descending-pass windows (HANDOFF WP-02: descending passes
are ``flyable: false`` by default because they require a southerly launch azimuth that range-safety
rules usually forbid, e.g. over populated areas or other countries; set True per site to override).

Owner: Yash (lane: mission/).
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    lat_deg: float  # geodetic, north positive
    lon_deg: float  # east positive
    alt_m: float = 0.0
    allow_descending: bool = False

    def with_descending(self, allow: bool = True) -> Site:
        return replace(self, allow_descending=allow)


SITES: dict[str, Site] = {
    s.key: s
    for s in (
        Site("cape_canaveral", "Cape Canaveral", 28.5619, -80.5772),  # SLC-40
        Site("kourou", "Kourou", 5.2360, -52.7750),  # Guiana Space Centre (ELA-3 area)
        Site("vandenberg", "Vandenberg", 34.6320, -120.6110),  # SLC-4E
        Site("baikonur", "Baikonur", 45.9200, 63.3420),  # Site 1
    )
}


def get_site(key: str) -> Site:
    try:
        return SITES[key]
    except KeyError:
        raise KeyError(f"unknown site {key!r}; known: {sorted(SITES)}") from None


def min_inclination_deg(site: Site | float) -> float:
    """Lowest inclination reachable by a direct (no dogleg) launch = |site latitude|."""
    lat = site.lat_deg if isinstance(site, Site) else float(site)
    return abs(lat)
