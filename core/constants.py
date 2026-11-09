"""Physical constants for MaxQ. SI units. Every value carries its source.

Gated file (HANDOFF section 9): changes need Yash's OK.
"""

# Standard gravitational parameter of Earth, m^3/s^2. Source: HANDOFF section 7 (EGM-96/WGS-84 value 3.986004418e14).
MU_M3_S2 = 3.986004418e14

# Earth equatorial radius (WGS-84 semi-major axis), m. Source: NIMA TR8350.2 / WGS-84; HANDOFF 7 (6,378.137 km).
R_E_M = 6_378_137.0

# WGS-84 flattening f = 1/298.257223563. Source: NIMA TR8350.2.
WGS84_F = 1.0 / 298.257223563

# Derived WGS-84 values.
WGS84_B_M = R_E_M * (1.0 - WGS84_F)  # semi-minor axis, m
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)  # first eccentricity squared

# Earth rotation rate, rad/s. Source: HANDOFF section 7 (WGS-84 / IERS value 7.2921159e-5 rad/s).
OMEGA_E_RAD_S = 7.2921159e-5

# Standard gravity, m/s^2. Source: CGPM 1901, HANDOFF section 7.
G0_M_S2 = 9.80665

# Julian date of the Unix epoch (1970-01-01T00:00:00Z) and of J2000.0. Source: IAU / standard definitions.
JD_UNIX_EPOCH = 2440587.5
JD_J2000 = 2451545.0

SECONDS_PER_DAY = 86400.0
