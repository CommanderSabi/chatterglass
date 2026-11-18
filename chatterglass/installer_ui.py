# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""First-run installer window.

Until Chatterglass is installed, this is the whole UI: one window that explains
what installing does, lists missing dependencies, and offers Install or Quit.
"""

from __future__ import annotations

from typing import Optional

import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk, Gtk  # noqa: E402

from . import install_state  # noqa: E402


STYLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")


def _load_styles() -> None:
    if not os.path.exists(STYLE_PATH):
        return
    provider = Gtk.CssProvider()
    try:
        if os.path.exists(STYLE_PATH):
            provider.load_from_path(STYLE_PATH)
        else:
            from . import _assets
            provider.load_from_data(_assets.STYLE_CSS)
    except Exception:  # noqa: BLE001
        return
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def ask_to_install(version: str) -> bool:
    """Show the installer window. True when the app is installed afterwards."""
    _load_styles()

    window = Gtk.Window(title="Install Chatterglass")
    window.set_default_size(560, 0)
    window.set_resizable(False)
    window.set_position(Gtk.WindowPosition.CENTER)
    window.set_name("cgroot")

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    box.set_border_width(22)
    window.add(box)

    title = Gtk.Label(xalign=0)
    title.set_markup("<span size='x-large' weight='bold'>Chatterglass %s</span>" % version)
    box.pack_start(title, False, False, 0)

    blurb = Gtk.Label(xalign=0, wrap=True)
    blurb.set_max_width_chars(58)
    blurb.set_text(
        "Transparent chat overlays and a Discord voice panel that float above your "
        "game, with nothing installed into the system beyond this one folder and a "
        "menu entry.")
    box.pack_start(blurb, False, False, 0)

    detail = Gtk.Label(xalign=0, wrap=True)
    detail.get_style_context().add_class("dim-label")
    detail.set_max_width_chars(58)
    detail.set_text(
        "Install location:\n  %s\n\nThis adds:\n"
        "  · the app in that folder\n"
        "  · a menu entry in ~/.local/share/applications\n"
        "  · an icon under ~/.local/share/icons/hicolor\n\n"
        "Uninstall, in Settings later, removes all of it again." %
        install_state.INSTALL_DIR)
    box.pack_start(detail, False, False, 0)

    problems = install_state.missing_dependencies()
    if problems:
        warn = Gtk.Label(xalign=0, wrap=True)
        warn.set_max_width_chars(58)
        warn.set_markup("<span foreground='#ff9c9c'><b>Missing requirements:</b> %s\n"
                        "Install those first, then run this file again.</span>"
                        % ", ".join(problems))
        box.pack_start(warn, False, False, 0)

    buttons = Gtk.Box(spacing=8)
    buttons.set_halign(Gtk.Align.END)
    box.pack_start(buttons, False, False, 4)

    quit_button = Gtk.Button(label="Quit")
    install_button = Gtk.Button(label="Install Chatterglass")
    install_button.get_style_context().add_class("suggested-action")
    buttons.pack_start(quit_button, False, False, 0)
    buttons.pack_start(install_button, False, False, 0)

    result = {"installed": False}

    def do_install(*_a):
        install_button.set_sensitive(False)
        install_button.set_label("Installing…")
        try:
            install_state.install(version)
        except Exception as error:  # noqa: BLE001
            dialog = Gtk.MessageDialog(transient_for=window, modal=True,
                                       message_type=Gtk.MessageType.ERROR,
                                       buttons=Gtk.ButtonsType.OK)
            dialog.set_markup("<b>Install failed</b>")
            dialog.format_secondary_text(str(error))
            dialog.run()
            dialog.destroy()
            install_button.set_sensitive(True)
            install_button.set_label("Install Chatterglass")
            return
        result["installed"] = True
        window.destroy()

    install_button.connect("clicked", do_install)
    quit_button.connect("clicked", lambda *_: window.destroy())
    window.connect("destroy", lambda *_: Gtk.main_quit())

    window.show_all()
    if problems:
        install_button.set_sensitive(False)
    Gtk.main()
    return result["installed"]
