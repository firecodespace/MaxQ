/* SGP4 worker: keeps the satellite records off the main thread.
 * Messages in:  {type:"init", objects:[[norad,name,groupIdx,tle1,tle2],...]}
 *               {type:"positions", id, t}                 t = ms since epoch (UTC)
 *               {type:"track", id, idx, t0, t1, step}     ground track of one object, ms and seconds
 * Messages out: {type:"ready", n, bad} | {type:"positions", id, t, lat, lon, alt, bad} | {type:"track", id, idx, pts}
 * lat/lon in degrees, alt in km (TEME -> geodetic via satellite.js, GMST from the same clock).
 */
importScripts("../vendor/satellite.min.js");

let recs = [];

function geodetic(rec, date, gmst) {
  const pv = satellite.propagate(rec, date);
  if (!pv || !pv.position) return null;
  const g = satellite.eciToGeodetic(pv.position, gmst);
  return [satellite.degreesLat(g.latitude), satellite.degreesLong(g.longitude), g.height];
}

self.onmessage = (e) => {
  const m = e.data;
  if (m.type === "init") {
    let bad = 0;
    recs = m.objects.map((o) => {
      try {
        const r = satellite.twoline2satrec(o[3], o[4]);
        if (r.error) { bad++; return null; }
        return r;
      } catch (_) { bad++; return null; }
    });
    self.postMessage({ type: "ready", n: recs.length, bad });
  } else if (m.type === "positions") {
    const date = new Date(m.t);
    const gmst = satellite.gstime(date);
    const n = recs.length;
    const lat = new Float32Array(n), lon = new Float32Array(n), alt = new Float32Array(n);
    let bad = 0;
    for (let i = 0; i < n; i++) {
      const g = recs[i] ? geodetic(recs[i], date, gmst) : null;
      if (!g) { lat[i] = NaN; lon[i] = NaN; alt[i] = NaN; bad++; continue; }
      lat[i] = g[0]; lon[i] = g[1]; alt[i] = g[2];
    }
    self.postMessage({ type: "positions", id: m.id, t: m.t, lat, lon, alt, bad }, [lat.buffer, lon.buffer, alt.buffer]);
  } else if (m.type === "track") {
    const rec = recs[m.idx];
    const pts = [];
    if (rec) {
      for (let t = m.t0; t <= m.t1; t += m.step * 1000) {
        const d = new Date(t);
        const g = geodetic(rec, d, satellite.gstime(d));
        if (g) pts.push(g[1], g[0]);
      }
    }
    const arr = new Float32Array(pts);
    self.postMessage({ type: "track", id: m.id, idx: m.idx, t0: m.t0, t1: m.t1, pts: arr }, [arr.buffer]);
  }
};
