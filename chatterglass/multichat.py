# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Build URLs for Colin Horn's multi-chat widget.

The merging of Twitch / YouTube / Kick chat happens on *his* server; this module
only constructs the query string the widget already understands, so Chatterglass
stays a thin front-end and he keeps getting the traffic and the credit.

Overlay by Colin Horn — https://chatoverlay.colinhorn.co.uk
Author — https://www.colinhorn.co.uk

Verified against the live widget's own code (see private/chatterglass-setup.md):
it reads these names through a helper `me("…")` in its bundle, plus
`getParam("…")` in connections/kick.js.
"""

from __future__ import annotations

import urllib.parse

BASE = "https://chatoverlay.colinhorn.co.uk/"
CREDIT_URL = "https://chatoverlay.colinhorn.co.uk"
CREDIT_TEXT = "Multi-chat overlay by Colin Horn"
AUTHOR_URL = "https://www.colinhorn.co.uk"

PLATFORMS = (("twitch", "Twitch"), ("youtube", "YouTube"), ("kick", "Kick"))


def _clean(value: str) -> str:
    return (value or "").strip().lstrip("@").strip()


def build_url(twitch: str = "", youtube: str = "", kick: str = "",
              badges: bool = True, monochrome: bool = False,
              bots: bool = True, commands: bool = True,
              animate: bool = True, fade: bool = True,
              bgcolor: str = "") -> str:
    """Return the widget URL for the given usernames and options."""
    params: dict[str, str] = {}
    for key, _label in PLATFORMS:
        value = _clean({"twitch": twitch, "youtube": youtube, "kick": kick}[key])
        if value:
            params[key] = value

    params["badges"] = "true" if badges else "false"
    params["monochrome"] = "true" if monochrome else "false"
    # Only send the switches when they differ from the widget's default, to keep
    # the URL readable.
    if not bots:
        params["bots"] = "false"
    if not commands:
        params["commands"] = "false"
    if not animate:
        params["animate"] = "false"
    if not fade:
        params["fade"] = "false"

    colour = _clean(bgcolor).lstrip("#")
    if colour:
        params["bgcolor"] = "#" + colour

    return BASE + "?" + urllib.parse.urlencode(params)


def slot_name(twitch: str = "", youtube: str = "", kick: str = "") -> str:
    """A short name for the slot: "Twitch + Kick"."""
    used = [label for key, label in PLATFORMS
            if _clean({"twitch": twitch, "youtube": youtube, "kick": kick}[key])]
    return " + ".join(used) if used else "Multi-chat"


def has_platform(twitch: str = "", youtube: str = "", kick: str = "") -> bool:
    return any(_clean({"twitch": twitch, "youtube": youtube, "kick": kick}[key])
               for key, _label in PLATFORMS)
