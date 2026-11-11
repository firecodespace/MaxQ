export const $ = (id) => document.getElementById(id);
export const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x));

export function fmtUtc(ms) {
  return new Date(ms).toISOString().replace("T", " ").slice(0, 19) + "Z";
}

/** "T-3h12m" / "T+0h05m" relative to a reference time. */
export function fmtRel(targetMs, nowMs) {
  const d = Math.round((targetMs - nowMs) / 60000);
  const sign = d >= 0 ? "-" : "+";
  const a = Math.abs(d);
  const days = Math.floor(a / 1440), h = Math.floor((a % 1440) / 60), m = a % 60;
  return `T${sign}${days ? days + "d" : ""}${h}h${String(m).padStart(2, "0")}m`;
}

/** HTML attributes whose mere PRESENCE is what counts, whatever their value. */
const BOOLEAN_ATTRS = new Set(["disabled", "hidden", "checked", "selected", "readonly", "required", "open"]);

export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (BOOLEAN_ATTRS.has(k)) {
      // setAttribute("disabled", false) yields disabled="false", and the attribute being THERE is
      // what disables the element: a button built with {disabled: false} comes out dead. These have
      // to be set and removed, not assigned.
      if (v) n.setAttribute(k, "");
      else n.removeAttribute(k);
    } else if (v != null) n.setAttribute(k, v);
  }
  for (const c of children) n.append(c);
  return n;
}

export function badge(text, cls) {
  return el("span", { class: `badge ${cls}`, text });
}

/** Binary search: index of the last element <= x (arrays ascending). */
export function floorIndex(arr, x) {
  let lo = 0, hi = arr.length - 1;
  if (x <= arr[0]) return 0;
  if (x >= arr[hi]) return hi;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= x) lo = mid; else hi = mid;
  }
  return lo;
}

/** Linear interpolation of ys at time x over ascending xs. */
export function interp(xs, ys, x) {
  const i = floorIndex(xs, x);
  if (i >= xs.length - 1) return ys[xs.length - 1];
  const f = (x - xs[i]) / (xs[i + 1] - xs[i]);
  return ys[i] + f * (ys[i + 1] - ys[i]);
}

/** Longitude interpolation that does not jump across the antimeridian. */
export function interpLon(xs, lons, x) {
  const i = floorIndex(xs, x);
  if (i >= xs.length - 1) return lons[xs.length - 1];
  const f = (x - xs[i]) / (xs[i + 1] - xs[i]);
  let d = lons[i + 1] - lons[i];
  if (d > 180) d -= 360; else if (d < -180) d += 360;
  let r = lons[i] + f * d;
  if (r > 180) r -= 360; else if (r < -180) r += 360;
  return r;
}

export function niceMax(v) {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
