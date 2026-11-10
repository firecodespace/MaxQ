/* Test helper: fly one ascent with the browser simulator and print the result as JSON.
 *
 *   node tests/ascent/js_ascent_runner.mjs '{"rocket":{...},"opts":{...}}'
 *
 * Used by tests/ascent/test_js_parity.py, which feeds it the same rocket and the same launch
 * conditions it gives ascent/sim.py and then compares the two sample by sample. Not used by the site.
 */

import { simulateAscent } from "../../web/js/sim3d/ascent.js";

const arg = process.argv[2];
if (!arg) {
  console.error("usage: node js_ascent_runner.mjs '<json with {rocket, opts}>'");
  process.exit(2);
}

let payload;
try {
  payload = JSON.parse(arg);
} catch (e) {
  console.error("bad JSON argument: " + e.message);
  process.exit(2);
}

const res = simulateAscent(payload.rocket, payload.opts || {});
process.stdout.write(
  JSON.stringify({
    trajectory: res.trajectory,
    extra: res.extra,
    diagnostics: {
      perigee_km: res.perigeeKm,
      apogee_km: res.apogeeKm,
      eccentricity: res.eccentricity,
      insertion_speed_mps: res.insertionSpeedMps,
      insertion_alt_m: res.insertionAltM,
      max_q_pa: res.maxQPa,
      t_max_q_s: res.tMaxQS,
      gravity_loss_mps: res.gravityLossMps,
      drag_loss_mps: res.dragLossMps,
      total_loss_mps: res.totalLossMps,
      t_insertion_s: res.tInsertionS,
      reached_orbit: res.reachedOrbit,
    },
  }),
);
