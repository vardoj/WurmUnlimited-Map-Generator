#!/usr/bin/env python3
"""Wurm Unlimited map generator.

Writes top_layer.map and rock_layer.map for a Wurm Unlimited dedicated server
world. The server rebuilds map_cave.map (including ore veins), resources.map,
flags.map and protectedTiles.bmap on its own when they are missing, so this tool
removes them from the target world.

File format (reverse-engineered from com.wurmonline.mesh.MeshIO / Tiles):
  header : 1024 bytes = big-endian int64 magic 5136955264682433437,
           byte version (0), byte sizeLevel, zero padding
  body   : size*size big-endian int32, row-major (index = y*size + x)
  tile   : type << 24 | data << 16 | (height & 0xFFFF)
           height = signed int16 in decimetres at the tile's north-west corner

Usage:
  python wurm_mapgen.py generate ../../dist/MyWorld --style fjords --size 2048 --seed 42
  python wurm_mapgen.py render ../../dist/MyWorld --out preview.png
  python mapgen_ui.py          (browser UI with live preview)

Optionally also writes map_cave.map (ore veins) and resources.map (fixed ore yields), plus
a playability report (map-report.txt / .json) into the world folder.

Requires numpy.
"""

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np

import paths
import playability
import playermap
import pngio
import shaping

MAGIC = 5136955264682433437
HEADER_SIZE = 1024

# Tile type ids (com.wurmonline.mesh.Tiles$Tile)
SAND, GRASS, ROCK, DIRT, CLAY, DIRT_PACKED, COBBLESTONE = 1, 2, 4, 5, 6, 8, 9
GRAVEL, PEAT, TUNDRA, MOSS, CLIFF, STEPPE, MARSH, TAR = 17, 18, 19, 20, 21, 22, 23, 24
SNOW, KELP, REED = 30, 32, 33

BIRCH, PINE, OAK, CEDAR, WILLOW, MAPLE, APPLE, LEMON = 100, 101, 102, 103, 104, 105, 106, 107
OLIVE, CHERRY, CHESTNUT, WALNUT, FIR, LINDEN, ORANGE = 108, 109, 110, 111, 112, 113, -93

LAVENDER, ROSE, THORN, GRAPE, CAMELLIA, OLEANDER = -114, -113, -112, -111, -110, -109
HAZELNUT, RASPBERRY, BLUEBERRY, LINGONBERRY = -96, -90, -87, -84

TYPE_NAMES = {
    SAND: "Sand", GRASS: "Grass", ROCK: "Rock", DIRT: "Dirt", CLAY: "Clay", GRAVEL: "Gravel",
    PEAT: "Peat", TUNDRA: "Tundra", MOSS: "Moss", CLIFF: "Cliff", STEPPE: "Steppe", MARSH: "Marsh",
    TAR: "Tar", KELP: "Kelp", REED: "Reed", DIRT_PACKED: "Packed dirt road", COBBLESTONE: "Cobblestone road",
    SNOW: "Snow",
    BIRCH: "Birch tree", PINE: "Pine tree", OAK: "Oak tree", CEDAR: "Cedar tree", WILLOW: "Willow tree",
    MAPLE: "Maple tree", APPLE: "Apple tree", LEMON: "Lemon tree", OLIVE: "Olive tree",
    CHERRY: "Cherry tree", CHESTNUT: "Chestnut tree", WALNUT: "Walnut tree", FIR: "Fir tree",
    LINDEN: "Linden tree", ORANGE: "Orange tree",
    LAVENDER: "Lavender bush", ROSE: "Rose bush", THORN: "Thorn bush", GRAPE: "Grape bush",
    CAMELLIA: "Camellia bush", OLEANDER: "Oleander bush", HAZELNUT: "Hazelnut bush",
    RASPBERRY: "Raspberry bush", BLUEBERRY: "Blueberry bush", LINGONBERRY: "Lingonberry bush",
}

# Species pools by climate band: cold, temperate, warm.
TREES = [
    [PINE, PINE, FIR, FIR, BIRCH, LINDEN],
    [OAK, OAK, BIRCH, MAPLE, LINDEN, CHESTNUT, WALNUT, CEDAR, PINE, APPLE],
    [OLIVE, LEMON, ORANGE, CHERRY, APPLE, CEDAR, OAK],
]
BUSHES = [
    [LINGONBERRY, BLUEBERRY, THORN],
    [ROSE, HAZELNUT, RASPBERRY, THORN, LAVENDER, BLUEBERRY],
    [GRAPE, CAMELLIA, OLEANDER, LAVENDER, ROSE],
]

# Cave layer tiles. The server's own first-start generator writes encode(-100, wall-or-ore, 0)
# for every tile (Server.loadCaveMesh / TileRockBehaviour.prospect); we do the same.
CAVE_WALL = -54
CAVE_FLOOR_HEIGHT = -100
# (key, label, tile id, default weight, preview colour). Default weights mirror the server's
# own mix (TileRockBehaviour.getOreId): iron 50, gold 2, silver/copper/zinc/lead/tin 4,
# marble/slate 1. Rock salt and sandstone otherwise only appear slowly over time
# (TilePoller), and adamantine/glimmersteel only through god or GM actions.
ORE_DEFS = [
    ("iron", "Iron", -34, 50, (190, 95, 60)),
    ("copper", "Copper", -33, 4, (60, 200, 160)),
    ("tin", "Tin", -30, 4, (215, 215, 225)),
    ("zinc", "Zinc", -31, 4, (120, 150, 200)),
    ("lead", "Lead", -32, 4, (130, 120, 170)),
    ("silver", "Silver", -35, 4, (240, 240, 250)),
    ("gold", "Gold", -36, 2, (250, 205, 40)),
    ("marble", "Marble", -50, 1, (245, 235, 215)),
    ("slate", "Slate", -51, 1, (90, 100, 110)),
    ("rocksalt", "Rock salt", -27, 0, (250, 170, 200)),
    ("sandstone", "Sandstone", -26, 0, (225, 175, 105)),
    ("adamantine", "Adamantine", -29, 0, (40, 220, 240)),
    ("glimmersteel", "Glimmersteel", -28, 0, (210, 120, 255)),
]
ORE_BY_ID = {d[2]: d for d in ORE_DEFS}

SHAPES = ("radial", "scattered", "twin", "ring", "peninsula", "crescent", "band", "volcano")

# Tunable settings, shared by the CLI and the UI.
PARAMS = [
    dict(name="shape", label="Land layout", group="Shape", kind="choice", choices=SHAPES, default="radial",
         help="radial: one central landmass; scattered: land anywhere; twin: two landmasses; ring: land around "
              "an inner sea; peninsula: mainland with a long arm; crescent: land around an open bay; "
              "band: one long island; volcano: a central volcanic cone"),
    dict(name="land", label="Land coverage", group="Shape", min=0.05, max=0.8, step=0.01, default=0.38,
         help="fraction of the map above water"),
    dict(name="scale", label="Landmass scale", group="Shape", min=1, max=10, step=0.1, default=2,
         help="noise cells across the map; higher = more, smaller landmasses"),
    dict(name="warp", label="Coastline warp", group="Shape", min=0, max=0.4, step=0.01, default=0.12,
         help="domain warp strength; higher = wilder coastlines"),
    dict(name="edge_falloff", label="Edge falloff", group="Shape", min=0, max=2, step=0.05, default=1.0,
         help="how hard the layout pushes land away from the edges"),
    dict(name="fjords", label="Fjords", group="Shape", min=0, max=1, step=0.05, default=0.0,
         help="carve narrow, deep sea inlets into coastal land"),
    dict(name="max_height", label="Highest peak (dm)", group="Terrain", min=500, max=8000, step=50, default=3000,
         help="height of the tallest peaks in decimetres"),
    dict(name="hill_height", label="Lowland relief (dm)", group="Terrain", min=0, max=2000, step=25, default=500,
         help="rolling-hill height away from mountains"),
    dict(name="mountain_height", label="Mountain relief (dm)", group="Terrain", min=0, max=5000, step=50, default=2600,
         help="height of ridged mountain ranges"),
    dict(name="mountain_coverage", label="Mountain coverage", group="Terrain", min=-0.5, max=0.6, step=0.01, default=0.0,
         help="how much of the land has mountain ranges"),
    dict(name="sea_depth", label="Sea depth (dm)", group="Terrain", min=100, max=2000, step=25, default=600,
         help="deepest sea in decimetres"),
    dict(name="erosion", label="Erosion passes", group="Terrain", kind="int", min=0, max=60, step=1, default=25,
         help="thermal erosion passes; smooths overly steep slopes"),
    dict(name="talus", label="Erosion talus (dm/tile)", group="Terrain", min=20, max=200, step=5, default=70,
         help="slope above which erosion moves material downhill"),
    dict(name="rock_slope", label="Exposed rock slope", group="Surface", min=20, max=150, step=1, default=60,
         help="tile slope (dm) above which bare rock shows"),
    dict(name="cliff_slope", label="Cliff slope", group="Surface", min=60, max=400, step=5, default=200,
         help="tile slope (dm) above which rock becomes cliff"),
    dict(name="forest", label="Forest cover", group="Vegetation", min=0, max=1, step=0.01, default=0.48,
         help="how much of the land is forested"),
    dict(name="bushes", label="Bushes", group="Vegetation", min=0, max=3, step=0.05, default=1.0,
         help="amount of berry and flower bushes; 1 = normal, 0 = none"),
    dict(name="flowers", label="Flowers in grass", group="Vegetation", min=0, max=0.6, step=0.01, default=0.08,
         help="share of grass tiles with flowers"),
    dict(name="conifers", label="Conifers vs broadleaf", group="Vegetation", min=-1, max=1, step=0.05,
         default=0.0, help="positive = more pine, fir and cedar; negative = more oak, birch, maple and linden"),
    dict(name="fruit_trees", label="Fruit trees", group="Vegetation", min=0, max=3, step=0.05, default=1.0,
         help="amount of apple, cherry, olive, lemon and orange trees; 1 = normal, 0 = none"),
    dict(name="temperature", label="Temperature", group="Climate", min=-0.5, max=0.5, step=0.01, default=0.0,
         help="colder = more tundra and conifers, warmer = fruit trees and steppe"),
    dict(name="moisture", label="Moisture", group="Climate", min=-0.5, max=0.5, step=0.01, default=0.0,
         help="drier = steppe and desert, wetter = marsh, peat and moss"),
    dict(name="tundra", label="Tundra", group="Climate", min=-1, max=1, step=0.05, default=0.0,
         help="more or less tundra than the temperature alone gives"),
    dict(name="steppe", label="Steppe", group="Climate", min=-1, max=1, step=0.05, default=0.0,
         help="more or less steppe than the moisture alone gives"),
    dict(name="marsh", label="Marsh", group="Climate", min=-1, max=1, step=0.05, default=0.0,
         help="more or less marsh along wet shores; -1 = none"),
    dict(name="snow_line", label="Snow line (dm)", group="Climate", kind="int", min=0, max=8000, step=50,
         default=0, help="non-rock ground above this height becomes snow; 0 = no snow. The server treats snow "
                         "as plain ground with no special behaviour"),
    dict(name="roads", label="Roads between spawns", group="Settlement", kind="choice",
         choices=("none", "dirt", "gravel", "cobblestone"), default="none",
         help="lay roads connecting the kingdom spawns, routed around water, rock and steep ground"),
    dict(name="spawn_clearing", label="Spawn clearing (tiles)", group="Settlement", kind="int", min=0, max=60,
         step=1, default=0, help="clear trees and level the ground this far around each spawn; 0 = off"),
    dict(name="rivers", label="River density", group="Water", min=0, max=1, step=0.01, default=0.5,
         help="0 = no rivers; higher = more and longer rivers with more tributaries"),
    dict(name="river_width", label="Max river width (tiles)", group="Water", min=4, max=40, step=1, default=14,
         help="width of the largest rivers at their mouths; small streams are 3 tiles"),
    dict(name="river_depth", label="Max river depth (dm)", group="Water", min=15, max=150, step=5, default=45,
         help="depth below water level of the largest rivers; lakes are 1.5x this"),
    dict(name="river_max_height", label="Highest river source (dm)", group="Water", min=100, max=3000, step=25,
         default=900, help="rivers and lakes only form below this height, which limits how deep valleys get"),
    dict(name="meander", label="Meander", group="Water", min=0, max=1, step=0.01, default=0.5,
         help="how much rivers wind back and forth"),
    dict(name="bank_slope", label="Valley side slope (dm/tile)", group="Water", min=4, max=40, step=1, default=12,
         help="steepness of the valley walls carved around rivers and lakes"),
    dict(name="lakes", label="Lakes", group="Water", min=0, max=1, step=0.01, default=0.45,
         help="how readily low-lying basins fill into lakes"),
    dict(name="caves", label="Cave & ore layer", group="Ores", kind="choice", choices=("generator", "server"),
         default="generator",
         help="generator: place ore veins with the settings below; server: let the server scatter ores itself "
              "on first start (random, like the stock maps)"),
    dict(name="ore_amount", label="Ore coverage (%)", group="Ores", min=0.2, max=8, step=0.1, default=2.0,
         help="share of tiles under dry land that are ore or special stone; 2% gives about as much reachable "
              "ore as the stock Adventure map"),
    dict(name="vein_size", label="Vein size (tiles)", group="Ores", kind="int", min=1, max=80, step=1, default=14,
         help="average length of a vein; 1 scatters single tiles like the server does"),
    dict(name="ore_mountains", label="Ore under mountains", group="Ores", min=0, max=1, step=0.01, default=0.6,
         help="0 = ore anywhere; 1 = ore only under high ground"),
    dict(name="ore_yield", label="Ore per tile", group="Ores", kind="int", min=0, max=9000, step=50, default=0,
         help="units of ore each vein tile holds (varies +-50%); 0 = server default (random 0-9999 on first mining)"),
] + [
    dict(name="ore_" + key, label=f"{label} weight", group="Ores", kind="int", min=0, max=60, step=1,
         default=weight, help=f"relative share of {label.lower()} among veins")
    for key, label, _, weight, _ in ORE_DEFS
]
PARAM_BY_NAME = {p["name"]: p for p in PARAMS}

STYLES = {
    "continent": dict(label="Continent", desc="One large landmass with mountain ranges, rivers, forests and lakes.", values={}),
    "archipelago": dict(label="Archipelago", desc="Dozens of islands of all sizes scattered across a shallow sea.",
                        values=dict(shape="scattered", land=0.24, scale=6.5, warp=0.16, edge_falloff=1.0,
                                    max_height=1800, hill_height=350, mountain_height=1400,
                                    mountain_coverage=-0.1, sea_depth=450, temperature=0.12,
                                    rivers=0.7, river_width=9, lakes=0.3)),
    "fjords": dict(label="Fjords", desc="Cold, mountainous land cut by long, narrow sea inlets.",
                   values=dict(shape="radial", land=0.5, scale=2.4, warp=0.1, edge_falloff=0.9, fjords=0.85,
                               max_height=3800, hill_height=700, mountain_height=3400,
                               mountain_coverage=0.35, sea_depth=900, rock_slope=55, temperature=-0.2,
                               moisture=0.1, forest=0.55, rivers=0.4, river_max_height=1600,
                               bank_slope=20, lakes=0.3)),
    "twin": dict(label="Twin continents", desc="Two landmasses facing each other across a strait.",
                 values=dict(shape="twin", land=0.4, scale=2.6, warp=0.13)),
    "inland_sea": dict(label="Inland sea", desc="A ring of land enclosing a great central sea.",
                       values=dict(shape="ring", land=0.42, scale=3, warp=0.1, sea_depth=700,
                                   temperature=0.08, rivers=0.65)),
    "highlands": dict(label="Highlands", desc="High, rugged terrain with little lowland and deep valleys.",
                      values=dict(shape="radial", land=0.58, scale=2, warp=0.12, edge_falloff=0.7,
                                  max_height=5000, hill_height=1200, mountain_height=4200,
                                  mountain_coverage=0.4, rock_slope=65, temperature=-0.05,
                                  river_max_height=1800, bank_slope=18, river_depth=35, lakes=0.2)),
    "supercontinent": dict(label="Supercontinent", desc="Land fills most of the map, with great inland lakes and long rivers.",
                           values=dict(shape="radial", land=0.55, scale=1.6, warp=0.2, edge_falloff=0.8,
                                       mountain_coverage=0.15, rivers=0.75, lakes=0.55, river_width=20)),
    "peninsula": dict(label="Peninsula", desc="A mainland coast with a long arm of land reaching out to sea.",
                      values=dict(shape="peninsula", land=0.4, scale=2.4, warp=0.12, rivers=0.55)),
    "crescent": dict(label="Crescent bay", desc="A horseshoe of land wrapped around a vast, sheltered bay.",
                     values=dict(shape="crescent", land=0.36, scale=3, warp=0.1, temperature=0.1, rivers=0.6)),
    "long_isle": dict(label="Long isle", desc="One long, narrow island with a mountain spine down its length.",
                      values=dict(shape="band", land=0.22, scale=3.2, warp=0.08, mountain_coverage=0.25,
                                  rivers=0.55, river_width=10)),
    "volcanic": dict(label="Volcanic island", desc="A towering volcano with a crater lake, ringed by warm coastal forest.",
                     values=dict(shape="volcano", land=0.27, scale=3, warp=0.12, max_height=4500, hill_height=350,
                                 mountain_height=1200, mountain_coverage=-0.3, sea_depth=900, rock_slope=50,
                                 temperature=0.25, moisture=0.15, rivers=0.75, river_max_height=1400,
                                 river_width=8, meander=0.9, lakes=0.1)),
    "broken_lands": dict(label="Broken lands", desc="Shattered land of large islands split by straits and sounds.",
                         values=dict(shape="scattered", land=0.45, scale=4, warp=0.22, fjords=0.3,
                                     edge_falloff=1.1, mountain_coverage=0.1, rivers=0.45)),
    "lake_district": dict(label="Lake district", desc="Low, rolling, wooded country dotted with dozens of lakes.",
                          values=dict(shape="radial", land=0.5, edge_falloff=0.8, max_height=1500, hill_height=350,
                                      mountain_height=900, mountain_coverage=-0.3, lakes=1.0, rivers=0.6,
                                      river_max_height=1500, moisture=0.25, forest=0.6, erosion=30)),
    "riverlands": dict(label="Riverlands", desc="Broad, flat lowlands crossed by wide, winding, navigable rivers.",
                       values=dict(shape="radial", land=0.5, max_height=1600, hill_height=300, mountain_height=1000,
                                   mountain_coverage=-0.25, rivers=1.0, river_width=30, river_depth=80,
                                   meander=0.8, bank_slope=6, river_max_height=1600, moisture=0.15, lakes=0.3)),
    "desert": dict(label="Desert", desc="Hot, dry land of sand seas and steppe, with rare oasis rivers.",
                   values=dict(shape="radial", land=0.45, hill_height=600, temperature=0.4, moisture=-0.5,
                               forest=0.15, rivers=0.3, lakes=0.15)),
    "frozen_north": dict(label="Frozen north", desc="Cold tundra and conifer forest under high, bare mountains.",
                         values=dict(shape="radial", land=0.45, max_height=3500, temperature=-0.45, moisture=0.1,
                                     mountain_coverage=0.2, forest=0.4, lakes=0.6)),
}


# Options that aren't sliders: an imported heightmap (file path or upload name) and the water
# level within it (0..1 of its grey range), a painted sketch (base64 grid), and brush edits.
EXTRA_OPTIONS = {
    "heightmap": str,
    "heightmap_sea": lambda v: min(max(float(v), 0.0), 0.99),
    "sketch": str,
    "edits": lambda v: shaping.clean_edits(v),
}
EXTRA_DEFAULTS = dict(heightmap=None, heightmap_sea=0.3, sketch=None)


def resolve_options(style="continent", values=None, seed=None):
    """Defaults, then the style's preset, then explicit values. Unknown keys are ignored."""
    opts = {p["name"]: p["default"] for p in PARAMS}
    opts.update(STYLES[style]["values"])
    opts.update(EXTRA_DEFAULTS, edits=[])
    for k, v in (values or {}).items():
        if k in EXTRA_OPTIONS:
            opts[k] = EXTRA_OPTIONS[k](v) if v not in (None, "", []) else EXTRA_DEFAULTS.get(k, [])
            continue
        p = PARAM_BY_NAME.get(k)
        if p is None or v is None:
            continue
        if p.get("kind") == "choice":
            if v not in p["choices"]:
                raise ValueError(f"{k} must be one of {p['choices']}")
        else:
            v = float(v)
            v = min(max(v, p["min"]), p["max"])
            if p.get("kind") == "int":
                v = int(round(v))
        opts[k] = v
    opts["style"] = style
    opts["seed"] = int(seed) if seed is not None else int(time.time()) & 0x7FFFFFFF
    return SimpleNamespace(**opts)


# --------------------------------------------------------------------------
# Presets: named settings saved as JSON next to this script
# --------------------------------------------------------------------------

PRESET_DIR = os.path.join(paths.TOOL_DIR, "presets")
PRESET_NAME_RE = re.compile(r"^[\w\- ]{1,40}$")


def _preset_path(name):
    name = str(name).strip()
    if not PRESET_NAME_RE.match(name):
        raise ValueError("Preset names are 1-40 letters, digits, spaces, '-' or '_'.")
    return os.path.join(PRESET_DIR, name + ".json")


def save_preset(name, style, values, seed=None, world_size=None):
    """Store only the settings that differ from the style, so presets follow later style tweaks."""
    if style not in STYLES:
        raise ValueError(f"unknown style {style}")
    base = vars(resolve_options(style, None, 0))
    full = vars(resolve_options(style, values, 0))
    changed = {k: full[k] for k in PARAM_BY_NAME if full[k] != base[k]}
    changed.update({k: full[k] for k in EXTRA_OPTIONS if full[k] not in (None, [], EXTRA_DEFAULTS.get(k))})
    data = dict(name=str(name).strip(), style=style, values=changed, saved=time.strftime("%Y-%m-%d %H:%M"))
    if seed is not None:
        data["seed"] = int(seed)
    if world_size:
        data["world_size"] = int(world_size)
    os.makedirs(PRESET_DIR, exist_ok=True)
    with open(_preset_path(name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def load_preset(name):
    path = _preset_path(name)
    if not os.path.isfile(path):
        raise ValueError(f"no preset named '{name}'")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("style") not in STYLES:
        raise ValueError(f"preset '{name}' uses an unknown style")
    return data


def list_presets():
    if not os.path.isdir(PRESET_DIR):
        return []
    out = []
    for f in sorted(os.listdir(PRESET_DIR), key=str.lower):
        if f.endswith(".json"):
            try:
                out.append(load_preset(f[:-5]))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
    return out


def delete_preset(name):
    path = _preset_path(name)
    if os.path.isfile(path):
        os.remove(path)


# --------------------------------------------------------------------------
# Logging (thread-local so the UI can capture a save job's progress)
# --------------------------------------------------------------------------

_log_local = threading.local()


def set_logger(fn):
    _log_local.fn = fn


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    fn = getattr(_log_local, "fn", None)
    if fn is None:
        print(line, flush=True)
    else:
        fn(line)


# --------------------------------------------------------------------------
# Map file IO
# --------------------------------------------------------------------------

def encode(tile_type, data, height):
    """Pack type/data/height arrays into uint32 tiles."""
    t = (np.asarray(tile_type, dtype=np.int32) & 0xFF).astype(np.uint32)
    d = (np.asarray(data, dtype=np.int32) & 0xFF).astype(np.uint32)
    h = (np.asarray(height, dtype=np.int32) & 0xFFFF).astype(np.uint32)
    return (t << 24) | (d << 16) | h


def decode(tiles):
    """Split uint32 tiles into (type int8, data uint8, height int16) arrays."""
    t = (tiles >> 24).astype(np.uint8).view(np.int8)
    d = ((tiles >> 16) & 0xFF).astype(np.uint8)
    h = (tiles & 0xFFFF).astype(np.uint16).view(np.int16)
    return t, d, h


def write_map(path, tiles):
    size = tiles.shape[0]
    level = size.bit_length() - 1
    if tiles.shape != (size, size) or 1 << level != size:
        raise ValueError("map must be a power-of-two square")
    header = struct.pack(">qbb", MAGIC, 0, level).ljust(HEADER_SIZE, b"\0")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(header)
        f.write(tiles.astype(">u4", copy=False).tobytes())
    os.replace(tmp, path)
    expected = HEADER_SIZE + 4 * size * size
    if os.path.getsize(path) != expected:
        raise IOError(f"{path}: wrong size written")


def read_map(path):
    with open(path, "rb") as f:
        magic, version, level = struct.unpack(">qbb", f.read(10))
    if magic != MAGIC:
        raise IOError(f"{path}: bad magic number, not a Wurm map")
    size = 1 << level
    tiles = np.fromfile(path, dtype=">u4", offset=HEADER_SIZE, count=size * size)
    return tiles.astype(np.uint32).reshape(size, size)


# --------------------------------------------------------------------------
# Noise
# --------------------------------------------------------------------------

class Perlin:
    """Fractal 2D gradient noise over the unit square.

    Every octave draws its gradients from its own (seed, layer, octave) stream, so
    a low-resolution preview that skips fine octaves still matches the full map.
    Each octave is rotated by its own random angle about the map centre, which hides
    the lattice-aligned (straight, axis-parallel) features plain Perlin noise has. The
    grid extends past each edge so rotated and domain-warped coordinates never clamp.
    """

    def __init__(self, seed, layer, base_freq, octaves, persistence=0.5, max_cells=None):
        self.layers = []
        freq, amp, norm = base_freq, 1.0, 0.0
        for i in range(octaves):
            cells = max(1, int(round(freq)))
            norm += amp
            if max_cells is None or cells <= max_cells:
                rng = np.random.default_rng([seed, layer, i])
                core = rng.uniform(0, 2 * np.pi, (cells + 2, cells + 2)).astype(np.float32)
                pad = int(math.ceil(cells * 0.6)) + 1
                angles = np.random.default_rng([seed, layer, i, 1]).uniform(
                    0, 2 * np.pi, (cells + 2 + 2 * pad,) * 2).astype(np.float32)
                angles[pad:pad + cells + 2, pad:pad + cells + 2] = core
                turn = np.random.default_rng([seed, layer, i, 2]).uniform(0, 2 * np.pi)
                self.layers.append((cells, pad, amp, np.cos(angles), np.sin(angles),
                                    np.float32(math.cos(turn)), np.float32(math.sin(turn))))
            freq *= 2.0
            amp *= persistence
        self.norm = norm

    @staticmethod
    def _octave(u, v, cells, pad, gx, gy):
        x = u * cells + pad
        y = v * cells + pad
        ix = np.floor(x).astype(np.int32)
        iy = np.floor(y).astype(np.int32)
        fx = (x - ix).astype(np.float32)
        fy = (y - iy).astype(np.float32)
        np.clip(ix, 0, cells + 2 * pad, out=ix)
        np.clip(iy, 0, cells + 2 * pad, out=iy)

        def dot(oy, ox, dx, dy):
            return gx[iy + oy, ix + ox] * dx + gy[iy + oy, ix + ox] * dy

        n00 = dot(0, 0, fx, fy)
        n10 = dot(0, 1, fx - 1, fy)
        n01 = dot(1, 0, fx, fy - 1)
        n11 = dot(1, 1, fx - 1, fy - 1)
        su = fx * fx * fx * (fx * (fx * 6 - 15) + 10)
        sv = fy * fy * fy * (fy * (fy * 6 - 15) + 10)
        a = n00 + su * (n10 - n00)
        b = n01 + su * (n11 - n01)
        return a + sv * (b - a)

    def sample(self, u, v, ridged=False):
        out = np.zeros(np.broadcast(u, v).shape, dtype=np.float32)
        du, dv = u - np.float32(0.5), v - np.float32(0.5)
        for cells, pad, amp, gx, gy, ca, sa in self.layers:
            ru = np.clip(0.5 + du * ca - dv * sa, -0.6, 1.59999)
            rv = np.clip(0.5 + du * sa + dv * ca, -0.6, 1.59999)
            n = self._octave(ru, rv, cells, pad, gx, gy)
            if ridged:
                n = 1.0 - np.abs(n) * 1.6
                n *= n
            out += amp * n
        return out / self.norm * (1.0 if ridged else 1.6)


def field(size, fn, chunk=256):
    """Evaluate fn(u, v) over the full size x size grid in row chunks."""
    out = np.empty((size, size), dtype=np.float32)
    u = (np.arange(size, dtype=np.float32) + 0.5) / size
    for y0 in range(0, size, chunk):
        y1 = min(size, y0 + chunk)
        v = ((np.arange(y0, y1, dtype=np.float32) + 0.5) / size)[:, None]
        out[y0:y1] = fn(np.broadcast_to(u[None, :], (y1 - y0, size)),
                        np.broadcast_to(v, (y1 - y0, size)))
    return out


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# --------------------------------------------------------------------------
# Terrain
# --------------------------------------------------------------------------

def layout_falloff(shape, du, dv, angle):
    """How strongly the land layout pushes each point under water (du, dv in -1..1)."""
    r = np.sqrt(du * du + dv * dv)
    if shape == "scattered":
        return smoothstep(0.75, 1.15, np.maximum(np.abs(du), np.abs(dv))) * 0.9
    if shape == "twin":
        ca, sa = math.cos(angle), math.sin(angle)
        ru = du * ca - dv * sa
        rv = du * sa + dv * ca
        d = np.minimum(np.hypot(ru - 0.47, rv), np.hypot(ru + 0.47, rv))
        return smoothstep(0.18, 0.62, d) * 0.95 + r * 0.1
    if shape == "ring":
        return smoothstep(0.06, 0.4, np.abs(r - 0.6)) + r * 0.1
    if shape in ("peninsula", "crescent", "band"):
        ca, sa = math.cos(angle), math.sin(angle)
        ru = du * ca - dv * sa
        rv = du * sa + dv * ca
        rim = smoothstep(0.8, 1.1, np.maximum(np.abs(du), np.abs(dv))) * 0.9
        if shape == "band":
            # A long island along a gently curving axis.
            axis = np.abs(rv - 0.15 * np.sin(ru * 2.5))
            return smoothstep(0.04, 0.26, axis) * 1.1 + smoothstep(0.7, 1.0, np.abs(ru)) * 0.9 + rim
        if shape == "peninsula":
            # Mainland along one side, with a long arm reaching out across the sea.
            mainland = np.maximum(0, ru + 0.45) * 1.5
            arm = np.abs(rv - 0.18 * np.sin(ru * 3)) * 2.4 + np.maximum(0, ru - 0.5) * 2.5
            return smoothstep(0.05, 0.5, np.minimum(mainland, arm)) * 0.95 + rim
        # Crescent: a thick ring around a great bay that opens to the sea on one side.
        theta = np.abs(np.arctan2(rv, ru))
        ring = smoothstep(0.08, 0.45, np.abs(r - 0.55))
        gap = smoothstep(0.95, 0.35, theta) * smoothstep(0.2, 0.45, r)
        return np.maximum(ring, gap) + r * 0.1
    if shape == "volcano":
        return smoothstep(0.15, 0.75, r) * 1.1 + r * 0.15
    return smoothstep(0.35, 1.05, r) * 0.9 + r * 0.15


def generate_heights(size, k, opts):
    """Corner heights in dm. k = world tiles per sample (1 for the real map, >1 for previews)."""
    seed = opts.seed
    world_level = int(round(math.log2(size * k)))
    detail_oct = max(3, world_level - 4)
    max_cells = size / 2

    def noise(layer, freq, octaves, persistence=0.5):
        return Perlin(seed, layer, freq, octaves, persistence, max_cells)

    warp_x = noise(1, 2, 3)
    warp_y = noise(2, 2, 3)
    continent = noise(3, opts.scale, detail_oct + 2)
    ridge = noise(4, 5, detail_oct)
    mountain_mask = noise(5, 2.5, 3)
    hills = noise(6, 12, detail_oct - 1, 0.45)
    fjord = noise(7, 4, 5, 0.6)
    angle = float(np.random.default_rng([seed, 99]).uniform(0, math.pi))
    warp = opts.warp
    sketch = shaping.sketch_of(opts)

    def warped(u, v):
        return u + warp * warp_x.sample(u, v), v + warp * warp_y.sample(u, v)

    def base(u, v):
        wu, wv = warped(u, v)
        c = continent.sample(wu, wv)
        du, dv = (wu - 0.5) * 2, (wv - 0.5) * 2
        edge = layout_falloff(opts.shape, du, dv, angle)
        # Whatever the layout, keep land out of the outer band so coasts never meet the map edge.
        squircle = (du ** 4 + dv ** 4) ** 0.25  # rounded square, so full maps don't look boxy
        guard = smoothstep(0.74, 0.98, squircle) * 1.5
        return c - edge * opts.edge_falloff - guard

    lo_m = -0.2 - opts.mountain_coverage

    def relief(u, v):
        wu = u + warp * 0.5 * warp_x.sample(v, u)
        wv = v + warp * 0.5 * warp_y.sample(v, u)
        m = smoothstep(lo_m, lo_m + 0.55, mountain_mask.sample(u, v))
        if sketch:
            m = np.maximum(m, sketch.sample("mountain", u, v)) * (1 - sketch.sample("lowland", u, v))
        r = ridge.sample(wu, wv, ridged=True)
        return m * r * r

    log("  landmasses")
    h = field(size, base)
    log("  mountains")
    mnt = field(size, relief)
    log("  hills")
    hl = field(size, hills.sample)

    if sketch:
        log("  sketch")
        paint = field(size, lambda u, v: sketch.sample("paint", *warped(u, v)))
        signed = field(size, lambda u, v: sketch.sample("signed", *warped(u, v)))
        land = shaping.apply_sketch_land(h, paint, signed, opts.land)
        del paint, signed
    else:
        sea = np.quantile(h[::4, ::4], 1.0 - opts.land)
        land = h - sea
    land_max = max(float(land.max()), 1e-6)
    sea_min = max(float(-land.min()), 1e-6)

    above = np.clip(land / land_max, 0, None)
    below = np.clip(-land / sea_min, 0, None)
    # Fjord coasts rise straight into mountains; other styles get gentle coastal plains.
    inland = smoothstep(0.0, 0.12 * (1 - 0.8 * opts.fjords), above)

    lowland = above ** 1.4 * opts.hill_height + hl * opts.hill_height * 0.25 * inland
    mountains = mnt * inland * opts.mountain_height
    height = np.where(land >= 0, lowland + mountains + 1.0, -(below ** 0.6) * opts.sea_depth)
    del h, mnt, hl, above, below, inland, lowland, mountains

    if opts.shape == "volcano":
        log("  volcano")
        vrng = np.random.default_rng([seed, 98])
        cx, cy = 0.5 + vrng.uniform(-0.06, 0.06), 0.5 + vrng.uniform(-0.06, 0.06)

        def dist(u, v):
            return np.hypot(u + 0.25 * warp * warp_x.sample(v, u) - cx, v + 0.25 * warp * warp_y.sample(v, u) - cy)

        r = field(size, dist) * 2
        cone = np.clip(1 - r / 0.7, 0, 1) ** 1.35 * opts.max_height * 1.2
        height = np.where(height > 0, height + cone, height + cone * 0.5)
        # Crater with a lake in the bottom.
        rc = 0.075
        rim = float(opts.max_height * 1.2 * (1 - rc / 0.7) ** 1.35)
        height = np.where(r < rc, np.minimum(height, -60 + (r / rc) ** 2.5 * (rim + 60)), height)

    # Rescale so the highest peaks hit max-height (ignoring a few outliers).
    peak = float(np.quantile(height[height > 0][::7], 0.9995)) if (height > 0).any() else 1.0
    height = np.where(height > 0, height * (opts.max_height / max(peak, 1.0)), height)

    if opts.fjords > 0:
        log("  fjords")

        def channels(u, v):
            wu = u + warp * 0.6 * warp_x.sample(v, u)
            wv = v + warp * 0.6 * warp_y.sample(v, u)
            return fjord.sample(wu, wv)

        # Channels follow the noise's zero crossings and fade out with distance from the sea,
        # so they open onto the coast and wind inland like glacial valleys.
        ch = np.clip(1 - np.abs(field(size, channels)) * 13, 0, 1)
        reach = size * 0.1 * (0.5 + opts.fjords)
        coast = 1 - smoothstep(reach * 0.4, reach, coarse_distance(height <= 0, reach))
        cut = smoothstep(0.2, 0.5, ch * coast * (0.6 + opts.fjords * 0.6))
        cut *= height > -150
        target = -(120 + opts.sea_depth * 0.4) * np.clip(ch * 1.4, 0.3, 1)
        height = height + cut * (target - height)

    # Keep a ring of open water around the map.
    margin = max(8, (size * k) // 128) / k
    ramp = np.minimum(np.arange(size), np.arange(size)[::-1]).astype(np.float32)
    ramp = smoothstep(0, margin, ramp)
    edge = np.minimum(ramp[:, None], ramp[None, :])
    height = np.where(edge < 1, np.minimum(height, -60 + edge * (height + 60)), height)
    return height.astype(np.float32)


def erode(height, iterations, talus):
    """Thermal erosion: move material downhill wherever slope exceeds talus (dm per sample)."""
    h = height
    for _ in range(iterations):
        delta = np.zeros_like(h)
        for axis, shift in ((0, 1), (0, -1), (1, 1), (1, -1)):
            nb = np.roll(h, shift, axis=axis)
            diff = h - nb
            move = np.where(diff > talus, (diff - talus) * 0.25, 0.0).astype(np.float32)
            delta -= move
            delta += np.roll(move, -shift, axis=axis)
        h += delta * 0.5
    return h


def is_plant(t):
    """Trees and bushes (tile ids 100-113 and the negative foliage ids)."""
    t = t.astype(np.int16)
    return (t >= 100) | ((t <= -84) & (t >= -128))


def tile_slope(corners):
    """Per-tile min, max and max-min height over its four corners (edges wrap)."""
    c = corners
    n = np.roll(c, -1, 1)
    s = np.roll(c, -1, 0)
    ns = np.roll(n, -1, 0)
    lo = np.minimum(np.minimum(c, n), np.minimum(s, ns))
    hi = np.maximum(np.maximum(c, n), np.maximum(s, ns))
    return lo, hi, hi - lo


def corners_of(mask):
    """Corner mask: true where any of the four tiles sharing that corner is set."""
    return mask | np.roll(mask, 1, 0) | np.roll(mask, 1, 1) | np.roll(np.roll(mask, 1, 0), 1, 1)


def distance_to(mask, max_dist):
    """Approximate Chebyshev distance (in samples) to the nearest set sample, capped."""
    dist = np.full(mask.shape, max_dist, dtype=np.int16)
    front = mask.copy()
    dist[front] = 0
    for d in range(1, max_dist):
        grown = front | np.roll(front, 1, 0) | np.roll(front, -1, 0)
        grown = grown | np.roll(grown, 1, 1) | np.roll(grown, -1, 1)
        new = grown & ~front
        if not new.any():
            break
        dist[new] = d
        front = grown
    return dist


def coarse_distance(mask, max_dist, max_grid=512):
    """distance_to on a downsampled grid (<= max_grid samples wide), upsampled back."""
    size = mask.shape[0]
    f = max(1, size // max_grid)
    small = mask[::f, ::f] if f == 1 else mask.reshape(size // f, f, size // f, f).any(axis=(1, 3))
    d = distance_to(small, max(2, int(math.ceil(max_dist / f)))).astype(np.float32) * f
    return np.repeat(np.repeat(d, f, 0), f, 1) if f > 1 else d


# --------------------------------------------------------------------------
# Rivers and lakes
#
# Wurm has a single water level: anything below height 0 is water. So rivers and
# lakes are carved below 0, with valley sides rising back to the terrain. Drainage
# is computed on a fixed 256x256 grid so previews and full maps get the same network.
# --------------------------------------------------------------------------

DRAIN_GRID = 256
NEIGHBOURS = ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1))


def drainage(hc, seed):
    """Priority-flood over a coarse heightmap.

    Returns (filled, parent, order): depression-filled heights, the downstream cell
    of each cell (-1 for water/border), and cells in the order they were reached.
    """
    import heapq
    g = hc.shape[0]
    h = hc.ravel().astype(np.float64) + np.random.default_rng([seed, 3000]).uniform(0, 0.01, g * g)
    filled = h.copy()
    parent = np.full(g * g, -1, dtype=np.int64)
    visited = np.zeros(g * g, dtype=bool)
    border = np.zeros((g, g), dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    seeds = np.nonzero((hc.ravel() <= 0) | border.ravel())[0]
    visited[seeds] = True
    heap = [(h[i], int(i)) for i in seeds]
    heapq.heapify(heap)
    order = []
    while heap:
        hv, i = heapq.heappop(heap)
        order.append(i)
        y, x = divmod(i, g)
        for dy, dx in NEIGHBOURS:
            ny, nx = y + dy, x + dx
            if 0 <= ny < g and 0 <= nx < g:
                j = ny * g + nx
                if not visited[j]:
                    visited[j] = True
                    filled[j] = max(h[j], hv + 1e-3)
                    parent[j] = i
                    heapq.heappush(heap, (filled[j], j))
    return filled.reshape(g, g), parent, order


def trace_rivers(hc, parent, order, opts, g):
    """River polylines as lists of (cell index, flow accumulation), longest first."""
    n = g * g
    acc = np.ones(n, dtype=np.float64)
    for i in reversed(order):
        p = parent[i]
        if p >= 0:
            acc[p] += acc[i]
    scale = (g / DRAIN_GRID) ** 2
    threshold = (1500 - 1440 * opts.rivers ** 0.5) * scale
    flat = hc.ravel()
    river = (flat > 0) & (acc >= threshold) & (flat < opts.river_max_height)
    has_child = np.zeros(n, dtype=bool)
    idx = np.nonzero(river & (parent >= 0))[0]
    has_child[parent[idx][river[parent[idx]]]] = True
    heads = np.nonzero(river & ~has_child)[0]
    heads = heads[np.argsort(-flat[heads])]  # highest sources first -> main stems traced first

    taken = np.zeros(n, dtype=bool)
    paths = []
    for head in heads:
        path, i = [], int(head)
        while True:
            path.append(i)
            if taken[i]:
                break  # joined an existing river
            taken[i] = True
            p = int(parent[i])
            if p < 0 or not river[p]:
                if p >= 0:
                    path.append(p)  # step into the sea or lake so the mouth opens
                break
            i = p
        if len(path) >= 5:
            paths.append([(c, acc[c]) for c in path])
    return paths, threshold


def chaikin(pts, rounds=3):
    for _ in range(rounds):
        if len(pts) < 3:
            break
        q = 0.75 * pts[:-1] + 0.25 * pts[1:]
        r = 0.25 * pts[:-1] + 0.75 * pts[1:]
        mid = np.empty((2 * len(q), 2))
        mid[0::2], mid[1::2] = q, r
        pts = np.vstack([pts[:1], mid, pts[-1:]])
    return pts


def resample(pts, values, spacing):
    seg = np.hypot(*np.diff(pts, axis=0).T)
    s = np.concatenate([[0], np.cumsum(seg)])
    if s[-1] <= 0:
        return pts, values, s
    t = np.arange(0, s[-1] + 1e-9, spacing)
    return (np.stack([np.interp(t, s, pts[:, 0]), np.interp(t, s, pts[:, 1])], 1),
            np.interp(t, s, values), t)


def bank_profile(t, s_bank, r0):
    """Height of a valley wall t samples from the water's edge: gentle near the water,
    steepening with distance so it always climbs back up to the surrounding terrain."""
    return t * s_bank * (1 + (t / r0) ** 2)


def bank_reach(h, s_bank, r0):
    """Distance t at which bank_profile(t) reaches height h (Cardano on t^3 + r0^2 t - h r0^2 / s = 0)."""
    p, q = r0 * r0, -h * r0 * r0 / s_bank
    disc = math.sqrt(q * q / 4 + p ** 3 / 27)
    return float(np.cbrt(-q / 2 + disc) + np.cbrt(-q / 2 - disc))


def stamp(height, mask, cx, cy, r_water, bed, s_under, s_bank, radius, k_r0):
    """Carve a round channel/basin centred on (cx, cy) in sample units, only lowering terrain."""
    size = height.shape[0]
    bed = max(bed, -r_water * s_under)
    r0 = r_water + bed / s_under
    x0, x1 = max(0, int(cx - radius)), min(size, int(cx + radius) + 2)
    y0, y1 = max(0, int(cy - radius)), min(size, int(cy + radius) + 2)
    if x0 >= x1 or y0 >= y1:
        return
    xs = np.arange(x0, x1, dtype=np.float32) - cx
    ys = (np.arange(y0, y1, dtype=np.float32) - cy)[:, None]
    d = np.sqrt(xs * xs + ys * ys)
    t = d - r_water
    prof = np.where(d < r0, bed, np.where(d < r_water, bed + (d - r0) * s_under, bank_profile(t, s_bank, k_r0)))
    sub = height[y0:y1, x0:x1]
    np.minimum(sub, prof, out=sub)
    mask[y0:y1, x0:x1] |= d < r_water


def add_water(height, k, opts):
    """Carve rivers and lakes into the heightmap in place. Returns (water mask, info)."""
    size = height.shape[0]
    world = size * k
    info = dict(rivers=0, river_tiles=0, lakes=0)
    mask = np.zeros(height.shape, dtype=bool)
    if opts.rivers <= 0 and opts.lakes <= 0:
        return mask, info

    g = min(DRAIN_GRID, size)
    f = size // g
    hc = height.reshape(g, f, g, f).mean(axis=(1, 3))
    filled, parent, order = drainage(hc, opts.seed)
    cell_tiles = world / g

    s_bank = opts.bank_slope * k   # dm per sample
    s_under = max(s_bank * 3, 30 * k)
    max_radius = 160 / k
    wall_r0 = 40 / k  # walls start steepening noticeably beyond ~40 tiles from the water
    # Coarse local maximum, so each stamp knows how wide its valley must be.
    reach = int(math.ceil(160 / cell_tiles))
    local_max = hc.copy()
    for _ in range(reach):
        grown = local_max
        for dy, dx in NEIGHBOURS:
            grown = np.maximum(grown, np.roll(np.roll(local_max, dy, 0), dx, 1))
        local_max = grown

    def valley_radius(cx, cy, r_water):
        h = local_max[min(g - 1, int(cy / f)), min(g - 1, int(cx / f))]
        return min(max_radius, r_water + bank_reach(max(0.0, float(h)) + 40, s_bank, wall_r0) + 2)

    if opts.rivers > 0:
        log("  rivers")
        paths, threshold = trace_rivers(hc, parent, order, opts, g)
        acc_ref = 2500 * (g / DRAIN_GRID) ** 2
        wander = Perlin(opts.seed, 300, 6, 2)     # long bends that break up straight grid runs
        meander = Perlin(opts.seed, 301, 40, 2)   # tight loops, ~60 tiles apart on a 2048 map
        min_r = 0.6  # keep narrow rivers visible in previews
        for pid, path in enumerate(paths):
            cells = np.array([c for c, _ in path])
            accs = np.array([a for _, a in path], dtype=np.float64)
            pts = np.stack([(cells % g + 0.5) * f, (cells // g + 0.5) * f], 1).astype(np.float64)
            pts = chaikin(pts)
            accs = np.interp(np.linspace(0, 1, len(pts)), np.linspace(0, 1, len(accs)), accs)
            t = np.clip(np.sqrt(np.maximum(accs - threshold, 0) / acc_ref), 0, 1)
            width = 3 + (opts.river_width - 3) * t                 # tiles
            pts, width, s = resample(pts, width, max(0.5, 1.5 / k))
            if len(pts) < 2:
                continue
            # Meander: push points sideways along the normal, tapering to zero at both ends.
            tan = np.gradient(pts, axis=0)
            tan /= np.maximum(np.hypot(tan[:, 0], tan[:, 1]), 1e-6)[:, None]
            normal = np.stack([-tan[:, 1], tan[:, 0]], 1)
            s_tiles = s * k
            u = np.clip(s_tiles / (world * 1.2), 0, 0.999).astype(np.float32)
            v = np.full_like(u, (pid * 0.618034) % 1)
            taper = np.minimum(1, np.minimum(s_tiles, s_tiles[-1] - s_tiles) / (2 * cell_tiles))
            offset = (wander.sample(u, v) * cell_tiles * 2.5
                      + meander.sample(u, v) * (10 + width * 2)) * opts.meander * taper / k
            pts = pts + normal * offset[:, None]
            depth = 15 + (opts.river_depth - 15) * np.clip(width / max(opts.river_width, 3), 0, 1)
            step = max(1, int(max(0.5, width.min() / k * 0.35)))
            for (cx, cy), w, d in zip(pts[::step], width[::step], depth[::step]):
                r = max(min_r, w / 2 / k)
                stamp(height, mask, cx, cy, r, -d, s_under, s_bank, valley_radius(cx, cy, r), wall_r0)
            info["rivers"] += 1
            info["river_tiles"] += int(s_tiles[-1])

    if opts.lakes > 0:
        log("  lakes")
        depth_needed = 320 - 300 * opts.lakes  # depression depth (dm) that makes a lake
        lake = (hc > 0) & ((filled - hc) > depth_needed) & (hc < opts.river_max_height)
        labels, sizes = label_components(lake)
        keep = np.argsort(-sizes)[:int(4 + 50 * opts.lakes)] + 1  # only the largest basins
        lake = np.isin(labels, keep)
        lake_depth = opts.river_depth * 1.5
        jitter = np.random.default_rng([opts.seed, 3100])
        for cy, cx in zip(*np.nonzero(lake)):
            px = (cx + 0.5 + jitter.uniform(-0.3, 0.3)) * f
            py = (cy + 0.5 + jitter.uniform(-0.3, 0.3)) * f
            r = cell_tiles * jitter.uniform(0.7, 1.0) / k
            stamp(height, mask, px, py, r, -lake_depth, s_under, s_bank, valley_radius(px, py, r), wall_r0)
        info["lakes"] = int(min(len(sizes), len(keep)))
    return mask, info


def label_components(mask):
    """8-connected component labels (1..n, 0 = background) and each component's size."""
    labels = np.zeros(mask.shape, dtype=np.int32)
    g = mask.shape[0]
    sizes = []
    for y, x in zip(*np.nonzero(mask)):
        if labels[y, x]:
            continue
        n = len(sizes) + 1
        stack = [(y, x)]
        labels[y, x] = n
        count = 0
        while stack:
            cy, cx = stack.pop()
            count += 1
            for dy, dx in NEIGHBOURS:
                ny, nx = cy + dy, cx + dx
                if 0 <= ny < g and 0 <= nx < g and mask[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = n
                    stack.append((ny, nx))
        sizes.append(count)
    return labels, np.array(sizes, dtype=np.int64)


# --------------------------------------------------------------------------
# Ores
#
# Veins are meandering random walks laid out in world-tile coordinates from a fixed
# coarse weight grid, so a preview shows exactly the veins the full map will get.
# --------------------------------------------------------------------------

def generate_ores(height, rock, k, opts):
    """Ore vein tiles as world-tile arrays (x, y, tile id), or None for server-made caves."""
    if opts.caves != "generator":
        return None
    size = rock.shape[0]
    world = int(round(size * k))
    weights = np.array([getattr(opts, "ore_" + d[0]) for d in ORE_DEFS], dtype=np.float64)
    if weights.sum() <= 0 or opts.ore_amount <= 0:
        return SimpleNamespace(x=np.zeros(0, np.int32), y=np.zeros(0, np.int32), t=np.zeros(0, np.int8))
    rng = np.random.default_rng([opts.seed, 4000])

    g = min(DRAIN_GRID, size)
    f = size // g
    rc = rock.reshape(g, f, g, f).mean(axis=(1, 3))
    under_land = height.reshape(g, f, g, f).min(axis=(1, 3)) > 0  # no mining under water
    if not under_land.any():
        return SimpleNamespace(x=np.zeros(0, np.int32), y=np.zeros(0, np.int32), t=np.zeros(0, np.int8))
    high = np.clip(rc / max(opts.max_height * 0.35, 1), 0, 1) ** 1.2
    cell_w = ((1 - opts.ore_mountains) + opts.ore_mountains * high) * under_land
    cell_w = cell_w.ravel() / cell_w.sum()

    target = opts.ore_amount / 100 * under_land.sum() / under_land.size * world * world
    mean_len = max(1, opts.vein_size)
    n = max(1, int(round(target / mean_len)))
    cells = rng.choice(g * g, n, p=cell_w)
    cw = world / g
    px = (cells % g + rng.random(n)) * cw
    py = (cells // g + rng.random(n)) * cw
    pick = rng.choice(len(ORE_DEFS), n, p=weights / weights.sum())
    kinds = np.array([d[2] for d in ORE_DEFS], dtype=np.int8)[pick]
    lengths = np.maximum(1, rng.integers(int(mean_len * 0.5), int(mean_len * 1.5) + 1, n))
    heading = rng.uniform(0, 2 * np.pi, n)

    xs, ys, ts = [], [], []
    for step in range(int(lengths.max())):
        live = lengths > step
        xs.append(px[live])
        ys.append(py[live])
        ts.append(kinds[live])
        heading += rng.normal(0, 0.45, n)
        px += np.cos(heading)
        py += np.sin(heading)
    x = np.clip(np.concatenate(xs).astype(np.int32), 1, world - 2)
    y = np.clip(np.concatenate(ys).astype(np.int32), 1, world - 2)
    t = np.concatenate(ts)
    # One ore per tile: drop repeats where walks crossed themselves or each other.
    _, first = np.unique(y.astype(np.int64) * world + x, return_index=True)
    return SimpleNamespace(x=x[first], y=y[first], t=t[first])


def cave_tiles(ores, world):
    """Full cave layer: solid rock everywhere, with the ore veins set in it."""
    cave = np.full((world, world), encode(CAVE_WALL, 0, CAVE_FLOOR_HEIGHT), dtype=np.uint32)
    if ores is not None and len(ores.x):
        cave[ores.y, ores.x] = encode(ores.t, 0, CAVE_FLOOR_HEIGHT)
    return cave


def resource_tiles(ores, world, opts):
    """resources.map with fixed ore quantities (high 16 bits); 0xFFFF elsewhere = 'not set yet'."""
    res = np.full((world, world), 0xFFFFFFFF, dtype=np.uint32)
    if ores is not None and len(ores.x) and opts.ore_yield > 0:
        rng = np.random.default_rng([opts.seed, 4100])
        q = np.clip(opts.ore_yield * rng.uniform(0.5, 1.5, len(ores.x)), 1, 65534).astype(np.uint32)
        res[ores.y, ores.x] = (q << 16) | 0xFFFF
    return res


# --------------------------------------------------------------------------
# Surface types
# --------------------------------------------------------------------------

def generate_surface(height, k, opts):
    size = height.shape[0]
    seed = opts.seed
    rng = np.random.default_rng([seed, 1000])
    lo, hi, slope = tile_slope(height)
    slope = slope / k  # dm per world tile
    layer = iter(range(100, 200))
    noise = lambda f, o: field(size, Perlin(seed, next(layer), f, o, max_cells=size / 2).sample)

    def dist_tiles(mask, max_tiles):
        # Whole tiles times a power-of-two k, so float32 holds them exactly.
        return (distance_to(mask, max(2, int(math.ceil(max_tiles / k)))) * k).astype(np.float32)

    log("  dirt depth and rock")
    depth_noise = noise(8, 4)
    rock_noise = noise(24, 3)
    base_depth = np.clip(62 + depth_noise * 14, 30, 75)
    rock_tiles = (slope >= opts.rock_slope + rock_noise * 12) & (lo > -300)
    rock_tiles |= (lo > opts.max_height * 0.82) & (slope > 25)
    corner_slope = np.maximum(slope, np.roll(np.roll(slope, 1, 0), 1, 1))
    depth = base_depth * (1 - smoothstep(10, opts.rock_slope, corner_slope))
    depth = np.maximum(depth, np.where(corner_slope < opts.rock_slope, 8, 0))
    depth[corners_of(rock_tiles)] = 0
    depth = np.round(depth)
    rock = height - depth
    del depth_noise, rock_noise, base_depth, corner_slope, depth  # free memory for large maps

    types = np.full(height.shape, GRASS, dtype=np.int16)
    data = np.zeros(height.shape, dtype=np.uint8)

    log("  climate")
    water = hi <= 0
    shore_dist = dist_tiles(water, 40)
    sea_dist = dist_tiles(lo > 0, 40)
    temp_noise = noise(3, 4)
    moist_noise = noise(4, 5)
    lat = np.linspace(-0.15, 0.25, size, dtype=np.float32)[:, None]  # south is warmer
    temp = 0.55 + opts.temperature + temp_noise * 0.35 + lat - np.clip(lo, 0, None) / opts.max_height * 0.9
    moist = opts.moisture + moist_noise * 0.6 + np.exp(-shore_dist.astype(np.float64) / 12.0) * 0.35
    del temp_noise, moist_noise
    patch = noise(32, 3)
    blotch = noise(64, 2)
    land = (lo > 0) & ~rock_tiles

    log("  ground cover")
    types[land & (temp < 0.22 + 0.2 * opts.tundra)] = TUNDRA
    types[land & (moist < -0.22 + 0.25 * opts.steppe) & (temp > 0.42 - 0.12 * opts.steppe)] = STEPPE
    desert = land & (moist < -0.38) & (temp > 0.6) & (patch > 0.05)
    types[desert] = SAND
    types[land & (moist > 0.28) & (slope < 25) & (patch > 0.2)] = PEAT
    types[land & (moist > 0.2) & (temp < 0.55) & (patch < -0.3)] = MOSS
    marsh = ((lo > -60) & (hi < 90 + 60 * max(0.0, opts.marsh)) & (slope < 10) & (moist > 0.2 - 0.2 * opts.marsh)
             & (patch > 0.15 - 0.3 * opts.marsh) & (shore_dist < 10 * (1 + opts.marsh)))
    types[marsh & ~rock_tiles] = MARSH
    beach_h = 18 + patch * 14
    beach = (lo > 0) & (hi < beach_h) & (shore_dist <= 3) & ~marsh & ~rock_tiles
    types[beach] = SAND
    types[land & (slope > 45) & (blotch > 0.55)] = GRAVEL

    # Seabed: dirt, sandy in the shallows.
    sea = (lo <= 0) & ~marsh & ~rock_tiles
    sand_floor = -120 - patch * 120
    types[sea] = DIRT
    types[sea & ((lo > sand_floor) & (sea_dist < 25))] = SAND

    # Rare resources.
    types[(np.abs(lo) < 60) & (shore_dist < 14) & (blotch > 0.6) & ~rock_tiles] = CLAY
    types[(slope < 30) & (blotch < -0.7) & ~rock_tiles & (lo > -80)] = TAR

    types[rock_tiles] = ROCK
    types[rock_tiles & (slope > opts.cliff_slope)] = CLIFF

    # Water plants.
    plant = rng.random(height.shape, dtype=np.float32)
    reed = ((lo > -80) & (hi < 40) & (lo <= 0) & (types != CLAY) & (types != TAR)
            & (sea_dist < 6) & (patch > 0.1) & (plant < 0.6))
    types[reed & ~rock_tiles] = REED
    kelp = (lo > -300) & (hi < -55) & (sea_dist < 30) & (patch < -0.3) & (plant < 0.35)
    types[kelp & ~rock_tiles] = KELP
    del water, land, desert, marsh, beach_h, beach, sea, sand_floor, plant, reed, kelp, patch, blotch, sea_dist

    log("  forests")
    forest = noise(6, 5)
    species_noise = noise(10, 3)
    roll = rng.random(height.shape, dtype=np.float32)
    band = np.where(temp < 0.38, 0, np.where(temp > 0.68, 2, 1)).astype(np.int8)
    tree_ok = (lo > 2) & (slope < 60) & np.isin(types, (GRASS, TUNDRA, STEPPE, MOSS, PEAT))
    threshold = 0.42 - 0.8 * opts.forest
    forest_density = np.where(forest > threshold, 0.65, 0.04)
    forest_density = np.where(types == STEPPE, forest_density * 0.15, forest_density)
    forest_density = np.where(types == TUNDRA, forest_density * 0.5, forest_density)
    trees = tree_ok & (roll < forest_density)
    bushes = tree_ok & ~trees & (roll < forest_density + np.where(forest > threshold - 0.1, 0.05, 0.015)
                                 * opts.bushes)

    species = np.zeros(height.shape, dtype=np.int16)
    for b in range(3):
        pool = np.array(TREES[b], dtype=np.int16)
        idx = ((species_noise * 0.5 + 0.5) * len(pool) * 1.5).astype(np.int32)
        scatter = rng.integers(0, len(pool), height.shape).astype(np.int16)
        idx = np.where(rng.random(height.shape) < 0.25, scatter, idx) % len(pool)
        species = np.where(band == b, pool[idx], species)
    species = np.where((shore_dist < 4) & (moist > 0.2) & (rng.random(height.shape) < 0.5), WILLOW, species)
    if opts.conifers or opts.fruit_trees != 1:
        species = mix_species(species, band, opts, np.random.default_rng([seed, 1001]))
    types[trees] = species[trees]
    del species, species_noise, forest, roll, forest_density, tree_ok, idx, scatter, temp, moist, shore_dist

    bush_species = np.zeros(height.shape, dtype=np.int16)
    for b in range(3):
        pool = np.array(BUSHES[b], dtype=np.int16)
        bush_species = np.where(band == b, pool[rng.integers(0, len(pool), height.shape)], bush_species)
    types[bushes] = bush_species[bushes]
    del bush_species, band

    log("  growth stages")
    # Data bytes.
    # Draws are cast to uint8 straight away (same values, far less memory on big maps).
    foliage = trees | bushes
    del trees, bushes
    age = rng.integers(4, 14, height.shape).astype(np.uint8)  # MATURE_ONE .. VERY_OLD_SPROUTING
    stage = rng.integers(0, 4, height.shape).astype(np.uint8)
    data[foliage] = ((age << 4) | stage)[foliage]
    del age, stage, foliage
    grassy = types == GRASS
    has_flower = rng.random(height.shape) < opts.flowers
    flower = np.where(has_flower, rng.integers(1, 16, height.shape).astype(np.uint8), np.uint8(0))
    del has_flower
    data[grassy] = ((rng.integers(0, 4, height.shape).astype(np.uint8) << 6) | flower)[grassy]
    del flower, grassy
    for t in (REED, KELP, CLAY, TAR):
        m = types == t
        data[m] = (rng.integers(0, 3, height.shape).astype(np.uint8) << 6)[m]

    if opts.snow_line > 0:
        snow = (lo > opts.snow_line) & ~rock_tiles
        types[snow], data[snow] = SNOW, 0

    rock = np.minimum(rock, height)
    return types, data, rock


CONIFERS = (PINE, FIR, CEDAR)
BROADLEAF = (OAK, BIRCH, MAPLE, LINDEN, CHESTNUT, WALNUT)
FRUIT = ((APPLE, CHERRY), (APPLE, CHERRY), (OLIVE, LEMON, ORANGE, CHERRY))  # by climate band


def mix_species(species, band, opts, rng):
    """Shift the tree mix toward conifers/broadleaf and more/fewer fruit trees."""
    out = species.copy()
    roll = rng.random(species.shape, dtype=np.float32)
    pick = rng.integers(0, 64, species.shape)

    def choose(pool):
        pool = np.array(pool, dtype=np.int16)
        return pool[pick % len(pool)]

    c = opts.conifers
    if c > 0:
        sel = np.isin(species, BROADLEAF) & (roll < c)
        out[sel] = choose(CONIFERS)[sel]
    elif c < 0:
        sel = np.isin(species, CONIFERS) & (roll < -c)
        out[sel] = choose(BROADLEAF)[sel]
    f = opts.fruit_trees
    fruit = np.isin(out, (APPLE, CHERRY, OLIVE, LEMON, ORANGE))
    if f < 1:
        sel = fruit & (roll < 1 - f)
        out[sel] = choose(CONIFERS if c > 0.5 else BROADLEAF)[sel]
    elif f > 1:
        sel = ~fruit & np.isin(out, BROADLEAF) & (roll > 1 - (f - 1) * 0.2)
        for b in range(3):
            m = sel & (band == b)
            out[m] = choose(FRUIT[b])[m]
    return out


# Resources a new player needs close to spawn: (mask builder, ideal distance in tiles).
SPAWN_NEEDS = (
    (lambda t, lo, hi: hi <= 0, 40),                                                    # water
    (lambda t, lo, hi: (t >= 100) | ((t <= -115) & (t >= -128)) | (t == ORANGE), 40),   # trees
    (lambda t, lo, hi: t == CLAY, 150),
    (lambda t, lo, hi: t == TAR, 250),
    (lambda t, lo, hi: np.isin(t, (ROCK, CLIFF)) & (hi > 0), 150),                      # somewhere to mine
    (lambda t, lo, hi: (t == SAND) & (hi > 0), 120),
)


def pick_spawns(types, height, k, seed, count=3):
    """Flat grass tiles near basic resources, spread out across the land, in world tile coordinates."""
    size = types.shape[0]
    lo, hi, slope = tile_slope(height.astype(np.float32))
    ok = (types == GRASS) & (slope / k < 6) & (lo > 20)
    inner = np.zeros_like(ok)
    b = size // 10
    inner[b:size - b, b:size - b] = True
    if (ok & inner).any():
        ok &= inner  # keep spawns away from the map edge when possible
    step = max(1, 4 // int(k)) if k < 4 else 1
    ys, xs = np.nonzero(ok[::step, ::step])
    if len(xs) == 0:
        return []
    ys, xs = ys * step, xs * step
    rng = np.random.default_rng([seed, 2000])
    order = rng.permutation(len(xs))[:4000]
    cand = np.stack([xs[order], ys[order]], 1)
    # Penalise candidates by how far past the ideal distance each needed resource is.
    t = types.astype(np.int16)
    penalty = np.zeros(len(cand))
    for need, ideal in SPAWN_NEEDS:
        cap = 4 * ideal / k
        d = coarse_distance(need(t, lo, hi), cap)[cand[:, 1], cand[:, 0]] * k
        penalty += np.maximum(0, d - ideal) / ideal
    # Room for a village: share of flat land within ~40 tiles (box mean via an integral image).
    r = max(1, int(round(40 / k)))
    flat = np.pad(((slope / k <= 10) & (lo > 0)).astype(np.float32), r, mode="edge")
    ii = np.pad(flat.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    cx, cy = cand[:, 0] + r, cand[:, 1] + r
    room = (ii[cy + r + 1, cx + r + 1] - ii[cy - r, cx + r + 1] - ii[cy + r + 1, cx - r]
            + ii[cy - r, cx - r]) / (2 * r + 1) ** 2
    penalty += np.maximum(0, 0.5 - room) * 4

    centre = np.array([size / 2, size / 2])
    min_gap = size * 0.15  # in samples; widen the pool until spawns can be this far apart
    for q in (0.2, 0.4, 0.7, 1.0):
        good = cand[penalty <= np.quantile(penalty, q) + 1e-9]
        picks = [good[np.argmin(np.abs(good - centre).sum(1) / size + penalty[penalty <= np.quantile(penalty, q) + 1e-9])]]
        while len(picks) < count:
            d = np.min([np.abs(good - p).sum(1) for p in picks], axis=0)
            picks.append(good[np.argmax(d)])
        gaps = [np.abs(a - b).sum() for i, a in enumerate(picks) for b in picks[i + 1:]]
        if not gaps or min(gaps) >= min_gap:
            break
    return [(int(x * k + k // 2), int(y * k + k // 2)) for x, y in picks]


def generate(opts, size, world_size=None):
    """Run the whole pipeline at `size` samples, representing a world_size map."""
    world_size = world_size or size
    k = world_size / size
    log(f"Generating {opts.style} map, {world_size}x{world_size}"
        + (f" (preview {size}x{size})" if k != 1 else "") + f", seed {opts.seed}")
    if opts.heightmap:
        log(f"Heightmap (imported from {os.path.basename(opts.heightmap)})")
        height = shaping.imported_heights(size, opts)
    else:
        log("Heightmap")
        height = generate_heights(size, k, opts)
    if opts.erosion:
        log(f"Erosion ({opts.erosion} passes)")
        height = erode(height, opts.erosion, opts.talus * k)
    if opts.edits:
        height = shaping.apply_height_edits(height, k, opts)
    log("Rivers and lakes")
    _, water = add_water(height, k, opts)
    height = np.clip(np.round(height), -32000, 32000)
    log("Surface")
    types, data, rock = generate_surface(height, k, opts)
    if opts.edits:
        shaping.apply_surface_edits(types, data, height, k, opts, opts.seed)
    log("Spawn points")
    spawns = shaping.apply_spawn_edits(pick_spawns(types, height, k, opts.seed), opts, types, height, k)
    if opts.spawn_clearing:
        shaping.spawn_clearings(types, data, height, rock, spawns, k, opts.spawn_clearing)
    if opts.roads != "none":
        log(f"Roads ({opts.roads})")
        water["roads"] = shaping.build_roads(types, data, height, rock, spawns, k, opts.roads)
    log("Ores")
    ores = generate_ores(height, rock, k, opts)
    return SimpleNamespace(types=types, data=data, height=height, rock=rock, spawns=spawns, k=k, water=water,
                           ores=ores, world=world_size)


def stats(result):
    t, h = result.types, result.height
    tree = (t >= 100) | ((t <= -115) & (t >= -128)) | (t == ORANGE)
    bush = (t < -83) & (t > -115) & (t != ORANGE)
    groups = {
        "Water": h < 0,
        "Grass": t == GRASS,
        "Forest": tree,
        "Bushes": bush,
        "Sand": (t == SAND) & (h >= 0),
        "Rock & cliff": np.isin(t, (ROCK, CLIFF, GRAVEL)) & (h >= 0),
        "Tundra": t == TUNDRA,
        "Steppe": t == STEPPE,
        "Marsh & reed": np.isin(t, (MARSH, REED)) & (h >= 0),
        "Peat & moss": np.isin(t, (PEAT, MOSS)),
        "Snow": t == SNOW,
        "Roads": np.isin(t, (DIRT_PACKED, COBBLESTONE)),
    }
    n = t.size
    comp = {name: round(float(m.sum()) / n, 4) for name, m in groups.items()}
    ores = getattr(result, "ores", None)
    ore_counts = None
    if ores is not None:
        ids, counts = np.unique(ores.t, return_counts=True)
        ore_counts = {ORE_BY_ID[int(i)][1]: int(c) for i, c in sorted(zip(ids, counts), key=lambda ic: -ic[1])}
    return dict(land=round(float((h > 0).mean()), 4), peak=int(h.max()), deepest=int(h.min()), composition=comp,
                water=getattr(result, "water", None), ores=ore_counts)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

COLORS = {
    SAND: (214, 197, 140), GRASS: (96, 140, 62), ROCK: (128, 124, 118), DIRT: (120, 96, 70),
    CLAY: (176, 120, 90), GRAVEL: (150, 145, 135), PEAT: (70, 60, 45), TUNDRA: (150, 160, 130),
    MOSS: (90, 120, 70), CLIFF: (95, 90, 88), STEPPE: (165, 160, 95), MARSH: (80, 110, 85),
    TAR: (30, 30, 30), KELP: (40, 90, 70), REED: (110, 140, 80),
    DIRT_PACKED: (160, 125, 85), COBBLESTONE: (175, 170, 160), SNOW: (240, 243, 247),
}

HEIGHT_RAMP = [  # (dm, rgb)
    (-2000, (10, 25, 60)), (-300, (30, 70, 130)), (-1, (80, 140, 190)), (0, (60, 120, 70)),
    (400, (120, 160, 80)), (1200, (190, 175, 110)), (2500, (150, 115, 85)), (4000, (235, 235, 235)),
]


def render_rgb(types, height, mode="terrain", k=1.0, ores=None):
    if mode == "ores":
        return render_ores(types, height, k, ores)
    t = types.astype(np.int16)
    h = height.astype(np.float32)
    if mode == "height":
        stops = np.array([s[0] for s in HEIGHT_RAMP], dtype=np.float32)
        cols = np.array([s[1] for s in HEIGHT_RAMP], dtype=np.float32)
        rgb = np.stack([np.interp(h, stops, cols[:, i]) for i in range(3)], -1)
    else:
        rgb = np.zeros(t.shape + (3,), dtype=np.float32)
        rgb[:] = (96, 140, 62)
        for typ, c in COLORS.items():
            rgb[t == typ] = c
        tree = (t >= 100) | ((t <= -115) & (t >= -128)) | (t == ORANGE)
        rgb[tree] = (40, 85, 35)
        bush = (t < -83) & (t > -115) & (t != ORANGE)
        rgb[bush] = (70, 105, 45)
    # Hillshade from the north-west.
    gy, gx = np.gradient(h / k)
    shade = np.clip(1.0 + (-gx - gy) * 0.012, 0.55, 1.35)
    rgb *= shade[..., None]
    if mode != "height":
        depth = np.clip(-h / 600.0, 0, 1)[..., None]
        blue = (np.array([60, 110, 170], dtype=np.float32) * (1 - depth)
                + np.array([15, 35, 80], dtype=np.float32) * depth)
        rgb = np.where((h < 0)[..., None], rgb * 0.3 + blue * 0.7, rgb)
    return np.clip(rgb, 0, 255).astype(np.uint8)


def render_ores(types, height, k, ores):
    """Muted terrain with ore veins drawn on top in each ore's colour."""
    base = render_rgb(types, height, "terrain", k).astype(np.float32)
    grey = base.mean(axis=2, keepdims=True)
    img = np.repeat(grey * 0.45, 3, axis=2) + base * 0.1
    if ores is not None and len(ores.x):
        size = types.shape[0]
        sx = np.clip((ores.x / k).astype(np.int32), 0, size - 1)
        sy = np.clip((ores.y / k).astype(np.int32), 0, size - 1)
        # Draw common ores first so rare ones stay visible on top.
        order = np.argsort([-np.sum(ores.t == d[2]) for d in ORE_DEFS], kind="stable")
        for i in order:
            m = ores.t == ORE_DEFS[i][2]
            img[sy[m], sx[m]] = ORE_DEFS[i][4]
    return np.clip(img, 0, 255).astype(np.uint8)


def png_bytes(img):
    return pngio.write_rgb(img)


def render_png(top, path, scale=1, mode="terrain", ores=None):
    types, _, height = decode(top)
    if scale > 1:
        types, height = types[::scale, ::scale], height[::scale, ::scale]
    with open(path, "wb") as f:
        f.write(png_bytes(render_rgb(types, height, mode, scale, ores)))


# --------------------------------------------------------------------------
# World folder handling
# --------------------------------------------------------------------------

LAYERS = ("top_layer.map", "rock_layer.map", "map_cave.map", "resources.map", "flags.map", "protectedTiles.bmap")


def prepare_world(out_dir, template, force):
    if os.path.isdir(out_dir) and os.listdir(out_dir):
        existing = [f for f in LAYERS if os.path.exists(os.path.join(out_dir, f))]
        if existing and not force:
            raise FileExistsError(f"{out_dir} already has map files; use --force to replace them "
                                  "(old files are backed up).")
        if existing:
            backup = os.path.join(out_dir, "map-backup-" + time.strftime("%Y%m%d-%H%M%S"))
            os.makedirs(backup)
            for f in existing:
                shutil.move(os.path.join(out_dir, f), os.path.join(backup, f))
            log(f"Backed up old map files to {backup}")
    elif template:
        if not os.path.isfile(os.path.join(template, "sqlite", "wurmlogin.db")):
            raise FileNotFoundError(f"Template {template} does not look like a world folder "
                                    "(no sqlite/wurmlogin.db).")
        log(f"Copying template world {template}")
        shutil.copytree(template, out_dir, ignore=shutil.ignore_patterns(*LAYERS, "map-preview.png", "map-backup-*"),
                        dirs_exist_ok=True)
    else:
        os.makedirs(out_dir, exist_ok=True)


def update_spawns(out_dir, spawns, name):
    db = os.path.join(out_dir, "sqlite", "wurmlogin.db")
    if not os.path.isfile(db) or not spawns:
        return
    cols = ("JENN", "MOL", "LIB")
    sets, args = [], []
    for col, (x, y) in zip(cols, spawns):
        sets += [f"SPAWNPOINT{col}X=?", f"SPAWNPOINT{col}Y=?"]
        args += [x, y]
    if name:
        sets.append("MAPNAME=?")
        args.append(name)
    con = sqlite3.connect(db)
    try:
        with con:
            con.execute(f"UPDATE SERVERS SET {', '.join(sets)} WHERE LOCAL=1", args)
    finally:
        con.close()
    log("Spawn points set: " + ", ".join(f"{c}={p}" for c, p in zip(cols, spawns)))


# Items that ship on the ground in template worlds, positioned for the template's own map.
SOURCE_FOUNTAIN, SOURCE_SPRING, SOULFALL_STONE = 766, 767, 1016
TEMPLATE_ITEMS = (SOURCE_FOUNTAIN, SOURCE_SPRING, SOULFALL_STONE)


def spread_points(cand, count, avoid, rng):
    """Pick `count` of the candidate (x, y) rows, spread out and away from the `avoid` points."""
    if len(cand) == 0 or count == 0:
        return []
    cand = cand[rng.permutation(len(cand))[:6000]]
    d = np.full(len(cand), np.inf)
    for p in avoid:
        d = np.minimum(d, np.hypot(*(cand - p).T))
    picks = []
    for _ in range(count):
        i = int(np.argmax(d)) if np.isfinite(d).any() else int(rng.integers(len(cand)))
        picks.append(cand[i])
        d = np.minimum(d, np.hypot(*(cand - cand[i]).T))
    return picks


def relocate_template(out_dir, result, seed, keep_items=False):
    """Remove (or with keep_items, re-place) the template's ground items and drop its named zones.

    The Creative template has 40 source springs/fountains and 5 Stones of Soulfall at
    coordinates from its own map, plus six named focus zones ("The Meadow", ...). On a new
    map those would sit underwater or on cliffs, so they are deleted, or moved onto
    suitable land when keep_items is set.
    """
    items_db = os.path.join(out_dir, "sqlite", "wurmitems.db")
    zones_db = os.path.join(out_dir, "sqlite", "wurmzones.db")
    size = result.types.shape[0]
    h = result.height.astype(np.float32)
    lo, hi, slope = tile_slope(h)
    t = result.types.astype(np.int16)
    plants = (t >= 100) | ((t <= -84) & (t >= -128))
    land = (lo > 5) & (slope <= 30) & (t != CLIFF) & ~plants
    near_water = coarse_distance(hi <= 0, 40) <= 30
    rng = np.random.default_rng([seed, 3000])
    if os.path.isfile(items_db):
        con = sqlite3.connect(items_db)
        try:
            rows = con.execute(f"SELECT WURMID, TEMPLATEID FROM ITEMS WHERE TEMPLATEID IN "
                               f"({','.join(map(str, TEMPLATE_ITEMS))}) AND PARENTID=-10 AND OWNERID=-10"
                               ).fetchall()
            if rows and not keep_items:
                with con:
                    con.executemany("DELETE FROM ITEMS WHERE WURMID=?", [(wid,) for wid, _ in rows])
                log(f"Removed {len(rows)} template items (springs, fountains, Stones of Soulfall)")
                rows = []
            spawns = [np.array(p) for p in result.spawns]
            placed = {}
            groups = (
                # Springs by the water, fountains anywhere, Soulfall stones far apart.
                ((SOURCE_SPRING,), land & near_water),
                ((SOURCE_FOUNTAIN,), land),
                ((SOULFALL_STONE,), land),
            )
            updates = []
            for tids, mask in groups:
                ids = [wid for wid, tid in rows if tid in tids]
                if not ids:
                    continue
                if not mask.any():
                    mask = lo > 0
                ys, xs = np.nonzero(mask)
                cand = np.stack([xs, ys], 1)
                avoid = spawns + list(placed.values())
                for wid, (x, y) in zip(ids, spread_points(cand, len(ids), avoid, rng)):
                    placed[wid] = np.array([x, y])
                    z = float(h[y, x] + h[y, (x + 1) % size] + h[(y + 1) % size, x]
                              + h[(y + 1) % size, (x + 1) % size]) / 40
                    zone = (int(x) >> 6) * (size >> 6) + (int(y) >> 6)
                    updates.append((x * 4 + 2.0, y * 4 + 2.0, z, zone, wid))
            with con:
                con.executemany("UPDATE ITEMS SET POSX=?, POSY=?, POSZ=?, ZONEID=?, ONBRIDGE=-10 WHERE WURMID=?",
                                updates)
            if updates:
                n = {tid: sum(1 for _, t in rows if t == tid) for tid in TEMPLATE_ITEMS}
                log(f"Moved template items onto the new land: {n[SOURCE_SPRING] + n[SOURCE_FOUNTAIN]} "
                    f"springs/fountains, {n[SOULFALL_STONE]} Stones of Soulfall")
        finally:
            con.close()
    if os.path.isfile(zones_db):
        con = sqlite3.connect(zones_db)
        try:
            with con:
                gone = con.execute("DELETE FROM FOCUSZONES").rowcount
            if gone:
                log(f"Removed {gone} named areas that belonged to the template map")
            leftovers = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                         for t in ("VILLAGES", "STRUCTURES", "FENCES", "BRIDGEPARTS", "DENS")}
            left = ", ".join(f"{n} {t.lower()}" for t, n in leftovers.items() if n)
            if left:
                log(f"WARNING: the world database still has {left} placed for the old map")
        finally:
            con.close()


def create_world(opts, size, out_dir, template=None, force=False, name=None, preview=True,
                 template_items=False):
    """Generate a full-size map and write it (plus template databases) into out_dir."""
    if size < 256 or size & (size - 1):
        raise ValueError("size must be a power of two, at least 256")
    started = time.time()
    result = generate(opts, size)
    prepare_world(out_dir, template, force)
    log("Writing map files")
    top = encode(result.types, result.data, result.height)
    write_map(os.path.join(out_dir, "top_layer.map"), top)
    write_map(os.path.join(out_dir, "rock_layer.map"), encode(0, 0, result.rock))
    if result.ores is not None:
        log("Writing cave layer")
        write_map(os.path.join(out_dir, "map_cave.map"), cave_tiles(result.ores, size))
        if opts.ore_yield > 0:
            write_map(os.path.join(out_dir, "resources.map"), resource_tiles(result.ores, size, opts))
    update_spawns(out_dir, result.spawns, name)
    relocate_template(out_dir, result, opts.seed, template_items)
    report = playability.report(result, opts)
    playability.write(report, out_dir)
    for line in playability.summary_lines(report):
        log(line)
    if preview:
        path = os.path.join(out_dir, "map-preview.png")
        scale = max(1, size // 2048)
        rgb = render_rgb(result.types[::scale, ::scale], result.height[::scale, ::scale], "terrain", scale)
        with open(path, "wb") as f:
            f.write(png_bytes(rgb))
        log(f"Preview: {path}")
    s = stats(result)
    log(f"Done in {time.time() - started:.0f}s: land {s['land']:.0%}, highest {s['peak']} dm, "
        f"deepest {s['deepest']} dm, {s['water']['rivers']} rivers ({s['water']['river_tiles']} tiles), "
        f"{s['water']['lakes']} lakes, seed {opts.seed}")
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def default_template():
    return os.path.join(paths.DIST_DIR, "Creative")


def cmd_generate(args):
    try:
        preset = load_preset(args.preset) if args.preset else {}
        values = dict(preset.get("values", {}))
        values.update({p["name"]: getattr(args, p["name"]) for p in PARAMS if getattr(args, p["name"]) is not None})
        if args.heightmap:
            values["heightmap"] = os.path.abspath(args.heightmap)
        if args.heightmap_sea_level is not None:
            values["heightmap_sea"] = args.heightmap_sea_level
        style = args.style or preset.get("style", "continent")
        seed = args.seed if args.seed is not None else preset.get("seed")
        size = args.size or preset.get("world_size", 2048)
        opts = resolve_options(style, values, seed)
        create_world(opts, size, args.out, args.template, args.force, args.name, not args.no_preview,
                     args.template_items)
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        sys.exit(str(e))


def cmd_render(args):
    top = read_map(os.path.join(args.world, "top_layer.map"))
    out = args.out or os.path.join(args.world, "map-preview.png")
    ores = read_world_ores(args.world) if args.mode == "ores" else None
    render_png(top, out, args.scale or max(1, top.shape[0] // 2048), args.mode, ores)
    log(f"Wrote {out}")


def cmd_export_heightmap(args):
    top = read_map(os.path.join(args.world, "top_layer.map"))
    _, _, height = decode(top)
    out = args.out or os.path.join(args.world, "heightmap.png")
    with open(out, "wb") as f:
        f.write(shaping.export_heightmap(height))
    log(f"Wrote {out} ({height.shape[0]}x{height.shape[0]}, 16-bit; re-import with --heightmap)")


def cmd_player_map(args):
    img = playermap.render_world(args.world, args.size, args.grid, not args.no_spawns, args.title)
    out = args.out or os.path.join(args.world, "player-map.png")
    with open(out, "wb") as f:
        f.write(png_bytes(img))
    log(f"Wrote {out}")


def read_world_ores(world):
    """Ore tiles from a world's map_cave.map, in the form generate_ores returns."""
    path = os.path.join(world, "map_cave.map")
    if not os.path.isfile(path):
        return None
    t, _, _ = decode(read_map(path))
    t = t.astype(np.int16)
    ys, xs = np.nonzero(np.isin(t, list(ORE_BY_ID)))
    return SimpleNamespace(x=xs, y=ys, t=t[ys, xs])


def main(argv=None):
    p = argparse.ArgumentParser(description="Wurm Unlimited map generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate a new world map")
    g.add_argument("out", help="world folder to write, e.g. dist/MyWorld")
    g.add_argument("--preset", help="start from a preset saved in the UI (tools/mapgen/presets/NAME.json)")
    g.add_argument("--heightmap", help="use this heightmap instead of generating one: a PNG (8/16-bit grey or "
                                      "RGB), a .r16/.raw 16-bit file, or a world's top_layer.map")
    g.add_argument("--heightmap-sea-level", type=float,
                   help="grey level (0-1) that becomes the water line in an imported heightmap (default 0.3); "
                        "heights then scale to --max-height and --sea-depth")
    g.add_argument("--style", choices=list(STYLES),
                   help="map style (default: the preset's, else continent); settings below override it")
    g.add_argument("--size", type=int, help="map width in tiles (power of two, default 2048)")
    g.add_argument("--seed", type=int, help="random seed (default: the preset's, else time based)")
    for prm in PARAMS:
        flag = "--" + prm["name"].replace("_", "-")
        if prm.get("kind") == "choice":
            g.add_argument(flag, choices=prm["choices"], help=prm["help"].replace("%", "%%"))
        else:
            g.add_argument(flag, type=int if prm.get("kind") == "int" else float,
                           help=f"{prm['help'].replace('%', '%%')} ({prm['min']}-{prm['max']}, default {prm['default']} or style)")
    g.add_argument("--template", default=default_template(),
                   help="world folder to copy databases from when OUT is new ('' for maps only)")
    g.add_argument("--name", help="set MAPNAME in the copied server database")
    g.add_argument("--force", action="store_true", help="replace map files in an existing world (backs them up)")
    g.add_argument("--no-preview", action="store_true", help="skip writing map-preview.png")
    g.add_argument("--template-items", action="store_true",
                   help="keep the template's springs, fountains and Stones of Soulfall, moved onto the new "
                        "land (default: remove them)")
    g.set_defaults(func=cmd_generate)

    r = sub.add_parser("render", help="render an existing world's top layer to PNG")
    r.add_argument("world", help="world folder containing top_layer.map")
    r.add_argument("--out", help="output PNG (default: WORLD/map-preview.png)")
    r.add_argument("--scale", type=int, help="downsample factor")
    r.add_argument("--mode", choices=("terrain", "height", "ores"), default="terrain",
                   help="ores draws the ore veins from map_cave.map")
    r.set_defaults(func=cmd_render)

    e = sub.add_parser("export-heightmap", help="save a world's heights as a 16-bit PNG for editing")
    e.add_argument("world", help="world folder containing top_layer.map")
    e.add_argument("--out", help="output PNG (default: WORLD/heightmap.png)")
    e.set_defaults(func=cmd_export_heightmap)

    pm = sub.add_parser("player-map", help="render a printable map with grid, spawns and scale bar")
    pm.add_argument("world", help="world folder containing top_layer.map")
    pm.add_argument("--out", help="output PNG (default: WORLD/player-map.png)")
    pm.add_argument("--size", type=int, default=2048, help="map width in pixels (default 2048)")
    pm.add_argument("--grid", type=int, default=256, help="grid cell size in tiles; 0 = no grid (default 256)")
    pm.add_argument("--title", help="title text (default: the world folder name; '' for none)")
    pm.add_argument("--no-spawns", action="store_true", help="leave out the kingdom spawn markers")
    pm.set_defaults(func=cmd_player_map)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
