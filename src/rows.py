import gettext
from html import unescape

from gi.repository import Adw, Gio, GLib, Gtk

from .config import PILL_FADE_MS, PILL_SHOW_MS, RESOURCE_BASE, ROW_HIGHLIGHT_MS

_ = gettext.gettext


class StreamerRowManager:
    _CSS_LOADED = False  # class-level flag — only load once ever

    def __init__(self, window):
        self.window = window
        self.streamer_rows = {}
        self.online_list = window.online_list
        self.offline_list = window.offline_list
        self._previous_online = set()

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
        css_provider.load_from_resource(f"{RESOURCE_BASE}/css/streamline.css")
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
                uptime = self.window.twitch._calculate_uptime(started_at)
            else:
                uptime = info.get("uptime", "N/A")
            tooltip = _("Title: {}\nUptime: {}").format(title, uptime)
            row.set_tooltip_text(tooltip)
        else:  # Offline streamer
            row.add_css_class("offline-row")

        # Add buttons
        self._add_row_buttons(row, streamer)
        return row

    def _add_row_buttons(self, row, streamer):
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
                    from pathlib import Path

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
                    f"twitch.tv/{streamer}", is_vod=False
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
                    f"twitch.tv/{streamer}", is_vod=False
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

        # Create menu model
        menu = Gio.Menu.new()

        # Add menu items - no icons needed
        menu_items = [
            (_("Chat"), "chat", self.window.show_chat_page),
            (_("Detached Chat"), "detached-chat", self.window.show_chat_popup),
            (_("Show VODs"), "show-vods", self.window.show_vods_page),
            (_("Unfollow"), "unfollow", self.window.unfollow_streamer),
        ]

        for label, action_name, handler in menu_items:
            item = Gio.MenuItem.new(label, f"row.{action_name}")
            menu.append_item(item)

        # Create popup menu
        popover = Gtk.PopoverMenu.new_from_model(menu)
        menu_button.set_popover(popover)

        # Add actions to the row
        action_group = Gio.SimpleActionGroup.new()
        row.insert_action_group("row", action_group)

        # Create the actions
        chat_action = None
        detach_action = None
        for _label, action_name, handler in menu_items:
            action = Gio.SimpleAction.new(action_name, None)
            if action_name == "chat":
                chat_action = action
            elif action_name == "detached-chat":
                detach_action = action
            action.connect("activate", lambda act, param, h=handler: h(streamer))
            action_group.add_action(action)

        def _sync_chat_actions(popover):
            if popover.get_visible():
                chat_open = streamer in self.window._active_chats
                if chat_action:
                    chat_action.set_enabled(not chat_open)
                if detach_action:
                    detach_action.set_enabled(not chat_open)

        popover.connect("notify::visible", lambda p, *_: _sync_chat_actions(p))

        row.add_suffix(menu_button)

    def set_avatar(self, streamer, path):
        """Swap a streamer's placeholder icon for its avatar image (called
        from the background downloader via GLib.idle_add)."""
        row = self.streamer_rows.get(streamer)
        if row is None or not hasattr(row, "_play_button"):
            return
        button = row._play_button
        overlay = button.get_child()

        # Replace the placeholder with the avatar and add the play-icon overlay
        pic = Gtk.Image.new_from_file(path)
        pic.set_pixel_size(48)
        overlay.set_child(pic)

        if row.is_online:
            icon = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
            icon.add_css_class("avatar-play-icon")
            overlay.add_overlay(icon)

        button.set_child(overlay)

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
            row = self.create_row(streamer, streamer_info.get(streamer, {}))
            if streamer in new_online:
                row.add_css_class("just-went-online")
                GLib.timeout_add(ROW_HIGHLIGHT_MS, self._clear_highlight, row)
            self.online_list.append(row)
            self.streamer_rows[streamer] = row

        for streamer in offline_streamers:
            row = self.create_row(streamer, {})
            self.offline_list.append(row)
            self.streamer_rows[streamer] = row

        # Show pill badge for any online / offline changes
        if new_online or new_offline:
            self._show_pill(len(new_online), len(new_offline))

    def _clear_highlight(self, row):
        """Remove the just-went-online glow from a row."""
        row.remove_css_class("just-went-online")
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

        # Phase 1: show for 3s, then start fading
        if self._pill_timeout_id:
            GLib.source_remove(self._pill_timeout_id)
        self._pill_timeout_id = GLib.timeout_add(PILL_SHOW_MS, self._start_pill_fade)

    def _start_pill_fade(self):
        """Begin fade-out, then hide after 1s."""
        self._pill_box.add_css_class("fading")
        self._pill_timeout_id = GLib.timeout_add(PILL_FADE_MS, self._hide_pill)
        return GLib.SOURCE_REMOVE

    def _hide_pill(self):
        """Hide the pill badge and reset state."""
        self._pill_box.set_visible(False)
        self._pill_box.remove_css_class("fading")
        self._pill_timeout_id = None
        return GLib.SOURCE_REMOVE
