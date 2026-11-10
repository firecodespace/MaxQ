/* The camera. Two models, because a globe and a rocket want opposite things.
 *
 * GEO MODE (the globe) is a geographic camera, the way Google Earth works, and it is worth saying
 * exactly what that means because it is not the same as orbiting around a point:
 *
 *   - The camera is defined by a place, not by angles: a `center` direction picking a point on the
 *     Earth, a `range` out from it, a `heading` (which way is up-screen) and a `tilt` (0 looking
 *     straight down, larger looking toward the horizon).
 *   - DRAGGING GRABS THE GROUND. The point under the cursor when the drag starts stays under the
 *     cursor for the whole drag: we solve for the rotation that puts it back there and turn the
 *     globe by it. That is the whole difference in feel. Orbiting by "so many degrees per pixel"
 *     slides the planet at some speed unrelated to what you grabbed, which is why it feels wrong
 *     near the poles, wrong when zoomed in, and wrong at the limb.
 *   - Letting go leaves it spinning, and the spin decays. The fling is most of what makes a globe
 *     feel like an object rather than a diagram.
 *   - Zooming pulls toward the cursor AND recentres on it, so zooming in on a feature brings it to
 *     the middle rather than letting it slide off the edge.
 *
 * FREE MODE (flight) is a plain orbit around a moving target, which is what you want when the
 * subject is a vehicle rather than a planet.
 *
 * In both, zoom is multiplicative in ALTITUDE ABOVE THE NEAREST SURFACE (see scalemath.js). A wheel
 * notch is the same proportional step everywhere, so crossing from past GEO down to a launch pad
 * takes a steady number of notches, the step gentles automatically as you approach the ground, and
 * the camera cannot punch through the planet.
 *
 * Units are KILOMETRES. Positions are absolute ECEF; the floating origin is applied in view.js.
 */

import * as THREE from "../../vendor/three/three.module.js";
import { R_E_M, WGS84_B_M, WGS84_E2 } from "./geoframe.js";
import { clipPlanes, zoomDistance } from "./scalemath.js";

const A_KM = R_E_M / 1000;
const B_KM = WGS84_B_M / 1000;
const HALF_PI = Math.PI / 2;
const MAX_TILT = (78 * Math.PI) / 180;

/** Radius of the ellipsoid surface, km, in the direction of a point. */
export function surfaceRadiusTowards(v) {
  const len = Math.hypot(v.x, v.y, v.z);
  if (!(len > 0)) return A_KM;
  const sinLat = v.z / len;
  const c2 = 1 - sinLat * sinLat;
  return 1 / Math.sqrt(c2 / (A_KM * A_KM) + (sinLat * sinLat) / (B_KM * B_KM));
}

/**
 * Nearest intersection of a ray with the WGS-84 ellipsoid, or null.
 *
 * Solved by squashing the ellipsoid into a unit sphere (x/a, y/a, z/b), intersecting there and
 * unsquashing the hit: exact, and far cheaper than iterating on the ellipsoid itself.
 */
export function rayEllipsoid(originKm, dirKm, out) {
  const ox = originKm.x / A_KM, oy = originKm.y / A_KM, oz = originKm.z / B_KM;
  const dx = dirKm.x / A_KM, dy = dirKm.y / A_KM, dz = dirKm.z / B_KM;
  const a = dx * dx + dy * dy + dz * dz;
  const b = 2 * (ox * dx + oy * dy + oz * dz);
  const c = ox * ox + oy * oy + oz * oz - 1;
  const disc = b * b - 4 * a * c;
  if (disc < 0 || a === 0) return null;
  const s = Math.sqrt(disc);
  let t = (-b - s) / (2 * a);
  if (t < 0) t = (-b + s) / (2 * a);
  if (t < 0) return null;
  const o = out || new THREE.Vector3();
  return o.set(originKm.x + dirKm.x * t, originKm.y + dirKm.y * t, originKm.z + dirKm.z * t);
}

/**
 * Where a ray points on the globe when it MISSES it: the nearest point on the limb.
 *
 * Needed so a drag that wanders off the edge of the planet keeps working instead of freezing. The
 * grab simply follows the silhouette rather than giving up.
 */
function rayGrazeEllipsoid(originKm, dirKm, out) {
  // Work in the squashed space where the ellipsoid is a unit sphere.
  const o = new THREE.Vector3(originKm.x / A_KM, originKm.y / A_KM, originKm.z / B_KM);
  const d = new THREE.Vector3(dirKm.x / A_KM, dirKm.y / A_KM, dirKm.z / B_KM).normalize();
  // Closest approach of the ray to the sphere's centre, pushed back onto the unit sphere.
  const t = -o.dot(d);
  const closest = o.clone().addScaledVector(d, Math.max(0, t));
  if (closest.lengthSq() === 0) closest.copy(d);
  closest.normalize();
  const res = out || new THREE.Vector3();
  return res.set(closest.x * A_KM, closest.y * A_KM, closest.z * B_KM);
}

/** Geodetic altitude of a point, km. Closed form, good to a metre at these ranges. */
export function altitudeKm(v) {
  const p = Math.hypot(v.x, v.y);
  const lat = Math.atan2(v.z, p * (1 - WGS84_E2));
  const s = Math.sin(lat), c = Math.cos(lat);
  const n = A_KM / Math.sqrt(1 - WGS84_E2 * s * s);
  return Math.abs(c) > Math.abs(s) ? p / c - n : Math.abs(v.z) / Math.abs(s) - n * (1 - WGS84_E2);
}

/** Local east/north at a unit direction on the globe. Falls back sanely at the poles. */
function localFrame(up, east, north) {
  east.set(-up.y, up.x, 0);
  if (east.lengthSq() < 1e-12) east.set(1, 0, 0);
  east.normalize();
  north.crossVectors(up, east).normalize();
}

export class OrbitCam {
  /**
   * @param {THREE.PerspectiveCamera} camera
   * @param {HTMLElement} dom element receiving pointer events
   * @param {object} hooks
   *   `pivotAt(ndcX, ndcY)` -> Vector3 in absolute km, or null. The view supplies this so objects
   *   can be zoom pivots too, not just the globe.
   */
  constructor(camera, dom, hooks = {}) {
    this.camera = camera;
    this.dom = dom;
    this.hooks = hooks;

    this.geo = true; // globe camera; false = free orbit around a target

    // --- geographic state
    this.center = new THREE.Vector3(1, 0, 0); // unit: the point on the globe at screen centre
    this.heading = 0; // rad, 0 = north up
    this.tilt = 0; // rad from straight down
    this.wantHeading = 0;
    this.wantTilt = 0;

    // --- shared
    this.target = new THREE.Vector3(0, 0, 0); // absolute look-at point, km
    this.wantTarget = new THREE.Vector3(0, 0, 0);
    this.distance = A_KM * 3.25;
    this.wantDistance = this.distance;
    this.azimuth = -1.1; // free mode only
    this.wantAzimuth = this.azimuth;
    this.elevation = 0.38;
    this.wantElevation = this.elevation;

    this.damping = 0.18;
    this.minAltKm = 0.02;
    this.maxAltKm = 420000;
    this.follow = null;
    this.enabled = true;

    // Fling: an axis and a rate, decayed every frame.
    this.spinAxis = new THREE.Vector3(0, 0, 1);
    this.spinRate = 0; // rad/s
    this.spinDecay = 2.6; // e-folds per second

    this._drag = null;
    this._appliedPos = null; // the absolute position the camera matrices were last built from
    this._rv = new THREE.Vector3();
    this._gv = new THREE.Vector3();
    this._p = new THREE.Vector3();
    this._v = new THREE.Vector3();
    this._e = new THREE.Vector3();
    this._n = new THREE.Vector3();
    this._up = new THREE.Vector3(0, 0, 1);
    this._horiz = new THREE.Vector3(0, 1, 0);
    this._q = new THREE.Quaternion();
    this._bind();
  }

  /** The absolute point on the globe the camera is looking at. */
  centerPoint(out) {
    const r = surfaceRadiusTowards(this.center);
    return (out || new THREE.Vector3()).copy(this.center).multiplyScalar(r);
  }

  /** Camera position, derived from the active model. */
  position(out) {
    const o = out || new THREE.Vector3();
    if (!this.geo) {
      const ce = Math.cos(this.elevation);
      return o.set(
        this.target.x + this.distance * ce * Math.cos(this.azimuth),
        this.target.y + this.distance * ce * Math.sin(this.azimuth),
        this.target.z + this.distance * Math.sin(this.elevation),
      );
    }
    const c = this.centerPoint(this._v);
    localFrame(this.center, this._e, this._n);
    const st = Math.sin(this.tilt);
    const ct = Math.cos(this.tilt);
    const ch = Math.cos(this.heading);
    const sh = Math.sin(this.heading);
    // The horizontal direction the view faces, at the centre: north turned by the heading.
    this._horiz.set(
      this._n.x * ch + this._e.x * sh,
      this._n.y * ch + this._e.y * sh,
      this._n.z * ch + this._e.z * sh,
    );
    // Screen-up.
    //
    // This has to be computed, not guessed. The view direction is -(center*cos t - horiz*sin t),
    // and up = center*sin t + horiz*cos t is exactly perpendicular to it for EVERY tilt (their dot
    // product is cos t sin t - sin t cos t = 0). Using the local vertical instead looks reasonable
    // and is degenerate at tilt 0, where it lies along the view direction: lookAt() then has no
    // basis to build and the camera's roll flips about from frame to frame. That is invisible in a
    // still image and wrecks anything that unprojects the cursor, which is to say the grab-drag.
    this._up.set(
      this.center.x * st + this._horiz.x * ct,
      this.center.y * st + this._horiz.y * ct,
      this.center.z * st + this._horiz.z * ct,
    ).normalize();
    return o.set(
      c.x + this.distance * (this.center.x * ct - this._horiz.x * st),
      c.y + this.distance * (this.center.y * ct - this._horiz.y * st),
      c.z + this.distance * (this.center.z * ct - this._horiz.z * st),
    );
  }

  /** How much of distance-to-target lies inside the planet. */
  _surfaceOffset() {
    if (this.geo) return 0; // the look-at point is already on the surface
    if (this.wantTarget.lengthSq() === 0) return A_KM;
    const r = this.wantTarget.length();
    return Math.max(0, surfaceRadiusTowards(this.wantTarget) - r);
  }

  /** Altitude of the look-at point above solid ground: the scale for every interaction. */
  pivotAltitudeKm() {
    return Math.max(0, this.distance - this._surfaceOffset());
  }

  _clampAltitude() {
    const pos = this.position(this._p);
    const alt = altitudeKm(pos);
    if (alt >= this.minAltKm) return;
    this.distance += (this.minAltKm - alt) * 1.05 + 1e-6;
    this.wantDistance = Math.max(this.wantDistance, this.distance);
  }

  /* ---------------------------------------------------------------- zoom */

  /** Zoom by `notches` (positive = in), pulling toward `pivot` if one was found. */
  zoom(notches, pivot) {
    const before = this.wantDistance;
    const offset = this._surfaceOffset();
    const next = zoomDistance(before, offset, notches, {
      rate: 0.22,
      minAltKm: this.minAltKm,
      maxAltKm: this.maxAltKm,
    });
    const factor = next / before;
    if (!isFinite(factor) || factor <= 0) return;

    if (pivot) {
      if (this.geo) {
        // Rotate the centre toward the pivot by as much as the zoom closed in. Zoom in hard and the
        // thing you pointed at ends up in the middle; nudge the wheel and it barely moves. This is
        // what "zoom where I am pointing" means on a globe, where you cannot simply translate.
        const p = this._v.copy(pivot).normalize();
        const s = Math.max(0, Math.min(1, 1 - factor));
        const dot = Math.max(-1, Math.min(1, this.center.dot(p)));
        const angle = Math.acos(dot);
        if (angle > 1e-6) {
          this._q.setFromAxisAngle(
            this._p.crossVectors(this.center, p).normalize(),
            angle * s,
          );
          this.center.applyQuaternion(this._q).normalize();
        }
      } else {
        this.wantTarget.set(
          pivot.x + (this.wantTarget.x - pivot.x) * factor,
          pivot.y + (this.wantTarget.y - pivot.y) * factor,
          pivot.z + (this.wantTarget.z - pivot.z) * factor,
        );
        this.follow = null;
      }
    }
    this.wantDistance = next;
    this.spinRate = 0; // a deliberate zoom ends a fling
  }

  /* ---------------------------------------------------------------- rotation */

  /**
   * Turn the globe so that the point `fromDir` ends up where `toDir` is: the grab.
   * Both are unit directions from the Earth's centre.
   */
  rotateBetween(fromDir, toDir, recordSpin, dtS) {
    const dot = Math.max(-1, Math.min(1, fromDir.dot(toDir)));
    let angle = Math.acos(dot);
    if (!(angle > 1e-9)) return;
    const axis = this._p.crossVectors(fromDir, toDir);
    if (axis.lengthSq() < 1e-18) return;
    axis.normalize();
    // Near the limb the surface is almost edge-on, so a pixel of cursor movement maps to a huge arc
    // and a grab there can throw the globe right round. Cap one frame's turn: a fast drag then
    // takes a few frames to catch up instead of snapping somewhere unrecognisable.
    const MAX_STEP = 0.22;
    if (angle > MAX_STEP) angle = MAX_STEP;
    this._q.setFromAxisAngle(axis, angle);
    this.center.applyQuaternion(this._q).normalize();
    if (recordSpin && dtS > 0) {
      this.spinAxis.copy(axis);
      // Smoothed, so one noisy frame cannot decide the fling, and capped so a fast flick spins the
      // globe rather than launching it.
      const inst = Math.max(-3.5, Math.min(3.5, angle / dtS));
      this.spinRate = this.spinRate * 0.6 + inst * 0.4;
    }
  }

  /** Change heading and tilt by a pixel delta (the right-drag / shift-drag gesture). */
  tiltDrag(dxPx, dyPx) {
    const k = 0.0042;
    this.wantHeading -= dxPx * k;
    this.wantTilt = Math.max(0, Math.min(MAX_TILT, this.wantTilt + dyPx * k));
  }

  /** Free-mode orbit by a pixel delta. */
  orbit(dxPx, dyPx) {
    const alt = this.pivotAltitudeKm();
    const sens = (0.06 + 0.24 * Math.min(1, alt / A_KM)) * (Math.PI / 180);
    this.wantAzimuth -= dxPx * sens;
    this.wantElevation = Math.max(
      -HALF_PI + 0.012,
      Math.min(HALF_PI - 0.012, this.wantElevation + dyPx * sens),
    );
  }

  /* ---------------------------------------------------------------- moves */

  /** Look at a place. In geo mode `targetKm` picks the ground point; in free mode the orbit centre. */
  goTo(targetKm, distanceKm) {
    if (this.geo) {
      if (targetKm && targetKm.lengthSq() > 0) this.center.copy(targetKm).normalize();
    } else {
      this.wantTarget.copy(targetKm);
    }
    if (distanceKm != null) this.wantDistance = Math.max(this.minAltKm, distanceKm);
    this.follow = null;
    this.spinRate = 0;
  }

  /** Jump with no travel: for a reset or a mode change, where easing would read as a glitch. */
  snap() {
    this.target.copy(this.wantTarget);
    this.distance = this.wantDistance;
    this.azimuth = this.wantAzimuth;
    this.elevation = this.wantElevation;
    this.heading = this.wantHeading;
    this.tilt = this.wantTilt;
    this.spinRate = 0;
    this._clampAltitude();
  }

  /** Track a moving point (free mode). */
  followPoint(fn, distanceKm) {
    this.follow = fn;
    if (distanceKm != null) this.wantDistance = Math.max(this.minAltKm, distanceKm);
  }

  /** Switch camera model, carrying the view across so the change is not a jump cut. */
  setGeo(on) {
    if (on === this.geo) return;
    const pos = this.position(this._p).clone();
    this.geo = on;
    if (on) {
      this.center.copy(pos).normalize();
      this.wantTilt = this.tilt = 0;
      this.wantHeading = this.heading = 0;
      this.target.set(0, 0, 0);
      this.wantTarget.set(0, 0, 0);
    } else {
      const d = pos.clone().sub(this.target);
      this.distance = this.wantDistance = Math.max(this.minAltKm, d.length());
      this.azimuth = this.wantAzimuth = Math.atan2(d.y, d.x);
      this.elevation = this.wantElevation = Math.asin(Math.max(-1, Math.min(1, d.z / d.length())));
    }
    this.spinRate = 0;
  }

  /* ---------------------------------------------------------------- per frame */

  _damp(dt) {
    // Frame-rate independent easing: the same visual speed at 30 Hz and at 144 Hz.
    const k = 1 - Math.pow(1 - this.damping, Math.min(4, dt * 60));
    // Distance eases geometrically, so a trip from GEO to a launch pad travels at a constant
    // proportional rate instead of crawling through the last thousand kilometres.
    this.distance *= Math.pow(this.wantDistance / this.distance, k);
    if (!isFinite(this.distance) || this.distance <= 0) this.distance = this.wantDistance;
    if (Math.abs(this.wantDistance / this.distance - 1) < 1e-4) this.distance = this.wantDistance;

    if (this.geo) {
      this.heading += (this.wantHeading - this.heading) * k;
      this.tilt += (this.wantTilt - this.tilt) * k;
      // The fling.
      if (Math.abs(this.spinRate) > 1e-4 && !this._drag) {
        this._q.setFromAxisAngle(this.spinAxis, -this.spinRate * dt);
        this.center.applyQuaternion(this._q).normalize();
        this.spinRate *= Math.exp(-this.spinDecay * dt);
        if (Math.abs(this.spinRate) < 1e-4) this.spinRate = 0;
      }
      this.centerPoint(this.target);
      this.wantTarget.copy(this.target);
    } else {
      this.target.lerp(this.wantTarget, k);
      this.azimuth += (this.wantAzimuth - this.azimuth) * k;
      this.elevation += (this.wantElevation - this.elevation) * k;
      if (this.target.distanceToSquared(this.wantTarget) < 1e-12) this.target.copy(this.wantTarget);
    }
  }

  /**
   * Solve one frame of grab-drag: turn the globe so the grabbed point sits under the cursor again.
   *
   * Runs exactly once per frame, before anything else moves the camera, so the ray is cast against
   * the camera the previous frame was drawn with and the correction converges instead of ringing.
   */
  _applyDrag() {
    const d = this._drag;
    if (!d || !d.ndc || !d.grab || !this.geo) return;
    // Time between APPLIED corrections, not between pointer events: several events can coalesce
    // into one frame, and dividing a frame's rotation by a sub-millisecond event gap is what turns
    // a gentle drag into a violent fling on release.
    const now = performance.now();
    const dtS = this._lastDragApply ? Math.min(0.1, Math.max(1 / 240, (now - this._lastDragApply) / 1000)) : 1 / 60;
    this._lastDragApply = now;
    const under = this._globeAt(d.ndc[0], d.ndc[1]).normalize();
    d.ndc = null;
    this.rotateBetween(under, d.grab, true, dtS);
  }

  /** Apply the state to the three.js camera. Returns the absolute camera position. */
  apply(originKm, dt = 1 / 60) {
    this._applyDrag();
    if (this.follow && !this.geo) {
      const p = this.follow();
      if (p) { this.wantTarget.copy(p); this.target.copy(p); }
      else this.follow = null;
    }
    this._damp(dt);
    this._clampAltitude();

    const pos = this.position(this._p);
    const { near, far } = clipPlanes(Math.max(this.pivotAltitudeKm(), altitudeKm(pos)));
    if (this.camera.near !== near || this.camera.far !== far) {
      this.camera.near = near;
      this.camera.far = far;
      this.camera.updateProjectionMatrix();
    }
    // position() computed the exact screen-up for the current tilt; see the note there for why it
    // cannot be the local vertical.
    if (this.geo) this.camera.up.copy(this._up);
    else this.camera.up.set(0, 0, 1);
    this.camera.position.set(pos.x - originKm.x, pos.y - originKm.y, pos.z - originKm.z);
    this.camera.lookAt(
      this.target.x - originKm.x,
      this.target.y - originKm.y,
      this.target.z - originKm.z,
    );
    this.camera.updateMatrixWorld();
    this._appliedPos = (this._appliedPos || new THREE.Vector3()).copy(pos);
    return pos;
  }

  /* ---------------------------------------------------------------- input */

  _ndc(e) {
    const r = this.dom.getBoundingClientRect();
    return [((e.clientX - r.left) / r.width) * 2 - 1, -(((e.clientY - r.top) / r.height) * 2 - 1)];
  }

  /**
   * The ray through a screen point, in absolute km.
   *
   * The origin is the position the camera was LAST APPLIED at, not the position the current state
   * would give. unproject() reads the camera's projection and world matrices, which were built from
   * that applied state, so pairing them with a freshly recomputed origin mixes two different
   * cameras and the ray lands in the wrong place.
   */
  _rayAt(ndcX, ndcY) {
    this.camera.updateMatrixWorld();
    const origin = this._appliedPos || this.position(new THREE.Vector3());
    const v = this._rv.set(ndcX, ndcY, 0.5).unproject(this.camera);
    // camera.position is in floating-origin space; the direction is origin-independent.
    const dir = v.sub(this.camera.position).normalize();
    return { origin, dir };
  }

  /** Where a screen point lands on the globe: a hit if there is one, the limb if not. */
  _globeAt(ndcX, ndcY) {
    const { origin, dir } = this._rayAt(ndcX, ndcY);
    return rayEllipsoid(origin, dir) || rayGrazeEllipsoid(origin, dir);
  }

  _resolvePivot(e) {
    const [x, y] = this._ndc(e);
    if (this.hooks.pivotAt) {
      const p = this.hooks.pivotAt(x, y);
      if (p) return p;
    }
    return null;
  }

  _bind() {
    const dom = this.dom;

    dom.addEventListener(
      "wheel",
      (e) => {
        if (!this.enabled) return;
        e.preventDefault();
        // Normalise the three deltaMode units so a trackpad and a wheel feel the same.
        const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? dom.clientHeight : 1;
        const notches = (-e.deltaY * unit) / 120;
        this.zoom(Math.max(-6, Math.min(6, notches)), this._resolvePivot(e));
      },
      { passive: false },
    );

    dom.addEventListener("pointerdown", (e) => {
      if (!this.enabled || e.pointerType === "touch") return;
      dom.setPointerCapture?.(e.pointerId);
      const tilting = e.button === 1 || e.button === 2 || e.shiftKey || e.ctrlKey;
      const [x, y] = this._ndc(e);
      this._lastDragApply = 0;
      this._drag = {
        id: e.pointerId,
        x: e.clientX,
        y: e.clientY,
        tilting,
        moved: 0,
        t: performance.now(),
        // The point of the globe under the cursor at mouse-down. For the whole drag we solve for
        // the rotation that puts this point back under the cursor: that is the grab.
        grab: !tilting && this.geo ? this._globeAt(x, y).normalize() : null,
      };
      this.spinRate = 0;
      dom.classList.add("dragging");
    });

    dom.addEventListener("pointermove", (e) => {
      const d = this._drag;
      if (!d || d.id !== e.pointerId) {
        if (!d) this.hooks.onHover?.(...this._ndc(e), e);
        return;
      }
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      d.x = e.clientX;
      d.y = e.clientY;
      d.moved += Math.abs(dx) + Math.abs(dy);
      const now = performance.now();
      const dtS = Math.max(1e-3, (now - d.t) / 1000);
      d.t = now;

      if (d.tilting) {
        this.tiltDrag(dx, dy);
      } else if (this.geo && d.grab) {
        // Record where the cursor is and stop. The rotation is solved ONCE PER FRAME, in apply(),
        // against the camera the frame was actually drawn with.
        //
        // Doing it here instead looks correct and is not: a pointermove can fire several times
        // between two frames (120 Hz mice, coalesced events), and each one would solve the same
        // "put the grabbed point back under the cursor" problem against a camera matrix that is
        // still from the last render. Every correction after the first is therefore computed from a
        // camera that already moved but has not been redrawn, so they stack, overshoot, and the
        // globe jitters under the cursor. One correction per rendered frame cannot stack.
        d.ndc = this._ndc(e);
        d.dtS = dtS;
      } else {
        this.orbit(dx, dy);
      }
    });

    const end = (e) => {
      const d = this._drag;
      this._drag = null;
      dom.classList.remove("dragging");
      dom.releasePointerCapture?.(e.pointerId);
      // A press that barely moved is a click, not a drag. A drag that ended while still moving
      // keeps its spin; one that ended at rest does not, which is how a real fling behaves.
      if (d && d.moved < 5 && !d.tilting) {
        this.spinRate = 0;
        this.hooks.onClick?.(...this._ndc(e), e);
      } else if (d && performance.now() - d.t > 90) {
        this.spinRate = 0;
      }
    };
    dom.addEventListener("pointerup", end);
    dom.addEventListener("pointercancel", end);
    dom.addEventListener("contextmenu", (e) => e.preventDefault());

    // Double-click flies in to what was double-clicked: the quickest way across a range spanning
    // eight orders of magnitude, with the easing making the journey legible instead of a cut.
    dom.addEventListener("dblclick", (e) => {
      if (!this.enabled) return;
      const p = this._resolvePivot(e) || (this.geo ? this._globeAt(...this._ndc(e)) : null);
      if (!p) return;
      const r = Math.hypot(p.x, p.y, p.z);
      const surf = surfaceRadiusTowards(p);
      const offset = this.geo ? 0 : Math.max(0, surf - r);
      const alt = Math.max(this.minAltKm * 6, (this.distance - offset) * 0.38);
      if (this.geo) this.center.copy(p).normalize();
      else this.wantTarget.copy(p);
      this.wantDistance = alt + offset;
      this.follow = null;
      this.spinRate = 0;
    });

    // Keyboard, so the viewport works without a mouse at all.
    dom.addEventListener("keydown", (e) => {
      if (!this.enabled) return;
      const nudge = (dx, dy) => {
        if (this.geo) {
          // Move by a fixed fraction of what is on screen, so a key press covers a sensible
          // distance whether you are looking at a continent or at a runway.
          const k = 0.16 * Math.atan(this.distance > 0 ? 1 : 1);
          localFrame(this.center, this._e, this._n);
          const span = Math.min(0.6, this.distance / Math.max(1, surfaceRadiusTowards(this.center))) * k;
          const dir = this._v
            .copy(this.center)
            .addScaledVector(this._e, dx * span)
            .addScaledVector(this._n, -dy * span)
            .normalize();
          this.rotateBetween(dir, this.center.clone(), false, 0);
          this.center.copy(dir);
        } else {
          this.orbit(dx * 26, dy * 26);
        }
      };
      const keys = {
        ArrowLeft: () => nudge(-1, 0),
        ArrowRight: () => nudge(1, 0),
        ArrowUp: () => nudge(0, -1),
        ArrowDown: () => nudge(0, 1),
        "+": () => this.zoom(1, null),
        "=": () => this.zoom(1, null),
        "-": () => this.zoom(-1, null),
        _: () => this.zoom(-1, null),
      };
      const fn = keys[e.key];
      if (!fn) return;
      e.preventDefault();
      fn();
    });

    /* ------------------------------------------------------------ touch */
    const touches = new Map();
    let pinch = null;
    let touchGrab = null;

    dom.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "touch") return;
      touches.set(e.pointerId, e);
      this.spinRate = 0;
      if (touches.size === 1 && this.geo) {
        touchGrab = this._globeAt(...this._ndc(e)).normalize();
      } else if (touches.size === 2) {
        const [a, b] = [...touches.values()];
        pinch = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        touchGrab = null;
      }
    });
    dom.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "touch" || !touches.has(e.pointerId)) return;
      const prev = touches.get(e.pointerId);
      touches.set(e.pointerId, e);
      if (touches.size === 2 && pinch) {
        const [a, b] = [...touches.values()];
        const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        if (d > 0) {
          this.zoom(Math.log(d / pinch) * 3.2, null);
          pinch = d;
        }
      } else if (touches.size === 1 && touchGrab && this.geo) {
        this._drag = this._drag || { grab: touchGrab, tilting: false, moved: 99, id: e.pointerId };
        this._drag.grab = touchGrab;
        this._drag.ndc = this._ndc(e);
        this._drag.dtS = 1 / 60;
      } else if (touches.size === 1 && !this.geo) {
        this.orbit(e.clientX - prev.clientX, e.clientY - prev.clientY);
      }
    });
    const drop = (e) => {
      touches.delete(e.pointerId);
      if (touches.size < 2) pinch = null;
      if (touches.size === 0) {
        touchGrab = null;
        if (this._drag && this._drag.id === e.pointerId) this._drag = null;
      }
    };
    dom.addEventListener("pointerup", drop);
    dom.addEventListener("pointercancel", drop);
  }
}
