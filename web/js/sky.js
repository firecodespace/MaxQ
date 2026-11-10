// Sky view (WP-09): orbit map coloured by cluster + congestion by altitude. Data: clusters.json (contract).
import { badge, el } from "./util.js";
import { getJSON } from "./data.js";
import { clusterColor as rankColor } from "./theme.js";

const NOISE_COLOR = "#897a65";
const M = { l: 46, r: 12, t: 12, b: 34 };
const X_MIN = Math.log10(130);
const X_MAX = Math.log10(100000);
const X_TICKS = [200, 500, 1000, 2000, 5000, 10000, 20000, 50000];
const fmtKm = (v) => (v >= 1000 ? `${v / 1000}k` : String(v));
const num = (v, d = 0) => Number(v).toLocaleString(undefined, { maximumFractionDigits: d });

export function pickClustersFile(manifest) {
  const files = ((manifest && manifest.files) || []).filter((f) => f.kind === "clusters");
  files.sort((a, b) => Number(a.dummy) - Number(b.dummy) || a.name.localeCompare(b.name));
  return files[0] || null;
}

function sizeCanvas(c) {
  const r = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(80, r.width), h = Math.max(80, r.height);
  c.width = Math.round(w * dpr);
  c.height = Math.round(h * dpr);
  return { w, h, dpr };
}

export class SkyView {
  /** @param {{manifest:object, nameOf:(norad:number)=>string|undefined}} opts */
  constructor({ manifest, nameOf }) {
    this.manifest = manifest;
    this.nameOf = nameOf || (() => undefined);
    this.loaded = false;
    this.data = null;
    this.visible = new Set();
    this.showLabels = true;
    this.hover = null; // {cluster, i}
    this.metric = "density";
    this.map = document.getElementById("sky-map");
    this.cong = document.getElementById("sky-cong");
    this._bind();
  }

  async ensureLoaded() {
    if (this.loaded) { this.redraw(); return; }
    this.loaded = true;
    const f = pickClustersFile(this.manifest);
    document.getElementById("sky-badges").replaceChildren();
    if (!f) return this._empty("No clusters file found. Run python -m catalog.cluster, then scripts/export_web_data.py.");
    const data = await getJSON(f.name);
    if (!data || !Array.isArray(data.points) || !Array.isArray(data.clusters)) {
      return this._empty(`Could not load a valid clusters file from ${f.name}.`);
    }
    this.data = data;
    document.getElementById("sky-badges").append(f.dummy ? badge("DUMMY DATA", "dummy") : badge("REAL (DBSCAN)", "real"));
    document.getElementById("sky-file").textContent = f.name;
    this._prepare();
    this._legend();
    this._summary();
    this._hotspots();
    this.redraw();
  }

  _empty(msg) {
    document.getElementById("sky-summary").textContent = msg;
    this.data = null;
    this.redraw();
  }

  _prepare() {
    const d = this.data;
    const byLabel = new Map();
    for (const c of d.clusters) byLabel.set(c.label, { c, xs: [], ys: [], ids: [] });
    for (const p of d.points) {
      const b = byLabel.get(p.cluster);
      if (b) { b.xs.push(p.x); b.ys.push(p.y); b.ids.push(p.norad_id); }
    }
    this.buckets = byLabel;
    const ranked = d.clusters.filter((c) => c.label !== -1).sort((a, b) => b.count - a.count);
    this.color = new Map(ranked.map((c, i) => [c.label, rankColor(i)]));
    this.color.set(-1, NOISE_COLOR);
    this.visible = new Set(d.clusters.map((c) => c.label));
    this.axes = d.embedding || { x: "x", y: "y" };
  }

  _legend() {
    const box = document.getElementById("sky-legend");
    box.replaceChildren();
    const items = [...this.data.clusters].sort((a, b) => (a.label === -1) - (b.label === -1) || b.count - a.count);
    for (const c of items) {
      const b = el("button", { type: "button", "aria-pressed": "true", title: "Click to show or hide; double-click to isolate" });
      b.append(
        el("span", { class: "dot", style: `background:${this.color.get(c.label)}` }),
        el("span", { class: "lname", text: c.name }),
        el("span", { class: "lcount", text: num(c.count) }),
      );
      b.addEventListener("click", () => {
        const on = !this.visible.has(c.label);
        on ? this.visible.add(c.label) : this.visible.delete(c.label);
        b.setAttribute("aria-pressed", String(on));
        this.redraw();
      });
      b.addEventListener("dblclick", () => {
        this.visible = new Set([c.label]);
        for (const x of box.children) x.setAttribute("aria-pressed", String(x === b));
        this.redraw();
      });
      b.addEventListener("pointerenter", () => { this.focus = c.label; this.redraw(); });
      b.addEventListener("pointerleave", () => { this.focus = null; this.redraw(); });
      b.dataset.label = String(c.label);
      box.append(b);
    }
  }

  _summary() {
    const m = this.data.metrics;
    const box = document.getElementById("sky-summary");
    if (!m) { box.textContent = `${this.data.clusters.length} clusters.`; return; }
    const kg = m.known_groups || {};
    const bits = [];
    for (const [g, v] of Object.entries(kg)) {
      if (["geo", "gps-ops", "starlink", "debris"].includes(g)) {
        bits.push(`${g}: ${(v.share_in_best_cluster * 100).toFixed(0)}% in "${v.best_cluster_name}"`);
      }
    }
    box.textContent =
      `${num(m.objects)} objects → ${m.clusters} clusters, ${(m.noise_fraction * 100).toFixed(1)}% unclustered. ` +
      `Unsupervised DBSCAN on inclination, mean motion and eccentricity at physical scales (2°, 2%, 0.005). ` +
      (bits.length ? `Agreement with the catalog's own groups: ${bits.join("; ")}. ` : "") +
      "Cluster names come from the data. Mean-element orbits: kilometre-level accuracy at best.";
  }

  _hotspots() {
    const box = document.getElementById("sky-hotspots");
    box.replaceChildren();
    const cg = this.data.congestion;
    if (!cg) { box.textContent = "No congestion profile in this file."; return; }
    const d = cg.density_per_million_km3, e = cg.altitude_edges_km, n = cg.counts;
    const peak = Math.max(...d);
    const runs = [];
    for (let i = 0; i < d.length; ) {
      if (d[i] >= 0.12 * peak) {
        let j = i;
        while (j + 1 < d.length && d[j + 1] >= 0.12 * peak) j++;
        let mx = i;
        for (let k = i; k <= j; k++) if (d[k] > d[mx]) mx = k;
        runs.push({ lo: e[i], hi: e[j + 1], peakAlt: (e[mx] + e[mx + 1]) / 2, peak: d[mx], count: n.slice(i, j + 1).reduce((a, b) => a + b, 0) });
        i = j + 1;
      } else i++;
    }
    runs.sort((a, b) => b.peak - a.peak);
    const ul = el("ul");
    for (const r of runs.slice(0, 5)) {
      ul.append(el("li", { text: `${num(r.lo)}–${num(r.hi)} km: ${num(r.count)} objects, peak density ${num(r.peak, 2)} per million km³ near ${num(r.peakAlt)} km` }));
    }
    box.append(el("strong", { text: "Most congested shells" }), ul);
  }

  _bind() {
    const rd = () => this.redraw();
    new ResizeObserver(rd).observe(this.map);
    new ResizeObserver(rd).observe(this.cong);
    this.map.addEventListener("pointermove", (e) => this._hoverMap(e));
    this.map.addEventListener("pointerleave", () => { this.hover = null; this._tip(""); this.redraw(); });
    this.cong.addEventListener("pointermove", (e) => this._hoverCong(e));
    this.cong.addEventListener("pointerleave", () => { this.congHover = null; document.getElementById("sky-cong-tip").textContent = "Hover a bar for details."; this.redraw(); });
    for (const r of document.querySelectorAll('input[name="sky-metric"]')) {
      r.addEventListener("change", () => { this.metric = r.value; this.redraw(); });
    }
    document.getElementById("sky-labels").addEventListener("change", (e) => { this.showLabels = e.target.checked; this.redraw(); });
    document.getElementById("sky-all").addEventListener("click", () => this._setAll(true));
    document.getElementById("sky-none").addEventListener("click", () => this._setAll(false));
  }

  _setAll(on) {
    if (!this.data) return;
    this.visible = on ? new Set(this.data.clusters.map((c) => c.label)) : new Set();
    for (const b of document.getElementById("sky-legend").children) b.setAttribute("aria-pressed", String(on));
    this.redraw();
  }

  _tip(t) { document.getElementById("sky-tip").textContent = t || "Hover a point for details."; }

  // ---- map geometry
  _px(x, y, w, h) {
    return [M.l + ((x - X_MIN) / (X_MAX - X_MIN)) * (w - M.l - M.r), M.t + (1 - y / 180) * (h - M.t - M.b)];
  }

  _hoverMap(e) {
    if (!this.data) return;
    const r = this.map.getBoundingClientRect();
    const px = e.clientX - r.left, py = e.clientY - r.top;
    let best = null, bd = 100;
    for (const [lab, b] of this.buckets) {
      if (!this.visible.has(lab)) continue;
      for (let i = 0; i < b.xs.length; i++) {
        const [x, y] = this._px(b.xs[i], b.ys[i], r.width, r.height);
        const d = (x - px) ** 2 + (y - py) ** 2;
        if (d < bd) { bd = d; best = { lab, i }; }
      }
    }
    this.hover = best;
    if (best) {
      const b = this.buckets.get(best.lab);
      const id = b.ids[best.i];
      const nm = this.nameOf(id);
      this._tip(`${nm ? nm + " · " : ""}NORAD ${id} · ${num(Math.pow(10, b.xs[best.i]))} km · ${b.ys[best.i].toFixed(1)}° · ${b.c.name}`);
    } else this._tip("");
    this.redraw();
  }

  redraw() {
    this._drawMap();
    this._drawCong();
  }

  _drawMap() {
    const c = this.map;
    const { w, h, dpr } = sizeCanvas(c);
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#0d0a07";
    ctx.fillRect(0, 0, w, h);
    ctx.font = "11px system-ui, sans-serif";
    ctx.strokeStyle = "rgba(181,162,138,.18)";
    ctx.fillStyle = "#b5a28a";
    ctx.textAlign = "center";
    for (const a of X_TICKS) {
      const [x] = this._px(Math.log10(a), 0, w, h);
      ctx.beginPath(); ctx.moveTo(x, M.t); ctx.lineTo(x, h - M.b); ctx.stroke();
      ctx.fillText(fmtKm(a), x, h - M.b + 14);
    }
    ctx.textAlign = "right";
    for (let a = 0; a <= 180; a += 30) {
      const [, y] = this._px(X_MIN, a, w, h);
      ctx.beginPath(); ctx.moveTo(M.l, y); ctx.lineTo(w - M.r, y); ctx.stroke();
      ctx.fillText(`${a}°`, M.l - 5, y + 4);
    }
    ctx.textAlign = "center";
    ctx.fillText(this.axes && this.axes.x ? this.axes.x.replace(/ \(.*\)/, " (km, log scale)") : "altitude", (w + M.l) / 2, h - 4);
    ctx.save();
    ctx.translate(11, (h - M.b) / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText(this.axes && this.axes.y ? this.axes.y : "inclination", 0, 0);
    ctx.restore();
    // GEO and ISS landmarks
    const [gx] = this._px(Math.log10(35786), 0, w, h);
    ctx.setLineDash([4, 4]); ctx.strokeStyle = "rgba(239,180,85,.5)";
    ctx.beginPath(); ctx.moveTo(gx, M.t); ctx.lineTo(gx, h - M.b); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "rgba(239,180,85,.8)"; ctx.textAlign = "right";
    ctx.fillText("GEO 35,786 km", gx - 4, M.t + 12);
    const [ix, iy] = this._px(Math.log10(420), 51.64, w, h);
    ctx.strokeStyle = "#efb455"; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(ix, iy, 6, 0, 2 * Math.PI); ctx.stroke(); ctx.lineWidth = 1;
    ctx.textAlign = "left"; ctx.fillText("ISS", ix + 9, iy - 7);
    if (!this.data) {
      ctx.fillStyle = "#b5a28a"; ctx.textAlign = "center";
      ctx.fillText("no clusters data", (w + M.l) / 2, h / 2);
      return;
    }
    // points
    for (const [lab, b] of this.buckets) {
      if (!this.visible.has(lab)) continue;
      const dim = this.focus != null && this.focus !== lab;
      ctx.fillStyle = this.color.get(lab);
      ctx.globalAlpha = dim ? 0.12 : lab === -1 ? 0.5 : 0.85;
      for (let i = 0; i < b.xs.length; i++) {
        const [x, y] = this._px(b.xs[i], b.ys[i], w, h);
        ctx.fillRect(x - 1, y - 1, 2, 2);
      }
    }
    ctx.globalAlpha = 1;
    // cluster labels
    if (this.showLabels) {
      ctx.font = "10px system-ui, sans-serif"; ctx.textAlign = "left";
      for (const c2 of this.data.clusters) {
        if (c2.label === -1 || c2.count < 150 || !this.visible.has(c2.label)) continue;
        const [x, y] = this._px(Math.log10(Math.max(c2.alt_km_median || 100, 100)), c2.incl_deg_median ?? 0, w, h);
        const t = c2.name.split(" · ")[0];
        ctx.fillStyle = "rgba(20,17,13,.8)";
        ctx.fillRect(x + 5, y - 15, ctx.measureText(t).width + 6, 13);
        ctx.fillStyle = this.color.get(c2.label);
        ctx.fillText(t, x + 8, y - 5);
      }
    }
    if (this.hover) {
      const b = this.buckets.get(this.hover.lab);
      const [x, y] = this._px(b.xs[this.hover.i], b.ys[this.hover.i], w, h);
      ctx.strokeStyle = "#f4ecdf"; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 6, 0, 2 * Math.PI); ctx.stroke(); ctx.lineWidth = 1;
    }
  }

  // ---- congestion chart
  _congGeom(w, h) {
    const cg = this.data.congestion;
    const vals = this.metric === "density" ? cg.density_per_million_km3 : cg.counts;
    const ymax = Math.max(...vals) * 1.08 || 1;
    const e = cg.altitude_edges_km;
    const X = (a) => M.l + ((a - e[0]) / (e[e.length - 1] - e[0])) * (w - M.l - M.r);
    const Y = (v) => M.t + (1 - v / ymax) * (h - M.t - M.b);
    return { cg, vals, ymax, e, X, Y };
  }

  _hoverCong(e) {
    if (!this.data || !this.data.congestion) return;
    const r = this.cong.getBoundingClientRect();
    const { cg, e: edges } = this._congGeom(r.width, r.height);
    const f = (e.clientX - r.left - M.l) / (r.width - M.l - M.r);
    const i = Math.floor(f * (edges.length - 1));
    if (i < 0 || i >= cg.counts.length) { this.congHover = null; return; }
    this.congHover = i;
    document.getElementById("sky-cong-tip").textContent =
      `${num(edges[i])}–${num(edges[i + 1])} km · ${num(cg.counts[i])} objects · density ${num(cg.density_per_million_km3[i], 3)} per million km³`;
    this.redraw();
  }

  _drawCong() {
    const c = this.cong;
    const { w, h, dpr } = sizeCanvas(c);
    const ctx = c.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#1f1a15";
    ctx.fillRect(0, 0, w, h);
    ctx.font = "11px system-ui, sans-serif";
    if (!this.data || !this.data.congestion) {
      ctx.fillStyle = "#b5a28a"; ctx.textAlign = "center";
      ctx.fillText("no congestion profile in this file", w / 2, h / 2);
      return;
    }
    const { cg, vals, ymax, e, X, Y } = this._congGeom(w, h);
    ctx.strokeStyle = "rgba(181,162,138,.2)"; ctx.fillStyle = "#b5a28a"; ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const v = (ymax * i) / 4, y = Y(v);
      ctx.beginPath(); ctx.moveTo(M.l, y); ctx.lineTo(w - M.r, y); ctx.stroke();
      ctx.fillText(Number(v.toPrecision(2)).toString(), M.l - 5, y + 4);
    }
    ctx.textAlign = "center";
    for (let a = 250; a <= 2000; a += 250) ctx.fillText(String(a), X(a), h - M.b + 14);
    ctx.fillText("altitude (km)", (w + M.l) / 2, h - 4);
    for (let i = 0; i < vals.length; i++) {
      const x0 = X(e[i]), x1 = X(e[i + 1]);
      ctx.fillStyle = this.congHover === i ? "#efb455" : "#8fbdce";
      ctx.fillRect(x0 + 0.5, Y(vals[i]), Math.max(1, x1 - x0 - 1), h - M.b - Y(vals[i]));
    }
    ctx.save();
    ctx.translate(11, (h - M.b) / 2); ctx.rotate(-Math.PI / 2); ctx.fillStyle = "#b5a28a"; ctx.textAlign = "center";
    ctx.fillText(this.metric === "density" ? "objects per million km³" : "objects per 25 km shell", 0, 0);
    ctx.restore();
  }
}
