#!/usr/bin/env python3
"""Hackathon Watch - frameless desktop shell for D:/Temp/hackwatch/.

Serves ui.html + hackwatch.json over a local HTTP server (the page uses
fetch()) and opens it in a pywebview window with:
  - frameless (borderless) dark window,
  - custom titlebar injected at runtime (ui.html is NOT modified):
    drag region + minimize / maximize-restore / close buttons in top-right.

Window control channel: the injected titlebar JS issues POSTs to
/__hwctl__ on the local server (min | max | close | move dx dy); desktop.py
applies them to the pywebview window. This avoids pywebview's js_api,
which on this system starts frameless windows in a minimized state.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DIR = os.path.dirname(os.path.abspath(__file__))
TITLE = "Hackathon Watch \u2014 NEXUS"
BG = "#0b0f14"
W, H = 1440, 900

# \u0420\u0430\u0431\u043e\u0447\u0438\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 (state/hackwatch.json) \u2014 \u0432\u043d\u0435 exe: %APPDATA%/Hackwatch.
def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    data = os.path.join(base, "Hackwatch")
    os.makedirs(data, exist_ok=True)
    return data

APP_DATA = app_data_dir()
for _name in ("hackwatch.json", "hackwatch.en.json", "hackwatch.ru.json"):
    _bundle = os.path.join(DIR, _name)
    _app = os.path.join(APP_DATA, _name)
    if os.path.exists(_bundle) and not os.path.exists(_app):
        try:
            import shutil
            shutil.copy2(_bundle, _app)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# Injected titlebar (runtime only; hackwatch.html left untouched)
# ---------------------------------------------------------------------------
def titlebar_js(port: int) -> str:
    return f"""
(() => {{
  if (document.getElementById('hw-titlebar')) return;
  const ctl = (action, x, y) =>
    fetch('/__hwctl__', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{ action, x, y }}),
    }}).catch(() => {{}});
  const bar = document.createElement('div');
  bar.id = 'hw-titlebar';
  bar.innerHTML =
    '<span class="hw-title">\u2694 Hackathon Watch \u00b7 NEXUS</span>' +
    '<div class="hw-controls">' +
    '  <button id="hw-min" title="Minimize"><span>–</span></button>' +
    '  <button id="hw-max" title="Maximize / Restore"><span>+</span></button>' +
    '  <button id="hw-close" title="Close"><span>×</span></button>' +
    '</div>';
  bar.style.cssText =
    'position:fixed;top:0;left:0;right:0;height:38px;z-index:2147483647;' +
    'display:flex;align-items:center;justify-content:space-between;' +
    'background:#0b0f14;border-bottom:1px solid #1e2a36;' +
    'color:#d7e0ea;font:13px/1 ui-monospace,SFMono-Regular,Menlo,monospace;' +
    'user-select:none;-webkit-user-select:none;cursor:default;';
  const title = bar.querySelector('.hw-title');
  title.style.cssText = 'padding:0 14px;color:#7a8b9c;letter-spacing:.04em;';
  const controls = bar.querySelector('.hw-controls');
  controls.style.cssText = 'display:flex;align-items:center;gap:10px;padding:0 18px;height:100%;';
  const dotStyle =
    'width:20px;height:20px;border-radius:50%;border:0;cursor:pointer;' +
    'display:flex;align-items:center;justify-content:center;padding:0;';
  const dotColors = {{'hw-min': '#febc2e', 'hw-max': '#28c840', 'hw-close': '#ff5f57'}};
  ['hw-min', 'hw-max', 'hw-close'].forEach(function (id) {{
    const b = bar.querySelector('#' + id);
    const glyph = b.querySelector('span');
    b.style.cssText = dotStyle + 'background:' + dotColors[id] + ';';
    glyph.style.cssText =
      'font-size:13px;line-height:1;color:rgba(0,0,0,.6);font-weight:700;' +
      'opacity:0;transition:opacity .12s;pointer-events:none;';
    b.addEventListener('mouseenter', function () {{ glyph.style.opacity = '1'; }});
    b.addEventListener('mouseleave', function () {{ glyph.style.opacity = '0'; }});
  }});
  const closeBtn = bar.querySelector('#hw-close');
  document.body.appendChild(bar);
  document.body.classList.add('hw-desktop');

  bar.querySelector('#hw-min').addEventListener('click', function () {{ ctl('min'); }});
  bar.querySelector('#hw-max').addEventListener('click', function () {{ ctl('max'); }});
  closeBtn.addEventListener('click', function () {{ ctl('close'); }});

bar.addEventListener('dblclick', function () {{ ctl('max'); }});
}})();
"""

ERROR_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Hackathon Watch</title></head>
<body style="background:#0b0f14;color:#d7e0ea;font:14px ui-monospace,Menlo,monospace;padding:48px;">
<h2 style="color:#f87171;">Page failed to load</h2>
<p>The local server could not serve <b>ui.html</b> from
<code>D:/Temp/hackwatch</code>.</p>
<p style="color:#7a8b9c;">Check that the files exist there, then close this window
and run <code>run_desktop.ps1</code> again.</p>
</body></html>"""


class CtrlHandler(SimpleHTTPRequestHandler):
    """Serves D:/Temp/hackwatch and answers /__hwctl__ window-control POSTs."""

    def __init__(self, *args, window_holder=None, **kwargs):
        self.window_holder = window_holder
        super().__init__(*args, **kwargs)

    def translate_path(self, path):
        # Данные (hackwatch*.json) живут в APP_DATA, не в bundled-каталоге.
        # Basename без query-строки (UI-фетч с cache-bust ?t=...).
        path = urllib.parse.urlparse(path).path
        name = os.path.basename(path)
        if name.startswith("hackwatch") and name.endswith(".json"):
            return os.path.join(APP_DATA, name)
        return super().translate_path(path)

    def do_GET(self):
        if self.path.rstrip("/") == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        return super().do_GET()

    def log_message(self, fmt, *args):  # silence request logging
        pass

    def do_POST(self):
        if self.path.rstrip("/") != "/__hwctl__":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            data = {}
        window = self.window_holder.window
        action = data.get("action")
        if window is None:
            self._reply({"ok": False, "error": "no window"})
            return
        try:
            if action == "min":
                window.minimize()
            elif action == "max":
                self.window_holder.toggle_maximize()
            elif action == "close":
                window.destroy()
            else:
                self._reply({"ok": False, "error": f"unknown action {action}"})
                return
            self._reply({"ok": True})
        except Exception as exc:
            self._reply({"ok": False, "error": str(exc)})

    def _reply(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WindowHolder:
    def __init__(self):
        self.window = None
        self._maximized = False

    def set(self, window):
        self.window = window
        window.events.maximized += lambda: setattr(self, "_maximized", True)
        window.events.restored += lambda: setattr(self, "_maximized", False)

    def toggle_maximize(self):
        if self.window is None:
            return
        if self._maximized:
            self.window.restore()
            self._maximized = False
        else:
            self.window.maximize()
            self._maximized = True


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Startup watchdog: pywebview's frameless windows occasionally get created in
# a minimized/offscreen state on this system. Watch placement for the first
# ~20s and restore the window if it is minimized (showCmd == 2).
# ---------------------------------------------------------------------------
def window_placement(hwnd: int) -> int | None:
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("X", ctypes.c_long), ("Y", ctypes.c_long)]

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class WINDOWPLACEMENT(ctypes.Structure):
            _fields_ = [("length", ctypes.c_int), ("flags", ctypes.c_int),
                        ("showCmd", ctypes.c_int), ("ptMinPosition", POINT),
                        ("ptMaxPosition", POINT), ("rcNormalPosition", RECT)]

        u32 = ctypes.windll.user32
        u32.GetWindowPlacement.argtypes = [ctypes.c_void_p,
                                           ctypes.POINTER(WINDOWPLACEMENT)]
        p = WINDOWPLACEMENT()
        p.length = ctypes.sizeof(WINDOWPLACEMENT)
        if u32.GetWindowPlacement(hwnd, ctypes.byref(p)):
            return int(p.showCmd)
    except Exception:
        pass
    return None


def find_window_handle(target_title: str) -> int | None:
    """Find our window's HWND by title (EnumWindows scan)."""
    try:
        import ctypes

        u32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                         ctypes.c_void_p)
        GetWindowTextLengthW = u32.GetWindowTextLengthW
        GetWindowTextW = u32.GetWindowTextW
        found = []
        def cb(hwnd, lparam):
            n = GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                GetWindowTextW(hwnd, buf, n + 1)
                if buf.value:
                    found.append((hwnd, buf.value))
            return True
        u32.EnumWindows(WNDENUMPROC(cb), 0)
        for h, t in found:
            if t == target_title:
                return int(h)
    except Exception:
        pass
    return None


def watchdog_restore(window, title: str, duration: float = 12.0,
                     interval: float = 0.5) -> None:
    hwnd = None
    deadline = time.monotonic() + duration
    restored = 0
    while time.monotonic() < deadline:
        try:
            if hwnd is None:
                hwnd = find_window_handle(title)
                if hwnd is None:
                    time.sleep(interval)
                    continue
            if window_placement(hwnd) == 2:  # minimized
                window.restore()
                restored += 1
        except Exception:
            pass
        time.sleep(interval)
    if restored:
        print(f"WATCHDOG restored minimized window {restored}x", flush=True)


def probe(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def fallback_browser(url: str) -> None:
    edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    if os.path.exists(edge):
        try:
            os.spawnl(os.P_NOWAIT, edge, edge, "--app=" + url)
            return
        except Exception:
            pass
    webbrowser.open(url)


def main() -> int:
    port = find_free_port()
    url = f"http://127.0.0.1:{port}/ui.html"

    holder = WindowHolder()
    handler = partial(CtrlHandler, directory=DIR, window_holder=holder)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"SERVING {url}", flush=True)

    try:
        import webview
    except Exception as exc:  # pywebview missing -> fallback
        print(f"FATAL pywebview unavailable: {exc}", flush=True)
        fallback_browser(url)
        return 1

    kwargs = dict(
        title=TITLE, frameless=True, width=W, height=H,
        background_color=BG, min_size=(900, 600),
    )
    if not probe(url):
        print("WARN page probe failed; showing error window", flush=True)
        window = webview.create_window(html=ERROR_HTML, **kwargs)
    else:
        window = webview.create_window(url=url, **kwargs)

    holder.set(window)
    js = titlebar_js(port)
    window.events.loaded += lambda: window.evaluate_js(js)
    threading.Thread(target=watchdog_restore, args=(window, TITLE),
                     daemon=True).start()

    try:
        webview.start(debug="--debug" in sys.argv, private_mode=True)
    except Exception as exc:
        print(f"FATAL webview start failed: {exc}", flush=True)
        fallback_browser(url)
        return 1
    finally:
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
