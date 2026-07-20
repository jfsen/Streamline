# streamer_list.py
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

"""Streamer list management — row creation, avatar/thumbnail downloads, pill badges."""

import gettext
import logging
import threading
from html import unescape
from pathlib import Path
from time import time

import requests
from gi.repository import Adw, Gdk, GLib, Gtk

from .config import PILL_FADE_MS, PILL_SHOW_MS, ROW_HIGHLIGHT_MS

logger = logging.getLogger(__name__)

_ = gettext.gettext


class StreamerRowManager:
    """Manages the online and offline streamer ListBox widgets.

    Creates, updates, and removes rows; handles avatar and thumbnail
    downloads; manages pill badges for online/offline transitions.
    """

    _CSS_LOADED = False  # class-level flag — only load once ever

    class _ThumbnailBatch:
        """Tracks a set of concurrent thumbnail downloads so all can be
        applied together with a crossfade once every download finishes."""
        __slots__ = ("pending", "results")

        def __init__(self):
            self.pending = 0
            # streamer -> (stack, path)
            self.results = {}

    def __init__(self, window):
        self.window = window
        self.streamer_rows = {}
        self.online_list = window.online_list
        self.offline_list = window.offline_list
        self._previous_online = set()

        # Thumbnail batching state
        self._current_batch = None  # _ThumbnailBatch or None

        # Custom header with "Online" label and pill badge
        self._header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._header_box.add_css_class("streamer-group-header")

        self._header_label = Gtk.Label(label=_("Online"))
        self._header_label.add_css_class("streamer-group-header-label")
        self._header_label.set_halign(Gtk.Align.START)
        self._header_box.append(self._header_label)

        # Pill badge
        self._pill_box = Gtk.Box(spacing=0)
        self._pill_box.add_css_class("online-pill")
        self._pill_box.set_valign(Gtk.Align.CENTER)
        self._pill_box.set_visible(False)
        self._pill_plus = Gtk.Label()
        self._pill_plus.add_css_class("pill-plus")
        self._pill_minus = Gtk.Label()
        self._pill_minus.add_css_class("pill-minus")
        self._header_box.append(self._pill_box)

        # Insert the custom header as the first child of the online group
        self.window.online_group.add(self._header_box)

        # Custom header for the offline group (label only)
        self._offline_header_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8
        )
        self._offline_header_box.add_css_class("streamer-group-header")

        self._offline_header_label = Gtk.Label(label=_("Offline"))
        self._offline_header_label.add_css_class("streamer-group-header-label")
        self._offline_header_label.set_halign(Gtk.Align.START)
        self._offline_header_box.append(self._offline_header_label)

        self.window.offline_group.add(self._offline_header_box)

        self._pill_timeout_id = None

    @classmethod
    def _ensure_css(cls, display):
        """Load common CSS from GResource — only once per process."""
        if cls._CSS_LOADED:
            return
        cls._CSS_LOADED = True
        css_provider = Gtk.CssProvider()
        css_provider.load_from_resource("/org/jfsen/Streamline/css/streamline.css")
        Gtk.StyleContext.add_provider_for_display(
            display, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def create_row(self, streamer, info):
        """Create an ActionRow with buttons and additional info."""
        self._ensure_css(self.window.get_display())
        row = Adw.ActionRow.new()

        # Store the login name in the row data for sorting
        row.login_name = streamer

        # Set streamer name using combined cache (if API is available)
        display_name = None
        if self.window.twitch is not None:
            display_name = self.window.twitch.user_cache.get(streamer, {}).get("name")

        # Check if display name has non-ASCII characters
        if display_name and any(ord(c) > 127 for c in display_name):
            # For non-ASCII display names, include login name in parentheses
            row.set_title(f"{display_name} ({streamer})")
        else:
            row.set_title(display_name or streamer)

        row.set_title_lines(1)

        # Store whether streamer is online in the row data
        row.is_online = bool(info)

        # Apply online/offline visual indicator
        if info:  # Online streamer
            row.add_css_class("online-row")
            viewers = info.get("viewers", "N/A")
            game = info.get("game", "Unknown")
            subtitle = _("{}\n{} viewers").format(game, viewers)
            row.set_subtitle(GLib.markup_escape_text(subtitle))

            raw_title = info.get("title", "No title")
            title = unescape(raw_title)  # Unescape special characters
            started_at = info.get("started_at")
            if started_at:
                uptime = self.window.twitch.calculate_uptime(started_at)
            else:
                uptime = info.get("uptime", "N/A")
            tooltip = _("Title: {}\nUptime: {}").format(title, uptime)
            row.set_tooltip_text(tooltip)
        else:  # Offline streamer
            row.add_css_class("offline-row")

        # Add buttons
        self._add_row_buttons(row, streamer, display_name)
        return row

    def _build_menu_popover(self, streamer):
        """Build and return a Gtk.Popover with Chat, VODs, and Unfollow actions.

        Extracted so both standard rows and card rows can share the same menu.
        """
        popover = Gtk.Popover()
        popover.add_css_class("streamer-more-popover")
        popover.set_has_arrow(False)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        # ── Chat row (label + detach icon) ──
        chat_row = Gtk.ListBoxRow()
        chat_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        chat_label = Gtk.Label(label=_("Chat"), xalign=0)
        chat_label.set_margin_start(12)
        chat_label.set_margin_end(12)
        chat_label.set_hexpand(True)
        chat_box.append(chat_label)

        detach_btn = Gtk.Button.new_from_icon_name("window-new-symbolic")
        detach_btn.add_css_class("flat")
        detach_btn.add_css_class("detach-button")
        detach_btn.set_valign(Gtk.Align.CENTER)
        detach_btn.set_tooltip_text(_("Open in detached window"))
        detach_btn.connect(
            "clicked",
            lambda btn: (
                popover.popdown(),
                self.window.show_chat_popup(streamer),
            ),
        )
        chat_box.append(detach_btn)

        overlay_btn = Gtk.Button.new_from_icon_name("big-rectangle-in-focus-symbolic")
        overlay_btn.add_css_class("flat")
        overlay_btn.add_css_class("detach-button")
        overlay_btn.set_valign(Gtk.Align.CENTER)
        overlay_btn.set_tooltip_text(_("Open as floating overlay"))
        overlay_btn.connect(
            "clicked",
            lambda btn: (
                popover.popdown(),
                self.window.show_chat_overlay(streamer),
            ),
        )
        chat_box.append(overlay_btn)

        chat_row.set_child(chat_box)
        chat_click = Gtk.GestureClick.new()
        chat_click.connect(
            "released",
            lambda g, n, x, y: (
                popover.popdown(),
                self.window.show_chat_page(streamer),
            ),
        )
        chat_row.add_controller(chat_click)
        listbox.append(chat_row)

        # ── Show VODs ──
        vods_label = Gtk.Label(label=_("Show VODs"), xalign=0)
        vods_label.set_margin_start(12)
        vods_label.set_margin_end(12)
        vods_label.set_margin_top(4)
        vods_label.set_margin_bottom(4)
        vods_row = Gtk.ListBoxRow()
        vods_row.set_child(vods_label)
        vods_click = Gtk.GestureClick.new()
        vods_click.connect(
            "released",
            lambda g, n, x, y: (
                popover.popdown(),
                self.window.show_vods_page(streamer),
            ),
        )
        vods_row.add_controller(vods_click)
        listbox.append(vods_row)

        # ── Unfollow ──
        unfollow_label = Gtk.Label(label=_("Unfollow"), xalign=0)
        unfollow_label.set_margin_start(12)
        unfollow_label.set_margin_end(12)
        unfollow_label.set_margin_top(4)
        unfollow_label.set_margin_bottom(4)
        unfollow_row = Gtk.ListBoxRow()
        unfollow_row.set_child(unfollow_label)
        unfollow_click = Gtk.GestureClick.new()
        unfollow_click.connect(
            "released",
            lambda g, n, x, y: (
                popover.popdown(),
                self.window.unfollow_streamer(streamer),
            ),
        )
        unfollow_row.add_controller(unfollow_click)
        listbox.append(unfollow_row)

        popover.set_child(listbox)

        # Gray out chat when one is already open for this streamer.
        def _sync_chat_row(popover):
            if popover.get_visible():
                chat_open = streamer in self.window._active_chats
                chat_label.set_sensitive(not chat_open)
                detach_btn.set_sensitive(not chat_open)
                overlay_btn.set_sensitive(not chat_open)

        popover.connect("notify::visible", lambda p, *_: _sync_chat_row(p))

        return popover

    def _add_row_buttons(self, row, streamer, display_name=None):
        """Add buttons and dropdown menu to the row."""
        row.add_css_class("action-row")

        # ── Play button ──
        show_avatars = getattr(self.window, "show_profile_pictures", True)

        if show_avatars:
            # Avatar mode: always use the overlay structure for consistent look.
            # Cached avatars show immediately; uncached ones use a placeholder
            # icon that set_avatar() swaps out once the download finishes.
            avatar_path = None
            if self.window.twitch is not None:
                url = self.window.twitch.user_cache.get(streamer, {}).get(
                    "profile_image_url", ""
                )
                if url:
                    p = Path(self.window.twitch._get_avatars_dir()) / f"{streamer}.jpg"
                    if p.exists():
                        avatar_path = str(p)

            play_button = Gtk.Button()
            play_button.add_css_class("flat")
            play_button.add_css_class("avatar-play-button")
            play_button.set_valign(Gtk.Align.CENTER)
            if row.is_online:
                play_button.set_tooltip_text(_("Play stream"))
            play_button.set_overflow(Gtk.Overflow.HIDDEN)
            if not row.is_online:
                play_button.add_css_class("offline-stream-button")
            play_button.connect(
                "clicked",
                lambda btn: self.window.player.play_content(
                    f"twitch.tv/{streamer}", is_vod=False, display_name=display_name
                ),
            )

            overlay = Gtk.Overlay()
            overlay.set_overflow(Gtk.Overflow.HIDDEN)
            overlay.add_css_class("avatar-overlay")

            if avatar_path:
                pic = Gtk.Image.new_from_file(avatar_path)
                pic.set_pixel_size(48)
                overlay.set_child(pic)

                if row.is_online:
                    icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
                    icon.add_css_class("avatar-play-icon")
                    overlay.add_overlay(icon)
            else:
                placeholder = Gtk.Image.new_from_icon_name(
                    "media-playback-start-symbolic"
                )
                placeholder.set_pixel_size(24)
                overlay.set_child(placeholder)

            play_button.set_child(overlay)
        else:
            # No avatars: old-style native GTK button
            play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
            play_button.add_css_class("flat")
            play_button.set_valign(Gtk.Align.CENTER)
            play_button.set_tooltip_text(_("Play stream"))
            if not row.is_online:
                play_button.add_css_class("offline-stream-button")
            play_button.connect(
                "clicked",
                lambda btn: self.window.player.play_content(
                    f"twitch.tv/{streamer}", is_vod=False, display_name=display_name
                ),
            )

        row.add_prefix(play_button)
        row._play_button = play_button

        # Create browser button
        browser_button = Gtk.Button(icon_name="web-browser-symbolic")
        browser_button.add_css_class("flat")
        browser_button.set_valign(Gtk.Align.CENTER)
        browser_button.set_tooltip_text(_("Open in browser"))
        browser_button.connect(
            "clicked", lambda btn: self.window.open_stream_in_browser(streamer)
        )
        row.add_suffix(browser_button)

        # Create menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("view-more-symbolic")
        menu_button.add_css_class("flat")
        menu_button.set_valign(Gtk.Align.CENTER)
        menu_button.set_tooltip_text(_("More"))

        # Use shared menu popover builder
        popover = self._build_menu_popover(streamer)
        menu_button.set_popover(popover)

        row.add_suffix(menu_button)

    def set_avatar(self, streamer, path):
        """Swap a streamer's placeholder icon for its avatar image (called
        from the background downloader via GLib.idle_add)."""
        row = self.streamer_rows.get(streamer)
        if row is None:
            return

        # Standard row: avatar lives on _play_button
        if hasattr(row, "_play_button"):
            button = row._play_button
            overlay = button.get_child()

            pic = Gtk.Image.new_from_file(path)
            pic.set_pixel_size(48)
            overlay.set_child(pic)

            if row.is_online:
                icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
                icon.add_css_class("avatar-play-icon")
                overlay.add_overlay(icon)

            button.set_child(overlay)
            return

        # Card row: avatar lives on _avatar_button
        if hasattr(row, "_avatar_button"):
            button = row._avatar_button
            overlay = button.get_child()

            pic = Gtk.Image.new_from_file(path)
            pic.set_pixel_size(44)
            overlay.set_child(pic)

            play_icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
            play_icon.add_css_class("avatar-play-icon")
            overlay.add_overlay(play_icon)

            button.set_child(overlay)
            return

    # ── Stream thumbnail helpers ─────────────────────────────

    @staticmethod
    def _stream_thumbnail_dir():
        d = Path(GLib.get_user_cache_dir()) / "Streamline" / "thumbnails"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _stream_thumbnail_path(streamer):
        """Path for a single cached thumbnail per streamer."""
        return StreamerRowManager._stream_thumbnail_dir() / f"{streamer}.jpg"

    @staticmethod
    def _thumbnail_is_stale(streamer):
        """Return True if the cached thumbnail is missing or older than 5 minutes.

        Twitch serves a frame from the last 5 minutes at a stable URL, so the URL
        itself never changes.  We use the file's modification time to decide when
        to re-download.
        """
        thumb = StreamerRowManager._stream_thumbnail_path(streamer)
        if not thumb.exists():
            return True
        age = time() - thumb.stat().st_mtime
        return age > 300  # 5 minutes

    def _download_stream_thumbnail(self, url, path, row, batch):
        """Download a stream thumbnail to disk.

        On completion, schedules _on_thumbnail_downloaded on the main
        thread.  The *batch* object tracks how many downloads are still
        outstanding so all thumbnails can be crossfaded at once.
        """
        try:
            sized_url = url.replace("{width}", "640").replace("{height}", "360")
            r = requests.get(sized_url, timeout=10)
            r.raise_for_status()
            path.write_bytes(r.content)
            GLib.idle_add(self._on_thumbnail_downloaded, row, path, batch)
        except Exception:
            logger.warning("Failed to download thumbnail for %s", path.stem)
            # Best-effort — still count as "done" so batch can proceed
            GLib.idle_add(self._on_thumbnail_downloaded, row, None, batch)

    def _on_thumbnail_downloaded(self, row, path, batch):
        """Called on the main thread when one thumbnail download finishes.

        When every download in the batch has reported back, all new
        thumbnails are applied together with an animated crossfade.
        """
        if batch is not self._current_batch:
            return GLib.SOURCE_REMOVE  # stale batch — ignore

        if path is not None and path.exists():
            stack = getattr(row, "_thumbnail_stack", None)
            streamer = getattr(row, "_streamer_key", None)
            if stack is not None and streamer is not None:
                batch.results[streamer] = (stack, path)

        batch.pending -= 1
        if batch.pending <= 0:
            self._apply_all_thumbnails(batch)
            self._current_batch = None

        return GLib.SOURCE_REMOVE

    def _apply_all_thumbnails(self, batch):
        """Apply all downloaded thumbnails at once with a crossfade.

        Each thumbnail is added to its card's ``Gtk.Stack`` as a new
        page named ``"live"``.  Switching to that page triggers the
        stack's ``CROSSFADE`` transition so the old placeholder (or
        stale cached thumbnail) smoothly blends into the new image.
        """
        count = len(batch.results)
        if count:
            logger.debug("Applying %d new thumbnail(s)", count)
        for stack, path in batch.results.values():
            texture = Gdk.Texture.new_from_filename(str(path))
            if texture is None:
                continue
            new_pic = Gtk.Picture.new_for_paintable(texture)
            new_pic.set_hexpand(True)
            new_pic.set_size_request(-1, 120)
            new_pic.set_content_fit(Gtk.ContentFit.COVER)
            new_pic.add_css_class("thumbnail")
            # Remove any previous "live" page so the stack does not
            # accumulate widgets across refresh cycles.
            old_live = stack.get_child_by_name("live")
            if old_live is not None:
                stack.remove(old_live)
            stack.add_named(new_pic, "live")
            stack.set_visible_child_name("live")

    def _build_stream_card(self, streamer, info):
        """Build a card-style row for an online streamer with a live
        preview thumbnail, stream metadata, and action buttons."""
        # ── Root card (vertical) ────────────────────────────
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add_css_class("card")

        # ── Thumbnail ───────────────────────────────────────
        thumb_url = info.get("thumbnail_url", "")
        thumb_path = self._stream_thumbnail_path(streamer) if thumb_url else None

        # Build the "old" page for the stack:
        #   1. Fresh cached thumbnail → show it (no download needed).
        #   2. Stale cached thumbnail → show it while downloading a
        #      new one so the crossfade blends old→new seamlessly.
        #   3. No cached file at all  → show the placeholder icon.
        if thumb_path and thumb_path.exists():
            old_page = Gtk.Picture.new_for_filename(str(thumb_path))
            old_page.set_content_fit(Gtk.ContentFit.COVER)
        else:
            old_page = Gtk.Box()
            old_page.add_css_class("thumbnail-placeholder")
            icon = Gtk.Image.new_from_icon_name("camera-video-symbolic")
            icon.set_pixel_size(48)
            icon.set_opacity(0.25)
            icon.set_halign(Gtk.Align.CENTER)
            icon.set_valign(Gtk.Align.CENTER)
            icon.set_hexpand(True)
            icon.set_vexpand(True)
            old_page.append(icon)
        old_page.set_hexpand(True)
        old_page.set_size_request(-1, 120)
        old_page.add_css_class("thumbnail")

        # Wrap in a Gtk.Stack so a fresh thumbnail can be crossfaded
        # in later, once every download in the batch is complete.
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_transition_duration(400)
        stack.set_hexpand(True)
        stack.set_size_request(-1, 120)
        stack.add_css_class("thumbnail-stack")
        stack.add_named(old_page, "placeholder")
        stack.set_visible_child_name("placeholder")
        row.append(stack)

        # Store references so the download callback can reach them.
        row._thumbnail_stack = stack
        row._streamer_key = streamer

        # Start background download if thumbnail is stale or missing
        if thumb_url and thumb_path and self._thumbnail_is_stale(streamer):
            batch = self._current_batch
            if batch is not None:
                batch.pending += 1
                threading.Thread(
                    target=self._download_stream_thumbnail,
                    args=(thumb_url, thumb_path, row, batch),
                    daemon=True,
                ).start()

        # ── Text area ───────────────────────────────────────
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text_box.set_margin_start(10)
        text_box.set_margin_end(10)
        text_box.set_margin_top(8)
        text_box.set_margin_bottom(8)

        # Resolve display name from user cache
        display_name = None
        if self.window.twitch is not None:
            display_name = self.window.twitch.user_cache.get(streamer, {}).get("name")
        if display_name and any(ord(c) > 127 for c in display_name):
            name_text = f"{display_name} ({streamer})"
        else:
            name_text = display_name or streamer

        show_avatars = getattr(self.window, "show_profile_pictures", True)
        viewers = info.get("viewers", "N/A")
        game = info.get("game", _("No category"))

        if show_avatars:
            # ── Row 1: avatar + name/game block ────────────
            top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

            # Avatar / play button (matches standard-row avatar style)
            avatar_path = None
            if self.window.twitch is not None:
                url = self.window.twitch.user_cache.get(streamer, {}).get(
                    "profile_image_url", ""
                )
                if url:
                    p = Path(self.window.twitch._get_avatars_dir()) / f"{streamer}.jpg"
                    if p.exists():
                        avatar_path = str(p)

            avatar_button = Gtk.Button()
            avatar_button.add_css_class("flat")
            avatar_button.add_css_class("avatar-play-button")
            avatar_button.set_valign(Gtk.Align.START)
            avatar_button.set_tooltip_text(_("Play stream"))
            avatar_button.set_overflow(Gtk.Overflow.HIDDEN)
            avatar_button.connect(
                "clicked",
                lambda btn: self.window.player.play_content(
                    f"twitch.tv/{streamer}", is_vod=False, display_name=display_name
                ),
            )

            overlay = Gtk.Overlay()
            overlay.set_overflow(Gtk.Overflow.HIDDEN)
            overlay.add_css_class("avatar-overlay")

            if avatar_path:
                pic = Gtk.Image.new_from_file(avatar_path)
                pic.set_pixel_size(44)
                overlay.set_child(pic)
                play_icon = Gtk.Image.new_from_icon_name(
                    "media-playback-start-symbolic"
                )
                play_icon.add_css_class("avatar-play-icon")
                overlay.add_overlay(play_icon)
            else:
                placeholder = Gtk.Image.new_from_icon_name(
                    "media-playback-start-symbolic"
                )
                placeholder.set_pixel_size(22)
                overlay.set_child(placeholder)

            avatar_button.set_child(overlay)
            top_row.append(avatar_button)
            row._avatar_button = avatar_button

            # Name + game/viewers block
            text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text_col.set_valign(Gtk.Align.CENTER)

            name_label = Gtk.Label(
                label=name_text,
                xalign=0,
                ellipsize=3,
                lines=1,
            )
            name_label.add_css_class("heading")
            text_col.append(name_label)

            meta_label = Gtk.Label(
                label=_("{} — {} viewers").format(game, viewers),
                xalign=0,
                wrap=True,
                wrap_mode=2,  # WORD
            )
            meta_label.add_css_class("dim-label")
            meta_label.add_css_class("caption")
            text_col.append(meta_label)

            top_row.append(text_col)
            text_box.append(top_row)
        else:
            # No avatars: name + game left-aligned
            name_label = Gtk.Label(
                label=name_text,
                xalign=0,
                ellipsize=3,
                lines=1,
            )
            name_label.add_css_class("heading")
            text_box.append(name_label)

            meta_label = Gtk.Label(
                label=_("{} — {} viewers").format(game, viewers),
                xalign=0,
                wrap=True,
                wrap_mode=2,  # WORD
            )
            meta_label.add_css_class("dim-label")
            meta_label.add_css_class("caption")
            text_box.append(meta_label)

        # Stream title (full width, below the avatar block)
        raw_title = info.get("title", _("No title"))
        title = unescape(raw_title)
        title_label = Gtk.Label(
            label=title,
            xalign=0,
            wrap=True,
            wrap_mode=2,  # WORD
            max_width_chars=40,
            ellipsize=3,  # END
            lines=2,
        )
        title_label.set_tooltip_text(title)
        text_box.append(title_label)

        # ── Bottom row: uptime + action buttons ────────────
        bottom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_row.set_margin_top(4)

        started_at = info.get("started_at")
        if started_at and self.window.twitch is not None:
            uptime = self.window.twitch.calculate_uptime(started_at)
        else:
            uptime = info.get("uptime", "")

        uptime_label = Gtk.Label(
            label=uptime,
            xalign=0,
            hexpand=True,
        )
        uptime_label.add_css_class("dim-label")
        uptime_label.add_css_class("caption")
        bottom_row.append(uptime_label)

        # Play button (only when PFPs are off; avatar handles it otherwise)
        if not show_avatars:
            play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
            play_button.add_css_class("flat")
            play_button.set_valign(Gtk.Align.CENTER)
            play_button.set_tooltip_text(_("Play stream"))
            play_button.connect(
                "clicked",
                lambda btn: self.window.player.play_content(
                    f"twitch.tv/{streamer}", is_vod=False, display_name=display_name
                ),
            )
            bottom_row.append(play_button)

        # Browser button
        browser_button = Gtk.Button(icon_name="web-browser-symbolic")
        browser_button.add_css_class("flat")
        browser_button.set_valign(Gtk.Align.CENTER)
        browser_button.set_tooltip_text(_("Open in browser"))
        browser_button.connect(
            "clicked",
            lambda btn: self.window.open_stream_in_browser(streamer),
        )
        bottom_row.append(browser_button)

        # Menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("view-more-symbolic")
        menu_button.add_css_class("flat")
        menu_button.set_valign(Gtk.Align.CENTER)
        menu_button.set_tooltip_text(_("More"))
        menu_button.set_popover(self._build_menu_popover(streamer))
        bottom_row.append(menu_button)

        text_box.append(bottom_row)
        row.append(text_box)

        return row

    def add_new_streamer(self, username):
        """New streamers are added to the offline list."""
        row = self.create_row(username, {})

        # Find insertion point to maintain alphabetical order by login name
        index = 0
        child = self.offline_list.get_first_child()
        while child is not None:
            # Compare using the stored login name
            if child.login_name.lower() > username.lower():
                break
            index += 1
            child = child.get_next_sibling()

        self.offline_list.insert(row, index)
        self.streamer_rows[username] = row
        self.window._update_streams_cache(username, add=True)
        return row

    def remove_streamer_row(self, username):
        """Remove streamer row from UI."""
        if username in self.streamer_rows:
            row = self.streamer_rows[username]
            parent = row.get_parent()
            if parent:
                # Card rows (Gtk.Box) are wrapped in an implicit ListBoxRow
                # by GTK, so row.get_parent() returns the wrapper, not the
                # ListBox.  Walk up and remove the wrapper instead.
                if not isinstance(parent, Gtk.ListBox):
                    row = parent
                    parent = parent.get_parent()
                parent.remove(row)
            # Clean up row reference
            del self.streamer_rows[username]
            self.window._update_streams_cache(username, add=False)

    def update_rows(self, online_streamers, offline_streamers, streamer_info):
        """Update all streamer rows."""

        # Detect newly-online and newly-offline streamers (skip if nothing was online before)
        current_online = set(online_streamers)
        new_online = set()
        new_offline = set()
        if self._previous_online:
            new_online = current_online - self._previous_online
            new_offline = self._previous_online - current_online
        self._previous_online = current_online

        # Toggle card mode on the online list based on thumbnail preference
        show_thumbnails = getattr(self.window, "show_stream_thumbnails", False)
        if show_thumbnails:
            self.window.online_list.remove_css_class("boxed-list")
        else:
            self.window.online_list.add_css_class("boxed-list")

        # Start a new download batch so all thumbnail downloads that
        # originate from this refresh are crossfaded in together.
        self._current_batch = self._ThumbnailBatch()

        # Clear existing rows
        def clear_list(list_box):
            while row := list_box.get_first_child():
                list_box.remove(row)

        clear_list(self.online_list)
        clear_list(self.offline_list)
        self.streamer_rows.clear()

        # Sort streamers alphabetically (case-insensitive)
        online_streamers.sort(key=str.lower)
        offline_streamers.sort(key=str.lower)

        # Add new streamer rows
        for streamer in online_streamers:
            info = streamer_info.get(streamer, {})
            if show_thumbnails:
                row = self._build_stream_card(streamer, info)
            else:
                row = self.create_row(streamer, info)
            if streamer in new_online:
                row.add_css_class("just-went-online")
                GLib.timeout_add(ROW_HIGHLIGHT_MS, self._start_highlight_fade, row)
            self.online_list.append(row)
            self.streamer_rows[streamer] = row

        for streamer in offline_streamers:
            row = self.create_row(streamer, {})
            self.offline_list.append(row)
            self.streamer_rows[streamer] = row

        # Show pill badge for any online / offline changes
        if new_online or new_offline:
            self._show_pill(len(new_online), len(new_offline))

    def _start_highlight_fade(self, row):
        """Begin fade-out of the just-went-online highlight."""
        row.add_css_class("highlight-fading")
        GLib.timeout_add(PILL_FADE_MS, self._clear_highlight, row)
        return GLib.SOURCE_REMOVE

    def _clear_highlight(self, row):
        """Remove all just-went-online classes after fade completes."""
        row.remove_css_class("just-went-online")
        row.remove_css_class("highlight-fading")
        return GLib.SOURCE_REMOVE

    def _show_pill(self, went_online, went_offline):
        """Show a divided pill badge on the online group header."""
        # Remove all children and rebuild
        while child := self._pill_box.get_first_child():
            self._pill_box.remove(child)

        if went_online:
            self._pill_plus.set_label(f"+{went_online}")
            self._pill_plus.remove_css_class("pill-solo")
            if not went_offline:
                self._pill_plus.add_css_class("pill-solo")
            self._pill_box.append(self._pill_plus)

        if went_offline:
            self._pill_minus.set_label(f"−{went_offline}")
            self._pill_minus.remove_css_class("pill-solo")
            if not went_online:
                self._pill_minus.add_css_class("pill-solo")
            self._pill_box.append(self._pill_minus)

        self._pill_box.remove_css_class("fading")
        self._pill_box.set_visible(True)

        # Phase 1: show static, then start fading
        if self._pill_timeout_id:
            GLib.source_remove(self._pill_timeout_id)
        self._pill_timeout_id = GLib.timeout_add(PILL_SHOW_MS, self._start_pill_fade)

    def _start_pill_fade(self):
        """Begin fade-out."""
        self._pill_box.add_css_class("fading")
        self._pill_timeout_id = GLib.timeout_add(PILL_FADE_MS, self._hide_pill)
        return GLib.SOURCE_REMOVE

    def _hide_pill(self):
        """Hide the pill badge and reset state."""
        self._pill_box.set_visible(False)
        self._pill_box.remove_css_class("fading")
        self._pill_timeout_id = None
        return GLib.SOURCE_REMOVE
