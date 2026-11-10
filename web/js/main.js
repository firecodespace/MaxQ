import { $, badge, clamp, el, fmtRel, fmtUtc, interp, interpLon } from "./util.js";
import { getJSON, PROFILES, probeManifest, trajectorySources, windowsSource } from "./data.js";
import { GROUP_COLORS, MapView } from "./map.js";
import { Plot } from "./plots.js";
import { SkyView } from "./sky.js";
import { drawKpChart, loadSpaceWeather, renderKp } from "./spaceweather.js";
import { MissionPanel } from "./panel.js";

// ---------------------------------------------------------------------------------------------- state
const clock = { baseSim: Date.now(), baseReal: performance.now(), warp: 1, paused: false, locked: false };
const simNow = () => (clock.paused ? clock.baseSim : clock.baseSim + (performance.now() - clock.baseReal) * clock.warp);

const S = {
  manifest: null,
  catalog: null, // {groups, objects, ...}
  groupIdx: new Uint8Array(0),
  groupsOn: new Set(),
  pos: null,
  issIdx: -1,
  issTrack: null,
  trackCenter: 0,
  selected: null,
  windows: null, // {profile, data, name, dummy}
  selWindow: null,
  traj: null, // {data, name, dummy}
  replay: { t: 0, playing: false, speed: 5, last: 0, touched: false },
};

const map = new MapView($("map"), {
  onPick: (px, py) => {
    const i = map.pick(px, py, S.pos, S.groupIdx, S.groupsOn, S.catalog ? S.catalog.groups : []);
    S.selected = i >= 0 ? i : null;
    updateInfo();
  },
});

function banner(text, cls = "") {
  $("banners").append(el("div", { class: `banner ${cls}`, text }));
}

// ---------------------------------------------------------------------------------------------- clock UI
function setSim(ms, pause) {
  clock.baseSim = ms;
  clock.baseReal = performance.now();
  if (pause !== undefined) clock.paused = pause;
  syncClockUi();
}

function syncClockUi() {
  $("btn-pause").textContent = clock.paused ? "Resume" : "Pause";
  $("sel-warp").value = String(clock.warp);
  const live = !clock.paused && clock.warp === 1 && Math.abs(simNow() - Date.now()) < 3000;
  const chip = $("clock-note");
  chip.textContent = clock.locked ? "locked to replay" : clock.paused ? "paused" : live ? "live" : `${clock.warp}x`;
  chip.className = "chip" + (live ? " on" : "");
}

$("btn-pause").addEventListener("click", () => {
  const t = simNow();
  clock.baseSim = t;
  clock.baseReal = performance.now();
  clock.paused = !clock.paused;
  if (!clock.paused) clock.locked = false;
  syncClockUi();
});
$("sel-warp").addEventListener("change", (e) => {
  const t = simNow();
  clock.baseSim = t;
  clock.baseReal = performance.now();
  clock.warp = Number(e.target.value);
  syncClockUi();
});
$("btn-now").addEventListener("click", () => {
  clock.warp = 1;
  clock.locked = false;
  setSim(Date.now(), false);
});
$("btn-zoom-in").addEventListener("click", () => map.zoomBy(1.5));
$("btn-zoom-out").addEventListener("click", () => map.zoomBy(1 / 1.5));
$("btn-zoom-reset").addEventListener("click", () => map.setView(0, 0, 1));

// ---------------------------------------------------------------------------------------------- worker
let worker = null;
let workerReady = false;
let inflight = false;
let reqId = 0;

function startWorker() {
  try {
    worker = new Worker("js/worker.js");
  } catch (e) {
    banner("Satellite worker could not start: " + e.message, "bad");
    return;
  }
  worker.onerror = (e) => banner("Satellite worker error: " + (e.message || "unknown"), "bad");
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "ready") {
      workerReady = true;
      if (m.bad) banner(`${m.bad} TLEs could not be initialised and are hidden.`, "warn");
    } else if (m.type === "positions") {
      inflight = false;
      S.pos = m;
      // The 3D view needs the positions as ECEF, which it derives itself; one reply feeds both views.
      sim3d?.setPositions(m);
    } else if (m.type === "track") {
      if (m.idx === S.issIdx) S.issTrack = m.pts;
    } else if (m.type === "track3") {
      if (m.idx === SIM.trackIdx) sim3d?.setTrack(m.pts);
    }
  };
  worker.postMessage({ type: "init", objects: S.catalog.objects });
}

function pump() {
  if (workerReady && !inflight) {
    inflight = true;
    worker.postMessage({ type: "positions", id: ++reqId, t: simNow() });
    const t = simNow();
    if (S.issIdx >= 0 && (!S.issTrack || Math.abs(t - S.trackCenter) > 15 * 60e3)) {
      S.trackCenter = t;
      worker.postMessage({ type: "track", id: ++reqId, idx: S.issIdx, t0: t - 50 * 60e3, t1: t + 50 * 60e3, step: 30 });
    }
    // The 3D orbit trail of whatever is selected, recentred when the clock drifts far from it.
    if (SIM.trackIdx != null && (SIM.trackWant || Math.abs(t - SIM.trackCenter) > 15 * 60e3)) {
      SIM.trackWant = false;
      SIM.trackCenter = t;
      worker.postMessage({ type: "track3", id: ++reqId, idx: SIM.trackIdx, t0: t - 55 * 60e3, t1: t + 55 * 60e3, step: 25 });
    }
  }
  setTimeout(pump, 60);
}

// ---------------------------------------------------------------------------------------------- catalog UI
function tleEpochMs(tle1) {
  const yy = Number(tle1.slice(18, 20));
  const year = yy < 57 ? 2000 + yy : 1900 + yy;
  return Date.UTC(year, 0, 1) + (Number(tle1.slice(20, 32)) - 1) * 86400000;
}

/** Groups that start hidden. Debris alone: it is the one layer that reads as noise rather than as
 * structure, and its count is on the toggle so nothing is concealed. */
const QUIET_LAYERS = ["debris"];

let groupCounts = {};
function buildChips() {
  const counts = {};
  S.catalog.groups.forEach((g) => (counts[g] = 0));
  S.catalog.objects.forEach((o) => counts[S.catalog.groups[o[2]]]++);
  groupCounts = counts;
  const box = $("chips");
  box.replaceChildren();
  for (const g of S.catalog.groups) {
    if (!counts[g]) continue;
    // Start with the dense layers off. Starlink and debris are two thirds of the catalog and they
    // turn both views into static; their counts are on the toggles, so nothing is hidden, and one
    // click brings them back. A first screen you can read beats a first screen that shows everything.
    if (!QUIET_LAYERS.includes(g)) S.groupsOn.add(g);
    const b = el("button", { type: "button", "aria-pressed": String(S.groupsOn.has(g)), title: `Toggle ${g}` });
    b.append(el("span", { class: "dot", style: `background:${GROUP_COLORS[g] || "#b5a28a"}` }), `${g} ${counts[g]}`);
    b.addEventListener("click", () => {
      const on = !S.groupsOn.has(g);
      on ? S.groupsOn.add(g) : S.groupsOn.delete(g);
      b.setAttribute("aria-pressed", String(on));
      // One set of visible groups across both views, so switching tabs never changes what is shown.
      sim3d?.setGroups(S.groupsOn);
      SIM.panel?.setLayers(S.catalog.groups, groupCounts, S.groupsOn);
    });
    box.append(b);
  }
}

function updateInfo() {
  const box = $("info");
  const i = S.selected;
  if (i == null || !S.catalog || !S.pos) {
    box.textContent = "Click an object on the map.";
    return;
  }
  const o = S.catalog.objects[i];
  const age = (simNow() - tleEpochMs(o[3])) / 86400000;
  const dl = el("dl");
  const row = (k, v) => dl.append(el("dt", { text: k }), el("dd", { text: v }));
  row("Name", o[1] || "(unnamed)");
  row("NORAD", String(o[0]));
  row("Group", S.catalog.groups[o[2]]);
  row("Latitude", S.pos.lat[i].toFixed(2) + "°");
  row("Longitude", S.pos.lon[i].toFixed(2) + "°");
  row("Altitude", S.pos.alt[i].toFixed(0) + " km");
  row("TLE age", age.toFixed(1) + " days (position error grows with age; km-level at best)");
  box.replaceChildren(dl);
}

// ---------------------------------------------------------------------------------------------- windows panel
const dummyFlags = { windows: false, traj: false };
function updateDummyBanner() {
  const old = document.getElementById("dummy-banner");
  if (old) old.remove();
  const parts = [];
  if (dummyFlags.windows) parts.push("launch windows");
  if (dummyFlags.traj) parts.push("ascent trajectory");
  if (parts.length) {
    const b = el("div", { class: "banner bad", id: "dummy-banner", text: `DUMMY DATA shown: ${parts.join(" and ")}. Not real results.` });
    $("banners").prepend(b);
  }
}

function buildTabs() {
  const tabs = $("tabs");
  tabs.replaceChildren();
  for (const p of PROFILES) {
    const src = windowsSource(S.manifest, p.id);
    const b = el("button", { type: "button", role: "tab", "aria-selected": "false", text: p.label });
    if (!src) {
      b.disabled = true;
      b.title = "No windows file for this profile";
    }
    b.addEventListener("click", () => loadWindows(p.id));
    b.dataset.profile = p.id;
    tabs.append(b);
  }
}

async function loadWindows(profile) {
  for (const b of $("tabs").children) b.setAttribute("aria-selected", String(b.dataset.profile === profile));
  const src = windowsSource(S.manifest, profile);
  const body = $("win-body");
  body.replaceChildren();
  $("win-badges").replaceChildren();
  S.windows = null;
  S.selWindow = null;
  dummyFlags.windows = false;
  if (!src) {
    $("win-site").textContent = "";
    $("win-note").textContent = "No launch windows available for this profile yet.";
    updateDummyBanner();
    return;
  }
  const data = await getJSON(src.name);
  if (!data || !Array.isArray(data.windows)) {
    $("win-note").textContent = `Could not load ${src.name}.`;
    updateDummyBanner();
    return;
  }
  S.windows = { profile, data, name: src.name, dummy: src.dummy };
  dummyFlags.windows = src.dummy;
  const badges = $("win-badges");
  if (src.dummy) badges.append(badge("DUMMY DATA", "dummy"));
  else if (String(data._note || "").includes("PLACEHOLDER")) badges.append(badge("PLACEHOLDER", "ph"));
  else badges.append(badge("REAL (TLE-based)", "real"));
  $("win-site").textContent = `${data.site.name} (${data.site.lat_deg.toFixed(2)}, ${data.site.lon_deg.toFixed(2)}) → NORAD ${data.target.norad_id}, TLE epoch ${data.target.tle_epoch_utc.slice(0, 16)}Z · ${src.name}`;
  $("win-note").textContent = data._note || "";
  const now = simNow();
  for (const w of data.windows) {
    const t = w.t_launch_unix * 1000;
    const tr = el("tr", { class: w.flyable ? "" : "nofly", tabindex: "0", "data-id": w.id, title: w.flyable ? "Click to jump to launch time" : "Descending pass: not flyable by default (site setting)" });
    const cell = (txt, cls) => el("td", { text: txt, class: cls || "" });
    const rdv = (a, b) => `${a == null ? "–" : a} / ${b == null ? "–" : b}`;
    tr.append(
      cell(w.id),
      cell(fmtUtc(t).slice(5, 16)),
      cell(fmtRel(t, now)),
      cell(w.pass === "ascending" ? "asc" : "desc"),
      cell(w.azimuth_deg.toFixed(1)),
      cell(w.phase_angle_deg == null ? "–" : w.phase_angle_deg.toFixed(0)),
      cell(rdv(w.rendezvous_hours_fast, w.rendezvous_hours_slow)),
      cell(w.flyable ? "✓" : "✗", w.flyable ? "ok" : "no"),
    );
    const go = () => selectWindow(w);
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    body.append(tr);
  }
  if (!data.windows.length) body.append(el("tr", {}, el("td", { colspan: "8", text: "No windows in this search span." })));
  updateDummyBanner();
}

function selectWindow(w) {
  S.selWindow = w.id;
  for (const tr of $("win-body").children) tr.classList.toggle("sel", tr.dataset.id === w.id);
  clock.locked = false;
  setSim(w.t_launch_unix * 1000, true);
  S.issTrack = null; // re-centre the ISS track on the new time
}

setInterval(() => {
  // keep the T- column fresh (cheap: a dozen rows)
  if (!S.windows) return;
  const now = simNow();
  for (const tr of $("win-body").children) {
    const w = S.windows.data.windows.find((x) => x.id === tr.dataset.id);
    if (w && tr.children[2]) tr.children[2].textContent = fmtRel(w.t_launch_unix * 1000, now);
  }
}, 1000);

// ---------------------------------------------------------------------------------------------- replay
const plots = {
  alt: new Plot($("plot-alt"), { title: "Altitude", unit: "km", color: "#8fbdce", onScrub: (t) => setReplayT(t, true) }),
  speed: new Plot($("plot-speed"), { title: "Speed", unit: "m/s", color: "#a3bb89", onScrub: (t) => setReplayT(t, true) }),
  q: new Plot($("plot-q"), { title: "Dynamic pressure q", unit: "kPa", color: "#efb455", onScrub: (t) => setReplayT(t, true) }),
};

function trajOk(d) {
  const keys = ["t_s", "lat_deg", "lon_deg", "alt_m", "speed_mps", "q_pa", "mass_kg"];
  return d && keys.every((k) => Array.isArray(d[k]) && d[k].length === d.t_s.length && d.t_s.length > 1);
}

async function loadTrajectory(name, dummy) {
  S.traj = null;
  dummyFlags.traj = false;
  $("traj-badges").replaceChildren();
  $("markers").replaceChildren();
  Object.values(plots).forEach((p) => p.clear());
  $("readout").textContent = "";
  const data = name ? await getJSON(name) : null;
  if (!trajOk(data)) {
    $("traj-note").textContent = name ? `Could not load a valid trajectory from ${name}.` : "No trajectory file available yet.";
    updateDummyBanner();
    return;
  }
  S.traj = { data, name, dummy };
  dummyFlags.traj = dummy;
  $("traj-badges").append(dummy ? badge("DUMMY DATA", "dummy") : badge("SIM · reduced fidelity", "sim"));
  const fo = data.final_orbit;
  $("traj-note").textContent = `${data.id} · rocket ${data.rocket_id} · window ${data.window_id} · seed ${data.seed} · final orbit ${fo.perigee_km} x ${fo.apogee_km} km, i=${fo.incl_deg}° · ${name}`;
  const tmax = data.t_s[data.t_s.length - 1];
  const scrub = $("scrub");
  scrub.max = String(tmax);
  scrub.step = "1";
  scrub.value = "0";
  for (const ev of data.events || []) {
    const m = el("div", { class: "marker", style: `left:${(ev.t_s / tmax) * 100}%`, text: ev.name });
    $("markers").append(m);
  }
  plots.alt.set(data.t_s, data.alt_m.map((v) => v / 1000), data.events);
  plots.speed.set(data.t_s, data.speed_mps, data.events);
  plots.q.set(data.t_s, data.q_pa.map((v) => v / 1000), data.events);
  S.replay.t = 0;
  S.replay.touched = false;
  setReplayT(0, false);
  updateDummyBanner();
}

function setReplayT(t, fromUser) {
  const d = S.traj && S.traj.data;
  if (!d) return;
  const tmax = d.t_s[d.t_s.length - 1];
  S.replay.t = clamp(t, 0, tmax);
  if (fromUser) { S.replay.touched = true; S.replay.playing = false; $("btn-play").textContent = "Play"; }
  $("scrub").value = String(S.replay.t);
  Object.values(plots).forEach((p) => p.setCursor(S.replay.t));
  const T = S.replay.t;
  $("readout").textContent =
    `T+${T.toFixed(0).padStart(3, "0")}s  alt ${(interp(d.t_s, d.alt_m, T) / 1000).toFixed(1)} km  ` +
    `v ${interp(d.t_s, d.speed_mps, T).toFixed(0)} m/s  q ${(interp(d.t_s, d.q_pa, T) / 1000).toFixed(1)} kPa  ` +
    `m ${(interp(d.t_s, d.mass_kg, T) / 1000).toFixed(0)} t`;
  if ($("chk-sync").checked && S.replay.touched) {
    clock.locked = true;
    setSim(d.t0_unix * 1000 + T * 1000, true);
  }
}

$("scrub").addEventListener("input", (e) => setReplayT(Number(e.target.value), true));
$("btn-play").addEventListener("click", () => {
  if (!S.traj) return;
  const d = S.traj.data;
  const tmax = d.t_s[d.t_s.length - 1];
  S.replay.playing = !S.replay.playing;
  if (S.replay.playing && S.replay.t >= tmax) S.replay.t = 0;
  S.replay.touched = true;
  S.replay.last = performance.now();
  $("btn-play").textContent = S.replay.playing ? "Pause" : "Play";
});
$("sel-speed").addEventListener("change", (e) => (S.replay.speed = Number(e.target.value)));

function buildTrajSelect() {
  const sel = $("traj-select");
  sel.replaceChildren();
  const list = trajectorySources(S.manifest);
  for (const f of list) sel.append(el("option", { value: f.name, text: `${f.name}${f.dummy ? " (DUMMY)" : ""}` }));
  if (!list.length) sel.append(el("option", { value: "", text: "no trajectory files" }));
  sel.addEventListener("change", () => {
    const f = list.find((x) => x.name === sel.value);
    loadTrajectory(f ? f.name : null, f ? f.dummy : false);
  });
  return list;
}

// ---------------------------------------------------------------------------------------------- render loop
let lastUi = 0;
function frame(now) {
  if (S.replay.playing && S.traj) {
    const d = S.traj.data;
    const tmax = d.t_s[d.t_s.length - 1];
    const t = S.replay.t + ((now - S.replay.last) / 1000) * S.replay.speed;
    S.replay.last = now;
    setReplayT(t, false);
    if (t >= tmax) { S.replay.playing = false; $("btn-play").textContent = "Play"; }
  }
  const sim = simNow();
  if ($("chk-follow").checked && S.traj) {
    const d = S.traj.data;
    map.setView(interpLon(d.t_s, d.lon_deg, S.replay.t), interp(d.t_s, d.lat_deg, S.replay.t), Math.max(map.view.zoom, 8));
  }
  if (!$("view-mission").hidden) map.draw({
    simMs: sim,
    pos: S.pos,
    groupIdx: S.groupIdx,
    groupsOn: S.groupsOn,
    groups: S.catalog ? S.catalog.groups : [],
    site: S.windows ? S.windows.data.site : null,
    issTrack: S.issTrack,
    traj: S.traj ? S.traj.data : null,
    replayT: S.replay.t,
    eventsOn: true,
    highlight: S.issIdx >= 0 ? S.issIdx : null,
    highlightLabel: "ISS",
    selected: S.selected,
    selectedLabel: S.selected != null && S.catalog ? S.catalog.objects[S.selected][1] : "",
  });
  simFrame(now);
  if (now - lastUi > 250) {
    lastUi = now;
    $("utc").textContent = fmtUtc(sim);
    syncClockUi();
    if (S.selected != null) updateInfo();
  }
  requestAnimationFrame(frame);
}

// ================================================================================================
// 3D simulator (WP-17)
//
// The 3D view is loaded dynamically for two reasons: three.js is about 2 MB of module, and a browser
// without WebGL 2 must still get a working page. If either the import or the context creation fails
// the page says so, hides the tab and leaves the 2D Mission view as the usable fallback -- the 2D map
// is not a legacy leftover, it is the degraded path.
// ================================================================================================

const SIM = {
  view: null, // Sim3D
  panel: null, // MissionPanel
  vehicle: null, // the manifest row for the selected rocket
  rocket: null, // the rocket.json contract object
  windows: new Map(), // profile -> windows.json
  replay: { t: 0, playing: false, speed: 5, last: 0 },
  trackIdx: null,
  trackCenter: 0,
  trackWant: false,
  live: false, // was this trajectory flown here, or loaded from a file?
  available: false,
};
let sim3d = null;

/** Launch azimuth and site latitude determine the plane; recover the inclination they imply. */
function inclinationFor(azimuthDeg, latDeg) {
  const c = Math.sin((azimuthDeg * Math.PI) / 180) * Math.cos((latDeg * Math.PI) / 180);
  return (Math.acos(Math.max(-1, Math.min(1, c))) * 180) / Math.PI;
}

async function simWindowsFor(profile) {
  if (SIM.windows.has(profile)) return SIM.windows.get(profile);
  const src = windowsSource(S.manifest, profile);
  // A dummy windows file must not quietly become the launch conditions for a "real" flight.
  const data = src && !src.dummy ? await getJSON(src.name) : null;
  const value = data && Array.isArray(data.windows) ? { ...data, _name: src.name } : null;
  SIM.windows.set(profile, value);
  return value;
}

/** Fly the selected vehicle from the selected window, in the browser. */
async function simLaunch() {
  const entry = SIM.vehicle;
  if (!entry || !SIM.rocket) {
    SIM.panel.status("Pick a vehicle first.", "bad");
    return;
  }
  const wins = await simWindowsFor(entry.profile);
  if (!wins) {
    SIM.panel.status(`No real launch windows for ${entry.profile}; cannot set launch conditions.`, "bad");
    return;
  }
  // Prefer the window the user picked on the Mission tab when it belongs to this profile.
  let w = null;
  if (S.windows && S.windows.profile === entry.profile && S.selWindow) {
    w = wins.windows.find((x) => x.id === S.selWindow) || null;
  }
  if (!w) w = wins.windows.find((x) => x.flyable) || wins.windows[0];
  if (!w) {
    SIM.panel.status("That windows file has no windows in it.", "bad");
    return;
  }

  SIM.panel.status("Flying...");
  try {
    const { simulateAscent } = await import("./sim3d/ascent.js");
    const t0 = performance.now();
    const res = simulateAscent(SIM.rocket, {
      siteLatDeg: wins.site.lat_deg,
      siteLonDeg: wins.site.lon_deg,
      azimuthDeg: w.azimuth_deg,
      targetInclDeg: inclinationFor(w.azimuth_deg, wins.site.lat_deg),
      pitchParams: entry.pitch_program,
      trajectoryId: `traj_${entry.rocket_id}_${w.id}_browser`,
      windowId: w.id,
      t0Unix: w.t_launch_unix,
    });
    const ms = performance.now() - t0;

    SIM.live = true;
    sim3d.setTrajectory(res.trajectory, res.extra, SIM.rocket);
    SIM.panel.setTrajectory(res.trajectory, { live: true });
    SIM.replay.t = 0;
    SIM.replay.playing = true;
    SIM.replay.last = performance.now();
    SIM.panel.setPlaying(true);
    sim3d.setReplayT(0);
    sim3d.setMode("flight");
    sim3d.setCameraMode("pad");

    const ins = res.trajectory.events.find((e) => e.name === "insertion");
    SIM.panel.status(
      res.reachedOrbit
        ? `Orbit: ${res.trajectory.final_orbit.perigee_km} x ${res.trajectory.final_orbit.apogee_km} km at T+${ins.t_s} s (${ms.toFixed(0)} ms)`
        : `Did not reach orbit (${ms.toFixed(0)} ms)`,
      res.reachedOrbit ? "ok" : "bad",
    );
  } catch (e) {
    console.error(e);
    SIM.panel.status("The ascent simulation failed: " + e.message, "bad");
  }
}

async function simSelectVehicle(entry) {
  SIM.vehicle = entry;
  SIM.rocket = null;
  SIM.panel.markVehicle(entry, null);
  const obj = await getJSON(entry.name);
  if (!obj || !Array.isArray(obj.stages)) {
    SIM.panel.status(`Could not load ${entry.name}.`, "bad");
    return;
  }
  SIM.rocket = obj;
  SIM.panel.markVehicle(entry, obj);
  SIM.panel.status(`${entry.label} ready. Press Launch to fly it.`);
  // Rebuild the vehicle model against the trajectory already on screen, if any.
  if (SIM.view?.traj) sim3d.setTrajectory(SIM.view.traj, SIM.view.trajExtra, obj);
}

function simSetReplayT(t, fromUser) {
  const d = sim3d?.traj;
  if (!d) return;
  const tmax = d.t_s[d.t_s.length - 1];
  SIM.replay.t = clamp(t, 0, tmax);
  if (fromUser) {
    SIM.replay.playing = false;
    SIM.panel.setPlaying(false);
  }
  sim3d.setReplayT(SIM.replay.t);
}

function simSelect(i) {
  sim3d.setSelected(i);
  SIM.trackIdx = i;
  SIM.trackWant = i != null;
  if (i == null) {
    SIM.panel.setSelection(null);
    return;
  }
  SIM.lastSel = i;
}

async function initSim3D() {
  const canvas = $("sim-canvas");
  if (!canvas) return;
  let Sim3D;
  try {
    ({ Sim3D } = await import("./sim3d/view.js"));
  } catch (e) {
    console.error(e);
    banner("The 3D simulator could not load (" + e.message + "). The Mission tab works without it.", "warn");
    $("tab-sim").hidden = true;
    showView("mission");
    return;
  }

  SIM.panel = new MissionPanel({
    onSelectVehicle: (v) => simSelectVehicle(v),
    onLaunch: () => simLaunch(),
    onPlayToggle: () => {
      if (!sim3d?.traj) return;
      const tmax = sim3d.traj.t_s[sim3d.traj.t_s.length - 1];
      if (!SIM.replay.playing && SIM.replay.t >= tmax) SIM.replay.t = 0;
      SIM.replay.playing = !SIM.replay.playing;
      SIM.replay.last = performance.now();
      SIM.panel.setPlaying(SIM.replay.playing);
    },
    onScrub: (t) => simSetReplayT(t, true),
    onSpeed: (x) => (SIM.replay.speed = x),
    onCamera: (m) => sim3d.setCameraMode(m),
    onReset: () => (sim3d.mode === "flight" ? sim3d.setCameraMode("chase") : sim3d.resetView()),
    onToggleGroup: (g, on) => {
      on ? S.groupsOn.add(g) : S.groupsOn.delete(g);
      sim3d.setGroups(S.groupsOn);
    },
  });

  sim3d = new Sim3D(canvas, {
    onPick: (i) => simSelect(i),
    onHoverObject: (i, ndcX, ndcY) => {
      const pin = $("sim-pin");
      if (i == null || !S.catalog) {
        pin.hidden = true;
        return;
      }
      const r = canvas.getBoundingClientRect();
      pin.hidden = false;
      pin.textContent = S.catalog.objects[i][1] || `NORAD ${S.catalog.objects[i][0]}`;
      pin.style.left = `${((ndcX + 1) / 2) * r.width}px`;
      pin.style.top = `${((1 - ndcY) / 2) * r.height}px`;
    },
  });
  SIM.view = sim3d;
  // A handle for poking at the scene from the console while developing. Local only: it is a
  // debugging affordance, not an API, and nothing in the page reads it.
  if (["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) window.__sim3d = sim3d;

  if (!sim3d.ok) {
    banner("WebGL is not available in this browser, so the 3D simulator is off. The Mission tab works without it.", "warn");
    $("tab-sim").hidden = true;
    showView("mission");
    return;
  }
  SIM.available = true;

  $("mode-earth").addEventListener("click", () => sim3d.setMode("earth"));
  $("mode-flight").addEventListener("click", () => {
    if (!sim3d.traj) {
      SIM.panel.status("Nothing to fly yet. Press Launch, or pick a trajectory on the Mission tab.", "bad");
      return;
    }
    sim3d.setMode("flight");
  });

  new ResizeObserver(() => sim3d.resize()).observe($("sim-canvas").parentElement);

  const rockets = (S.manifest.files || []).filter((f) => f.kind === "rocket" && f.rocket_id);
  SIM.panel.setVehicles(rockets);
  if (!rockets.length) {
    SIM.panel.status("No vehicles in the manifest; re-run scripts/export_web_data.py.", "bad");
  }

  // Start from whatever real trajectory the Mission tab already loaded, so the view is never empty.
  if (S.traj && !S.traj.dummy) {
    SIM.live = false;
    const match = rockets.find((r) => r.rocket_id === S.traj.data.rocket_id);
    const rocketObj = match ? await getJSON(match.name) : null;
    sim3d.setTrajectory(S.traj.data, null, rocketObj);
    SIM.panel.setTrajectory(S.traj.data, { live: false, name: S.traj.name });
  }
}

/** Per-frame work for the 3D view. Called from the single page render loop. */
let simFrameFailed = false;
function simFrame(now) {
  if (!SIM.available || simFrameFailed || $("view-sim").hidden) return;
  try {
    simFrameInner(now);
  } catch (e) {
    // One bad frame must not take the whole page down with it: the render loop is shared with the
    // 2D map and the clock, and an exception here would stop requestAnimationFrame for everything.
    simFrameFailed = true;
    console.error(e);
    banner("The 3D view hit an error and has been stopped: " + e.message + " The other tabs still work.", "bad");
  }
}

function simFrameInner(now) {

  if (SIM.replay.playing && sim3d.traj) {
    const tmax = sim3d.traj.t_s[sim3d.traj.t_s.length - 1];
    const t = SIM.replay.t + ((now - SIM.replay.last) / 1000) * SIM.replay.speed;
    SIM.replay.last = now;
    SIM.replay.t = Math.min(t, tmax);
    sim3d.setReplayT(SIM.replay.t);
    if (t >= tmax) {
      SIM.replay.playing = false;
      SIM.panel.setPlaying(false);
    }
  }

  // In flight mode the clock belongs to the flight, so the sun is where it was at launch plus T+.
  const d = sim3d.traj;
  sim3d.setTime(sim3d.mode === "flight" && d ? d.t0_unix * 1000 + SIM.replay.t * 1000 : simNow());

  const tel = sim3d.frame(now / 1000);
  SIM.panel.tick(tel, SIM.replay.t);

  if (SIM.lastSel != null && S.catalog && S.pos) {
    const i = SIM.lastSel;
    SIM.panel.setSelection(
      S.catalog.objects[i],
      { latDeg: S.pos.lat[i], lonDeg: S.pos.lon[i], altKm: S.pos.alt[i] },
      S.catalog.groups[S.catalog.objects[i][2]],
      tel?.closeUp,
      (simNow() - tleEpochMs(S.catalog.objects[i][3])) / 86400000,
    );
  }
}

// ---------------------------------------------------------------------------------------------- views (tabs)
let sky = null;
const nameIndex = new Map();
function showView(view) {
  for (const b of document.querySelectorAll(".viewtabs button")) b.setAttribute("aria-selected", String(b.dataset.view === view));
  $("view-sim").hidden = view !== "sim";
  $("view-mission").hidden = view !== "mission";
  $("view-sky").hidden = view !== "sky";
  if (view === "sky" && sky) sky.ensureLoaded();
  if (view === "mission") window.dispatchEvent(new Event("resize"));
  // A canvas sized while hidden has no size, so the 3D view is measured again on the way in.
  if (view === "sim" && SIM.available) sim3d.resize();
}
for (const b of document.querySelectorAll(".viewtabs button")) b.addEventListener("click", () => showView(b.dataset.view));

// ---------------------------------------------------------------------------------------------- boot
async function boot() {
  syncClockUi();
  requestAnimationFrame(frame);

  const land = await getJSON("land.json");
  if (land && land.rings) map.land = land.rings;
  else banner("Coastline data missing; showing the graticule only.", "warn");

  S.manifest = (await getJSON("manifest.json")) || (await probeManifest());
  sky = new SkyView({ manifest: S.manifest, nameOf: (id) => nameIndex.get(id) });
  if (S.manifest._probed) banner("manifest.json not found; probing for known data files.", "warn");

  // Space weather (context only): never blocks the page and never shows stale data on failure
  const refreshKp = () => loadSpaceWeather().then((pts) => renderKp(pts)).catch((e) => renderKp(null, e));
  refreshKp();
  setInterval(refreshKp, 10 * 60e3);
  new ResizeObserver(() => drawKpChart($("sw-chart"), window.__kpPoints || null)).observe($("sw-chart"));

  // Windows panel
  buildTabs();
  const first = PROFILES.find((p) => windowsSource(S.manifest, p.id));
  if (first) await loadWindows(first.id);
  else {
    $("win-note").textContent = "No launch windows file found.";
    banner("No windows data found in public/data/. Run scripts/export_web_data.py.", "warn");
  }

  // Replay panel
  const list = buildTrajSelect();
  const t0 = list[0];
  await loadTrajectory(t0 ? t0.name : null, t0 ? t0.dummy : false);

  // Satellites
  const cat = await getJSON("catalog_web.json");
  if (cat && Array.isArray(cat.objects) && cat.objects.length) {
    S.catalog = cat;
    for (const o of cat.objects) nameIndex.set(o[0], o[1]);
    S.groupIdx = Uint8Array.from(cat.objects, (o) => o[2]);
    S.issIdx = cat.objects.findIndex((o) => o[0] === 25544);
    buildChips();
    $("catalog-note").textContent =
      `${cat.shown_objects.toLocaleString()} of ${cat.total_objects.toLocaleString()} catalog objects shown (` +
      `${cat.catalog_mode}); latest TLE epoch ${String(cat.latest_tle_epoch_utc).slice(0, 16)}Z. ` +
      "Real CelesTrak elements propagated with SGP4 in your browser; position error is kilometre-level and grows with element age. " +
      `Includes ${(cat.omm_only_objects || 0).toLocaleString()} objects numbered 100000+ (no TLE exists; propagated from OMM elements).`;
    if (cat.catalog_mode !== "full-catalog") banner("Using the small offline catalog sample, not the full live catalog.", "warn");
    startWorker();
    pump();
  } else {
    banner("Live satellite catalog (catalog_web.json) not available; the map shows windows and replay only.", "warn");
    $("catalog-note").textContent = "No satellite catalog loaded.";
  }

  // 3D simulator last: it is the heaviest import, and everything above has to work without it.
  await initSim3D();
  if (SIM.available && S.catalog) {
    sim3d.setCatalog(S.catalog);
    sim3d.setGroups(S.groupsOn);
    const counts = {};
    for (const g of S.catalog.groups) counts[g] = 0;
    for (const o of S.catalog.objects) counts[S.catalog.groups[o[2]]]++;
    SIM.panel.setLayers(S.catalog.groups, counts, S.groupsOn);
    $("cat-count").textContent = `${S.catalog.shown_objects.toLocaleString()} of ${S.catalog.total_objects.toLocaleString()} objects, SGP4 in your browser`;
  }
}

boot().catch((e) => {
  console.error(e);
  banner("Unexpected error while starting: " + e.message, "bad");
});
