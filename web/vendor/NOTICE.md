# Vendored third-party files (pinned)

| File | Source | Version | License |
|---|---|---|---|
| `satellite.min.js` | npm `satellite.js` (`dist/satellite.min.js`, UMD build; global `satellite`) | 5.0.0 | MIT (per package.json) |
| `../public/data/land.json` | Natural Earth 110m land via npm `world-atlas` (decoded with `topojson-client@3.1.0`, coordinates rounded to 0.1 deg) | world-atlas 2.0.2 | Natural Earth: public domain; world-atlas: ISC |
| `three/three.module.js`, `three/three.core.js` | npm `three` (`build/`, ES-module build; `three.module.js` imports `three.core.js`, so both are required) | 0.187.0 | MIT (`three/LICENSE`) |
| `inter/inter-latin-variable.woff2` | npm `@fontsource-variable/inter` (`files/inter-latin-wght-normal.woff2`, latin subset, variable weight 100-900) | 5.3.0 | SIL Open Font License 1.1 (`inter/LICENSE`) |

## Earth textures (`../public/textures/`)

| File | Pixels | Underlying source | Obtained from | License |
|---|---|---|---|---|
| `earth_day.jpg` | 4096x2048 | NASA Visible Earth, **Blue Marble Next Generation** (true-colour land surface, topography and bathymetry) | npm `three-globe` 2.45.6, `example/img/earth-blue-marble.jpg` | NASA imagery: public domain. The package that carried it: MIT. |
| `earth_night.jpg` | 4096x2048 | NASA Earth Observatory, **night lights** ("Black Marble" family) | npm `three-globe` 2.45.6, `example/img/earth-night.jpg` | as above |
| `earth_topo.png` | 2048x1024 | NASA-derived elevation/bathymetry grey map, used as a bump map only | npm `three-globe` 2.45.6, `example/img/earth-topology.png` | as above |
| `earth_water.png` | 1600x800 | land/water mask, used to confine specular reflection to the oceans | npm `three-globe` 2.45.6, `example/img/earth-water.png` | as above |

Provenance note, stated plainly because it matters for attribution: these four images are NASA
imagery (public domain), but they were **not** downloaded from a NASA server. `eoimages.gsfc.nasa.gov`
is not reachable from this build environment, so they were taken from the example assets of the
`three-globe` npm package, which redistributes them. They are the standard NASA Blue Marble /
night-lights equirectangular maps, resampled by that package to the sizes above; the exact NASA
product revision and resampling steps are therefore not pinned by us. If an exact NASA product
version is ever required (for a publication, say), re-fetch from NASA Visible Earth directly.

Nothing else in the 3D view is a downloaded asset: Earth's atmosphere and the star field are
generated in shaders, and the rockets, satellites, stations and debris are built from primitives in
`web/js/sim3d/`. They are schematic by construction and labelled as such in the UI.

Added 2026-11-10 for WP-03 (Yellow: new pinned dependency, recorded in `docs/decisions.md`);
three.js, Inter and the Earth textures added the same day for the 3D simulator (WP-17).
