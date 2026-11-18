# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Single-instance control over D-Bus, on purpose.

Once the overlays are click-through there is nothing left to click, so the way
back has to be out-of-band. Overlayed solved that with a tray icon; a tray item
is unreliable on a bare KDE Wayland session, but D-Bus is not: the second launch
of ``chatterglass`` talks to the first one and gets the control window back.

That also makes a global shortcut trivial to wire up in KDE: bind a shortcut to
``python3 -m chatterglass --show`` (or ``--toggle``) and it works, because the
keybinding is the compositor's job, not the app's.
"""

from __future__ import annotations

import os
import secrets
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "org.chatterglass.Control"
OBJECT_PATH = "/org/chatterglass/Control"
INTERFACE = "org.chatterglass.Control"

_METHOD_ARGS = """
    <arg type="s" name="token"/>"""

XML = """
<node>
  <interface name="org.chatterglass.Control">
    <method name="ShowControl">{a}</method>
    <method name="ToggleControl">{a}</method>
    <method name="HideOverlays">{a}</method>
    <method name="ShowOverlays">{a}</method>
    <method name="ToggleClickThrough">{a}</method>
    <method name="ReloadAll">{a}</method>
    <method name="Quit">{a}</method>
    <property name="Status" type="s" access="read"/>
  </interface>
</node>
""".format(a=_METHOD_ARGS)


def token_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, "chatterglass-control.token")


def _read_token() -> str:
    try:
        with open(token_path(), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""

METHODS = ("ShowControl", "ToggleControl", "HideOverlays", "ShowOverlays",
           "ToggleClickThrough", "ReloadAll", "Quit")


def _session_bus() -> Optional[Gio.DBusConnection]:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def call(method: str, timeout_ms: int = 500) -> bool:
    """Ask a running instance to do something. False if none is listening."""
    if method not in METHODS:
        raise ValueError("unknown method %r" % method)
    bus = _session_bus()
    if bus is None:
        return False
    try:
        bus.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method,
                      GLib.Variant("(s)", (_read_token(),)), None,
                      Gio.DBusCallFlags.NONE, timeout_ms, None)
        return True
    except GLib.Error:
        return False


def is_running() -> bool:
    bus = _session_bus()
    if bus is None:
        return False
    try:
        result = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "NameHasOwner",
                               GLib.Variant("(s)", (BUS_NAME,)), None,
                               Gio.DBusCallFlags.NONE, 500, None)
        return bool(result.unpack()[0])
    except GLib.Error:
        return False


class ControlService:
    """Owns the bus name and dispatches method calls to the app."""

    def __init__(self, handlers: dict[str, Callable[[], None]],
                 status_provider: Callable[[], str]):
        self.handlers = handlers
        self.status_provider = status_provider
        self._connection: Optional[Gio.DBusConnection] = None
        self._owner_id = 0
        self._info = Gio.DBusNodeInfo.new_for_xml(XML)
        self._token = secrets.token_urlsafe(24)

    def own(self) -> bool:
        connection = _session_bus()
        if connection is None:
            print("warning: no session bus; single-instance control disabled",
                  flush=True)
            return False
        self._connection = connection
        interface = self._info.interfaces[0]
        connection.register_object(OBJECT_PATH, interface,
                                   self._on_method_call, self._on_get_property, None)
        # A per-session token in the runtime dir. Honest limitation: any process
        # running as the same user can read this file, so it does not stop a
        # same-user program; the session bus already isolates other users. It is
        # defence in depth against stray callers, not a boundary.
        try:
            os.makedirs(os.path.dirname(token_path()), mode=0o700, exist_ok=True)
            with open(token_path(), "w", encoding="utf-8") as handle:
                handle.write(self._token)
            os.chmod(token_path(), 0o600)
        except OSError:
            pass
        self._owner_id = Gio.bus_own_name_on_connection(
            connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
        return True

    def release(self) -> None:
        try:
            os.unlink(token_path())
        except OSError:
            pass
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0

    def _on_method_call(self, _connection, _sender, _path, _iface, method,
                        params, invocation) -> None:
        try:
            supplied = params.unpack()[0]
        except Exception:  # noqa: BLE001
            supplied = ""
        if supplied != self._token:
            invocation.return_dbus_error("org.chatterglass.Error", "bad control token")
            return
        handler = self.handlers.get(method)
        try:
            if handler is not None:
                handler()
            invocation.return_value(None)
        except Exception as error:  # noqa: BLE001
            invocation.return_dbus_error("org.chatterglass.Error", str(error))

    def _on_get_property(self, _connection, _sender, _path, _iface, prop):
        if prop == "Status":
            return GLib.Variant("s", self.status_provider())
        return None
