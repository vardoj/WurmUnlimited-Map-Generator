"""Ways to steer a map beyond the sliders: imported heightmaps, painted sketches, brush edits,
and starter roads and clearings around the kingdom spawns.

Everything here works in world-tile coordinates and scales with k (world tiles per sample),
so previews and full-size maps get the same result.
"""

import base64
import heapq
import math
import os

import numpy as np

import paths
import pngio

IMPORT_DIR = os.path.join(paths.TOOL_DIR, "imports")
EXACT_UNITS = "dm+32768"  # tEXt marker on heightmaps we export: value - 32768 = height in dm


# --------------------------------------------------------------------------
# Heightmap import and export
# --------------------------------------------------------------------------

_cache = {}


DIST = paths.DIST_DIR


def heightmap_path(src):
    """A file path, "world:NAME" for a world's top_layer.map, or the name of an upload in imports/."""
    if src.startswith("world:"):
        path = os.path.join(DIST, os.path.basename(src[6:]), "top_layer.map")
        if os.path.isfile(path):
            return path
        raise ValueError(f"world {src[6:]} has no map")
    if os.path.isfile(src):
        return src
    name = os.path.basename(src)
    path = os.path.join(IMPORT_DIR, name)
    if os.path.isfile(path):
        return path
    raise ValueError(f"heightmap not found: {src}")


def load_heightmap(src):
    """Returns (array, exact). exact=True means the values are heights in dm; otherwise 0..1."""
    import wurm_mapgen as m
    path = heightmap_path(src)
    key = (path, os.path.getmtime(path))
    if key in _cache:
        return _cache[key]
    ext = os.path.splitext(path)[1].lower()
    if ext == ".map":
        _, _, h = m.decode(m.read_map(path))
        out = (h.astype(np.float32), True)
    elif ext in (".r16", ".raw"):
        raw = np.fromfile(path, dtype="<u2")
        n = int(math.isqrt(len(raw)))
        if n * n != len(raw):
            raise ValueError("raw heightmaps must be square 16-bit little-endian files")
        out = (raw.reshape(n, n).astype(np.float32) / 65535, False)
    else:
        with open(path, "rb") as f:
            vals, text = pngio.read(f.read())
        if text.get("wurm-mapgen-units") == EXACT_UNITS:
            out = (np.round(vals * 65535) - 32768, True)
        else:
            out = (vals, False)
    if out[0].shape[0] != out[0].shape[1]:
        m.log(f"  heightmap is {out[0].shape[1]}x{out[0].shape[0]}; stretching it to a square map")
    _cache.clear()
    _cache[key] = out
    return out


def resize(a, n):
    """Resample a 2D array to n x n (block average when shrinking by a whole factor, else bilinear)."""
    h, w = a.shape
    if h == w == n:
        return a.astype(np.float32)
    if h % n == 0 and w % n == 0:
        return a.reshape(n, h // n, n, w // n).mean(axis=(1, 3)).astype(np.float32)
    ys = np.clip((np.arange(n) + 0.5) * h / n - 0.5, 0, h - 1)
    xs = np.clip((np.arange(n) + 0.5) * w / n - 0.5, 0, w - 1)
    y0, x0 = ys.astype(np.int32), xs.astype(np.int32)
    y1, x1 = np.minimum(y0 + 1, h - 1), np.minimum(x0 + 1, w - 1)
    fy, fx = (ys - y0)[:, None].astype(np.float32), (xs - x0)[None, :].astype(np.float32)
    rows = a[y0] * (1 - fy) + a[y1] * fy
    return (rows[:, x0] * (1 - fx) + rows[:, x1] * fx).astype(np.float32)


def imported_heights(size, opts):
    """Corner heights in dm from opts.heightmap."""
    arr, exact = load_heightmap(opts.heightmap)
    a = resize(arr, size)
    if exact:
        return a
    sl = min(max(opts.heightmap_sea, 0.0), 0.99)
    if sl <= 0:
        return 1 + a * opts.max_height
    return np.where(a >= sl, 1 + (a - sl) / (1 - sl) * opts.max_height,
                    (a - sl) / sl * opts.sea_depth).astype(np.float32)


def export_heightmap(height):
    """16-bit PNG bytes holding exact heights (dm + 32768), re-importable without loss."""
    return pngio.write_gray16(height.astype(np.int32) + 32768,
                              {"wurm-mapgen-units": EXACT_UNITS, "Software": "Wurm map generator"})


def save_upload(data, name_hint):
    """Store an uploaded heightmap in imports/ under a content hash; returns its name."""
    import hashlib
    ext = os.path.splitext(name_hint)[1].lower()
    if ext not in (".png", ".r16", ".raw", ".map"):
        raise ValueError("heightmaps must be .png, .r16/.raw or a Wurm .map file")
    os.makedirs(IMPORT_DIR, exist_ok=True)
    name = hashlib.sha1(data).hexdigest()[:16] + ext
    path = os.path.join(IMPORT_DIR, name)
    if not os.path.isfile(path):
        with open(path, "wb") as f:
            f.write(data)
    arr, exact = load_heightmap(name)  # validate now so errors show at upload
    return dict(name=name, width=int(arr.shape[1]), height=int(arr.shape[0]), exact=exact)


# --------------------------------------------------------------------------
# Sketches: a coarse painted grid of sea / land / mountains / lowland
# --------------------------------------------------------------------------

SKETCH_SEA, SKETCH_LAND, SKETCH_MOUNTAIN, SKETCH_LOWLAND = 1, 2, 3, 4


def _blur(a, radius):
    """Separable triangle blur that wraps nothing (edges clamp)."""
    r = max(1, int(radius))
    kern = np.concatenate([np.arange(1, r + 2), np.arange(r, 0, -1)]).astype(np.float32)
    kern /= kern.sum()
    p = np.pad(a, r, mode="edge")
    p = np.apply_along_axis(lambda v: np.convolve(v, kern, mode="valid"), 0, p)
    return np.apply_along_axis(lambda v: np.convolve(v, kern, mode="valid"), 1, p)


class Sketch:
    """Smooth fields sampled from the painted grid at any (u, v) in 0..1."""

    def __init__(self, encoded):
        raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
        g = int(math.isqrt(len(raw)))
        if g * g != len(raw) or g < 8:
            raise ValueError("bad sketch data")
        grid = raw.reshape(g, g)
        self.g = g
        landish = np.isin(grid, (SKETCH_LAND, SKETCH_MOUNTAIN, SKETCH_LOWLAND)).astype(np.float32)
        sea = (grid == SKETCH_SEA).astype(np.float32)
        r = g / 96
        self.fields = {
            "signed": _blur(landish - sea, r),
            "paint": _blur(landish + sea, r),
            "mountain": _blur((grid == SKETCH_MOUNTAIN).astype(np.float32), r * 1.5),
            "lowland": _blur((grid == SKETCH_LOWLAND).astype(np.float32), r * 1.5),
        }
        self.empty = not grid.any()

    def sample(self, name, u, v):
        f = self.fields[name]
        g = self.g
        x = np.clip(u * g - 0.5, 0, g - 1.001)
        y = np.clip(v * g - 0.5, 0, g - 1.001)
        x0, y0 = x.astype(np.int32), y.astype(np.int32)
        fx, fy = x - x0, y - y0
        return ((f[y0, x0] * (1 - fx) + f[y0, x0 + 1] * fx) * (1 - fy)
                + (f[y0 + 1, x0] * (1 - fx) + f[y0 + 1, x0 + 1] * fx) * fy)


def sketch_of(opts):
    if not getattr(opts, "sketch", None):
        return None
    s = Sketch(opts.sketch)
    return None if s.empty else s


def apply_sketch_land(h, paint, signed, land_share):
    """Pick the sea level from unpainted areas, then pull painted areas firmly to land or sea.
    Returns the field relative to sea level (positive = land)."""
    free = paint < 0.05
    sample = h[free][::5] if free.mean() > 0.05 else h[::4, ::4].ravel()
    sea = float(np.quantile(sample, 1.0 - land_share))
    land = h - sea
    amp = float(np.std(land)) or 1.0
    return land * (1 - paint) + signed * (0.35 * amp + 0.6 * np.abs(land))


# --------------------------------------------------------------------------
# Brush edits: {"op", "x", "y", "r", "s"[, "z" for flatten][, "k" for spawn]} in world tiles
# --------------------------------------------------------------------------

HEIGHT_OPS = ("raise", "lower", "flatten")
SURFACE_OPS = ("forest", "clear", "grass", "clay", "sand", "tar", "gravel")
MAX_EDITS = 20000


def _edits(opts, ops):
    return [e for e in (getattr(opts, "edits", None) or []) if e.get("op") in ops]


def _window(size, cx, cy, r):
    x0, x1 = max(0, int(cx - r) - 1), min(size, int(cx + r) + 2)
    y0, y1 = max(0, int(cy - r) - 1), min(size, int(cy + r) + 2)
    if x0 >= x1 or y0 >= y1:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    d = np.hypot(xx - cx, yy - cy) / r
    return (slice(y0, y1), slice(x0, x1)), d


def apply_height_edits(height, k, opts):
    """Raise, lower and flatten dabs. Runs before rivers are carved, so water follows the edits."""
    size = height.shape[0]
    for e in _edits(opts, HEIGHT_OPS):
        r = max(1.0, float(e["r"]) / k)
        win = _window(size, float(e["x"]) / k, float(e["y"]) / k, r)
        if win is None:
            continue
        sl, d = win
        w = np.clip(1 - d * d, 0, 1) ** 2
        s = float(min(max(e.get("s", 0.5), 0.0), 1.0))
        if e["op"] == "raise":
            height[sl] += w * s * 60
        elif e["op"] == "lower":
            height[sl] -= w * s * 60
        else:
            z = float(e.get("z", 0))
            height[sl] += w * min(1.0, s * 1.2) * (z - height[sl])
    return height


def apply_surface_edits(types, data, height, k, opts, seed):
    import wurm_mapgen as m
    edits = _edits(opts, SURFACE_OPS)
    if not edits:
        return
    size = types.shape[0]
    lo, hi, _ = m.tile_slope(height.astype(np.float32))
    rng = np.random.default_rng([seed, 4000])
    rocky = np.isin(types, (m.ROCK, m.CLIFF))
    paint = {"grass": m.GRASS, "clay": m.CLAY, "sand": m.SAND, "tar": m.TAR, "gravel": m.GRAVEL}
    for e in edits:
        r = max(0.6, float(e["r"]) / k)
        win = _window(size, float(e["x"]) / k - 0.5, float(e["y"]) / k - 0.5, r)
        if win is None:
            continue
        sl, d = win
        inside = (d <= 1) & ~rocky[sl]
        t, dt = types[sl], data[sl]
        if e["op"] == "clear":
            sel = inside & m.is_plant(t)
            t[sel], dt[sel] = m.GRASS, 0
        elif e["op"] == "forest":
            s = float(min(max(e.get("s", 0.5), 0.0), 1.0))
            ok = inside & (lo[sl] > 2) & np.isin(t, (m.GRASS, m.DIRT, m.STEPPE, m.TUNDRA, m.MOSS, m.PEAT))
            sel = ok & (rng.random(t.shape) < 0.15 + 0.6 * s)
            pool = np.array(m.TREES[1], dtype=np.int16)
            t[sel] = pool[rng.integers(0, len(pool), t.shape)][sel]
            dt[sel] = ((rng.integers(4, 14, t.shape) << 4) | rng.integers(0, 4, t.shape))[sel]
        else:
            sel = inside & (lo[sl] > (-80 if e["op"] in ("clay", "sand") else 0))
            t[sel], dt[sel] = paint[e["op"]], 0


SPAWN_SNAP = 12  # tiles a moved spawn may shift to reach dry, walkable ground


def apply_spawn_edits(spawns, opts, types, height, k):
    """Put moved spawns where the user dropped them, nudged onto the nearest dry, gentle,
    non-rock tile within SPAWN_SNAP tiles (left as dropped if there is none)."""
    import wurm_mapgen as m
    edits = _edits(opts, ("spawn",))
    if not edits:
        return list(spawns)
    spawns = list(spawns)
    size = types.shape[0]
    lo, _, slope = m.tile_slope(height.astype(np.float32))
    ok = (lo > 0) & (slope / k <= 25) & ~np.isin(types, (m.ROCK, m.CLIFF))
    for e in edits:
        i = int(e.get("k", 0))
        if not 0 <= i < 3:
            continue
        while len(spawns) <= i:
            spawns.append(spawns[-1] if spawns else (0, 0))
        x, y = float(e["x"]), float(e["y"])
        tx, ty = min(size - 1, max(0, int(x / k))), min(size - 1, max(0, int(y / k)))
        if not ok[ty, tx]:
            r = max(1, int(math.ceil(SPAWN_SNAP / k)))
            y0, x0 = max(0, ty - r), max(0, tx - r)
            ys, xs = np.nonzero(ok[y0:ty + r + 1, x0:tx + r + 1])
            if len(xs):
                j = int(np.argmin((xs + x0 - tx) ** 2 + (ys + y0 - ty) ** 2))
                tx, ty = int(xs[j] + x0), int(ys[j] + y0)
                x, y = tx * k + k / 2, ty * k + k / 2
        spawns[i] = (int(x), int(y))
    return spawns


def clean_edits(edits):
    """Validate brush edits from the UI or a preset."""
    out = []
    for e in (edits or [])[:MAX_EDITS]:
        op = e.get("op")
        if op not in HEIGHT_OPS + SURFACE_OPS + ("spawn",):
            continue
        item = {"op": op, "x": float(e["x"]), "y": float(e["y"])}
        if op != "spawn":
            item["r"] = min(max(float(e.get("r", 10)), 1.0), 400.0)
            item["s"] = min(max(float(e.get("s", 0.5)), 0.0), 1.0)
        if op == "flatten":
            item["z"] = float(e.get("z", 0))
        if op == "spawn":
            item["k"] = int(e.get("k", 0))
        out.append(item)
    return out


# --------------------------------------------------------------------------
# Starter roads and spawn clearings
# --------------------------------------------------------------------------

ROAD_GRID = 256  # routes are planned on a fixed grid so previews and full maps agree


def _protected_corners(types):
    import wurm_mapgen as m
    return m.corners_of(np.isin(types, (m.ROCK, m.CLIFF)))


def spawn_clearings(types, data, height, rock, spawns, k, radius):
    """Clear trees and gently level the ground in a disc around each spawn."""
    import wurm_mapgen as m
    if radius <= 0 or not spawns:
        return
    size = types.shape[0]
    keep = _protected_corners(types)
    for (x, y) in spawns:
        cx, cy = x / k, y / k
        r = max(1.5, radius / k)
        win = _window(size, cx, cy, r)
        if win is None:
            continue
        sl, d = win
        t, dt = types[sl], data[sl]
        sel = (d <= 1) & m.is_plant(t)
        t[sel], dt[sel] = m.GRASS, 0
        z = float(height[min(size - 1, int(cy)), min(size - 1, int(cx))])
        if z <= 5:
            continue
        w = (1 - m.smoothstep(0.55, 1.0, d)) * 0.9
        h = height[sl]
        new = np.where(keep[sl] | (h <= 0), h, np.maximum(1, h + w * (z - h)))
        changed = new != h
        height[sl] = np.round(new)
        rock[sl] = np.where(changed, np.minimum(rock[sl], height[sl] - 5), rock[sl])


def _route(cost, a, b):
    """Cheapest 8-connected path on the cost grid (inf = blocked); list of (x, y) or None."""
    g = cost.shape[0]
    dist = np.full(cost.shape, np.inf)
    prev = np.full(cost.shape, -1, dtype=np.int64)
    ax, ay = a
    dist[ay, ax] = 0
    heap = [(0.0, ay * g + ax)]
    goal = b[1] * g + b[0]
    steps = [(-1, -1, 1.414), (-1, 0, 1), (-1, 1, 1.414), (0, -1, 1), (0, 1, 1), (1, -1, 1.414), (1, 0, 1),
             (1, 1, 1.414)]
    while heap:
        dcur, i = heapq.heappop(heap)
        if i == goal:
            break
        y, x = divmod(i, g)
        if dcur > dist[y, x]:
            continue
        for dy, dx, step in steps:
            ny, nx = y + dy, x + dx
            if 0 <= ny < g and 0 <= nx < g:
                c = cost[ny, nx]
                if c == np.inf:
                    continue
                nd = dcur + step * (c + cost[y, x]) * 0.5
                if nd < dist[ny, nx]:
                    dist[ny, nx] = nd
                    prev[ny, nx] = i
                    heapq.heappush(heap, (nd, ny * g + nx))
    if not np.isfinite(dist[b[1], b[0]]):
        return None
    path, i = [], goal
    while i != -1:
        y, x = divmod(int(i), g)
        path.append((x, y))
        i = prev[y, x]
    return path[::-1]


def build_roads(types, data, height, rock, spawns, k, kind):
    """Connect the spawns with roads that avoid water, rock and steep ground."""
    import wurm_mapgen as m
    if kind == "none" or len(spawns) < 2:
        return 0
    road_type = {"dirt": m.DIRT_PACKED, "gravel": m.GRAVEL, "cobblestone": m.COBBLESTONE}[kind]
    size = types.shape[0]
    f = max(1, size // ROAD_GRID)
    g = size // f
    lo, hi, slope = m.tile_slope(height.astype(np.float32))
    slope = slope / k

    def cells(a, how):
        a = a.reshape(g, f, g, f)
        return a.max(axis=(1, 3)) if how == "max" else a.mean(axis=(1, 3))

    wet = cells(hi <= 2, "max") if f > 1 else hi <= 2
    rocky = cells(np.isin(types, (m.ROCK, m.CLIFF)).astype(np.float32), "mean")
    steep = cells(slope, "mean") if f > 1 else slope
    cost = 1 + (steep / 10) ** 2 + rocky * 40
    cost = np.where(wet, np.inf, cost)

    # Connect the spawns in order of distance (a minimum spanning tree of 2-3 points).
    pts = [(min(g - 1, int(x / k / f)), min(g - 1, int(y / k / f))) for x, y in spawns]
    pairs = sorted(((math.dist(pts[i], pts[j]), i, j) for i in range(len(pts)) for j in range(i + 1, len(pts))))
    group = list(range(len(pts)))
    built = 0
    keep = _protected_corners(types)
    for _, i, j in pairs:
        if group[i] == group[j]:
            continue
        for p in (pts[i], pts[j]):
            cost[p[1], p[0]] = min(cost[p[1], p[0]], 1.0) if np.isfinite(cost[p[1], p[0]]) else 1.0
        path = _route(cost, pts[i], pts[j])
        if path is None:
            m.log(f"  no road between spawns {i + 1} and {j + 1} (separated by water)")
            continue
        old = group[j]
        group = [group[i] if v == old else v for v in group]
        built += 1
        line = m.chaikin(np.array([((x + 0.5) * f, (y + 0.5) * f) for x, y in path], dtype=np.float64), 3)
        line, _, _ = m.resample(line, np.zeros(len(line)), 0.5)
        _pave(types, data, height, rock, line, k, road_type, keep, lo)
    return built


def _pave(types, data, height, rock, line, k, road_type, keep, lo):
    import wurm_mapgen as m
    size = types.shape[0]
    half = max(0.5, 1.5 / k)  # roads are about 3 tiles wide
    ix = np.clip(line[:, 0].astype(np.int32), 0, size - 1)
    iy = np.clip(line[:, 1].astype(np.int32), 0, size - 1)
    # Smooth the ground along the road so it climbs gently.
    prof = height[iy, ix].astype(np.float32)
    win = max(3, int(30 / k / 0.5))
    pad = np.pad(prof, win, mode="edge")
    smooth = np.convolve(pad, np.ones(2 * win + 1) / (2 * win + 1), mode="same")[win:-win]
    rr = int(math.ceil(half)) + 1
    for (x, y), z in zip(line, smooth):
        y0, y1 = max(0, int(y) - rr), min(size, int(y) + rr + 1)
        x0, x1 = max(0, int(x) - rr), min(size, int(x) + rr + 1)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d = np.hypot(xx + 0.5 - x, yy + 0.5 - y)
        sl = (slice(y0, y1), slice(x0, x1))
        on = (d <= half + 0.5) & (lo[sl] > 0) & ~np.isin(types[sl], (m.ROCK, m.CLIFF))
        types[sl][on], data[sl][on] = road_type, 0
        near = (d <= half + 1.5) & m.is_plant(types[sl])
        types[sl][near], data[sl][near] = m.GRASS, 0
        if z > 3:
            h = height[sl]
            w = np.clip(1 - d / (half + 2), 0, 1) * 0.8
            new = np.where(keep[sl] | (h <= 0), h, np.maximum(2, np.round(h + w * (z - h))))
            changed = new != h
            height[sl] = new
            rock[sl] = np.where(changed, np.minimum(rock[sl], new - 5), rock[sl])
