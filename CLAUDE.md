# Map generator: working notes

- Modules: `wurm_mapgen.py` (pipeline, PARAMS/STYLES, CLI), `shaping.py` (heightmap import/export,
  sketch, brush edits, roads/clearings), `playability.py` (report), `playermap.py` (printable map,
  bitmap font), `pngio.py` (PNG read/write without Pillow), `mapgen_ui.py` + `mapgen_ui.html` (UI),
  `viewer3d.py` + `viewer.html` (3D viewer: terrain chunks, DDS/WOM decoding of the client's
  `graphics.jar`, resolved through its `mappings.txt`; never copy client assets into this folder).
- `WurmMapGenerator.exe` (built by `build_app.py` from `mapgen_app.py`) bundles a snapshot of all the
  code and HTML: rebuild it after any change, or the user keeps running the old version. Paths go
  through `paths.py` (RES_DIR for bundled read-only files, TOOL_DIR for presets/imports, DIST_DIR for
  worlds); never derive locations from `__file__` in other modules. three.js is vendored in `vendor/`.
- With every new option at its default, output must stay byte-identical to before: draw random
  numbers in the same order and dtype, and add new randomness from a new `default_rng([seed, N])`.

- `README.md` is the end-user guide. Update it in the same change whenever a user-visible
  behaviour changes: settings (`PARAMS`: names, ranges, defaults, help), styles (`STYLES`), UI
  controls or cards, CLI flags, files written to the world folder, playability thresholds
  (`playability.RESOURCES`), or timings.
- The settings and styles tables in the README mirror `PARAMS` and `STYLES`; regenerate them from
  the code rather than editing by hand when those change.
- Write the README for server admins, not developers: what a control does and why you'd change
  it, not how it's implemented.
