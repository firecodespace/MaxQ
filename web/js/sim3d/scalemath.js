/* The scale and zoom model. Pure functions, no three.js, so tests/web/scalemath.test.mjs can pin
 * the behaviour down exactly.
 *
 * The problem this solves
 * ----------------------
 * One scene has to hold a 6,378 km planet, a 70 m rocket and a 10 m satellite. At true scale the
 * satellite is 1/640,000 of Earth's radius: from any distance that shows the globe it is far smaller
 * than one pixel, and from any distance that shows the satellite the globe fills the sky. A single
 * fixed scale therefore cannot work, and scaling everything up by a constant ("exaggerated scale")
 * throws the geometry away.
 *
 * What we do instead
 * ------------------
 * POSITIONS ARE NEVER SCALED. Every object sits at its true kilometre position, always. Only the
 * drawn SIZE of a small body adapts, by one rule:
 *
 *     draw it at its true size whenever its true size covers at least `minPx` pixels;
 *     otherwise draw it at whatever size covers exactly `minPx` pixels.
 *
 * So close up you see true proportions, and far away a satellite is a marker that stays findable
 * instead of vanishing. `inflation()` reports the factor so the UI can say when a body is drawn
 * larger than life, and the viewport shows a scale bar rather than implying everything is to scale.
 *
 * Precision is handled separately, by a floating origin (see view.js): positions are expressed
 * relative to a point near the camera before they reach a Float32 attribute, which is what keeps a
 * metre-sized nozzle from jittering 6,378 km from the origin.
 *
 * Units are KILOMETRES throughout this file.
 */

export const R_EARTH_KM = 6378.137;

/** Half-height of the view frustum at a distance: distance * tan(fov/2). `fovYDeg` is vertical. */
export const frustumHalfHeight = (distanceKm, fovYDeg) =>
  distanceKm * Math.tan((fovYDeg * Math.PI) / 360);

/** Radius in pixels that a sphere of `radiusKm` covers at `distanceKm`. */
export function pixelRadius(radiusKm, distanceKm, fovYDeg, viewportPx) {
  const half = frustumHalfHeight(distanceKm, fovYDeg);
  if (!(half > 0)) return Infinity;
  return (radiusKm / half) * (viewportPx / 2);
}

/** Inverse of pixelRadius: the radius in km that would cover `px` pixels. */
export function kmForPixels(px, distanceKm, fovYDeg, viewportPx) {
  const half = frustumHalfHeight(distanceKm, fovYDeg);
  return (px * 2 * half) / viewportPx;
}

/**
 * The radius to actually draw a body at.
 *
 * Returns the true radius once the body is big enough to see, and otherwise the radius that makes it
 * exactly `minPx` pixels across the radius. `maxInflation` caps how far the lie can go, so a 10 cm
 * fleck of debris never swells into something the size of a moon; past that cap the body keeps
 * shrinking honestly and eventually disappears, which is the truthful outcome.
 */
export function renderRadius(trueRadiusKm, distanceKm, fovYDeg, viewportPx, opts = {}) {
  const minPx = opts.minPx ?? 2.5;
  const maxInflation = opts.maxInflation ?? 4000;
  const floor = kmForPixels(minPx, distanceKm, fovYDeg, viewportPx);
  if (!(floor > trueRadiusKm)) return trueRadiusKm;
  return Math.min(floor, trueRadiusKm * maxInflation);
}

/** How many times larger than life a body is being drawn (1 = true scale). */
export function inflation(trueRadiusKm, drawnRadiusKm) {
  if (!(trueRadiusKm > 0)) return Infinity;
  return drawnRadiusKm / trueRadiusKm;
}

/**
 * Should this body be a real mesh or a screen-space marker?
 *
 * A mesh is only worth its vertices once the body covers a few pixels at true scale; below that a
 * marker is both cheaper and more honest, since any mesh would be sub-pixel anyway. `hysteresis`
 * keeps a body from flickering between the two while the camera drifts around the boundary.
 */
export function detailLevel(trueRadiusKm, distanceKm, fovYDeg, viewportPx, opts = {}) {
  const meshPx = opts.meshPx ?? 5;
  const hysteresis = opts.hysteresis ?? 1.4;
  const wasMesh = opts.wasMesh ?? false;
  const px = pixelRadius(trueRadiusKm, distanceKm, fovYDeg, viewportPx);
  const threshold = wasMesh ? meshPx / hysteresis : meshPx;
  return px >= threshold ? "mesh" : "marker";
}

/* ------------------------------------------------------------------ zoom
 *
 * Zoom is multiplicative in ALTITUDE ABOVE THE NEAREST SURFACE, not in distance to the orbit
 * centre. That one choice gives the whole "highly tuned" feel asked for: a notch of the wheel is the
 * same proportional step wherever you are, so you cross from 60,000 km out to 50 m above a launch
 * pad in a steady number of notches, you never shoot through the surface, and the step is
 * automatically gentle when you are close to something and sweeping when you are not.
 */

/**
 * New camera distance from the pivot after `notches` of wheel (positive = zoom in).
 *
 * `surfaceKm` is the pivot's own radius, so `distanceKm - surfaceKm` is the altitude being scaled.
 * The result is clamped so the camera stays between `minAltKm` above the surface and `maxAltKm`.
 */
export function zoomDistance(distanceKm, surfaceKm, notches, opts = {}) {
  const rate = opts.rate ?? 0.22;
  const minAltKm = opts.minAltKm ?? 0.02; // 20 m: close enough to stand beside a rocket
  const maxAltKm = opts.maxAltKm ?? 420000; // past the Moon's distance, so GEO fits easily
  const alt = Math.max(minAltKm, distanceKm - surfaceKm);
  const next = alt * Math.exp(-notches * rate);
  return surfaceKm + Math.min(maxAltKm, Math.max(minAltKm, next));
}

/**
 * Pivoted zoom: move the camera along the ray through a point, keeping that point fixed on screen.
 *
 * `camKm` and `pivotKm` are [x, y, z]; `factor` is the new distance over the old. This is what makes
 * the cursor a pivot: pass the point under the cursor and that feature stays put while everything
 * else flows around it. In free space the caller passes the orbit centre and the behaviour collapses
 * to a plain dolly, which is the expected feel.
 */
export function pivotZoom(camKm, pivotKm, factor) {
  return [
    pivotKm[0] + (camKm[0] - pivotKm[0]) * factor,
    pivotKm[1] + (camKm[1] - pivotKm[1]) * factor,
    pivotKm[2] + (camKm[2] - pivotKm[2]) * factor,
  ];
}

/**
 * Near and far clip planes for a camera at `altAboveSurfaceKm`.
 *
 * With a logarithmic depth buffer the ratio may be enormous, so near is set from the altitude (never
 * larger than a hundredth of it, so nothing close is sliced) and far is set past GEO. Both are
 * clamped: a near plane of zero breaks the projection matrix.
 */
export function clipPlanes(altAboveSurfaceKm, opts = {}) {
  const near = Math.max(0.002, Math.min(1000, altAboveSurfaceKm * 0.01));
  const far = Math.max(opts.minFarKm ?? 2e6, (altAboveSurfaceKm + R_EARTH_KM) * 12);
  return { near, far };
}

/** Degrees of camera rotation per pixel dragged, eased so a close-in view is not twitchy. */
export function orbitSensitivity(altAboveSurfaceKm) {
  const t = Math.min(1, altAboveSurfaceKm / R_EARTH_KM);
  return 0.06 + 0.24 * t;
}

/* ------------------------------------------------------------------ scale bar */

/** 1, 2 or 5 times a power of ten, the largest such value <= v. */
export function niceStep(v) {
  if (!(v > 0)) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / p;
  return (m >= 5 ? 5 : m >= 2 ? 2 : 1) * p;
}

/**
 * A scale bar for the current view: a round distance and how wide it is in pixels.
 *
 * Measured in the plane through the pivot, which is where the user is looking, so the number is
 * honest for what is on screen even though a perspective view has no single scale.
 */
export function scaleBar(distanceKm, fovYDeg, viewportPx, targetPx = 110) {
  const kmAtTarget = kmForPixels(targetPx, distanceKm, fovYDeg, viewportPx);
  const km = niceStep(kmAtTarget);
  const px = (km / kmForPixels(1, distanceKm, fovYDeg, viewportPx)) || 0;
  const label = km >= 1 ? `${km.toLocaleString()} km` : `${Math.round(km * 1000).toLocaleString()} m`;
  return { km, px, label };
}
