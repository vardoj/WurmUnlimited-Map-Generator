"""Minimal PNG reading and writing with numpy only (no Pillow needed).

Reads 8- and 16-bit greyscale, grey+alpha, RGB and RGBA images (non-interlaced), which covers
heightmaps saved by GIMP, Photoshop, Krita, World Machine, Gaea and similar tools. Writes
8-bit RGB images and 16-bit greyscale heightmaps.
"""

import struct
import zlib

import numpy as np

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}


def _chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def write_rgb(img):
    """PNG bytes for an (h, w, 3) uint8 image."""
    hgt, wid = img.shape[:2]
    raw = b"".join(b"\0" + img[y].tobytes() for y in range(hgt))
    return (SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", wid, hgt, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))


def write_rgba(img):
    """PNG bytes for an (h, w, 4) uint8 image."""
    hgt, wid = img.shape[:2]
    rows = np.zeros((hgt, 1 + wid * 4), dtype=np.uint8)
    rows[:, 1:] = img.reshape(hgt, wid * 4)
    return (SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", wid, hgt, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(rows.tobytes(), 6)) + _chunk(b"IEND", b""))


def write_gray16(values, text=None):
    """PNG bytes for an (h, w) array of 0..65535 values, with optional tEXt entries."""
    v = np.clip(values, 0, 65535).astype(">u2")
    hgt, wid = v.shape
    rows = np.empty((hgt, 1 + wid * 2), dtype=np.uint8)
    rows[:, 0] = 0
    rows[:, 1:] = v.view(np.uint8).reshape(hgt, wid * 2)
    out = SIGNATURE + _chunk(b"IHDR", struct.pack(">IIBBBBB", wid, hgt, 16, 0, 0, 0, 0))
    for key, val in (text or {}).items():
        out += _chunk(b"tEXt", key.encode("latin-1") + b"\0" + str(val).encode("latin-1"))
    return out + _chunk(b"IDAT", zlib.compress(rows.tobytes(), 6)) + _chunk(b"IEND", b"")


def _unfilter(data, hgt, wid, bpp):
    """Undo PNG scanline filters. Rows using Average/Paeth are solved along anti-diagonals
    so every step is vectorised, which keeps large images fast without compiled code."""
    stride = wid * bpp
    rows = np.frombuffer(data, dtype=np.uint8)[: hgt * (stride + 1)].reshape(hgt, stride + 1)
    ftype = rows[:, 0].copy()
    filt = rows[:, 1:].reshape(hgt, wid, bpp).astype(np.int16)
    if ftype.max(initial=0) > 4:
        raise ValueError("corrupt PNG (bad filter type)")
    out = np.zeros((hgt, wid, bpp), dtype=np.int16)
    if not (ftype >= 3).any():
        # Only None/Sub/Up filters: rows depend only on the row above.
        prev = np.zeros((wid, bpp), dtype=np.int16)
        for y in range(hgt):
            f, cur = ftype[y], filt[y]
            if f == 0:
                row = cur
            elif f == 1:
                row = np.cumsum(cur, axis=0) & 0xFF
            elif f == 2:
                row = (cur + prev) & 0xFF
            out[y] = row
            prev = out[y]
        return out.astype(np.uint8)
    ft = ftype[:, None].astype(np.int16)
    for d in range(hgt + wid - 1):
        y0, y1 = max(0, d - wid + 1), min(hgt - 1, d)
        ys = np.arange(y0, y1 + 1)
        xs = d - ys
        a = np.where((xs > 0)[:, None], out[ys, np.maximum(xs - 1, 0)], 0)
        b = np.where((ys > 0)[:, None], out[np.maximum(ys - 1, 0), xs], 0)
        c = np.where(((xs > 0) & (ys > 0))[:, None], out[np.maximum(ys - 1, 0), np.maximum(xs - 1, 0)], 0)
        p = a + b - c
        pa, pb, pc = np.abs(p - a), np.abs(p - b), np.abs(p - c)
        paeth = np.where((pa <= pb) & (pa <= pc), a, np.where(pb <= pc, b, c))
        f = ft[ys]
        pred = np.select([f == 1, f == 2, f == 3, f == 4], [a, b, (a + b) >> 1, paeth], 0)
        out[ys, xs] = (filt[ys, xs] + pred) & 0xFF
    return out.astype(np.uint8)


def read_rgba(data):
    """Decode 8-bit PNG bytes to an (h, w, 4) uint8 RGBA array."""
    vals, _, ch = _decode(data)
    v = np.clip(vals * 255 + 0.5, 0, 255).astype(np.uint8)
    if ch == 1:
        v = np.repeat(v, 3, -1)
    elif ch == 2:
        v = np.concatenate([np.repeat(v[..., :1], 3, -1), v[..., 1:]], -1)
    if v.shape[-1] == 3:
        v = np.concatenate([v, np.full(v.shape[:2] + (1,), 255, np.uint8)], -1)
    return v


def read(data):
    """Decode PNG bytes. Returns (values, text) where values is a float32 (h, w) array scaled
    0..1 (luminance for colour images) and text holds the tEXt entries."""
    vals, text, ch = _decode(data)
    if ch >= 3:
        return vals[..., 0] * 0.299 + vals[..., 1] * 0.587 + vals[..., 2] * 0.114, text
    return vals[..., 0], text


def _decode(data):
    if data[:8] != SIGNATURE:
        raise ValueError("not a PNG file")
    pos, idat, text, hdr = 8, [], {}, None
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if tag == b"IHDR":
            hdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"tEXt" and b"\0" in body:
            k, v = body.split(b"\0", 1)
            text[k.decode("latin-1")] = v.decode("latin-1")
        elif tag == b"IEND":
            break
    if hdr is None:
        raise ValueError("PNG has no header")
    wid, hgt, depth, ctype, _, _, interlace = hdr
    if ctype not in CHANNELS or depth not in (8, 16):
        raise ValueError("unsupported PNG: use 8- or 16-bit greyscale or RGB (no palette)")
    if interlace:
        raise ValueError("interlaced PNGs are not supported; save without interlacing")
    ch = CHANNELS[ctype]
    bpp = ch * depth // 8
    px = _unfilter(zlib.decompress(b"".join(idat)), hgt, wid, bpp)
    if depth == 16:
        px = px.reshape(hgt, wid * ch, 2)
        vals = (px[..., 0].astype(np.uint32) << 8 | px[..., 1]).reshape(hgt, wid, ch).astype(np.float32) / 65535
    else:
        vals = px.reshape(hgt, wid, ch).astype(np.float32) / 255
    return vals, text, ch
