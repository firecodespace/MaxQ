// WP-16: NOAA SWPC planetary Kp index as CONTEXT ONLY (display; nothing in MaxQ's calculations uses it).
// One public, CORS-enabled JSON file holds observed + estimated + predicted values:
// https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json
// Pure functions (parse / scale / class / current) are importable in Node for unit tests; DOM code is only in render*.

export const KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json";
const KINDS = new Set(["observed", "estimated", "predicted"]);

/** Validate and normalise NOAA rows -> [{t (ms UTC), kp, kind, scale}] ascending. Throws on an unusable payload. */
export function parseKpForecast(rows) {
  if (!Array.isArray(rows)) throw new Error("NOAA Kp payload is not a list");
  const pts = [];
  for (const r of rows) {
    if (!r || typeof r.time_tag !== "string") continue;
    const kp = Number(r.kp ?? r.Kp);
    const t = Date.parse(r.time_tag.endsWith("Z") ? r.time_tag : r.time_tag + "Z"); // NOAA time tags are UTC without a zone
    if (!Number.isFinite(kp) || !Number.isFinite(t) || kp < 0 || kp > 9) continue;
    const kind = KINDS.has(r.observed) ? r.observed : "observed";
    pts.push({ t, kp, kind, scale: r.noaa_scale || gScale(kp) });
  }
  if (pts.length < 3) throw new Error("NOAA Kp payload has no usable rows");
  return pts.sort((a, b) => a.t - b.t);
}

/**
 * Fallback NOAA geomagnetic storm scale from Kp (G1 = Kp 5 ... G5 = Kp 9) using the INTEGER PART of Kp, e.g. 8.67 ("9-") is G4.
 * Conservative on purpose: thirds just below a level (4.67 = "5-") are not counted, so this never claims a storm that NOAA does
 * not. NOAA's own `noaa_scale` field is preferred whenever the payload provides it.
 */
export function gScale(kp) {
  const k = Math.floor(kp + 1e-9);
  return k >= 9 ? "G5" : k >= 8 ? "G4" : k >= 7 ? "G3" : k >= 6 ? "G2" : k >= 5 ? "G1" : null;
}

/** Plain-language activity class used for colours. */
export function kpClass(kp) {
  if (kp >= 7) return { id: "severe", label: "severe storm", color: "#ff5f6d" };
  if (kp >= 5) return { id: "storm", label: "storm", color: "#2a6fd6" };
  if (kp >= 4) return { id: "active", label: "active", color: "#4d9fff" };
  return { id: "quiet", label: "quiet", color: "#7cc6ff" };
}

/** Latest non-predicted value at or before `nowMs`; falls back to the latest observed value. */
export function currentKp(points, nowMs = Date.now()) {
  const past = points.filter((p) => p.kind !== "predicted" && p.t <= nowMs);
  return past.length ? past[past.length - 1] : points.filter((p) => p.kind === "observed").pop() || null;
}

/** Highest predicted value in the next `hours` after `nowMs` (null if no forecast rows). */
export function peakForecast(points, nowMs = Date.now(), hours = 72) {
  const fut = points.filter((p) => p.t > nowMs && p.t <= nowMs + hours * 3600e3);
  return fut.length ? fut.reduce((a, b) => (b.kp > a.kp ? b : a)) : null;
}

let cache = null;
/** Fetch (with a timeout and a 5-minute in-memory cache). Rejects on network/format problems; never returns stale data on error. */
export async function loadSpaceWeather(url = KP_URL, { timeoutMs = 8000, maxAgeMs = 5 * 60e3 } = {}) {
  if (cache && cache.url === url && Date.now() - cache.at < maxAgeMs) return cache.points;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctl.signal, cache: "no-cache" });
    if (!r.ok) throw new Error(`NOAA SWPC responded ${r.status}`);
    const points = parseKpForecast(await r.json());
    cache = { url, at: Date.now(), points };
    return points;
  } finally {
    clearTimeout(timer);
  }
}

const hhmm = (ms) => new Date(ms).toISOString().slice(5, 16).replace("T", " ") + "Z";

// ------------------------------------------------------------------------------------------ DOM (browser only)

/** Update the header chip, the Sky-tab status line and chart. `points` = data, or null with `err` on failure. */
export function renderKp(points, err) {
  const chip = document.getElementById("kp-chip");
  const status = document.getElementById("sw-status");
  const note = document.getElementById("sw-summary");
  window.__kpPoints = points || null;
  if (!points) {
    if (chip) {
      chip.textContent = "Kp n/a";
      chip.className = "chip";
      chip.title = `Space weather unavailable${err ? ": " + (err.message || err) : ""}. Display only; nothing depends on it.`;
    }
    if (status) status.textContent = "NOAA SWPC unavailable right now; nothing in MaxQ depends on it.";
    if (note) note.textContent = "";
    drawKpChart(document.getElementById("sw-chart"), null);
    return;
  }
  const now = Date.now();
  const cur = currentKp(points, now);
  const peak = peakForecast(points, now);
  if (chip && cur) {
    const c = kpClass(cur.kp);
    chip.textContent = `Kp ${cur.kp.toFixed(1)} · ${c.label}`;
    chip.className = `chip kp-${c.id}`;
    chip.title = `NOAA planetary Kp (${cur.kind}) for the 3 h period starting ${hhmm(cur.t)}${cur.scale ? ", NOAA scale " + cur.scale : ""}. Context only.`;
  }
  if (status && cur) status.textContent = `Current ${cur.kp.toFixed(2)} (${cur.kind}, ${hhmm(cur.t)}); live from NOAA SWPC`;
  if (note) {
    const p = peak ? kpClass(peak.kp) : null;
    note.textContent =
      (peak ? `Forecast peak next 72 h: Kp ${peak.kp.toFixed(1)} (${p.label}${peak.scale ? ", " + peak.scale : ""}) around ${hhmm(peak.t)}. ` : "") +
      "Context only: nothing in MaxQ's calculations uses this. Geomagnetic storms heat and expand the upper atmosphere, increasing drag on low orbits, " +
      "which makes recent TLE-based positions less accurate. Kp is the 3-hourly planetary index (0-9); NOAA's G1 storm level starts at Kp 5.";
  }
  drawKpChart(document.getElementById("sw-chart"), points, now);
}

export function drawKpChart(canvas, points, nowMs = Date.now()) {
  if (!canvas) return;
  const r = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(80, r.width), h = Math.max(80, r.height);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#070c15";
  ctx.fillRect(0, 0, w, h);
  ctx.font = "11px system-ui, sans-serif";
  if (!points) {
    ctx.fillStyle = "#8da3c2";
    ctx.textAlign = "center";
    ctx.fillText("space weather unavailable", w / 2, h / 2);
    return;
  }
  const m = { l: 30, r: 8, t: 10, b: 24 };
  const pts = points.filter((p) => p.t >= nowMs - 3 * 86400e3); // last 3 days + forecast
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t + 3 * 3600e3;
  const X = (t) => m.l + ((t - t0) / (t1 - t0)) * (w - m.l - m.r);
  const Y = (k) => m.t + (1 - k / 9) * (h - m.t - m.b);
  ctx.strokeStyle = "rgba(141,163,194,.2)";
  ctx.fillStyle = "#8da3c2";
  ctx.textAlign = "right";
  for (const k of [0, 3, 5, 7, 9]) {
    ctx.beginPath(); ctx.moveTo(m.l, Y(k)); ctx.lineTo(w - m.r, Y(k)); ctx.stroke();
    ctx.fillText(String(k), m.l - 4, Y(k) + 4);
  }
  ctx.setLineDash([4, 4]); ctx.strokeStyle = "rgba(42,111,214,.6)";
  ctx.beginPath(); ctx.moveTo(m.l, Y(5)); ctx.lineTo(w - m.r, Y(5)); ctx.stroke(); ctx.setLineDash([]);
  const bw = Math.max(2, ((w - m.l - m.r) * 3 * 3600e3) / (t1 - t0) - 1);
  for (const p of pts) {
    const c = kpClass(p.kp).color;
    const x = X(p.t), y = Y(p.kp);
    if (p.kind === "predicted") {
      ctx.strokeStyle = c; ctx.lineWidth = 1.5; ctx.strokeRect(x + 0.5, y, bw, h - m.b - y);
      ctx.lineWidth = 1;
    } else {
      ctx.fillStyle = c; ctx.fillRect(x + 0.5, y, bw, h - m.b - y);
    }
  }
  const nx = X(nowMs);
  ctx.strokeStyle = "#eaf1fb"; ctx.beginPath(); ctx.moveTo(nx, m.t); ctx.lineTo(nx, h - m.b); ctx.stroke();
  ctx.fillStyle = "#eaf1fb"; ctx.textAlign = "center";
  ctx.fillText("now", nx, h - m.b + 12);
  ctx.fillStyle = "#8da3c2";
  ctx.textAlign = "left"; ctx.fillText("observed (filled)", m.l + 4, h - 6);
  ctx.textAlign = "right"; ctx.fillText("forecast (outline)", w - m.r, h - 6);
}
