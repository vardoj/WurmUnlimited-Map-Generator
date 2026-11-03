"""Where things live, both when run as Python scripts and from the packaged WurmMapGenerator.exe.

RES_DIR     read-only files shipped with the tool (the HTML pages, vendor/three.js)
TOOL_DIR    the tool's own writable folder (presets/, imports/): next to the scripts or the .exe
SERVER_DIR  the Wurm Unlimited Dedicated Server install (contains dist/ with the worlds)
DIST_DIR    SERVER_DIR/dist
STEAM_COMMON  the Steam "common" folder, used to find the Wurm Unlimited client

The server folder is found by looking upward from TOOL_DIR for a folder that has both
server.jar and dist/. Set WURM_SERVER_DIR to override, or let the app ask and remember it.
"""

import json
import os
import sys

FROZEN = getattr(sys, "frozen", False)
_HERE = os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", _HERE) if FROZEN else _HERE
TOOL_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else _HERE
SETTINGS_FILE = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "WurmMapGenerator",
                             "settings.json")


def is_server_dir(path):
    return bool(path) and os.path.isfile(os.path.join(path, "server.jar")) and os.path.isdir(
        os.path.join(path, "dist"))


def _search_up(start):
    path = os.path.abspath(start)
    for _ in range(6):
        if is_server_dir(path):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def find_server_dir():
    for cand in (os.environ.get("WURM_SERVER_DIR"), _search_up(TOOL_DIR), load_settings().get("server_dir")):
        if is_server_dir(cand):
            return os.path.abspath(cand)
    return None


SERVER_DIR = find_server_dir()


def set_server_dir(path):
    """Point the tool at a server install (used by the app when the .exe lives elsewhere)."""
    global SERVER_DIR, DIST_DIR, STEAM_COMMON
    SERVER_DIR = os.path.abspath(path)
    DIST_DIR = os.path.join(SERVER_DIR, "dist")
    STEAM_COMMON = os.path.dirname(SERVER_DIR)
    settings = load_settings()
    settings["server_dir"] = SERVER_DIR
    save_settings(settings)


# Fall back to the old relative layout (tools/mapgen inside the server folder).
_base = SERVER_DIR or os.path.normpath(os.path.join(TOOL_DIR, "..", ".."))
DIST_DIR = os.path.join(_base, "dist")
STEAM_COMMON = os.path.dirname(_base)
