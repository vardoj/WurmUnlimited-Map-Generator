#!/usr/bin/env python3
"""Browser UI for wurm_mapgen: tweak settings, preview maps, and create worlds.

  python mapgen_ui.py [--port 8765] [--no-browser]

Serves on 127.0.0.1 only. Previews are generated at reduced resolution with the
same seed, so they match the shape of the full-size map.
"""

import argparse
import base64
import json
import os
import re
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from urllib.parse import parse_qs

import paths
import playability
import playermap
import pngio
import shaping
import viewer3d
import wurm_mapgen as mapgen

DIST = paths.DIST_DIR
PAGE = os.path.join(paths.RES_DIR, "mapgen_ui.html")
VENDOR_DIR = os.path.join(paths.RES_DIR, "vendor")
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")

preview_lock = threading.Lock()


class Job:
    """The single world-creation job that may run at a time."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = "idle"  # idle | running | done | error
        self.lines = []
        self.world = None
        self.started = None
        self.finished = None

    def snapshot(self):
        with self.lock:
            return dict(state=self.state, lines=list(self.lines[-200:]), world=self.world,
                        elapsed=round((self.finished or time.time()) - self.started, 1) if self.started else 0)

    def start(self, fn, world):
        with self.lock:
            if self.state == "running":
                return False
            self.state, self.lines, self.world = "running", [], world
            self.started, self.finished = time.time(), None
        threading.Thread(target=self._run, args=(fn,), daemon=True).start()
        return True

    def _run(self, fn):
        mapgen.set_logger(self._append)
        try:
            fn()
            state = "done"
        except Exception as e:  # report every failure to the page
            self._append(f"ERROR: {e}")
            traceback.print_exc()
            state = "error"
        with self.lock:
            self.state, self.finished = state, time.time()

    def _append(self, line):
        print(line, flush=True)
        with self.lock:
            self.lines.append(line)


job = Job()


def list_worlds():
    worlds = []
    if os.path.isdir(DIST):
        for name in sorted(os.listdir(DIST)):
            path = os.path.join(DIST, name)
            if os.path.isfile(os.path.join(path, "sqlite", "wurmlogin.db")) or os.path.isfile(
                    os.path.join(path, "top_layer.map")):
                worlds.append(dict(name=name, has_map=os.path.isfile(os.path.join(path, "top_layer.map")),
                                   has_db=os.path.isfile(os.path.join(path, "sqlite", "wurmlogin.db"))))
    return worlds


def meta():
    return dict(params=mapgen.PARAMS,
                styles={k: dict(label=v["label"], desc=v["desc"], values=v["values"]) for k, v in mapgen.STYLES.items()},
                type_names={str(k): v for k, v in mapgen.TYPE_NAMES.items()},
                ores=[dict(label=label, color="#%02x%02x%02x" % color) for _, label, _, _, color in mapgen.ORE_DEFS],
                report_limits=[dict(label=label, good=good, ok=ok) for _, label, good, ok in playability.RESOURCES],
                worlds=list_worlds(), dist=DIST, presets=mapgen.list_presets())


MAX_UPLOAD = 300 * 1024 * 1024
VIEWER_PAGE = os.path.join(paths.RES_DIR, "viewer.html")

# ---- 3D viewer data ----

last_preview = {}  # arrays of the most recent preview, for viewing it in 3D
_world_cache = {}


def view_source(name):
    """Terrain arrays for the viewer: 'preview' (latest preview) or 'world:NAME'."""
    if name == "preview":
        if not last_preview:
            raise ValueError("generate a preview first")
        return last_preview
    if not name.startswith("world:"):
        raise ValueError("bad source")
    path = os.path.join(world_dir(name[6:]), "top_layer.map")
    key = (path, os.path.getmtime(path))
    if key not in _world_cache:
        types, data, height = mapgen.decode(mapgen.read_map(path))
        _world_cache.clear()
        _world_cache[key] = dict(types=types, data=data, height=height, k=1.0, world=types.shape[0],
                                 spawns=playermap.read_spawns(os.path.dirname(path)), label=name[6:])
    return _world_cache[key]


def view_meta(src_name):
    src = view_source(src_name)
    a = viewer3d.art()
    return dict(
        source=src_name, label=src.get("label", "Preview"), size=int(src["types"].shape[0]), k=src["k"],
        world=int(src["world"]), chunk=min(viewer3d.CHUNK, int(src["types"].shape[0])),
        spawns=[list(map(int, s)) for s in src["spawns"]], client=a.available, client_path=a.path,
        layers=len(viewer3d.layer_keys()), tile_layers={str(t): i for t, i in viewer3d.tile_layers().items()},
        models={str(t): viewer3d.TREE_MODELS[t] for t in viewer3d.TREE_MODELS} if a.available else {},
        bushes=sorted(viewer3d.BUSHES),
        type_names={str(k & 0xFF): v for k, v in mapgen.TYPE_NAMES.items()},
        tree_colors={str(k & 0xFF): "#%02x%02x%02x" % c for k, c in TREE_COLORS.items()},
        water=viewer3d.art().resolve(viewer3d.WATER_TEXTURE) is not None if a.available else False,
    )


# Fallback colours for trees when the client art isn't installed.
TREE_COLORS = {**{t: (40, 85, 35) for t in range(100, 114)}, -93: (60, 100, 40), 101: (30, 70, 40),
               112: (30, 70, 40), **{t: (70, 105, 45) for t in range(-114, -83)}}


def view_minimap(src_name):
    src = view_source(src_name)
    step = max(1, src["types"].shape[0] // 512)
    t, h = src["types"][::step, ::step], src["height"][::step, ::step]
    return mapgen.png_bytes(mapgen.render_rgb(t, h, "terrain", src["k"] * step))


def client_texture(path, size):
    a = viewer3d.art()
    if not a.available:
        raise ValueError("Wurm client not installed")
    if not (path.startswith(("trees/", "bush/", "texture/")) or path.startswith("img.texture.")):
        raise ValueError("not an allowed texture")
    return pngio.write_rgba(a.texture_rgba(path, size))


def world_dir(name):
    if not NAME_RE.match(name or ""):
        raise ValueError("bad world name")
    path = os.path.join(DIST, name)
    if not os.path.isfile(os.path.join(path, "top_layer.map")):
        raise ValueError(f"world {name} has no map")
    return path


def player_map(query):
    q = parse_qs(query)
    get = lambda k, d: q.get(k, [d])[0]
    px = int(get("px", "2048"))
    grid = int(get("grid", "256"))
    if px not in (1024, 2048, 4096) or grid not in (0, 64, 128, 256, 512, 1024):
        raise ValueError("unsupported size or grid")
    img = playermap.render_world(world_dir(get("world", "")), px, grid, get("spawns", "1") == "1",
                                 get("title", "")[:60])
    return mapgen.png_bytes(img)


def make_thumb(body):
    """A small terrain image plus headline numbers for the seed gallery."""
    opts = mapgen.resolve_options(body.get("style", "continent"), body.get("values"), body.get("seed"))
    world_size = int(body.get("world_size", 2048))
    if world_size not in (1024, 2048, 4096, 8192):
        raise ValueError("unsupported size")
    with preview_lock:
        mapgen.set_logger(lambda line: None)
        result = mapgen.generate(opts, 256, world_size)
    rep = playability.report(result, opts)
    counts = {lv: sum(c["level"] == lv for c in rep["checks"]) for lv in ("bad", "warn")}
    s = mapgen.stats(result)
    water = s.get("water") or {}
    return dict(seed=opts.seed, land=s["land"], peak=s["peak"], rivers=water.get("rivers", 0),
                lakes=water.get("lakes", 0), problems=counts["bad"], warnings=counts["warn"],
                image=base64.b64encode(mapgen.png_bytes(
                    mapgen.render_rgb(result.types, result.height, "terrain", result.k))).decode())


def presets_action(body):
    action = body.get("action")
    if action == "save":
        mapgen.save_preset(body.get("name", ""), body.get("style", "continent"), body.get("values"),
                           body.get("seed") if body.get("include_seed") else None, body.get("world_size"))
    elif action == "delete":
        mapgen.delete_preset(body.get("name", ""))
    else:
        raise ValueError("unknown preset action")
    return dict(presets=mapgen.list_presets())


def make_preview(body):
    opts = mapgen.resolve_options(body.get("style", "continent"), body.get("values"), body.get("seed"))
    size = int(body.get("preview_size", 512))
    world_size = int(body.get("world_size", 2048))
    if size not in (256, 512, 1024) or world_size not in (1024, 2048, 4096, 8192):
        raise ValueError("unsupported size")
    size = min(size, world_size)
    started = time.time()
    with preview_lock:
        mapgen.set_logger(lambda line: None)
        result = mapgen.generate(opts, size, world_size)
    last_preview.clear()
    last_preview.update(types=result.types, data=result.data, height=result.height, k=result.k,
                        world=world_size, spawns=result.spawns,
                        label=f"Preview: {opts.style}, seed {opts.seed}"
                              + (f" (1 sample = {result.k:g} tiles; create the world for full detail)"
                                 if result.k > 1 else ""))
    images = {mode: base64.b64encode(mapgen.png_bytes(
        mapgen.render_rgb(result.types, result.height, mode, result.k, result.ores))).decode()
        for mode in ("terrain", "height", "ores")}
    return dict(
        seed=opts.seed, size=size, world_size=world_size, k=result.k, images=images,
        stats=mapgen.stats(result), spawns=result.spawns, elapsed=round(time.time() - started, 2),
        report=playability.report(result, opts),
        types=base64.b64encode(result.types.astype(np.int8).tobytes()).decode(),
        heights=base64.b64encode(result.height.astype("<i2").tobytes()).decode(),
    )


def start_save(body):
    name = str(body.get("name", "")).strip()
    if not NAME_RE.match(name):
        raise ValueError("World name must be 1-40 letters, digits, '-' or '_'.")
    size = int(body.get("world_size", 2048))
    if size not in (1024, 2048, 4096, 8192):
        raise ValueError("unsupported map size")
    template = body.get("template") or ""
    if template:
        if not NAME_RE.match(template):
            raise ValueError("bad template name")
        template = os.path.join(DIST, template)
    out = os.path.join(DIST, name)
    force = bool(body.get("force"))
    if os.path.isfile(os.path.join(out, "top_layer.map")) and not force:
        raise ValueError(f"World '{name}' already has a map. Tick 'Replace existing map' to overwrite "
                         "(the old map files are backed up first).")
    opts = mapgen.resolve_options(body.get("style", "continent"), body.get("values"), body.get("seed"))

    def run():
        mapgen.create_world(opts, size, out, template or None, force, name if body.get("set_name") else None,
                            template_items=bool(body.get("template_items")))

    if not job.start(run, name):
        raise RuntimeError("A world is already being created; wait for it to finish.")
    return dict(ok=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # the page navigated away or cancelled the request

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path == "/api/meta":
            self._send(200, meta())
        elif path == "/api/job":
            self._send(200, job.snapshot())
        elif path.startswith("/vendor/"):
            name = path.rsplit("/", 1)[1]
            f = os.path.join(VENDOR_DIR, name)
            if re.match(r"^[\w.\-]+\.js$", name) and os.path.isfile(f):
                with open(f, "rb") as fh:
                    self._send(200, fh.read(), "text/javascript; charset=utf-8")
            else:
                self._send(404, {"error": "not found"})
        elif path == "/viewer":
            with open(VIEWER_PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif path.startswith("/api/view/"):
            q = parse_qs(self.path.partition("?")[2])
            get = lambda k, d="": q.get(k, [d])[0]
            try:
                what = path[len("/api/view/"):]
                if what == "meta":
                    self._send(200, view_meta(get("src", "preview")))
                elif what == "chunk":
                    self._send(200, viewer3d.chunk_bytes(view_source(get("src", "preview")), int(get("cx")),
                                                         int(get("cy"))), "application/octet-stream")
                elif what == "atlas":
                    size = int(get("size", "256"))
                    if size not in (128, 256, 512):
                        raise ValueError("bad size")
                    self._send(200, viewer3d.atlas_png(size, mapgen.COLORS, get("flat") != "1")[0], "image/png")
                elif what == "model":
                    self._send(200, viewer3d.model_json(int(get("type"))))
                elif what == "texture":
                    self._send(200, client_texture(get("path"), int(get("size", "512"))), "image/png")
                elif what == "minimap":
                    self._send(200, view_minimap(get("src", "preview")), "image/png")
                else:
                    self._send(404, {"error": "not found"})
            except (ValueError, KeyError, OSError) as e:
                self._send(400, {"error": str(e)})
        elif path == "/api/playermap":
            try:
                self._send(200, player_map(self.path.partition("?")[2]), "image/png")
            except (ValueError, OSError) as e:
                self._send(400, {"error": str(e)})
        elif path.startswith("/world-heightmap/"):
            try:
                _, _, h = mapgen.decode(mapgen.read_map(os.path.join(world_dir(path.rsplit("/", 1)[1]),
                                                                    "top_layer.map")))
                self._send(200, shaping.export_heightmap(h), "image/png")
            except (ValueError, OSError) as e:
                self._send(400, {"error": str(e)})
        elif path.startswith("/world-preview/"):
            name = path.rsplit("/", 1)[1]
            png = os.path.join(DIST, name, "map-preview.png")
            if NAME_RE.match(name) and os.path.isfile(png):
                with open(png, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, {"error": "not found"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        # Only accept same-origin requests so other websites can't drive the generator.
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host')}":
            return self._send(403, {"error": "cross-origin request refused"})
        ctype = self.headers.get("Content-Type", "").split(";")[0]
        path, _, query = self.path.partition("?")
        if path == "/api/upload":
            # Raw heightmap bytes; a custom header is required so a plain cross-site form can't post here.
            if ctype != "application/octet-stream" or self.headers.get("X-Mapgen") != "1":
                return self._send(415, {"error": "expected a heightmap upload"})
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0 or n > MAX_UPLOAD:
                return self._send(413, {"error": "file is empty or too large"})
            try:
                name = parse_qs(query).get("name", ["upload.png"])[0]
                self._send(200, shaping.save_upload(self.rfile.read(n), name))
            except ValueError as e:
                self._send(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"error": str(e)})
            return
        if ctype != "application/json":
            return self._send(415, {"error": "expected JSON"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/preview":
                self._send(200, make_preview(body))
            elif self.path == "/api/thumb":
                self._send(200, make_thumb(body))
            elif self.path == "/api/presets":
                self._send(200, presets_action(body))
            elif self.path == "/api/save":
                self._send(200, start_save(body))
            else:
                self._send(404, {"error": "not found"})
        except (ValueError, RuntimeError, KeyError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})


def make_server(port=8765):
    """The UI's HTTP server on 127.0.0.1 (port 0 picks a free port). Returns (server, url)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


def main():
    p = argparse.ArgumentParser(description="Wurm map generator UI")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = p.parse_args()
    server, url = make_server(args.port)
    print(f"Wurm map generator UI at {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
