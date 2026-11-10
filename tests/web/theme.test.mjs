/* The palette is written down twice, so this checks the two copies agree.
 *
 * js/theme.js holds the colours for canvas 2D and for WebGL shaders, which cannot read CSS; style.css
 * holds the same colours as custom properties for the DOM. Neither can be derived from the other at
 * run time without a layout read per frame, so they are kept in step by this test instead: change a
 * colour in one place and forget the other, and the suite fails rather than the page slowly drifting
 * into two palettes.
 */

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { C, GROUP_COLORS, SERIES, clusterColor, hex, rgba } from "../../web/js/theme.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const CSS = readFileSync(join(ROOT, "web", "css", "style.css"), "utf8");

/** Read a custom property's value from the :root block. */
function cssVar(name) {
  const m = CSS.match(new RegExp(`--${name}\\s*:\\s*([^;]+);`));
  return m ? m[1].trim() : null;
}

test("every palette token in theme.js matches the stylesheet", () => {
  const pairs = [
    ["bg", C.bg],
    ["panel", C.panel],
    ["panel2", C.panel2],
    ["panel3", C.panel3],
    ["line", C.line],
    ["line2", C.line2],
    ["text", C.text],
    ["muted", C.muted],
    ["faint", C.faint],
    ["ochre", C.ochre],
    ["sage", C.sage],
    ["clay", C.clay],
    ["rust", C.rust],
    ["sand", C.sand],
    ["slate", C.slate],
  ];
  for (const [name, value] of pairs) {
    const css = cssVar(name);
    assert.ok(css, `--${name} is missing from style.css`);
    assert.equal(css.toLowerCase(), value.toLowerCase(), `--${name}: CSS has ${css}, theme.js has ${value}`);
  }
});

test("the stylesheet loads Inter from the vendored file, not from a network font service", () => {
  assert.ok(/@font-face/.test(CSS), "no @font-face rule");
  assert.ok(/vendor\/inter\/inter-latin-variable\.woff2/.test(CSS), "Inter is not loaded from web/vendor");
  assert.ok(!/fonts\.googleapis|fonts\.gstatic|use\.typekit/.test(CSS), "a third-party font host crept in");
});

test("the layout class for the simulator cannot collide with the SIM badge", () => {
  // `.badge.sim` already existed; a layout rule written as `.sim { display: grid }` silently turned
  // every SIM badge into a page-width grid. The view uses `.simview` for that reason.
  assert.ok(!/^\.sim\s*\{/m.test(CSS), "a bare `.sim` rule is back, and it will hit .badge.sim");
  assert.ok(/\.simview\s*\{/.test(CSS), ".simview is missing");
});

test("group colours are defined for every catalog group and are distinct", () => {
  const groups = ["stations", "gps-ops", "geo", "active", "starlink", "debris"];
  const seen = new Set();
  for (const g of groups) {
    const c = GROUP_COLORS[g];
    assert.ok(/^#[0-9a-f]{6}$/i.test(c), `${g} has no usable colour (${c})`);
    assert.ok(!seen.has(c.toLowerCase()), `${g} reuses the colour ${c}`);
    seen.add(c.toLowerCase());
  }
});

test("series colours exist for every plotted quantity", () => {
  for (const k of ["alt", "speed", "q", "mass", "accel"]) {
    assert.ok(/^#[0-9a-f]{6}$/i.test(SERIES[k]), `series ${k} is not a hex colour`);
  }
});

test("hex and rgba convert the way the shaders and canvases expect", () => {
  assert.equal(hex("#efb455"), 0xefb455);
  assert.equal(hex("efb455"), 0xefb455);
  assert.equal(rgba("#efb455", 0.5), "rgba(239,180,85,0.5)");
});

test("cluster colours stay inside the earthy range and repeat for the same rank", () => {
  assert.equal(clusterColor(7), clusterColor(7));
  for (let i = 0; i < 30; i++) {
    const m = clusterColor(i).match(/hsl\(([\d.]+) (\d+)% (\d+)%\)/);
    assert.ok(m, `cluster colour ${i} is not an hsl() triple`);
    const sat = Number(m[2]);
    assert.ok(sat <= 55, `cluster ${i} is too saturated for the palette (${sat}%)`);
  }
});
