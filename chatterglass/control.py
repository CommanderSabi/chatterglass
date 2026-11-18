# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Commander Sabi
"""The control window: the only normal (non-overlay) window in the app."""

from __future__ import annotations

import html
import os
import threading

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

STYLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")

from .config import Slot, extract_url  # noqa: E402
from . import __version__, install_state, multichat, updater  # noqa: E402


STATUS_TEXT = {
    "idle": "Discord idle",
    "connecting": "Connecting to Discord…",
    "connected": "Discord connected",
    "disconnected": "Discord disconnected",
    "discord-not-running": "Discord is not running \u2014 start Discord first",
    "app-id-missing": "Discord: application ID needed",
    "app-id-rejected": "Discord rejected the application ID",
    "authorising": "Waiting for you to click Allow in Discord",
    "auth-failed": "Discord authorisation failed",
    "not-connected": "Discord: not connected",
}


class ControlWindow:
    def __init__(self, app):
        self.app = app
        self.selected_id = None
        self._loading = False
        self._syncing = False

        self._load_styles()

        self.window = Gtk.Window(title="Chatterglass")
        self.window.set_default_size(760, 520)
        self.window.set_border_width(0)
        self.window.connect("delete-event", self._on_delete)

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("Chatterglass")
        self.window.set_titlebar(header)

        # Update indicator: a small downward arrow, revealed only when a newer
        # release exists. Glows for people who turned automatic updates off.
        self.update_button = Gtk.Button()
        self.update_button.set_relief(Gtk.ReliefStyle.NONE)
        self.update_label = Gtk.Label()
        self.update_label.set_markup("<span foreground='#f0a13c'>\u25bc</span>")
        self.update_button.add(self.update_label)
        self.update_button.set_tooltip_text("Check for updates")
        self.update_button.set_no_show_all(True)
        self.update_button.connect("clicked", lambda *_: self._on_update_clicked())
        header.pack_start(self.update_button)
        self._update_available = None
        self._pulse_source = None
        self._pulse_bright = False
        GLib.timeout_add_seconds(6, self._begin_update_check)

        self.status_label = Gtk.Label(label="Discord idle")
        self.status_label.get_style_context().add_class("dim-label")
        header.pack_end(self.status_label)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_name("cgroot")
        self.window.add(root)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(250)
        root.pack_start(paned, True, True, 0)

        # ---- left: two separate lists, because a voice panel is not a chat
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.set_border_width(8)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(left)
        paned.pack1(scroller, True, False)

        voice_title = Gtk.Label(xalign=0)
        voice_title.set_markup("<span size='small' weight='bold' foreground='#8b8f9c'>VOICE PANEL</span>")
        left.pack_start(voice_title, False, False, 6)
        self.voice_list = self._make_list()
        left.pack_start(self.voice_list, False, False, 0)

        chats_title = Gtk.Label(xalign=0)
        chats_title.set_markup("<span size='small' weight='bold' foreground='#8b8f9c'>CHAT OVERLAYS</span>")
        left.pack_start(chats_title, False, False, 14)
        self.chat_list = self._make_list()
        left.pack_start(self.chat_list, False, False, 0)

        # ---- right: editor
        self.editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.editor.set_border_width(12)
        paned.pack2(self.editor, True, False)

        self.placeholder = Gtk.Label(
            label="Select an overlay, or add one below.",
        )
        self.placeholder.get_style_context().add_class("dim-label")
        self.editor.pack_start(self.placeholder, False, False, 0)

        self.form = Gtk.Grid(column_spacing=10, row_spacing=8)
        self.editor.pack_start(self.form, False, False, 0)

        row = 0
        self.kind_label = Gtk.Label(label="", xalign=0)
        self.kind_label.get_style_context().add_class("dim-label")
        self.form.attach(Gtk.Label(label="Type", xalign=0), 0, row, 1, 1)
        self.form.attach(self.kind_label, 1, row, 2, 1)
        row += 1

        self.name_entry = Gtk.Entry()
        self.name_entry.connect("changed", self._on_changed)
        self.form.attach(Gtk.Label(label="Name", xalign=0), 0, row, 1, 1)
        self.form.attach(self.name_entry, 1, row, 2, 1)
        row += 1

        self.url_entry = Gtk.Entry()
        self.url_entry.set_placeholder_text("https://… or paste an <iframe> snippet")
        self.url_entry.connect("changed", self._on_changed)
        self.url_entry.connect("activate", self._on_url_committed)
        self.url_label = Gtk.Label(label="URL", xalign=0)
        self.form.attach(self.url_label, 0, row, 1, 1)
        self.form.attach(self.url_entry, 1, row, 2, 1)
        row += 1

        self.url_hint = Gtk.Label(xalign=0, wrap=True)
        self.url_hint.get_style_context().add_class("dim-label")
        self.url_hint.set_text("Press Enter (or Reload) to load a new URL; nothing "
                               "reloads while you are still typing.")
        self.url_hint_pad = Gtk.Label(label="", xalign=0)
        self.form.attach(self.url_hint, 1, row, 2, 1)
        self.form.attach(self.url_hint_pad, 0, row, 1, 1)
        row += 1

        self.pos_row = row
        self.x_spin = self._spin(0, 12000, 1)
        self.y_spin = self._spin(0, 12000, 1)
        self.w_spin = self._spin(40, 12000, 10)
        self.h_spin = self._spin(40, 12000, 10)
        self.form.attach(Gtk.Label(label="Position", xalign=0), 0, row, 1, 1)
        box = Gtk.Box(spacing=6)
        for widget, prefix in ((self.x_spin, "x"), (self.y_spin, "y")):
            box.pack_start(Gtk.Label(label=prefix), False, False, 0)
            box.pack_start(widget, False, False, 0)
        self.form.attach(box, 1, row, 1, 1)
        row += 1

        self.form.attach(Gtk.Label(label="Size", xalign=0), 0, row, 1, 1)
        box = Gtk.Box(spacing=6)
        for widget, prefix in ((self.w_spin, "w"), (self.h_spin, "h")):
            box.pack_start(Gtk.Label(label=prefix), False, False, 0)
            box.pack_start(widget, False, False, 0)
        self.form.attach(box, 1, row, 1, 1)
        row += 1

        self.monitor_spin = self._spin(0, 8, 1)
        self.form.attach(Gtk.Label(label="Monitor", xalign=0), 0, row, 1, 1)
        self.form.attach(self.monitor_spin, 1, row, 2, 1)
        row += 1

        self.opacity_label = Gtk.Label(label="Background", xalign=0)
        self.opacity_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05)
        self.opacity_scale.set_draw_value(True)
        self.opacity_scale.connect("value-changed", self._on_changed)
        self.form.attach(self.opacity_label, 0, row, 1, 1)
        self.form.attach(self.opacity_scale, 1, row, 2, 1)
        row += 1

        self.zoom_spin = self._spin(0.25, 4.0, 0.05)
        self.form.attach(Gtk.Label(label="Zoom", xalign=0), 0, row, 1, 1)
        self.form.attach(self.zoom_spin, 1, row, 2, 1)
        row += 1

        # No Layer control: every overlay belongs on the overlay layer. Re-adding
        # the dropdown, with its confirmation dialog, is described in
        # private/chatterglass-setup.md if it is ever wanted back.

        # Voice panel only: the channel name line at the top of the panel.
        self.header_label = Gtk.Label(label="Panel", xalign=0)
        self.show_header = Gtk.Switch()
        self.show_header.set_halign(Gtk.Align.START)
        self.show_header.connect("notify::active", self._on_changed)
        self.form.attach(self.header_label, 0, row, 1, 1)
        self.form.attach(self.show_header, 1, row, 2, 1)
        self.header_row = row
        row += 1

        self.form.attach(Gtk.Label(label="Mouse", xalign=0), 0, row, 1, 1)
        mouse_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.click_through = Gtk.Switch()
        self.click_through.set_halign(Gtk.Align.START)
        self.click_through.connect("notify::active", self._on_changed)
        mouse_box.pack_start(self.click_through, False, False, 0)
        mouse_label = Gtk.Label(xalign=0, wrap=True)
        mouse_label.get_style_context().add_class("dim-label")
        mouse_label.set_text(
            "On: this overlay ignores the mouse completely, so it can never block "
            "your game. Off: it takes the mouse, so you can click and scroll inside "
            "it."
        )
        mouse_box.pack_start(mouse_label, False, False, 0)
        self.form.attach(mouse_box, 1, row, 2, 1)
        row += 1

        actions = Gtk.Box(spacing=6)
        self.editor.pack_start(actions, False, False, 6)
        self.reload_button = Gtk.Button(label="Reload")
        self.reload_button.connect("clicked", self._on_reload)
        self.delete_button = Gtk.Button(label="Remove")
        self.delete_button.get_style_context().add_class("destructive-action")
        self.delete_button.connect("clicked", self._on_delete_slot)
        actions.pack_start(self.reload_button, False, False, 0)
        actions.pack_start(self.delete_button, False, False, 0)

        self.hint = Gtk.Label(xalign=0, wrap=True)
        self.hint.get_style_context().add_class("dim-label")
        self.hint.set_line_wrap(True)
        self.hint.set_max_width_chars(46)
        self.editor.pack_start(self.hint, False, False, 6)

        # ---- bottom bar
        bar = Gtk.Box(spacing=6)
        bar.set_border_width(8)
        root.pack_start(bar, False, False, 0)

        add_web = Gtk.Button(label="Add overlay")
        add_web.set_tooltip_text("Type or paste a URL or an <iframe> snippet by hand")
        add_web.connect("clicked", self._on_add_dialog)
        bar.pack_start(add_web, False, False, 0)

        add_clip = Gtk.Button(label="Add from clipboard")
        add_clip.set_tooltip_text(
            "Takes the URL or <iframe> snippet currently on the clipboard and adds it "
            "as a new overlay straight away"
        )
        add_clip.connect("clicked", self._on_add_clipboard)
        bar.pack_start(add_clip, False, False, 0)

        # Only offered when there is no voice panel: you can only be in one voice
        # channel, so a second panel would show the same thing twice.
        self.add_voice_button = Gtk.Button(label="Restore Discord panel")
        self.add_voice_button.set_tooltip_text("Add the Discord voice panel back")
        self.add_voice_button.connect("clicked", self._on_add_voice)
        bar.pack_start(self.add_voice_button, False, False, 0)

        settings_button = Gtk.Button(label="Settings")
        settings_button.set_tooltip_text("Discord application ID and polling")
        settings_button.connect("clicked", lambda *_: self.open_settings_window())
        bar.pack_start(settings_button, False, False, 0)

        self.connect_label = Gtk.Label()
        self.connect_button = Gtk.Button()
        self.connect_button.add(self.connect_label)
        self.connect_button.set_tooltip_text(
            "Asks Discord for permission to see which voice channel you are in")
        self.connect_button.connect("clicked", self._on_connect)
        bar.pack_start(self.connect_button, False, False, 0)

        bar.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 4)

        hide_all = Gtk.Button(label="Hide all")
        hide_all.connect("clicked", lambda *_: self.app.set_all_visible(False))
        show_all = Gtk.Button(label="Show all")
        show_all.connect("clicked", lambda *_: self.app.set_all_visible(True))
        bar.pack_start(hide_all, False, False, 0)
        bar.pack_start(show_all, False, False, 0)

        # A bulk action, not a state: its label always names what pressing it
        # will do, and a separate line reports the actual current state. One
        # control cannot honestly be both.
        self.click_button = Gtk.Button(label="Pass clicks through everything")
        self.click_button.set_tooltip_text(
            "Set every overlay to the same mouse behaviour. Individual overlays can "
            "still differ; this button states what it will do, not what is set now."
        )
        self.click_button.connect("clicked", self._on_bulk_click)
        bar.pack_end(self.click_button, False, False, 0)

        self.click_state = Gtk.Label(xalign=0)
        self.click_state.get_style_context().add_class("dim-label")
        bar.pack_start(self.click_state, False, False, 6)

        escape = Gtk.Label(xalign=0)
        escape.set_markup(
            "<span size='small'>This window stays open. Minimise it while you play; "
            "<b>Hide all</b> stops the overlays without quitting, and closing this "
            "window stops everything.</span>"
        )
        escape.get_style_context().add_class("dim-label")
        escape.set_line_wrap(True)
        self.editor.pack_end(escape, False, False, 8)

        self.refresh_rows()
        self.refresh_status()
        self.refresh_state()

    # -- helpers ---------------------------------------------------------
    def _load_styles(self) -> None:
        provider = Gtk.CssProvider()
        try:
            if os.path.exists(STYLE_PATH):
                provider.load_from_path(STYLE_PATH)
            else:
                from . import _assets
                provider.load_from_data(_assets.STYLE_CSS)
        except Exception as error:  # noqa: BLE001 - styling must never be fatal
            print("could not load styles: %r" % (error,), flush=True)
            return
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _spin(self, low: float, high: float, step: float) -> Gtk.SpinButton:
        widget = Gtk.SpinButton.new_with_range(low, high, step)
        widget.set_numeric(True)
        widget.connect("value-changed", self._on_changed)
        return widget

    def _make_list(self) -> Gtk.ListBox:
        widget = Gtk.ListBox()
        widget.set_selection_mode(Gtk.SelectionMode.SINGLE)
        widget.connect("row-selected", self._on_row_selected)
        return widget

    def show(self) -> None:
        self.window.deiconify()
        self.window.show_all()
        self.window.present()
        self.refresh_rows()
        self.refresh_state()

    def hide(self) -> None:
        self.window.hide()

    def current_slot(self) -> Slot | None:
        if self.selected_id is None:
            return None
        return self.app.config.slot(self.selected_id)

    def sync_form(self) -> None:
        """Push slot values into the form after a drag changed them."""
        slot = self.current_slot()
        if slot is None:
            return
        self._loading = True
        try:
            self.x_spin.set_value(slot.x)
            self.y_spin.set_value(slot.y)
            self.w_spin.set_value(slot.width)
            self.h_spin.set_value(slot.height)
            self.opacity_scale.set_value(slot.opacity)
        finally:
            self._loading = False

    # -- list ------------------------------------------------------------
    def refresh_rows(self, select: str | None = None) -> None:
        if select is not None:
            self.selected_id = select
        for container in (self.voice_list, self.chat_list):
            for child in container.get_children():
                container.remove(child)

        row_to_select = None
        for slot in self.app.config.slots:
            container = self.voice_list if slot.kind == "voice" else self.chat_list
            row = self._build_row(slot)
            container.add(row)
            if slot.id == self.selected_id:
                row_to_select = row

        self.voice_list.show_all()
        self.chat_list.show_all()
        if hasattr(self, "add_voice_button"):
            has_voice = any(slot.kind == "voice" for slot in self.app.config.slots)
            self.add_voice_button.set_visible(not has_voice)

        if row_to_select is None and self.app.config.slots:
            # Fresh start: select something so the editor does not look dead.
            first = self.app.config.slots[0]
            self.selected_id = first.id
            container = self.voice_list if first.kind == "voice" else self.chat_list
            for child in container.get_children():
                if getattr(child, "slot_id", None) == first.id:
                    row_to_select = child
                    break

        if row_to_select is not None:
            parent = row_to_select.get_parent()
            if parent is not None:
                parent.select_row(row_to_select)
        else:
            self._load_form(None)

    def _build_row(self, slot) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        outer.set_border_width(6)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)

        title = Gtk.Label(xalign=0)
        if slot.visible:
            title.set_markup("<b>%s</b>" % html.escape(slot.name))
        else:
            title.set_markup("<span foreground='#6b7080'>%s</span>" % html.escape(slot.name))
        subtitle = Gtk.Label(xalign=0)
        subtitle.get_style_context().add_class("dim-label")
        subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        if slot.kind == "voice":
            subtitle.set_text("who is talking in your current call")
        else:
            subtitle.set_text(slot.url or "no URL yet")
        if not slot.visible:
            subtitle.set_opacity(0.45)
        box.pack_start(title, False, False, 0)
        box.pack_start(subtitle, False, False, 0)

        toggle = Gtk.Switch()
        toggle.set_valign(Gtk.Align.CENTER)
        toggle.set_tooltip_text("Show or hide this overlay")
        toggle.set_active(bool(slot.visible))
        toggle.connect("notify::active", self._on_row_toggled, slot)

        rename = Gtk.Button(label="Rename")
        rename.set_relief(Gtk.ReliefStyle.NONE)
        rename.set_valign(Gtk.Align.CENTER)
        rename.set_tooltip_text("Rename this overlay")
        rename.connect("clicked", lambda _button, target=slot: self._on_rename(target))

        outer.pack_start(box, True, True, 0)
        outer.pack_start(rename, False, False, 0)
        outer.pack_start(toggle, False, False, 0)
        row.add(outer)
        row.slot_id = slot.id
        return row

    def _on_rename(self, slot) -> None:
        dialog = Gtk.Dialog(title="Rename overlay", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                           "Rename", Gtk.ResponseType.OK)
        area = dialog.get_content_area()
        area.set_border_width(12)
        area.set_spacing(8)
        area.add(Gtk.Label(label="Name", xalign=0))
        entry = Gtk.Entry()
        entry.set_text(slot.name)
        entry.set_activates_default(True)
        entry.connect("activate", lambda *_: dialog.response(Gtk.ResponseType.OK))
        area.add(entry)
        dialog.show_all()
        entry.grab_focus()
        entry.select_region(0, -1)
        if dialog.run() == Gtk.ResponseType.OK:
            name = entry.get_text().strip()
            if name:
                slot.name = name[:80]
                self.app.update_slot(slot)
        dialog.destroy()
        self.refresh_rows(select=slot.id)

    def _on_row_toggled(self, switch, _pspec, slot) -> None:
        """Per-slot on/off, from the switch on the row itself.

        Deferred, because updating the slot rebuilds the row list and the switch
        we are currently inside would be destroyed mid-signal.
        """
        slot.visible = switch.get_active()
        GLib.idle_add(self._apply_visibility, slot)

    def _apply_visibility(self, slot) -> bool:
        self.app.update_slot(slot)
        self.refresh_rows(select=slot.id)
        return False

    def _on_row_selected(self, listbox, row) -> None:
        if row is None:
            return
        other = self.chat_list if listbox is self.voice_list else self.voice_list
        other.unselect_all()
        self.selected_id = row.slot_id
        self._load_form(self.app.config.slot(self.selected_id))

    # -- form ------------------------------------------------------------
    def _load_form(self, slot: Slot | None) -> None:
        self._loading = True
        try:
            has = slot is not None
            for widget in (self.name_entry, self.url_entry, self.monitor_spin,
                           self.opacity_scale, self.zoom_spin, self.click_through,
                           self.show_header, self.x_spin, self.y_spin,
                           self.w_spin, self.h_spin):
                widget.set_sensitive(has)
            self.reload_button.set_sensitive(has)
            self.delete_button.set_sensitive(has)
            for widget in (self.x_spin, self.y_spin, self.w_spin, self.h_spin):
                widget.set_sensitive(has)
            if not has:
                self.kind_label.set_text("")
                self.hint.set_text("")
                for widget in (self.url_label, self.url_entry, self.url_hint,
                               self.url_hint_pad, self.header_label,
                               self.show_header):
                    widget.set_visible(False)
                return
            self.kind_label.set_text("Discord voice" if slot.kind == "voice" else "Web overlay")
            self.name_entry.set_text(slot.name)
            self.url_entry.set_text(slot.url or "")
            # The Discord panel has no URL: hide the field instead of showing a
            # greyed-out box that begs the question "why is this here?".
            is_web = slot.kind == "web"
            for widget in (self.url_label, self.url_entry, self.url_hint, self.url_hint_pad):
                widget.set_visible(is_web)
            self.url_entry.set_sensitive(is_web)
            self.x_spin.set_value(slot.x)
            self.y_spin.set_value(slot.y)
            self.w_spin.set_value(slot.width)
            self.h_spin.set_value(slot.height)
            self.monitor_spin.set_value(slot.monitor)
            self.opacity_scale.set_value(slot.opacity)
            self.zoom_spin.set_value(slot.zoom)
            self.click_through.set_active(slot.click_through)
            voice = slot.kind == "voice"
            self.header_label.set_visible(voice)
            self.show_header.set_visible(voice)
            if voice:
                self.header_label.set_text("Show channel")
                self.header_label.set_tooltip_text(
                    "The channel name line at the top of the voice panel")
                self.show_header.set_active(slot.show_header)
            self.opacity_label.set_text("Background" if voice else "Opacity")
            self.opacity_label.set_visible(has)
            self.opacity_scale.set_visible(has)
            if voice:
                self.opacity_scale.set_range(0.0, 1.0)
                self.hint.set_text(
                    "Background fades only the panel behind the names and avatars \u2014 "
                    "at zero you get just the people talking, floating over your game."
                )
            else:
                self.opacity_scale.set_range(0.0, 1.0)
                self.hint.set_text(
                    "Background fades a panel drawn behind the chat, so message bubbles "
                    "stay fully solid \u2014 at zero you get bare chat floating over your "
                    "game. Pages that paint their own background will cover it."
                )
        finally:
            self._loading = False

    def _on_changed(self, *_args) -> None:
        if self._loading:
            return
        slot = self.current_slot()
        if slot is None:
            return
        slot.name = self.name_entry.get_text().strip() or slot.name
        if slot.kind == "web":
            slot.url = self.url_entry.get_text().strip() or None
        slot.x = int(self.x_spin.get_value())
        slot.y = int(self.y_spin.get_value())
        slot.width = int(self.w_spin.get_value())
        slot.height = int(self.h_spin.get_value())
        slot.monitor = int(self.monitor_spin.get_value())
        slot.opacity = float(self.opacity_scale.get_value())
        slot.zoom = float(self.zoom_spin.get_value())
        slot.click_through = self.click_through.get_active()
        if slot.kind == "voice":
            slot.show_header = self.show_header.get_active()
        self.app.update_slot(slot)

    # -- buttons ---------------------------------------------------------
    def _on_bulk_click(self, *_args) -> None:
        # Any overlay taking the mouse means the bulk action passes clicks
        # through; only when everything already passes through does it switch
        # the other way.
        self.app.toggle_click_through()

    def refresh_state(self) -> None:
        """Report the aggregate state and name the bulk action's effect."""
        slots = self.app.config.slots
        passing = sum(1 for slot in slots if slot.click_through)
        total = len(slots)

        if not slots:
            self.click_button.set_label("Pass clicks through everything")
            self.click_state.set_text("")
        elif passing == total:
            self.click_button.set_label("Make every overlay clickable")
            self.click_state.set_text("All %d pass clicks through" % total)
        else:
            self.click_button.set_label("Pass clicks through everything")
            if passing == 0:
                self.click_state.set_text("All %d take the mouse" % total)
            else:
                self.click_state.set_text("Mixed: %d of %d pass clicks through"
                                         % (passing, total))

        slot = self.current_slot()
        if slot is not None and hasattr(self, "click_through"):
            self._loading = True
            try:
                self.click_through.set_active(slot.click_through)
            finally:
                self._loading = False

    def _on_url_committed(self, *_args) -> None:
        slot = self.current_slot()
        if slot is not None and slot.kind == "web":
            self.app.reload_slot(slot.id)

    def _on_reload(self, *_args) -> None:
        if self.selected_id:
            self.app.reload_slot(self.selected_id)

    def _on_delete_slot(self, *_args) -> None:
        if self.selected_id:
            slot_id = self.selected_id
            self.selected_id = None
            self.app.remove_slot(slot_id)

    def _confirm_third_party_url(self, url: str) -> bool:
        """Say plainly that this is someone else's page, then let them proceed."""
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                   message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.NONE)
        dialog.set_markup("<b>Add this third-party overlay?</b>")
        dialog.format_secondary_text(
            "%s\n\nThis page is not part of Chatterglass. Whatever it draws appears "
            "over your game and can imitate other programs. Only continue if you trust "
            "where it came from." % url)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Add overlay", Gtk.ResponseType.OK)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK

    def _on_add_clipboard(self, *_args) -> None:
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        text = clipboard.wait_for_text() or ""
        preview_url = extract_url(text) or text.strip()[:120]
        if not self._confirm_third_party_url(preview_url):
            return
        slot = self.app.add_from_text(text)
        if slot is None:
            self._alert("Could not find a URL on the clipboard.", text[:200])
        else:
            self.selected_id = slot.id
            self.refresh_rows(select=slot.id)

    def _on_add_voice(self, *_args) -> None:
        existing = next((s for s in self.app.config.slots if s.kind == "voice"), None)
        if existing is not None:
            self.selected_id = existing.id
            self.refresh_rows(select=existing.id)
            return
        slot = self.app.add_slot(kind="voice")
        self.refresh_rows(select=slot.id)

    def _on_connect(self, *_args) -> None:
        self.app.connect_discord()
        self.refresh_status()

    # -- updates ---------------------------------------------------------
    def _begin_update_check(self) -> bool:
        threading.Thread(target=self._update_check_worker, daemon=True).start()
        return False

    def _update_check_worker(self) -> None:
        result = updater.check(__version__, self.app.config.updates.repo)
        GLib.idle_add(self._update_check_done, result)

    def _update_check_done(self, result: dict) -> bool:
        if result.get("error"):
            self.update_button.set_tooltip_text("Update check failed: %s" % result["error"])
            return False
        if not result.get("available"):
            self.update_button.set_tooltip_text("Chatterglass is up to date")
            return False
        self._update_available = result
        self.update_button.show()
        self.update_button.set_tooltip_text(
            "Update available: %s \u2014 click to install" % result.get("latest"))
        self._start_pulse()
        if self.app.config.updates.auto:
            self._install_update(silent=True)
        return False

    def _start_pulse(self) -> None:
        if self._pulse_source is None:
            self._pulse_source = GLib.timeout_add(700, self._pulse_tick)

    def _pulse_tick(self) -> bool:
        if not self._update_available:
            self._pulse_source = None
            return False
        self._pulse_bright = not self._pulse_bright
        colour = "#ffb84d" if self._pulse_bright else "#8a6a2a"
        self.update_label.set_markup("<span foreground='%s'>\u25bc</span>" % colour)
        return True

    def _on_update_clicked(self) -> None:
        if self._update_available is None:
            self.update_button.set_tooltip_text("Checking for updates\u2026")
            self._begin_update_check()
            return
        latest = self._update_available.get("latest") or "?"
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                   message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.NONE)
        dialog.set_markup("<b>Chatterglass %s is available</b>" % GLib.markup_escape_text(latest))
        dialog.format_secondary_text(
            "It will be downloaded and checked against the built-in signing key. "
            "Chatterglass restarts to use it.")
        dialog.add_button("Later", Gtk.ResponseType.CANCEL)
        dialog.add_button("Install update", Gtk.ResponseType.OK)
        if dialog.run() == Gtk.ResponseType.OK:
            self._install_update()
        dialog.destroy()

    def _install_update(self, silent: bool = False) -> None:
        info = self._update_available or {}
        url = info.get("url") or ""
        version = info.get("latest") or ""
        if not url:
            if not silent:
                self._message("Update not installable",
                              "That release has no downloadable .zip asset.")
            return
        self.update_button.set_tooltip_text("Downloading update\u2026")

        def worker() -> None:
            try:
                path = updater.download_and_verify(url, version)
                install_state.apply_update(path)
                GLib.idle_add(self._update_finished, True, version)
            except Exception as error:  # noqa: BLE001
                GLib.idle_add(self._update_finished, False, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def _update_finished(self, ok: bool, detail: str) -> bool:
        if ok:
            self._update_available = None
            self.update_label.set_markup("<span foreground='#48d67a'>\u2713</span>")
            self.update_button.set_tooltip_text(
                "Update %s installed \u2014 restart Chatterglass to use it" % detail)
            self._message("Update installed",
                          "Chatterglass %s was downloaded, signature-checked and put in "
                          "place. Restart it to use the new version." % detail)
        else:
            self.update_button.set_tooltip_text("Update failed: %s" % detail)
            self._message("Update failed", detail)
        return False

    def _message(self, title: str, detail: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                   message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK)
        dialog.set_markup("<b>%s</b>" % GLib.markup_escape_text(title))
        dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()

    def _info_row(self, label: str, value: str) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        key = Gtk.Label(xalign=0)
        key.set_markup("<b>%s</b>" % GLib.markup_escape_text(label))
        key.set_size_request(110, -1)
        val = Gtk.Label(xalign=0, wrap=True)
        val.set_text(value)
        row.pack_start(key, False, False, 0)
        row.pack_start(val, True, True, 0)
        return row

    def open_settings_window(self) -> None:
        """Settings and About as two tabs, in one window."""
        window = Gtk.Dialog(title="Chatterglass", transient_for=self.window, modal=True)
        window.add_buttons("Close", Gtk.ResponseType.CLOSE)
        window.set_default_size(720, 520)
        area = window.get_content_area()
        area.set_border_width(12)
        notebook = Gtk.Notebook()
        area.add(notebook)

        # ---------------- Settings tab ----------------
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_border_width(14)

        facts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        facts.pack_start(self._info_row("Version", __version__), False, False, 0)
        facts.pack_start(self._info_row("Installed in", install_state.INSTALL_DIR),
                         False, False, 0)
        page.pack_start(facts, False, False, 0)

        discord_frame = Gtk.Frame(label="  Discord  ")
        discord_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(discord_box, "set_" + side)(8)
        poll = Gtk.SpinButton.new_with_range(0.5, 30.0, 0.5)
        poll.set_value(self.app.config.discord.poll_seconds)
        poll_row = Gtk.Box(spacing=8)
        poll_row.pack_start(Gtk.Label(label="Check the voice channel every"), False, False, 0)
        poll_row.pack_start(poll, False, False, 0)
        poll_row.pack_start(Gtk.Label(label="seconds"), False, False, 0)
        discord_box.pack_start(poll_row, False, False, 0)

        current_id = (self.app.config.discord.client_id or "").strip()
        if not current_id.isdigit() or set(current_id) <= {"0"}:
            discord_box.pack_start(Gtk.Label(xalign=0, label="Application ID"), False, False, 0)
            id_entry = Gtk.Entry()
            id_entry.set_text("" if not current_id.strip("0") else current_id)
            id_entry.set_placeholder_text("only needed if Discord rejected the built-in one")
            discord_box.pack_start(id_entry, False, False, 0)
        else:
            id_entry = None
        discord_frame.add(discord_box)
        page.pack_start(discord_frame, False, False, 0)

        updates_frame = Gtk.Frame(label="  Updates  ")
        updates_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(updates_box, "set_" + side)(8)
        auto = Gtk.Switch()
        auto.set_active(self.app.config.updates.auto)
        auto.set_halign(Gtk.Align.START)
        auto_row = Gtk.Box(spacing=8)
        auto_row.pack_start(auto, False, False, 0)
        auto_row.pack_start(Gtk.Label(xalign=0, label="Install updates automatically"), False, False, 0)
        updates_box.pack_start(auto_row, False, False, 0)
        hint = Gtk.Label(xalign=0, wrap=True)
        hint.get_style_context().add_class("dim-label")
        hint.set_text("Updates are downloaded from the project's GitHub releases and are "
                      "only applied when their signature checks out. With this off, a "
                      "small arrow appears when one is available.")
        updates_box.pack_start(hint, False, False, 0)
        check_now = Gtk.Button(label="Check for updates now")
        check_now.set_halign(Gtk.Align.START)
        updates_box.pack_start(check_now, False, False, 0)
        updates_frame.add(updates_box)
        page.pack_start(updates_frame, False, False, 0)

        screen_frame = Gtk.Frame(label="  On screen  ")
        screen_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(screen_box, "set_" + side)(8)

        screen_toggle = Gtk.Switch()
        screen_toggle.set_active(bool(self.app.config.display.show_on_screen))
        screen_toggle.set_halign(Gtk.Align.START)
        screen_row = Gtk.Box(spacing=8)
        screen_row.pack_start(screen_toggle, False, False, 0)
        screen_row.pack_start(Gtk.Label(xalign=0, label="Show the overlays on this screen"),
                              False, False, 0)
        screen_box.pack_start(screen_row, False, False, 0)

        screen_help = Gtk.Label(xalign=0, wrap=True)
        screen_help.get_style_context().add_class("dim-label")
        screen_help.set_text("Turn this off if you only want the overlays inside OBS. "
                             "Nothing is drawn over your game and nothing can be clicked, "
                             "while your layout, chat addresses and positions are kept. "
                             "The OBS page keeps working either way.")
        screen_box.pack_start(screen_help, False, False, 0)

        def on_screen_toggled(switch, _param):
            self.app.set_screen_overlays(switch.get_active())

        screen_toggle.connect("notify::active", on_screen_toggled)
        screen_frame.add(screen_box)
        page.pack_start(screen_frame, False, False, 0)

        obs_frame = Gtk.Frame(label="  OBS / browser source  ")
        obs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(obs_box, "set_" + side)(8)

        obs_line = Gtk.Label(xalign=0, wrap=True)
        obs_line.set_text("In OBS add a Browser Source and paste the address below. It shows "
                          "only your overlays, in the positions set in this window \u2014 no "
                          "Chatterglass window, nothing else. Overlays you add later appear "
                          "there automatically.")
        obs_box.pack_start(obs_line, False, False, 0)

        obs_toggle = Gtk.Switch()
        obs_toggle.set_active(bool(self.app.config.obs.enabled))
        obs_toggle.set_halign(Gtk.Align.START)
        toggle_row = Gtk.Box(spacing=8)
        toggle_row.pack_start(obs_toggle, False, False, 0)
        toggle_row.pack_start(Gtk.Label(xalign=0, label="Serve the overlay page"), False, False, 0)
        obs_box.pack_start(toggle_row, False, False, 0)

        obs_url = Gtk.Entry()
        obs_url.set_editable(False)
        obs_url.set_text(self.app.obs.url())
        obs_copy = Gtk.Button(label="Copy address")
        obs_copy.connect("clicked",
                         lambda *_: self._copy_to_clipboard(obs_url.get_text(), obs_copy))
        url_row = Gtk.Box(spacing=8)
        url_row.pack_start(obs_url, True, True, 0)
        url_row.pack_start(obs_copy, False, False, 0)
        obs_box.pack_start(url_row, False, False, 0)

        obs_status = Gtk.Label(xalign=0, wrap=True)
        obs_status.get_style_context().add_class("dim-label")
        obs_box.pack_start(obs_status, False, False, 0)

        obs_note = Gtk.Label(xalign=0, wrap=True)
        obs_note.get_style_context().add_class("dim-label")
        obs_note.set_text("Only reachable from this computer. It stops when Chatterglass "
                          "closes, and it is off unless you switch it on.")
        obs_box.pack_start(obs_note, False, False, 0)

        def refresh_obs():
            running = self.app.obs.running
            obs_url.set_text(self.app.obs.url())
            obs_status.set_text(
                "Running \u2014 paste this into an OBS Browser Source."
                if running else "Off \u2014 no address is being served.")

        def on_obs_toggled(switch, _param):
            url = self.app.set_obs_enabled(switch.get_active())
            if switch.get_active() and url is None:
                obs_status.set_text("Could not start \u2014 port %d may be in use."
                                    % self.app.config.obs.port)
                return
            refresh_obs()

        obs_toggle.connect("notify::active", on_obs_toggled)
        refresh_obs()

        obs_frame.add(obs_box)
        page.pack_start(obs_frame, False, False, 0)

        danger_frame = Gtk.Frame(label="  Uninstall  ")
        danger_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(danger_box, "set_" + side)(8)
        danger_line = Gtk.Label(xalign=0, wrap=True)
        danger_line.set_text("Removes Chatterglass and everything it installed: the program, "
                             "the menu entry, the icons and your saved Discord token.")
        danger_box.pack_start(danger_line, False, False, 0)
        uninstall_button = Gtk.Button(label="Uninstall Chatterglass")
        uninstall_button.get_style_context().add_class("destructive-action")
        uninstall_button.set_halign(Gtk.Align.START)
        uninstall_button.connect("clicked", lambda *_: self._confirm_uninstall(window))
        danger_box.pack_start(uninstall_button, False, False, 0)
        danger_frame.add(danger_box)
        page.pack_start(danger_frame, False, False, 0)

        notebook.append_page(page, Gtk.Label(label="Settings"))

        # ---------------- About tab ----------------
        about = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        about.set_border_width(14)

        cols = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=24)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        left.pack_start(self._info_row("Product", "Chatterglass %s" % __version__), False, False, 0)
        left.pack_start(self._info_row("Author", "Commander Sabi"), False, False, 0)
        left.pack_start(self._info_row("What it is", "Chat overlays and a Discord voice panel that float over your game"), False, False, 0)
        left.pack_start(self._info_row("License", "MIT"), False, False, 0)
        left.pack_start(self._info_row("Copyright", "\u00a9 2026 Commander Sabi"), False, False, 0)
        left.pack_start(self._info_row("Privacy", "Stays on your machine \u2014 no data is sent to me"), False, False, 0)
        left.pack_start(self._info_row("Chat overlay", "Colin Horn's project, not part of Chatterglass"), False, False, 0)

        why = Gtk.Label(xalign=0, wrap=True)
        why.set_max_width_chars(56)
        why.set_text(
            "Why Chatterglass exists: there was no good overlay for this on Linux \u2014 "
            "the one that existed stopped getting updates, and the alternatives are "
            "Windows-only or need you to run a bot. This is meant to be easy for people "
            "arriving from Windows: one file, press Install, and it works.")
        left.pack_start(why, False, False, 6)
        cols.pack_start(left, True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_halign(Gtk.Align.START)
        right.set_valign(Gtk.Align.START)
        support_frame = Gtk.Frame(label="  Support Chatterglass  ")
        support_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(support_box, "set_" + side)(8)
        support_line = Gtk.Label(xalign=0, wrap=True)
        support_line.set_max_width_chars(34)
        support_line.set_text("Chatterglass is free and open-source. If it helps you, a "
                              "small donation helps free up time to keep improving it.")
        support_box.pack_start(support_line, False, False, 0)
        patreon = Gtk.Button(label="Donate on Patreon")
        patreon.set_halign(Gtk.Align.FILL)
        patreon.connect("clicked", lambda *_: self._open_link("https://www.patreon.com/cw/CommanderSabi"))
        support_box.pack_start(patreon, False, False, 0)
        usdc_button = Gtk.Button(label="Donate with crypto \u2014 copy USDC (Solana) address")
        usdc_button.set_halign(Gtk.Align.FILL)
        usdc_button.connect(
            "clicked",
            lambda *_: self._copy_to_clipboard(
                "2stgEZfERUMs7naTjikdR4CkDUnU1gsYKSHCX4VH5DQt", usdc_button))
        support_box.pack_start(usdc_button, False, False, 0)
        usdc_hint = Gtk.Label(xalign=0, wrap=True)
        usdc_hint.set_max_width_chars(34)
        usdc_hint.set_text("Crypto donation \u2014 USDC on the Solana network only:")
        support_box.pack_start(usdc_hint, False, False, 0)

        # The address itself, visible and selectable, so it can be copied by hand.
        usdc_address = Gtk.Label(xalign=0, wrap=True)
        usdc_address.set_max_width_chars(34)
        usdc_address.set_selectable(True)
        usdc_address.set_markup(
            "<span font_family='monospace' size='small'>"
            "2stgEZfERUMs7naTjikdR4CkDUnU1gsYKSHCX4VH5DQt</span>")
        support_box.pack_start(usdc_address, False, False, 0)
        support_frame.add(support_box)
        right.pack_start(support_frame, False, False, 0)
        cols.pack_start(right, False, False, 0)
        about.pack_start(cols, False, False, 0)

        whats_new = Gtk.Expander(label="What's new")
        changelog = Gtk.Label(xalign=0, wrap=True)
        changelog.set_margin_start(8)
        changelog.set_margin_top(4)
        changelog.set_text("0.1.0 \u2014 first release: Discord voice panel, chat overlays, "
                           "multi-chat (Twitch / YouTube / Kick), click-through control, "
                           "and a single-file installer.")
        whats_new.add(changelog)
        about.pack_start(whats_new, False, False, 0)

        bug = Gtk.Button(label="Report a bug\u2026")
        bug.set_halign(Gtk.Align.START)
        bug.connect("clicked", lambda *_: self.show_bug_report())
        about.pack_start(bug, False, False, 0)

        notebook.append_page(about, Gtk.Label(label="About"))

        def on_check_now(*_a):
            result = updater.check(__version__, self.app.config.updates.repo)
            dialog = Gtk.MessageDialog(transient_for=window, modal=True,
                                       message_type=Gtk.MessageType.INFO,
                                       buttons=Gtk.ButtonsType.OK)
            if result.get("error"):
                dialog.set_markup("<b>Could not check for updates</b>")
                dialog.format_secondary_text(str(result["error"]))
            elif result.get("available"):
                dialog.set_markup("<b>Update available: %s</b>" % result.get("latest"))
                dialog.format_secondary_text("It will be offered the next time you start "
                                             "Chatterglass.")
            else:
                dialog.set_markup("<b>You are up to date</b>")
            dialog.run()
            dialog.destroy()

        check_now.connect("clicked", on_check_now)

        state = {"done": False}

        def save_and_close(*_a):
            if state["done"]:
                return
            state["done"] = True
            self.app.config.discord.poll_seconds = float(poll.get_value())
            if id_entry is not None:
                self.app.set_discord_settings(id_entry.get_text(), poll.get_value())
            self.app.config.updates.auto = auto.get_active()
            self.app.config.save()

        window.connect("response", save_and_close)
        window.show_all()
        window.run()
        window.destroy()

    def show_bug_report(self) -> None:
        """Consent-first diagnostics, the same shape as Horidoro's report dialog."""
        from . import bugreport

        dialog = Gtk.Dialog(title="Report a bug", transient_for=self.window, modal=True)
        dialog.set_default_size(700, 580)
        area = dialog.get_content_area()
        area.set_spacing(8)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for side in ("margin_start", "margin_end", "margin_top", "margin_bottom"):
            getattr(box, "set_" + side)(12)
        area.add(box)

        explain = Gtk.Label(xalign=0, wrap=True)
        explain.set_max_width_chars(78)
        explain.set_text(
            "This report contains ONLY: the app version, your distribution and kernel, "
            "your session type, the Python/GTK/WebKitGTK versions, whether layer-shell "
            "is available, whether the app is installed and where, the Discord "
            "connection status, and how many overlays exist with their size and "
            "visibility.\n\n"
            "It does NOT contain: chat overlay URLs, channel names, your Discord "
            "username or token, any message text, or any of your files.\n\n"
            "Nothing leaves your machine until you press Copy, Save or Open issue. "
            "The text is also cleaned line by line: control characters are removed and "
            "lines that look like instructions to an AI are dropped, so a report cannot "
            "carry hidden instructions.")
        box.pack_start(explain, False, False, 0)

        box.pack_start(Gtk.Label(xalign=0, label="What happened?"), False, False, 4)
        description = Gtk.TextView()
        description.set_wrap_mode(Gtk.WrapMode.WORD)
        scroller = Gtk.ScrolledWindow()
        scroller.set_min_content_height(80)
        scroller.add(description)
        box.pack_start(scroller, False, False, 0)

        preview_expander = Gtk.Expander(label="Preview \u2014 exactly what will be shared")
        preview = Gtk.TextView()
        preview.set_editable(False)
        preview.set_wrap_mode(Gtk.WrapMode.WORD)
        preview_scroller = Gtk.ScrolledWindow()
        preview_scroller.set_min_content_height(210)
        preview_scroller.add(preview)
        preview_expander.add(preview_scroller)
        box.pack_start(preview_expander, True, True, 0)

        def assemble() -> str:
            buffer = description.get_buffer()
            text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
            return bugreport.build_report(self.app, extra=text)

        def refresh(*_a) -> None:
            preview.get_buffer().set_text(assemble())

        description.get_buffer().connect("changed", refresh)
        refresh()

        buttons = Gtk.Box(spacing=8)
        box.pack_start(buttons, False, False, 4)

        def show_notice(message: str) -> None:
            notice = Gtk.MessageDialog(transient_for=dialog, modal=True,
                                       message_type=Gtk.MessageType.INFO,
                                       buttons=Gtk.ButtonsType.OK)
            notice.set_markup("<b>Report a bug</b>")
            notice.format_secondary_text(message)
            notice.run()
            notice.destroy()

        def do_copy(*_a) -> None:
            self._copy_to_clipboard(assemble())
            show_notice("Copied to the clipboard \u2014 paste it into a new GitHub issue.")

        def do_save(*_a) -> None:
            chooser = Gtk.FileChooserDialog(
                "Save bug report", dialog, Gtk.FileChooserAction.SAVE,
                ("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.ACCEPT))
            chooser.set_current_name("chatterglass-bug-report.md")
            if chooser.run() == Gtk.ResponseType.ACCEPT:
                try:
                    with open(chooser.get_filename(), "w", encoding="utf-8") as handle:
                        handle.write(assemble())
                    show_notice("Saved to %s" % chooser.get_filename())
                except OSError as error:
                    show_notice("Could not save: %s" % error)
            chooser.destroy()

        def do_issue(*_a) -> None:
            import urllib.parse
            body = urllib.parse.quote(assemble())
            url = ("https://github.com/%s/issues/new?title=%s&body=%s"
                   % (self.app.config.updates.repo, urllib.parse.quote("Chatterglass bug report"), body))
            self._open_link(url)
            dialog.destroy()

        for label, handler in (("Copy to clipboard", do_copy),
                               ("Save to file\u2026", do_save),
                               ("Open GitHub issue", do_issue),
                               ("Cancel", lambda *_: dialog.destroy())):
            button = Gtk.Button(label=label)
            button.connect("clicked", handler)
            buttons.pack_start(button, False, False, 0)

        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def _open_link(self, url: str) -> None:
        """Open a URL in the user's browser, with a fallback and a visible error."""
        try:
            Gtk.show_uri_on_window(self.window, url, Gtk.get_current_event_time())
            return
        except Exception:  # noqa: BLE001 - fall through to webbrowser
            pass
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as error:  # noqa: BLE001
            print("could not open %s: %r" % (url, error), flush=True)

    def _confirm_uninstall(self, parent) -> None:
        dialog = Gtk.MessageDialog(transient_for=parent, modal=True,
                                   message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.NONE)
        dialog.set_markup("<b>Uninstall Chatterglass?</b>")
        dialog.format_secondary_text(
            "This removes the program, the menu entry, the icons and your saved "
            "Discord token. It cannot be undone.")
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Uninstall", Gtk.ResponseType.YES)
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.YES:
            return
        failed = install_state.uninstall()
        if failed:
            message = Gtk.MessageDialog(transient_for=parent, modal=True,
                                        message_type=Gtk.MessageType.ERROR,
                                        buttons=Gtk.ButtonsType.OK)
            message.set_markup("<b>Some files could not be removed</b>")
            message.format_secondary_text("\n".join(failed))
            message.run()
            message.destroy()
            return
        self.app.quit()

    def _copy_to_clipboard(self, text: str, widget=None) -> None:
        """Copy, and say so on the button itself for a moment."""
        from gi.repository import Gdk
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)
        try:
            clipboard.store()
        except Exception:  # noqa: BLE001 - best effort
            pass
        if widget is None:
            return
        try:
            original = widget.get_label()
            original_tip = widget.get_tooltip_text()
            width_before = widget.get_preferred_width().natural_width
            widget.set_label("Copied \u2713")
            width_after = widget.get_preferred_width().natural_width
            # Lock the wider of the two so the button cannot shrink and shove
            # its neighbours around.
            widget.set_size_request(max(width_before, width_after), -1)
            widget.get_style_context().add_class("copied")
            widget.set_tooltip_text("Copied to the clipboard")
        except Exception:  # noqa: BLE001
            return

        def restore():
            try:
                widget.set_label(original)
                widget.get_style_context().remove_class("copied")
                widget.set_tooltip_text(original_tip)
            except Exception:  # noqa: BLE001
                pass
            return False

        GLib.timeout_add(1400, restore)

    def _on_add_dialog(self, *_args) -> None:
        dialog = Gtk.Dialog(title="Add overlay", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        dialog.set_default_size(560, 470)
        area = dialog.get_content_area()
        area.set_border_width(12)
        area.set_spacing(10)

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(stack)
        switcher.set_halign(Gtk.Align.START)
        area.pack_start(switcher, False, False, 0)
        area.pack_start(stack, True, True, 0)

        # ---------------- page 1: standard multi-chat (Colin Horn)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        entries = {}
        row = 0
        for key, label in multichat.PLATFORMS:
            entry = Gtk.Entry()
            entry.set_placeholder_text("username")
            entries[key] = entry
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(entry, 1, row, 2, 1)
            row += 1
        page.pack_start(grid, False, False, 0)

        switches = {}
        for key, label, default in (("badges", "Badges", True),
                                    ("monochrome", "Monochrome", False)):
            check = Gtk.CheckButton(label=label)
            check.set_active(default)
            switches[key] = check
        row_box = Gtk.Box(spacing=14)
        row_box.pack_start(switches["badges"], False, False, 0)
        row_box.pack_start(switches["monochrome"], False, False, 0)
        page.pack_start(row_box, False, False, 0)

        extras = {}
        extra_row = Gtk.Box(spacing=14)
        for key, label, default in (("bots", "Show bots", True),
                                    ("commands", "Show commands", True),
                                    ("animate", "Animate", True),
                                    ("fade", "Fade", True)):
            check = Gtk.CheckButton(label=label)
            check.set_active(default)
            extras[key] = check
            extra_row.pack_start(check, False, False, 0)
        page.pack_start(extra_row, False, False, 0)

        colour_row = Gtk.Box(spacing=8)
        colour_entry = Gtk.Entry()
        colour_entry.set_placeholder_text("background colour, e.g. 1a1a1a (optional)")
        colour_entry.set_width_chars(28)
        colour_row.pack_start(Gtk.Label(label="Colour", xalign=0), False, False, 0)
        colour_row.pack_start(colour_entry, False, False, 0)
        page.pack_start(colour_row, False, False, 0)

        preview = Gtk.Entry()
        preview.set_editable(False)
        page.pack_start(Gtk.Label(label="Overlay URL", xalign=0), False, False, 4)
        page.pack_start(preview, False, False, 0)

        credit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        thanks = Gtk.Label(xalign=0, wrap=True)
        thanks.set_max_width_chars(64)
        thanks.set_markup(
            "<span size='small'>Thanks to <b>Colin Horn</b>: the multi-chat overlay on "
            "this tab is his own project, not part of Chatterglass. Chatterglass only "
            "builds the address for it and shows what his overlay draws.</span>"
        )
        thanks.get_style_context().add_class("dim-label")
        credit.pack_start(thanks, False, False, 0)

        links = Gtk.Box(spacing=12)
        links.pack_start(Gtk.LinkButton.new_with_label(
            multichat.CREDIT_URL, "Check out his overlay"), False, False, 0)
        links.pack_start(Gtk.LinkButton.new_with_label(
            multichat.AUTHOR_URL, "More from Colin Horn"), False, False, 0)
        credit.pack_start(links, False, False, 0)
        page.pack_start(credit, False, False, 8)

        def refresh_preview(*_a):
            preview.set_text(multichat.build_url(
                twitch=entries["twitch"].get_text(),
                youtube=entries["youtube"].get_text(),
                kick=entries["kick"].get_text(),
                badges=switches["badges"].get_active(),
                monochrome=switches["monochrome"].get_active(),
                bots=extras["bots"].get_active(),
                commands=extras["commands"].get_active(),
                animate=extras["animate"].get_active(),
                fade=extras["fade"].get_active(),
                bgcolor=colour_entry.get_text(),
            ))

        for widget in list(entries.values()) + list(switches.values()) + list(extras.values()):
            widget.connect("changed" if isinstance(widget, Gtk.Entry) else "toggled",
                           refresh_preview)
        colour_entry.connect("changed", refresh_preview)
        refresh_preview()

        stack.add_titled(page, "chat", "Multi-chat")

        # ---------------- page 2: any URL
        page2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        url_entry = Gtk.Entry()
        url_entry.set_placeholder_text("https://… or a full <iframe src=\"…\"> snippet")
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("Name (optional)")
        custom_warning = Gtk.Label(xalign=0, wrap=True)
        custom_warning.set_max_width_chars(70)
        custom_warning.set_markup(
            "<span size='small'><b>Third-party content.</b> An overlay page is someone "
            "else's web page shown on top of your game; it can draw anything, including "
            "things that look like other programs. Only add addresses from sources you "
            "trust.</span>")
        page2.pack_start(custom_warning, False, False, 0)

        page2.pack_start(Gtk.Label(label="URL or snippet", xalign=0), False, False, 8)
        page2.pack_start(url_entry, False, False, 0)
        page2.pack_start(Gtk.Label(label="Name", xalign=0), False, False, 4)
        page2.pack_start(name_entry, False, False, 0)
        stack.add_titled(page2, "custom", "Custom URL")

        dialog.show_all()
        response = dialog.run()
        page_name = stack.get_visible_child_name()
        text = url_entry.get_text()
        name = name_entry.get_text().strip() or None
        built = preview.get_text()
        twitch = entries["twitch"].get_text()
        youtube = entries["youtube"].get_text()
        kick = entries["kick"].get_text()
        dialog.destroy()

        if response != Gtk.ResponseType.OK:
            return

        if page_name == "chat":
            if not multichat.has_platform(twitch=twitch, youtube=youtube, kick=kick):
                self._alert("Enter at least one username",
                            "Twitch, YouTube or Kick \u2014 that is how the multi-chat "
                            "widget knows whose chat to show.")
                return
            slot = self.app.add_slot(kind="web",
                                     name=multichat.slot_name(twitch=twitch,
                                                              youtube=youtube,
                                                              kick=kick),
                                     url=built)
            self.selected_id = slot.id
            self.refresh_rows(select=slot.id)
            return

        preview_url = extract_url(text) or text.strip()[:120]
        if not self._confirm_third_party_url(preview_url):
            return
        slot = self.app.add_from_text(text)
        if slot is None:
            self._alert("Could not find a URL in that text.", text[:200])
            return
        if name:
            slot.name = name
            self.app.update_slot(slot)
        self.refresh_rows(select=slot.id)

    def _alert(self, message: str, detail: str = "") -> None:
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
                                   message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.OK)
        dialog.set_markup("<b>%s</b>" % html.escape(message))
        if detail:
            dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()

    # -- status ----------------------------------------------------------
    def refresh_status(self) -> None:
        status, detail = self.app.discord_status
        text = STATUS_TEXT.get(status, status)
        users = len(self.app.voice_state.get("users") or [])
        if status == "connected":
            channel = self.app.voice_state.get("channel")
            if channel:
                text = "%s · %s (%d)" % (text, channel, users)
        self.status_label.set_text(text)
        self.status_label.set_tooltip_text(detail or "")

        if hasattr(self, "connect_button"):
            has_voice = any(slot.kind == "voice" for slot in self.app.config.slots)
            self.connect_button.set_visible(has_voice)
            self.connect_button.set_sensitive(status in ("not-connected", "auth-failed"))
            if status == "discord-not-running":
                self.connect_button.set_tooltip_text("Start Discord first, then press this")
            elif status == "app-id-missing":
                self.connect_button.set_tooltip_text(
                    "An application ID is needed first \u2014 see Settings")
            elif status == "connected":
                self.connect_button.set_tooltip_text("Already connected to Discord")
            else:
                self.connect_button.set_tooltip_text(
                    "Opens Discord's own window asking whether to allow this app")
            if status == "connected":
                colour, label = "#48d67a", "Discord connected"
            elif status == "authorising":
                colour, label = "#f0a13c", "Waiting for Allow\u2026"
            elif status in ("not-connected", "auth-failed"):
                colour, label = "#ff6b6b", "Connect to Discord"
            elif status == "discord-not-running":
                colour, label = "#8b8f9c", "Discord not running"
            else:
                colour, label = "#8b8f9c", "Connect to Discord"
            self.connect_label.set_markup(
                "<span foreground='%s'>\u25cf</span>  %s"
                % (colour, GLib.markup_escape_text(label))
            )

    def _on_delete(self, *_args) -> bool:
        self.app.quit()
        return True
