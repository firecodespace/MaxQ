// Small canvas line plots with event markers and a scrub cursor.
import { niceMax } from "./util.js";

const M = { l: 46, r: 8, t: 20, b: 20 };

export class Plot {
  constructor(canvas, { title, unit, color, onScrub }) {
    this.c = canvas;
    this.ctx = canvas.getContext("2d");
    this.opt = { title, unit, color };
    this.data = null;
    this.cursor = 0;
    this.resize();
    new ResizeObserver(() => { this.resize(); this.draw(); }).observe(canvas);
    const scrub = (e) => {
      if (!this.data) return;
      const r = canvas.getBoundingClientRect();
      const f = (e.clientX - r.left - M.l) / (r.width - M.l - M.r);
      onScrub(Math.min(1, Math.max(0, f)) * this.data.xmax);
    };
    let down = false;
    canvas.addEventListener("pointerdown", (e) => { down = true; canvas.setPointerCapture(e.pointerId); scrub(e); });
    canvas.addEventListener("pointermove", (e) => { if (down) scrub(e); });
    canvas.addEventListener("pointerup", () => { down = false; });
    canvas.addEventListener("pointercancel", () => { down = false; });
  }

  resize() {
    const r = this.c.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    this.w = Math.max(80, r.width);
    this.h = Math.max(60, r.height);
    this.c.width = Math.round(this.w * this.dpr);
    this.c.height = Math.round(this.h * this.dpr);
  }

  set(xs, ys, events) {
    const xmax = xs.length ? xs[xs.length - 1] : 1;
    let ymax = 0;
    for (const v of ys) if (v > ymax) ymax = v;
    this.data = { xs, ys, events: events || [], xmax: xmax || 1, ymax: niceMax(ymax) };
    this.draw();
  }

  clear() { this.data = null; this.draw(); }

  setCursor(t) { this.cursor = t; this.draw(); }

  draw() {
    const { ctx, dpr, w, h } = this;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillStyle = "#f4ecdf";
    ctx.fillText(`${this.opt.title} (${this.opt.unit})`, M.l, 13);
    if (!this.data) {
      ctx.fillStyle = "#b5a28a";
      ctx.fillText("no data", M.l, h / 2);
      return;
    }
    const { xs, ys, events, xmax, ymax } = this.data;
    const pw = w - M.l - M.r, ph = h - M.t - M.b;
    const X = (x) => M.l + (x / xmax) * pw;
    const Y = (y) => M.t + ph - (y / ymax) * ph;
    ctx.strokeStyle = "rgba(181,162,138,.2)";
    ctx.fillStyle = "#b5a28a";
    ctx.lineWidth = 1;
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i++) {
      const y = Y((ymax * i) / 4);
      ctx.beginPath(); ctx.moveTo(M.l, y); ctx.lineTo(w - M.r, y); ctx.stroke();
      ctx.fillText(Number((ymax * i / 4).toPrecision(3)).toString(), M.l - 4, y + 4);
    }
    ctx.textAlign = "center";
    for (let i = 0; i <= 4; i++) ctx.fillText(Math.round((xmax * i) / 4) + "s", X((xmax * i) / 4), h - 5);
    ctx.textAlign = "left";
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = "rgba(239,180,85,.55)";
    for (const ev of events) {
      const x = X(ev.t_s);
      ctx.beginPath(); ctx.moveTo(x, M.t); ctx.lineTo(x, M.t + ph); ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.strokeStyle = this.opt.color;
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    for (let i = 0; i < xs.length; i++) {
      const x = X(xs[i]), y = Y(ys[i]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
    // cursor + dot
    const cx = X(Math.min(this.cursor, xmax));
    ctx.strokeStyle = "#f4ecdf";
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx, M.t); ctx.lineTo(cx, M.t + ph); ctx.stroke();
    let lo = 0;
    while (lo < xs.length - 2 && xs[lo + 1] <= this.cursor) lo++;
    const f = xs[lo + 1] > xs[lo] ? (this.cursor - xs[lo]) / (xs[lo + 1] - xs[lo]) : 0;
    const v = ys[lo] + Math.min(1, Math.max(0, f)) * ((ys[lo + 1] ?? ys[lo]) - ys[lo]);
    ctx.fillStyle = "#f4ecdf";
    ctx.beginPath(); ctx.arc(cx, Y(v), 3.5, 0, 2 * Math.PI); ctx.fill();
  }
}
