/* The launch vehicle: geometry derived from its own stage numbers, and an exhaust plume driven by
 * thrust, exhaust velocity and ambient pressure.
 *
 * The rockets are fictional archetypes -- that is a project-wide decision, not a shortcut here -- so
 * there is no drawing to copy and no photograph to match. What there IS is a contract
 * (contracts/rocket.schema.json) giving each stage its propellant mass, dry mass, thrust, specific
 * impulse, drag coefficient and reference area. Every dimension below is computed from those
 * numbers, so the vehicle you see is the vehicle the simulator flies:
 *
 *   diameter          from `area_m2`, the reference area the drag term already uses: D = 2*sqrt(A/pi)
 *   stage length      from `prop_kg` and the bulk density of its propellant combination, divided by
 *                     the tank cross-section: a stage carrying 390 t of kerolox cannot be short
 *   propellant type   from `isp_s`: above 380 s only a hydrogen upper stage is in that range
 *   nozzle exit area  from `thrust_n` through the thrust coefficient and chamber pressure, then an
 *                     expansion ratio chosen by whether the stage works at sea level or in vacuum
 *   fairing           from `payload_kg` at a typical payload bulk density
 *
 * Checked against the archetypes: MedLift-A comes out near 64 m at 521 t, HeavyLift-C near 75 m at
 * 1,049 t. Those are the right size for their mass, which is the point -- the proportions follow
 * from the physics rather than from taste.
 *
 * Two things are PRESENTATION, not data, and are called out in the UI: the engine count (the contract
 * gives total thrust, not how many bells it comes out of, so the layout is chosen from how much of
 * the base the exit area fills) and the paint. Everything else is derived.
 *
 * Geometry is built in METRES along local +Z (nose up, engines at -Z) and scaled to kilometres by the
 * caller, so the same model works at both ends of the zoom range.
 */

import * as THREE from "../../vendor/three/three.module.js";
import { G0_M_S2 } from "./geoframe.js";
import { pressure } from "./ascent.js";
import { hex } from "../theme.js";

const M2KM = 1 / 1000;

/* Bulk density of a loaded stage, kg/m^3: the mass of propellant divided by the tank volume it needs,
 * at the mixture ratio those engines run. LOX/RP-1 near 2.56 : 1 gives about 1,030; LOX/LH2 near
 * 6 : 1 gives about 360, because hydrogen is enormous for its mass. */
export const RHO_KEROLOX = 1030;
export const RHO_HYDROLOX = 360;
/** Tanks are not cylinders: domes, the intertank and the thrust structure cost volume. */
export const TANK_VOLUME_EFFICIENCY = 0.9;
/** Payload bulk density, kg/m^3 -- satellites are mostly empty space inside the fairing. */
export const RHO_PAYLOAD = 200;

/** Ratio of specific heats for hot rocket exhaust. 1.2 is the usual working value. */
export const GAMMA = 1.2;

/**
 * Supersonic exit Mach number for a nozzle area ratio, from the isentropic area-Mach relation
 *
 *     A_e/A_t = (1/M) [ (2/(g+1)) (1 + (g-1)/2 M^2) ] ^ ((g+1) / (2(g-1)))
 *
 * which cannot be inverted in closed form, so it is bisected. It is called once per stage when the
 * vehicle is built, never per frame.
 *
 * This replaced a power-law fit (pe = pc * eps^-1.2) that was tidy and wrong: it put a sea-level
 * first stage's exit pressure several times ABOVE ambient at lift-off, so the plume flared like a
 * vacuum engine on the pad instead of standing in a tight overexpanded column. The whole point of
 * driving the plume from physics is lost if the physics is a curve fit.
 */
export function machFromAreaRatio(eps) {
  if (!(eps > 1)) return 1;
  const expo = (GAMMA + 1) / (2 * (GAMMA - 1));
  const area = (m) => (1 / m) * Math.pow((2 / (GAMMA + 1)) * (1 + ((GAMMA - 1) / 2) * m * m), expo);
  let lo = 1.0001;
  let hi = 50;
  for (let i = 0; i < 80; i++) {
    const mid = 0.5 * (lo + hi);
    if (area(mid) < eps) lo = mid;
    else hi = mid;
  }
  return 0.5 * (lo + hi);
}

/** Which propellant a stage burns, inferred from its specific impulse. */
export const propellantOf = (stage) => (stage.isp_s >= 380 ? "hydrolox" : "kerolox");
export const bulkDensityOf = (stage) => (propellantOf(stage) === "hydrolox" ? RHO_HYDROLOX : RHO_KEROLOX);

/** Stage outer diameter, m, from the reference area the drag model already uses. */
export const diameterOf = (stage) => 2 * Math.sqrt(stage.area_m2 / Math.PI);

/** Stage length, m: the propellant has to fit. */
export function stageLengthM(stage) {
  const area = stage.area_m2 * TANK_VOLUME_EFFICIENCY;
  return stage.prop_kg / (bulkDensityOf(stage) * area);
}

/**
 * Nozzle geometry for a stage.
 *
 * T = Cf * pc * At sizes the throat; the expansion ratio then sizes the exit. A first stage has to
 * start at sea level, so it is expanded to about 16 and leaves the gas above ambient pressure at
 * lift-off; a vacuum stage is expanded far further, to about 80, which is why its bell is enormous
 * and why it would be destroyed trying to run at sea level.
 */
export function nozzleOf(stage, isFirstStage) {
  const pc = 10e6; // chamber pressure, Pa: a staged-combustion-era value
  const cf = isFirstStage ? 1.6 : 1.85; // thrust coefficient, sea level vs vacuum
  const eps = isFirstStage ? 16 : 80; // expansion ratio
  const throatArea = stage.thrust_n / (cf * pc);
  const exitArea = throatArea * eps;

  // Exit pressure, from the expansion ratio through the isentropic flow relations.
  const exitMach = machFromAreaRatio(eps);
  const exitPressure = pc / Math.pow(1 + ((GAMMA - 1) / 2) * exitMach * exitMach, GAMMA / (GAMMA - 1));

  // Engine count. The contract gives total thrust, never how many bells it comes out of, so this is
  // a LAYOUT CHOICE and the UI says so. It is made by asking whether the hardware fits: work out the
  // diameter of a single bell passing all this flow, and if that will not sit inside the stage, use
  // the smallest cluster that will. A sea-level first stage ends up with a cluster, as real ones do,
  // and a highly expanded vacuum upper stage ends up with one big bell, as real ones do.
  const stageDia = 2 * Math.sqrt(stage.area_m2 / Math.PI);
  const singleDia = 2 * Math.sqrt(exitArea / Math.PI);
  const fits = (n) => 2 * Math.sqrt(exitArea / (n * Math.PI)) <= (n === 1 ? 0.72 : 0.46) * stageDia;
  const count = fits(1) ? 1 : fits(4) ? 4 : 9;
  void singleDia;

  return {
    count,
    exitArea,
    throatArea,
    exitPressure,
    exitDiameter: 2 * Math.sqrt(exitArea / (count * Math.PI)),
    expansionRatio: eps,
    exhaustVelocity: stage.isp_s * G0_M_S2,
    massFlow: stage.thrust_n / (stage.isp_s * G0_M_S2),
  };
}

/** Every derived dimension for a vehicle, in metres. Pure: no three.js, so it is easy to check. */
export function vehicleDimensions(rocket) {
  const stages = rocket.stages.map((s, i) => {
    const d = diameterOf(s);
    return {
      index: i,
      diameter: d,
      radius: d / 2,
      length: stageLengthM(s),
      propellant: propellantOf(s),
      nozzle: nozzleOf(s, i === 0),
      spec: s,
    };
  });

  const topD = stages[stages.length - 1].diameter;
  const payloadVolume = rocket.payload_kg / RHO_PAYLOAD;
  const fairingBarrel = Math.max(2, payloadVolume / (Math.PI * (topD / 2) ** 2 * 0.6));
  const fairingCone = topD * 1.5;
  const interstage = stages.length > 1 ? stages[0].diameter * 1.05 : 0;

  const total =
    stages.reduce((a, s) => a + s.length, 0) + interstage + fairingBarrel + fairingCone;

  return {
    stages,
    interstage,
    fairingBarrel,
    fairingCone,
    totalLength: total,
    maxDiameter: Math.max(...stages.map((s) => s.diameter)),
    liftoffThrust: stages[0].spec.thrust_n,
    // Thrust-to-weight at lift-off: below 1 it does not leave the pad, which is worth showing.
    liftoffTWR:
      stages[0].spec.thrust_n /
      ((rocket.payload_kg + rocket.stages.reduce((a, s) => a + s.dry_kg + s.prop_kg, 0)) * G0_M_S2),
  };
}

/* ------------------------------------------------------------------ plume
 *
 * An exhaust plume's shape is set by the pressure it leaves the nozzle at against the pressure
 * outside. Overexpanded (pe < pa, which is a sea-level stage at lift-off) the atmosphere squeezes the
 * jet into a narrow column with shock structure in it. Underexpanded (pe > pa, the same engine high
 * up) the gas has nothing holding it in and flares into the enormous bell you see on every ascent
 * video. The usual trick of fading in a bigger sprite with altitude gets the look; this gets the
 * look BECAUSE it computes the cause.
 */

/** Plume state for a thrusting stage at an altitude. Pure, so plume.test.mjs can pin it down. */
export function plumeState(nozzle, thrustN, altM) {
  const pa = pressure(altM);
  const pe = nozzle.exitPressure;
  const ratio = pe / Math.max(pa, 1e-3);

  // Half-angle of the flare. Underexpanded jets open up with the square root of the excess pressure
  // ratio; overexpanded ones pinch in instead. Capped at 62 degrees: past that the plume has
  // separated and the shape stops meaning anything.
  const openDeg = ratio > 1
    ? Math.min(62, (180 / Math.PI) * Math.atan(0.42 * Math.sqrt(ratio - 1)))
    : -Math.min(14, (180 / Math.PI) * Math.atan(0.5 * Math.sqrt(1 / Math.max(ratio, 1e-6) - 1)));

  // Length scales with exhaust momentum and stretches as the air thins.
  const lengthM = nozzle.exitDiameter * (4.5 + 7.5 * Math.min(4, Math.max(0, Math.log10(Math.max(ratio, 1e-3)) + 1)));

  // Brightness. Dense air near the ground makes a bright, opaque jet (and afterburns the fuel-rich
  // core); in vacuum the same mass flow is a thin, translucent glow.
  const dense = Math.min(1, pa / 101325);
  const opacity = 0.2 + 0.62 * dense;

  // Shock diamonds only exist inside an atmosphere and only when the jet is overexpanded.
  const shocks = ratio < 1 && pa > 2000 ? Math.min(6, Math.round(4 * (1 - ratio))) : 0;

  const throttle = thrustN > 0 ? Math.min(1, thrustN / Math.max(1, nozzle.massFlow * nozzle.exhaustVelocity)) : 0;

  return { pa, pe, ratio, openDeg, lengthM, opacity, shocks, dense, throttle };
}

const PLUME_VERT = /* glsl */ `
  #include <common>
  #include <logdepthbuf_pars_vertex>
  varying float vT;      // 0 at the nozzle, 1 at the tip
  varying float vRad;    // 0 on the axis, 1 at the edge
  attribute float aT;
  attribute float aRad;
  void main() {
    vT = aT;
    vRad = aRad;
    gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(position, 1.0);
    #include <logdepthbuf_vertex>
  }
`;

const PLUME_FRAG = /* glsl */ `
  precision mediump float;
  #include <common>
  #include <logdepthbuf_pars_fragment>
  uniform float opacity;
  uniform float shocks;
  uniform float flicker;
  uniform vec3 coreColor;
  uniform vec3 edgeColor;
  varying float vT;
  varying float vRad;
  void main() {
    #include <logdepthbuf_fragment>
    // Along the plume: full at the throat, fading out at the tip.
    float along = pow(1.0 - vT, 1.35);
    // Across it: a hot core, cooler and thinner at the edge.
    float across = pow(1.0 - vRad, 1.6);
    float a = along * (0.35 + 0.65 * across) * opacity * flicker;

    // Shock diamonds: standing waves in an overexpanded jet, so they live near the nozzle.
    if (shocks > 0.5) {
      float band = 0.5 + 0.5 * cos(vT * shocks * 6.2831853);
      a *= 1.0 + 0.55 * band * (1.0 - vT) * (1.0 - vRad);
    }
    if (a <= 0.004) discard;
    vec3 col = mix(edgeColor, coreColor, across * along);
    gl_FragColor = vec4(col, a);
  }
`;

/**
 * The plume mesh. Its vertices are rewritten each frame from plumeState, which is what makes the
 * shape change with altitude instead of a texture swap.
 */
class Plume {
  constructor(segAlong = 26, segAround = 20) {
    this.na = segAlong;
    this.nr = segAround;
    const nV = (segAlong + 1) * (segAround + 1);
    const pos = new Float32Array(nV * 3);
    const aT = new Float32Array(nV);
    const aRad = new Float32Array(nV);
    const idx = new Uint32Array(segAlong * segAround * 6);

    let k = 0;
    for (let j = 0; j <= segAlong; j++) {
      for (let i = 0; i <= segAround; i++, k++) {
        aT[k] = j / segAlong;
        aRad[k] = 1;
      }
    }
    let t = 0;
    for (let j = 0; j < segAlong; j++) {
      for (let i = 0; i < segAround; i++) {
        const a = j * (segAround + 1) + i;
        const b = a + segAround + 1;
        idx[t++] = a; idx[t++] = b; idx[t++] = a + 1;
        idx[t++] = a + 1; idx[t++] = b; idx[t++] = b + 1;
      }
    }

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aT", new THREE.BufferAttribute(aT, 1));
    g.setAttribute("aRad", new THREE.BufferAttribute(aRad, 1));
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e3);

    this.material = new THREE.ShaderMaterial({
      uniforms: {
        opacity: { value: 0.6 },
        shocks: { value: 0 },
        flicker: { value: 1 },
        coreColor: { value: new THREE.Color(1.0, 0.95, 0.86) },
        edgeColor: { value: new THREE.Color(0.96, 0.52, 0.2) },
      },
      vertexShader: PLUME_VERT,
      fragmentShader: PLUME_FRAG,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    });
    this.mesh = new THREE.Mesh(g, this.material);
    this.mesh.name = "plume";
    this.mesh.frustumCulled = false;
    this.mesh.visible = false;
  }

  /** Reshape for a thrust level and altitude. `scale` converts metres to scene units. */
  update(nozzle, thrustN, altM, scale, timeS, hydrolox) {
    if (!(thrustN > 0)) {
      this.mesh.visible = false;
      return null;
    }
    const st = plumeState(nozzle, thrustN, altM);
    const attr = this.mesh.geometry.getAttribute("position");
    const arr = attr.array;
    const r0 = (nozzle.exitDiameter / 2) * scale;
    const len = st.lengthM * scale;
    const tanOpen = Math.tan((st.openDeg * Math.PI) / 180);

    let k = 0;
    for (let j = 0; j <= this.na; j++) {
      const t = j / this.na;
      // Radius grows linearly with the flare angle, then necks down to a point at the tip so the
      // plume ends rather than being cut off square.
      const flare = r0 + tanOpen * len * t;
      const taper = t < 0.72 ? 1 : 1 - (t - 0.72) / 0.28;
      const r = Math.max(0, flare * taper);
      const z = -len * t;
      for (let i = 0; i <= this.nr; i++, k++) {
        const a = (i / this.nr) * Math.PI * 2;
        arr[k * 3] = Math.cos(a) * r;
        arr[k * 3 + 1] = Math.sin(a) * r;
        arr[k * 3 + 2] = z;
      }
    }
    attr.needsUpdate = true;

    const u = this.material.uniforms;
    u.opacity.value = st.opacity * (0.55 + 0.45 * st.throttle);
    u.shocks.value = st.shocks;
    // Combustion is not steady. A couple of incommensurate sines read as roughness without the cost
    // or the randomness of a particle system.
    u.flicker.value = 0.9 + 0.07 * Math.sin(timeS * 37.1) + 0.05 * Math.sin(timeS * 91.7);
    // Hydrogen burns almost colourless; kerosene is bright orange and sooty.
    if (hydrolox) {
      u.coreColor.value.setRGB(0.82, 0.88, 1.0);
      u.edgeColor.value.setRGB(0.45, 0.58, 0.92);
    } else {
      u.coreColor.value.setRGB(1.0, 0.95, 0.86);
      u.edgeColor.value.setRGB(0.96, 0.52, 0.2);
    }
    this.mesh.visible = true;
    return st;
  }

  dispose() {
    this.mesh.geometry.dispose();
    this.material.dispose();
  }
}

/* ------------------------------------------------------------------ the vehicle model */

const bodyMat = () => new THREE.MeshStandardMaterial({ color: 0xd9d2c6, metalness: 0.45, roughness: 0.44 });
const darkMat = () => new THREE.MeshStandardMaterial({ color: 0x2a241d, metalness: 0.5, roughness: 0.6 });
const bellMat = () => new THREE.MeshStandardMaterial({ color: 0x8c7f70, metalness: 0.82, roughness: 0.32, side: THREE.DoubleSide });
const bandMat = () => new THREE.MeshStandardMaterial({ color: hex("#2a6fd6"), metalness: 0.3, roughness: 0.55 });

/**
 * Build a vehicle from a contract rocket object.
 *
 * `scale` is scene units per metre (1/1000 for a kilometre scene). Returns the group plus the handles
 * the animation needs: which parts belong to which stage, and the plumes.
 */
export function createVehicle(rocket, opts = {}) {
  const scale = opts.scale ?? M2KM;
  const dims = vehicleDimensions(rocket);
  const group = new THREE.Group();
  group.name = `vehicle-${rocket.id}`;

  const stageGroups = [];
  const plumes = [];
  let z = 0; // running height from the base, in metres

  dims.stages.forEach((st, i) => {
    const sg = new THREE.Group();
    sg.name = `stage-${i + 1}`;

    // Tank barrel.
    const barrel = new THREE.Mesh(
      new THREE.CylinderGeometry(st.radius * scale, st.radius * scale, st.length * scale, 40, 1, true),
      i === 0 ? bodyMat() : darkMat(),
    );
    barrel.rotation.x = Math.PI / 2; // cylinder's +Y to the vehicle's +Z
    barrel.position.z = (z + st.length / 2) * scale;
    sg.add(barrel);

    // A band where the tanks meet, so the stage divisions are legible at a glance.
    const band = new THREE.Mesh(
      new THREE.CylinderGeometry(st.radius * 1.004 * scale, st.radius * 1.004 * scale, st.length * 0.012 * scale, 40),
      bandMat(),
    );
    band.rotation.x = Math.PI / 2;
    band.position.z = (z + st.length * 0.62) * scale;
    sg.add(band);

    // Engines: a bell per engine, in the octaweb / quad / single layout nozzleOf chose.
    const nz = st.nozzle;
    const bellLen = nz.exitDiameter * (i === 0 ? 1.5 : 2.4); // vacuum bells are long
    const ring = nz.count > 1 ? st.radius * 0.62 : 0;
    for (let e = 0; e < nz.count; e++) {
      const bell = new THREE.Mesh(
        new THREE.CylinderGeometry(
          (nz.exitDiameter / 2) * scale,
          (nz.exitDiameter / 6) * scale,
          bellLen * scale,
          18,
          1,
          true,
        ),
        bellMat(),
      );
      bell.rotation.x = -Math.PI / 2;
      let ex = 0, ey = 0;
      if (nz.count > 1) {
        if (e === 0 && nz.count === 9) { ex = 0; ey = 0; }
        else {
          const k = nz.count === 9 ? e - 1 : e;
          const m = nz.count === 9 ? 8 : nz.count;
          const a = (k / m) * Math.PI * 2;
          ex = Math.cos(a) * ring;
          ey = Math.sin(a) * ring;
        }
      }
      bell.position.set(ex * scale, ey * scale, (z - bellLen / 2) * scale);
      sg.add(bell);

      const plume = new Plume();
      plume.mesh.position.set(ex * scale, ey * scale, (z - bellLen) * scale);
      sg.add(plume.mesh);
      plumes.push({ stage: i, plume, nozzle: nz, hydrolox: st.propellant === "hydrolox" });
    }

    group.add(sg);
    stageGroups.push({ group: sg, dims: st, baseZ: z });
    z += st.length;
    if (i === 0) z += dims.interstage;
  });

  // Interstage shell, drawn with the first stage because it is left behind with it.
  if (dims.interstage > 0) {
    const s0 = dims.stages[0];
    const inter = new THREE.Mesh(
      new THREE.CylinderGeometry(s0.radius * 0.99 * scale, s0.radius * 0.99 * scale, dims.interstage * scale, 36, 1, true),
      darkMat(),
    );
    inter.rotation.x = Math.PI / 2;
    inter.position.z = (s0.length + dims.interstage / 2) * scale;
    stageGroups[0].group.add(inter);
  }

  // Fairing: barrel plus nose cone, carrying the payload.
  const topR = dims.stages[dims.stages.length - 1].radius;
  const fairing = new THREE.Group();
  fairing.name = "fairing";
  const fb = new THREE.Mesh(
    new THREE.CylinderGeometry(topR * scale, topR * scale, dims.fairingBarrel * scale, 36, 1, true),
    bodyMat(),
  );
  fb.rotation.x = Math.PI / 2;
  fb.position.z = (z + dims.fairingBarrel / 2) * scale;
  fairing.add(fb);
  const cone = new THREE.Mesh(
    new THREE.ConeGeometry(topR * scale, dims.fairingCone * scale, 36, 1, true),
    bodyMat(),
  );
  cone.rotation.x = Math.PI / 2;
  cone.position.z = (z + dims.fairingBarrel + dims.fairingCone / 2) * scale;
  fairing.add(cone);
  group.add(fairing);

  return {
    group,
    dims,
    stageGroups,
    plumes,
    fairing,
    rocket,
    scale,
    /** Local +Z offset, in scene units, from the base of the vehicle to its centre of length. */
    centreOffset: (dims.totalLength / 2) * scale,

    /**
     * Animate for one instant of flight.
     * `stage` is 0 (coasting), 1 or 2; `thrustN` the current thrust; `altM` the altitude.
     */
    update({ stage, thrustN, altM, timeS, separated }) {
      if (separated && stageGroups[0]) stageGroups[0].group.visible = false;
      let state = null;
      for (const p of plumes) {
        const live = p.stage + 1 === stage && thrustN > 0;
        const st = live
          ? p.plume.update(p.nozzle, thrustN / p.nozzle.count, altM, scale, timeS, p.hydrolox)
          : p.plume.update(p.nozzle, 0, altM, scale, timeS, p.hydrolox);
        if (st) state = st;
      }
      return state;
    },

    dispose() {
      for (const p of plumes) p.plume.dispose();
      group.traverse((o) => {
        if (o.geometry && o.name !== "plume") o.geometry.dispose?.();
        if (o.material && o.name !== "plume") o.material.dispose?.();
      });
    },
  };
}
