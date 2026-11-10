/* Catalog objects in 3D: the whole-catalog point layer, orbit trails, and the close-up models.
 *
 * Honesty, because this is where it is easiest to mislead. The POSITIONS are real: SGP4 on real
 * CelesTrak elements, propagated in the existing Web Worker, kilometre-level and degrading with
 * element age. The SHAPES are not: the catalog carries no size, mass or geometry, so a close-up model
 * is a schematic for its class (station, satellite, debris), the same shape for every member of that
 * class. The UI says so wherever a model is drawn. We do not invent a Starlink that looks like a
 * Starlink; we draw "a satellite" and label the class.
 *
 * Scene units are KILOMETRES.
 */

import * as THREE from "../../vendor/three/three.module.js";
import { geodeticToEcef } from "./geoframe.js";
import { GROUP_COLORS, hex } from "../theme.js";
import { detailLevel } from "./scalemath.js";

/* Representative sizes per class, in METRES, used only for the close-up models and for the true-scale
 * test that decides when to draw one. Rounded public figures for a typical member of the class, not
 * the real dimensions of any particular object. */
export const CLASS_SIZE_M = {
  stations: { span: 109, bus: 12, label: "station (schematic, ~100 m class)" },
  "gps-ops": { span: 17, bus: 2.4, label: "navigation satellite (schematic)" },
  geo: { span: 26, bus: 3.2, label: "geostationary satellite (schematic)" },
  active: { span: 12, bus: 2.0, label: "satellite (schematic)" },
  starlink: { span: 9, bus: 1.6, label: "small satellite (schematic)" },
  debris: { span: 0.6, bus: 0.4, label: "debris fragment (schematic)" },
};

/** Pixel radius each group gets as a marker. Debris is deliberately the faintest and smallest. */
const MARKER_PX = { stations: 6.0, "gps-ops": 4.4, geo: 4.2, active: 3.6, starlink: 3.2, debris: 2.6 };
const MARKER_ALPHA = { stations: 1, "gps-ops": 0.95, geo: 0.92, active: 0.8, starlink: 0.8, debris: 0.5 };

const POINT_VERT = /* glsl */ `
  #include <common>
  #include <logdepthbuf_pars_vertex>
  attribute float aSize;
  attribute float aOn;
  attribute vec3 aColor;
  attribute float aAlpha;
  uniform float pixelRatio;
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vColor = aColor;
    vAlpha = aAlpha;
    vec4 mv = viewMatrix * modelMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    // A FIXED pixel size, not a size that shrinks with range. That is the minimum-on-screen-size
    // rule from scalemath.js applied on the GPU: a 10 m satellite is far below one pixel from any
    // distance that shows the globe, so a marker is the only honest way to show it is there.
    gl_PointSize = aSize * pixelRatio * aOn;
    if (aOn < 0.5) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);  // off-screen: cheaper than a draw call
    #include <logdepthbuf_vertex>
  }
`;

const POINT_FRAG = /* glsl */ `
  precision mediump float;
  #include <common>
  #include <logdepthbuf_pars_fragment>
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    #include <logdepthbuf_fragment>
    vec2 d = gl_PointCoord - vec2(0.5);
    float r = length(d) * 2.0;
    // A bright core inside a dark rim. A plain coloured dot disappears against sunlit land as
    // readily as a dark one disappears against space; the rim means the marker reads on both.
    float core = smoothstep(0.62, 0.3, r);
    float halo = smoothstep(1.0, 0.6, r) * (1.0 - core);
    float a = (core + halo * 0.85) * vAlpha;
    if (a <= 0.02) discard;
    gl_FragColor = vec4(mix(vec3(0.02, 0.02, 0.03), vColor, core), a);
  }
`;

/**
 * Every catalog object as one draw call.
 *
 * The SGP4 worker hands over three Float32Arrays of (lat, lon, alt); this converts them to ECEF
 * kilometres and writes straight into the position attribute. 6,819 objects is one buffer upload per
 * worker reply, not per frame.
 */
export class SatelliteLayer {
  constructor(catalog) {
    this.groups = catalog.groups;
    const n = catalog.objects.length;
    this.n = n;
    this.groupIdx = Uint8Array.from(catalog.objects, (o) => o[2]);

    const pos = new Float32Array(n * 3);
    const col = new Float32Array(n * 3);
    const size = new Float32Array(n);
    const alpha = new Float32Array(n);
    const on = new Float32Array(n);

    const c = new THREE.Color();
    for (let i = 0; i < n; i++) {
      const g = this.groups[this.groupIdx[i]];
      c.setHex(hex(GROUP_COLORS[g] || "#8da3c2"));
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
      size[i] = MARKER_PX[g] ?? 2.2;
      alpha[i] = MARKER_ALPHA[g] ?? 0.8;
      on[i] = 1;
      // Park everything inside the Earth until the first worker reply, so nothing flashes at the
      // origin on the first frame.
      pos[i * 3] = 0; pos[i * 3 + 1] = 0; pos[i * 3 + 2] = 0;
    }

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aColor", new THREE.BufferAttribute(col, 3));
    g.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    g.setAttribute("aAlpha", new THREE.BufferAttribute(alpha, 1));
    g.setAttribute("aOn", new THREE.BufferAttribute(on, 1));
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e6);

    this.material = new THREE.ShaderMaterial({
      uniforms: { pixelRatio: { value: 1 } },
      vertexShader: POINT_VERT,
      fragmentShader: POINT_FRAG,
      transparent: true,
      depthWrite: false,
    });
    this.points = new THREE.Points(g, this.material);
    this.points.name = "catalog";
    this.points.frustumCulled = false;
    // The floating origin is carried by this group's transform, not by rewriting every vertex. In
    // flight mode the origin moves with the vehicle, so the alternative is re-uploading the whole
    // position buffer sixty times a second for no visual gain.
    this.root = new THREE.Group();
    this.root.name = "catalog-root";
    this.root.add(this.points);

    // Absolute ECEF km, kept so picking and "go to object" do not need the floating origin undone.
    this.absolute = new Float64Array(n * 3);
    this.hasPositions = false;
  }

  /** Feed a worker `positions` reply. lat/lon in degrees, alt in km. */
  setPositions(lat, lon, altKm) {
    const abs = this.absolute;
    const attr = this.points.geometry.getAttribute("position");
    const arr = attr.array;
    const on = this.points.geometry.getAttribute("aOn").array;
    const p = [0, 0, 0];
    for (let i = 0; i < this.n; i++) {
      const la = lat[i];
      if (la !== la) { // NaN: SGP4 could not propagate this element
        abs[i * 3] = NaN;
        on[i] = 0;
        continue;
      }
      geodeticToEcef(la, lon[i], altKm[i] * 1000, p);
      const x = p[0] / 1000, y = p[1] / 1000, z = p[2] / 1000;
      abs[i * 3] = x; abs[i * 3 + 1] = y; abs[i * 3 + 2] = z;
      // Absolute kilometres go straight into the buffer; the group transform supplies the offset.
      arr[i * 3] = x; arr[i * 3 + 1] = y; arr[i * 3 + 2] = z;
      if (this._groupsOn && !this._groupsOn.has(this.groups[this.groupIdx[i]])) on[i] = 0;
      else on[i] = 1;
    }
    attr.needsUpdate = true;
    this.points.geometry.getAttribute("aOn").needsUpdate = true;
    this.hasPositions = true;
  }

  /** Move the whole layer to the current floating origin. One matrix, not 6,819 vertices. */
  refresh(originKm) {
    const ox = originKm ? originKm.x : 0;
    const oy = originKm ? originKm.y : 0;
    const oz = originKm ? originKm.z : 0;
    this._origin = { x: ox, y: oy, z: oz };
    this.root.position.set(-ox, -oy, -oz);
  }

  /** Show or hide whole groups. `onSet` is a Set of group names. */
  setGroups(onSet) {
    this._groupsOn = onSet;
    const on = this.points.geometry.getAttribute("aOn");
    const arr = on.array;
    const abs = this.absolute;
    for (let i = 0; i < this.n; i++) {
      const visible = onSet.has(this.groups[this.groupIdx[i]]);
      arr[i] = visible && abs[i * 3] === abs[i * 3] ? 1 : 0;
    }
    on.needsUpdate = true;
  }

  /** Absolute ECEF km of one object, or null if it has no position. */
  positionOf(i, out) {
    const x = this.absolute[i * 3];
    if (!(x === x)) return null;
    return (out || new THREE.Vector3()).set(x, this.absolute[i * 3 + 1], this.absolute[i * 3 + 2]);
  }

  /**
   * Nearest visible object to a screen point, within `tolPx`, or -1.
   *
   * A CPU loop over the catalog rather than a GPU pick: one pass over 6,819 points per click is
   * well under a millisecond, and it avoids a readback and a second render target.
   */
  pick(ndcX, ndcY, camera, viewportPx, onSet, tolPx = 14) {
    if (!this.hasPositions) return -1;
    const v = this._pickV || (this._pickV = new THREE.Vector3());
    const o = this._origin || { x: 0, y: 0, z: 0 };
    const tolX = (tolPx * 2) / viewportPx.w;
    const tolY = (tolPx * 2) / viewportPx.h;
    let best = -1;
    let bestD = Infinity;
    for (let i = 0; i < this.n; i++) {
      const x = this.absolute[i * 3];
      if (!(x === x)) continue;
      if (!onSet.has(this.groups[this.groupIdx[i]])) continue;
      v.set(x - o.x, this.absolute[i * 3 + 1] - o.y, this.absolute[i * 3 + 2] - o.z);
      v.project(camera);
      if (v.z < -1 || v.z > 1) continue;
      const dx = (v.x - ndcX) / tolX;
      const dy = (v.y - ndcY) / tolY;
      const d = dx * dx + dy * dy;
      if (d <= 1 && d < bestD) { bestD = d; best = i; }
    }
    return best;
  }

  dispose() {
    this.points.geometry.dispose();
    this.material.dispose();
  }
}

/* ------------------------------------------------------------------ orbit trail */

/**
 * The orbit of one object, as a polyline through its propagated positions.
 *
 * Fed from the worker's `track3` reply (lat, lon, alt triples), so the curve is SGP4 output and not a
 * drawn ellipse: it shows the real precession and the real eccentricity.
 */
export class OrbitTrail {
  constructor(color = "#4d9fff", maxPoints = 1024) {
    this.max = maxPoints;
    this.absolute = new Float64Array(maxPoints * 3);
    this.count = 0;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(maxPoints * 3), 3));
    g.setDrawRange(0, 0);
    this.material = new THREE.LineBasicMaterial({
      color: hex(color), transparent: true, opacity: 0.6, depthWrite: false,
    });
    this.line = new THREE.Line(g, this.material);
    this.line.name = "orbit-trail";
    this.line.frustumCulled = false;
    this.line.visible = false;
    // Same reasoning as the catalog layer: the floating origin rides on a transform rather than on
    // a thousand vertices rewritten every frame.
    this.root = new THREE.Group();
    this.root.name = "orbit-trail-root";
    this.root.add(this.line);
  }

  /** `pts` is a flat Float32Array of (lat, lon, altKm) triples. */
  set(pts) {
    const n = Math.min(this.max, Math.floor(pts.length / 3));
    const attr = this.line.geometry.getAttribute("position");
    const arr = attr.array;
    const p = [0, 0, 0];
    for (let i = 0; i < n; i++) {
      geodeticToEcef(pts[i * 3], pts[i * 3 + 1], pts[i * 3 + 2] * 1000, p);
      const x = p[0] / 1000, y = p[1] / 1000, z = p[2] / 1000;
      this.absolute[i * 3] = x; this.absolute[i * 3 + 1] = y; this.absolute[i * 3 + 2] = z;
      arr[i * 3] = x; arr[i * 3 + 1] = y; arr[i * 3 + 2] = z;
    }
    attr.needsUpdate = true;
    this.count = n;
    this.line.visible = n > 1;
    this.line.geometry.setDrawRange(0, n);
  }

  clear() {
    this.count = 0;
    this.line.visible = false;
    this.line.geometry.setDrawRange(0, 0);
  }

  /** Move the trail to the current floating origin: one transform, no vertex work. */
  refresh(originKm) {
    this.root.position.set(
      originKm ? -originKm.x : 0,
      originKm ? -originKm.y : 0,
      originKm ? -originKm.z : 0,
    );
  }

  dispose() {
    this.line.geometry.dispose();
    this.material.dispose();
  }
}

/* ------------------------------------------------------------------ close-up models
 *
 * Built from primitives, in metres, then scaled into kilometres when placed. One model per class, so
 * the vertex count stays trivial and no object is given a shape we cannot source.
 */

const M2KM = 1 / 1000;

function panelMaterial() {
  return new THREE.MeshStandardMaterial({
    color: 0x1d2a3a, metalness: 0.35, roughness: 0.45, emissive: 0x0a1018, emissiveIntensity: 0.6,
  });
}
function foilMaterial() {
  return new THREE.MeshStandardMaterial({ color: 0xd8c6a3, metalness: 0.75, roughness: 0.42 });
}
function strutMaterial() {
  return new THREE.MeshStandardMaterial({ color: 0x8b8275, metalness: 0.6, roughness: 0.5 });
}

/** A station: long truss, four solar array pairs, a module stack across it. ISS layout, schematic. */
export function stationModel(spanM = 109) {
  const g = new THREE.Group();
  const truss = new THREE.Mesh(new THREE.BoxGeometry(spanM * M2KM, 2.2 * M2KM, 2.2 * M2KM), strutMaterial());
  g.add(truss);

  const panelW = (spanM * 0.3) * M2KM;
  const panelH = 11 * M2KM;
  for (const sx of [-1, 1]) {
    for (const sy of [-1, 1]) {
      const p = new THREE.Mesh(new THREE.BoxGeometry(panelW, panelH, 0.25 * M2KM), panelMaterial());
      p.position.set(sx * spanM * 0.33 * M2KM, sy * (panelH * 0.62), 0);
      g.add(p);
    }
  }
  // Pressurised modules, laid across the truss as on the real thing.
  for (let i = -1; i <= 1; i++) {
    const mod = new THREE.Mesh(
      new THREE.CylinderGeometry(2.1 * M2KM, 2.1 * M2KM, 13 * M2KM, 14),
      foilMaterial(),
    );
    mod.rotation.x = Math.PI / 2;
    mod.position.set(i * 5 * M2KM, 0, -3.2 * M2KM);
    g.add(mod);
  }
  const radiator = new THREE.Mesh(new THREE.BoxGeometry(spanM * 0.16 * M2KM, 7 * M2KM, 0.2 * M2KM), strutMaterial());
  radiator.position.set(0, 0, 5 * M2KM);
  g.add(radiator);
  g.name = "station-model";
  return g;
}

/** A satellite: box bus, two panels, a dish. */
export function satelliteModel(spanM = 12, busM = 2) {
  const g = new THREE.Group();
  const bus = new THREE.Mesh(
    new THREE.BoxGeometry(busM * M2KM, busM * M2KM, busM * 1.3 * M2KM),
    foilMaterial(),
  );
  g.add(bus);
  const wingL = ((spanM - busM) / 2) * M2KM;
  for (const s of [-1, 1]) {
    const p = new THREE.Mesh(
      new THREE.BoxGeometry(wingL, busM * 0.8 * M2KM, 0.1 * M2KM),
      panelMaterial(),
    );
    p.position.x = s * (wingL / 2 + (busM / 2) * M2KM);
    g.add(p);
  }
  const dish = new THREE.Mesh(
    new THREE.SphereGeometry(busM * 0.5 * M2KM, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2.4),
    strutMaterial(),
  );
  dish.rotation.x = Math.PI;
  dish.position.z = -busM * 0.8 * M2KM;
  g.add(dish);
  g.name = "satellite-model";
  return g;
}

/** Debris: an irregular lump. Deterministic from a seed so one object always looks the same. */
export function debrisModel(sizeM = 0.6, seed = 1) {
  const geo = new THREE.IcosahedronGeometry((sizeM / 2) * M2KM, 1);
  const p = geo.getAttribute("position");
  let s = seed >>> 0 || 1;
  const rnd = () => {
    s ^= s << 13; s >>>= 0; s ^= s >> 17; s ^= s << 5; s >>>= 0;
    return s / 4294967296;
  };
  for (let i = 0; i < p.count; i++) {
    const f = 0.62 + 0.76 * rnd();
    p.setXYZ(i, p.getX(i) * f, p.getY(i) * f, p.getZ(i) * f);
  }
  geo.computeVertexNormals();
  const m = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x7d6a5c, metalness: 0.4, roughness: 0.8 }));
  m.name = "debris-model";
  return m;
}

/**
 * One reusable close-up model, swapped to the right class on demand.
 *
 * Only ever one object is shown in detail (the selected one) and only when it is actually big enough
 * on screen to be worth it, per scalemath.detailLevel. Everything else stays a marker.
 */
export class CloseUpModel {
  constructor() {
    this.group = new THREE.Group();
    this.group.name = "closeup";
    this.group.visible = false;
    this.cache = new Map();
    this.current = null;
    this.wasMesh = false;
  }

  _for(groupName) {
    if (this.cache.has(groupName)) return this.cache.get(groupName);
    const size = CLASS_SIZE_M[groupName] || CLASS_SIZE_M.active;
    const model =
      groupName === "stations"
        ? stationModel(size.span)
        : groupName === "debris"
          ? debrisModel(size.span)
          : satelliteModel(size.span, size.bus);
    this.cache.set(groupName, model);
    return model;
  }

  /**
   * Place the model, or decide a marker is the honest choice.
   *
   * Returns {shown, label, spanM} so the panel can say what is being drawn and at what scale.
   */
  update(groupName, posKm, originKm, camera, cameraPosKm, viewportH, fovY) {
    if (!groupName || !posKm) {
      this.group.visible = false;
      this.wasMesh = false;
      return { shown: false };
    }
    const size = CLASS_SIZE_M[groupName] || CLASS_SIZE_M.active;
    const trueRadiusKm = (size.span / 2) * M2KM;
    const dist = cameraPosKm.distanceTo(posKm);
    const level = detailLevel(trueRadiusKm, dist, fovY, viewportH, { meshPx: 4, wasMesh: this.wasMesh });
    this.wasMesh = level === "mesh";
    if (level !== "mesh") {
      this.group.visible = false;
      return { shown: false, spanM: size.span, label: size.label };
    }

    if (this.current !== groupName) {
      this.group.clear();
      this.group.add(this._for(groupName));
      this.current = groupName;
    }
    this.group.visible = true;
    this.group.position.set(posKm.x - originKm.x, posKm.y - originKm.y, posKm.z - originKm.z);
    // Point the long axis along the velocity-ish direction: without attitude data the honest default
    // is nadir-pointing with the bus facing Earth, which is how most of these fly.
    const up = posKm.clone().normalize();
    const east = new THREE.Vector3(-up.y, up.x, 0).normalize();
    const m = new THREE.Matrix4().makeBasis(east, new THREE.Vector3().crossVectors(up, east), up);
    this.group.quaternion.setFromRotationMatrix(m);
    return { shown: true, spanM: size.span, label: size.label };
  }

  dispose() {
    for (const m of this.cache.values()) {
      m.traverse((o) => { o.geometry?.dispose?.(); o.material?.dispose?.(); });
    }
    this.cache.clear();
  }
}
