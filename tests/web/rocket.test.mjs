/* The vehicle geometry and the plume are derived from each rocket's own contract numbers, so they
 * can be checked against physics rather than against a screenshot.
 *
 * What these tests are really protecting: the claim, made in the UI, that nothing about the rocket's
 * shape was drawn by hand. If a dimension stops following from the stage masses, thrust and
 * reference area, that claim becomes false and these fail.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import {
  GAMMA,
  RHO_HYDROLOX,
  RHO_KEROLOX,
  bulkDensityOf,
  diameterOf,
  machFromAreaRatio,
  nozzleOf,
  plumeState,
  propellantOf,
  stageLengthM,
  vehicleDimensions,
} from "../../web/js/sim3d/rocket.js";
import { idealDeltaV, liftoffMass } from "../../web/js/sim3d/ascent.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const rocket = (id) =>
  JSON.parse(readFileSync(join(ROOT, "web", "public", "data", `rocket_${id}.json`), "utf8"));

const ARCHETYPES = ["medlift_kerolox_a", "crewlift_kerolox_b", "heavylift_hydrolox_c"];

test("the exported archetypes are contract-shaped and loadable", () => {
  for (const id of ARCHETYPES) {
    const r = rocket(id);
    assert.equal(r.schema_version, 1);
    assert.equal(r.id, id);
    assert.ok(r.stages.length >= 2, `${id} needs two stages`);
    for (const s of r.stages) {
      for (const k of ["dry_kg", "prop_kg", "thrust_n", "isp_s", "cd", "area_m2"]) {
        assert.ok(Number.isFinite(s[k]) && s[k] > 0, `${id}: ${k}`);
      }
    }
  }
});

test("the area-Mach inversion reproduces textbook values for gamma = 1.2", () => {
  assert.equal(GAMMA, 1.2);
  // Standard results: an area ratio of 16 gives about Mach 3.6, and 80 gives about Mach 4.7.
  assert.ok(Math.abs(machFromAreaRatio(16) - 3.6) < 0.05, machFromAreaRatio(16));
  assert.ok(Math.abs(machFromAreaRatio(80) - 4.73) < 0.05, machFromAreaRatio(80));
  // Monotone in the area ratio, and never subsonic.
  let prev = 1;
  for (const e of [2, 5, 10, 25, 60, 150]) {
    const m = machFromAreaRatio(e);
    assert.ok(m > prev, `Mach must rise with area ratio (${e})`);
    prev = m;
  }
});

test("a sea-level first stage leaves the nozzle near ambient, a vacuum stage far below it", () => {
  const r = rocket("medlift_kerolox_a");
  const s1 = nozzleOf(r.stages[0], true);
  const s2 = nozzleOf(r.stages[1], false);
  // The whole point of expanding a first stage to only about 16: it must work at 101 kPa without
  // the flow separating, so the exit sits a little under ambient.
  assert.ok(s1.exitPressure > 40e3 && s1.exitPressure < 101325, `${s1.exitPressure} Pa`);
  // A vacuum stage is expanded much further, so its exit pressure is a small fraction of that.
  assert.ok(s2.exitPressure < s1.exitPressure / 5, `${s2.exitPressure} Pa`);
});

test("engine count follows from whether the hardware fits", () => {
  for (const id of ARCHETYPES) {
    const d = vehicleDimensions(rocket(id));
    const first = d.stages[0].nozzle;
    const upper = d.stages[1].nozzle;
    assert.ok([1, 4, 9].includes(first.count));
    // Every bell has to sit inside the stage it hangs off.
    for (const st of d.stages) {
      assert.ok(
        st.nozzle.exitDiameter < st.diameter,
        `${id}: a ${st.nozzle.exitDiameter} m bell cannot fit a ${st.diameter} m stage`,
      );
    }
    // A highly expanded vacuum stage ends up with one big bell, as real ones do.
    assert.equal(upper.count, 1, `${id}: upper stage should be a single engine`);
  }
});

test("stage length follows from the propellant it has to hold", () => {
  const r = rocket("medlift_kerolox_a");
  const s = r.stages[0];
  const len = stageLengthM(s);
  // Recompute independently: volume = mass / density, divided by the usable cross-section.
  const expected = s.prop_kg / (RHO_KEROLOX * s.area_m2 * 0.9);
  assert.ok(Math.abs(len - expected) < 1e-9);
  // Doubling the propellant must double the tank.
  assert.ok(Math.abs(stageLengthM({ ...s, prop_kg: s.prop_kg * 2 }) - len * 2) < 1e-9);
});

test("hydrogen stages come out much longer than kerosene ones for the same mass", () => {
  const kero = { dry_kg: 1, prop_kg: 100000, thrust_n: 1e6, isp_s: 300, cd: 0.3, area_m2: 10 };
  const hydro = { ...kero, isp_s: 445 };
  assert.equal(propellantOf(kero), "kerolox");
  assert.equal(propellantOf(hydro), "hydrolox");
  assert.equal(bulkDensityOf(hydro), RHO_HYDROLOX);
  // Hydrogen is about a third the bulk density, so the tank is about three times as long.
  const ratio = stageLengthM(hydro) / stageLengthM(kero);
  assert.ok(ratio > 2.5 && ratio < 3.2, `ratio ${ratio}`);
});

test("diameter comes from the same reference area the drag model uses", () => {
  const s = { dry_kg: 1, prop_kg: 1, thrust_n: 1, isp_s: 1, cd: 0.3, area_m2: 10.8 };
  assert.ok(Math.abs(diameterOf(s) - 2 * Math.sqrt(10.8 / Math.PI)) < 1e-12);
  assert.ok(Math.abs(diameterOf(s) - 3.708) < 0.01);
});

test("the archetypes come out the right size for their mass", () => {
  // Not a tuned target: these follow from the stage numbers. The check is that they land in the
  // range a vehicle of that mass actually occupies, rather than being metres or kilometres out.
  for (const id of ARCHETYPES) {
    const r = rocket(id);
    const d = vehicleDimensions(r);
    const liftoff = liftoffMass(r);
    assert.ok(d.totalLength > 35 && d.totalLength < 130, `${id}: ${d.totalLength} m`);
    assert.ok(d.maxDiameter > 2 && d.maxDiameter < 12, `${id}: ${d.maxDiameter} m`);
    // Slenderness: real launch vehicles run about 10:1 to 25:1.
    const slender = d.totalLength / d.maxDiameter;
    assert.ok(slender > 8 && slender < 26, `${id}: ratio ${slender}`);
    // It has to be able to leave the pad, and not at an implausible acceleration.
    assert.ok(d.liftoffTWR > 1.15 && d.liftoffTWR < 2.2, `${id}: TWR ${d.liftoffTWR}`);
    assert.ok(liftoff > 100000, `${id}: liftoff mass ${liftoff} kg`);
    // Enough ideal delta-v to be worth flying at all.
    assert.ok(idealDeltaV(r) > 8000, `${id}: ideal dv ${idealDeltaV(r)}`);
  }
});

test("the plume is pinched at sea level and flares as the air thins", () => {
  const r = rocket("medlift_kerolox_a");
  const nz = nozzleOf(r.stages[0], true);
  const T = r.stages[0].thrust_n;

  const pad = plumeState(nz, T, 0);
  const mid = plumeState(nz, T, 20000);
  const vac = plumeState(nz, T, 120000);

  // On the pad the atmosphere is pushing in: the jet is overexpanded, so the half-angle is negative
  // (it necks in) and shock diamonds exist.
  assert.ok(pad.ratio < 1, `pe/pa on the pad was ${pad.ratio}`);
  assert.ok(pad.openDeg < 0, `pad half-angle ${pad.openDeg}`);
  assert.ok(pad.shocks > 0, "an overexpanded jet in atmosphere has shock diamonds");

  // High up there is nothing holding it in, so it flares and the diamonds are gone.
  assert.ok(mid.ratio > 1 && mid.openDeg > 10, `at 20 km: ratio ${mid.ratio}, angle ${mid.openDeg}`);
  assert.equal(vac.shocks, 0, "no shock structure in vacuum");
  assert.ok(vac.openDeg > mid.openDeg, "the flare keeps opening as the air thins");
  assert.ok(vac.openDeg <= 62, "but is capped where the model stops meaning anything");

  // And it gets longer and fainter.
  assert.ok(vac.lengthM > pad.lengthM * 2, `length ${pad.lengthM} -> ${vac.lengthM}`);
  assert.ok(vac.opacity < pad.opacity, "a vacuum plume is thin, not opaque");
});

test("no thrust means no plume", () => {
  const nz = nozzleOf(rocket("medlift_kerolox_a").stages[0], true);
  assert.equal(plumeState(nz, 0, 5000).throttle, 0);
});
