/* The one place colours are defined for JavaScript (canvas 2D, WebGL, inline styles).
 *
 * These values must stay in step with the custom properties in css/style.css: CSS cannot be read
 * from a WebGL shader and getComputedStyle on :root is both slow and fragile, so the palette lives
 * here as plain hex and the stylesheet repeats it. tests/web/theme.test.mjs checks the two agree,
 * so a colour changed in one place and not the other fails the suite instead of drifting quietly.
 *
 * Palette: black, white and blue. Near-black blue-tinted surfaces, near-white text, one blue accent
 * with a lighter and a deeper shade either side of it. The only colour outside that family is a
 * single red, kept exclusively for danger -- DUMMY data and hard failures -- because a warning that
 * shares its colour with the interface is not a warning.
 */

export const C = {
  // Surfaces, darkest to lightest. Each carries a trace of blue so black reads as depth, not as a
  // hole in the screen.
  bg: "#04070e",
  panel: "#0a0f1a",
  panel2: "#111926",
  panel3: "#192337",
  line: "#1f2c42",
  line2: "#304563",

  // Type.
  text: "#eaf1fb",
  muted: "#8da3c2",
  faint: "#5a6e8e",

  // Blues.
  blue: "#4d9fff",
  sky: "#7cc6ff",
  deep: "#2a6fd6",
  ice: "#b9dcff",
  steel: "#a3bcd9",

  // The one non-blue, for danger only.
  danger: "#ff5f6d",

  white: "#ffffff",
};

/** Semantic aliases. Use these in UI code so a palette change does not need a search for hexes. */
export const T = {
  accent: C.blue,
  ok: C.sky,
  warn: C.ice,
  bad: C.danger,
  grid: C.line,
  gridFaint: "#15202f",
  axis: C.faint,
  plotBg: "#070c15",
  readout: C.ice,
};

/** Catalog group colours, shared by the 2D map, the Sky scatter and the 3D view. */
export const GROUP_COLORS = {
  stations: C.white,
  "gps-ops": C.sky,
  geo: C.blue,
  active: C.steel,
  starlink: C.deep,
  debris: "#607796",
};

/** Per-quantity plot colours, so the panel graphs and the HUD agree. */
export const SERIES = {
  alt: C.blue,
  speed: C.sky,
  q: C.white,
  mass: C.steel,
  accel: C.ice,
};

/** Colour for cluster number `rank`. Twenty-odd clusters need to be told apart, so the hue walks the
 * golden angle, but it is confined to the blue half of the wheel and held at low saturation so the
 * scatter stays inside the palette instead of turning into a rainbow. */
export const clusterColor = (rank) =>
  `hsl(${(192 + ((rank * 137.508) % 56)) | 0} ${48 + ((rank * 17) % 34)}% ${46 + ((rank * 29) % 28)}%)`;

/** "#rrggbb" -> 0xrrggbb, for three.js Color and material constructors. */
export const hex = (s) => parseInt(String(s).replace("#", ""), 16);

/** "#rrggbb" -> "rgba(r,g,b,a)", for canvas fills that need transparency. */
export function rgba(s, a) {
  const v = hex(s);
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
}
