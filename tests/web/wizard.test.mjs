/* The mission wizard's decisions (WP2-07).
 *
 * These guard the honesty rules, which is why they are tests and not comments. A vehicle that
 * missed its payload tolerance must never be quietly offered; a published payload must never be
 * shown without the measured gap beside it; a crew mission must never be presented as flyable while
 * the simulator cannot throttle; and the request that reaches the API must satisfy its contract.
 *
 * Fixtures are the real exported library, so a change to the vehicle data that breaks the wizard
 * fails here rather than in a browser.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  PROFILES,
  buildMissionRequest,
  capabilityLabel,
  constraintPreview,
  isPlaceholderWindows,
  missionId,
  padsForVehicle,
  profileById,
  rankVehicles,
  stepBlockers,
  usableWindows,
  vehicleCapability,
} from "../../web/js/wizard-logic.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const load = (p) => JSON.parse(readFileSync(join(ROOT, p), "utf8"));

const ROCKETS = load("web/public/data/rockets.json");
const PADS = load("web/public/data/pads.json");
const WINDOWS = load("web/public/data/windows_crew_iss_cape_canaveral.json");

const byId = (id) => ROCKETS.find((v) => v.id === id);

/* ------------------------------------------------------------------ fixtures are what we think */

test("the exported library has what the wizard needs", () => {
  assert.ok(ROCKETS.length >= 3, "fewer than three vehicles exported");
  assert.ok(PADS.length >= 3, "fewer than three pads exported");
  for (const v of ROCKETS.filter((x) => x.kind === "real")) {
    assert.ok(v.sources.length, `${v.id} has no cited source`);
    assert.ok(v.sim_validation, `${v.id} has no measured payload comparison`);
  }
});

/* ------------------------------------------------------------------ capability */

test("capacity is the SIMULATED payload, not the published one", () => {
  // Offering what the brochure allows but the simulator cannot lift sends the user to a mission
  // that fails for a reason the wizard already knew.
  const f9 = vehicleCapability(byId("falcon9_block5"));
  assert.equal(f9.measured, true);
  assert.equal(f9.capacity_kg, byId("falcon9_block5").sim_validation.payload_to_leo_kg_sim);
  assert.notEqual(f9.capacity_kg, f9.published_kg);
  assert.ok(f9.within_tolerance);
});

test("a published payload is never shown without the measured gap", () => {
  for (const v of ROCKETS.filter((x) => x.kind === "real")) {
    const label = capabilityLabel(v);
    assert.match(label, /published/, `${v.id}: ${label}`);
    assert.match(label, /%/, `${v.id} quotes a payload with no measured error: ${label}`);
  }
  assert.match(capabilityLabel(byId("atlas_v_401")), /OUTSIDE/);
});

/* ------------------------------------------------------------------ ranking */

test("a vehicle outside its payload tolerance is offered but marked ineligible, not hidden", () => {
  const ranked = rankVehicles(ROCKETS, { profile: "cargo_iss", payload_kg: 4000, pads: PADS });
  const atlas = ranked.find((r) => r.vehicle.id === "atlas_v_401");
  assert.ok(atlas, "Atlas V disappeared from the list rather than being explained");
  assert.equal(atlas.eligible, false);
  assert.ok(
    atlas.reasons.some((r) => /outside the stated tolerance/.test(r)),
    atlas.reasons.join("; "),
  );
});

test("a crew mission only offers crew-capable vehicles", () => {
  const ranked = rankVehicles(ROCKETS, { profile: "crew_iss", payload_kg: 12000, pads: PADS });
  for (const r of ranked) {
    if (!r.vehicle.crew_rated) {
      assert.equal(r.eligible, false, `${r.vehicle.id} offered for a crew mission`);
      assert.ok(r.reasons.includes("not crew capable"));
    }
  }
  const eligible = ranked.filter((r) => r.eligible);
  assert.ok(eligible.length >= 1, "no vehicle can fly a crew mission at all");
  assert.ok(eligible.every((r) => r.vehicle.crew_rated));
});

test("a payload over the simulated capacity is refused with the numbers", () => {
  const cap = vehicleCapability(byId("falcon9_block5")).capacity_kg;
  const ranked = rankVehicles(ROCKETS, { profile: "cargo_iss", payload_kg: cap + 5000, pads: PADS });
  const f9 = ranked.find((r) => r.vehicle.id === "falcon9_block5");
  assert.equal(f9.eligible, false);
  assert.ok(f9.reasons.some((r) => /exceeds the simulated/.test(r)), f9.reasons.join("; "));
});

test("eligible vehicles are listed first", () => {
  const ranked = rankVehicles(ROCKETS, { profile: "cargo_iss", payload_kg: 5000, pads: PADS });
  const firstIneligible = ranked.findIndex((r) => !r.eligible);
  if (firstIneligible !== -1) {
    assert.ok(ranked.slice(firstIneligible).every((r) => !r.eligible), "ordering interleaves them");
  }
});

test("pads are filtered to the ones that launch the chosen vehicle", () => {
  const pads = padsForVehicle(PADS, "falcon9_block5");
  assert.ok(pads.length >= 1);
  assert.ok(pads.every((p) => p.supported_rocket_ids.includes("falcon9_block5")));
  assert.equal(padsForVehicle(PADS, "no_such_rocket").length, 0);
});

/* ------------------------------------------------------------------ windows */

test("only flyable windows are offered, soonest first", () => {
  const ws = usableWindows(WINDOWS, Date.parse("2026-11-09T22:00:00Z"));
  assert.ok(ws.length, "no flyable windows in the real file");
  assert.ok(ws.every((w) => w.flyable));
  for (let i = 1; i < ws.length; i++) {
    assert.ok(ws[i].t_launch_unix >= ws[i - 1].t_launch_unix, "windows out of order");
  }
});

test("a placeholder windows file is recognised as one", () => {
  assert.equal(isPlaceholderWindows(WINDOWS), false);
  assert.equal(isPlaceholderWindows({ _note: "PLACEHOLDER nominal daily slot" }), true);
});

/* ------------------------------------------------------------------ constraint preview */

test("a crew mission is told it cannot pass, before anything is simulated", () => {
  // The measured finding from WP2-06: no throttling, so peak g is 4.4-4.6 against a 4.0 g limit at
  // every payload tried. The wizard must say so rather than let the user launch into it.
  const pv = constraintPreview({
    profile: "crew_iss",
    vehicle: byId("falcon9_block5"),
    windowsFile: WINDOWS,
    window: usableWindows(WINDOWS)[0],
    kp: 3.7,
    payload_kg: 12000,
  });
  const c03 = pv.items.find((i) => i.id === "C03");
  assert.equal(c03.status, "no-go");
  assert.match(c03.detail, /throttl/i);
  assert.equal(pv.verdict, "no-go");
});

test("a cargo mission with a real window and quiet weather reads as go", () => {
  const pv = constraintPreview({
    profile: "cargo_iss",
    vehicle: byId("falcon9_block5"),
    windowsFile: WINDOWS,
    window: usableWindows(WINDOWS)[0],
    kp: 2.0,
    payload_kg: 8000,
  });
  assert.notEqual(pv.verdict, "no-go");
  assert.equal(pv.items.find((i) => i.id === "C04").status, "go");
  assert.equal(pv.items.find((i) => i.id === "C05").status, "go");
});

test("missing inputs read as unknown, never as go", () => {
  const pv = constraintPreview({ profile: "cargo_iss", vehicle: byId("falcon9_block5"), windowsFile: null, window: null, kp: null, payload_kg: 8000 });
  assert.equal(pv.items.find((i) => i.id === "C04").status, "unknown");
  assert.equal(pv.items.find((i) => i.id === "C05").status, "unknown");
  assert.ok(pv.items.every((i) => i.status !== "go" || i.id === "P"));
});

test("a storm is a warning and a severe storm is a no-go", () => {
  const at = (kp) =>
    constraintPreview({ profile: "cargo_iss", vehicle: byId("falcon9_block5"), windowsFile: WINDOWS, window: usableWindows(WINDOWS)[0], kp, payload_kg: 8000 })
      .items.find((i) => i.id === "C05").status;
  assert.equal(at(2.0), "go");
  assert.equal(at(5.5), "warn");
  assert.equal(at(7.5), "no-go");
});

test("space weather is labelled as context, because nothing in the simulation uses it", () => {
  const pv = constraintPreview({ profile: "cargo_iss", vehicle: byId("falcon9_block5"), windowsFile: WINDOWS, window: usableWindows(WINDOWS)[0], kp: 3.0, payload_kg: 8000 });
  assert.match(pv.items.find((i) => i.id === "C05").detail, /context only/i);
});

/* ------------------------------------------------------------------ the request */

test("the built request satisfies the mission_request contract", () => {
  const req = buildMissionRequest({
    profile: "cargo_iss",
    payload_kg: 9000,
    vehicle: byId("falcon9_block5"),
    pad: padsForVehicle(PADS, "falcon9_block5")[0],
    window: usableWindows(WINDOWS)[0],
    nowMs: Date.parse("2026-11-11T04:46:40Z"),
  });
  // Mirrors contracts/mission_request.schema.json; the Python side validates it for real.
  assert.equal(req.schema_version, 1);
  for (const k of ["id", "profile", "payload", "rocket_id", "pad_id", "target", "window_id",
                   "constraints_profile", "objective", "budget", "seed"]) {
    assert.ok(k in req, `missing ${k}`);
  }
  assert.equal(req.payload.crew, 0, "a cargo mission must not carry crew");
  assert.equal(req.constraints_profile, "cargo_default");
  assert.equal(req.target.norad_id, 25544);
  assert.ok(req.budget.max_attempts >= 1 && req.budget.max_wall_s > 0);
});

test("cost normalisers are real scales, not 1.0", () => {
  // Section 5.2 divides by these. Leaving them at 1.0 makes the fuel term dwarf the time term and
  // the weights meaningless.
  const v = byId("falcon9_block5");
  const req = buildMissionRequest({
    profile: "cargo_iss", payload_kg: 9000, vehicle: v,
    pad: padsForVehicle(PADS, v.id)[0], window: usableWindows(WINDOWS)[0],
  });
  const propellant = v.stages.reduce((a, s) => a + s.prop_kg, 0);
  assert.equal(req.objective.fuel_ref_kg, Math.round(propellant));
  assert.equal(req.objective.time_ref_s, 86400);
});

test("a crew request carries crew and the crew constraints profile", () => {
  const req = buildMissionRequest({
    profile: "crew_iss", payload_kg: 12000, crew: 4, vehicle: byId("falcon9_block5"),
    pad: padsForVehicle(PADS, "falcon9_block5")[0], window: usableWindows(WINDOWS)[0],
  });
  assert.equal(req.payload.crew, 4);
  assert.equal(req.constraints_profile, "crew_default");
});

test("mission ids are readable and stable for a given time", () => {
  const a = missionId("crew_iss", Date.parse("2026-11-11T04:46:40Z"));
  assert.equal(a, missionId("crew_iss", Date.parse("2026-11-11T04:46:40Z")));
  assert.match(a, /^msn_crew_iss_\d{6}T\d{4}$/);
});

/* ------------------------------------------------------------------ step gating */

test("each step states what is blocking it", () => {
  assert.deepEqual(stepBlockers({ profile: "cargo_iss" })[1], []);
  assert.ok(stepBlockers({ profile: "meo_gps" })[1].length, "an unready profile should block");
  assert.ok(stepBlockers({ profile: "crew_iss", payload_kg: 12000, crew: 0 })[2].length);
  assert.deepEqual(stepBlockers({ profile: "crew_iss", payload_kg: 12000, crew: 4 })[2], []);
  assert.ok(stepBlockers({ profile: "cargo_iss", payload_kg: 9000 })[3].length);
  assert.ok(stepBlockers({ profile: "cargo_iss", payload_kg: 9000, vehicle: {} })[4].length);
  assert.ok(stepBlockers({ profile: "cargo_iss", payload_kg: 9000, vehicle: {}, pad: {} })[5].length);
});

test("profiles that are not ready are visible but not selectable", () => {
  assert.ok(PROFILES.length >= 4, "the later profiles should still be listed");
  assert.equal(profileById("meo_gps").ready, false);
  assert.equal(profileById("cargo_iss").ready, true);
});

/* ------------------------------------------------------------------ the DOM helper
 *
 * Not wizard logic, but the wizard is where it bit: every Next button in the flow was built with
 * {disabled: !canAdvance()}, and setAttribute("disabled", false) leaves disabled="false" on the
 * element, which disables it. The whole wizard was unusable and looked fine.
 */

test("el() treats boolean attributes as present-or-absent, not as values", async () => {
  const calls = [];
  const element = () => {
    const attrs = new Map();
    return {
      attrs,
      className: "",
      textContent: "",
      setAttribute: (k, v) => attrs.set(k, v),
      removeAttribute: (k) => attrs.delete(k),
      addEventListener: (k) => calls.push(k),
      append: () => {},
    };
  };
  globalThis.document = { createElement: () => element() };
  const { el } = await import("../../web/js/util.js");

  assert.equal(el("button", { disabled: false }).attrs.has("disabled"), false,
    "disabled: false must not leave the attribute on the element");
  assert.equal(el("button", { disabled: true }).attrs.get("disabled"), "");
  assert.equal(el("div", { hidden: false }).attrs.has("hidden"), false);
  assert.equal(el("input", { checked: true }).attrs.get("checked"), "");
  // Ordinary attributes are untouched.
  assert.equal(el("a", { href: "x" }).attrs.get("href"), "x");
  assert.equal(el("a", { title: null }).attrs.has("title"), false);
  delete globalThis.document;
});
