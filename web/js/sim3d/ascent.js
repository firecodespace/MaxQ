/* The ascent simulator, in the browser.
 *
 * This is a line-by-line port of ascent/sim.py (WP-05): 2D point mass in the launch plane, RK4 at a
 * 0.05 s sub-step, thrust and Isp with staging and a dry-mass drop, inverse-square gravity, drag
 * against an exponential atmosphere, the eastward boost from Earth's rotation, and the same
 * five-parameter pitch program. The output is a trajectory.json conforming to
 * contracts/trajectory.schema.json, so it drops straight into the replay, the plots and the 3D view
 * with no special case anywhere.
 *
 * Why a port and not a service call: the point of "press launch and watch it fly" is that changing
 * the vehicle, the azimuth or the pitch program re-flies the ascent at once, and a static site has
 * no server to ask. The risk of a port is that it quietly disagrees with the Python it claims to
 * reproduce, so the agreement is a TEST, not a hope: tests/ascent/test_js_parity.py runs this file
 * in Node and compares every sample against the Python simulator for all three archetypes. If the
 * two drift apart, the suite fails.
 *
 * It is still the same reduced-fidelity model as the Python: 2D, no aerodynamic lift, no angle of
 * attack, no thrust vectoring, no throttle, exponential atmosphere, a spherical Earth for the
 * downrange geodesic. It is labelled SIM in the UI for that reason.
 *
 * Units are SI (metres, seconds, kilograms, newtons), as in the Python.
 */

import {
  MU_M3_S2,
  OMEGA_E_RAD_S,
  R_E_M,
  G0_M_S2,
  deg2rad,
  ecefToGeodetic,
  earthRotationBoost,
  rad2deg,
  sphericalForward,
} from "./geoframe.js";

// ascent/atmosphere.py
export const RHO_0 = 1.225;
export const SCALE_HEIGHT_M = 8500.0;
export const P_0 = 101325.0;
export const ATMOSPHERE_LIMIT_M = 140000.0;

/** Exponential atmosphere density, kg/m^3. Zero at and above the drag cut-off. */
export function density(altM) {
  let h = altM;
  if (h < 0) h = 0;
  if (h >= ATMOSPHERE_LIMIT_M) return 0;
  return RHO_0 * Math.exp(-h / SCALE_HEIGHT_M);
}

/** Exponential atmosphere pressure, Pa. Used by the plume model, not by the trajectory. */
export function pressure(altM) {
  let h = altM;
  if (h < 0) h = 0;
  if (h >= ATMOSPHERE_LIMIT_M) return 0;
  return P_0 * Math.exp(-h / SCALE_HEIGHT_M);
}

/** Pitch program psi(t), degrees above the local horizontal. Port of build_pitch_evaluator. */
export function buildPitchEvaluator(pitchParams, tMeco, tIgn2, tMax) {
  const p = pitchParams || [];
  const pStart = p.length > 0 ? +p[0] : 10.0;
  const pMeco = p.length > 1 ? +p[1] : 20.0;
  const pFinal = p.length > 2 ? +p[2] : 0.0;
  const a1 = p.length > 3 ? +p[3] : 0.8;
  const a2 = p.length > 4 ? +p[4] : 0.8;

  return function pitchDeg(t) {
    if (t < pStart) return 90.0;
    if (t <= tMeco) {
      let tau = (t - pStart) / Math.max(1.0, tMeco - pStart);
      tau = Math.min(1.0, Math.max(0.0, tau));
      return 90.0 - (90.0 - pMeco) * Math.pow(tau, a1);
    }
    if (t < tIgn2) return pMeco;
    let tau = (t - tIgn2) / Math.max(1.0, tMax - tIgn2);
    tau = Math.min(1.0, Math.max(0.0, tau));
    return pMeco - (pMeco - pFinal) * Math.pow(tau, a2);
  };
}

/* ------------------------------------------------------------------ stage and vehicle helpers */

export const massFlowRate = (stage) => stage.thrust_n / (stage.isp_s * G0_M_S2);
export const burnTime = (stage) => stage.prop_kg / massFlowRate(stage);
export const stageMass = (stage) => stage.dry_kg + stage.prop_kg;

export function liftoffMass(rocket) {
  return rocket.payload_kg + rocket.stages.reduce((a, s) => a + stageMass(s), 0);
}

/** Ideal Tsiolkovsky delta-v over all stages, m/s. Port of Rocket.ideal_delta_v. */
export function idealDeltaV(rocket) {
  let dv = 0;
  let m = liftoffMass(rocket);
  for (const s of rocket.stages) {
    const burnout = m - s.prop_kg;
    if (burnout <= 0) continue;
    dv += s.isp_s * G0_M_S2 * Math.log(m / burnout);
    m = burnout - s.dry_kg;
  }
  return dv;
}

/** Python's round() to `d` decimals, close enough for the contract's fixed precision. */
const r1 = (x) => Math.round(x * 10) / 10;
const r4 = (x) => Math.round(x * 1e4) / 1e4;

/**
 * Fly a rocket to a parking orbit.
 *
 * Mirrors simulate_ascent(): same defaults, same loop order, same cut-off test, same event rounding.
 * Returns {trajectory, ...diagnostics}; `trajectory` is contract-shaped.
 */
export function simulateAscent(rocket, opts = {}) {
  const siteLatDeg = opts.siteLatDeg ?? 28.5;
  const siteLonDeg = opts.siteLonDeg ?? -80.6;
  const azimuthDeg = opts.azimuthDeg ?? 44.9;
  const targetInclDeg = opts.targetInclDeg ?? 51.6;
  const trajectoryId = opts.trajectoryId ?? "traj_medlift_a_w0";
  const windowId = opts.windowId ?? "w0";
  const t0Unix = opts.t0Unix ?? 1794286000;
  const seed = opts.seed ?? 42;
  const dt = opts.dtSubstep ?? 0.05;
  const targetParkingAltM = opts.targetParkingAltM ?? 200000.0;

  if (!rocket || !Array.isArray(rocket.stages) || rocket.stages.length < 2) {
    throw new Error(`Rocket ${rocket && rocket.id} must have at least 2 stages for the ascent sim`);
  }
  const s1 = rocket.stages[0];
  const s2 = rocket.stages[1];

  // Staging schedule
  const tBurn1 = burnTime(s1);
  const tSepCoast = 5.0;
  const tMeco = tBurn1;
  const tSep = tMeco + tSepCoast;
  const tIgn2 = tSep;
  const tBurn2 = burnTime(s2);
  const tMaxSim = tIgn2 + tBurn2 + 10.0;

  const pitchParams = opts.pitchParams ?? [11.865, 18.262, -1.0, 0.81, 0.747];
  const pitchDeg = buildPitchEvaluator(pitchParams, tMeco, tIgn2, tIgn2 + tBurn2);

  const cosSiteLat = Math.cos(deg2rad(siteLatDeg));

  // Initial state
  let r = R_E_M;
  let theta = 0.0;
  let vr = 0.0;
  let vd = earthRotationBoost(siteLatDeg);
  let m = liftoffMass(rocket);

  let t = 0.0;
  let maxQ = 0.0;
  let tMaxQ = 0.0;
  let dvDrag = 0.0;
  let dvGrav = 0.0;
  let idealDv = 0.0;

  const tS = [];
  const latArr = [];
  const lonArr = [];
  const altArr = [];
  const spdArr = [];
  const qArr = [];
  const massArr = [];
  // Not in the contract, but the flight camera needs an attitude and the HUD a thrust state, and
  // deriving them later from finite differences is both noisier and a guess. Recorded here because
  // the simulator is the one place that actually knows them.
  const pitchArr = [];
  const stageArr = [];
  const thrustArr = [];

  let nextRecordT = 0.0;
  let tInsertion = 0.0;
  let insertionReached = false;
  let vInsSpeed = 0.0;
  let hInsAlt = 0.0;
  let perigeeKm = 0.0;
  let apogeeKm = 0.0;
  let eccFinal = 0.0;
  let stage1Separated = false;

  const ecef = [0, 0, 0];

  while (t <= tMaxSim) {
    const h = r - R_E_M;
    const speed = Math.sqrt(vr * vr + vd * vd);
    const vCirc = Math.sqrt(MU_M3_S2 / r);

    // ---- record at 1 Hz
    if (t >= nextRecordT - 1e-6) {
      const downrangeM = R_E_M * theta;
      const [latIner, lonIner] = sphericalForward(siteLatDeg, siteLonDeg, azimuthDeg, downrangeM);

      // Earth-fixed longitude: take out the rotation accumulated since launch.
      let lonEcef = lonIner - rad2deg(OMEGA_E_RAD_S * t);
      lonEcef = ((lonEcef + 180) % 360 + 360) % 360 - 180;

      const phi = deg2rad(latIner);
      const lam = deg2rad(lonEcef);
      ecef[0] = r * Math.cos(phi) * Math.cos(lam);
      ecef[1] = r * Math.cos(phi) * Math.sin(lam);
      ecef[2] = r * Math.sin(phi);
      const g = ecefToGeodetic(ecef[0], ecef[1], ecef[2]);

      const rhoRec = density(h);
      const vAirDRec = vd - OMEGA_E_RAD_S * r * cosSiteLat;
      const vAirRec = Math.sqrt(vr * vr + vAirDRec * vAirDRec);
      const qRec = 0.5 * rhoRec * vAirRec * vAirRec;

      tS.push(Math.round(nextRecordT));
      latArr.push(r4(g.latDeg));
      lonArr.push(r4(g.lonDeg));
      altArr.push(r1(g.altM));
      spdArr.push(r1(speed));
      qArr.push(r1(qRec));
      massArr.push(r1(m));
      pitchArr.push(r1(pitchDeg(t)));
      stageArr.push(insertionReached ? 0 : t <= tMeco ? 1 : t < tIgn2 ? 0 : 2);
      thrustArr.push(insertionReached ? 0 : t <= tMeco ? s1.thrust_n : t < tIgn2 ? 0 : s2.thrust_n);

      nextRecordT += 1.0;
    }

    // ---- max q
    const rhoCur = density(h);
    const vAirDCur = vd - OMEGA_E_RAD_S * r * cosSiteLat;
    const vAirCur = Math.sqrt(vr * vr + vAirDCur * vAirDCur);
    const qCur = 0.5 * rhoCur * vAirCur * vAirCur;
    if (qCur > maxQ) {
      maxQ = qCur;
      tMaxQ = t;
    }

    // ---- insertion cut-off: upper stage lit, above 160 km, and either the semi-major axis has
    // reached the target parking orbit or the speed is already circular.
    const epsCur = 0.5 * speed * speed - MU_M3_S2 / r;
    const aSemi = epsCur < 0 ? -MU_M3_S2 / (2.0 * epsCur) : 1e9;
    if (
      t > tIgn2 &&
      h >= 160000.0 &&
      (aSemi >= R_E_M + targetParkingAltM || speed >= vCirc) &&
      !insertionReached
    ) {
      insertionReached = true;
      tInsertion = t;
      vInsSpeed = speed;
      hInsAlt = h;
      const hAng = r * vd;
      const eccSq = 1.0 + (2.0 * epsCur * hAng * hAng) / (MU_M3_S2 * MU_M3_S2);
      eccFinal = Math.sqrt(Math.max(0.0, eccSq));
      perigeeKm = (aSemi * (1.0 - eccFinal) - R_E_M) / 1000.0;
      apogeeKm = (aSemi * (1.0 + eccFinal) - R_E_M) / 1000.0;
    }

    // Coast 5 s after insertion so a central difference can recover velocity downstream.
    if (insertionReached && t >= tInsertion + 5.0) break;

    // ---- stage parameters for this step
    let thrust;
    let cd;
    let area;
    let mdot;
    if (insertionReached) {
      thrust = 0; cd = 0; area = 0; mdot = 0;
    } else if (t <= tMeco) {
      thrust = s1.thrust_n; cd = s1.cd; area = s1.area_m2; mdot = massFlowRate(s1);
    } else if (t < tIgn2) {
      thrust = 0; cd = s1.cd; area = s1.area_m2; mdot = 0;
    } else {
      thrust = s2.thrust_n; cd = s2.cd; area = s2.area_m2; mdot = massFlowRate(s2);
    }

    // ---- drop the spent first stage
    if (t >= tMeco && !stage1Separated) {
      m -= s1.dry_kg;
      stage1Separated = true;
    }

    // ---- derivatives (thrust/cd/area/mdot are constant across the RK4 step, as in the Python)
    const deriv = (tC, rC, thC, vrC, vdC, mC) => {
      const hC = rC - R_E_M;
      const rhoC = density(hC);
      const vAirDC = vdC - OMEGA_E_RAD_S * rC * cosSiteLat;
      const vAirC = Math.sqrt(vrC * vrC + vAirDC * vAirDC);
      const fDragC = 0.5 * rhoC * vAirC * vAirC * cd * area;

      const dragR = vAirC > 0 ? fDragC * (vrC / vAirC) : 0.0;
      const dragD = vAirC > 0 ? fDragC * (vAirDC / vAirC) : 0.0;

      const psiC = deg2rad(pitchDeg(tC));
      const ftR = thrust * Math.sin(psiC);
      const ftD = thrust * Math.cos(psiC);

      const gLoc = MU_M3_S2 / (rC * rC);
      return [
        vrC,
        vdC / rC,
        (vdC * vdC) / rC - gLoc + (ftR - dragR) / mC,
        -((vrC * vdC) / rC) + (ftD - dragD) / mC,
        -mdot,
        fDragC,
      ];
    };

    const k1 = deriv(t, r, theta, vr, vd, m);
    const k2 = deriv(
      t + 0.5 * dt,
      r + 0.5 * dt * k1[0],
      theta + 0.5 * dt * k1[1],
      vr + 0.5 * dt * k1[2],
      vd + 0.5 * dt * k1[3],
      m + 0.5 * dt * k1[4],
    );
    const k3 = deriv(
      t + 0.5 * dt,
      r + 0.5 * dt * k2[0],
      theta + 0.5 * dt * k2[1],
      vr + 0.5 * dt * k2[2],
      vd + 0.5 * dt * k2[3],
      m + 0.5 * dt * k2[4],
    );
    const k4 = deriv(
      t + dt,
      r + dt * k3[0],
      theta + dt * k3[1],
      vr + dt * k3[2],
      vd + dt * k3[3],
      m + dt * k3[4],
    );

    // ---- losses
    dvDrag += (k1[5] / m) * dt;
    dvGrav += (MU_M3_S2 / (r * r)) * Math.sin(deg2rad(pitchDeg(t))) * dt;
    idealDv += (thrust / m) * dt;

    r += (dt / 6.0) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]);
    theta += (dt / 6.0) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]);
    vr += (dt / 6.0) * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2]);
    vd += (dt / 6.0) * (k1[3] + 2 * k2[3] + 2 * k3[3] + k4[3]);
    m += (dt / 6.0) * (k1[4] + 2 * k2[4] + 2 * k3[4] + k4[4]);

    t += dt;
  }

  if (!insertionReached) {
    tInsertion = t;
    vInsSpeed = Math.sqrt(vr * vr + vd * vd);
    hInsAlt = r - R_E_M;
    const epsFinal = 0.5 * vInsSpeed * vInsSpeed - MU_M3_S2 / r;
    const aFinal = epsFinal < 0 ? -MU_M3_S2 / (2.0 * epsFinal) : 1e9;
    const hAng = r * vd;
    const eccSq = 1.0 + (2.0 * epsFinal * hAng * hAng) / (MU_M3_S2 * MU_M3_S2);
    eccFinal = Math.sqrt(Math.max(0.0, eccSq));
    perigeeKm = (aFinal * (1.0 - eccFinal) - R_E_M) / 1000.0;
    apogeeKm = (aFinal * (1.0 + eccFinal) - R_E_M) / 1000.0;
  }

  const last = tS[tS.length - 1];
  const clampEv = (v, lo) => Math.min(Math.max(lo, Math.round(v)), last);
  const secMaxQ = clampEv(tMaxQ, 0);
  const secMeco = clampEv(tMeco, 0);
  const secSep = clampEv(tSep, secMeco);
  const secIns = clampEv(tInsertion, secSep);

  const trajectory = {
    schema_version: 1,
    id: trajectoryId,
    rocket_id: rocket.id,
    window_id: windowId,
    t0_unix: t0Unix,
    seed,
    t_s: tS,
    lat_deg: latArr,
    lon_deg: lonArr,
    alt_m: altArr,
    speed_mps: spdArr,
    q_pa: qArr,
    mass_kg: massArr,
    events: [
      { t_s: secMaxQ, name: "max_q" },
      { t_s: secMeco, name: "meco" },
      { t_s: secSep, name: "stage_sep" },
      { t_s: secIns, name: "insertion" },
    ],
    final_orbit: {
      perigee_km: r1(perigeeKm),
      apogee_km: r1(apogeeKm),
      incl_deg: Math.round(targetInclDeg * 100) / 100,
      ecc: r4(eccFinal),
    },
    params: { pitch_program: pitchParams.map(Number) },
  };

  return {
    trajectory,
    // Attitude and thrust, parallel to t_s. Outside the contract on purpose: the schema forbids
    // extra properties, so these are handed over beside the trajectory rather than inside it.
    extra: { pitch_deg: pitchArr, stage: stageArr, thrust_n: thrustArr },
    perigeeKm,
    apogeeKm,
    eccentricity: eccFinal,
    insertionSpeedMps: vInsSpeed,
    insertionAltM: hInsAlt,
    maxQPa: maxQ,
    tMaxQS: tMaxQ,
    gravityLossMps: dvGrav,
    dragLossMps: dvDrag,
    idealDvMps: idealDv,
    totalLossMps: dvGrav + dvDrag,
    tInsertionS: tInsertion,
    reachedOrbit: insertionReached,
  };
}
