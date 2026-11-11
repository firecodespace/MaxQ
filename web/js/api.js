/* Client for the mission API (WP2-06), with an honest offline path.
 *
 * MaxQ is deployed two ways and both have to work:
 *
 *   LIVE     the local API is running (`make server`). Missions can be created, launched, watched
 *            over a WebSocket and replayed.
 *   STATIC   no backend at all, which is the public deploy. The library and the launch windows are
 *            read from the exported files under public/data, so the wizard still shows real
 *            vehicles, real pads and real windows -- and anything that would need a backend is
 *            disabled and says why, rather than failing silently or showing invented data.
 *
 * `probe()` decides which mode is in force, once, and everything else branches on it. The API base
 * can be overridden with ?api=http://host:port for a non-default port.
 */

const DEFAULT_BASE = "http://127.0.0.1:8000";
const STATIC_BASE = "public/data/";
const PROBE_TIMEOUT_MS = 1500;

export const MODE = { LIVE: "live", STATIC: "static" };

function apiBase() {
  const q = new URLSearchParams(location.search).get("api");
  if (q) return q.replace(/\/$/, "");
  try {
    const saved = localStorage.getItem("maxq.api");
    if (saved) return saved.replace(/\/$/, "");
  } catch (_) {
    /* private browsing: fall through to the default */
  }
  return DEFAULT_BASE;
}

async function fetchJson(url, opts = {}, timeoutMs = 15000) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...opts, signal: ctrl.signal });
    const text = await r.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch (_) {
      body = text;
    }
    if (!r.ok) {
      const err = new Error(`${r.status} ${r.statusText}`);
      err.status = r.status;
      err.body = body;
      throw err;
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

export class MaxQApi {
  constructor(base = apiBase()) {
    this.base = base;
    this.mode = null;
    this.versions = null;
    this.reason = null;
  }

  /** Decide live or static. Called once at boot; never throws. */
  async probe() {
    try {
      const h = await fetchJson(`${this.base}/api/health`, {}, PROBE_TIMEOUT_MS);
      this.mode = MODE.LIVE;
      this.versions = { sim_version: h.sim_version, verifier_version: h.verifier_version };
      return this.mode;
    } catch (e) {
      this.mode = MODE.STATIC;
      this.reason =
        e.name === "AbortError"
          ? `No response from ${this.base}`
          : `${this.base} unreachable (${e.message})`;
      return this.mode;
    }
  }

  get isLive() {
    return this.mode === MODE.LIVE;
  }

  /* ---------------------------------------------------------------- library */

  /** Vehicles, each with its cited sources and its measured sim_validation. */
  async rockets() {
    return this.isLive
      ? fetchJson(`${this.base}/api/library/rockets`)
      : fetchJson(`${STATIC_BASE}rockets.json`);
  }

  async pads() {
    return this.isLive
      ? fetchJson(`${this.base}/api/library/pads`)
      : fetchJson(`${STATIC_BASE}pads.json`);
  }

  /**
   * Launch windows for a profile.
   *
   * Static mode has to find the file by name, because there is no endpoint to ask. The manifest
   * lists what was exported, so the lookup is over real files rather than a guess at a filename.
   */
  async windows(profile, manifest) {
    if (this.isLive) {
      return fetchJson(`${this.base}/api/library/windows?profile=${encodeURIComponent(profile)}`);
    }
    const files = (manifest && manifest.files) || [];
    const hit = files.find(
      (f) => f.kind === "windows" && !f.dummy && f.name.startsWith(`windows_${profile}_`),
    );
    if (!hit) {
      const err = new Error(`no exported windows for ${profile}`);
      err.status = 404;
      throw err;
    }
    return fetchJson(STATIC_BASE + hit.name);
  }

  /* ---------------------------------------------------------------- missions (live only) */

  async createMission(request) {
    return fetchJson(`${this.base}/api/missions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  }

  async launch(missionId, maxAttempts) {
    const q = maxAttempts ? `?max_attempts=${maxAttempts}` : "";
    return fetchJson(`${this.base}/api/missions/${encodeURIComponent(missionId)}/launch${q}`, {
      method: "POST",
    });
  }

  async abort(missionId) {
    return fetchJson(`${this.base}/api/missions/${encodeURIComponent(missionId)}/abort`, {
      method: "POST",
    });
  }

  async runStatus(missionId) {
    return fetchJson(`${this.base}/api/missions/${encodeURIComponent(missionId)}/run`);
  }

  async attempts(missionId) {
    return fetchJson(`${this.base}/api/missions/${encodeURIComponent(missionId)}/attempts`);
  }

  /** Live event stream. Returns the WebSocket so the caller can close it. */
  stream(missionId, { onEvent, onClose, onError } = {}) {
    const ws = new WebSocket(
      `${this.base.replace(/^http/, "ws")}/api/missions/${encodeURIComponent(missionId)}/stream`,
    );
    ws.addEventListener("message", (m) => {
      let ev;
      try {
        ev = JSON.parse(m.data);
      } catch (_) {
        return;
      }
      if (ev.type === "stream_closed") onClose?.(ev);
      else onEvent?.(ev);
    });
    ws.addEventListener("error", (e) => onError?.(e));
    ws.addEventListener("close", () => onClose?.({ type: "socket_closed" }));
    return ws;
  }

  async sessions() {
    return this.isLive ? fetchJson(`${this.base}/api/sessions`) : [];
  }

  async sessionEvents(sessionId) {
    return fetchJson(`${this.base}/api/sessions/${encodeURIComponent(sessionId)}/events`);
  }
}
