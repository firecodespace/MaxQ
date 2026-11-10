/* Unit tests for the 3D camera, the scale model and the frame conversions.
 *
 * These run in Node with no browser: three.js imports cleanly there, and OrbitCam only needs an
 * object with addEventListener, so the camera can be driven and measured directly.
 *
 * The grab-drag tests exist because of a specific bug that was invisible in a screenshot. With the
 * camera's up vector set to the local vertical, a top-down view has up parallel to the view
 * direction; lookAt() then has no basis to build, the camera's roll flips from frame to frame, and
 * every unprojected cursor ray lands somewhere different. The picture looks perfect and the drag
 * shakes. Nothing but a numeric check catches that, so here it is.
 */

import test from "node:test";
import assert from "node:assert/strict";

import * as THREE from "../../web/vendor/three/three.module.js";
import { OrbitCam, altitudeKm, rayEllipsoid, surfaceRadiusTowards } from "../../web/js/sim3d/controls.js";
import {
  clipPlanes,
  detailLevel,
  inflation,
  kmForPixels,
  niceStep,
  pixelRadius,
  renderRadius,
  scaleBar,
  zoomDistance,
} from "../../web/js/sim3d/scalemath.js";
import {
  R_E_M,
  ecefToGeodetic,
  ellipsoidRadiusM,
  geodeticToEcef,
  gmstRad,
  subsolarPoint,
  wrapLon,
} from "../../web/js/sim3d/geoframe.js";

const R_EQ_KM = R_E_M / 1000;

/** The smallest stand-in for a canvas that OrbitCam needs. */
function fakeDom() {
  return {
    clientWidth: 1200,
    clientHeight: 800,
    addEventListener() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 800 }),
    classList: { add() {}, remove() {} },
    setPointerCapture() {},
    releasePointerCapture() {},
  };
}

function makeCam() {
  const camera = new THREE.PerspectiveCamera(42, 1.5, 1, 1e7);
  const cam = new OrbitCam(camera, fakeDom(), {});
  cam.geo = true;
  cam.center.set(1, 0, 0);
  cam.wantDistance = cam.distance = 2 * R_EQ_KM;
  return { cam, camera };
}

/* ------------------------------------------------------------------ geoframe */

test("geodetic and ECEF round-trip to sub-millimetre", () => {
  for (const [lat, lon, alt] of [
    [0, 0, 0],
    [28.5619, -80.5772, 0],
    [-33.9, 151.2, 1234.5],
    [89.9, 17, 400000],
    [-89.99, -179.9, 35786000],
  ]) {
    const [x, y, z] = geodeticToEcef(lat, lon, alt);
    const g = ecefToGeodetic(x, y, z);
    assert.ok(Math.abs(g.latDeg - lat) < 1e-9, `lat ${lat} -> ${g.latDeg}`);
    assert.ok(Math.abs(wrapLon(g.lonDeg - lon)) < 1e-9, `lon ${lon} -> ${g.lonDeg}`);
    assert.ok(Math.abs(g.altM - alt) < 1e-3, `alt ${alt} -> ${g.altM}`);
  }
});

test("the ellipsoid is flattened by the right amount", () => {
  // WGS-84: equatorial 6,378.137 km, polar 6,356.752 km, a difference of 21.4 km.
  assert.ok(Math.abs(ellipsoidRadiusM(0) - 6378137) < 1e-6);
  assert.ok(Math.abs(ellipsoidRadiusM(90) - 6356752.314) < 1e-3);
  assert.ok(ellipsoidRadiusM(0) - ellipsoidRadiusM(90) > 21000);
});

test("GMST advances by a sidereal day, not a solar one", () => {
  const t0 = 1794267690;
  const a = gmstRad(t0);
  const b = gmstRad(t0 + 86164.0905); // one sidereal day
  const diff = Math.abs(((b - a + Math.PI) % (2 * Math.PI)) - Math.PI);
  assert.ok(diff < 1e-4, `GMST drifted ${diff} rad over a sidereal day`);
});

test("the subsolar point tracks the season and the hour", () => {
  // 10 November 2026: the sun is well south of the equator, around -17 degrees.
  const s = subsolarPoint(Date.UTC(2026, 10, 10, 0, 52) / 1000);
  assert.ok(s.latDeg < -15 && s.latDeg > -19, `subsolar latitude ${s.latDeg}`);
  // Local noon is near 180 E at 00:52 UTC; allow for the equation of time (about 16 minutes = 4 deg).
  assert.ok(Math.abs(wrapLon(s.lonDeg - 167)) < 6, `subsolar longitude ${s.lonDeg}`);

  // Six months later it must be north of the equator by a similar amount.
  const s2 = subsolarPoint(Date.UTC(2027, 4, 10, 0, 52) / 1000);
  assert.ok(s2.latDeg > 15, `subsolar latitude in May ${s2.latDeg}`);
});

/* ------------------------------------------------------------------ scale model */

test("a sub-pixel body is inflated to exactly the pixel floor, while the cap allows", () => {
  const trueR = 0.5; // a 1 km body
  const dist = 20000;
  assert.ok(pixelRadius(trueR, dist, 42, 800) < 2.5, "it starts under the floor");
  const drawn = renderRadius(trueR, dist, 42, 800, { minPx: 2.5 });
  assert.ok(Math.abs(pixelRadius(drawn, dist, 42, 800) - 2.5) < 1e-9, "inflated to exactly the floor");
  assert.ok(inflation(trueR, drawn) > 1, "and the UI can report how much of a lie that is");
});

test("past the cap a body keeps shrinking honestly rather than ballooning", () => {
  // A 10 m satellite seen from 20,000 km would need roughly 9,600x to reach a 2.5 px floor. The cap
  // refuses: a body that small, that far away, genuinely cannot be drawn to scale AND be visible,
  // and quietly inflating it ten thousand times would be the dishonest answer. The catalog layer
  // handles these as fixed-size GPU markers instead, which is what the cap is protecting.
  const trueR = 0.005;
  const dist = 20000;
  const drawn = renderRadius(trueR, dist, 42, 800, { minPx: 2.5, maxInflation: 4000 });
  assert.ok(Math.abs(drawn - trueR * 4000) < 1e-12, "the cap binds");
  assert.ok(pixelRadius(drawn, dist, 42, 800) < 2.5, "and it is still under the floor, truthfully");
});

test("close up, the same satellite is drawn at true scale", () => {
  const trueR = 0.005;
  const dist = 0.05; // 50 m away
  assert.equal(renderRadius(trueR, dist, 42, 800, { minPx: 2.5 }), trueR);
  assert.equal(inflation(trueR, renderRadius(trueR, dist, 42, 800)), 1);
});

test("inflation is capped, so a fleck of debris never becomes a moon", () => {
  const tiny = 1e-7; // 0.1 mm
  const drawn = renderRadius(tiny, 1e5, 42, 800, { minPx: 3, maxInflation: 4000 });
  assert.ok(drawn <= tiny * 4000 + 1e-18, "the cap holds");
});

test("detail level switches to a mesh only once the body covers real pixels", () => {
  const stationR = 0.0545; // ISS-class, 109 m across
  assert.equal(detailLevel(stationR, 20000, 42, 800), "marker");
  assert.equal(detailLevel(stationR, 2, 42, 800), "mesh");
  // Hysteresis keeps it from flickering at the boundary.
  const d = 1 / Math.tan((42 * Math.PI) / 360) * (stationR / 5) * 400;
  assert.equal(detailLevel(stationR, d * 1.15, 42, 800, { wasMesh: true }), "mesh");
  assert.equal(detailLevel(stationR, d * 1.15, 42, 800, { wasMesh: false }), "marker");
});

test("zoom is a constant proportional step in altitude, at every altitude", () => {
  const surf = R_EQ_KM;
  // One notch in from 40,000 km up and from 2 km up must change altitude by the same ratio.
  const high = 40000 + surf;
  const low = 2 + surf;
  const rHigh = (zoomDistance(high, surf, 1) - surf) / 40000;
  const rLow = (zoomDistance(low, surf, 1) - surf) / 2;
  assert.ok(Math.abs(rHigh - rLow) < 1e-9, `${rHigh} vs ${rLow}`);
  assert.ok(rHigh < 1, "a positive notch zooms in");
});

test("zoom cannot be driven through the surface or past the clamp", () => {
  const surf = R_EQ_KM;
  let d = surf + 10000;
  for (let i = 0; i < 400; i++) d = zoomDistance(d, surf, 3, { minAltKm: 0.02 });
  assert.ok(d - surf >= 0.02 - 1e-12, `altitude fell to ${d - surf}`);
  let u = surf + 10;
  for (let i = 0; i < 400; i++) u = zoomDistance(u, surf, -3, { maxAltKm: 420000 });
  assert.ok(u - surf <= 420000 + 1e-6);
});

test("clip planes keep a workable range and never a zero near plane", () => {
  for (const alt of [0.02, 1, 100, 12000, 400000]) {
    const { near, far } = clipPlanes(alt);
    assert.ok(near > 0, "near must be positive or the projection matrix is undefined");
    assert.ok(near <= Math.max(0.002, alt * 0.01) + 1e-12);
    assert.ok(far > near * 10);
  }
});

test("the scale bar reports a round number and its true width", () => {
  const b = scaleBar(20000, 42, 800, 110);
  assert.ok([1, 2, 5].includes(b.km / Math.pow(10, Math.floor(Math.log10(b.km)))));
  const kmPerPx = kmForPixels(1, 20000, 42, 800);
  assert.ok(Math.abs(b.px * kmPerPx - b.km) < 1e-6, "the bar's pixel width must match its label");
  assert.ok(b.px <= 110 + 1e-9);
  assert.equal(niceStep(7.3), 5);
  assert.equal(niceStep(0.04), 0.02);
});

/* ------------------------------------------------------------------ ray / ellipsoid */

test("a ray down the equator hits the ellipsoid at the equatorial radius", () => {
  const o = new THREE.Vector3(30000, 0, 0);
  const hit = rayEllipsoid(o, new THREE.Vector3(-1, 0, 0));
  assert.ok(hit, "the ray must hit");
  assert.ok(Math.abs(hit.x - R_EQ_KM) < 1e-6, `hit at ${hit.x}`);
});

test("a ray down the pole hits at the polar radius, which is 21 km shorter", () => {
  const hit = rayEllipsoid(new THREE.Vector3(0, 0, 30000), new THREE.Vector3(0, 0, -1));
  assert.ok(hit);
  assert.ok(Math.abs(hit.z - 6356.752) < 1e-2, `hit at ${hit.z}`);
});

test("a ray that misses the Earth returns nothing", () => {
  assert.equal(rayEllipsoid(new THREE.Vector3(30000, 0, 0), new THREE.Vector3(0, 1, 0)), null);
});

test("surface radius and altitude agree with each other", () => {
  for (const v of [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 0, 1), new THREE.Vector3(1, 1, 1)]) {
    const r = surfaceRadiusTowards(v);
    const onSurface = v.clone().normalize().multiplyScalar(r);
    assert.ok(Math.abs(altitudeKm(onSurface)) < 1e-6, `altitude on the surface was ${altitudeKm(onSurface)}`);
  }
});

/* ------------------------------------------------------------------ the camera */

test("screen-up is perpendicular to the view direction at EVERY tilt", () => {
  // The bug this guards: with up set to the local vertical, a top-down view has up parallel to the
  // view direction, lookAt() degenerates, and the camera's roll becomes unstable frame to frame.
  const { cam } = makeCam();
  const origin = new THREE.Vector3(0, 0, 0);
  for (const tiltDeg of [0, 1, 15, 45, 70, 78]) {
    cam.tilt = cam.wantTilt = (tiltDeg * Math.PI) / 180;
    for (const headingDeg of [0, 90, 215]) {
      cam.heading = cam.wantHeading = (headingDeg * Math.PI) / 180;
      const pos = cam.position(new THREE.Vector3());
      const view = cam.centerPoint(new THREE.Vector3()).sub(pos).normalize();
      const dot = Math.abs(view.dot(cam._up));
      assert.ok(dot < 1e-9, `tilt ${tiltDeg}, heading ${headingDeg}: up . view = ${dot}`);
      assert.ok(Math.abs(cam._up.length() - 1) < 1e-9, "up must be a unit vector");
      void origin;
    }
  }
});

test("the camera sits at the requested range above the point it is looking at", () => {
  const { cam } = makeCam();
  for (const tiltDeg of [0, 30, 60]) {
    cam.tilt = (tiltDeg * Math.PI) / 180;
    cam.distance = 1500;
    const pos = cam.position(new THREE.Vector3());
    const ctr = cam.centerPoint(new THREE.Vector3());
    assert.ok(Math.abs(pos.distanceTo(ctr) - 1500) < 1e-6);
  }
});

test("grab-drag converges: the grabbed point returns under the cursor", () => {
  // The whole contract of a Google-Earth style drag in one assertion.
  const { cam, camera } = makeCam();
  const origin = new THREE.Vector3(0, 0, 0);
  camera.aspect = 1.5;

  const settle = () => {
    for (let i = 0; i < 6; i++) cam.apply(origin, 1 / 60);
  };
  settle();

  const grab = cam._globeAt(-0.2, 0.1).normalize();
  const targetNdc = [0.25, -0.15];

  cam._drag = { id: 1, grab, tilting: false, moved: 99, t: performance.now() };
  let drift = Infinity;
  for (let i = 0; i < 40; i++) {
    cam._drag.ndc = targetNdc;
    cam.apply(origin, 1 / 60);
    const under = cam._globeAt(targetNdc[0], targetNdc[1]).normalize();
    drift = (Math.acos(Math.max(-1, Math.min(1, under.dot(grab)))) * 180) / Math.PI;
  }
  cam._drag = null;
  assert.ok(drift < 0.01, `the grabbed point settled ${drift} deg from the cursor`);
});

test("grab-drag converges when tilted too", () => {
  const { cam } = makeCam();
  const origin = new THREE.Vector3(0, 0, 0);
  cam.tilt = cam.wantTilt = (55 * Math.PI) / 180;
  cam.heading = cam.wantHeading = 1.1;
  for (let i = 0; i < 6; i++) cam.apply(origin, 1 / 60);

  const grab = cam._globeAt(0.1, 0.2).normalize();
  const targetNdc = [-0.3, 0.05];
  cam._drag = { id: 1, grab, tilting: false, moved: 99, t: performance.now() };
  let drift = Infinity;
  for (let i = 0; i < 60; i++) {
    cam._drag.ndc = targetNdc;
    cam.apply(origin, 1 / 60);
    const under = cam._globeAt(targetNdc[0], targetNdc[1]).normalize();
    drift = (Math.acos(Math.max(-1, Math.min(1, under.dot(grab)))) * 180) / Math.PI;
  }
  cam._drag = null;
  assert.ok(drift < 0.05, `tilted grab settled ${drift} deg from the cursor`);
});

test("zooming on a pivot brings it toward the middle of the screen", () => {
  const { cam } = makeCam();
  const origin = new THREE.Vector3(0, 0, 0);
  cam.apply(origin, 1 / 60);
  const pivot = cam._globeAt(0.5, 0.3).clone();
  const before = Math.acos(Math.max(-1, Math.min(1, cam.center.dot(pivot.clone().normalize()))));
  for (let i = 0; i < 10; i++) cam.zoom(1, pivot);
  const after = Math.acos(Math.max(-1, Math.min(1, cam.center.dot(pivot.clone().normalize()))));
  assert.ok(after < before, `the pivot moved from ${before} to ${after} rad off centre`);
  assert.ok(cam.wantDistance < 2 * R_EQ_KM, "and the camera actually came closer");
});

test("the fling decays to a stop", () => {
  const { cam } = makeCam();
  const origin = new THREE.Vector3(0, 0, 0);
  cam.spinAxis.set(0, 0, 1);
  cam.spinRate = 2;
  for (let i = 0; i < 600; i++) cam.apply(origin, 1 / 60);
  assert.equal(cam.spinRate, 0, "a fling must come to rest, not drift forever");
});

test("the camera never ends up below the ground", () => {
  const { cam } = makeCam();
  const origin = new THREE.Vector3(0, 0, 0);
  cam.wantDistance = -500; // absurd input
  for (let i = 0; i < 60; i++) cam.apply(origin, 1 / 60);
  const pos = cam.position(new THREE.Vector3());
  assert.ok(altitudeKm(pos) >= cam.minAltKm - 1e-6, `camera altitude ${altitudeKm(pos)}`);
});

test("switching between the geographic and free cameras does not jump the view", () => {
  const { cam } = makeCam();
  const origin = new THREE.Vector3(0, 0, 0);
  cam.apply(origin, 1 / 60);
  const before = cam.position(new THREE.Vector3());
  cam.setGeo(false);
  const after = cam.position(new THREE.Vector3());
  // Free mode orbits the Earth's centre rather than a ground point, so the position is preserved
  // exactly; it is the model underneath that changed.
  assert.ok(before.distanceTo(after) < 1e-6, `the view jumped ${before.distanceTo(after)} km`);
});
