# MaxQ web (WP-03, frontend v0)

Static site, no build step. 2D equirectangular map (canvas), live satellites propagated with `satellite.js`
(SGP4) in a Web Worker, launch-window panel, ascent replay with scrubber, event markers and telemetry plots.

```bash
make web          # = python scripts/export_web_data.py, then python -m http.server 8765 --directory web
# or, without make:
python scripts/export_web_data.py
python -m http.server 8765 --directory web
```

* Data: `public/data/` (written by `scripts/export_web_data.py`; every JSON is contract-validated first).
  `manifest.json` lists the files and which are dummy. Files named `*_dummy.json` or with a `_note` starting with
  "dummy" are shown with a red DUMMY badge and a page banner.
* Real trajectories: any non-dummy `trajectory*.json` in `data/results/` is exported and listed first in the
  replay selector after you re-run the export.
* Missing files never crash the page: the affected panel shows a message and a banner.
* Vendored libs: see `vendor/NOTICE.md` (satellite.js 5.0.0, Natural Earth land).
* `catalog_web.json` is a deterministic per-group subsample (about 6.8k of 18.5k objects); `--all` exports everything.
