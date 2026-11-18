# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""Runtime: owns the config, the overlay windows and the Discord client."""

from __future__ import annotations

from typing import Optional

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import GLib, Gtk  # noqa: E402

from .config import Config, Slot, extract_url, new_id  # noqa: E402
from .discord_ipc import DiscordClient  # noqa: E402
from .obs import OverlayServer  # noqa: E402
from .overlays import OverlayManager  # noqa: E402


class App:
    def __init__(self, config: Config, debug: bool = False):
        self.config = config
        self.debug = debug
        self.manager = OverlayManager(debug=debug)
        self.discord: Optional[DiscordClient] = None
        self.obs = OverlayServer(self, config.obs.port)
        self.control = None
        self.mode = "edit"
        self.mode = "edit"
        self.voice_state = {"connected": False, "status": "idle", "channel": None,
                            "guild": None, "users": []}
        self.discord_status = ("idle", "")

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self.config.display.show_on_screen:
            for slot in self.config.slots:
                if slot.visible:
                    self.manager.add(slot)
        self._sync_discord()
        if self.config.obs.enabled:
            self.obs.start()

    def shutdown(self) -> None:
        try:
            self.config.save()
        except OSError as error:
            print("could not save config: %s" % error, flush=True)
        if self.discord is not None:
            self.discord.stop()
            self.discord = None
        self.manager.shutdown()
        self.obs.stop()
        bus = getattr(self, "service", None)
        if bus is not None:
            bus.release()

    # -- discord ---------------------------------------------------------
    def set_screen_overlays(self, enabled: bool) -> None:
        """Show the overlays on this screen, or leave them to OBS only.

        Turning it off destroys the windows (so nothing is drawn and nothing is
        clickable); the layout, chat URLs and visibility settings are kept and
        come back exactly as they were.
        """
        self.config.display.show_on_screen = bool(enabled)
        self.config.save()
        if enabled:
            for slot in self.config.slots:
                if slot.visible:
                    self.manager.apply(slot)
        else:
            self.manager.shutdown()
        self._sync_discord()
        if self.control is not None:
            self.control.refresh_rows()

    def set_obs_enabled(self, enabled: bool) -> Optional[str]:
        """Start or stop the local overlay page. Returns its URL when running."""
        self.config.obs.enabled = bool(enabled)
        self.config.save()
        if enabled:
            return self.obs.start()
        self.obs.stop()
        return None

    def _sync_discord(self) -> None:
        wanted = (
            self.config.discord.enabled
            and any(slot.kind == "voice" and slot.visible for slot in self.config.slots)
        )
        if wanted and self.discord is None:
            self.discord = DiscordClient(
                client_id=self.config.discord.client_id,
                poll_seconds=self.config.discord.poll_seconds,
                access_token=self.config.discord.access_token,
                refresh_token=self.config.discord.refresh_token,
                on_token=self._store_token,
                on_update=self._on_voice_update,
                on_status=self._on_discord_status,
                verbose=self.debug,
            )
            self.discord.start()
        elif not wanted and self.discord is not None:
            self.discord.stop()
            self.discord = None
            self.voice_state = {"connected": False, "status": "idle", "channel": None,
                                "guild": None, "users": []}
            self.manager.broadcast_voice(self.voice_state)

    def connect_discord(self) -> None:
        """User pressed Connect: start (or restart) the Allow flow."""
        self._sync_discord()
        if self.discord is not None:
            self.discord.authorise()

    def _store_token(self, tokens: dict) -> None:
        self.config.discord.access_token = str(tokens.get("access_token") or "")
        self.config.discord.refresh_token = str(tokens.get("refresh_token") or "")
        try:
            self.config.save()
        except OSError as error:
            print("could not save Discord token: %s" % error, flush=True)

    def _on_voice_update(self, state: dict) -> None:
        GLib.idle_add(self._deliver_voice, state)

    def _on_discord_status(self, status: str, detail: str) -> None:
        GLib.idle_add(self._deliver_status, status, detail)

    def _deliver_voice(self, state: dict) -> bool:
        self.voice_state = state
        self.manager.broadcast_voice(state)
        if self.control is not None:
            self.control.refresh_status()
        return False

    def _deliver_status(self, status: str, detail: str) -> bool:
        self.discord_status = (status, detail)
        if self.control is not None:
            self.control.refresh_status()
        return False

    # -- slots -----------------------------------------------------------
    def update_slot(self, slot: Slot, resave: bool = True) -> None:
        if self.config.display.show_on_screen:
            self.manager.apply(slot)
        if resave:
            self.config.save()
        self._sync_discord()
        if self.control is not None:
            self.control.refresh_rows()

    def add_slot(self, kind: str = "web", name: Optional[str] = None,
                 url: Optional[str] = None) -> Slot:
        index = len([s for s in self.config.slots if s.kind == kind]) + 1
        slot = Slot(
            id=new_id(),
            kind=kind,
            name=name or ("Discord" if kind == "voice" else "Chat %d" % index),
            url=url,
            x=40 + 24 * index,
            y=40 + 24 * index,
            width=320 if kind == "voice" else 380,
            height=240 if kind == "voice" else 420,
            # Slightly see-through by default, like the Discord panel. The page
            # still paints its own background; this only softens the whole
            # surface a touch, and the slider goes to 1.0 if you want it solid.
            opacity=0.72 if kind == "voice" else 0.9,
        )
        self.config.slots.append(slot)
        self.manager.add(slot)
        self.config.save()
        self._sync_discord()
        if self.control is not None:
            self.control.refresh_rows(select=slot.id)
        return slot

    def add_from_text(self, text: str) -> Optional[Slot]:
        url = extract_url(text)
        if url is None:
            return None
        slot = self.add_slot(kind="web", url=url)
        self.manager.reload(slot.id)
        return slot

    def remove_slot(self, slot_id: str) -> None:
        self.config.remove(slot_id)
        self.manager.remove(slot_id)
        self.config.save()
        self._sync_discord()
        if self.control is not None:
            self.control.refresh_rows()

    def reload_slot(self, slot_id: str) -> None:
        self.manager.reload(slot_id)

    def reload_all(self) -> None:
        for slot in self.config.slots:
            self.manager.reload(slot.id)

    def set_click_through(self, value: bool, save: bool = True) -> None:
        """Every overlay either takes the mouse or lets it through.

        Edit mode turns this off (overlays clickable) so you can see and poke at
        what you are arranging; live mode turns it on.
        """
        for slot in self.config.slots:
            slot.click_through = bool(value)
            self.manager.apply(slot)
        if save:
            self.config.save()
        if self.control is not None:
            self.control.refresh_state()

    def toggle_click_through(self) -> bool:
        target = not all(slot.click_through for slot in self.config.slots)
        self.set_click_through(target)
        return target

    def set_discord_settings(self, client_id: str, poll_seconds: float) -> None:
        """Store a new application ID / poll interval and reconnect."""
        self.config.discord.client_id = (client_id or "").strip()
        self.config.discord.poll_seconds = max(0.5, float(poll_seconds))
        self.config.save()
        if self.discord is not None:
            self.discord.stop()
            self.discord = None
            self.discord_status = ("idle", "")
        self._sync_discord()
        if self.control is not None:
            self.control.refresh_status()

    def status_text(self) -> str:
        status, detail = self.discord_status
        channel = (self.voice_state or {}).get("channel")
        users = len((self.voice_state or {}).get("users") or [])
        if status == "connected" and channel:
            return "discord connected: %s (%d)" % (channel, users)
        return "discord %s%s" % (status, (" (%s)" % detail) if detail else "")

    def set_all_visible(self, visible: bool) -> None:
        for slot in self.config.slots:
            slot.visible = visible
            self.manager.apply(slot)
        self.config.save()
        self._sync_discord()
        if self.control is not None:
            self.control.refresh_rows()

    def quit(self) -> None:
        self.shutdown()
        Gtk.main_quit()
