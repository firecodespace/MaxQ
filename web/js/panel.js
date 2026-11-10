/* The mission control panel beside the 3D viewport.
 *
 * Everything readable lives here, and nothing lives on top of the viewport except a six-line corner
 * HUD and a scale bar. That split is deliberate: a view with numbers written across it stops being a
 * view, and the request was explicitly to keep the main screen clean.
 *
 * Layout rules, applied consistently so the panel reads as one instrument rather than a pile of
 * widgets: values are tabular-figure monospace so digits do not shift as they change; a tile is
 * value-then-unit-then-label, never a sentence; the four contract events share one rail so the whole
 * flight is one glance; and the three graphs are the same three quantities, in the same three
 * colours, as the Mission tab's replay plots.
 */

import { $, el, fmtUtc } from "./util.js";
import { Plot } from "./plots.js";
import { C, GROUP_COLORS, SERIES } from "./theme.js";
import { vehicleDimensions } from "./sim3d/rocket.js";

const num = (v, d = 0) =>
  v == null || !isFinite(v) ? "--" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });

/** "61.2 t" / "521 t" / "840 kg": one unit, chosen by magnitude, so the tile never overflows. */
function mass(kg) {
  if (kg == null || !isFinite(kg)) return ["--", ""];
  return kg >= 1000 ? [num(kg / 1000, kg >= 100000 ? 0 : 1), "t"] : [num(kg, 0), "kg"];
}

export class MissionPanel {
  /**
   * @param {object} hooks
   *   onSelectVehicle(entry), onLaunch(), onPlayToggle(), onScrub(t), onSpeed(x),
   *   onToggleGroup(name, on), onSelectObject(i)
   */
  constructor(hooks = {}) {
    this.hooks = hooks;
    this.vehicles = [];
    this.vehicle = null;
    this.traj = null;
    this.profile = null;

    this.plots = {
      alt: new Plot($("mc-alt"), { title: "Altitude", unit: "km", color: SERIES.alt, onScrub: (t) => hooks.onScrub?.(t) }),
      speed: new Plot($("mc-speed"), { title: "Speed", unit: "m/s", color: SERIES.speed, onScrub: (t) => hooks.onScrub?.(t) }),
      q: new Plot($("mc-q"), { title: "Dynamic pressure q", unit: "kPa", color: SERIES.q, onScrub: (t) => hooks.onScrub?.(t) }),
    };

    this._buildTiles();
    this._buildCamTools();

    $("veh-profile").addEventListener("change", (e) => this.setProfile(e.target.value));
    $("btn-launch").addEventListener("click", () => hooks.onLaunch?.());
    $("btn-replay").addEventListener("click", () => hooks.onPlayToggle?.());
    $("sim-speed").addEventListener("change", (e) => hooks.onSpeed?.(Number(e.target.value)));
    $("sim-scrub").addEventListener("input", (e) => hooks.onScrub?.(Number(e.target.value)));
  }

  /* ---------------------------------------------------------------- static scaffolding */

  _buildTiles() {
    // Nine readouts, three across. Chosen because each one changes the decision you would make:
    // where it is, how fast, how hard the air is pushing, how much is left, and what it is doing.
    this.tileDefs = [
      { k: "T+", id: "t" },
      { k: "Altitude", id: "alt" },
      { k: "Speed", id: "spd" },
      { k: "Vertical", id: "vs" },
      { k: "Accel", id: "acc" },
      { k: "Dyn. pressure", id: "q" },
      { k: "Mass", id: "mass" },
      { k: "Downrange", id: "dr" },
      { k: "Pitch", id: "pitch" },
    ];
    const box = $("tel-tiles");
    box.replaceChildren();
    this.tiles = {};
    for (const d of this.tileDefs) {
      const v = el("span", { class: "v mono", text: "--" });
      const t = el("div", { class: "tile" }, v, el("span", { class: "k", text: d.k }));
      box.append(t);
      this.tiles[d.id] = { tile: t, value: v };
    }
  }

  _buildCamTools() {
    const box = $("cam-tools");
    box.replaceChildren();
    this.camButtons = {};
    // Earth mode gets one free camera; flight mode gets the three shots a launch is actually watched
    // from. The set swaps with the mode rather than greying out, so there is never a dead control.
    const defs = [
      { id: "orbit", label: "Free", modes: ["earth", "flight"] },
      { id: "chase", label: "Chase", modes: ["flight"] },
      { id: "onboard", label: "Onboard", modes: ["flight"] },
      { id: "pad", label: "Pad", modes: ["flight"] },
    ];
    for (const d of defs) {
      const b = el("button", {
        type: "button",
        "aria-pressed": "false",
        title: `${d.label} camera`,
        text: d.label,
      });
      b.addEventListener("click", () => this.hooks.onCamera?.(d.id));
      b.dataset.modes = d.modes.join(",");
      box.append(b);
      this.camButtons[d.id] = b;
    }
    this.resetBtn = el("button", { type: "button", title: "Reset the view", text: "Reset" });
    this.resetBtn.addEventListener("click", () => this.hooks.onReset?.());
    box.append(this.resetBtn);
  }

  /* ---------------------------------------------------------------- vehicles */

  /** `entries` are the manifest's rocket rows, already carrying the derived vehicle numbers. */
  setVehicles(entries) {
    this.vehicles = entries.filter((e) => e.rocket_id && Array.isArray(e.pitch_program));
    const sel = $("veh-profile");
    sel.replaceChildren();
    const labels = {
      cargo_iss: "Cargo to ISS", crew_iss: "Crew to ISS", meo_gps: "GPS (MEO)", geo_comm: "GEO comms",
    };
    const seen = [];
    for (const v of this.vehicles) if (!seen.includes(v.profile)) seen.push(v.profile);
    for (const p of seen) sel.append(el("option", { value: p, text: labels[p] || p }));
    if (seen.length) this.setProfile(seen[0]);
    else $("veh-note").textContent = "No vehicles were exported. Run scripts/export_web_data.py.";
  }

  setProfile(profile) {
    this.profile = profile;
    $("veh-profile").value = profile;
    const list = $("veh-list");
    list.replaceChildren();
    const mine = this.vehicles.filter((v) => v.profile === profile);
    // Archetypes first: they are the ones with a tuned ascent.
    mine.sort((a, b) => Number(b.archetype) - Number(a.archetype) || a.label.localeCompare(b.label));
    for (const v of mine) {
      const b = el("button", { type: "button", "aria-pressed": "false", title: v.label });
      b.append(
        el("span", { class: "vn", text: v.label.replace(" (fictional archetype)", "") }),
        el("span", { class: "vt", text: v.archetype ? "tuned" : "search" }),
        el("span", {
          class: "vs",
          text: `${num(v.liftoff_kg / 1000, 0)} t liftoff · ${num(v.payload_kg / 1000, 1)} t payload · ${num(v.ideal_dv_mps, 0)} m/s ideal dv`,
        }),
      );
      b.addEventListener("click", () => this.hooks.onSelectVehicle?.(v));
      b.dataset.rocket = v.rocket_id;
      list.append(b);
    }
    if (mine.length) this.hooks.onSelectVehicle?.(mine[0]);
  }

  /** Mark one vehicle selected and show what its geometry works out to. */
  markVehicle(entry, rocketObj) {
    this.vehicle = entry;
    for (const b of $("veh-list").children) {
      b.setAttribute("aria-pressed", String(b.dataset.rocket === entry.rocket_id));
    }
    let geo = "";
    if (rocketObj) {
      const d = vehicleDimensions(rocketObj);
      const s1 = d.stages[0];
      geo =
        `Derived geometry: ${num(d.totalLength, 1)} m tall, ${num(d.maxDiameter, 2)} m across, ` +
        `${s1.nozzle.count} first-stage engine${s1.nozzle.count === 1 ? "" : "s"} ` +
        `(${num(s1.nozzle.exitDiameter, 2)} m bell), liftoff thrust-to-weight ${num(d.liftoffTWR, 2)}. ` +
        `Stage 1 burns ${s1.propellant}, stage 2 ${d.stages[1] ? d.stages[1].propellant : "n/a"}. ` +
        `Every dimension comes from this rocket's own stage masses, thrust and reference area.`;
    }
    const pitch = entry.pitch_tuned
      ? "Pitch program tuned for this vehicle by the CMA-ES optimiser (WP-06)."
      : `Pitch program borrowed from ${entry.pitch_from}: this design came out of the NSGA-II search (WP-07) and its ascent was never tuned, so the flight is indicative, not optimal.`;
    $("veh-note").textContent = `${geo} ${pitch}`;
  }

  /* ---------------------------------------------------------------- trajectory */

  /** A flown trajectory: fills the plots, the timeline and the scrubber. */
  setTrajectory(traj, meta = {}) {
    this.traj = traj;
    const badges = $("tel-badges");
    badges.replaceChildren();
    if (!traj) {
      Object.values(this.plots).forEach((p) => p.clear());
      $("tel-timeline").replaceChildren();
      $("tel-note").textContent = "";
      return;
    }
    badges.append(
      el("span", { class: "badge sim", text: meta.live ? "SIM · flown in your browser" : "SIM · reduced fidelity" }),
    );

    const tmax = traj.t_s[traj.t_s.length - 1];
    const scrub = $("sim-scrub");
    scrub.max = String(tmax);
    scrub.value = "0";

    this.plots.alt.set(traj.t_s, traj.alt_m.map((v) => v / 1000), traj.events);
    this.plots.speed.set(traj.t_s, traj.speed_mps, traj.events);
    this.plots.q.set(traj.t_s, traj.q_pa.map((v) => v / 1000), traj.events);

    this._buildTimeline(traj, tmax);

    const fo = traj.final_orbit;
    const ins = (traj.events || []).find((e) => e.name === "insertion");
    $("tel-note").textContent =
      `Insertion at T+${ins ? ins.t_s : "?"} s into ${fo.perigee_km} x ${fo.apogee_km} km, i = ${fo.incl_deg}°, e = ${fo.ecc}. ` +
      (meta.live
        ? `Flown here by the browser port of ascent/sim.py; it reproduces the Python simulator sample for sample (tests/ascent/test_js_parity.py).`
        : `Loaded from ${meta.name || "a trajectory file"}.`) +
      ` Launch ${traj.t0_unix ? fmtUtc(traj.t0_unix * 1000) : ""}.`;
  }

  _buildTimeline(traj, tmax) {
    const box = $("tel-timeline");
    box.replaceChildren();
    box.append(el("div", { class: "rail" }));
    this.tlDone = el("div", { class: "done", style: "width:0%" });
    box.append(this.tlDone);
    this.tlEvents = [];
    for (const ev of traj.events || []) {
      const pct = (ev.t_s / tmax) * 100;
      const node = el(
        "div",
        { class: "ev", style: `left:${pct}%` },
        el("i"),
        el("span", { text: ev.name.replace("_", " ") }),
      );
      box.append(node);
      this.tlEvents.push({ node, t: ev.t_s });
    }
    this.tlNow = el("div", { class: "now", style: "left:0%" });
    box.append(this.tlNow);
    this.tlMax = tmax;
  }

  /* ---------------------------------------------------------------- per-frame */

  /**
   * Update the live readouts. `tel` is what Sim3D.frame() returned.
   *
   * Throttled to about 15 Hz and diffed before writing. This used to run every frame: nine tiles
   * rebuilt with replaceChildren, a HUD rebuilt, three canvases redrawn and a scale bar restyled,
   * sixty times a second. That is a full style and layout pass per frame for numbers a person
   * cannot read that fast, and it was costing more than the 3D scene it sat beside. Numbers still
   * change smoothly enough to read as live; the renderer keeps the whole frame budget.
   */
  tick(tel, replayT, force) {
    const now = performance.now();
    if (!force && this._lastTick && now - this._lastTick < 66) return;
    this._lastTick = now;

    const st = tel && tel.vehicle;
    const cache = this._vals || (this._vals = {});
    const set = (id, value, unit) => {
      const t = this.tiles[id];
      if (!t) return;
      const key = value + "\u0000" + (unit || "");
      if (cache[id] === key) return; // nothing changed: do not touch the DOM
      cache[id] = key;
      t.value.replaceChildren(document.createTextNode(value));
      if (unit) t.value.append(el("small", { text: unit }));
    };

    if (!st || !this.traj) {
      for (const d of this.tileDefs) set(d.id, "--");
    } else {
      const d = this.traj;
      const tmax = d.t_s[d.t_s.length - 1];
      const T = Math.min(replayT, tmax);
      set("t", `${T.toFixed(0)}`, "s");
      set("alt", num(st.altM / 1000, st.altM < 10000 ? 2 : 1), "km");
      set("spd", num(st.speed, 0), "m/s");

      // Vertical speed and acceleration by central difference on the 1 Hz series, which is what the
      // data actually supports; a longer window would smooth away staging.
      const at = (f, t) => {
        const i = Math.max(0, Math.min(d.t_s.length - 1, Math.round(t)));
        return d[f][i];
      };
      const dt = 2;
      const vs = (at("alt_m", T + dt) - at("alt_m", T - dt)) / (2 * dt);
      const acc = (at("speed_mps", T + dt) - at("speed_mps", T - dt)) / (2 * dt);
      set("vs", num(vs, 0), "m/s");
      set("acc", num(acc / 9.80665, 2), "g");
      set("q", num(st.qPa / 1000, 1), "kPa");
      const [mv, mu] = mass(st.massKg);
      set("mass", mv, mu);

      // Downrange: great-circle distance from the pad along the surface.
      const toRad = Math.PI / 180;
      const dlat = (st.lat - d.lat_deg[0]) * toRad;
      const dlon = (st.lon - d.lon_deg[0]) * toRad;
      const a =
        Math.sin(dlat / 2) ** 2 +
        Math.cos(st.lat * toRad) * Math.cos(d.lat_deg[0] * toRad) * Math.sin(dlon / 2) ** 2;
      const dr = 6378.137 * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
      set("dr", num(dr, dr < 100 ? 1 : 0), "km");
      set("pitch", num(st.pitchDeg, 1), "°");

      // Highlight the tiles that are at their interesting moment.
      const maxQ = (d.events || []).find((e) => e.name === "max_q");
      this.tiles.q.tile.classList.toggle("hot", maxQ ? Math.abs(T - maxQ.t_s) < 6 : false);
      this.tiles.acc.tile.classList.toggle("hot", Math.abs(acc / 9.80665) > 3);

      if (this.tlNow) {
        this.tlNow.style.left = `${(T / this.tlMax) * 100}%`;
        this.tlDone.style.width = `${(T / this.tlMax) * 100}%`;
        for (const e of this.tlEvents) e.node.classList.toggle("past", T >= e.t);
      }
      const scrub = $("sim-scrub");
      if (document.activeElement !== scrub) scrub.value = String(Math.round(T));
      Object.values(this.plots).forEach((p) => p.setCursor(T));
    }

    // The corner HUD: the smallest set that tells you where the camera is and what the vehicle is
    // doing. Deliberately not a second telemetry panel.
    const hud = $("sim-hud");
    const rows = [];
    if (tel) {
      if (tel.mode === "flight" && st) {
        rows.push(["T+", `${replayT.toFixed(0)} s`]);
        rows.push(["alt", `${(st.altM / 1000).toFixed(st.altM < 10000 ? 2 : 1)} km`]);
        rows.push(["v", `${st.speed.toFixed(0)} m/s`]);
        rows.push(["q", `${(st.qPa / 1000).toFixed(1)} kPa`]);
        if (tel.plume) rows.push(["pe/pa", tel.plume.ratio > 99 ? ">99" : tel.plume.ratio.toFixed(1)]);
        // Say so when the pad camera is on a long lens: a view that silently magnifies is a view
        // that lies about how far away the thing is.
        if (tel.padZoom > 1.2) rows.push(["lens", `${tel.padZoom.toFixed(0)}x`]);
      } else {
        rows.push(["cam", `${tel.camAltKm > 999 ? num(tel.camAltKm, 0) : tel.camAltKm.toFixed(1)} km`]);
        if (st) {
          rows.push(["veh", `${(st.altM / 1000).toFixed(0)} km`]);
          rows.push(["T+", `${replayT.toFixed(0)} s`]);
        }
      }
    }
    const ev = this._currentEvent(replayT);
    const perf = tel && tel.fps
      ? `${tel.fps.toFixed(0)} fps · ${tel.drawCalls} draws · ${tel.pixelRatio.toFixed(2)}x`
      : "";
    const key = rows.map((r) => r.join("=")).join("|") + "|" + (ev || "") + "|" + perf;
    if (key !== this._hudKey) {
      this._hudKey = key;
      hud.replaceChildren();
      for (const [k, v] of rows) hud.append(el("b", { text: k }), el("span", { text: v }));
      if (ev) hud.append(el("div", { class: "ev", text: ev.toUpperCase().replace("_", " ") }));
      if (perf) hud.append(el("div", { class: "perf", text: perf }));
    }

    // Scale bar.
    if (tel && tel.scaleBar && tel.scaleBar.label !== this._scaleKey) {
      this._scaleKey = tel.scaleBar.label;
      const bar = $("sim-scale");
      bar.firstElementChild.style.width = `${Math.max(14, Math.min(190, tel.scaleBar.px))}px`;
      bar.lastElementChild.textContent = tel.scaleBar.label;
    }

    // Camera buttons that belong to this mode.
    const modeKey = tel ? `${tel.mode}/${tel.cameraMode}` : "";
    if (tel && modeKey !== this._modeKey) {
      this._modeKey = modeKey;
      for (const [id, b] of Object.entries(this.camButtons)) {
        const shown = b.dataset.modes.split(",").includes(tel.mode);
        b.hidden = !shown;
        b.setAttribute("aria-pressed", String(tel.cameraMode === id));
      }
      $("mode-earth").setAttribute("aria-pressed", String(tel.mode === "earth"));
      $("mode-flight").setAttribute("aria-pressed", String(tel.mode === "flight"));
    }
  }

  _currentEvent(T) {
    if (!this.traj) return null;
    for (const e of this.traj.events || []) if (Math.abs(T - e.t_s) < 2.5) return e.name;
    return null;
  }

  setPlaying(on) {
    $("btn-replay").textContent = on ? "Pause" : "Play";
  }

  status(text, cls = "") {
    const s = $("sim-status");
    s.textContent = text;
    s.className = cls;
  }

  /* ---------------------------------------------------------------- catalog */

  setLayers(groups, counts, onSet) {
    const box = $("sim-layers");
    box.replaceChildren();
    for (const g of groups) {
      if (!counts[g]) continue;
      const b = el("button", { type: "button", "aria-pressed": String(onSet.has(g)), title: `Show or hide ${g}` });
      b.append(
        el("span", { class: "dot", style: `background:${GROUP_COLORS[g] || C.muted}` }),
        el("span", { text: g }),
        el("span", { class: "lcount", text: counts[g].toLocaleString() }),
      );
      b.addEventListener("click", () => {
        const on = b.getAttribute("aria-pressed") !== "true";
        b.setAttribute("aria-pressed", String(on));
        this.hooks.onToggleGroup?.(g, on);
      });
      box.append(b);
    }
  }

  /** The selected object. `closeUp` is what the view reported about drawing it. */
  setSelection(obj, pos, groupName, closeUp, ageDays) {
    const box = $("sim-sel");
    if (!obj) {
      box.textContent = "Click an object in the view.";
      return;
    }
    const dl = el("dl");
    const row = (k, v) => dl.append(el("dt", { text: k }), el("dd", { text: v }));
    row("Name", obj[1] || "(unnamed)");
    row("NORAD", String(obj[0]));
    row("Group", groupName);
    if (pos) {
      row("Position", `${pos.latDeg.toFixed(2)}°, ${pos.lonDeg.toFixed(2)}°`);
      row("Altitude", `${num(pos.altKm, 0)} km`);
    }
    if (ageDays != null) row("TLE age", `${ageDays.toFixed(1)} d`);
    if (closeUp) {
      row(
        "Drawn as",
        closeUp.shown
          ? `${closeUp.label}, at true size`
          : `marker (true size ${closeUp.spanM ? `${closeUp.spanM} m` : "unknown"} is under a pixel from here)`,
      );
    }
    box.replaceChildren(dl);
  }
}
