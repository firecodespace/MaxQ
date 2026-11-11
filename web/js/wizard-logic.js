/* The wizard's decisions, with no DOM in sight (WP2-07).
 *
 * Which vehicles can fly a mission, which pads can launch them, whether a payload fits, what the
 * constraints say before anything is simulated, and the mission_request that comes out at the end.
 * All of it is pure, so tests/web/wizard.test.mjs can hold it to the contract and to the measured
 * findings without a browser.
 *
 * The honesty rules live here rather than in the view, because that is where they cannot be skipped
 * by a later redesign: a vehicle whose simulated payload missed its tolerance is never silently
 * offered, and a crew mission is never presented as flyable while the simulator cannot throttle.
 */

/** Mission profiles the wizard offers. `ready: false` ones are visible but not selectable. */
export const PROFILES = [
  {
    id: "crew_iss",
    label: "Crew to ISS",
    blurb: "Four crew to the International Space Station.",
    crewed: true,
    ready: true,
    accel_limit_g: 4.0,
    default_payload_kg: 12000,
  },
  {
    id: "cargo_iss",
    label: "Cargo to ISS",
    blurb: "Pressurised and unpressurised cargo to the International Space Station.",
    crewed: false,
    ready: true,
    accel_limit_g: 6.0,
    default_payload_kg: 12000,
  },
  { id: "meo_gps", label: "GPS (MEO)", blurb: "Navigation satellite to medium Earth orbit.", crewed: false, ready: false, accel_limit_g: 6.0, default_payload_kg: 4000 },
  { id: "geo_comm", label: "GEO comms", blurb: "Communications satellite to geostationary transfer.", crewed: false, ready: false, accel_limit_g: 6.0, default_payload_kg: 6000 },
];

export const profileById = (id) => PROFILES.find((p) => p.id === id) || null;

/* ------------------------------------------------------------------ vehicles */

/**
 * What the simulator says this vehicle can lift, and how far that is from the published figure.
 *
 * `capacity_kg` is the SIMULATED figure, not the published one, because that is what the mission
 * will actually be flown against. Offering a payload the published number allows but the simulator
 * cannot lift would send the user to a mission that fails for a reason the wizard already knew.
 */
export function vehicleCapability(v) {
  const sv = v.sim_validation || null;
  const published = v.payload_to_leo_kg_published;
  const sim = sv && sv.payload_to_leo_kg_sim != null ? sv.payload_to_leo_kg_sim : null;
  return {
    published_kg: published,
    capacity_kg: sim != null ? sim : published,
    error_pct: sv ? sv.error_pct : null,
    tolerance_pct: sv ? sv.tolerance_pct : null,
    measured: sim != null,
    within_tolerance: !!(sv && sv.pass),
    sim_version: sv ? sv.sim_version : null,
    note: sv ? sv.note : null,
  };
}

/** Human sentence for a vehicle card. Never quotes a published figure without the measured gap. */
export function capabilityLabel(v) {
  const c = vehicleCapability(v);
  if (!c.measured) {
    return c.published_kg
      ? `${Math.round(c.published_kg).toLocaleString()} kg published; not yet simulated`
      : "no published payload figure";
  }
  const sign = c.error_pct >= 0 ? "+" : "";
  const verdict = c.within_tolerance
    ? `within the stated ±${c.tolerance_pct}%`
    : `OUTSIDE the stated ±${c.tolerance_pct}%`;
  return (
    `${Math.round(c.capacity_kg).toLocaleString()} kg simulated vs ` +
    `${Math.round(c.published_kg).toLocaleString()} kg published (${sign}${c.error_pct}%, ${verdict})`
  );
}

/**
 * Vehicles that can fly this mission, each with why it can or cannot.
 *
 * Nothing is hidden: a vehicle that fails is returned with `eligible: false` and a reason, so the
 * wizard can show the whole library and explain the gaps instead of presenting a short list that
 * looks arbitrary.
 */
export function rankVehicles(vehicles, { profile, payload_kg, pads = [] }) {
  const p = profileById(profile);
  const out = (vehicles || []).map((v) => {
    const cap = vehicleCapability(v);
    const reasons = [];
    if (v.kind !== "real") reasons.push("fictional archetype, not a real vehicle");
    if (p && p.crewed && !v.crew_rated) reasons.push("not crew capable");
    if (!cap.within_tolerance) {
      reasons.push(
        cap.measured
          ? `the simulator is ${cap.error_pct > 0 ? "+" : ""}${cap.error_pct}% from its published payload, outside the stated tolerance`
          : "no measured payload comparison",
      );
    }
    if (payload_kg != null && cap.capacity_kg != null && payload_kg > cap.capacity_kg) {
      reasons.push(
        `payload ${Math.round(payload_kg).toLocaleString()} kg exceeds the simulated ` +
          `${Math.round(cap.capacity_kg).toLocaleString()} kg`,
      );
    }
    const padsFor = (pads || []).filter((pad) => (pad.supported_rocket_ids || []).includes(v.id));
    if (pads.length && !padsFor.length) reasons.push("no pad in the library launches it");
    return { vehicle: v, capability: cap, pads: padsFor, eligible: reasons.length === 0, reasons };
  });
  // Eligible first, then the ones with most capacity: the usable choices lead.
  return out.sort(
    (a, b) =>
      Number(b.eligible) - Number(a.eligible) ||
      (b.capability.capacity_kg || 0) - (a.capability.capacity_kg || 0),
  );
}

export const padsForVehicle = (pads, rocketId) =>
  (pads || []).filter((p) => (p.supported_rocket_ids || []).includes(rocketId));

/* ------------------------------------------------------------------ windows */

/** Flyable windows, soonest first, with the time to launch worked out against `nowMs`. */
export function usableWindows(windowsFile, nowMs = Date.now()) {
  const all = (windowsFile && windowsFile.windows) || [];
  return all
    .filter((w) => w.flyable)
    .map((w) => ({ ...w, t_ms: w.t_launch_unix * 1000, in_s: w.t_launch_unix - nowMs / 1000 }))
    .sort((a, b) => a.t_launch_unix - b.t_launch_unix);
}

/** True when the windows file is a labelled placeholder rather than computed geometry. */
export const isPlaceholderWindows = (f) =>
  String((f && f._note) || "").toUpperCase().includes("PLACEHOLDER");

/* ------------------------------------------------------------------ constraints preview */

/**
 * What can be said about a mission BEFORE it is simulated.
 *
 * Deliberately not the verifier: the verifier judges a flown attempt and is ground truth. This is a
 * pre-flight read of the things already known -- is there a real window, what is the space weather,
 * can this vehicle's acceleration ever satisfy this profile's limit. Each item says `go`, `warn`,
 * `no-go` or `unknown`, and `unknown` is never dressed up as `go`.
 */
export function constraintPreview({ profile, vehicle, windowsFile, window: win, kp, payload_kg }) {
  const p = profileById(profile);
  const items = [];
  const add = (id, name, status, detail) => items.push({ id, name, status, detail });

  // C04 - a real launch window
  if (!windowsFile) {
    add("C04", "Launch window", "unknown", "No windows file loaded.");
  } else if (isPlaceholderWindows(windowsFile)) {
    add("C04", "Launch window", "warn", "These windows are a labelled PLACEHOLDER, not computed geometry.");
  } else if (!win) {
    add("C04", "Launch window", "no-go", "No flyable window selected.");
  } else {
    add(
      "C04",
      "Launch window",
      "go",
      `${win.id} at ${new Date(win.t_ms).toISOString().slice(0, 16)}Z, inertial azimuth ` +
        `${win.azimuth_deg.toFixed(1)}°, plane error ${win.plane_error_deg.toFixed(2)}°.`,
    );
  }

  // C03 - peak acceleration. The measured finding, surfaced before the user commits.
  if (p && vehicle) {
    if (p.crewed) {
      add(
        "C03",
        "Peak acceleration",
        "no-go",
        `The ascent model burns each stage to depletion at constant thrust, so peak acceleration is ` +
          `set by the vehicle rather than by the flight plan. Measured 4.4–4.6 g against this ` +
          `profile's ${p.accel_limit_g.toFixed(1)} g limit at every payload tried, so no crew ` +
          `mission can pass today. Throttling is needed (tracked for the ascent lane).`,
      );
    } else {
      add("C03", "Peak acceleration", "warn", `Limit ${p.accel_limit_g.toFixed(1)} g; measured near 5.5 g on a comparable flight. Judged by the verifier after the flight, not here.`);
    }
  }

  // C05 - weather. Kp is real and live, but it is space weather, not launch weather.
  if (kp == null) {
    add("C05", "Space weather", "unknown", "Planetary Kp unavailable.");
  } else {
    const status = kp >= 7 ? "no-go" : kp >= 5 ? "warn" : "go";
    add("C05", "Space weather", status, `Planetary Kp ${kp.toFixed(1)} (NOAA SWPC, live). Context only: nothing in the simulation uses it.`);
  }

  // Payload against what the simulator can actually lift
  if (vehicle && payload_kg != null) {
    const cap = vehicleCapability(vehicle);
    if (cap.capacity_kg == null) add("P", "Payload", "unknown", "No capacity figure for this vehicle.");
    else if (payload_kg > cap.capacity_kg) {
      add("P", "Payload", "no-go", `${Math.round(payload_kg).toLocaleString()} kg exceeds the simulated ${Math.round(cap.capacity_kg).toLocaleString()} kg.`);
    } else {
      const pct = Math.round((100 * payload_kg) / cap.capacity_kg);
      add("P", "Payload", pct > 92 ? "warn" : "go", `${pct}% of the simulated ${Math.round(cap.capacity_kg).toLocaleString()} kg capacity.`);
    }
  }

  const worst = items.some((i) => i.status === "no-go")
    ? "no-go"
    : items.some((i) => i.status === "warn" || i.status === "unknown")
      ? "warn"
      : "go";
  return { items, verdict: worst };
}

/* ------------------------------------------------------------------ the request */

/** A stable, readable mission id. */
export function missionId(profile, nowMs = Date.now()) {
  const iso = new Date(nowMs).toISOString(); // 2026-11-11T04:46:40.000Z
  const stamp = `${iso.slice(2, 10).replace(/-/g, "")}T${iso.slice(11, 16).replace(":", "")}`;
  return `msn_${profile}_${stamp}`;
}

/**
 * Build a `mission_request` (contract 6.1).
 *
 * `fuel_ref_kg` and `time_ref_s` are the normalisers in the cost function (section 5.2), so they
 * must be real scales rather than 1.0, or the fuel and time terms cannot be compared with each
 * other. Fuel is referenced to the vehicle's own propellant load and time to a day.
 */
export function buildMissionRequest({
  profile,
  payload_kg,
  crew,
  vehicle,
  pad,
  window: win,
  target_norad = 25544,
  w_fuel = 1.0,
  w_time = 0.5,
  max_attempts = 24,
  max_wall_s = 600,
  seed = 7,
  nowMs = Date.now(),
  id = null,
}) {
  const p = profileById(profile);
  const propellant = (vehicle.stages || []).reduce((a, s) => a + (s.prop_kg || 0), 0);
  return {
    schema_version: 1,
    id: id || missionId(profile, nowMs),
    profile,
    payload: { mass_kg: Number(payload_kg), crew: p && p.crewed ? Number(crew ?? 4) : 0 },
    rocket_id: vehicle.id,
    pad_id: pad.id,
    target: { norad_id: target_norad },
    window_id: win ? win.id : "w0",
    constraints_profile: p && p.crewed ? "crew_default" : "cargo_default",
    objective: {
      w_fuel,
      w_time,
      fuel_ref_kg: Math.max(1, Math.round(propellant)),
      time_ref_s: 86400,
    },
    budget: { max_attempts: Number(max_attempts), max_wall_s: Number(max_wall_s) },
    seed: Number(seed),
  };
}

/** Which step a wizard state is ready to advance past, and why not. */
export function stepBlockers(state) {
  const p = profileById(state.profile);
  return {
    1: p && p.ready ? [] : ["Choose a mission type that is ready."],
    2:
      state.payload_kg > 0
        ? p && p.crewed && !(state.crew > 0)
          ? ["A crew mission needs at least one crew member."]
          : []
        : ["Set a payload mass."],
    3: state.vehicle ? [] : ["Choose a vehicle."],
    4: state.pad ? [] : ["Choose a pad."],
    5: state.window ? [] : ["Choose a launch window."],
    6: [],
  };
}
