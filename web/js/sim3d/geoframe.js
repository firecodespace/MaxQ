/* Reference frames for the 3D view: WGS-84 geodetic <-> ECEF, GMST, subsolar point.
 *
 * This is a deliberate port of core/frames.py and ascent/geo.py, kept to the same constants and the
 * same formulas so a position computed in the browser lands where the Python pipeline says it does.
 * Pure functions, no three.js: tests/web/geoframe.test.mjs checks it against values produced by the
 * Python modules.
 *
 * Units. Everything here is SI (metres, radians where noted) to match Python. The renderer works in
 * KILOMETRES; the conversion happens at the scene boundary, never in here.
 *
 * Frame. The scene is drawn in ECEF, the Earth-fixed frame: the globe does not spin, the sun
 * direction and the stars do. That is what lets a satellite's (lat, lon, alt) from the SGP4 worker
 * and a trajectory sample from the contract be placed by the same function with no extra rotation.
 */

// core/constants.py, value for value.
export const R_E_M = 6378137.0; // WGS-84 semi-major axis
export const WGS84_F = 1.0 / 298.257223563;
export const WGS84_B_M = R_E_M * (1.0 - WGS84_F);
export const WGS84_E2 = WGS84_F * (2.0 - WGS84_F);
export const OMEGA_E_RAD_S = 7.2921159e-5;
export const MU_M3_S2 = 3.986004418e14;
export const G0_M_S2 = 9.80665;
export const JD_UNIX_EPOCH = 2440587.5;
export const JD_J2000 = 2451545.0;
export const SECONDS_PER_DAY = 86400.0;

const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;
const TWO_PI = 2 * Math.PI;

// Written as multiplications by the same two constants Python's math.radians / numpy.rad2deg use, so
// a value converted here and a value converted there agree to the last bit rather than to one ulp.
export const deg2rad = (d) => d * DEG;
export const rad2deg = (r) => r * RAD;

/** Greenwich mean sidereal time, radians in [0, 2pi). IAU-1982 (Vallado eq. 3-47), as in frames.py. */
export function gmstRad(tUnix) {
  const jd = tUnix / SECONDS_PER_DAY + JD_UNIX_EPOCH;
  const t = (jd - JD_J2000) / 36525.0;
  const seconds =
    67310.54841 + (876600.0 * 3600.0 + 8640184.812866) * t + 0.093104 * t * t - 6.2e-6 * t * t * t;
  let r = deg2rad(((seconds % SECONDS_PER_DAY) + SECONDS_PER_DAY) % SECONDS_PER_DAY / 240.0);
  r %= TWO_PI;
  return r < 0 ? r + TWO_PI : r;
}

/** WGS-84 geodetic (deg, deg, metres above the ellipsoid) -> ECEF metres [x, y, z]. */
export function geodeticToEcef(latDeg, lonDeg, altM, out) {
  const lat = deg2rad(latDeg);
  const lon = deg2rad(lonDeg);
  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  const n = R_E_M / Math.sqrt(1.0 - WGS84_E2 * sinLat * sinLat);
  const r = (n + altM) * cosLat;
  const o = out || [0, 0, 0];
  o[0] = r * Math.cos(lon);
  o[1] = r * Math.sin(lon);
  o[2] = (n * (1.0 - WGS84_E2) + altM) * sinLat;
  return o;
}

/** ECEF metres -> {latDeg, lonDeg, altM}. Bowring fixed point, the same 10 iterations as frames.py. */
export function ecefToGeodetic(x, y, z) {
  const lon = Math.atan2(y, x);
  const p = Math.hypot(x, y);
  let lat = Math.atan2(z, p * (1.0 - WGS84_E2));
  for (let i = 0; i < 10; i++) {
    const sinLat = Math.sin(lat);
    const n = R_E_M / Math.sqrt(1.0 - WGS84_E2 * sinLat * sinLat);
    const next = Math.atan2(z + WGS84_E2 * n * sinLat, p);
    const done = Math.abs(next - lat) < 1e-14;
    lat = next;
    if (done) break;
  }
  const sinLat = Math.sin(lat);
  const cosLat = Math.cos(lat);
  const n = R_E_M / Math.sqrt(1.0 - WGS84_E2 * sinLat * sinLat);
  // Whichever form is better conditioned: p/cos near the equator, z/sin near the poles.
  const alt =
    Math.abs(cosLat) > Math.abs(sinLat)
      ? p / cosLat - n
      : Math.abs(z) / Math.abs(sinLat) - n * (1.0 - WGS84_E2);
  return { latDeg: rad2deg(lat), lonDeg: rad2deg(lon), altM: alt };
}

/** Radius of the WGS-84 ellipsoid surface, metres, at a geodetic latitude. */
export function ellipsoidRadiusM(latDeg) {
  const lat = deg2rad(latDeg);
  const s = Math.sin(lat);
  const c = Math.cos(lat);
  const n = R_E_M / Math.sqrt(1.0 - WGS84_E2 * s * s);
  return Math.hypot(n * c, n * (1.0 - WGS84_E2) * s);
}

/** Great-circle step on a sphere of radius R_E_M. Port of ascent/geo.py spherical_forward. */
export function sphericalForward(lat0Deg, lon0Deg, azimuthDeg, downrangeM) {
  const lat0 = deg2rad(lat0Deg);
  const lon0 = deg2rad(lon0Deg);
  const beta = deg2rad(azimuthDeg);
  const sigma = downrangeM / R_E_M;

  const sinLat0 = Math.sin(lat0);
  const cosLat0 = Math.cos(lat0);
  const sinSigma = Math.sin(sigma);
  const cosSigma = Math.cos(sigma);

  let sinLat = sinLat0 * cosSigma + cosLat0 * sinSigma * Math.cos(beta);
  sinLat = Math.max(-1, Math.min(1, sinLat));
  const lat = Math.asin(sinLat);

  const dlon = Math.atan2(
    Math.sin(beta) * sinSigma * cosLat0,
    cosSigma - sinLat0 * sinLat,
  );
  let lon = lon0 + dlon;
  lon = ((lon + Math.PI) % TWO_PI + TWO_PI) % TWO_PI - Math.PI;
  return [rad2deg(lat), rad2deg(lon)];
}

/** Eastward surface speed from Earth's rotation, m/s. Port of ascent/geo.py. */
export const earthRotationBoost = (latDeg) => OMEGA_E_RAD_S * R_E_M * Math.cos(deg2rad(latDeg));

/** Circular orbital speed at an altitude, m/s. */
export const circularSpeed = (altM) => Math.sqrt(MU_M3_S2 / (R_E_M + altM));

/** Normalise to (-180, 180]. */
export function wrapLon(d) {
  let x = ((d + 180) % 360 + 360) % 360 - 180;
  if (x === -180) x = 180;
  return x;
}

/* ------------------------------------------------------------------ sun
 * Low-precision solar position (the standard Almanac series, ~0.01 deg). Good to a few kilometres
 * on the terminator, which is far finer than the 4096-pixel Earth texture can show.
 */

/** Subsolar point at a time: the (lat, lon) where the sun is overhead. Degrees. */
export function subsolarPoint(tUnix) {
  const n = tUnix / SECONDS_PER_DAY + JD_UNIX_EPOCH - JD_J2000; // days since J2000.0
  const L = deg2rad(280.460 + 0.9856474 * n); // mean longitude
  const g = deg2rad(357.528 + 0.9856003 * n); // mean anomaly
  const lambda = L + deg2rad(1.915) * Math.sin(g) + deg2rad(0.020) * Math.sin(2 * g);
  const eps = deg2rad(23.439 - 0.0000004 * n); // obliquity

  const dec = Math.asin(Math.sin(eps) * Math.sin(lambda));
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lambda), Math.cos(lambda));
  return { latDeg: rad2deg(dec), lonDeg: wrapLon(rad2deg(ra - gmstRad(tUnix))) };
}

/** Unit vector toward the sun in ECEF, [x, y, z]. */
export function sunDirEcef(tUnix) {
  const s = subsolarPoint(tUnix);
  const lat = deg2rad(s.latDeg);
  const lon = deg2rad(s.lonDeg);
  const c = Math.cos(lat);
  return [c * Math.cos(lon), c * Math.sin(lon), Math.sin(lat)];
}

/* ------------------------------------------------------------------ orbit helpers */

/** Osculating elements from an ECEF-frame state is wrong by the rotation term, so this takes an
 * inertial state. Returns {aM, ecc, inclDeg, perigeeKm, apogeeKm} for a position/velocity in metres
 * and m/s. Used only to label a replayed orbit, never to propagate. */
export function elementsFromState(rM, vM) {
  const r = Math.hypot(rM[0], rM[1], rM[2]);
  const v2 = vM[0] * vM[0] + vM[1] * vM[1] + vM[2] * vM[2];
  const eps = 0.5 * v2 - MU_M3_S2 / r;
  const aM = eps < 0 ? -MU_M3_S2 / (2 * eps) : Infinity;
  const h = [
    rM[1] * vM[2] - rM[2] * vM[1],
    rM[2] * vM[0] - rM[0] * vM[2],
    rM[0] * vM[1] - rM[1] * vM[0],
  ];
  const hm = Math.hypot(h[0], h[1], h[2]);
  const ecc = Math.sqrt(Math.max(0, 1 + (2 * eps * hm * hm) / (MU_M3_S2 * MU_M3_S2)));
  const inclDeg = rad2deg(Math.acos(Math.max(-1, Math.min(1, h[2] / hm))));
  return {
    aM,
    ecc,
    inclDeg,
    perigeeKm: (aM * (1 - ecc) - R_E_M) / 1000,
    apogeeKm: (aM * (1 + ecc) - R_E_M) / 1000,
  };
}
