/* The one place colours are defined for JavaScript (canvas 2D, WebGL, inline styles).
 *
 * These values must stay in step with the custom properties in css/style.css: CSS cannot be read
 * from a WebGL shader and getComputedStyle on :root is both slow and fragile, so the palette lives
 * here as plain hex and the stylesheet repeats it. tests/web/theme.test.mjs checks the two agree,
 * so a colour changed in one place and not the other fails the suite instead of drifting quietly.
 *
 * Palette: earthy. Warm charcoal and umber surfaces, bone text, ochre as the single primary accent,
 * sage / clay / rust for state. No saturated blues or greens anywhere.
 */

export const C = {
  bg: "#191511",
  panel: "#262019",
  panel2: "#332b22",
  panel3: "#40362a",
  line: "#4c4034",
  line2: "#645443",
  text: "#f4ecdf",
  muted: "#b5a28a",
  faint: "#897a65",

  ochre: "#efb455",
  sage: "#a3bb89",
  clay: "#d47e46",
  rust: "#d15c45",
  sand: "#e3d3b2",
  slate: "#8fbdce",
};

/** Semantic aliases. Use these in UI code so a palette change does not need a search for hexes. */
export const T = {
  accent: C.ochre,
  ok: C.sage,
  warn: C.ochre,
  bad: C.rust,
  grid: C.line,
  gridFaint: "#2a231c",
  axis: C.faint,
  plotBg: "#1f1a15",
  readout: C.sand,
};

/** Catalog group colours, shared by the 2D map, the Sky scatter and the 3D view. */
export const GROUP_COLORS = {
  stations: C.ochre,
  "gps-ops": C.sage,
  geo: C.sand,
  active: "#9bb2bd",
  starlink: C.slate,
  debris: "#b06a56",
};

/** Per-quantity plot colours, so the panel graphs and the HUD agree. */
export const SERIES = {
  alt: C.slate,
  speed: C.sage,
  q: C.ochre,
  mass: C.clay,
  accel: C.sand,
};

/** Colour for cluster number `rank`. Twenty-odd clusters genuinely need hue variety, so the hue
 * walks the golden angle, but saturation and lightness are held low and warm so the scatter still
 * sits inside the earthy palette instead of turning into a rainbow. */
export const clusterColor = (rank) => `hsl(${(rank * 137.508) % 360} 40% 60%)`;

/** "#rrggbb" -> 0xrrggbb, for three.js Color and material constructors. */
export const hex = (s) => parseInt(String(s).replace("#", ""), 16);

/** "#rrggbb" -> "rgba(r,g,b,a)", for canvas fills that need transparency. */
export function rgba(s, a) {
  const v = hex(s);
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
}
