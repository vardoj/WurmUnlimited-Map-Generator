"""Wurm Map Generator as a desktop app: the same UI in its own window (Edge WebView2), with no
browser tab or console window. The 3D viewer opens in a window of its own.

  pythonw mapgen_app.py          (or double-click WurmMapGenerator.exe)

The UI's local web server still runs inside the app, on a free port on 127.0.0.1 only.
"""

import os
import sys
import threading
import traceback

import paths

APP_TITLE = "Wurm Map Generator"
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "WurmMapGenerator")


def _log_to_file():
    """A windowed .exe has no console; keep its output in a log file for troubleshooting."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    log = open(os.path.join(LOG_DIR, "app.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log


SETUP_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Wurm Map Generator</title>
<style>
body { margin: 0; height: 100vh; display: grid; place-items: center; background: #1d2126; color: #eef1f4;
       font: 14px/1.5 system-ui, "Segoe UI", sans-serif; }
div { max-width: 460px; padding: 26px; background: #262b31; border-radius: 12px; }
h1 { font-size: 18px; margin: 0 0 8px; }
p { color: #b4bcc6; margin: 0 0 14px; }
button { font: inherit; padding: 7px 14px; border-radius: 8px; border: 0; background: #6aa84f; color: #fff; cursor: pointer; }
#err { color: #f19a8c; margin-top: 10px; min-height: 20px; }
</style></head><body><div>
<h1>Where is your Wurm Unlimited server?</h1>
<p>Choose the <b>Wurm Unlimited Dedicated Server</b> folder (the one with <code>server.jar</code> and
<code>dist</code>). The map generator remembers it.</p>
<button onclick="pick()">Choose folder…</button>
<div id="err"></div>
<script>
async function pick() {
  const r = await window.pywebview.api.choose_server_dir();
  if (r && r.error) document.getElementById("err").textContent = r.error;
}
</script></div></body></html>"""


class Api:
    """Functions the pages can call through window.pywebview.api."""

    def __init__(self):
        self._base_url = None
        self._main = None

    def open_window(self, path, title=None):
        """Open a page of the tool (e.g. the 3D viewer) in a new app window."""
        import webview
        if not self._base_url or not str(path).startswith("/"):
            return {"error": "bad path"}
        webview.create_window(title or APP_TITLE, self._base_url.rstrip("/") + path, width=1400, height=900,
                              min_size=(800, 560), js_api=self)
        return {"ok": True}

    def choose_server_dir(self):
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
        picked = self._main.create_file_dialog(kind)
        if not picked:
            return {"error": ""}
        folder = picked[0] if isinstance(picked, (list, tuple)) else picked
        if not paths.is_server_dir(folder):
            return {"error": "That folder doesn't contain server.jar and a dist folder."}
        paths.set_server_dir(folder)
        threading.Thread(target=start_ui, args=(self,), daemon=True).start()
        return {"ok": True}


def start_ui(api):
    """Start the UI server (importing it only now, once the server folder is known) and show it."""
    import mapgen_ui
    server, url = mapgen_ui.make_server(0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api._base_url = url
    print(f"{APP_TITLE} serving {url} for {paths.SERVER_DIR}", flush=True)
    api._main.load_url(url)
    return server


def self_test(api, webview):
    """--selftest: check the UI loads, the page can reach the app API, and the 3D viewer opens
    in its own window; print the results and quit."""
    import time

    def wait_js(win, expr, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            try:
                v = win.evaluate_js(expr)
                if v:
                    return v
            except Exception:
                pass
            time.sleep(0.5)
        return None

    results = {}
    results["ui_loaded"] = bool(wait_js(api._main, "document.getElementById('generate') ? 1 : 0"))
    results["api_bridge"] = bool(wait_js(api._main, "window.pywebview && window.pywebview.api && window.pywebview.api.open_window ? 1 : 0"))
    results["preview"] = wait_js(api._main, "state && state.result ? document.getElementById('status').textContent : ''", 60)
    # Shift+click on the map should open the 3D viewer at that spot.
    before = len(webview.windows)
    api._main.evaluate_js("""(() => { const r = document.getElementById('map').getBoundingClientRect();
        document.getElementById('map').dispatchEvent(new MouseEvent('click', {bubbles: true, shiftKey: true,
        clientX: r.left + r.width / 2, clientY: r.top + r.height / 2})); })()""")
    time.sleep(2)
    results["shift_click_viewer"] = len(webview.windows) > before
    for w in webview.windows[before:]:
        w.destroy()
    api._main.evaluate_js("openWindow('/viewer?src=world:Adventure', 'Wurm Map Viewer')")
    time.sleep(2)
    viewer = webview.windows[-1] if len(webview.windows) > 1 else None
    results["viewer_window"] = viewer is not None
    if viewer:
        results["viewer_chunks"] = wait_js(viewer, "window.__viewer && window.__viewer.chunks.size", 40)
    print("SELFTEST", results, flush=True)
    for w in list(webview.windows):
        w.destroy()


def main():
    _log_to_file()
    try:
        import webview
    except ImportError:
        sys.exit("The desktop app needs pywebview:  python -m pip install --user pywebview")
    webview.settings["ALLOW_DOWNLOADS"] = True
    api = Api()
    need_setup = not paths.is_server_dir(paths.SERVER_DIR)
    api._main = webview.create_window(APP_TITLE, html=SETUP_PAGE if need_setup else "<body style='background:#1d2126'></body>",
                                     width=1500, height=960, min_size=(900, 620), js_api=api)

    def closing():
        # Don't cut off a world that is still being written.
        ui = sys.modules.get("mapgen_ui")
        if ui and ui.job.snapshot()["state"] == "running":
            return api._main.create_confirmation_dialog(
                APP_TITLE, "A world is still being created. Quit anyway? Its map files may be left incomplete.")
        return True

    api._main.events.closing += closing

    def ready():
        if not need_setup:
            try:
                start_ui(api)
            except Exception:
                traceback.print_exc()
                api._main.load_html(f"<pre style='color:#f19a8c'>{traceback.format_exc()}</pre>")
        if "--selftest" in sys.argv:
            self_test(api, webview)

    webview.start(ready, private_mode=False, storage_path=os.path.join(LOG_DIR, "webview"))
    os._exit(0)  # stop the server and any background job with the window


if __name__ == "__main__":
    main()
