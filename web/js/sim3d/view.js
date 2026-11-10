/* The 3D simulator: scene, renderer, the two modes and the cameras.
 *
 * Two modes, as asked for:
 *   "earth"  the globe, the live catalog, launch sites and the ascent path, free to orbit and zoom
 *   "flight" the vehicle itself, flying the trajectory, with chase / onboard / pad cameras
 *
 * Precision: the FLOATING ORIGIN. A scene holding both a 6,378 km planet and a 60 m rocket cannot be
 * expressed in the Float32 attributes a GPU reads: at Earth-radius magnitudes a float32 resolves
 * about half a metre, so a nozzle modelled in its true place would visibly quiver. So nothing is ever
 * drawn at its absolute coordinate. Every position is written out relative to `origin`, which is the
 * Earth's centre in earth mode and EXACTLY the vehicle's position in flight mode. The rocket's own
 * vertices then live within tens of metres of zero, where float32 resolves microns, and the Earth
 * sits 6,378 km away where half a metre is far below what a 4,096-pixel texture can show anyway.
 *
 * Scene axes are ECEF axes and scene units are KILOMETRES (see globe.js).
 */

import * as THREE from "../../vendor/three/three.module.js";
import { R_EQ_KM, createGlobe, createStars } from "./globe.js";
import { OrbitCam, altitudeKm, rayEllipsoid } from "./controls.js";
import { CloseUpModel, OrbitTrail, SatelliteLayer } from "./objects.js";
import { createVehicle } from "./rocket.js";
import { geodeticToEcef, sunDirEcef, wrapLon } from "./geoframe.js";
import { floorIndex, interp, interpLon } from "../util.js";
import { scaleBar } from "./scalemath.js";
import { C, hex } from "../theme.js";

const FOV = 42;
/* Opening view, as a distance from the Earth's CENTRE in Earth radii: the whole disc then subtends
 * asin(1/3.0) = 19.5 degrees against a 21-degree half-frustum, so the planet fills the height with a
 * little room around it. Closer and the limb is cut off; further and it is a marble on a black page.
 *
 * The geographic camera measures its range from the GROUND POINT it is looking at, not from the
 * centre, so the altitude it wants is one Earth radius less. DEFAULT_ALT_KM is that conversion done
 * once, rather than at each of the three places that reset the view. */
const DEFAULT_RANGE_R = 3.0;
const DEFAULT_ALT_KM = (DEFAULT_RANGE_R - 1) * R_EQ_KM;

/** Local east/north/up basis at a geodetic position. Unit vectors in ECEF. */
function enuBasis(latDeg, lonDeg) {
  const la = (latDeg * Math.PI) / 180;
  const lo = (lonDeg * Math.PI) / 180;
  const sl = Math.sin(la), cl = Math.cos(la), so = Math.sin(lo), co = Math.cos(lo);
  return {
    east: new THREE.Vector3(-so, co, 0),
    north: new THREE.Vector3(-sl * co, -sl * so, cl),
    up: new THREE.Vector3(cl * co, cl * so, sl),
  };
}

export class Sim3D {
  constructor(canvas, hooks = {}) {
    this.canvas = canvas;
    this.hooks = hooks;
    this.mode = "earth";
    this.cameraMode = "orbit";
    this.origin = new THREE.Vector3(0, 0, 0);
    this.simMs = Date.now();
    this.replayT = 0;
    this.traj = null;
    this.trajExtra = null;
    this.vehicle = null;
    this.selected = null;
    this.groupsOn = new Set();
    this.ok = false;

    try {
      this.renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        // A logarithmic depth buffer is what lets one projection hold a near plane of a few metres
        // and a far plane past GEO without the z-fighting a linear buffer would give at that ratio.
        logarithmicDepthBuffer: true,
        powerPreference: "high-performance",
      });
    } catch (e) {
      this.error = e;
      return;
    }
    // Not pure black: a trace of blue reads as space rather than as a dead canvas, and it gives
    // the star field and the atmosphere something to sit against.
    this.renderer.setClearColor(0x070912, 1);
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    // Filmic tone mapping with a little headroom: the bright limb and the plume both run well past
    // 1.0, and clipping them to white is what makes a space scene look flat.
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.35;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(FOV, 1, 1, 1e7);
    this.camera.up.set(0, 0, 1);

    this.sunLight = new THREE.DirectionalLight(0xfff4e0, 3.4);
    // Ambient stands in for earthshine and starlight: without it the shadowed side of a rocket is
    // pure black, which is technically defensible in vacuum and unreadable on a screen.
    this.scene.add(this.sunLight, new THREE.AmbientLight(0x6b5e4c, 0.85));

    this.globe = createGlobe({ onProgress: (p) => this.hooks.onTexture?.(p) });
    this.scene.add(this.globe.group);

    this.stars = createStars();
    this.scene.add(this.stars.points);

    this.trail = new OrbitTrail(C.ochre);
    this.scene.add(this.trail.line);

    this.closeUp = new CloseUpModel();
    this.scene.add(this.closeUp.group);

    this._buildTrajectoryObjects();
    this._buildMarkers();

    this.cam = new OrbitCam(this.camera, canvas, {
      pivotAt: (x, y) => this._pivotAt(x, y),
      onClick: (x, y) => this._click(x, y),
      onHover: (x, y) => this._hover(x, y),
    });
    // Geographic camera on the globe (grab-and-drag, like Google Earth); the flight mode swaps it
    // for a free orbit around the vehicle.
    this.cam.geo = true;
    this.cam.wantDistance = DEFAULT_ALT_KM;
    this.cam.snap();

    this._tmp = new THREE.Vector3();
    this._tmp2 = new THREE.Vector3();
    this._camAbs = new THREE.Vector3();
    this.ok = true;
    this.resize();
  }

  /* ---------------------------------------------------------------- scene furniture */

  _buildTrajectoryObjects() {
    const mkLine = (color, opacity, max) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(max * 3), 3));
      g.setDrawRange(0, 0);
      g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e6);
      const m = new THREE.LineBasicMaterial({ color: hex(color), transparent: true, opacity, depthWrite: false });
      const l = new THREE.Line(g, m);
      l.frustumCulled = false;
      return l;
    };
    this.pathLine = mkLine(C.ochre, 0.95, 4096);
    this.pathLine.name = "ascent-path";
    this.groundLine = mkLine(C.clay, 0.42, 4096);
    this.groundLine.name = "ground-track";
    this.scene.add(this.pathLine, this.groundLine);
    this._pathAbs = null;
    this._groundAbs = null;
  }

  _buildMarkers() {
    // Launch site and the four contract events, as one small point cloud with per-point colour.
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(8 * 3), 3));
    g.setAttribute("aColor", new THREE.BufferAttribute(new Float32Array(8 * 3), 3));
    g.setAttribute("aSize", new THREE.BufferAttribute(new Float32Array(8), 1));
    g.setDrawRange(0, 0);
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e6);
    const mat = new THREE.ShaderMaterial({
      uniforms: { pixelRatio: { value: 1 } },
      vertexShader: /* glsl */ `
        #include <common>
        #include <logdepthbuf_pars_vertex>
        attribute vec3 aColor; attribute float aSize;
        uniform float pixelRatio; varying vec3 vC;
        void main() {
          vC = aColor;
          gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
          gl_PointSize = aSize * pixelRatio;
          #include <logdepthbuf_vertex>
        }`,
      fragmentShader: /* glsl */ `
        precision mediump float;
        #include <common>
        #include <logdepthbuf_pars_fragment>
        varying vec3 vC;
        void main() {
          #include <logdepthbuf_fragment>
          vec2 d = gl_PointCoord - vec2(0.5);
          float r = length(d) * 2.0;
          float ring = smoothstep(1.0, 0.82, r) * smoothstep(0.42, 0.62, r);
          float dot_ = smoothstep(0.34, 0.10, r);
          float a = max(ring, dot_);
          if (a <= 0.02) discard;
          gl_FragColor = vec4(vC, a);
        }`,
      transparent: true,
      depthWrite: false,
    });
    this.markers = new THREE.Points(g, mat);
    this.markers.name = "markers";
    this.markers.frustumCulled = false;
    this.scene.add(this.markers);
    this._markerAbs = [];

    // The vehicle's current position in earth mode, so the ascent reads as a moving thing.
    const vg = new THREE.BufferGeometry();
    vg.setAttribute("position", new THREE.BufferAttribute(new Float32Array(3), 3));
    vg.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e6);
    this.vehicleDot = new THREE.Points(
      vg,
      new THREE.ShaderMaterial({
        uniforms: { pixelRatio: { value: 1 } },
        vertexShader: /* glsl */ `
          #include <common>
          #include <logdepthbuf_pars_vertex>
          uniform float pixelRatio;
          void main() {
            gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
            gl_PointSize = 9.0 * pixelRatio;
            #include <logdepthbuf_vertex>
          }`,
        fragmentShader: /* glsl */ `
          precision mediump float;
          #include <common>
          #include <logdepthbuf_pars_fragment>
          void main() {
            #include <logdepthbuf_fragment>
            vec2 d = gl_PointCoord - vec2(0.5);
            float r = length(d) * 2.0;
            float a = smoothstep(1.0, 0.2, r);
            if (a <= 0.02) discard;
            gl_FragColor = vec4(1.0, 0.92, 0.78, a);
          }`,
        transparent: true,
        depthWrite: false,
      }),
    );
    this.vehicleDot.frustumCulled = false;
    this.vehicleDot.visible = false;
    this.scene.add(this.vehicleDot);
  }

  /* ---------------------------------------------------------------- data in */

  setCatalog(catalog) {
    if (this.sats) {
      this.scene.remove(this.sats.points);
      this.sats.dispose();
    }
    this.sats = new SatelliteLayer(catalog);
    this.catalog = catalog;
    this.scene.add(this.sats.points);
    if (this.groupsOn.size) this.sats.setGroups(this.groupsOn);
    this._syncPixelRatio();
  }

  /** A `positions` reply from the SGP4 worker. */
  setPositions(msg) {
    if (!this.sats) return;
    this.sats.setPositions(msg.lat, msg.lon, msg.alt);
    this.sats.refresh(this.origin);
  }

  /** A `track3` reply: flat (lat, lon, altKm) triples for the selected object's orbit. */
  setTrack(pts) {
    if (!pts || pts.length < 6) { this.trail.clear(); return; }
    this.trail.set(pts);
    this.trail.refresh(this.origin);
  }

  setGroups(set) {
    this.groupsOn = set;
    this.sats?.setGroups(set);
  }

  setSelected(i) {
    this.selected = i;
    if (i == null) this.trail.clear();
  }

  /**
   * Load a trajectory (contract-shaped) and, when the browser simulator produced it, the attitude
   * series beside it. Builds the 3D path, the ground track, the event markers and the vehicle.
   */
  setTrajectory(traj, extra, rocket) {
    this.traj = traj;
    this.trajExtra = extra || null;
    this.replayT = 0;

    if (this.vehicle) {
      this.scene.remove(this.vehicle.group);
      this.vehicle.dispose();
      this.vehicle = null;
    }
    if (rocket) {
      this.vehicle = createVehicle(rocket, { scale: 1 / 1000 });
      this.vehicle.group.visible = false;
      this.scene.add(this.vehicle.group);
    }

    if (!traj) {
      this.pathLine.geometry.setDrawRange(0, 0);
      this.groundLine.geometry.setDrawRange(0, 0);
      this.markers.geometry.setDrawRange(0, 0);
      this.vehicleDot.visible = false;
      this._pathAbs = null;
      return;
    }

    const n = traj.t_s.length;
    const path = new Float64Array(n * 3);
    const ground = new Float64Array(n * 3);
    const p = [0, 0, 0];
    for (let i = 0; i < n; i++) {
      geodeticToEcef(traj.lat_deg[i], traj.lon_deg[i], traj.alt_m[i], p);
      path[i * 3] = p[0] / 1000; path[i * 3 + 1] = p[1] / 1000; path[i * 3 + 2] = p[2] / 1000;
      // The ground track is the same latitude and longitude at zero altitude: where the vehicle is
      // over, not where it is.
      geodeticToEcef(traj.lat_deg[i], traj.lon_deg[i], 0, p);
      ground[i * 3] = p[0] / 1000; ground[i * 3 + 1] = p[1] / 1000; ground[i * 3 + 2] = p[2] / 1000;
    }
    this._pathAbs = path;
    this._groundAbs = ground;
    this._pathCount = n;

    // Markers: the launch point, then the four events.
    const abs = [];
    abs.push({ pos: [path[0], path[1], path[2]], color: C.sage, size: 11 });
    for (const ev of traj.events || []) {
      const i = Math.max(0, Math.min(n - 1, traj.t_s.indexOf(ev.t_s) >= 0 ? traj.t_s.indexOf(ev.t_s) : ev.t_s));
      abs.push({ pos: [path[i * 3], path[i * 3 + 1], path[i * 3 + 2]], color: C.ochre, size: 9, name: ev.name });
    }
    this._markerAbs = abs;
    const mc = this.markers.geometry.getAttribute("aColor");
    const ms = this.markers.geometry.getAttribute("aSize");
    const col = new THREE.Color();
    abs.forEach((m, i) => {
      col.setHex(hex(m.color));
      mc.array[i * 3] = col.r; mc.array[i * 3 + 1] = col.g; mc.array[i * 3 + 2] = col.b;
      ms.array[i] = m.size;
    });
    mc.needsUpdate = true;
    ms.needsUpdate = true;
    this.markers.geometry.setDrawRange(0, abs.length);

    this._refreshTrajectory();
    this.vehicleDot.visible = this.mode === "earth";
  }

  /** Rewrite the trajectory geometry relative to the current origin. */
  _refreshTrajectory() {
    if (!this._pathAbs) return;
    const { x: ox, y: oy, z: oz } = this.origin;
    const n = this._pathCount;
    const pa = this.pathLine.geometry.getAttribute("position");
    const ga = this.groundLine.geometry.getAttribute("position");
    const cap = Math.min(n, pa.array.length / 3);
    for (let i = 0; i < cap; i++) {
      pa.array[i * 3] = this._pathAbs[i * 3] - ox;
      pa.array[i * 3 + 1] = this._pathAbs[i * 3 + 1] - oy;
      pa.array[i * 3 + 2] = this._pathAbs[i * 3 + 2] - oz;
      ga.array[i * 3] = this._groundAbs[i * 3] - ox;
      ga.array[i * 3 + 1] = this._groundAbs[i * 3 + 1] - oy;
      ga.array[i * 3 + 2] = this._groundAbs[i * 3 + 2] - oz;
    }
    pa.needsUpdate = true;
    ga.needsUpdate = true;
    this.pathLine.geometry.setDrawRange(0, cap);
    this.groundLine.geometry.setDrawRange(0, cap);

    const ma = this.markers.geometry.getAttribute("position");
    this._markerAbs.forEach((m, i) => {
      ma.array[i * 3] = m.pos[0] - ox;
      ma.array[i * 3 + 1] = m.pos[1] - oy;
      ma.array[i * 3 + 2] = m.pos[2] - oz;
    });
    ma.needsUpdate = true;
  }

  /* ---------------------------------------------------------------- state in */

  setTime(simMs) { this.simMs = simMs; }
  setReplayT(t) { this.replayT = t; }

  setMode(mode) {
    if (mode === this.mode) return;
    this.mode = mode;
    if (mode === "flight") {
      this.cam.setGeo(false);
      this.cameraMode = "chase";
      this.vehicleDot.visible = false;
      if (this.vehicle) this.vehicle.group.visible = true;
    } else {
      this.cameraMode = "orbit";
      if (this.vehicle) this.vehicle.group.visible = false;
      this.vehicleDot.visible = !!this.traj;
      this._setOrigin(0, 0, 0);
      // Come back to the globe from wherever the flight ended, but EASE the range: the travel is
      // what tells you the two modes are the same world seen from different distances.
      this.cam.setGeo(true);
      this.cam.wantDistance = DEFAULT_ALT_KM;
    }
    this.hooks.onMode?.(mode, this.cameraMode);
  }

  setCameraMode(m) {
    this.cameraMode = m;
    if (this.mode === "flight") this.cam.setGeo(false);
    if (m === "orbit" && this.mode === "flight") {
      // Hand control to the user, centred on the vehicle at a sensible stand-off.
      const st = this._vehicleState();
      if (st) {
        this.cam.target.copy(st.posKm);
        this.cam.distance = Math.max(0.12, (this.vehicle?.dims.totalLength || 60) * 0.006);
      }
    }
    this.hooks.onMode?.(this.mode, m);
  }

  /** Point the camera at a catalog object. */
  goToObject(i) {
    const p = this.sats?.positionOf(i);
    if (!p) return false;
    if (this.cam.geo) {
      // On the globe, "go to" means bring it to the middle: the object's ground point becomes the
      // centre and the range drops to roughly its own altitude.
      this.cam.center.copy(p).normalize();
      this.cam.wantDistance = Math.max(200, altitudeKm(p) * 2.2);
      this.cam.spinRate = 0;
    } else {
      this.cam.goTo(p, Math.max(0.6, p.length() * 0.004));
      this.cam.followPoint(() => this.sats.positionOf(i));
    }
    return true;
  }

  /** Point the camera at a launch site. */
  goToSite(latDeg, lonDeg, altKm = 60) {
    const p = [0, 0, 0];
    geodeticToEcef(latDeg, lonDeg, 0, p);
    this.cam.goTo(new THREE.Vector3(p[0] / 1000, p[1] / 1000, p[2] / 1000), altKm);
  }

  /** Level the view: tilt and heading back to straight down and north up. */
  levelView() {
    this.cam.wantTilt = 0;
    this.cam.wantHeading = 0;
  }

  resetView() {
    this.cam.setGeo(true);
    this.cam.wantDistance = DEFAULT_ALT_KM;
    this.cam.wantTilt = 0;
    this.cam.wantHeading = 0;
    this.cam.spinRate = 0;
    this.cam.follow = null;
    this._oriented = false; // face the daylit side again
  }

  /* ---------------------------------------------------------------- picking */

  _pivotAt(ndcX, ndcY) {
    // An object under the cursor wins; otherwise the globe; otherwise empty space.
    if (this.sats && this.mode === "earth") {
      const i = this.sats.pick(ndcX, ndcY, this.camera, this._size(), this.groupsOn, 12);
      if (i >= 0) return this.sats.positionOf(i);
    }
    const ray = this._ray(ndcX, ndcY);
    const hit = rayEllipsoid(this._camAbs, ray);
    return hit || null;
  }

  _ray(ndcX, ndcY) {
    const v = this._tmp2.set(ndcX, ndcY, 0.5).unproject(this.camera);
    return v.sub(this.camera.position).normalize();
  }

  _click(ndcX, ndcY) {
    if (!this.sats) return;
    const i = this.sats.pick(ndcX, ndcY, this.camera, this._size(), this.groupsOn, 14);
    this.hooks.onPick?.(i >= 0 ? i : null);
  }

  _hover(ndcX, ndcY) {
    if (!this.sats || !this.hooks.onHoverObject) return;
    const i = this.sats.pick(ndcX, ndcY, this.camera, this._size(), this.groupsOn, 10);
    this.hooks.onHoverObject(i >= 0 ? i : null, ndcX, ndcY);
  }

  /* ---------------------------------------------------------------- frame */

  _size() {
    return { w: this.canvas.clientWidth || 1, h: this.canvas.clientHeight || 1 };
  }

  _syncPixelRatio() {
    const pr = this.renderer.getPixelRatio();
    if (this.sats) this.sats.material.uniforms.pixelRatio.value = pr;
    this.stars.material.uniforms.pixelRatio.value = pr;
    this.markers.material.uniforms.pixelRatio.value = pr;
    this.vehicleDot.material.uniforms.pixelRatio.value = pr;
  }

  resize() {
    if (!this.ok) return;
    const { w, h } = this._size();
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / Math.max(1, h);
    this.camera.updateProjectionMatrix();
    this._syncPixelRatio();
  }

  _setOrigin(x, y, z) {
    if (this.origin.x === x && this.origin.y === y && this.origin.z === z) return;
    this.origin.set(x, y, z);
    this.globe.setOrigin(this.origin);
    this.sats?.refresh(this.origin);
    this.trail.refresh(this.origin);
    this._refreshTrajectory();
  }

  /**
   * Where the vehicle is and how it is pointing, at the current replay time.
   *
   * Position is interpolated from the trajectory contract. ATTITUDE comes from the simulator's own
   * pitch program when the browser produced the flight (`extra.pitch_deg`), which is the only place
   * it actually exists; for a trajectory loaded from a file there is no attitude in the contract, so
   * the body axis is taken from the velocity direction instead and the UI says it is derived. Either
   * way the vehicle has no roll and no angle of attack, because the 2D point-mass model has neither.
   */
  _vehicleState() {
    const d = this.traj;
    if (!d) return null;
    const T = this.replayT;
    const lat = interp(d.t_s, d.lat_deg, T);
    const lon = interpLon(d.t_s, d.lon_deg, T);
    const altM = interp(d.t_s, d.alt_m, T);
    const speed = interp(d.t_s, d.speed_mps, T);
    const p = [0, 0, 0];
    geodeticToEcef(lat, lon, altM, p);
    const posKm = new THREE.Vector3(p[0] / 1000, p[1] / 1000, p[2] / 1000);

    // Ground-track bearing from two nearby samples: the direction the vehicle is travelling over the
    // surface, which is the horizontal part of its body axis.
    const dt = 2;
    const lat2 = interp(d.t_s, d.lat_deg, Math.min(d.t_s[d.t_s.length - 1], T + dt));
    const lon2 = interpLon(d.t_s, d.lon_deg, Math.min(d.t_s[d.t_s.length - 1], T + dt));
    const basis = enuBasis(lat, lon);
    const dLat = (lat2 - lat) * Math.PI / 180;
    const dLon = (wrapLon(lon2 - lon)) * Math.PI / 180 * Math.cos((lat * Math.PI) / 180);
    let horiz = new THREE.Vector3()
      .addScaledVector(basis.north, dLat)
      .addScaledVector(basis.east, dLon);
    if (horiz.lengthSq() < 1e-18) horiz = basis.east.clone();
    horiz.normalize();

    let pitchDeg;
    let derived;
    if (this.trajExtra?.pitch_deg) {
      pitchDeg = interp(d.t_s, this.trajExtra.pitch_deg, T);
      derived = false;
    } else {
      // Flight-path angle from the altitude rate against the ground speed: the best available
      // stand-in when the file carries no attitude.
      const alt2 = interp(d.t_s, d.alt_m, Math.min(d.t_s[d.t_s.length - 1], T + dt));
      const climb = (alt2 - altM) / dt;
      pitchDeg = (Math.atan2(climb, Math.max(1, speed)) * 180) / Math.PI;
      derived = true;
    }
    const pr = (pitchDeg * Math.PI) / 180;
    const axis = new THREE.Vector3()
      .addScaledVector(horiz, Math.cos(pr))
      .addScaledVector(basis.up, Math.sin(pr))
      .normalize();

    // Stage and thrust are step functions, so take the sample at or before T rather than
    // interpolating between two of them -- half a stage index is not a state the vehicle is ever in.
    const ev = (name) => (d.events || []).find((e) => e.name === name);
    let stage;
    let thrustN;
    if (this.trajExtra?.stage) {
      const i = floorIndex(d.t_s, T);
      stage = this.trajExtra.stage[i];
      thrustN = this.trajExtra.thrust_n[i];
    } else {
      // No attitude or thrust in the file: fall back to the contract's own event times, which do say
      // when the first stage cut off and when the upper stage reached orbit.
      const meco = ev("meco")?.t_s ?? Infinity;
      const sepT = ev("stage_sep")?.t_s ?? meco;
      const ins = ev("insertion")?.t_s ?? Infinity;
      stage = T <= meco ? 1 : T < sepT ? 0 : T < ins ? 2 : 0;
      const st1 = this.vehicle?.rocket?.stages?.[0];
      const st2 = this.vehicle?.rocket?.stages?.[1];
      thrustN = stage === 1 ? (st1?.thrust_n ?? 0) : stage === 2 ? (st2?.thrust_n ?? 0) : 0;
    }
    const sep = ev("stage_sep");

    return {
      posKm, axis, up: basis.up, horiz, lat, lon, altM, speed, pitchDeg, derived, stage, thrustN,
      separated: sep ? T >= sep.t_s : false,
      massKg: interp(d.t_s, d.mass_kg, T),
      qPa: interp(d.t_s, d.q_pa, T),
    };
  }

  /** Place the camera for the flight-mode cameras that are not free orbit. */
  _placeFlightCamera(st) {
    const o = this.origin;
    if (this.cameraMode !== "pad" && this.camera.fov !== FOV) {
      this.camera.fov = FOV;
      this._padZoom = 1;
      this.camera.updateProjectionMatrix();
    }
    const len = (this.vehicle?.dims.totalLength || 60) / 1000; // km
    const rel = (v) => new THREE.Vector3(v.x - o.x, v.y - o.y, v.z - o.z);

    if (this.cameraMode === "chase") {
      // Uprange and above, framing the vehicle against the Earth.
      //
      // The offset is along the GROUND TRACK, not along the body axis. Early in the flight the
      // vehicle is nearly vertical, so "behind" in body axis means directly underneath: the camera
      // ends up below the rocket looking up its tail at empty sky, with the planet out of frame
      // entirely. Offsetting uprange and lifting above keeps the ground in shot the whole way,
      // which is what a chase view is for.
      // The vertical offset swings with altitude, and that is a framing decision worth stating.
      // Down low the camera sits slightly BELOW the vehicle, so the backdrop is horizon, air and
      // space: that is the shot every launch is watched in, and it is the only one that works
      // there, because the ground five kilometres under a rocket is a single texel of dark ocean
      // however it is lit. High up the camera moves ABOVE, so the backdrop becomes the lit Earth,
      // which by then is the thing worth looking at.
      const f = Math.min(1, Math.max(0, (st.altM / 1000 - 3) / 90));
      const up = st.up.clone().multiplyScalar(len * (-0.3 + 1.7 * f));
      const back = st.horiz.clone().multiplyScalar(-len * 2.4);
      const side = new THREE.Vector3().crossVectors(st.horiz, st.up).normalize().multiplyScalar(len * 1.3);
      const cam = st.posKm.clone().add(back).add(up).add(side);
      this.camera.near = Math.max(0.0015, len * 0.05);
      this.camera.far = 2e6;
      this.camera.updateProjectionMatrix();
      this.camera.up.copy(st.up);
      this.camera.position.copy(rel(cam));
      const look = rel(st.posKm);
      this.camera.lookAt(look.x, look.y, look.z);
      return cam;
    }

    if (this.cameraMode === "onboard") {
      // At the nose, looking forward along the body axis, tilted down so the Earth stays in frame --
      // which is what an onboard camera actually shows for most of an ascent.
      const nose = st.posKm.clone().addScaledVector(st.axis, len * 0.55);
      const aim = nose
        .clone()
        .addScaledVector(st.axis, len * 40)
        .addScaledVector(st.up, -len * 14);
      this.camera.near = Math.max(0.0015, len * 0.03);
      this.camera.far = 2e6;
      this.camera.updateProjectionMatrix();
      this.camera.up.copy(st.up);
      this.camera.position.copy(rel(nose));
      const look = rel(aim);
      this.camera.lookAt(look.x, look.y, look.z);
      return nose;
    }

    // "pad": a fixed tracking camera at the launch site, 2 km back, following the vehicle up.
    const d = this.traj;
    const p = [0, 0, 0];
    geodeticToEcef(d.lat_deg[0], d.lon_deg[0], d.alt_m[0], p);
    const pad = new THREE.Vector3(p[0] / 1000, p[1] / 1000, p[2] / 1000);
    const padBasis = enuBasis(d.lat_deg[0], d.lon_deg[0]);
    const camPos = pad
      .clone()
      .addScaledVector(padBasis.east, -1.4)
      .addScaledVector(padBasis.north, -1.6)
      .addScaledVector(padBasis.up, 0.03);
    void padBasis;

    // A pad camera that does not zoom is useless after about twenty seconds: at T+90 the vehicle is
    // forty kilometres away and a 65 m rocket is under two pixels wide. Real range-tracking cameras
    // solve this with very long lenses, so this one does too -- the field of view narrows to keep
    // the vehicle about a fifth of the frame, down to the equivalent of a 2,000 mm lens, and the
    // telemetry reports the magnification so the view never silently lies about its scale.
    const dist = camPos.distanceTo(st.posKm);
    const lenKm = (this.vehicle?.dims.totalLength || 60) / 1000;
    const wantHalf = Math.atan(lenKm / 2 / Math.max(1e-6, dist) / 0.2);
    const half = Math.max((0.6 * Math.PI) / 360, Math.min((FOV * Math.PI) / 360, wantHalf));
    this.camera.fov = (half * 360) / Math.PI;
    this._padZoom = FOV / this.camera.fov;
    this.camera.near = Math.max(0.005, dist * 0.02);
    this.camera.far = 2e6;
    this.camera.updateProjectionMatrix();
    this.camera.up.copy(padBasis.up);
    this.camera.position.copy(rel(camPos));
    const look = rel(st.posKm);
    this.camera.lookAt(look.x, look.y, look.z);
    return camPos;
  }

  /** Render one frame and return the telemetry the panel and HUD display. */
  frame(timeS) {
    if (!this.ok) return null;
    const dt = this._lastT ? Math.min(0.25, Math.max(1 / 240, timeS - this._lastT)) : 1 / 60;
    this._lastT = timeS;

    const tUnix = this.simMs / 1000;
    const sun = sunDirEcef(tUnix);
    const st = this._vehicleState();

    // First frame: face the daylit hemisphere, offset enough that the terminator and some of the
    // night side are in shot. Otherwise the opening view is whatever side of the planet happens to
    // be in darkness, which on a dark page reads as a bug rather than as midnight.
    if (!this._oriented) {
      this._oriented = true;
      // Put the screen centre a little east of the subsolar point and a little off the sun's
      // latitude, so the opening view has a lit face, a terminator and some night in it.
      const s0 = new THREE.Vector3(sun[0], sun[1], sun[2]).normalize();
      const axis = new THREE.Vector3(0, 0, 1).cross(s0).normalize();
      const c = s0.clone().applyAxisAngle(new THREE.Vector3(0, 0, 1), -0.52);
      if (axis.lengthSq() > 1e-9) c.applyAxisAngle(axis, 0.3);
      this.cam.center.copy(c).normalize();
      this.cam.wantTilt = this.cam.tilt = 0;
      this.cam.wantHeading = this.cam.heading = 0;
    }

    // --- origin and camera
    if (this.mode === "flight" && st) {
      this._setOrigin(st.posKm.x, st.posKm.y, st.posKm.z);
      if (this.cameraMode === "orbit") {
        this.cam.followPoint(() => this._vehicleState()?.posKm || null);
        this._camAbs.copy(this.cam.apply(this.origin, dt));
      } else {
        this._camAbs.copy(this._placeFlightCamera(st));
      }
    } else {
      this._setOrigin(0, 0, 0);
      if (this.camera.fov !== FOV) {
        this.camera.fov = FOV;
        this._padZoom = 1;
        this.camera.updateProjectionMatrix();
      }
      this._camAbs.copy(this.cam.apply(this.origin, dt));
    }

    // --- sun and sky
    this.globe.update(sun, this.camera.position, altitudeKm(this._camAbs));
    this.stars.update(this.camera);
    this.sunLight.position.set(sun[0], sun[1], sun[2]).multiplyScalar(1e6);
    this.sunLight.target.position.set(0, 0, 0);

    // --- the vehicle
    let plume = null;
    if (this.vehicle && st) {
      const inFlight = this.mode === "flight";
      this.vehicle.group.visible = inFlight;
      if (inFlight) {
        this.vehicle.group.position.set(
          st.posKm.x - this.origin.x,
          st.posKm.y - this.origin.y,
          st.posKm.z - this.origin.z,
        );
        // Local +Z is the body axis; pick any consistent perpendicular for the other two.
        const zAxis = st.axis.clone();
        const xAxis = new THREE.Vector3().crossVectors(st.up, zAxis);
        if (xAxis.lengthSq() < 1e-12) xAxis.copy(st.horiz);
        xAxis.normalize();
        const yAxis = new THREE.Vector3().crossVectors(zAxis, xAxis).normalize();
        this.vehicle.group.quaternion.setFromRotationMatrix(
          new THREE.Matrix4().makeBasis(xAxis, yAxis, zAxis),
        );
        plume = this.vehicle.update({
          stage: st.stage,
          thrustN: st.thrustN,
          altM: st.altM,
          timeS,
          separated: st.separated,
        });
      }
    }

    // --- vehicle dot in earth mode
    if (this.mode === "earth" && st && this.traj) {
      const a = this.vehicleDot.geometry.getAttribute("position");
      a.array[0] = st.posKm.x - this.origin.x;
      a.array[1] = st.posKm.y - this.origin.y;
      a.array[2] = st.posKm.z - this.origin.z;
      a.needsUpdate = true;
      this.vehicleDot.visible = true;
    } else {
      this.vehicleDot.visible = false;
    }

    // --- close-up of the selected catalog object
    let closeUp = { shown: false };
    if (this.selected != null && this.sats) {
      const p = this.sats.positionOf(this.selected, this._tmp);
      const g = this.catalog?.groups[this.sats.groupIdx[this.selected]];
      closeUp = this.closeUp.update(
        g, p, this.origin, this.camera, this.camera.position, this._size().h, FOV,
      );
    } else {
      this.closeUp.update(null, null, this.origin, this.camera, this.camera.position, this._size().h, FOV);
    }

    // In flight mode the path is drawn only as far as the vehicle has flown. The full arc reaches
    // orbit, so from beside the rocket it is a pair of enormous curves sweeping across the frame
    // with nothing to say; a trail behind the vehicle shows where it has been, which is the useful
    // half of the same line. On the globe the whole path is the point, so it stays whole.
    if (this._pathAbs) {
      const nPath = this.mode === "flight"
        ? Math.max(2, Math.min(this._pathCount, Math.floor(this.replayT) + 2))
        : this._pathCount;
      this.pathLine.geometry.setDrawRange(0, nPath);
      this.groundLine.geometry.setDrawRange(0, nPath);
      this.groundLine.visible = this.mode === "earth";
    }

    this.renderer.render(this.scene, this.camera);
    this._everRendered = true;

    // --- telemetry for the DOM
    const { h, w } = this._size();
    const camAlt = altitudeKm(this._camAbs);
    const pivotAlt = this.cam.pivotAltitudeKm();
    const bar = scaleBar(this.mode === "flight" ? Math.max(0.01, camAlt) : Math.max(1, this.cam.distance), FOV, h);
    return {
      mode: this.mode,
      cameraMode: this.cameraMode,
      camAltKm: camAlt,
      pivotAltKm: pivotAlt,
      scaleBar: bar,
      viewport: { w, h },
      vehicle: st,
      plume,
      closeUp,
      padZoom: this.cameraMode === "pad" ? this._padZoom || 1 : 1,
      sunLit: st ? sun[0] * st.posKm.x + sun[1] * st.posKm.y + sun[2] * st.posKm.z > 0 : null,
    };
  }

  dispose() {
    if (!this.ok) return;
    this.globe.dispose();
    this.stars.dispose();
    this.trail.dispose();
    this.closeUp.dispose();
    this.sats?.dispose();
    this.vehicle?.dispose();
    this.renderer.dispose();
  }
}
