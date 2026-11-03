# Wurm Map Generator

Make new maps for a Wurm Unlimited dedicated server. Choose a style (continent, archipelago,
fjords, volcanic island, …), tune it, preview it, and write a ready-to-run server world, complete
with rivers, lakes, forests, ore veins, kingdom spawn points and a playability report.

You can also sketch the rough layout yourself, brush changes onto the result, start from an
imported heightmap, connect the spawns with roads, and print a gridded map for your players.

There are three ways to use it:

- the **desktop app** (`WurmMapGenerator.exe`): the full UI in its own window
- the **browser UI** (`mapgen_ui.py`): the same UI in your web browser
- the **command line** (`wurm_mapgen.py`): the same generator, for scripting and repeat runs

All of them produce identical maps for the same settings and seed.

---

## Contents

1. [Getting it](#getting-it)
2. [Requirements](#requirements)
3. [Quick start](#quick-start)
4. [Using the browser UI](#using-the-browser-ui)
5. [Map styles](#map-styles)
6. [Settings reference](#settings-reference)
7. [Sketching and brush edits](#sketching-and-brush-edits)
8. [Imported heightmaps](#imported-heightmaps)
9. [Ores and caves](#ores-and-caves)
10. [The playability report](#the-playability-report)
11. [Presets and the seed gallery](#presets-and-the-seed-gallery)
12. [Player maps](#player-maps)
13. [Exploring in 3D](#exploring-in-3d)
14. [Command line](#command-line)
15. [What gets written to the world folder](#what-gets-written-to-the-world-folder)
16. [Map sizes and timings](#map-sizes-and-timings)
17. [The desktop app](#the-desktop-app)
18. [Troubleshooting](#troubleshooting)

---

## Getting it

Put the tool in a `tools\mapgen` folder inside your Wurm Unlimited Dedicated Server install
(`...\steamapps\common\Wurm Unlimited Dedicated Server`), so it finds your worlds by itself:

```bash
git clone https://github.com/vardoj/WurmUnlimited-Map-Generator.git tools/mapgen
```

The repository holds the source only. To get the double-click app, build it once (see
[The desktop app](#the-desktop-app)), or run the browser UI straight from source.

The tool contains no Wurm game files. The 3D viewer reads textures and models from your own
Wurm Unlimited client install while it runs.

---

## Requirements

**To use the app:** just Windows 10 or 11. `WurmMapGenerator.exe` contains everything it needs
(Python, numpy, the 3D library). It shows its pages with Windows' built-in Edge WebView2, which
Windows 11 includes and recent Windows 10 updates install.

**To run from source** (the command line, or the UI in a browser):
- **Python 3** (tested with 3.12)
- **numpy**: install it once with:

  ```bash
  python -m pip install --user numpy
  ```

All commands below are run from the server's install folder
(`...\steamapps\common\Wurm Unlimited Dedicated Server`).

---

## Quick start

1. Double-click **`tools\mapgen\WurmMapGenerator.exe`**. The map generator opens in its own window.
   The first start takes a few seconds while it unpacks.

   (Or, from source: `python tools/mapgen/mapgen_ui.py`. Your browser opens at
   <http://127.0.0.1:8765/>; keep the console window open while you use it.)

2. Click a **style** card. A preview appears after a couple of seconds.
3. Click **Shuffle** or **Gallery** until you like the shape, and adjust sliders if you want.
4. Under **Create world**, type a folder name such as `MyWorld` and click **Create world**.
5. When it finishes, select the new world in the Wurm Unlimited server launcher and start it.

The new world is written to `dist\MyWorld`, alongside the stock `Creative` and `Adventure` worlds.

---

## Using the browser UI

### Left sidebar

| Part | What it does |
| --- | --- |
| **Style** | Sixteen starting points (see [Map styles](#map-styles)). Picking one resets every slider to that style's values. *Surprise me* picks a random style and seed. A **modified** badge shows when you've changed sliders. |
| **Presets** | Save the current settings under a name and load them later. See [Presets](#presets-and-the-seed-gallery). |
| **Seed** | The random number that decides the map's shape. The same seed and settings always give the same map. *Shuffle* picks a new one; *Gallery* shows nine at once. |
| **Map size** | Size of the finished world: 1024, 2048 (the stock size), 4096 or 8192 tiles square. |
| **Preview detail** | *Fast (512)* or *Detailed (1024)*. The preview is a scaled-down version of the real map, so it matches the full-size result closely. |
| **Base terrain** | *Generated* (the default) or *Imported heightmap*: start from another world's map or a heightmap image instead. See [Imported heightmaps](#imported-heightmaps). |
| **Setting groups** | Shape, Water, Settlement, Ores, Vegetation, Climate, Surface and Terrain sliders. Hover a setting's name for a description. A changed setting is highlighted; **double-click a slider** to put it back to the style's value. |
| **Auto-preview / Reset to style** | Turn off auto-preview to change several things before regenerating. *Reset to style* undoes all slider changes. |

### Map area

- **Generate preview** re-runs the preview by hand (useful with auto-preview off).
- **Terrain / Height / Ores** switch between the surface map, a height map, and an underground
  view showing ore veins.
- **Spawn points** shows the three kingdom spawns: **J** (Jenn-Kellon), **M** (Mol-Rehan)
  and **L** (Horde of the Summoned). **Drag a marker** to move that spawn. The preview and the
  playability report update when you let go. A spawn dropped in water or on rock is nudged
  onto the nearest dry, walkable ground within 12 tiles. If there's none that close, it stays
  where you dropped it, and the report flags it. Undo (in Paint → Edit) or Ctrl+Z puts it back.
- **Hover** over the map to read the world coordinates, height in metres and tile type.
- **Zoom** with the mouse wheel (it zooms toward the cursor), the **+ / −** buttons in the map's
  corner, or a **double-click** (double-click again, or **Fit**, to see the whole map). Up to 16×.
  **Drag** the map to move around. While painting, drag with the middle mouse button, or hold
  **Space** and drag. Spawn markers, paint strokes and Playability highlights all follow the zoom.
  The preview's pixels show when zoomed in; choose *Detailed (1024)* preview detail for a sharper view.
- **Paint: Off / Sketch / Edit** turns the map into a canvas. See
  [Sketching and brush edits](#sketching-and-brush-edits).

### Side cards

The map grows to fill the window: it's the largest square that fits beside these cards, and it
resizes with the window. On small windows the cards move below the map.

- **Playability**: problems and warnings, and a table of how far each spawn is from key
  resources (see [The playability report](#the-playability-report)). **Hover** over an entry to
  see it on the map. For example, hovering *Jenn-Kellon spawn: Mineable rock 195 tiles away*
  draws a line from that spawn to the nearest rock.
- **Map summary**: land share, highest peak, deepest sea, number of rivers and lakes, and the
  mix of terrain types.
- **Spawn points**: coordinates of each kingdom's spawn.
- **Ores**: the share and tile count of each ore and special stone.

### Create world

| Field | Meaning |
| --- | --- |
| **World folder name** | Folder created under `dist`. Letters, digits, `-` and `_`, up to 40 characters. |
| **Copy databases from** | The world whose databases are copied into a new folder. *Creative* (the default) is a clean, empty world. *None* writes the map files only. |
| **Use folder name as map name** | Sets the server's map name to the folder name. |
| **Replace existing map** | Needed when the folder already has a map. The old map files are first moved into a `map-backup-<date>` folder inside the world. The world's databases (players, villages, items) are kept, so only do this on worlds you don't mind resetting. |
| **Keep template springs, fountains & Soulfall stones** | Off (the default): the 40 source springs/fountains and 5 Stones of Soulfall that come with the Creative template are removed. On: they're kept and moved onto suitable land on the new map. |

Progress shows in the log box. A full 2048 world takes about a minute.

### Player map & heightmap export

Below Create world. Pick any world that has a map, then **Download player map** (see
[Player maps](#player-maps)) or **Download heightmap**, a 16-bit PNG you can edit and import again.

---

## Map styles

| Style | Description |
| --- | --- |
| Continent | One large landmass with mountain ranges, rivers, forests and lakes. |
| Archipelago | Dozens of islands of all sizes scattered across a shallow sea. |
| Fjords | Cold, mountainous land cut by long, narrow sea inlets. |
| Twin continents | Two landmasses facing each other across a strait. |
| Inland sea | A ring of land enclosing a great central sea. |
| Highlands | High, rugged terrain with little lowland and deep valleys. |
| Supercontinent | Land fills most of the map, with great inland lakes and long rivers. |
| Peninsula | A mainland coast with a long arm of land reaching out to sea. |
| Crescent bay | A horseshoe of land wrapped around a vast, sheltered bay. |
| Long isle | One long, narrow island with a mountain spine down its length. |
| Volcanic island | A towering volcano with a crater lake, ringed by warm coastal forest. |
| Broken lands | Shattered land of large islands split by straits and sounds. |
| Lake district | Low, rolling, wooded country dotted with dozens of lakes. |
| Riverlands | Broad, flat lowlands crossed by wide, winding, navigable rivers. |
| Desert | Hot, dry land of sand seas and steppe, with rare oasis rivers. |
| Frozen north | Cold tundra and conifer forest under high, bare mountains. |

A style is just a set of slider values, so any style can be pushed toward another.

---

## Settings reference

Heights are in **decimetres** (dm), the game's own unit: 10 dm = 1 m. The defaults below are the
base values; each style changes some of them. On the command line, every setting is a flag with
dashes instead of underscores, for example `--river-width 20`.

### Shape

| Setting | Flag | Range | Default | What it does |
| --- | --- | --- | --- | --- |
| Land layout | `--shape` | radial, scattered, twin, ring, peninsula, crescent, band, volcano | radial | Overall arrangement of land: one central mass, land anywhere, two masses, a ring around an inner sea, mainland with a long arm, a horseshoe around a bay, one long island, or a central volcanic cone. |
| Land coverage | `--land` | 0.05–0.8 | 0.38 | Fraction of the map above water. |
| Landmass scale | `--scale` | 1–10 | 2 | Higher means more, smaller landmasses. |
| Coastline warp | `--warp` | 0–0.4 | 0.12 | Higher means wilder, more twisted coastlines. |
| Edge falloff | `--edge-falloff` | 0–2 | 1.0 | How strongly land is kept away from the map edges. |
| Fjords | `--fjords` | 0–1 | 0 | Carves narrow, deep sea inlets into coastal land. |

### Terrain

| Setting | Flag | Range | Default | What it does |
| --- | --- | --- | --- | --- |
| Highest peak | `--max-height` | 500–8000 dm | 3000 | Height of the tallest peaks. |
| Lowland relief | `--hill-height` | 0–2000 dm | 500 | Rolling-hill height away from mountains. |
| Mountain relief | `--mountain-height` | 0–5000 dm | 2600 | Height of ridged mountain ranges. |
| Mountain coverage | `--mountain-coverage` | −0.5–0.6 | 0 | How much of the land has mountain ranges. |
| Sea depth | `--sea-depth` | 100–2000 dm | 600 | Depth of the deepest sea. |
| Erosion passes | `--erosion` | 0–60 | 25 | Smooths overly steep slopes. More passes = softer terrain. |
| Erosion talus | `--talus` | 20–200 dm/tile | 70 | Slope above which erosion moves material downhill. |

### Surface

| Setting | Flag | Range | Default | What it does |
| --- | --- | --- | --- | --- |
| Exposed rock slope | `--rock-slope` | 20–150 dm | 60 | Tiles steeper than this show bare rock. |
| Cliff slope | `--cliff-slope` | 60–400 dm | 200 | Rock steeper than this becomes cliff. |

### Vegetation

| Setting | Flag | Range | Default | What it does |
| --- | --- | --- | --- | --- |
| Forest cover | `--forest` | 0–1 | 0.48 | How much of the land is forested. |
| Bushes | `--bushes` | 0–3 | 1 | Amount of berry and flower bushes. 1 = normal, 0 = none. |
| Flowers in grass | `--flowers` | 0–0.6 | 0.08 | Share of grass tiles with flowers. |
| Conifers vs broadleaf | `--conifers` | −1–1 | 0 | Positive turns broadleaf trees into pine, fir and cedar; negative does the reverse. |
| Fruit trees | `--fruit-trees` | 0–3 | 1 | Amount of apple, cherry, olive, lemon and orange trees. 1 = normal, 0 = none. |

### Climate

| Setting | Flag | Range | Default | What it does |
| --- | --- | --- | --- | --- |
| Temperature | `--temperature` | −0.5–0.5 | 0 | Colder gives more tundra and conifers; warmer gives fruit trees and steppe. |
| Moisture | `--moisture` | −0.5–0.5 | 0 | Drier gives steppe and desert; wetter gives marsh, peat and moss. |
| Tundra | `--tundra` | −1–1 | 0 | More or less tundra than the temperature alone gives. |
| Steppe | `--steppe` | −1–1 | 0 | More or less steppe than the moisture alone gives. |
| Marsh | `--marsh` | −1–1 | 0 | More or less marsh along wet shores; −1 = none. |
| Snow line | `--snow-line` | 0–8000 dm | 0 | Non-rock ground above this height becomes snow; 0 = no snow. The server has no special rules for snow, so it behaves like plain ground. |

### Settlement

| Setting | Flag | Range | Default | What it does |
| --- | --- | --- | --- | --- |
| Roads between spawns | `--roads` | none, dirt, gravel, cobblestone | none | Lays roads about 3 tiles wide that connect the kingdom spawns. Routes go around water, rock and steep ground, and the ground along them is evened out. Spawns separated by water get no road between them. |
| Spawn clearing | `--spawn-clearing` | 0–60 tiles | 0 | Clears trees and gently levels the ground this far around each spawn, so new players have room to build. |

### Water

Wurm has a single water level, so rivers and lakes are carved valleys that reach below sea
level. Boats can use the larger ones.

| Setting | Flag | Range | Default | What it does |
| --- | --- | --- | --- | --- |
| River density | `--rivers` | 0–1 | 0.5 | 0 = no rivers. Higher = more and longer rivers with more tributaries. |
| Max river width | `--river-width` | 4–40 tiles | 14 | Width of the largest rivers at their mouths. Small streams are 3 tiles. |
| Max river depth | `--river-depth` | 15–150 dm | 45 | Depth of the largest rivers. Lakes are 1.5× this. |
| Highest river source | `--river-max-height` | 100–3000 dm | 900 | Rivers and lakes only form below this height, which limits how deep valleys get. |
| Meander | `--meander` | 0–1 | 0.5 | How much rivers wind back and forth. |
| Valley side slope | `--bank-slope` | 4–40 dm/tile | 12 | Steepness of the valley walls around rivers and lakes. |
| Lakes | `--lakes` | 0–1 | 0.45 | How readily low-lying basins fill into lakes. |

### Ores

See [Ores and caves](#ores-and-caves).

| Setting | Flag | Range | Default | What it does |
| --- | --- | --- | --- | --- |
| Cave & ore layer | `--caves` | generator, server | generator | *generator*: place ore veins with the settings below. *server*: let the server scatter ore randomly on first start, like the stock maps. |
| Ore coverage | `--ore-amount` | 0.2–8 % | 2.0 | Share of the rock under dry land that is ore or special stone. 2% is about as much reachable ore as the stock Adventure map. |
| Vein size | `--vein-size` | 1–80 tiles | 14 | Average vein length. 1 scatters single tiles like the server does. |
| Ore under mountains | `--ore-mountains` | 0–1 | 0.6 | 0 = ore anywhere; 1 = ore only under high ground. |
| Ore per tile | `--ore-yield` | 0–9000 | 0 | Units of ore in each vein tile (varies ±50%). 0 = server default: a random 0–9999 rolled the first time the tile is mined. |
| Ore weights | `--ore-iron`, `--ore-copper`, `--ore-tin`, `--ore-zinc`, `--ore-lead`, `--ore-silver`, `--ore-gold`, `--ore-marble`, `--ore-slate`, `--ore-rocksalt`, `--ore-sandstone`, `--ore-adamantine`, `--ore-glimmersteel` | 0–60 each | iron 50; copper, tin, zinc, lead, silver 4; gold 2; marble, slate 1; others 0 | Relative share of each ore among the veins. Only the ratios matter. |

---

## Sketching and brush edits

Use the **Paint** buttons above the map to draw on it. The **Brush** slider sets the brush size
in tiles. Each finished stroke regenerates the preview (with Auto-preview on).

### Sketch: draw the layout, let the generator fill in the detail

Choose **Sketch**, then paint with:

| Brush | Effect |
| --- | --- |
| **Sea** | Forces water: cut bays, straits, inland seas or channels between islands. |
| **Land** | Forces land: join islands, add a peninsula, fill a bay. |
| **Mountains** | Land with mountain ranges. |
| **Lowland** | Land kept free of mountains. |
| **Erase** | Back to whatever the generator would make there. |

Unpainted areas follow the style and sliders as usual. A sketch is a rough guide: coastlines
still wiggle naturally, and Land coverage applies to the unpainted part of the map. The
sketch is drawn as coloured shading while Sketch mode is on. **Clear sketch** removes it.

### Edit: change the generated map

Choose **Edit**, pick a brush from the list, and paint:

- **Raise ground / Lower ground**: build up hills or dig down; *Strength* sets how much each pass changes. Lowering below sea level makes water.
- **Flatten**: levels the ground toward the height where the stroke started. Good for village sites.
- **Plant forest / Clear trees**: add or remove trees and bushes.
- **Grass, Clay, Sand, Tar, Gravel**: paint that ground type (rock and cliff are left alone).
- **Move … spawn**: click to put that kingdom's spawn point there. You can also just drag the
  J/M/L markers in any mode.

Height edits happen before rivers are carved, so rivers and lakes follow your new terrain.
Surface edits are applied after the generator has painted the ground. **Undo** (or Ctrl+Z)
removes the last stroke, and **Clear edits** removes them all.

Edits are stored in world-tile coordinates, so they land in the same places on the full-size
map as on the preview, and they're saved with presets. Changing the seed or style keeps your
sketch and edits but applies them to the new terrain.

---

## Imported heightmaps

Under **Base terrain**, choose **Imported heightmap**, then either:

- **From a world**: reuse the terrain of any world in `dist`, such as the stock Adventure map or
  one you made earlier, or
- **Upload a file**:
  - **PNG**: 8- or 16-bit greyscale, or colour (converted to brightness). Use 16-bit for smooth slopes.
  - **.r16 / .raw**: square 16-bit little-endian files, as exported by World Machine, Gaea and similar tools.
  - **.map**: a Wurm `top_layer.map` file.

The generator still adds erosion, rivers and lakes, ground cover, forests, ores and spawns. The
Shape sliders don't apply.

**Water level:** for ordinary images, **Water level in the image** picks the grey level that
becomes the shoreline. Brighter areas rise up to *Highest peak*; darker areas sink to *Sea depth*.
Heightmaps exported by this tool and Wurm `.map` files store exact heights, so they need no
water level and come back unchanged.

**Edit in an image editor and import again:**

1. Download the world's heightmap: the *Download heightmap* button, or `export-heightmap` on the
   command line.
2. Edit it in GIMP, Krita or Photoshop. Keep it 16-bit greyscale: lighter is higher, and each
   step of 1 is 1 dm.
3. Import it as above and create the world.

Set *Erosion passes* to 0 if you want your edits kept exactly. Lowering *River density* to 0
stops new rivers being carved into the imported terrain.

Uploads are stored in `tools/mapgen/imports/`. Images that aren't square are stretched to a
square map.

---

## Ores and caves

With **Cave & ore layer = generator** (the default), the generator writes the underground layer
itself:

- Ore comes in **veins** that wander like real seams, rather than single scattered tiles.
- Ore is only placed **under dry land**, never under the sea where it couldn't be mined.
- The default ore mix matches the server's own: mostly iron, then copper, tin, zinc, lead and
  silver, a little gold, and the occasional marble or slate.
- **Rock salt, sandstone, adamantine and glimmersteel** are off by default. A fresh server never
  places them: rock salt and sandstone only appear slowly over time, and adamantine and
  glimmersteel only come from god or GM actions. Raise their weights to have them from day one.
- The **Ores** view and the **Ores** card show exactly the veins the full map will get.

With **server**, no cave layer is written and the server scatters ore randomly the first time it
starts, as on the stock maps.

Generated veins stay as written. The only exception is if the server's `REPROSPECT` option is
turned on (it's off by default), which makes the server re-roll all ores.

---

## The playability report

Every preview and every created world gets a report. In the UI it's the **Playability** card;
in the world folder it's `map-report.txt` (readable) and `map-report.json` (for tools).

Each line is marked ✓ good, *i* info, **!** warning or **×** problem. The report covers:

- **Each kingdom spawn**: distance to the basics a new player needs, and how much flat land is
  within 40 tiles for a first village. It also flags a spawn standing in water, on bare rock, or
  on steep ground (possible after moving one by hand). Distances are flagged when they pass these limits:

  | Resource | Good within | Acceptable within |
  | --- | --- | --- |
  | Water | 40 tiles | 100 tiles |
  | Trees | 40 | 100 |
  | Clay | 150 | 400 |
  | Tar | 250 | 600 |
  | Sand | 120 | 350 |
  | Mineable rock | 120 | 350 |
  | Peat, moss | 400 | 900 (optional) |
  | Iron ore | 150 | 400 |
  | Copper ore, tin ore | 300 | 700 |

- **Building land**: the share of land flat enough to build on as-is, and after light flattening.
- **Islands**: how many islands (and how much land) can only be reached by boat, and whether the
  kingdoms spawn on different landmasses.
- **Spawn spacing**: how far apart the kingdom spawns are.
- **Missing resources**: no clay, tar, peat or moss anywhere; no iron; no tin or copper (so no
  bronze).

**Seeing it on the map:** in the UI, hover over any part of the Playability card to draw it on the preview:

| Hover over | Shows |
| --- | --- |
| A spawn's check | Lines from that spawn to each resource that's too far away, plus a circle if it lacks flat land |
| A distance in the table | A line from that spawn to the nearest tile of that resource |
| A spawn's column heading | Lines to every resource from that spawn |
| A resource's row name | Lines from all three spawns to their nearest tile of it |
| *Flat land nearby* | The 40-tile circle the flat-land share is measured in |
| *Islands reachable only by boat* | A ring around each of those islands |
| *Spawns are … apart* / *different landmasses* | Lines between the spawns |

`map-report.json` includes the same nearest-resource coordinates for each spawn.

Spawn points are chosen automatically. The generator prefers flat grassland with water, trees,
clay, tar, sand and rock nearby, room for a village, and the three kingdoms well spread out.
If the report flags a spawn, try another seed (the gallery shows problem counts for each one).

---

## Presets and the seed gallery

### Presets

- **Save**: type a name in the sidebar's Presets section and click **Save current**. Tick
  **Include seed & map size** to save that exact map. Leave it unticked to save just the
  settings, so you can use them with any seed.
- **Load**: pick a preset from the list and click **Load**. **Delete** removes it.
- A preset stores its style plus only the settings you changed. If a style is later improved, presets based on it pick up the improvements.
- A preset also keeps your sketch, brush edits and imported heightmap, if any.
- Presets are small JSON files in `tools/mapgen/presets/`. Copy them to share with other people.
- Use one from the command line with `--preset "Name"`.

### Seed gallery

Click **Gallery** (next to Shuffle) to see nine random seeds rendered with your current settings.
They fill in one by one, taking about 10–15 seconds for all nine. Each shows its land share, number of rivers,
and the playability problem and warning counts. **Click one** to use its seed, click
**New seeds** for another nine, or press **Esc** to close.

To **compare two**, click **Compare** on two thumbnails. They're shown side by side, larger,
with their numbers. Pick one with **Use this seed**, or click **Back to all**.

---

## Player maps

A clean, printable map of a created world to post on your forum or Discord. Each map has:

- the terrain, with lettered columns and numbered rows (e.g. *C4*)
- the kingdom spawn points
- a title and a scale bar, with north at the top

In the UI, use **Player map & heightmap export** below Create world. Choose the image size,
grid size (or none), title, and whether to show spawns. From the command line:

```bash
python tools/mapgen/wurm_mapgen.py player-map dist/MyWorld --size 4096 --grid 256 --title "My World"
```

This works for any world, including the stock ones.

---

## Exploring in 3D

Walk or fly around a map at true scale (1 tile = 4 m), with the game's own textures and trees,
to check the landscape before anyone plays on it.

**Opening it:**
- **Explore in 3D** next to *Generate preview* opens the current preview. **Shift+click** on the
  map to start at that spot.
- **Explore in 3D** in the *Player map & heightmap export* card, or after creating a world, opens
  a created world at full detail.
- Inside the viewer, the menu at the top right switches between the preview and any world in `dist`.

A preview is coarse: at *Fast (512)* on a 2048 map each point stands for 4×4 tiles, so the ground
looks blocky and there's one tree per 4×4 tiles. Create the world (or use *Detailed* preview
detail) to see it exactly as the server will have it.

**Controls:**

| Key | Action |
| --- | --- |
| Click | Capture the mouse to look around (**Esc** frees it) |
| W A S D / arrows | Move |
| Shift | Run when walking; 4× speed when flying |
| Space | Jump when walking; climb when flying |
| C | Descend when flying |
| F | Switch between walking (character eye height, follows the ground, swims in water) and flying |
| Mouse wheel | Flying speed |
| M | Hide or show the panels |

**Panels:**
- **Readout** (top left): the tile you're on, ground height, tile type and slope (*steep* is
  flagged), plus mode and frame rate.
- **Go to** (top right):
  - jump to the J, M or L spawn, the map centre, or typed tile coordinates
  - click the minimap to teleport
  - spawns are also marked in the world by coloured light pillars
- **View sliders:** view distance, how far trees are drawn, and how far full tree models are used.
  Beyond that, trees are flat images of the same models, which keeps big forests fast.

**Game textures and models** come straight from your Wurm Unlimited client install
(`steamapps/common/Wurm Unlimited/WurmLauncher/packs/graphics.jar`). They're read while the viewer
runs and are never copied into this tool:
- every ground type with the client's own texture
- trees and bushes using the client's models, sized by age, at the client's own positions in each tile
- the client's water texture

Without the client, or with *Game textures & models* unticked, the viewer uses plain colours and
simple tree shapes. If the client is installed somewhere else, set the environment variable
`WURM_CLIENT_GRAPHICS` to the full path of its `graphics.jar` before starting the UI.

**What it can't show:** the real client's lighting, water shader, grass blades and exact tile-edge
blending. Shape, ground types, forests, water and spawns all come from the map files themselves.
For a pixel-exact check, log in with the game client.

The viewer works offline: its three.js 3D library is bundled in `tools/mapgen/vendor/`.

---

## Command line

### Create a world

```bash
python tools/mapgen/wurm_mapgen.py generate dist/MyWorld --style archipelago --seed 12345
```

Useful options:

| Option | Meaning |
| --- | --- |
| `--style NAME` | One of: continent, archipelago, fjords, twin, inland_sea, highlands, supercontinent, peninsula, crescent, long_isle, volcanic, broken_lands, lake_district, riverlands, desert, frozen_north. Default: the preset's style, else continent. |
| `--preset NAME` | Start from a saved preset (including its sketch, edits and heightmap). Any other flags override it. |
| `--heightmap FILE` | Start from a heightmap instead of generating one: PNG, `.r16`/`.raw`, or a world's `top_layer.map`. |
| `--heightmap-sea-level F` | Grey level (0–1) that becomes the shoreline in an imported image (default 0.3). |
| `--size N` | Map width in tiles: 1024, 2048 (default), 4096 or 8192. |
| `--seed N` | Random seed. Default: the preset's seed, else based on the time. |
| `--<setting> VALUE` | Any setting from the [reference](#settings-reference), e.g. `--land 0.3 --rivers 0.8`. |
| `--template DIR` | World to copy databases from when the folder is new (default `dist/Creative`; `''` for map files only). |
| `--name NAME` | Set the server's map name. |
| `--force` | Replace the map in an existing world (the old map files are backed up first). |
| `--template-items` | Keep the template's springs, fountains and Stones of Soulfall, moved onto the new land. |
| `--no-preview` | Don't write `map-preview.png`. |

Run `python tools/mapgen/wurm_mapgen.py generate -h` for the full list.

More examples:

```bash
python tools/mapgen/wurm_mapgen.py generate dist/Isles --preset "My islands"
```

```bash
python tools/mapgen/wurm_mapgen.py generate dist/BigVolcano --style volcanic --size 4096 --ore-yield 2000 --name BigVolcano
```

```bash
python tools/mapgen/wurm_mapgen.py generate dist/Remix --heightmap dist/Adventure/top_layer.map --roads gravel --spawn-clearing 25
```

### Render a map image

Make a PNG of any world's map, including the stock ones:

```bash
python tools/mapgen/wurm_mapgen.py render dist/Adventure --out adventure.png
```

`--mode height` draws a height map instead, `--mode ores` draws the ore veins from the world's
cave layer, and `--scale N` shrinks the image by N.

### Export a heightmap

```bash
python tools/mapgen/wurm_mapgen.py export-heightmap dist/MyWorld --out myworld-heights.png
```

This writes a 16-bit PNG with exact heights. Edit it and bring it back with `--heightmap`.

### Make a player map

```bash
python tools/mapgen/wurm_mapgen.py player-map dist/MyWorld
```

Options: `--size` (pixels, default 2048), `--grid` (tiles per cell, default 256; 0 for none),
`--title`, `--no-spawns`, `--out`.

### UI options

```bash
python tools/mapgen/mapgen_ui.py --port 9000 --no-browser
```

The UI only listens on your own computer (127.0.0.1), so other machines can't reach it.

---

## What gets written to the world folder

| File | Contents |
| --- | --- |
| `top_layer.map` | Surface: terrain type, trees, grass and height of every tile. |
| `rock_layer.map` | The rock surface under the dirt. |
| `map_cave.map` | Underground rock and ore veins (only with *Cave & ore layer = generator*). |
| `resources.map` | Fixed ore amounts (only when *Ore per tile* is above 0). |
| `sqlite\*.db` | Copied from the template. Spawn points (and optionally the map name) are set to match the new map. |
| `map-preview.png` | An image of the finished map. |
| `map-report.txt`, `map-report.json` | The playability report. |
| `heightmap.png`, `player-map.png` | Only if you run `export-heightmap` or `player-map` without `--out`. |

Any of `map_cave.map`, `resources.map`, `flags.map` and `protectedTiles.bmap` that are not written
are created by the server on first start.

**Template clean-up.** The Creative template's databases contain a few things placed for
the Creative map: 40 source springs and fountains, 5 Stones of Soulfall, and 6 named areas
(The Meadow, West Lake, …). These would end up underwater or on cliffs on a new map, so:

- The named areas are always removed.
- The springs, fountains and stones are removed. If you choose to keep them (the UI checkbox, or
  `--template-items`), they're moved onto suitable land instead.

If you replace the map of a world that already has villages or buildings, the log warns you,
because those stay where they were on the old map.

**Restoring a replaced map:** move the files from the world's `map-backup-<date>` folder back
into the world folder.

---

## Map sizes and timings

Measured on a desktop PC; expect similar on other recent machines:

| Size | Time to create | Memory needed |
| --- | --- | --- |
| Preview (512) | 2–3 s | small |
| Gallery (9 seeds) | 10–15 s | small |
| 1024 | ~15 s | ~0.2 GB |
| 2048 | ~45 s | ~0.5 GB |
| 4096 | ~3 min | ~2 GB |
| 8192 | ~11 min | ~7 GB |

Roads add a few seconds. An imported heightmap skips terrain generation, so it's faster.

A preview and a full map with the same seed and settings have the same shape. Small details,
such as the exact path of minor streams, can differ slightly.

---

## The desktop app

`WurmMapGenerator.exe` is the same tool as the browser UI, in its own window:
- **New windows:** the 3D viewer, and anything else that would open a new tab, open as separate app windows.
- **Downloads:** player maps and heightmaps go to your Downloads folder.
- **Closing:** if a world is still being created, the app asks before quitting, so its map files aren't left half-written.
- **Network:** it runs a private copy of the UI's web server on a free port on your own PC (127.0.0.1), so it never clashes with a browser copy that's already running.

**Where it looks for your worlds:** inside the server folder, it finds everything automatically.
If you copy the `.exe` somewhere else (or give it to another server admin), it asks once for the
*Wurm Unlimited Dedicated Server* folder and remembers it in
`%APPDATA%\WurmMapGenerator\settings.json`. Presets and uploaded heightmaps are kept in
`presets\` and `imports\` next to the `.exe`.

**Log file:** `%LOCALAPPDATA%\WurmMapGenerator\app.log`. Look there if the app closes or shows an error.

**Rebuilding it** (after changing the tool's source files):

```bash
python -m pip install --user pyinstaller pywebview
```

```bash
python tools/mapgen/build_app.py
```

This writes a new `tools\mapgen\WurmMapGenerator.exe` (about 24 MB).

---

## Troubleshooting

**The app won't start, or Windows warns about it**: Windows SmartScreen and some antivirus tools are
wary of new, unsigned `.exe` files. Choose *More info → Run anyway* if you trust where it came from.
If it starts and closes again, check `%LOCALAPPDATA%\WurmMapGenerator\app.log`. If the window stays
blank, install the *Microsoft Edge WebView2 Runtime* from Microsoft's website.

**`ModuleNotFoundError: No module named 'numpy'`**: install it with
`python -m pip install --user numpy`, using the same `python` you run the tool with.

**"Could not reach the generator server"** in the browser: the console window running
`mapgen_ui.py` was closed or crashed. Start it again.

**Port 8765 is already in use**: another copy is probably still running. Close it, or start
with `--port 8766`.

**"World 'X' already has a map"**: choose a new folder name, or tick *Replace existing map*
(`--force` on the command line).

**The ores I mine don't match the Ores view**: check that *Cave & ore layer* was set to
*generator* when the world was created, and that the server's `REPROSPECT` option is off.

**"Unsupported PNG" when uploading a heightmap**: palette-based (indexed colour) and
interlaced PNGs aren't supported. Re-save it as greyscale (ideally 16-bit) with interlacing off.

**No road between two spawns**: the spawns are on different landmasses. Roads never cross water.
Move a spawn with the Edit brush, or connect the land with a Sketch *Land* stroke.

**My edits moved after changing a slider**: edits stay in the same map coordinates, but the
terrain under them changes with the settings. Make edits last, once the rest is settled.

**The 3D viewer shows only sky, or "Loading" never finishes**: the viewer pauses while its browser
tab is hidden or minimised. Bring the tab (or the app's viewer window) to the front.

**The 3D viewer says the Wurm client wasn't found**: install Wurm Unlimited from Steam, or set
`WURM_CLIENT_GRAPHICS` to its `graphics.jar` and restart the UI.

**A spawn is far from clay or tar**: try other seeds with the gallery; the problem counts
under each thumbnail show which seeds have well-placed spawns. Clay forms near coasts and
lakes, so styles with more shoreline (archipelago, lake district) tend to have it close by.
