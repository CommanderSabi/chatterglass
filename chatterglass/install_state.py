# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Install and uninstall, manifest-driven.

Nothing is written outside the manifest's paths, and uninstall removes every one
of them, so an uninstall leaves no trash on the machine.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
from typing import List, Optional

APP = "chatterglass"
DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
INSTALL_DIR = os.path.join(DATA_HOME, APP)
MANIFEST = os.path.join(INSTALL_DIR, "install-manifest.json")
APPS_DIR = os.path.join(DATA_HOME, "applications")
DESKTOP_ENTRY = os.path.join(APPS_DIR, "chatterglass.desktop")
ICONS_ROOT = os.path.join(DATA_HOME, "icons", "hicolor")
ICON_SIZES = (32, 48, 64, 128, 256, 512)

DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name=Chatterglass
GenericName=Chat Overlay
Comment=Transparent chat overlays and a Discord voice panel over your game
Exec={exec_path}
Path={install_dir}
Icon=chatterglass
Terminal=false
StartupNotify=false
Categories=AudioVideo;Utility;
Keywords=overlay;chat;discord;twitch;stream;
"""


def project_root() -> Optional[str]:
    """The folder we were started from, if this is a source checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    if os.path.isdir(os.path.join(root, "icons")) and os.path.exists(
            os.path.join(root, "chatterglass-run")):
        return root
    return None


def is_installed() -> bool:
    """Installed whether the program is a package folder or the single file."""
    if not os.path.exists(MANIFEST):
        return False
    return (os.path.isdir(os.path.join(INSTALL_DIR, "chatterglass"))
            or os.path.exists(os.path.join(INSTALL_DIR, "chatterglass.pyz")))


def installed_version() -> Optional[str]:
    try:
        with open(MANIFEST, "r", encoding="utf-8") as handle:
            return json.load(handle).get("version")
    except (OSError, ValueError):
        return None


def running_from_install() -> bool:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(here) == os.path.join(INSTALL_DIR, "chatterglass")


def missing_dependencies() -> List[str]:
    problems: List[str] = []
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # noqa: F401
    except Exception:  # noqa: BLE001
        problems.append("python3-gobject with GTK 3")
    try:
        import gi
        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2  # noqa: F401
    except Exception:  # noqa: BLE001
        problems.append("webkit2gtk4.1")
    try:
        import gi
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell  # noqa: F401
    except Exception:  # noqa: BLE001
        problems.append("gtk-layer-shell (GtkLayerShell-0.1 typelib)")
    return problems


def _copy_tree(source: str, destination: str, skip: tuple) -> None:
    os.makedirs(destination, exist_ok=True)
    for entry in os.listdir(source):
        if entry in skip:
            continue
        src = os.path.join(source, entry)
        dst = os.path.join(destination, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)


def running_program() -> Optional[str]:
    """The single-file build we were started from, if that is how we are running."""
    candidate = os.path.abspath(sys.argv[0] or "")
    if candidate and os.path.isfile(candidate) and candidate.endswith(".pyz"):
        return candidate
    return None


def _write_icons(destination_root: str, source_root: Optional[str]) -> List[str]:
    """Copy icons from the loose files or from the embedded copies."""
    written: List[str] = []
    for size in ICON_SIZES:
        target_dir = os.path.join(ICONS_ROOT, "%dx%d" % (size, size), "apps")
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "chatterglass.png")
        loose = os.path.join(source_root, "icons", "chatterglass-%d.png" % size) if source_root else None
        if loose and os.path.exists(loose):
            shutil.copy2(loose, target)
        else:
            from . import _assets
            encoded = _assets.ICONS.get(size)
            if not encoded:
                continue
            with open(target, "wb") as handle:
                handle.write(base64.b64decode(encoded))
        written.append(target)
    return written


def install(version: str, desktop_shortcut: bool = False) -> List[str]:
    """Install the program. Returns every path created."""
    root = project_root()
    single = running_program()
    if root is None and single is None:
        raise RuntimeError(
            "cannot install: run install.sh from the unpacked release, or the "
            "single-file chatterglass.pyz")

    created: List[str] = []
    os.makedirs(INSTALL_DIR, exist_ok=True)
    created.append(INSTALL_DIR)

    # Preserve an existing config.json (Discord tokens) across reinstall.
    existing_config = os.path.join(INSTALL_DIR, "config.json")
    kept_config = None
    if os.path.exists(existing_config):
        kept_config = existing_config + ".keep"
        shutil.move(existing_config, kept_config)

    if root is not None:
        _copy_tree(os.path.join(root, "chatterglass"),
                   os.path.join(INSTALL_DIR, "chatterglass"),
                   skip=("__pycache__", "config.json"))
        shutil.rmtree(os.path.join(INSTALL_DIR, "chatterglass", "__pycache__"),
                      ignore_errors=True)
        if os.path.isdir(os.path.join(root, "icons")):
            _copy_tree(os.path.join(root, "icons"),
                       os.path.join(INSTALL_DIR, "icons"), skip=())
        for name in ("README.md", "LICENSE", "NOTICE", "PRIVACY.md"):
            src = os.path.join(root, name)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(INSTALL_DIR, name))
        program = "chatterglass"
        launcher_body = ('exec python3 -m chatterglass "$@"\n')
    else:
        # Single-file build: the program is this one file.
        target = os.path.join(INSTALL_DIR, "chatterglass.pyz")
        shutil.copy2(single, target)
        os.chmod(target, 0o755)
        created.append(target)
        program = target
        launcher_body = ('exec python3 "%s" "$@"\n' % target)

    if kept_config and os.path.exists(kept_config):
        shutil.move(kept_config, existing_config)

    # Carry an existing Discord authorisation from the source folder, so
    # installing does not mean authorising again.
    source_config = os.path.join(root, "config.json") if root else None
    target_config = os.path.join(INSTALL_DIR, "config.json")
    if source_config and os.path.exists(source_config) and not os.path.exists(target_config):
        shutil.copy2(source_config, target_config)
        os.chmod(target_config, 0o600)

    created.extend(_write_icons(INSTALL_DIR, root))

    launcher = os.path.join(INSTALL_DIR, "chatterglass-run")
    with open(launcher, "w", encoding="utf-8") as handle:
        handle.write("#!/usr/bin/env bash\n"
                     "set -eu\n"
                     'cd "$(dirname "$(readlink -f "$0")")"\n'
                     "export PYTHONDONTWRITEBYTECODE=1\n" + launcher_body)
    os.chmod(launcher, 0o755)
    created.append(launcher)

    os.makedirs(APPS_DIR, exist_ok=True)
    with open(DESKTOP_ENTRY, "w", encoding="utf-8") as handle:
        handle.write(DESKTOP_TEMPLATE.format(exec_path=launcher, install_dir=INSTALL_DIR))
    os.chmod(DESKTOP_ENTRY, 0o755)
    created.append(DESKTOP_ENTRY)

    if desktop_shortcut:
        shortcut = os.path.expanduser("~/Desktop/chatterglass.desktop")
        try:
            shutil.copy2(DESKTOP_ENTRY, shortcut)
            os.chmod(shortcut, 0o755)
            created.append(shortcut)
        except OSError:
            pass

    with open(MANIFEST, "w", encoding="utf-8") as handle:
        json.dump({"version": version, "paths": created}, handle, indent=2)

    return created


def _revoke_discord_token(install_dir: str) -> str:
    """Best effort: ask Discord to revoke the stored token before deleting it.

    Discord's revoke endpoint normally wants a client secret, which a public
    client does not have, so this can legitimately fail. The authoritative
    removal is the user's Authorised Apps page, and the caller says so.
    """
    import json as _json
    import urllib.parse
    import urllib.request

    from .config import DEFAULT_CONFIG_PATH
    try:
        with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as handle:
            config = _json.load(handle)
    except (OSError, ValueError):
        return "no stored token"
    token = (config.get("discord") or {}).get("access_token") or ""
    client_id = (config.get("discord") or {}).get("client_id") or ""
    if not token:
        return "no stored token"

    body = urllib.parse.urlencode({
        "token": token,
        "token_type_hint": "access_token",
        "client_id": client_id,
    }).encode("ascii")
    request = urllib.request.Request(
        "https://discord.com/api/v10/oauth2/token/revoke", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return "revoked" if response.status in (200, 204) else "revoke returned %s" % response.status
    except Exception as error:  # noqa: BLE001
        return "could not revoke (%s)" % error


def config_paths() -> List[str]:
    """Every config file this app may have written, current and legacy."""
    from .config import DEFAULT_CONFIG_PATH, legacy_config_paths
    paths = [DEFAULT_CONFIG_PATH]
    paths += legacy_config_paths()
    paths.append(os.path.join(INSTALL_DIR, "config.json"))
    seen, unique = set(), []
    for path in paths:
        key = os.path.abspath(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _path_is_ours(path: str) -> bool:
    """True only for the places this app is allowed to delete.

    The manifest is a plain file; a crafted one must not be able to turn an
    uninstall into "delete any directory on the disk".
    """
    protected = {
        os.path.abspath(INSTALL_DIR),
        os.path.abspath(DESKTOP_ENTRY),
        os.path.abspath(ICONS_ROOT),
        os.path.expanduser("~/Desktop/chatterglass.desktop"),
    }
    target = os.path.abspath(path)
    if target in protected:
        return True
    if target.startswith(os.path.abspath(INSTALL_DIR) + os.sep):
        return True
    if target.startswith(os.path.abspath(ICONS_ROOT) + os.sep):
        return True
    if target == os.path.abspath(APPS_DIR + os.sep + "chatterglass.desktop"):
        return True
    return target == os.path.abspath(os.path.expanduser("~/Desktop/chatterglass.desktop"))


def uninstall() -> List[str]:
    """Remove everything the installer created. Returns paths that survived."""
    revoke_note = _revoke_discord_token(INSTALL_DIR)
    config_targets = config_paths()
    failed: List[str] = []
    paths: List[str] = []
    try:
        with open(MANIFEST, "r", encoding="utf-8") as handle:
            paths = json.load(handle).get("paths", [])
    except (OSError, ValueError):
        paths = [INSTALL_DIR, DESKTOP_ENTRY]

    for path in paths:
        if not os.path.exists(path):
            continue
        if not _path_is_ours(path):
            failed.append(path)
            continue
        try:
            if os.path.isdir(path) and os.path.abspath(path) == os.path.abspath(INSTALL_DIR):
                shutil.rmtree(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            failed.append(path)

    # Remove icon directories we emptied, but only if now empty, and prune the
    # hicolor tree if it only existed for us.
    for size in ICON_SIZES:
        folder = os.path.join(ICONS_ROOT, "%dx%d" % (size, size), "apps")
        try:
            if os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
                parent = os.path.dirname(folder)
                if os.path.isdir(parent) and not os.listdir(parent):
                    os.rmdir(parent)
        except OSError:
            pass

    for leftover in (ICONS_ROOT, os.path.dirname(ICONS_ROOT), APPS_DIR):
        try:
            if os.path.isdir(leftover) and not os.listdir(leftover):
                os.rmdir(leftover)
        except OSError:
            pass

    # Settings and the Discord token are part of the app: remove them too.
    for config_file in config_targets:
        try:
            if os.path.exists(config_file):
                os.remove(config_file)
            parent = os.path.dirname(config_file)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        except OSError:
            failed.append(config_file)

    try:
        shutil.rmtree(INSTALL_DIR)
    except OSError:
        if os.path.isdir(INSTALL_DIR):
            failed.append(INSTALL_DIR)

    globals()["LAST_REVOKE"] = revoke_note
    return failed


def program_path() -> Optional[str]:
    """The single-file program this app is (installed copy, or the portable file)."""
    installed = os.path.join(INSTALL_DIR, "chatterglass.pyz")
    if os.path.exists(installed):
        return installed
    candidate = os.path.abspath(sys.argv[0] or "")
    if candidate.endswith(".pyz") and os.path.isfile(candidate):
        return candidate
    return None


def apply_update(zip_path: str) -> str:
    """Replace the program with the one inside a verified release zip.

    The running process keeps its own copy, so the new file takes effect on the
    next start; nothing is swapped mid-run.
    """
    import tempfile
    import zipfile

    target = program_path()
    if target is None:
        raise RuntimeError("cannot update: not running as a single-file build")

    # Defence in depth: the caller already verified, but never unpack an
    # unsigned archive even if some future caller forgets.
    from . import release_key
    if not release_key.verify_signature(zip_path, zip_path + ".sig"):
        raise RuntimeError("refusing to install an update whose signature does not verify")

    with zipfile.ZipFile(zip_path) as bundle:
        if "chatterglass.pyz" not in bundle.namelist():
            raise RuntimeError("release does not contain chatterglass.pyz")
        payload = bundle.read("chatterglass.pyz")

    directory = os.path.dirname(target) or "."
    handle = tempfile.NamedTemporaryFile(dir=directory, prefix=".chatterglass-update-",
                                         delete=False)
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.chmod(handle.name, 0o755)
        os.replace(handle.name, target)
    except Exception:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return target
