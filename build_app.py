#!/usr/bin/env python3
"""Build WurmMapGenerator.exe: the desktop app packed into one file with Python and numpy.

  python -m pip install --user pyinstaller pywebview
  python build_app.py

The .exe is written next to this script. Build files go to a temporary folder.
"""

import os
import struct
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "app_icon.ico")


def make_icon(path):
    """An island rendered by the generator itself, in a rounded square, as a multi-size .ico."""
    sys.path.insert(0, HERE)
    import pngio
    import wurm_mapgen as m
    m.set_logger(lambda line: None)
    r = m.generate(m.resolve_options("volcanic", {"land": 0.42}, 2024), 256, 1024)
    rgb = m.render_rgb(r.types, r.height, "terrain", r.k).astype(np.float32)
    images = []
    for size in (256, 64, 48, 32, 16):
        # Average each output pixel's block of source pixels (sizes needn't divide 256).
        edges = np.linspace(0, 256, size + 1).astype(int)
        img = np.array([[rgb[edges[j]:edges[j + 1], edges[i]:edges[i + 1]].mean(axis=(0, 1))
                         for i in range(size)] for j in range(size)])
        yy, xx = np.mgrid[0:size, 0:size] + 0.5
        rad = size * 0.2
        dx = np.maximum(np.abs(xx - size / 2) - (size / 2 - rad), 0)
        dy = np.maximum(np.abs(yy - size / 2) - (size / 2 - rad), 0)
        alpha = np.clip((rad - np.hypot(dx, dy)) * 1.5, 0, 1) * 255
        rgba = np.concatenate([img, alpha[..., None]], -1).astype(np.uint8)
        images.append((size, pngio.write_rgba(rgba)))
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    with open(path, "wb") as f:
        f.write(head + entries + blobs)


def main():
    if not os.path.isfile(ICON):
        print("Making app icon")
        make_icon(ICON)
    work = os.path.join(tempfile.gettempdir(), "wurm-mapgen-build")
    sep = os.pathsep
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "WurmMapGenerator", "--icon", ICON,
        "--add-data", f"{os.path.join(HERE, 'mapgen_ui.html')}{sep}.",
        "--add-data", f"{os.path.join(HERE, 'viewer.html')}{sep}.",
        "--add-data", f"{os.path.join(HERE, 'vendor')}{sep}vendor",
        "--distpath", HERE, "--workpath", work, "--specpath", work,
        "--paths", HERE,
        os.path.join(HERE, "mapgen_app.py"),
    ]
    print(" ".join(args))
    subprocess.check_call(args)
    exe = os.path.join(HERE, "WurmMapGenerator.exe")
    print(f"Built {exe} ({os.path.getsize(exe) / 2**20:.0f} MB)")


if __name__ == "__main__":
    main()
