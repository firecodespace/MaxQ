// Data loading. Every fetch is allowed to fail: callers get null and show a message instead of crashing.
const BASE = "public/data/";

export async function getJSON(name) {
  try {
    const r = await fetch(BASE + name, { cache: "no-cache" });
    if (!r.ok) return null;
    return await r.json();
  } catch (_) {
    return null;
  }
}

export const PROFILES = [
  { id: "crew_iss", label: "Crew to ISS" },
  { id: "cargo_iss", label: "Cargo to ISS" },
  { id: "meo_gps", label: "GPS (MEO)" },
  { id: "geo_comm", label: "GEO comms" },
];

export function isDummyNote(obj) {
  return String((obj && obj._note) || "").trim().toLowerCase().startsWith("dummy");
}

/** Find the windows file for a profile: real file first, then the (crew_iss-only) dummy. Returns {name, dummy} or null. */
export function windowsSource(manifest, profile) {
  const files = (manifest && manifest.files) || [];
  const real = files.filter((f) => f.kind === "windows" && !f.dummy && f.name.startsWith(`windows_${profile}_`));
  if (real.length) return { name: real[0].name, dummy: false };
  const dummy = files.find((f) => f.kind === "windows" && f.dummy);
  if (dummy && profile === "crew_iss") return { name: dummy.name, dummy: true };
  return null;
}

/** Trajectory files from the manifest, real ones first. */
export function trajectorySources(manifest) {
  const files = ((manifest && manifest.files) || []).filter((f) => f.kind === "trajectory");
  return files.sort((a, b) => Number(a.dummy) - Number(b.dummy) || a.name.localeCompare(b.name));
}

/** Fallback when manifest.json is missing: probe the well-known names. */
export async function probeManifest() {
  const names = ["windows_dummy.json", "trajectory_dummy.json", "windows_crew_iss_cape_canaveral.json"];
  const files = [];
  for (const n of names) {
    const obj = await getJSON(n);
    if (obj) files.push({ name: n, kind: n.split("_")[0], dummy: n.includes("_dummy") || isDummyNote(obj) });
  }
  return { files, catalog: { name: "catalog_web.json" }, _probed: true };
}
