# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Entry point: ``python3 -m chatterglass``.

Running it again while an instance is alive does not start a second copy - it
asks the first one to show its control window. That is the way back out of
click-through mode, and it is what a KDE global shortcut should call.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk  # noqa: E402

from . import __version__, layer, service  # noqa: E402
from . import install_state, installer_ui  # noqa: E402
from .app import App  # noqa: E402
from .config import DEFAULT_CONFIG_PATH, Config  # noqa: E402
from .control import ControlWindow  # noqa: E402
from .discord_ipc import candidate_sockets, find_socket  # noqa: E402

COMMANDS = {
    "show": "ShowControl",
    "toggle": "ToggleControl",
    "hide_overlays": "HideOverlays",
    "show_overlays": "ShowOverlays",
    "toggle_click_through": "ToggleClickThrough",
    "reload_all": "ReloadAll",
    "quit": "Quit",
}


def diagnose(config: Config) -> int:
    import gi as _gi
    _gi.require_version("WebKit2", "4.1")
    from gi.repository import WebKit2

    print("chatterglass %s" % __version__)
    print("python           : %s" % sys.version.split()[0])
    print("GTK              : %d.%d.%d" % (Gtk.get_major_version(),
                                            Gtk.get_minor_version(),
                                            Gtk.get_micro_version()))
    print("WebKitGTK        : %d.%d.%d" % (WebKit2.get_major_version(),
                                            WebKit2.get_minor_version(),
                                            WebKit2.get_micro_version()))
    print("session type     : %s" % os.environ.get("XDG_SESSION_TYPE", "?"))
    print("desktop          : %s" % os.environ.get("XDG_CURRENT_DESKTOP", "?"))
    print("layer-shell      : %s" % ("supported" if layer.supported() else "NOT SUPPORTED"))
    print("instance         : %s" % ("running" if service.is_running() else "not running"))
    print("config           : %s" % config.path)
    print("slots            : %d" % len(config.slots))
    for slot in config.slots:
        print("  - %-6s %-22s %s" % (slot.kind, slot.name[:22],
                                      slot.url or "(built-in panel)"))
    socket_path = find_socket()
    if socket_path:
        print("discord socket   : %s" % socket_path)
    else:
        print("discord socket   : none listening (Discord probably not running)")
        for path in candidate_sockets()[:3]:
            state = "exists" if os.path.exists(path) else "missing"
            print("    %-60s %s" % (path, state))
    return 0


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="chatterglass",
        description="Transparent chat and Discord voice overlay for KDE Wayland.",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help="config file (default: %(default)s)")
    parser.add_argument("--check", action="store_true",
                        help="print diagnostics and exit without showing overlays")
    parser.add_argument("--debug", action="store_true",
                        help="verbose logging and web inspector enabled")
    parser.add_argument("--dev", action="store_true",
                        help="run from this folder without installing (development)")
    parser.add_argument("--install", action="store_true",
                        help="install without showing the window")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the installed copy and everything it created")
    parser.add_argument("--yes", action="store_true",
                        help="with --uninstall: do not ask for confirmation")
    parser.add_argument("--version", action="version", version="chatterglass " + __version__)

    group = parser.add_argument_group("commands against a running instance")
    for flag, method in COMMANDS.items():
        group.add_argument("--" + flag.replace("_", "-"), dest=flag,
                           action="store_true", help=method)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    Gtk.init([])
    config = Config.load(args.config)

    if args.check:
        return diagnose(config)

    if args.uninstall:
        if not install_state.is_installed():
            print("nothing installed at %s" % install_state.INSTALL_DIR)
            return 0
        if not args.yes:
            answer = input("Remove Chatterglass and everything it installed? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("cancelled")
                return 1
        failed = install_state.uninstall()
        if failed:
            print("could not remove:")
            for path in failed:
                print("  %s" % path)
            return 1
        print("removed")
        return 0

    if args.install:
        problems = install_state.missing_dependencies()
        if problems:
            print("missing requirements: %s" % ", ".join(problems), file=sys.stderr)
            return 1
        install_state.install(__version__)
        print("installed to %s" % install_state.INSTALL_DIR)
        return 0

    command = next((name for name in COMMANDS if getattr(args, name, False)), None)

    if command is not None:
        if service.call(COMMANDS[command]):
            return 0
        if command == "quit":
            print("chatterglass is not running", file=sys.stderr)
            return 1
        # Nothing to talk to yet: fall through and start normally.

    # Already running? Hand over rather than starting a second copy, which would
    # add a duplicate set of overlays on top of the first one's.
    if service.call("ShowControl"):
        print("chatterglass is already running; showing its window", file=sys.stderr)
        return 0

    if not layer.supported():
        print("warning: this compositor does not expose zwlr_layer_shell_v1; "
              "overlays will sit underneath fullscreen windows.", file=sys.stderr)

    # Until it is installed, the app is only an installer. Everything else stays
    # switched off, so nobody ends up running a half-configured copy.
    if not args.dev and not install_state.is_installed():
        if not installer_ui.ask_to_install(__version__):
            return 0
        # Hand over to the installed copy: replace this process with it, so what
        # keeps running is the installed app and never the unpacked download.
        launcher = os.path.join(install_state.INSTALL_DIR, "chatterglass-run")
        if os.path.exists(launcher):
            print("Installed. Starting the installed copy…", flush=True)
            try:
                os.execv("/bin/sh", ["sh", launcher, *sys.argv[1:]])
            except OSError as error:
                print("could not start %s: %s" % (launcher, error), flush=True)
        print("Installed. Start Chatterglass from your application menu.", flush=True)
        return 0
    config = Config.load(args.config)

    app = App(config, debug=args.debug)
    control = ControlWindow(app)
    app.control = control

    bus = service.ControlService(
        handlers={
            "ShowControl": lambda: control.show(),
            "ToggleControl": lambda: control.hide() if control.window.get_visible()
            else control.show(),
            "HideOverlays": lambda: app.set_all_visible(False),
            "ShowOverlays": lambda: app.set_all_visible(True),
            "ToggleClickThrough": lambda: app.toggle_click_through(),
            "ReloadAll": lambda: app.reload_all(),
            "Quit": lambda: app.quit(),
        },
        status_provider=app.status_text,
    )
    bus.own()
    app.service = bus

    app.start()
    control.show()

    try:
        Gtk.main()
    except KeyboardInterrupt:
        app.quit()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        app.shutdown()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
