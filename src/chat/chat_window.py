# chat_window.py
#
# Copyright 2025 jfsen
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Standalone chat windows — detached popup and floating overlay."""

import gettext
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, Gtk

_ = gettext.gettext
logger = logging.getLogger(__name__)


class ChatWindow(Adw.Window):
    """A self-contained window that hosts a read-only Twitch chat.

    Supports two modes:

    * **detached** — a normal window you can move, resize, and minimise.
    * **overlay** — borderless, always-on-top, semi-transparent;
      designed to float next to (or on top of) a video player.
    """

    def __init__(
        self,
        twitch,
        streamer,
        display_name=None,
        alternating_bg=False,
        disable_emote_animations=False,
        theme="system",
        transient_for=None,
        highlight_first_msg=True,
        highlight_mod=True,
        highlight_vip=True,
        highlight_partner=True,
        highlight_broadcaster=True,
        overlay=False,
    ):
        super().__init__(
            title=_("Chat: {}").format(display_name or streamer),
        )
        self.set_default_size(360, 520)
        self._overlay = overlay

        label = "overlay" if overlay else "detached"
        logger.info("Creating %s chat window for #%s", label, streamer)

        if transient_for:
            self.set_transient_for(transient_for)

        if overlay:
            self.set_decorated(False)

        from .config import MAX_MESSAGES
        from .page import ChatPage

        self._streamer = streamer
        self._chat_page = ChatPage(
            parent=None,
            streamer=streamer,
            display_name=display_name,
            alternating_bg=alternating_bg,
            disable_emote_animations=disable_emote_animations,
            theme=theme,
            twitch=twitch,
            enable_detach=False,
            hide_toolbar=overlay,
            max_messages=150 if overlay else MAX_MESSAGES,
            overlay=overlay,
            highlight_first_msg=highlight_first_msg,
            highlight_mod=highlight_mod,
            highlight_vip=highlight_vip,
            highlight_partner=highlight_partner,
            highlight_broadcaster=highlight_broadcaster,
        )

        if overlay:
            content = self._build_overlay_content()
        else:
            content = self._chat_page
        self.set_content(content)

        self.connect("close-request", self._on_close_request)

    def _build_overlay_content(self):
        """Wrap the chat page with a flat, auto-hiding native headerbar."""
        # ── Headerbar (initially hidden) ────────────────────
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_visible(False)
        self._header = header

        title_label = Gtk.Label(
            label=_("Chat: {}").format(self._streamer),
            xalign=0,
            tooltip_text=_("Right-click → Always on Top to pin above other windows"),
        )
        header.set_title_widget(title_label)

        # Drag gesture on the title for moving the undecorated window.
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        title_label.add_controller(drag)

        self._header_hide_timeout = None

        # ── CSS: transparent window + rounded content box ────
        self._overlay_css_provider = Gtk.CssProvider()
        self.add_css_class("overlay-chat")
        self._overlay_style_manager = Adw.StyleManager.get_default()
        self._apply_overlay_css()
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            self._overlay_css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        self._overlay_style_manager.connect(
            "notify::dark",
            lambda m, p: self._apply_overlay_css(),
        )

        # ── Assembly ─────────────────────────────────────────
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(self._chat_page)

        # Show header on mouse motion in the top ~60 px, hide after 2 s.
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_overlay_motion)
        content.add_controller(motion)

        return content

    def _on_overlay_motion(self, controller, x, y):
        if y < 60:
            self._header.set_visible(True)
            if self._header_hide_timeout is not None:
                GLib.source_remove(self._header_hide_timeout)
                self._header_hide_timeout = None
        elif self._header_hide_timeout is None:
            self._header_hide_timeout = GLib.timeout_add(500, self._hide_header)

    def _hide_header(self):
        self._header.set_visible(False)
        self._header_hide_timeout = None
        return GLib.SOURCE_REMOVE

    def _on_drag_begin(self, gesture, x, y):
        surface = self.get_surface()
        if surface is not None:
            device = gesture.get_device()
            surface.begin_move(device, 1, int(x), int(y), 0)

    def _apply_overlay_css(self):
        """Rebuild the overlay CSS for the current dark/light preference."""
        dark = self._overlay_style_manager.get_dark()
        bg = "rgba(0,0,0,0.50)" if dark else "rgba(255,255,255,0.55)"
        self._overlay_css_provider.load_from_data(
            f"window.overlay-chat {{ background: {bg}; border-radius: 0; }}"
            f"scrolledwindow scrollbar {{ opacity: 0; }}",
            -1,
        )

    def _on_close_request(self, window):
        """Clean up the chat connection when the window is closed."""
        label = "Overlay" if self._overlay else "Detached"
        logger.info("%s chat window closed for #%s", label, self._streamer)
        self._chat_page.cleanup()
        return False  # Allow the window to close normally
