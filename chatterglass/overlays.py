# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""One layer-shell window per slot, content in a WebKit webview."""

from __future__ import annotations

import json
import os
import tempfile

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402

from . import layer  # noqa: E402
from . import _assets  # noqa: E402
from .config import Slot, is_allowed_overlay_url  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL_HTML = os.path.join(HERE, "panel.html")
BACKDROP_JS = os.path.join(HERE, "backdrop.js")

_PANEL_CACHE = None


def panel_file() -> str:
    """Path to the panel HTML: the loose file, or a temp copy of the embedded one.

    A single-file build has no loose assets next to the module, and WebKit needs a
    real path for load_uri, so the embedded copy is materialised once.
    """
    global _PANEL_CACHE
    if os.path.exists(PANEL_HTML):
        return PANEL_HTML
    if _PANEL_CACHE is None:
        directory = tempfile.mkdtemp(prefix="chatterglass-panel-")
        path = os.path.join(directory, "panel.html")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_assets.PANEL_HTML)
        _PANEL_CACHE = path
    return _PANEL_CACHE


def backdrop_source() -> str:
    if os.path.exists(BACKDROP_JS):
        with open(BACKDROP_JS, "r", encoding="utf-8") as handle:
            return handle.read()
    return _assets.BACKDROP_JS

_WEB_CONTEXT = None


def _web_context():
    """One ephemeral context for every overlay.

    Nothing is written to the user's browser profile: no cookies, no cache, no
    local storage from third-party chat pages, and no persistence outside this
    project folder.
    """
    global _WEB_CONTEXT
    if _WEB_CONTEXT is None:
        try:
            _WEB_CONTEXT = WebKit2.WebContext.new_ephemeral()
        except Exception:  # noqa: BLE001
            _WEB_CONTEXT = None
    return _WEB_CONTEXT


class SlotWindow:
    def __init__(self, slot: Slot, debug: bool = False):
        self.slot = slot
        self.debug = debug
        self._zoom = slot.zoom
        self._monitor = slot.monitor
        self._opacity = slot.opacity
        self._show_header = slot.show_header
        self._layer = slot.layer
        self.pending_state = None
        self._loaded = False

        self.window = layer.create_window(
            namespace="chatterglass-%s" % slot.id,
            layer=slot.layer,
            x=slot.x,
            y=slot.y,
            width=slot.width,
            height=slot.height,
            monitor=slot.monitor,
        )
        self.webview = (WebKit2.WebView(web_context=_web_context())
                        if _web_context() is not None else WebKit2.WebView())
        transparent = Gdk.RGBA()
        transparent.red = transparent.green = transparent.blue = transparent.alpha = 0.0
        self.webview.set_background_color(transparent)
        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_developer_extras(bool(debug))
        settings.set_media_playback_requires_user_gesture(True)
        if slot.zoom != 1.0:
            self.webview.set_zoom_level(float(slot.zoom))
        self._install_backdrop()
        self._install_shell()
        self.window.add(self.webview)
        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.connect("load-failed", self._on_load_failed)
        self.window.connect("destroy", self._on_destroy)
        self.window.show_all()
        layer.set_click_through(self.window, slot.click_through)
        self.apply_opacity(slot)
        self.load()

    # -- injected backdrop -----------------------------------------------
    def _install_backdrop(self) -> None:
        """Gives chat overlays a background of our own to fade.

        Without it the opacity slider can only fade the whole surface, text
        included, because the page owns its pixels.
        """
        if self.slot.kind != "web":
            return
        try:
            source = backdrop_source()
            manager = self.webview.get_user_content_manager()
            manager.add_script(WebKit2.UserScript.new(
                source,
                WebKit2.UserContentInjectedFrames.TOP_FRAME,
                WebKit2.UserScriptInjectionTime.END,
                None,
                None,
            ))
        except Exception as error:  # noqa: BLE001
            print("[slot %s] backdrop unavailable: %r" % (self.slot.id, error),
                  flush=True)

    # -- shell -----------------------------------------------------------
    def _install_shell(self) -> None:
        # No wrapper widget: a Gtk.Box between the window and the webview left
        # the webview with zero height and nothing rendered at all.
        self.webview.set_hexpand(True)
        self.webview.set_vexpand(True)

    def set_outline(self, show: bool) -> None:
        """Kept as a no-op hook: the dashed placement outline was not wanted."""
        return

    # -- javascript ------------------------------------------------------
    def _js(self, script: str) -> bool:
        if not self._loaded:
            return False
        try:
            self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
            return True
        except Exception:
            try:
                self.webview.run_javascript(script, None, None, None)
                return True
            except Exception as error:  # noqa: BLE001
                print("[slot %s] js failed: %r" % (self.slot.id, error), flush=True)
                return False

    # -- loading ---------------------------------------------------------
    def load(self) -> None:
        self._loaded = False
        if self.slot.kind == "voice":
            self.webview.load_uri(GLib.filename_to_uri(panel_file(), None))
        elif self.slot.url and is_allowed_overlay_url(self.slot.url):
            self.webview.load_uri(self.slot.url)
        elif self.slot.url:
            # Reached when a URL was written into config.json by hand or when the
            # scheme list changed. Never navigate to it.
            self.webview.load_html(
                self._error_html(self.slot.url,
                                 "Only http and https overlay URLs are allowed"),
                None,
            )
        else:
            self.webview.load_html(self._placeholder_html(), None)

    def reload(self) -> None:
        self.webview.reload()

    def _placeholder_html(self) -> str:
        return (
            "<html><body style='background:transparent;color:#9aa0ad;"
            "font:13px sans-serif;display:grid;place-items:center;height:100vh;margin:0;"
            "border:1px dashed rgba(255,255,255,.15);border-radius:12px'>"
            "no URL set for this slot</body></html>"
        )

    def _on_load_changed(self, _webview, event) -> None:
        if event == WebKit2.LoadEvent.FINISHED:
            self._loaded = True
            if self.pending_state is not None:
                self.push_voice(self.pending_state)
            self.apply_opacity(self.slot)

    def _on_load_failed(self, _webview, _event, uri, error) -> bool:
        print("[slot %s] load failed for %s: %s" % (self.slot.id, uri, error.message),
              flush=True)
        self.webview.load_html(self._error_html(uri, error.message), None)
        return True

    def _error_html(self, uri: str, message: str) -> str:
        import html as escape_html
        return (
            "<html><body style='margin:0;background:rgba(20,21,26,.92);color:#ff9c9c;"
            "font:12px/1.4 sans-serif;display:grid;place-items:center;height:100vh;"
            "border:1px dashed rgba(255,120,120,.6);border-radius:10px;padding:10px;"
            "text-align:center;box-sizing:border-box'>"
            "<div><b>Could not load this overlay</b><br><br>%s<br><br>"
            "<span style='color:#c9ccd6'>%s</span></div></body></html>"
            % (escape_html.escape(uri or ""), escape_html.escape(message or ""))
        )

    def _on_destroy(self, _window) -> None:
        self._loaded = False

    # -- content ---------------------------------------------------------
    def push_voice(self, state: dict) -> None:
        if self.slot.kind != "voice":
            return
        payload = dict(state)
        payload["show_header"] = bool(self.slot.show_header)
        self.pending_state = payload
        self._js("window.cg && window.cg.update(%s);" % json.dumps(payload))

    def apply_opacity(self, slot: Slot) -> None:
        """Only the Discord panel has a background this app owns.

        Fading the window would fade avatars and names too, which must not
        happen. Third-party chat pages paint their own background and have their
        own transparency setting, so their surface opacity is left untouched.
        """
        if slot.kind == "voice":
            self.window.set_opacity(1.0)
            self._js("window.cg && window.cg.setBackdrop && window.cg.setBackdrop(%s);"
                     % json.dumps(round(float(slot.opacity), 3)))
        else:
            # Background of our own, injected behind the page: fades the panel
            # only, so incoming chat text stays fully opaque.
            self.window.set_opacity(1.0)
            self._js("window.__cgSetBackdrop && window.__cgSetBackdrop(%s);"
                     % json.dumps(round(float(slot.opacity), 3)))

    # -- live updates ----------------------------------------------------
    def apply(self, slot: Slot) -> None:
        """Apply live changes.

        No URL reload here: the editor mutates one Slot object in place, so
        comparing against a previous state is meaningless, and reloading on
        every keystroke would be hostile. Loading is explicit - Enter in the URL
        field, the Reload button, or adding a slot.
        """
        self.slot = slot
        layer.set_geometry(self.window, slot.x, slot.y, slot.width, slot.height)
        layer.set_click_through(self.window, slot.click_through)
        layer.set_keyboard(self.window, not slot.click_through)
        if slot.opacity != self._opacity:
            self._opacity = slot.opacity
            self.apply_opacity(slot)
        if slot.show_header != self._show_header:
            self._show_header = slot.show_header
            if self.pending_state is not None:
                self.push_voice(self.pending_state)
        if slot.zoom != self._zoom:
            self._zoom = slot.zoom
            self.webview.set_zoom_level(float(slot.zoom))
        if slot.layer != self._layer:
            self._layer = slot.layer
            layer.set_layer(self.window, slot.layer)
        if slot.monitor != self._monitor:
            self._monitor = slot.monitor
            try:
                import gi as _gi
                _gi.require_version("GtkLayerShell", "0.1")
                from gi.repository import GtkLayerShell
                GtkLayerShell.set_monitor(self.window, slot.monitor)
            except Exception:
                pass
        if slot.visible and not self.window.get_visible():
            self.window.show_all()
            layer.set_click_through(self.window, slot.click_through)
        elif not slot.visible and self.window.get_visible():
            self.window.hide()

    def destroy(self) -> None:
        self.window.destroy()


class OverlayManager:
    """Creates, updates and disposes the slot windows."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.windows: dict[str, SlotWindow] = {}

    def add(self, slot: Slot) -> SlotWindow:
        window = SlotWindow(slot, debug=self.debug)
        self.windows[slot.id] = window
        return window

    def remove(self, slot_id: str) -> None:
        window = self.windows.pop(slot_id, None)
        if window is not None:
            window.destroy()

    def apply(self, slot: Slot) -> None:
        window = self.windows.get(slot.id)
        if window is None:
            self.add(slot)
        else:
            window.apply(slot)

    def reload(self, slot_id: str) -> None:
        window = self.windows.get(slot_id)
        if window is not None:
            window.reload()

    def broadcast_voice(self, state: dict) -> None:
        for window in self.windows.values():
            window.push_voice(state)

    def has_voice_slot(self) -> bool:
        return any(w.slot.kind == "voice" and w.slot.visible for w in self.windows.values())

    def shutdown(self) -> None:
        for window in list(self.windows.values()):
            window.destroy()
        self.windows.clear()
