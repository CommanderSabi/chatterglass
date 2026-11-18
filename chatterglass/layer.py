# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Layer-shell surfaces: transparent, unfocusable, click-through, above games.

KDE Wayland will not let an ordinary window sit above a borderless-fullscreen
game, so every overlay here is a ``zwlr_layer_surface_v1`` (via gtk-layer-shell).
That is also why exclusive-fullscreen games still win: nothing composites over a
surface the compositor has handed to direct scanout.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")

from gi.repository import Gdk, Gtk, GtkLayerShell  # noqa: E402

_LAYERS = {
    "background": GtkLayerShell.Layer.BACKGROUND,
    "bottom": GtkLayerShell.Layer.BOTTOM,
    "top": GtkLayerShell.Layer.TOP,
    "overlay": GtkLayerShell.Layer.OVERLAY,
}


def supported() -> bool:
    return bool(GtkLayerShell.is_supported())


def _paint_transparent(_widget, cr):
    """Keep the toplevel itself invisible so only the web content is drawn."""
    cr.set_operator(1)  # cairo.OPERATOR_SOURCE
    cr.set_source_rgba(0.0, 0.0, 0.0, 0.0)
    cr.paint()
    return False


def create_window(namespace: str = "chatterglass", layer: str = "overlay",
                  x: int = 0, y: int = 0, width: int = 360, height: int = 300,
                  monitor: int = 0) -> Gtk.Window:
    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_app_paintable(True)
    window.set_skip_taskbar_hint(True)
    window.set_skip_pager_hint(True)
    window.set_accept_focus(False)
    window.set_focus_on_map(False)
    window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
    window.set_title(namespace)

    screen = window.get_screen()
    visual = screen.get_rgba_visual()
    if visual is not None:
        window.set_visual(visual)
    window.connect("draw", _paint_transparent)

    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_namespace(window, namespace)
    GtkLayerShell.set_layer(window, _LAYERS.get(layer, GtkLayerShell.Layer.OVERLAY))
    # -1: never reserve screen space, otherwise KWin would shrink maximised work.
    GtkLayerShell.set_exclusive_zone(window, -1)
    set_geometry(window, x, y, width, height)
    try:
        GtkLayerShell.set_monitor(window, monitor)
    except Exception:
        pass
    # Never take the keyboard: this surface is a viewer, not an input target.
    set_keyboard(window, False)
    return window


def set_layer(window: Gtk.Window, layer: str) -> None:
    """Change the layer of a live surface (takes effect on the next commit)."""
    GtkLayerShell.set_layer(window, _LAYERS.get(layer, GtkLayerShell.Layer.OVERLAY))


def set_position(window: Gtk.Window, x: int, y: int) -> None:
    """Move by changing layer-shell margins. Cheap enough to run per pointer event."""
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.TOP, max(0, int(y)))
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.LEFT, max(0, int(x)))


def set_size(window: Gtk.Window, width: int, height: int) -> None:
    width = max(40, int(width))
    height = max(40, int(height))
    window.set_size_request(width, height)
    window.resize(width, height)


def set_geometry(window: Gtk.Window, x: int, y: int, width: int, height: int) -> None:
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.TOP, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.RIGHT, False)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, False)
    set_position(window, x, y)
    set_size(window, width, height)


def set_keyboard(window: Gtk.Window, interactive: bool) -> None:
    try:
        GtkLayerShell.set_keyboard_interactivity(window, bool(interactive))
    except Exception:
        pass


def set_click_through(window: Gtk.Window, click_through: bool) -> None:
    """Empty input region == the compositor passes all pointer events through.

    Must run after the window is realised, otherwise there is no GdkWindow yet.

    Note: passing ``None`` to clear the shape is rejected by PyGObject
    ("Argument 1 does not allow None as a value"), so "not click-through" is
    expressed as a region covering the whole surface instead.
    """
    gdk_window = window.get_window()
    if gdk_window is None:
        return
    try:
        import cairo
        if click_through:
            gdk_window.input_shape_combine_region(cairo.Region(), 0, 0)
        else:
            width = max(1, int(gdk_window.get_width()))
            height = max(1, int(gdk_window.get_height()))
            region = cairo.Region(cairo.RectangleInt(0, 0, width, height))
            gdk_window.input_shape_combine_region(region, 0, 0)
    except Exception as error:  # noqa: BLE001 - never crash the UI thread
        print("could not set input shape: %r" % (error,), flush=True)
