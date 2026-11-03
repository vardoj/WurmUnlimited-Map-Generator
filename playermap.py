"""Printable player maps: the terrain with a lettered grid, spawn markers, a title and a scale bar.

Text uses a small built-in bitmap font, so no imaging library is needed.
"""

import os
import sqlite3

import numpy as np

# 5x7 glyphs, one 5-character string per row ('#' = ink).
_GLYPHS = {
    'A': ('.###.', '#...#', '#...#', '#####', '#...#', '#...#', '#...#'),
    'B': ('####.', '#...#', '#...#', '####.', '#...#', '#...#', '####.'),
    'C': ('.###.', '#...#', '#....', '#....', '#....', '#...#', '.###.'),
    'D': ('####.', '#...#', '#...#', '#...#', '#...#', '#...#', '####.'),
    'E': ('#####', '#....', '#....', '####.', '#....', '#....', '#####'),
    'F': ('#####', '#....', '#....', '####.', '#....', '#....', '#....'),
    'G': ('.###.', '#...#', '#....', '#.###', '#...#', '#...#', '.####'),
    'H': ('#...#', '#...#', '#...#', '#####', '#...#', '#...#', '#...#'),
    'I': ('.###.', '..#..', '..#..', '..#..', '..#..', '..#..', '.###.'),
    'J': ('..###', '...#.', '...#.', '...#.', '...#.', '#..#.', '.##..'),
    'K': ('#...#', '#..#.', '#.#..', '##...', '#.#..', '#..#.', '#...#'),
    'L': ('#....', '#....', '#....', '#....', '#....', '#....', '#####'),
    'M': ('#...#', '##.##', '#.#.#', '#.#.#', '#...#', '#...#', '#...#'),
    'N': ('#...#', '##..#', '#.#.#', '#..##', '#...#', '#...#', '#...#'),
    'O': ('.###.', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'),
    'P': ('####.', '#...#', '#...#', '####.', '#....', '#....', '#....'),
    'Q': ('.###.', '#...#', '#...#', '#...#', '#.#.#', '#..#.', '.##.#'),
    'R': ('####.', '#...#', '#...#', '####.', '#.#..', '#..#.', '#...#'),
    'S': ('.####', '#....', '#....', '.###.', '....#', '....#', '####.'),
    'T': ('#####', '..#..', '..#..', '..#..', '..#..', '..#..', '..#..'),
    'U': ('#...#', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'),
    'V': ('#...#', '#...#', '#...#', '#...#', '#...#', '.#.#.', '..#..'),
    'W': ('#...#', '#...#', '#...#', '#.#.#', '#.#.#', '##.##', '#...#'),
    'X': ('#...#', '#...#', '.#.#.', '..#..', '.#.#.', '#...#', '#...#'),
    'Y': ('#...#', '#...#', '.#.#.', '..#..', '..#..', '..#..', '..#..'),
    'Z': ('#####', '....#', '...#.', '..#..', '.#...', '#....', '#####'),
    '0': ('.###.', '#...#', '#..##', '#.#.#', '##..#', '#...#', '.###.'),
    '1': ('..#..', '.##..', '..#..', '..#..', '..#..', '..#..', '.###.'),
    '2': ('.###.', '#...#', '....#', '...#.', '..#..', '.#...', '#####'),
    '3': ('#####', '...#.', '..#..', '...#.', '....#', '#...#', '.###.'),
    '4': ('...#.', '..##.', '.#.#.', '#..#.', '#####', '...#.', '...#.'),
    '5': ('#####', '#....', '####.', '....#', '....#', '#...#', '.###.'),
    '6': ('..##.', '.#...', '#....', '####.', '#...#', '#...#', '.###.'),
    '7': ('#####', '....#', '...#.', '..#..', '.#...', '.#...', '.#...'),
    '8': ('.###.', '#...#', '#...#', '.###.', '#...#', '#...#', '.###.'),
    '9': ('.###.', '#...#', '#...#', '.####', '....#', '...#.', '.##..'),
    '-': ('.....', '.....', '.....', '.###.', '.....', '.....', '.....'),
    ' ': ('.....', '.....', '.....', '.....', '.....', '.....', '.....'),
    '.': ('.....', '.....', '.....', '.....', '.....', '.##..', '.##..'),
    ',': ('.....', '.....', '.....', '.....', '.##..', '..#..', '.#...'),
    "'": ('..#..', '..#..', '.#...', '.....', '.....', '.....', '.....'),
    '&': ('.##..', '#..#.', '#.#..', '.#...', '#.#.#', '#..#.', '.##.#'),
    '_': ('.....', '.....', '.....', '.....', '.....', '.....', '#####'),
}
FONT = {c: np.array([[ch == "#" for ch in row] for row in rows]) for c, rows in _GLYPHS.items()}

SPAWN_STYLE = (("J", (47, 111, 214)), ("M", (201, 59, 47)), ("L", (123, 75, 191)))
PAPER = np.array([244, 239, 226], dtype=np.float32)
INK = np.array([40, 38, 34], dtype=np.float32)


def text_width(text, scale):
    return len(text) * 6 * scale - scale


def draw_text(img, text, x, y, scale, color, anchor="left"):
    if anchor == "center":
        x -= text_width(text, scale) // 2
    elif anchor == "right":
        x -= text_width(text, scale)
    for ch in text.upper():
        g = FONT.get(ch, FONT[" "])
        big = np.repeat(np.repeat(g, scale, 0), scale, 1)
        h, w = big.shape
        y0, x0 = max(0, y), max(0, x)
        y1, x1 = min(img.shape[0], y + h), min(img.shape[1], x + w)
        if y0 < y1 and x0 < x1:
            region = img[y0:y1, x0:x1]
            region[big[y0 - y:y1 - y, x0 - x:x1 - x]] = color
        x += 6 * scale


def _disc(img, cx, cy, r, color):
    y0, y1 = max(0, int(cy - r)), min(img.shape[0], int(cy + r) + 1)
    x0, x1 = max(0, int(cx - r)), min(img.shape[1], int(cx + r) + 1)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    img[y0:y1, x0:x1][(xx - cx) ** 2 + (yy - cy) ** 2 <= r * r] = color


def read_spawns(world_dir):
    db = os.path.join(world_dir, "sqlite", "wurmlogin.db")
    if not os.path.isfile(db):
        return []
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT SPAWNPOINTJENNX, SPAWNPOINTJENNY, SPAWNPOINTMOLX, SPAWNPOINTMOLY, "
                          "SPAWNPOINTLIBX, SPAWNPOINTLIBY FROM SERVERS WHERE LOCAL=1").fetchone()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [(row[i], row[i + 1]) for i in (0, 2, 4)] if row else []


def column_label(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def render(types, height, world, px=2048, grid=256, spawns=(), title=""):
    """Player map image (uint8 RGB). world = map width in tiles; types/height may be downsampled."""
    import wurm_mapgen as m
    step = max(1, types.shape[0] // px)
    t, h = types[::step, ::step], height[::step, ::step]
    k = world / t.shape[0]
    terrain = m.render_rgb(t, h, "terrain", k).astype(np.float32)
    # Soften towards paper for a printed look.
    terrain = terrain * 0.85 + PAPER * 0.15
    n = terrain.shape[0]
    fs = max(1, n // 512)          # font scale
    margin = 16 * fs + 7 * fs
    top = margin + (12 * fs if title else 0)
    bottom = margin + 10 * fs
    img = np.empty((n + top + bottom, n + 2 * margin, 3), dtype=np.float32)
    img[:] = PAPER
    img[top:top + n, margin:margin + n] = terrain
    scale = n / world  # pixels per tile

    if grid:
        lines = np.arange(0, world + 1, grid)
        for v in lines:
            p = int(round(v * scale))
            p = min(p, n - 1)
            img[top:top + n, margin + p] = img[top:top + n, margin + p] * 0.55 + INK * 0.45
            img[top + p, margin:margin + n] = img[top + p, margin:margin + n] * 0.55 + INK * 0.45
        for i, v in enumerate(lines[:-1]):
            c = int((v + grid / 2) * scale)
            draw_text(img, column_label(i), margin + c, top - 9 * fs, fs, INK, "center")
            draw_text(img, column_label(i), margin + c, top + n + 3 * fs, fs, INK, "center")
            draw_text(img, str(i + 1), margin - 3 * fs, top + c - 3 * fs, fs, INK, "right")
            draw_text(img, str(i + 1), margin + n + 3 * fs, top + c - 3 * fs, fs, INK)

    img[top - 1, margin - 1:margin + n + 1] = INK
    img[top + n, margin - 1:margin + n + 1] = INK
    img[top:top + n, margin - 1] = INK
    img[top:top + n, margin + n] = INK

    for (x, y), (letter, color) in zip(spawns, SPAWN_STYLE):
        cx, cy = margin + x * scale, top + y * scale
        _disc(img, cx, cy, 6 * fs, (255, 255, 255))
        _disc(img, cx, cy, 5 * fs, color)
        draw_text(img, letter, int(cx) + 1, int(cy - 3 * fs), fs, (255, 255, 255), "center")

    if title:
        draw_text(img, title, img.shape[1] // 2, 5 * fs, 2 * fs, INK, "center")

    # Scale bar and north marker along the bottom.
    bar_tiles = next((b for b in (2000, 1000, 500, 250, 100, 50) if b * scale <= n / 4), 50)
    bx, by = margin, top + n + 13 * fs
    img[by:by + 2 * fs, bx:bx + int(bar_tiles * scale)] = INK
    draw_text(img, f"{bar_tiles} TILES", bx + int(bar_tiles * scale) + 4 * fs, by - 2 * fs, fs, INK)
    draw_text(img, "N UP", margin + n, by - 2 * fs, fs, INK, "right")
    return np.clip(img, 0, 255).astype(np.uint8)


def render_world(world_dir, px=2048, grid=256, spawns=True, title=None):
    import wurm_mapgen as m
    top = m.read_map(os.path.join(world_dir, "top_layer.map"))
    types, _, height = m.decode(top)
    world = types.shape[0]
    if title is None:
        title = os.path.basename(os.path.normpath(world_dir))
    return render(types, height, world, px, grid, read_spawns(world_dir) if spawns else (), title)
