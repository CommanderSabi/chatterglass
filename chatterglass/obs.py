"""Local overlay page for OBS (or any browser source).

The app serves a tiny loopback-only site that reproduces the user's overlay
layout, so a single OBS Browser Source shows the overlays and nothing else — no
GUI, no compositing tricks. It reads the live config, so adding a slot shows up
without copying a new URL.

Loopback only, no authentication, and it dies with the app because it lives in
this process.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import _assets
from .config import is_allowed_overlay_url

OVERLAY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Chatterglass overlay</title>
<style>
  html, body { margin:0; padding:0; width:100%; height:100%; background:transparent;
               overflow:hidden; }
  .slot { position:absolute; overflow:hidden; }
  .slot iframe { width:100%; height:100%; border:0; background:transparent; }
</style></head>
<body>
<div id="stage"></div>
<script>
(function () {
  var stage = document.getElementById("stage");
  var known = {};

  function apply(slots) {
    var seen = {};
    slots.forEach(function (s) {
      seen[s.id] = true;
      var el = known[s.id];
      if (!el) {
        el = document.createElement("div");
        el.className = "slot";
        var frame = document.createElement("iframe");
        frame.setAttribute("allowtransparency", "true");
        el.appendChild(frame);
        stage.appendChild(el);
        known[s.id] = el;
      }
      el.style.left = s.x + "px";
      el.style.top = s.y + "px";
      el.style.width = s.width + "px";
      el.style.height = s.height + "px";
      el.style.opacity = s.opacity;
      el.style.display = s.visible ? "block" : "none";
      var frame = el.firstChild;
      if (frame.getAttribute("src") !== s.src) frame.setAttribute("src", s.src);
      if (s.zoom && s.zoom !== 1) frame.style.transform = "scale(" + s.zoom + ")";
      else frame.style.transform = "";
      frame.style.transformOrigin = "top left";
      if (s.zoom && s.zoom !== 1) {
        frame.style.width = (100 / s.zoom) + "%";
        frame.style.height = (100 / s.zoom) + "%";
      } else {
        frame.style.width = "100%";
        frame.style.height = "100%";
      }
    });
    Object.keys(known).forEach(function (id) {
      if (!seen[id]) { known[id].remove(); delete known[id]; }
    });
  }

  function poll() {
    fetch("/layout.json", {cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(apply)
      .catch(function () {})
      .then(function () { setTimeout(poll, 1500); });
  }
  poll();
})();
</script>
</body></html>
"""

PANEL_POLL_JS = """
<script>
(function () {
  function pull() {
    fetch("/voice.json", {cache: "no-store"})
      .then(function (r) { return r.json(); })
      .then(function (state) {
        if (window.cg && window.cg.update) window.cg.update(state);
        if (window.cg && window.cg.setBackdrop && state.backdrop !== undefined) {
          window.cg.setBackdrop(state.backdrop);
        }
      })
      .catch(function () {})
      .then(function () { setTimeout(pull, 1000); });
  }
  pull();
})();
</script>
"""


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ChatterglassOverlay/1.0"

        def log_message(self, *_args):
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path in ("/", "/overlay", "/overlay.html"):
                self._send(200, OVERLAY_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif path == "/layout.json":
                payload = json.dumps(self.server.layout()).encode("utf-8")
                self._send(200, payload, "application/json")
            elif path == "/voice.json":
                payload = json.dumps(self.server.voice()).encode("utf-8")
                self._send(200, payload, "application/json")
            elif path in ("/panel", "/panel.html"):
                html = _assets.PANEL_HTML.replace("</body>", PANEL_POLL_JS + "</body>")
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")

    return Handler


class OverlayServer:
    """Loopback HTTP server exposing the current overlay layout."""

    def __init__(self, app, port: int = 8766):
        self.app = app
        self.port = int(port)
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- data ------------------------------------------------------------
    def layout(self):
        slots = []
        for slot in self.app.config.slots:
            if slot.kind == "voice":
                source = "/panel.html?slot=%s" % slot.id
                opacity = 1.0
            else:
                # Same allow-list as the on-screen windows: a hand-edited config
                # must not get data:/file:/javascript: into OBS's browser either.
                source = slot.url if is_allowed_overlay_url(slot.url) else ""
                opacity = max(0.05, min(1.0, float(slot.opacity)))
            if not source:
                continue
            slots.append({
                "id": slot.id,
                "kind": slot.kind,
                "src": source,
                "x": slot.x,
                "y": slot.y,
                "width": slot.width,
                "height": slot.height,
                "zoom": slot.zoom,
                "opacity": opacity,
                "visible": bool(slot.visible),
            })
        return slots

    def voice(self):
        state = dict(getattr(self.app, "voice_state", {}) or {})
        show_header = True
        backdrop = 0.72
        for slot in self.app.config.slots:
            if slot.kind == "voice":
                show_header = bool(slot.show_header)
                backdrop = float(slot.opacity)
                break
        state["show_header"] = show_header
        state["backdrop"] = backdrop
        return state

    # -- lifecycle -------------------------------------------------------
    def start(self) -> Optional[str]:
        if self._httpd is not None:
            return self.url()
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", self.port), make_handler(self.app))
        except OSError:
            return None
        httpd.daemon_threads = True
        # The handler asks its server object for fresh data on every request.
        httpd.layout = self.layout  # type: ignore[attr-defined]
        httpd.voice = self.voice  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="chatterglass-obs",
                                        daemon=True)
        self._thread.start()
        return self.url()

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        self._thread = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def url(self) -> str:
        return "http://127.0.0.1:%d/overlay" % self.port
