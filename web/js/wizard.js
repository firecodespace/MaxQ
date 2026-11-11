/* The mission wizard (WP2-07): mission type, payload, vehicle, pad, constraints, launch.
 *
 * All six steps are fed by real data -- the cited vehicle and pad library, real plane-crossing
 * windows, live NOAA Kp -- and every number that is a simulated figure rather than a published one
 * says so on the card it appears on. The decisions live in wizard-logic.js so they can be tested
 * without a browser; this file is the view and the wiring.
 *
 * Without the local API the wizard still works from the exported files and only the Launch button
 * is unavailable, with the reason on it. See js/api.js.
 */

import { $, el } from "./util.js";
import { MODE } from "./api.js";
import {
  PROFILES,
  buildMissionRequest,
  capabilityLabel,
  constraintPreview,
  isPlaceholderWindows,
  padsForVehicle,
  profileById,
  rankVehicles,
  stepBlockers,
  usableWindows,
  vehicleCapability,
} from "./wizard-logic.js";

const STEPS = [
  { n: 1, key: "type", label: "Mission" },
  { n: 2, key: "payload", label: "Payload" },
  { n: 3, key: "vehicle", label: "Vehicle" },
  { n: 4, key: "pad", label: "Pad" },
  { n: 5, key: "constraints", label: "Constraints" },
  { n: 6, key: "launch", label: "Launch" },
];

const kg = (v) => `${Math.round(v).toLocaleString()} kg`;
const STATUS_WORD = { go: "GO", warn: "WATCH", "no-go": "NO-GO", unknown: "UNKNOWN" };

export class Wizard {
  /** @param {object} hooks {api, getManifest, getKp, onLaunched} */
  constructor(hooks = {}) {
    this.hooks = hooks;
    this.api = hooks.api;
    this.step = 1;
    this.state = {
      profile: "cargo_iss",
      payload_kg: 12000,
      crew: 0,
      vehicle: null,
      pad: null,
      window: null,
      max_attempts: 24,
    };
    this.rockets = [];
    this.pads = [];
    this.windowsFile = null;
    this.windowsError = null;
    this.busy = false;
    this.launched = null;

    this.root = $("wizard");
    this.railEl = $("wiz-rail");
    this.bodyEl = $("wiz-body");
    this.footEl = $("wiz-foot");
  }

  /* ---------------------------------------------------------------- data */

  async load() {
    try {
      [this.rockets, this.pads] = await Promise.all([this.api.rockets(), this.api.pads()]);
    } catch (e) {
      this.rockets = [];
      this.pads = [];
      this.loadError = e.message;
    }
    await this.loadWindows();
    this.render();
  }

  async loadWindows() {
    this.windowsFile = null;
    this.windowsError = null;
    try {
      this.windowsFile = await this.api.windows(this.state.profile, this.hooks.getManifest?.());
      const usable = usableWindows(this.windowsFile);
      // Keep a chosen window only if it still exists for this profile.
      if (!usable.some((w) => this.state.window && w.id === this.state.window.id)) {
        this.state.window = usable[0] || null;
      }
    } catch (e) {
      this.windowsError = e.status === 404 ? "No launch windows have been computed for this profile." : e.message;
      this.state.window = null;
    }
  }

  /* ---------------------------------------------------------------- navigation */

  blockers(step = this.step) {
    return stepBlockers(this.state)[step] || [];
  }

  canAdvance() {
    return this.blockers().length === 0;
  }

  go(step) {
    this.step = Math.max(1, Math.min(STEPS.length, step));
    this.render();
    this.root.scrollTop = 0;
  }

  async setProfile(id) {
    const p = profileById(id);
    if (!p || !p.ready) return;
    this.state.profile = id;
    this.state.crew = p.crewed ? 4 : 0;
    this.state.payload_kg = p.default_payload_kg;
    this.state.vehicle = null;
    this.state.pad = null;
    await this.loadWindows();
    this.render();
  }

  setVehicle(v) {
    this.state.vehicle = v;
    const pads = padsForVehicle(this.pads, v.id);
    this.state.pad = pads.length === 1 ? pads[0] : null;
    this.render();
  }

  /* ---------------------------------------------------------------- render */

  render() {
    this.renderRail();
    this.bodyEl.replaceChildren(this[`step${this.step}`]());
    this.renderFoot();
  }

  renderRail() {
    this.railEl.replaceChildren();
    for (const s of STEPS) {
      const done = s.n < this.step;
      const b = el("button", {
        type: "button",
        class: `wstep${s.n === this.step ? " now" : ""}${done ? " done" : ""}`,
        "aria-current": s.n === this.step ? "step" : "false",
        // Going back is always allowed; going forward only as far as the first unmet requirement.
        disabled: s.n > this.step && !this.canAdvance(),
      });
      b.append(el("i", { text: String(s.n) }), el("span", { text: s.label }));
      b.addEventListener("click", () => this.go(s.n));
      this.railEl.append(b);
    }
  }

  renderFoot() {
    this.footEl.replaceChildren();
    const blockers = this.blockers();
    const back = el("button", { type: "button", text: "Back", disabled: this.step === 1 });
    back.addEventListener("click", () => this.go(this.step - 1));
    this.footEl.append(back);

    const why = el("span", { class: "wiz-why", text: blockers[0] || "" });
    this.footEl.append(why);

    if (this.step < STEPS.length) {
      const next = el("button", { type: "button", class: "primary", text: "Next", disabled: !this.canAdvance() });
      next.addEventListener("click", () => this.go(this.step + 1));
      this.footEl.append(next);
    }
  }

  /* ---------------------------------------------------------------- step 1: mission type */

  step1() {
    const box = el("div", { class: "wiz-step" });
    box.append(
      el("h3", { text: "What is the mission?" }),
      el("p", { class: "note", text: "Only the two ISS profiles have real launch windows computed. The others are listed so the scope is visible, and are not selectable yet." }),
    );
    const grid = el("div", { class: "wiz-cards" });
    for (const p of PROFILES) {
      const card = el("button", {
        type: "button",
        class: "wcard",
        "aria-pressed": String(this.state.profile === p.id),
        disabled: !p.ready,
      });
      card.append(
        el("b", { text: p.label }),
        el("span", { class: "note", text: p.blurb }),
        el("span", { class: "wtag", text: p.ready ? `${p.accel_limit_g.toFixed(1)} g limit` : "not yet" }),
      );
      card.addEventListener("click", () => this.setProfile(p.id));
      grid.append(card);
    }
    box.append(grid);

    if (profileById(this.state.profile)?.crewed) {
      box.append(
        el("div", { class: "wiz-flag bad" },
          el("b", { text: "A crew mission cannot pass the verifier today. " }),
          document.createTextNode(
            "The ascent model burns each stage to depletion at constant thrust, so peak acceleration " +
            "is set by the vehicle rather than the flight plan: 4.4–4.6 g measured at every payload " +
            "tried, against this profile's 4.0 g limit. You can plan and launch it, and every attempt " +
            "will fail C03 until the simulator can throttle. Cargo to ISS, at 6 g, is flyable.",
          )),
      );
    }
    return box;
  }

  /* ---------------------------------------------------------------- step 2: payload */

  step2() {
    const p = profileById(this.state.profile);
    const box = el("div", { class: "wiz-step" });
    box.append(el("h3", { text: "What is it carrying?" }));

    const mass = el("input", { type: "number", id: "wiz-mass", min: "100", max: "40000", step: "100", value: String(this.state.payload_kg) });
    mass.addEventListener("input", () => {
      this.state.payload_kg = Number(mass.value) || 0;
      if (this.state.vehicle) this.state.vehicle = null;
      this.renderFoot();
      this.renderHint();
    });
    box.append(el("label", { class: "wiz-field" }, el("span", { text: "Payload mass" }), mass, el("em", { text: "kg" })));

    if (p?.crewed) {
      const crew = el("input", { type: "number", id: "wiz-crew", min: "1", max: "7", step: "1", value: String(this.state.crew || 4) });
      crew.addEventListener("input", () => {
        this.state.crew = Number(crew.value) || 0;
        this.renderFoot();
      });
      box.append(el("label", { class: "wiz-field" }, el("span", { text: "Crew" }), crew, el("em", { text: "people" })));
    }

    this.hintEl = el("div", { class: "note", id: "wiz-hint" });
    box.append(this.hintEl);
    queueMicrotask(() => this.renderHint());
    return box;
  }

  renderHint() {
    if (!this.hintEl) return;
    const fits = rankVehicles(this.rockets, { profile: this.state.profile, payload_kg: this.state.payload_kg, pads: this.pads })
      .filter((r) => r.eligible);
    this.hintEl.textContent = fits.length
      ? `${fits.length} vehicle${fits.length === 1 ? "" : "s"} in the library can lift this: ${fits.map((r) => r.vehicle.name.split(" (")[0]).join(", ")}.`
      : "No vehicle in the library can lift this payload for this mission type. Lower the mass, or go back and change the mission.";
    this.hintEl.className = fits.length ? "note" : "note wiz-warn";
  }

  /* ---------------------------------------------------------------- step 3: vehicle */

  step3() {
    const box = el("div", { class: "wiz-step" });
    box.append(
      el("h3", { text: "Which vehicle?" }),
      el("p", { class: "note", text: "Real vehicles, with figures taken from cited sources. Each card shows what the simulator actually lifted next to the published figure, because that measured gap is what the mission will be flown against." }),
    );
    if (this.loadError) {
      box.append(el("div", { class: "wiz-flag bad", text: `Could not load the vehicle library: ${this.loadError}` }));
      return box;
    }

    const ranked = rankVehicles(this.rockets, {
      profile: this.state.profile,
      payload_kg: this.state.payload_kg,
      pads: this.pads,
    });
    const list = el("div", { class: "wiz-cards wide" });
    for (const r of ranked) {
      const v = r.vehicle;
      const cap = vehicleCapability(v);
      const card = el("button", {
        type: "button",
        class: `wcard veh${r.eligible ? "" : " off"}`,
        "aria-pressed": String(this.state.vehicle?.id === v.id),
        disabled: !r.eligible,
      });
      card.append(
        el("b", { text: v.name }),
        el("span", { class: "wtag", text: v.kind === "real" ? "REAL" : "FICTIONAL" }),
        el("span", { class: "note mono", text: capabilityLabel(v) }),
      );
      if (cap.measured && !cap.within_tolerance) {
        card.append(el("span", { class: "wiz-why bad", text: "Outside the stated tolerance: the simulator does not fly this vehicle well." }));
      }
      if (!r.eligible) card.append(el("span", { class: "wiz-why", text: r.reasons.join(" · ") }));
      const srcs = el("span", { class: "wsrc" });
      (v.sources || []).slice(0, 3).forEach((s, i) => {
        if (i) srcs.append(document.createTextNode(" · "));
        const a = el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", text: s.title.replace(/^(SpaceX|ULA|Wikipedia|Wevolver):\s*/, "") });
        a.addEventListener("click", (e) => e.stopPropagation());
        srcs.append(a);
      });
      if ((v.sources || []).length) card.append(srcs);
      card.addEventListener("click", () => this.setVehicle(v));
      list.append(card);
    }
    box.append(list);
    return box;
  }

  /* ---------------------------------------------------------------- step 4: pad */

  step4() {
    const box = el("div", { class: "wiz-step" });
    box.append(el("h3", { text: "Which pad?" }));
    const pads = padsForVehicle(this.pads, this.state.vehicle?.id);
    if (!pads.length) {
      box.append(el("div", { class: "wiz-flag bad", text: "No pad in the library launches this vehicle." }));
      return box;
    }
    box.append(el("p", { class: "note", text: "Pad latitude sets how much of Earth's rotation the launch starts with, and which inclinations are reachable." }));
    const list = el("div", { class: "wiz-cards wide" });
    for (const p of pads) {
      const card = el("button", { type: "button", class: "wcard", "aria-pressed": String(this.state.pad?.id === p.id) });
      card.append(
        el("b", { text: p.name }),
        el("span", { class: "note mono", text: `${p.lat_deg.toFixed(4)}°, ${p.lon_deg.toFixed(4)}°` }),
        el("span", { class: "note", text: p.site || "" }),
      );
      if (p.azimuth_range_deg == null) {
        card.append(el("span", { class: "wiz-why", text: "No published launch-azimuth range; the source gives an orbital inclination range instead." }));
      }
      card.addEventListener("click", () => {
        this.state.pad = p;
        this.render();
      });
      list.append(card);
    }
    box.append(list);
    return box;
  }

  /* ---------------------------------------------------------------- step 5: constraints */

  step5() {
    const box = el("div", { class: "wiz-step" });
    box.append(el("h3", { text: "What do the constraints say?" }));

    // --- windows
    if (this.windowsError) {
      box.append(el("div", { class: "wiz-flag bad", text: this.windowsError }));
    } else if (this.windowsFile) {
      const ws = usableWindows(this.windowsFile);
      const sel = el("select", { id: "wiz-window", "aria-label": "Launch window" });
      for (const w of ws) {
        sel.append(el("option", {
          value: w.id,
          text: `${w.id} · ${new Date(w.t_ms).toISOString().slice(0, 16).replace("T", " ")}Z · az ${w.azimuth_deg.toFixed(1)}° · rdv ${w.rendezvous_hours_fast ?? "–"} h`,
        }));
      }
      sel.value = this.state.window?.id || (ws[0] && ws[0].id) || "";
      sel.addEventListener("change", () => {
        this.state.window = ws.find((w) => w.id === sel.value) || null;
        this.render();
      });
      box.append(el("label", { class: "wiz-field wide" }, el("span", { text: "Launch window" }), sel));
      box.append(el("div", { class: "note", text:
        `${ws.length} flyable window${ws.length === 1 ? "" : "s"} from the live ISS element set ` +
        `(${this.windowsFile.target?.norad_id}), site ${this.windowsFile.site?.name}. ` +
        "Azimuth is the inertial azimuth of the target plane." }));
      if (isPlaceholderWindows(this.windowsFile)) {
        box.append(el("div", { class: "wiz-flag warn", text: "These windows are a labelled PLACEHOLDER, not computed plane crossings." }));
      }
    }

    // --- the checklist
    const pv = constraintPreview({
      profile: this.state.profile,
      vehicle: this.state.vehicle,
      windowsFile: this.windowsFile,
      window: this.state.window,
      kp: this.hooks.getKp?.() ?? null,
      payload_kg: this.state.payload_kg,
    });
    this.preview = pv;

    const checks = el("div", { class: "wiz-checks" });
    for (const it of pv.items) {
      checks.append(el("div", { class: `wcheck ${it.status}` },
        el("span", { class: "wid", text: it.id }),
        el("span", { class: "wname", text: it.name }),
        el("span", { class: "wstat", text: STATUS_WORD[it.status] }),
        el("span", { class: "wdetail note", text: it.detail })));
    }
    box.append(checks);
    box.append(el("p", { class: "note", text:
      "This is a pre-flight read of what is already known, not a verdict. The verifier judges each " +
      "attempt after it is flown, and it is the only thing that decides feasibility." }));
    return box;
  }

  /* ---------------------------------------------------------------- step 6: launch */

  step6() {
    const box = el("div", { class: "wiz-step" });
    box.append(el("h3", { text: "Review and launch" }));

    const v = this.state.vehicle;
    const cap = v ? vehicleCapability(v) : null;
    const rows = [
      ["Mission", profileById(this.state.profile)?.label],
      ["Payload", `${kg(this.state.payload_kg)}${this.state.crew ? ` · ${this.state.crew} crew` : ""}`],
      ["Vehicle", v ? `${v.name} — ${capabilityLabel(v)}` : "—"],
      ["Pad", this.state.pad ? `${this.state.pad.name} (${this.state.pad.lat_deg.toFixed(3)}°, ${this.state.pad.lon_deg.toFixed(3)}°)` : "—"],
      ["Window", this.state.window ? `${this.state.window.id} · ${new Date(this.state.window.t_ms).toISOString().slice(0, 16).replace("T", " ")}Z` : "—"],
      ["Budget", `${this.state.max_attempts} attempts`],
    ];
    const dl = el("dl", { class: "wiz-review" });
    for (const [k, val] of rows) dl.append(el("dt", { text: k }), el("dd", { text: String(val ?? "—") }));
    box.append(dl);

    const attempts = el("input", { type: "number", min: "1", max: "400", step: "1", value: String(this.state.max_attempts) });
    attempts.addEventListener("input", () => (this.state.max_attempts = Math.max(1, Number(attempts.value) || 1)));
    box.append(el("label", { class: "wiz-field" }, el("span", { text: "Attempts to run" }), attempts, el("em", { text: "simulations" })));

    if (this.preview?.verdict === "no-go") {
      box.append(el("div", { class: "wiz-flag bad", text:
        "The pre-flight checks say this mission cannot pass. You can still run it — the attempts and " +
        "their constraint reports are real results — but expect every one to come back infeasible." }));
    }

    const status = el("div", { class: "note", id: "wiz-status" });
    const launch = el("button", { type: "button", class: "primary", text: "Launch" });

    if (!this.api.isLive) {
      launch.disabled = true;
      launch.textContent = "Launch needs the local API";
      box.append(el("div", { class: "wiz-flag warn" },
        el("b", { text: "No backend. " }),
        document.createTextNode(
          `Everything above is real data read from the exported files, but running a mission needs the API. ` +
          `Start it with \`make server\` and reload. (${this.api.reason || "not reachable"})`,
        )));
    } else {
      launch.addEventListener("click", async () => {
        if (this.busy) return;
        this.busy = true;
        launch.disabled = true;
        status.className = "note";
        status.textContent = "Creating the mission…";
        try {
          const req = buildMissionRequest({
            profile: this.state.profile,
            payload_kg: this.state.payload_kg,
            crew: this.state.crew,
            vehicle: this.state.vehicle,
            pad: this.state.pad,
            window: this.state.window,
            target_norad: this.windowsFile?.target?.norad_id ?? 25544,
            max_attempts: this.state.max_attempts,
          });
          await this.api.createMission(req);
          status.textContent = "Launching…";
          const run = await this.api.launch(req.id, this.state.max_attempts);
          this.launched = { request: req, run };
          status.className = "note wiz-ok";
          status.textContent = `Running ${req.id} (${run.max_attempts} attempts, ${run.max_wall_s} s budget).`;
          this.hooks.onLaunched?.(req, run);
        } catch (e) {
          status.className = "note wiz-warn";
          const fields = e.body?.detail?.fields;
          status.textContent = fields
            ? `The API rejected the mission: ${fields.slice(0, 3).join("; ")}`
            : `Could not launch: ${e.message}`;
          launch.disabled = false;
        } finally {
          this.busy = false;
        }
      });
    }

    box.append(el("div", { class: "runbar" }, launch, status));
    return box;
  }
}

export { MODE };
