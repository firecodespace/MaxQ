// 2D equirectangular map on a canvas: land, graticule, day/night, satellites, ground tracks, launch site, ascent path.
import { clamp, interp, interpLon } from "./util.js";

// Group colours live in theme.js (shared with the Sky scatter and the 3D view); re-exported here so
// the long-standing `import { GROUP_COLORS } from "./map.js"` call sites keep working unchanged.
import { GROUP_COLORS } from "./theme.js";
export { GROUP_COLORS };

export class MapView {
  constructor(canvas, hooks = {}) {
    this.c = canvas;
    this.ctx = canvas.getContext("2d");
    this.hooks = hooks; // {onPick(clientX, clientY, x, y)}
    this.view = { lon: 0, lat: 0, zoom: 1 };
    this.land = null;
    this.dpr = 1;
    this.w = 300;
    this.h = 150;
    this._bind();
    this.resize();
    new ResizeObserver(() => this.resize()).observe(canvas);
  }

  resize() {
    const r = this.c.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    this.w = Math.max(50, r.width);
    this.h = Math.max(50, r.height);
    this.c.width = Math.round(this.w * this.dpr);
    this.c.height = Math.round(this.h * this.dpr);
  }

  get scale() {
    return Math.min(this.w / 360, this.h / 180) * this.view.zoom; // px per degree
  }

  /** Screen position of a lon/lat, wrapped to the copy nearest the view centre. */
  project(lon, lat) {
    const s = this.scale;
    let d = lon - this.view.lon;
    d = ((((d + 180) % 360) + 360) % 360) - 180;
    return [this.w / 2 + d * s, this.h / 2 - (lat - this.view.lat) * s];
  }

  setView(lon, lat, zoom) {
    this.view.zoom = clamp(zoom, 1, 60);
    if (this.view.zoom === 1) { this.view.lon = 0; this.view.lat = 0; return; }
    this.view.lon = ((((lon + 180) % 360) + 360) % 360) - 180;
    const maxLat = Math.max(0, 90 - this.h / 2 / this.scale);
    this.view.lat = clamp(lat, -maxLat, maxLat);
  }

  zoomBy(f, ax = this.w / 2, ay = this.h / 2) {
    const s0 = this.scale;
    const lon = this.view.lon + (ax - this.w / 2) / s0;
    const lat = this.view.lat - (ay - this.h / 2) / s0;
    const z = clamp(this.view.zoom * f, 1, 60);
    this.view.zoom = z;
    const s1 = this.scale;
    this.setView(lon - (ax - this.w / 2) / s1, lat + (ay - this.h / 2) / s1, z);
  }

  _bind() {
    const c = this.c;
    let drag = null;
    c.addEventListener("pointerdown", (e) => {
      c.setPointerCapture(e.pointerId);
      drag = { x: e.clientX, y: e.clientY, lon: this.view.lon, lat: this.view.lat, moved: 0 };
    });
    c.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      drag.moved = Math.max(drag.moved, Math.hypot(dx, dy));
      if (this.view.zoom > 1) {
        const s = this.scale;
        this.setView(drag.lon - dx / s, drag.lat + dy / s, this.view.zoom);
      }
    });
    c.addEventListener("pointerup", (e) => {
      if (drag && drag.moved < 4 && this.hooks.onPick) {
        const r = c.getBoundingClientRect();
        this.hooks.onPick(e.clientX - r.left, e.clientY - r.top);
      }
      drag = null;
    });
    c.addEventListener("pointercancel", () => { drag = null; });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      this.zoomBy(e.deltaY < 0 ? 1.25 : 0.8, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    c.addEventListener("dblclick", () => this.setView(0, 0, 1));
  }

  // ------------------------------------------------------------------ drawing
  draw(s) {
    const { ctx, dpr, w, h } = this;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#02040a";
    ctx.fillRect(0, 0, w, h);
    this._graticule();
    this._land();
    this._night(s.simMs);
    if (s.site) this._site(s.site);
    if (s.issTrack) this._track(s.issTrack);
    if (s.traj) this._trajectory(s.traj, s.replayT, s.eventsOn);
    if (s.pos) this._points(s);
    if (s.pos && s.highlight != null) this._highlight(s.pos, s.highlight, s.highlightLabel, "#4d9fff");
    if (s.pos && s.selected != null) this._highlight(s.pos, s.selected, s.selectedLabel, "#eaf1fb");
  }

  _graticule() {
    const { ctx } = this;
    ctx.strokeStyle = "rgba(141,163,194,.14)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    const step = this.view.zoom > 6 ? 5 : this.view.zoom > 2.5 ? 15 : 30;
    for (let lon = -180; lon <= 180; lon += step) {
      const [x] = this.project(lon, 0);
      ctx.moveTo(x, 0); ctx.lineTo(x, this.h);
    }
    for (let lat = -90; lat <= 90; lat += step) {
      const [, y] = this.project(0, lat);
      ctx.moveTo(0, y); ctx.lineTo(this.w, y);
    }
    ctx.stroke();
  }

  _land() {
    if (!this.land) return;
    const { ctx } = this;
    const s = this.scale;
    ctx.fillStyle = "#111926";
    ctx.strokeStyle = "#304563";
    ctx.lineWidth = 1;
    const offsets = this.view.zoom > 1 ? [-360, 0, 360] : [0];
    for (const off of offsets) {
      ctx.beginPath();
      for (const ring of this.land) {
        for (let i = 0; i < ring.length; i++) {
          const x = this.w / 2 + (ring[i][0] + off - this.view.lon) * s;
          const y = this.h / 2 - (ring[i][1] - this.view.lat) * s;
          if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.closePath();
      }
      ctx.fill();
      ctx.stroke();
    }
  }

  /** Approximate night side (subsolar point from the date; equation of time ignored, +-4 deg). */
  _night(simMs) {
    const d = new Date(simMs);
    const doy = (Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) - Date.UTC(d.getUTCFullYear(), 0, 0)) / 86400000;
    const decl = 23.44 * Math.sin((2 * Math.PI * (doy - 81)) / 365) * (Math.PI / 180);
    const hours = d.getUTCHours() + d.getUTCMinutes() / 60 + d.getUTCSeconds() / 3600;
    const subLon = -(hours - 12) * 15;
    if (Math.abs(decl) < 1e-3) return;
    const { ctx } = this;
    const pts = [];
    for (let lon = -180; lon <= 180; lon += 3) {
      const H = ((lon - subLon) * Math.PI) / 180;
      pts.push([lon, (Math.atan(-Math.cos(H) / Math.tan(decl)) * 180) / Math.PI]);
    }
    const pole = decl > 0 ? -90 : 90;
    const s = this.scale;
    ctx.fillStyle = "rgba(2,4,10,.5)";
    for (const off of this.view.zoom > 1 ? [-360, 0, 360] : [0]) {
      ctx.beginPath();
      pts.forEach(([lon, lat], i) => {
        const x = this.w / 2 + (lon + off - this.view.lon) * s;
        const y = this.h / 2 - (lat - this.view.lat) * s;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.lineTo(this.w / 2 + (180 + off - this.view.lon) * s, this.h / 2 - (pole - this.view.lat) * s);
      ctx.lineTo(this.w / 2 + (-180 + off - this.view.lon) * s, this.h / 2 - (pole - this.view.lat) * s);
      ctx.closePath();
      ctx.fill();
    }
  }

  _site(site) {
    const { ctx } = this;
    const [x, y] = this.project(site.lon_deg, site.lat_deg);
    ctx.strokeStyle = "#7cc6ff";
    ctx.fillStyle = "#7cc6ff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, y - 8); ctx.lineTo(x + 6, y + 5); ctx.lineTo(x - 6, y + 5); ctx.closePath();
    ctx.stroke();
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText(site.name, x + 9, y + 4);
  }

  _polyline(flat, color, dash) {
    const { ctx } = this;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.setLineDash(dash || []);
    ctx.beginPath();
    let px = null;
    for (let i = 0; i < flat.length; i += 2) {
      const [x, y] = this.project(flat[i], flat[i + 1]);
      if (px === null || Math.abs(x - px) > this.w / 2) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      px = x;
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  _track(track) {
    this._polyline(track, "rgba(77,159,255,.65)", [4, 4]);
  }

  _trajectory(traj, replayT, eventsOn) {
    const { ctx } = this;
    const flat = new Float32Array(traj.t_s.length * 2);
    for (let i = 0; i < traj.t_s.length; i++) { flat[2 * i] = traj.lon_deg[i]; flat[2 * i + 1] = traj.lat_deg[i]; }
    this._polyline(flat, "#ff5f6d");
    if (eventsOn) {
      ctx.font = "10px system-ui, sans-serif";
      for (const ev of traj.events || []) {
        const [x, y] = this.project(interpLon(traj.t_s, traj.lon_deg, ev.t_s), interp(traj.t_s, traj.lat_deg, ev.t_s));
        ctx.fillStyle = "#4d9fff";
        ctx.fillRect(x - 3, y - 3, 6, 6);
        if (this.view.zoom > 1.5) ctx.fillText(ev.name, x + 6, y - 4);
      }
    }
    const [x, y] = this.project(interpLon(traj.t_s, traj.lon_deg, replayT), interp(traj.t_s, traj.lat_deg, replayT));
    ctx.fillStyle = "#eaf1fb";
    ctx.strokeStyle = "#ff5f6d";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
  }

  _points({ pos, groupIdx, groupsOn, groups }) {
    const { ctx } = this;
    const n = pos.lat.length;
    const s = this.scale;
    const size = this.view.zoom > 4 ? 3 : this.w < 500 ? 1.3 : 2;
    for (let g = 0; g < groups.length; g++) {
      if (!groupsOn.has(groups[g])) continue;
      ctx.fillStyle = GROUP_COLORS[groups[g]] || "#8da3c2";
      ctx.globalAlpha = groups[g] === "debris" ? 0.6 : 0.9;
      for (let i = 0; i < n; i++) {
        if (groupIdx[i] !== g) continue;
        const lat = pos.lat[i];
        if (lat !== lat) continue; // NaN
        let d = pos.lon[i] - this.view.lon;
        d = ((((d + 180) % 360) + 360) % 360) - 180;
        const x = this.w / 2 + d * s;
        const y = this.h / 2 - (lat - this.view.lat) * s;
        if (x < -2 || x > this.w + 2 || y < -2 || y > this.h + 2) continue;
        ctx.fillRect(x - size / 2, y - size / 2, size, size);
      }
    }
    ctx.globalAlpha = 1;
  }

  _highlight(pos, i, label, color) {
    if (!(pos.lat[i] === pos.lat[i])) return;
    const { ctx } = this;
    const [x, y] = this.project(pos.lon[i], pos.lat[i]);
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 7, 0, 2 * Math.PI); ctx.stroke();
    if (label) {
      ctx.font = "12px system-ui, sans-serif";
      ctx.fillText(label, x + 10, y - 8);
    }
  }

  /** Nearest visible object within `radius` css px of (px, py); returns index or -1. */
  pick(px, py, pos, groupIdx, groupsOn, groups, radius = 12) {
    if (!pos) return -1;
    let best = -1, bd = radius * radius;
    for (let i = 0; i < pos.lat.length; i++) {
      if (!groupsOn.has(groups[groupIdx[i]]) || pos.lat[i] !== pos.lat[i]) continue;
      const [x, y] = this.project(pos.lon[i], pos.lat[i]);
      const d = (x - px) ** 2 + (y - py) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    return best;
  }
}
