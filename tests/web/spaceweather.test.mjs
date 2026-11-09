// WP-16 unit tests (Node's built-in runner: `node --test tests/web`). Real NOAA SWPC rows fetched 2026-11-09.
import test from "node:test";
import assert from "node:assert/strict";
import { currentKp, gScale, kpClass, loadSpaceWeather, parseKpForecast, peakForecast } from "../../web/js/spaceweather.js";

const REAL = [
  { time_tag: "2026-11-02T00:00:00", kp: 1.0, observed: "observed", noaa_scale: null },
  { time_tag: "2026-11-02T03:00:00", kp: 2.0, observed: "observed", noaa_scale: null },
  { time_tag: "2026-11-09T18:00:00", kp: 2.67, observed: "observed", noaa_scale: null },
  { time_tag: "2026-11-09T21:00:00", kp: 3.67, observed: "estimated", noaa_scale: null },
  { time_tag: "2026-11-10T00:00:00", kp: 3.67, observed: "predicted", noaa_scale: null },
  { time_tag: "2026-11-10T03:00:00", kp: 5.33, observed: "predicted", noaa_scale: "G1" },
  { time_tag: "2026-11-10T06:00:00", kp: 2.67, observed: "predicted", noaa_scale: null },
];
const T = (s) => Date.parse(s + "Z");

test("parse: UTC times, kinds, sorted, scale filled in", () => {
  const shuffled = [REAL[3], REAL[0], REAL[5], REAL[1], REAL[2], REAL[4], REAL[6]];
  const pts = parseKpForecast(shuffled);
  assert.equal(pts.length, 7);
  assert.deepEqual(pts.map((p) => p.t), [...pts.map((p) => p.t)].sort((a, b) => a - b));
  assert.equal(pts[0].t, T("2026-11-02T00:00:00"));
  assert.equal(pts.find((p) => p.kp === 5.33).scale, "G1");
  assert.equal(pts.find((p) => p.kp === 3.67 && p.kind === "estimated").kind, "estimated");
});

test("parse: accepts the observed-only file shape (capital Kp) and tolerates junk rows", () => {
  const pts = parseKpForecast([
    { time_tag: "2026-11-02T00:00:00", Kp: 1.0, a_running: 4 },
    { time_tag: "2026-11-02T03:00:00", Kp: 2.0, a_running: 7 },
    { time_tag: "2026-11-02T06:00:00", Kp: 3.0, a_running: 15 },
    { time_tag: "nonsense", Kp: 3 },
    { time_tag: "2026-11-02T09:00:00", Kp: 42 },
    null,
  ]);
  assert.equal(pts.length, 3);
  assert.ok(pts.every((p) => p.kind === "observed"));
});

test("parse: refuses unusable payloads loudly", () => {
  assert.throws(() => parseKpForecast({ error: "x" }), /not a list/);
  assert.throws(() => parseKpForecast([]), /no usable rows/);
  assert.throws(() => parseKpForecast([{ time_tag: "2026-11-02T00:00:00", kp: "high" }]), /no usable rows/);
});

test("fallback G scale uses the integer part of Kp (conservative); NOAA's own label wins in parse", () => {
  assert.equal(gScale(4.0), null);
  assert.equal(gScale(4.67), null); // "5-" is deliberately not counted by the fallback
  assert.equal(gScale(5), "G1");
  assert.equal(gScale(5.33), "G1");
  assert.equal(gScale(6.33), "G2");
  assert.equal(gScale(7), "G3");
  assert.equal(gScale(8.67), "G4"); // "9-" is still G4
  assert.equal(gScale(9), "G5");
  const pts = parseKpForecast([...REAL.slice(0, 2), { time_tag: "2026-11-02T06:00:00", kp: 4.0, observed: "observed", noaa_scale: "G2" }]);
  assert.equal(pts[2].scale, "G2"); // provided label is kept verbatim
});

test("activity classes", () => {
  assert.equal(kpClass(2.67).id, "quiet");
  assert.equal(kpClass(4).id, "active");
  assert.equal(kpClass(5).id, "storm");
  assert.equal(kpClass(7.33).id, "severe");
});

test("current value is the latest non-predicted point at or before now", () => {
  const pts = parseKpForecast(REAL);
  const cur = currentKp(pts, T("2026-11-09T21:30:00"));
  assert.equal(cur.kind, "estimated");
  assert.equal(cur.kp, 3.67);
  assert.equal(currentKp(pts, T("2026-11-09T19:00:00")).kind, "observed");
  // with `now` after the last row, still never returns a predicted value
  assert.notEqual(currentKp(pts, T("2026-11-20T00:00:00")).kind, "predicted");
});

test("forecast peak within the horizon", () => {
  const pts = parseKpForecast(REAL);
  const peak = peakForecast(pts, T("2026-11-09T21:30:00"), 72);
  assert.equal(peak.kp, 5.33);
  assert.equal(peak.scale, "G1");
  assert.equal(peakForecast(pts, T("2026-11-09T21:30:00"), 1), null);
});

test("load: HTTP errors and bad JSON reject (no stale data is returned)", async () => {
  const real = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
    await assert.rejects(loadSpaceWeather("https://example.test/a", { maxAgeMs: 0 }), /503/);
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ not: "a list" }) });
    await assert.rejects(loadSpaceWeather("https://example.test/b", { maxAgeMs: 0 }), /not a list/);
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => REAL });
    const ok = await loadSpaceWeather("https://example.test/c", { maxAgeMs: 0 });
    assert.equal(ok.length, 7);
    globalThis.fetch = async () => {
      throw new Error("network down");
    };
    await assert.rejects(loadSpaceWeather("https://example.test/d", { maxAgeMs: 0 }), /network down/);
  } finally {
    globalThis.fetch = real;
  }
});

test("load: caches within maxAge and refetches after", async () => {
  const real = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => (calls++, REAL) });
    await loadSpaceWeather("https://example.test/cache", { maxAgeMs: 60000 });
    await loadSpaceWeather("https://example.test/cache", { maxAgeMs: 60000 });
    assert.equal(calls, 1);
    await loadSpaceWeather("https://example.test/cache", { maxAgeMs: 0 });
    assert.equal(calls, 2);
  } finally {
    globalThis.fetch = real;
  }
});
