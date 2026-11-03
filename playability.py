"""Playability report for maps made by wurm_mapgen.

Checks what matters when a server goes live: how far each kingdom's spawn point is from
the basics (water, trees, clay, tar, sand, mineable rock, ore), how much land is flat
enough to build on, and which land can only be reached by boat. Works on previews and
full maps alike; all distances are in world tiles.
"""

import json
import math
import os

import numpy as np

KINGDOMS = ("Jenn-Kellon", "Mol-Rehan", "Horde of the Summoned")

# (key, label, good within, acceptable within) in tiles from a spawn point.
RESOURCES = [
    ("water", "Water", 40, 100),
    ("trees", "Trees", 40, 100),
    ("clay", "Clay", 150, 400),
    ("tar", "Tar", 250, 600),
    ("sand", "Sand", 120, 350),
    ("rock", "Mineable rock", 120, 350),
    ("peat", "Peat", 400, 900),
    ("moss", "Moss", 400, 900),
    ("iron", "Iron ore", 150, 400),
    ("copper", "Copper ore", 300, 700),
    ("tin", "Tin ore", 300, 700),
]
ORE_KEYS = {"iron": -34, "copper": -33, "tin": -30}
OPTIONAL = {"peat", "moss"}  # nice to have; a map without them is flagged once, not per spawn
FLAT_SLOPE = 10        # dm per tile: building without flattening
BUILD_SLOPE = 25       # dm per tile: buildable after a little flattening
FLAT_RADIUS = 40       # tiles around a spawn checked for village space
ISLAND_MIN_TILES = 400


def _points(mask, k):
    ys, xs = np.nonzero(mask)
    return (xs + 0.5) * k, (ys + 0.5) * k


def _nearest(px, py, x, y):
    """(distance, (x, y) of the nearest point), or (None, None) when there are no points."""
    if len(px) == 0:
        return None, None
    d2 = (px - x) ** 2 + (py - y) ** 2
    i = int(np.argmin(d2))
    return float(np.sqrt(d2[i])), (int(px[i]), int(py[i]))


# Shapes the UI draws on the map while a check is hovered, in world-tile coordinates.
def _line(a, b, label):
    return dict(type="line", a=[int(a[0]), int(a[1])], b=[int(b[0]), int(b[1])], label=label)


def _circle(at, r, label):
    return dict(type="circle", at=[int(at[0]), int(at[1])], r=int(r), label=label)


def _landmasses(land, k):
    """Label land at <= 512 samples wide; returns (labels, area of each label in tiles, factor)."""
    import wurm_mapgen as m
    size = land.shape[0]
    f = max(1, size // 512)
    small = land.reshape(size // f, f, size // f, f).mean(axis=(1, 3)) > 0.5 if f > 1 else land
    labels, sizes = m.label_components(small)
    return labels, sizes * (f * k) ** 2, f


def report(result, opts):
    import wurm_mapgen as m
    t, h, k = result.types, result.height, result.k
    lo, hi, slope = m.tile_slope(h.astype(np.float32))
    slope = slope / k
    land = lo > 0
    tree = (t >= 100) | ((t <= -115) & (t >= -128)) | (t == m.ORANGE)
    masks = {
        "water": hi <= 0,
        "trees": tree,
        "clay": t == m.CLAY,
        "tar": t == m.TAR,
        "sand": (t == m.SAND) & (hi > 0),
        "rock": np.isin(t, (m.ROCK, m.CLIFF)) & (hi > 0),
        "peat": t == m.PEAT,
        "moss": t == m.MOSS,
    }
    points = {key: _points(mask, k) for key, mask in masks.items()}
    ores = result.ores
    if ores is not None:
        for key, tid in ORE_KEYS.items():
            sel = ores.t == tid
            points[key] = (ores.x[sel] + 0.5, ores.y[sel] + 0.5)

    labels, areas, lf = _landmasses(land, k)
    main = int(np.argmax(areas)) + 1 if len(areas) else 0
    land_tiles = float(land.sum()) * k * k

    def landmass_at(x, y):
        ly = min(labels.shape[0] - 1, int(y / k / lf))
        lx = min(labels.shape[1] - 1, int(x / k / lf))
        return int(labels[ly, lx])

    checks, spawn_rows = [], []
    radius = int(math.ceil(FLAT_RADIUS / k))
    for name, (x, y) in zip(KINGDOMS, result.spawns):
        dist, nearest, problems, shapes = {}, {}, [], []
        for key, label, good, ok in RESOURCES:
            if key not in points:
                continue
            d, at = _nearest(*points[key], x, y)
            dist[label] = None if d is None else int(round(d))
            if at is not None:
                nearest[label] = list(at)
            if d is None and key in OPTIONAL:
                continue
            if d is None or d > ok:
                problems.append(("bad", f"{label} {'not on the map' if d is None else f'{int(d)} tiles away'}"))
            elif d > good:
                problems.append(("warn", f"{label} {int(d)} tiles away"))
            else:
                continue
            if at is not None:
                shapes.append(_line((x, y), at, f"{label} {int(d)}"))
        sx, sy = min(t.shape[1] - 1, int(x / k)), min(t.shape[0] - 1, int(y / k))
        if hi[sy, sx] <= 0:
            problems.append(("bad", "spawn point is in water"))
        elif t[sy, sx] in (m.ROCK, m.CLIFF):
            problems.append(("bad", "spawn point is on bare rock"))
        elif slope[sy, sx] > 25:
            problems.append(("warn", f"spawn point is on steep ground ({int(slope[sy, sx])} dm per tile)"))
        win =(slice(max(0, sy - radius), sy + radius + 1), slice(max(0, sx - radius), sx + radius + 1))
        wl = land[win]
        flat = float(((slope[win] <= FLAT_SLOPE) & wl).sum() / max(1, wl.sum()))
        if flat < 0.35:
            problems.append(("warn", f"only {flat:.0%} flat land within {FLAT_RADIUS} tiles"))
            shapes.append(_circle((x, y), FLAT_RADIUS, f"{flat:.0%} flat"))
        lm = landmass_at(x, y)
        spawn_rows.append(dict(name=name, x=x, y=y, landmass=lm, flat=round(flat, 2), distances=dist,
                               nearest=nearest))
        if problems:
            worst = "bad" if any(lv == "bad" for lv, _ in problems) else "warn"
            checks.append(dict(level=worst, text=f"{name} spawn: " + "; ".join(p for _, p in problems),
                               shapes=shapes))
        else:
            checks.append(dict(level="good", text=f"{name} spawn has every basic resource close by",
                               shapes=[_line((x, y), at, f"{lb} {dist[lb]}") for lb, at in nearest.items()]))

    if not result.spawns:
        checks.append(dict(level="bad", text="No flat grass found for spawn points"))

    # Village space.
    flat_share = float((land & (slope <= FLAT_SLOPE)).sum() / max(1, land.sum()))
    build_share = float((land & (slope <= BUILD_SLOPE)).sum() / max(1, land.sum()))
    checks.append(dict(level="good" if flat_share >= 0.3 else "warn",
                       text=f"{flat_share:.0%} of land is flat enough to build on as-is, "
                            f"{build_share:.0%} after light flattening"))

    # Islands and boat-only land.
    islands = [i + 1 for i, a in enumerate(areas) if a >= ISLAND_MIN_TILES and i + 1 != main]
    off_main = float(sum(areas[i - 1] for i in islands) / max(1.0, land_tiles))
    if islands:
        # Mark each boat-only island with a ring sized to its area.
        ys, xs = np.nonzero(labels)
        lab = labels[ys, xs]
        cnt = np.bincount(lab, minlength=len(areas) + 1).astype(np.float64)
        cx = np.bincount(lab, xs, minlength=len(areas) + 1) / np.maximum(cnt, 1)
        cy = np.bincount(lab, ys, minlength=len(areas) + 1) / np.maximum(cnt, 1)
        cell = lf * k
        rings = [_circle(((cx[i] + 0.5) * cell, (cy[i] + 0.5) * cell), max(12, math.sqrt(areas[i - 1] / math.pi)),
                         "") for i in islands]
        checks.append(dict(level="info", text=f"{len(islands)} island{'s' if len(islands) != 1 else ''} "
                                              f"reachable only by boat ({off_main:.0%} of the land)",
                           shapes=rings))
    spawn_lms = {row["landmass"] for row in spawn_rows}
    pairs = [(a, b) for i, a in enumerate(result.spawns) for b in result.spawns[i + 1:]]
    if len(spawn_lms) > 1:
        checks.append(dict(level="warn", text="Kingdom spawns are on different landmasses; players need boats "
                                              "to meet",
                           shapes=[_line(ra, rb, "") for ia, ra in enumerate(result.spawns)
                                   for ib, rb in enumerate(result.spawns)
                                   if ia < ib and spawn_rows[ia]["landmass"] != spawn_rows[ib]["landmass"]]))

    # Spawn spacing.
    if len(result.spawns) > 1:
        gaps = [math.dist(a, b) for a, b in pairs]
        checks.append(dict(level="info" if min(gaps) >= 300 else "warn",
                           text=f"Spawns are {int(min(gaps))}-{int(max(gaps))} tiles apart",
                           shapes=[_line(a, b, str(int(math.dist(a, b)))) for a, b in pairs]))

    # Resources across the whole map.
    for key, label in (("clay", "Clay"), ("tar", "Tar"), ("peat", "Peat"), ("moss", "Moss")):
        n = int(masks[key].sum() * k * k)
        if n == 0:
            checks.append(dict(level="bad" if key in ("clay", "tar") else "warn",
                               text=f"No {label.lower()} anywhere on the map"))

    ore_counts = None
    if ores is None:
        checks.append(dict(level="info", text="Ores will be scattered randomly by the server on first start"))
    else:
        ids, counts = np.unique(ores.t, return_counts=True)
        ore_counts = {m.ORE_BY_ID[int(i)][1]: int(c) for i, c in zip(ids, counts)}
        if not ore_counts.get("Iron"):
            checks.append(dict(level="bad", text="No iron ore on the map"))
        if not ore_counts.get("Tin") or not ore_counts.get("Copper"):
            checks.append(dict(level="warn", text="Missing tin or copper ore, so no bronze"))

    water = getattr(result, "water", None) or {}
    metrics = dict(
        world_size=result.world, land_tiles=int(land_tiles), flat_share=round(flat_share, 3),
        buildable_share=round(build_share, 3), islands=len(islands), boat_only_share=round(off_main, 3),
        rivers=water.get("rivers"), river_tiles=water.get("river_tiles"), lakes=water.get("lakes"),
        resource_tiles={key: int(mask.sum() * k * k) for key, mask in masks.items() if key not in ("water", "trees")},
        ores=ore_counts,
    )
    order = {"bad": 0, "warn": 1, "info": 2, "good": 3}
    checks.sort(key=lambda c: order[c["level"]])
    return dict(seed=opts.seed, style=opts.style, checks=checks, spawns=spawn_rows, metrics=metrics,
                flat_radius=FLAT_RADIUS)


def summary_lines(rep):
    bad = sum(c["level"] == "bad" for c in rep["checks"])
    warn = sum(c["level"] == "warn" for c in rep["checks"])
    lines = [f"Playability: {bad} problem{'s' if bad != 1 else ''}, {warn} warning{'s' if warn != 1 else ''}"]
    lines += [f"  [{c['level']}] {c['text']}" for c in rep["checks"] if c["level"] in ("bad", "warn")]
    return lines


def write(rep, out_dir):
    with open(os.path.join(out_dir, "map-report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    marks = {"bad": "XX", "warn": "!!", "info": "--", "good": "ok"}
    lines = [f"Wurm map playability report - style {rep['style']}, seed {rep['seed']}", ""]
    lines += [f"[{marks[c['level']]}] {c['text']}" for c in rep["checks"]]
    lines += ["", "Distance from each spawn point (tiles):"]
    labels = [label for _, label, _, _ in RESOURCES if any(label in s["distances"] for s in rep["spawns"])]
    for s in rep["spawns"]:
        lines.append(f"  {s['name']} at {s['x']},{s['y']}  (flat land nearby: {s['flat']:.0%})")
        lines.append("    " + ", ".join(f"{lb} {s['distances'].get(lb, '-')}" for lb in labels))
    mt = rep["metrics"]
    lines += ["", f"Land: {mt['land_tiles']} tiles, {mt['flat_share']:.0%} flat, {mt['buildable_share']:.0%} buildable",
              f"Rivers: {mt['rivers']} ({mt['river_tiles']} tiles), lakes: {mt['lakes']}, "
              f"boat-only islands: {mt['islands']}",
              "Resource tiles: " + ", ".join(f"{k} {v}" for k, v in mt["resource_tiles"].items())]
    if mt["ores"]:
        lines.append("Ore tiles: " + ", ".join(f"{k} {v}" for k, v in sorted(mt["ores"].items(), key=lambda kv: -kv[1])))
    with open(os.path.join(out_dir, "map-report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
