"""Data for the 3D map viewer: terrain chunks, plus the Wurm client's own textures and models.

The client's art is read at runtime from your own Wurm Unlimited install (packs/graphics.jar)
and never copied into this tool. Without a client install the viewer falls back to plain
colours and simple tree shapes.
"""

import base64
import os
import struct
import threading
import zipfile

import numpy as np

import paths
import pngio

STEAM_COMMON = paths.STEAM_COMMON
CLIENT_GRAPHICS = os.environ.get("WURM_CLIENT_GRAPHICS") or os.path.join(
    STEAM_COMMON, "Wurm Unlimited", "WurmLauncher", "packs", "graphics.jar")
CHUNK = 64  # samples per terrain chunk side

# Ground texture resource per tile type (from com.wurmonline.mesh.Tiles$Tile.getTextureResource).
# Keys are the tile type as an unsigned byte.
TILE_TEXTURES = {
    1: "img.texture.terrain.sand", 2: "img.texture.terrain.grass", 4: "img.texture.terrain.rock",
    5: "img.texture.terrain.dirt", 6: "img.texture.terrain.clay", 8: "img.texture.terrain.dirt.packed",
    9: "img.texture.terrain.cobblestone", 17: "img.texture.terrain.gravel", 18: "img.texture.terrain.peat",
    19: "img.texture.terrain.tundra", 20: "img.texture.terrain.moss", 21: "img.texture.terrain.rock.cliff",
    22: "img.texture.terrain.steppe", 23: "img.texture.terrain.marsh", 24: "img.texture.terrain.tar",
    30: "img.texture.terrain.grass.winter", 32: "img.texture.terrain.grass.kelp",
    33: "img.texture.terrain.grass.reed",
}
# Tree and bush models (Tiles$Tile.getModelResourceName). Lingonberry grows on tundra.
TREE_MODELS = {
    100: "model.tree.birch", 101: "model.tree.pine", 102: "model.tree.oak", 103: "model.tree.cedar",
    104: "model.tree.willow", 105: "model.tree.maple", 106: "model.tree.apple", 107: "model.tree.lemon",
    108: "model.tree.olive", 109: "model.tree.cherry", 110: "model.tree.chestnut", 111: "model.tree.walnut",
    112: "model.tree.fir", 113: "model.tree.linden", 163: "model.tree.orange",
    142: "model.bush.lavendel", 143: "model.bush.rose", 144: "model.bush.thorn", 145: "model.bush.grape",
    146: "model.bush.camellia", 147: "model.bush.oleander", 160: "model.bush.hazelnut",
    166: "model.bush.raspberry", 169: "model.bush.blueberry", 172: "model.bush.lingonberry",
}
BUSHES = {142, 143, 144, 145, 146, 147, 160, 166, 169, 172}
UNDER_PLANT = {172: "img.texture.terrain.tundra"}  # ground under a plant tile; default grass
WATER_TEXTURE = "img.texture.terrain.water"
WATER_NORMALS = "texture/waterDetail_n.png"


# --------------------------------------------------------------------------
# The client's graphics pack
# --------------------------------------------------------------------------

class ClientArt:
    def __init__(self, path=CLIENT_GRAPHICS):
        self.path = path
        self.lock = threading.Lock()
        self.zip = None
        self.mappings = {}
        self.cache = {}
        if os.path.isfile(path):
            self.zip = zipfile.ZipFile(path)
            names = set(self.zip.namelist())
            self.names = names
            text = self.zip.read("mappings.txt").decode("latin-1") if "mappings.txt" in names else ""
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    self.mappings[k.strip()] = v.strip()

    @property
    def available(self):
        return self.zip is not None

    def read(self, name):
        with self.lock:
            return self.zip.read(name)

    def resolve(self, key):
        """Resource key (img.texture..., model....) or a plain path -> file path in the pack."""
        path = self.mappings.get(key, key)
        return path if path in self.names else None

    def texture_rgba(self, key, size):
        """Decoded RGBA image for a texture key or path, at the mip level nearest `size`."""
        ck = ("tex", key, size)
        if ck not in self.cache:
            path = self.resolve(key)
            if path is None:
                raise KeyError(key)
            data = self.read(path)
            if path.lower().endswith(".dds"):
                img = decode_dds(data, size)
            else:
                vals = _png_rgba(data)
                img = _shrink(vals, size)
            self.cache[ck] = img
        return self.cache[ck]

    def model(self, key):
        ck = ("model", key)
        if ck not in self.cache:
            path = self.resolve(key)
            if path is None:
                raise KeyError(key)
            meshes = parse_wom(self.read(path))
            folder = path.rsplit("/", 1)[0]
            for mesh in meshes:
                tex = mesh.pop("texture")
                if tex:
                    cand = tex if "/" in tex else f"{folder}/{tex}"
                    mesh["texture"] = cand if cand in self.names else self.resolve(tex)
            self.cache[ck] = meshes
        return self.cache[ck]


_art = None


def art():
    global _art
    if _art is None:
        _art = ClientArt()
    return _art


# --------------------------------------------------------------------------
# DDS (DXT1/DXT3/DXT5 and uncompressed) decoding
# --------------------------------------------------------------------------

def _rgb565(c):
    r = ((c >> 11) & 31).astype(np.float32) * (255 / 31)
    g = ((c >> 5) & 63).astype(np.float32) * (255 / 63)
    b = (c & 31).astype(np.float32) * (255 / 31)
    return np.stack([r, g, b], -1)


def _unblock(px, nby, nbx):
    """(nby, nbx, 16, 4) block pixels -> (nby*4, nbx*4, 4) image."""
    return px.reshape(nby, nbx, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(nby * 4, nbx * 4, 4)


def _dxt_colour(blk, nby, nbx, four_colour):
    c0 = blk[..., 0].astype(np.uint16) | (blk[..., 1].astype(np.uint16) << 8)
    c1 = blk[..., 2].astype(np.uint16) | (blk[..., 3].astype(np.uint16) << 8)
    bits = (blk[..., 4].astype(np.uint32) | (blk[..., 5].astype(np.uint32) << 8)
            | (blk[..., 6].astype(np.uint32) << 16) | (blk[..., 7].astype(np.uint32) << 24))
    p0, p1 = _rgb565(c0), _rgb565(c1)
    pal = np.zeros((nby, nbx, 4, 4), dtype=np.float32)
    pal[..., 0, :3], pal[..., 1, :3] = p0, p1
    pal[..., :2, 3] = 255
    gt = (c0 > c1)[..., None] | four_colour
    pal[..., 2, :3] = np.where(gt, (2 * p0 + p1) / 3, (p0 + p1) / 2)
    pal[..., 3, :3] = np.where(gt, (p0 + 2 * p1) / 3, 0)
    pal[..., 2, 3] = 255
    pal[..., 3, 3] = np.where(gt[..., 0], 255, 0)
    idx = ((bits[..., None] >> (2 * np.arange(16, dtype=np.uint32))) & 3).astype(np.int64)
    return np.take_along_axis(pal, idx[..., None].repeat(4, -1), axis=2)


def decode_dds(data, want=None):
    """Decode a DDS file to an RGBA uint8 array, picking the largest mip level <= want."""
    if data[:4] != b"DDS ":
        raise ValueError("not a DDS file")
    height, width = struct.unpack_from("<II", data, 12)
    mips = max(1, struct.unpack_from("<I", data, 28)[0])
    pf_flags, fourcc, bits = struct.unpack_from("<I4sI", data, 80)
    masks = struct.unpack_from("<4I", data, 92)
    off = 128 + (20 if fourcc == b"DX10" else 0)
    compressed = bool(pf_flags & 4)
    bsize = {b"DXT1": 8, b"DXT3": 16, b"DXT5": 16}.get(fourcc) if compressed else None
    if compressed and bsize is None:
        raise ValueError(f"unsupported DDS format {fourcc!r}")
    w, h = width, height
    for level in range(mips):
        if compressed:
            nbx, nby = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
            n = nbx * nby * bsize
        else:
            n = w * h * bits // 8
        last = level == mips - 1
        if want is None or (w <= want and h <= want) or last:
            raw = np.frombuffer(data, dtype=np.uint8, count=n, offset=off)
            break
        off += n
        w, h = max(1, w // 2), max(1, h // 2)
    if not compressed:
        px = raw.reshape(h, w, bits // 8).astype(np.uint32)
        val = sum(px[..., i] << (8 * i) for i in range(bits // 8))
        out = np.zeros((h, w, 4), dtype=np.uint8)
        for ch, mask in enumerate(masks):
            if mask:
                shift = (mask & -mask).bit_length() - 1
                out[..., ch] = ((val & mask) >> shift) * 255 // (mask >> shift)
            elif ch == 3:
                out[..., 3] = 255
        return out
    blk = raw.reshape(nby, nbx, bsize)
    if bsize == 8:
        px = _dxt_colour(blk, nby, nbx, False)
    else:
        px = _dxt_colour(blk[..., 8:], nby, nbx, True)
        if fourcc == b"DXT3":
            a = blk[..., :8]
            nib = np.stack([a & 15, a >> 4], -1).reshape(nby, nbx, 16)
            px[..., 3] = nib.astype(np.float32) * 17
        else:
            a0, a1 = blk[..., 0].astype(np.float32), blk[..., 1].astype(np.float32)
            ab = np.zeros((nby, nbx), dtype=np.uint64)
            for i in range(6):
                ab |= blk[..., 2 + i].astype(np.uint64) << np.uint64(8 * i)
            idx = ((ab[..., None] >> (3 * np.arange(16, dtype=np.uint64))) & np.uint64(7)).astype(np.int64)
            t = np.arange(8, dtype=np.float32)
            big = (a0 > a1)[..., None]
            pal8 = np.where(big,
                            np.concatenate([a0[..., None], a1[..., None],
                                            ((7 - t[2:]) * a0[..., None] + (t[2:] - 1) * a1[..., None]) / 7], -1),
                            np.concatenate([a0[..., None], a1[..., None],
                                            ((5 - t[2:6]) * a0[..., None] + (t[2:6] - 1) * a1[..., None]) / 5,
                                            np.zeros((nby, nbx, 1), np.float32),
                                            np.full((nby, nbx, 1), 255, np.float32)], -1))
            px[..., 3] = np.take_along_axis(pal8, idx, axis=2)
    img = _unblock(np.clip(px + 0.5, 0, 255).astype(np.uint8), nby, nbx)
    return img[:h, :w]


def _png_rgba(data):
    return pngio.read_rgba(data)


def _shrink(img, size):
    while img.shape[0] > size and img.shape[0] % 2 == 0:
        img = img.reshape(img.shape[0] // 2, 2, img.shape[1] // 2, 2, 4).mean(axis=(1, 3)).astype(np.uint8)
    return img


def _fit(img, size):
    """Exactly size x size (nearest-neighbour resample; textures are already near the size)."""
    if img.shape[0] == size and img.shape[1] == size:
        return img
    ys = (np.arange(size) * img.shape[0] // size)
    xs = (np.arange(size) * img.shape[1] // size)
    return img[ys][:, xs]


# --------------------------------------------------------------------------
# WOM models (Wurm's model format)
# --------------------------------------------------------------------------

def parse_wom(d):
    """Static meshes from a .wom file: positions, normals, uvs, indices, texture name."""
    p = 0

    def i32():
        nonlocal p
        v = struct.unpack_from("<i", d, p)[0]
        p += 4
        return v

    def u8():
        nonlocal p
        v = d[p]
        p += 1
        return v

    def text():
        nonlocal p
        n = i32()
        v = d[p:p + n].decode("latin-1")
        p += n
        return v

    meshes = []
    for _ in range(i32()):
        tan, bi, col = u8(), u8(), u8()
        name = text()
        nv = i32()
        stride = 8 + 3 * (col + tan + bi)
        v = np.frombuffer(d, dtype="<f4", count=nv * stride, offset=p).reshape(nv, stride)
        p += nv * stride * 4
        ni = i32()
        idx = np.frombuffer(d, dtype="<u2", count=ni, offset=p)
        p += ni * 2
        tex = None
        for _ in range(i32()):
            t = text()
            text()
            tex = tex or t
            if u8():
                if u8():
                    p += 16
                if u8():
                    p += 4
                if u8():
                    p += 16
                if u8():
                    p += 16
        meshes.append(dict(name=name, texture=tex, positions=v[:, 0:3].copy(), normals=v[:, 3:6].copy(),
                           uvs=v[:, 6:8].copy(), indices=idx.copy()))
    return meshes


def model_json(type_byte):
    """Mesh data for a tree/bush tile type, as JSON-friendly base64 arrays."""
    meshes = art().model(TREE_MODELS[type_byte])
    out = []
    for m in meshes:
        if "fruit" in m["name"].lower():
            continue  # the client only shows fruit in season
        out.append(dict(
            name=m["name"], texture=m.get("texture"),
            positions=base64.b64encode(m["positions"].astype("<f4").tobytes()).decode(),
            normals=base64.b64encode(m["normals"].astype("<f4").tobytes()).decode(),
            uvs=base64.b64encode(m["uvs"].astype("<f4").tobytes()).decode(),
            indices=base64.b64encode(m["indices"].astype("<u2").tobytes()).decode()))
    return dict(type=type_byte, bush=type_byte in BUSHES, meshes=out)


# --------------------------------------------------------------------------
# Ground texture atlas
# --------------------------------------------------------------------------

def layer_keys():
    keys = ["img.texture.terrain.grass"]
    for k in list(TILE_TEXTURES.values()) + list(UNDER_PLANT.values()):
        if k not in keys:
            keys.append(k)
    return keys


def tile_layers():
    """Unsigned tile byte -> atlas layer index (plants use the ground under them)."""
    keys = layer_keys()
    out = {t: keys.index(k) for t, k in TILE_TEXTURES.items()}
    for t in TREE_MODELS:
        out[t] = keys.index(UNDER_PLANT.get(t, "img.texture.terrain.grass"))
    return out


def atlas_png(size, colors, use_client=True):
    """All ground textures stacked vertically (layer i at rows i*size..). Uses plain colours with
    a little noise when the client isn't installed (or use_client is False)."""
    keys = layer_keys()
    a = art()
    rng = np.random.default_rng(7)
    layers = []
    for i, key in enumerate(keys):
        img = None
        if a.available and use_client:
            try:
                img = _fit(a.texture_rgba(key, size), size)
            except (KeyError, ValueError):
                img = None
        if img is None:
            t = next((t for t, k in TILE_TEXTURES.items() if k == key), 2)
            base = np.array(colors.get(t, (96, 140, 62)), dtype=np.float32)
            noise = rng.normal(1, 0.06, (size, size, 1)).astype(np.float32)
            img = np.concatenate([np.clip(base * noise, 0, 255), np.full((size, size, 1), 255, np.float32)],
                                 -1).astype(np.uint8)
        img = img.copy()
        img[..., 3] = 255
        layers.append(img)
    return pngio.write_rgba(np.concatenate(layers, 0)), len(keys)


# --------------------------------------------------------------------------
# Terrain chunks
# --------------------------------------------------------------------------

_MASK48 = (1 << 48) - 1


def _java_float(seed):
    """java.util.Random(seed).nextFloat() for an int64 array of seeds."""
    s = (seed.astype(np.uint64) ^ np.uint64(0x5DEECE66D)) & np.uint64(_MASK48)
    s = (s * np.uint64(0x5DEECE66D) + np.uint64(0xB)) & np.uint64(_MASK48)
    return (s >> np.uint64(24)).astype(np.float64) / float(1 << 24)


def tree_offsets(tx, ty):
    """Where the client puts a tree inside its tile (TerrainUtilities.getTreePosX/Y)."""
    tx64, ty64 = tx.astype(np.int64), ty.astype(np.int64)
    with np.errstate(over="ignore"):
        sx = tx64 * np.int64(31273612) + ty64 * np.int64(4327864168313)
        sy = tx64 * np.int64(31273612) + ty64 * np.int64(4327864168314)
    return _java_float(sx) * 0.75 + 0.125, _java_float(sy) * 0.75 + 0.125


def chunk_bytes(src, cx, cy):
    """Binary chunk: int32 n; int16 heights (n+3)^2 for corners -1..n+1; uint8 tile types
    (n+2)^2 for tiles -1..n; int32 tree count; trees as float32 x, float32 y (world tiles),
    uint8 type, uint8 age, 2 bytes padding."""
    types, data, height, k = src["types"], src["data"], src["height"], src["k"]
    size = types.shape[0]
    n = min(CHUNK, size)
    x0, y0 = cx * n, cy * n
    if not (0 <= x0 < size and 0 <= y0 < size):
        raise ValueError("chunk out of range")
    cyi = np.clip(np.arange(y0 - 1, y0 + n + 2), 0, size - 1)
    cxi = np.clip(np.arange(x0 - 1, x0 + n + 2), 0, size - 1)
    h = height[np.ix_(cyi, cxi)].astype("<i2")
    tyi, txi = cyi[:n + 2], cxi[:n + 2]
    t = (types[np.ix_(tyi, txi)].astype(np.int16) & 0xFF).astype(np.uint8)
    inner = t[1:-1, 1:-1]
    plant = np.isin(inner, list(TREE_MODELS))
    ys, xs = np.nonzero(plant)
    gx, gy = xs + x0, ys + y0
    dd = data[gy, gx].astype(np.int32)
    ox, oy = tree_offsets(gx * int(k), gy * int(k)) if k == int(k) else tree_offsets(gx, gy)
    centre = ((dd >> 2) & 1) == 1
    ox, oy = np.where(centre, 0.5, ox), np.where(centre, 0.5, oy)
    trees = np.zeros(len(xs), dtype=[("x", "<f4"), ("y", "<f4"), ("t", "u1"), ("a", "u1"), ("pad", "u2")])
    trees["x"] = (gx + ox) * k
    trees["y"] = (gy + oy) * k
    trees["t"] = inner[ys, xs]
    trees["a"] = (dd >> 4) & 15
    return (struct.pack("<i", n) + h.tobytes() + t.tobytes() + struct.pack("<i", len(trees)) + trees.tobytes())
