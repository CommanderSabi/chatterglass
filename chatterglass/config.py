# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Configuration model.

Everything lives next to the project by default (``config.json`` in the repo
root) so that a run leaves no residue anywhere else on the system. ``--config``
overrides it.
"""

from __future__ import annotations

import json
import os
import sys
import re
import urllib.parse
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(PACKAGE_DIR)


def default_config_path() -> str:
    """One config location, however the app was launched.

    Version 0.1.0 derived this from the program's own location, which produced a
    different config for the installed copy, for the portable download and for a
    source checkout - so settings appeared to vanish when switching between them.
    """
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "chatterglass", "config.json")


def legacy_config_paths():
    """Where older builds may have left a config, newest first."""
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    program = os.path.abspath(sys.argv[0] or "")
    candidates = []
    if program.endswith(".pyz"):
        candidates.append(os.path.join(os.path.dirname(program), "config.json"))
    candidates += [
        os.path.join(data_home, "chatterglass", "config.json"),
        os.path.join(PROJECT_DIR, "config.json"),
    ]
    existing = [c for c in candidates if os.path.exists(c)]

    def rank(path):
        """Prefer the config with the most slots, then the most recently written.

        A default config is newer than a carefully arranged one, so "newest"
        alone throws away the user's work.
        """
        try:
            with open(path, "r", encoding="utf-8") as handle:
                slots = len(json.load(handle).get("slots") or [])
        except (OSError, ValueError):
            slots = 0
        return (slots, os.path.getmtime(path))

    return sorted(existing, key=rank, reverse=True)


def adopt_legacy_config(path: str) -> None:
    """Move an older build's config into the canonical location (newest wins)."""
    if os.path.exists(path):
        return
    for old in legacy_config_paths():
        try:
            with open(old, "rb") as source:
                payload = source.read()
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            with open(path, "wb") as target:
                target.write(payload)
            os.chmod(path, 0o600)
            print("adopted existing settings from %s" % old, flush=True)
            return
        except OSError:
            continue


DEFAULT_CONFIG_PATH = default_config_path()

MIN_SIZE = 40
MAX_SIZE = 20000

# Chatterglass's own Discord application. See DiscordSettings below.
DEFAULT_CLIENT_ID = "1571677742397919393"

_ATTR_SRC_RE = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.I)
_URL_RE = re.compile(r"""https?://[^\s"'<>\\]+""", re.I)
_BARE_HOST_RE = re.compile(r"""^[a-z0-9][a-z0-9.\-]*(?::\d+)?(?:[/?#]\S*)?$""", re.I)

# Only real web content may be navigated to. Overlays are pasted in from third
# parties, so data:, file: and javascript: are refused rather than rendered
# above the user's screen (see private/audit/run-1).
ALLOWED_OVERLAY_SCHEMES = ("http", "https")


def is_allowed_overlay_url(url: Optional[str]) -> bool:
    """True when the URL is http(s) with a host and no embedded credentials."""
    if not url:
        return False
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme.lower() not in ALLOWED_OVERLAY_SCHEMES:
        return False
    if not parts.hostname:
        return False
    if parts.username or parts.password:
        return False
    return True


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def extract_url(raw: str) -> Optional[str]:
    """Pull a URL out of whatever was pasted.

    Handles a bare URL, an ``<iframe src="...">`` / ``<a href="...">`` snippet
    (what the overlay generators hand you for OBS), and a bare
    ``host:port/path`` such as a local TikFinity overlay.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if "<" in text:
        match = _ATTR_SRC_RE.search(text)
        if match:
            candidate = match.group(1).strip()
            return candidate if is_allowed_overlay_url(candidate) else None
    match = _URL_RE.search(text)
    if match:
        candidate = match.group(0).rstrip(".,);")
        return candidate if is_allowed_overlay_url(candidate) else None
    if _BARE_HOST_RE.match(text) and ("." in text or ":" in text or "/" in text):
        candidate = "http://" + text
        return candidate if is_allowed_overlay_url(candidate) else None
    return None


def _clamp(value: Any, low, high, fallback):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def _clampf(value: Any, low, high, fallback):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


@dataclass
class Slot:
    """One floating surface: either the Discord voice panel or a web overlay."""

    id: str = field(default_factory=new_id)
    kind: str = "web"              # "web" | "voice"
    name: str = "Overlay"
    url: Optional[str] = None
    x: int = 40
    y: int = 40
    width: int = 360
    height: int = 300
    monitor: int = 0
    opacity: float = 1.0
    zoom: float = 1.0
    click_through: bool = False
    visible: bool = True
    show_header: bool = True
    layer: str = "overlay"         # "overlay" | "top" | "bottom" | "background"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Slot":
        slot = cls()
        slot.id = str(data.get("id") or new_id())
        slot.kind = "voice" if data.get("kind") == "voice" else "web"
        slot.name = str(data.get("name") or "Overlay")[:80]
        url = data.get("url")
        slot.url = str(url) if url else None
        slot.x = _clamp(data.get("x", 40), 0, MAX_SIZE, 40)
        slot.y = _clamp(data.get("y", 40), 0, MAX_SIZE, 40)
        slot.width = _clamp(data.get("width", 360), MIN_SIZE, MAX_SIZE, 360)
        slot.height = _clamp(data.get("height", 300), MIN_SIZE, MAX_SIZE, 300)
        slot.monitor = _clamp(data.get("monitor", 0), 0, 16, 0)
        slot.opacity = _clampf(data.get("opacity", 1.0), 0.05, 1.0, 1.0)
        slot.zoom = _clampf(data.get("zoom", 1.0), 0.25, 4.0, 1.0)
        slot.click_through = bool(data.get("click_through", False))
        slot.visible = bool(data.get("visible", True))
        slot.show_header = bool(data.get("show_header", True))
        slot.layer = data.get("layer") if data.get("layer") in (
            "overlay", "top", "bottom", "background"
        ) else "overlay"
        return slot

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiscordSettings:
    enabled: bool = True
    # Chatterglass's registered Discord application. Public identifier, not a
    # secret: it is what Discord shows in the consent window. Baked in so that
    # nobody installing this app has to create an application of their own.
    client_id: str = DEFAULT_CLIENT_ID
    poll_seconds: float = 2.0
    show_join_history: bool = False
    access_token: str = ""
    refresh_token: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DiscordSettings":
        return cls(
            enabled=bool(data.get("enabled", True)),
            client_id=str(data.get("client_id") or DEFAULT_CLIENT_ID),
            poll_seconds=_clampf(data.get("poll_seconds", 2.0), 0.5, 60.0, 2.0),
            show_join_history=bool(data.get("show_join_history", False)),
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UpdatesSettings:
    auto: bool = True
    repo: str = "CommanderSabi/chatterglass"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UpdatesSettings":
        return cls(
            auto=bool(data.get("auto", True)),
            repo=str(data.get("repo") or "CommanderSabi/chatterglass"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DisplaySettings:
    show_on_screen: bool = True    # the person's own screen; OBS is separate

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DisplaySettings":
        return cls(show_on_screen=bool(data.get("show_on_screen", True)))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ObsSettings:
    enabled: bool = False          # off until asked for
    port: int = 8766

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObsSettings":
        return cls(
            enabled=bool(data.get("enabled", False)),
            port=max(1024, min(65535, int(data.get("port", 8766) or 8766))),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Config:
    slots: List[Slot] = field(default_factory=list)
    discord: DiscordSettings = field(default_factory=DiscordSettings)
    updates: UpdatesSettings = field(default_factory=UpdatesSettings)
    obs: ObsSettings = field(default_factory=ObsSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    path: str = DEFAULT_CONFIG_PATH

    @classmethod
    def default(cls, path: str = DEFAULT_CONFIG_PATH) -> "Config":
        voice = Slot(kind="voice", name="Discord", width=320, height=240)
        return cls(slots=[voice], discord=DiscordSettings(), path=path)

    @classmethod
    def load(cls, path: str = DEFAULT_CONFIG_PATH) -> "Config":
        adopt_legacy_config(path)
        if not os.path.exists(path):
            return cls.default(path)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return cls.default(path)
        slots = [Slot.from_dict(item) for item in data.get("slots", []) if isinstance(item, dict)]
        config = cls(
            slots=slots,
            discord=DiscordSettings.from_dict(data.get("discord", {}) or {}),
            updates=UpdatesSettings.from_dict(data.get("updates", {}) or {}),
            obs=ObsSettings.from_dict(data.get("obs", {}) or {}),
            display=DisplaySettings.from_dict(data.get("display", {}) or {}),
            path=path,
        )
        if not config.slots:
            return cls.default(path)
        return config

    def save(self) -> None:
        payload = {
            "version": 1,
            "discord": self.discord.to_dict(),
            "updates": self.updates.to_dict(),
            "obs": self.obs.to_dict(),
            "display": self.display.to_dict(),
            "slots": [slot.to_dict() for slot in self.slots],
        }
        directory = os.path.dirname(os.path.abspath(self.path))
        if os.path.isfile(directory):
            raise OSError("refusing to write config inside a file: %s" % directory)
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, self.path)
        # This file holds Discord access and refresh tokens: keep it to the user.
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def slot(self, slot_id: str) -> Optional[Slot]:
        return next((s for s in self.slots if s.id == slot_id), None)

    def remove(self, slot_id: str) -> None:
        self.slots = [s for s in self.slots if s.id != slot_id]
