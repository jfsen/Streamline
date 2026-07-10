# window.py
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

import gettext
import logging
import threading

import gi

_ = gettext.gettext
import requests

logger = logging.getLogger(__name__)

gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk

from .auto_refresh import AutoRefresher
from .config import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    MIN_HEIGHT,
    MIN_WIDTH,
    TWITCH_CLIENT_ID,
    TWITCH_CLIENT_SECRET,
)
from .dialogs import StreamlineDialogs
from .preferences import StreamlinePreferences
from .rows import StreamerRowManager
from .stream_player import StreamPlayer
from .theme import ThemeManager
from .twitch import TwitchAPI
from .vod_page import VODPage

# ── Application identity ───────────────────────────────────

APP_ID = "org.jfsen.Streamline"


@Gtk.Template(resource_path="/org/jfsen/Streamline/window.ui")
class StreamlineWindow(Adw.ApplicationWindow):
    __gtype_name__ = "StreamlineWindow"

    preferences_page = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()
    online_group = Gtk.Template.Child()
    offline_group = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    quick_play_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ── Phase 1: fast setup so the window presents immediately ──

        # Initialize GSettings and populate attributes
        self.settings = Gio.Settings.new("org.jfsen.Streamline")
        self._initialize_from_config()

        # Store CSS provider for theme overrides
        self.theme_manager = ThemeManager(self, self.settings)
        self.theme_manager.apply()
        self.settings.connect("changed::theme", lambda s, k: self.theme_manager.apply())

        # React to live preference changes that affect row rendering
        self.settings.connect(
            "changed::show-profile-pictures", self._on_row_pref_changed
        )
        self.settings.connect(
            "changed::show-stream-thumbnails", self._on_row_pref_changed
        )

        # React to streamer list changes from CLI or external tools.
        self.settings.connect("changed::streamers", self._on_streamers_changed)

        # Auto-refresh timer (reacts to its own GSettings keys internally).
        self._auto_refresher = AutoRefresher(self)

        # Stored fetch data so preferences can rebuild rows without a network call
        self._last_online = []
        self._last_offline = []
        self._last_info = {}

        # Set minimum window size
        self.set_size_request(MIN_WIDTH, MIN_HEIGHT)

        # Default to a compact window
        self.set_default_size(DEFAULT_WIDTH, DEFAULT_HEIGHT)

        # Initialize dialogs manager (lightweight)
        self.dialogs = StreamlineDialogs(self)

        # Create ListBoxes for online and offline streamers
        self.online_list = Gtk.ListBox()
        self.online_list.add_css_class("boxed-list")
        self.online_list.add_css_class("streamer-list")
        self.online_list.set_selection_mode(Gtk.SelectionMode.NONE)

        self.offline_list = Gtk.ListBox()
        self.offline_list.add_css_class("boxed-list")
        self.offline_list.add_css_class("streamer-list")
        self.offline_list.set_selection_mode(Gtk.SelectionMode.NONE)

        # Row manager (CSS loading deferred until first row is created)
        # Must be created before adding lists to groups,
        # because it inserts custom headers as each group's first child.
        self.row_manager = StreamerRowManager(self)

        self.online_group.add(self.online_list)
        self.offline_group.add(self.offline_list)

        # Initialize API-related attributes
        self.twitch = None
        self._startup_complete = False

        # Initialize StreamPlayer (lightweight)
        self.player = StreamPlayer(self)

        # Connect headerbar buttons
        self.refresh_button.connect("clicked", self.on_refresh_button_clicked)
        self.quick_play_button.connect("clicked", self.quick_play)

        # Add navigation view
        self.navigation_view = Adw.NavigationView()
        self.main_content = self.get_content()
        self.set_content(self.navigation_view)

        # Add main page
        self.main_page = Adw.NavigationPage(
            title=_("Streamline"), child=self.main_content
        )
        self.navigation_view.add(self.main_page)
        self._preferences = None
        self._active_chats = {}  # streamer → ChatPage or ChatWindow

        # ── Phase 2: deferred heavy work — runs after the first frame paints ──
        GLib.idle_add(self._complete_startup, priority=GLib.PRIORITY_LOW)

    def _complete_startup(self):
        """Finish initialization that can wait until the window is on screen."""
        if self._startup_complete:
            return GLib.SOURCE_REMOVE
        self._startup_complete = True

        # Initialize API — credentials come from config.py
        self._init_twitch_api()

        # If API couldn't start, show streamers as offline
        if not self.twitch:
            self.update_action_rows([], self.all_streamers, {})

        # Start auto-refresh timer if enabled.
        self._auto_refresher.start()

        return GLib.SOURCE_REMOVE

    def _initialize_from_config(self):
        """Populate window attributes from GSettings.

        Credentials are sourced from config.py only.
        """
        self.client_id = TWITCH_CLIENT_ID
        self.client_secret = TWITCH_CLIENT_SECRET
        self.player_type = self.settings.get_string("player-type")
        self.custom_player_path = self.settings.get_string("custom-player-path")
        self.all_streamers = self.settings.get_strv("streamers")
        self.stream_quality = self.settings.get_string("stream-quality")
        self.custom_quality = self.settings.get_string("custom-quality")
        self.theme = self.settings.get_string("theme")
        self.low_latency = self.settings.get_boolean("low-latency")
        self.chat_alternating_bg = self.settings.get_boolean(
            "chat-alternating-background"
        )
        self.chat_disable_emote_animations = self.settings.get_boolean(
            "chat-disable-emote-animations"
        )
        self.show_profile_pictures = self.settings.get_boolean("show-profile-pictures")
        self.show_stream_thumbnails = self.settings.get_boolean(
            "show-stream-thumbnails"
        )
        self.chat_highlight_first_msg = self.settings.get_boolean(
            "chat-highlight-first-msg"
        )
        self.chat_highlight_mod = self.settings.get_boolean("chat-highlight-mod")
        self.chat_highlight_vip = self.settings.get_boolean("chat-highlight-vip")
        self.chat_highlight_partner = self.settings.get_boolean(
            "chat-highlight-partner"
        )
        self.chat_highlight_broadcaster = self.settings.get_boolean(
            "chat-highlight-broadcaster"
        )
        self.auto_refresh = self.settings.get_boolean("auto-refresh")
        self.auto_refresh_interval = self.settings.get_int("auto-refresh-interval")

    def _init_twitch_api(self):
        """Initialize the Twitch API with credentials from config.py.

        Shows cached streamer data immediately so the window opens fast,
        then fetches fresh data in a background thread.
        """
        if not self.client_id or not self.client_secret:
            logger.warning("Twitch API not configured — client ID or secret missing")
            return

        self.twitch = None
        try:
            self.twitch = TwitchAPI(self.client_id, self.client_secret)
            logger.info("Twitch API initialized")
        except Exception:
            logger.exception("Failed to initialize Twitch API")
            return

        # Show cached data immediately so the window opens fast
        cached_data, _cooldown = self.twitch._load_streams_cache()
        if cached_data:
            self.update_action_rows(
                list(cached_data["online"].keys()),
                cached_data["offline"],
                cached_data["online"],
            )
        else:
            self.update_action_rows([], self.all_streamers, {})

        # Fetch fresh stream data in a background thread
        threading.Thread(target=self._background_fetch_streams, daemon=True).start()

    def _show_fetch_error(self, error):
        """Route a fetch error to the appropriate UI callback."""
        if isinstance(error, requests.ConnectionError):
            GLib.idle_add(
                self._show_error_dialog,
                _("Connection Error"),
                _(
                    "Could not fetch streamer data. "
                    "Please check your internet connection."
                ),
            )
        elif isinstance(error, requests.HTTPError):
            status = error.response.status_code if error.response else None
            if status == 401:
                GLib.idle_add(
                    self._show_error_dialog,
                    _("Authentication Error"),
                    _(
                        "API authentication failed (HTTP 401). "
                        "Check your Twitch Client ID and Client Secret in config.py."
                    ),
                )
            elif status == 429:
                GLib.idle_add(
                    self._show_error_dialog,
                    _("API Rate Limit"),
                    _(
                        "API rate limit reached. "
                        "Please wait a few minutes before refreshing again."
                    ),
                )
            else:
                GLib.idle_add(
                    self._show_error_dialog,
                    _("API Error"),
                    _("HTTP error {} occurred while fetching streamer data.").format(
                        status
                    ),
                )
        else:
            GLib.idle_add(
                self._show_error_dialog,
                _("API Error"),
                _("Failed to fetch streamer data: {}").format(str(error)[:100]),
            )

    def _background_fetch_streams(self):
        """Fetch stream data in a background thread, then update UI on main thread."""
        assert self.twitch is not None
        try:
            online, offline, info = self.twitch.get_streams(self.all_streamers)
            GLib.idle_add(self._on_streams_fetched, online, offline, info)
        except Exception as e:
            logger.error("Initial stream fetch failed: %s", e)
            self._show_fetch_error(e)

    def _on_streams_fetched(self, online, offline, info):
        """Callback from background thread: update UI with fresh stream data."""
        self.update_action_rows(online, offline, info)
        self._start_avatar_downloads(online, offline)

    def on_refresh_button_clicked(self, button):
        """Refresh streamer data in a background thread."""
        if not self.twitch:
            self.show_toast(_("API not available"))
            return

        # Check cache status: enforce cooldown
        cached_data, seconds_until_refresh = self.twitch._load_streams_cache()

        if seconds_until_refresh > 0:
            # Data is still within cooldown, inform user how long until refresh is available
            self.show_toast(
                _("Please wait {}s before refreshing").format(seconds_until_refresh)
            )
            return

        # Cache is expired, do refresh in background thread
        self.refresh_button.set_sensitive(False)
        threading.Thread(target=self._background_refresh, daemon=True).start()

    def _background_refresh(self):
        """Perform refresh in background thread, then update UI on main thread."""
        assert self.twitch is not None
        try:
            online, offline, info = self.twitch.get_streams(self.all_streamers)
            GLib.idle_add(self._on_refresh_complete, online, offline, info)
        except Exception as e:
            GLib.idle_add(self._on_refresh_error, e)

    def _on_refresh_complete(self, online, offline, info):
        """Callback from background thread: update UI with fresh stream data."""
        self.refresh_button.set_sensitive(True)
        self.show_toast(_("Stream data refreshed"))
        self.update_action_rows(online, offline, info)
        self._start_avatar_downloads(online, offline)

    def _on_refresh_error(self, error):
        """Callback from background thread: show error and re-enable refresh button."""
        logger.error("Stream refresh failed: %s", error)
        self.refresh_button.set_sensitive(True)
        # Expire cache cooldown so next manual refresh attempt is not blocked
        if self.twitch is not None:
            self.twitch._invalidate_streams_cache()
        self._show_fetch_error(error)

    # ── Row management ───────────────────────────────────────

    def update_action_rows(self, online_streamers, offline_streamers, streamer_info):
        """Update the online and offline streamer lists."""
        self._last_online = online_streamers
        self._last_offline = offline_streamers
        self._last_info = streamer_info
        self.row_manager.update_rows(online_streamers, offline_streamers, streamer_info)

    def _on_row_pref_changed(self, settings, key):
        """Live-rebuild rows when a preference affecting row rendering changes."""
        self._initialize_from_config()
        # Derive current lists from self.all_streamers so that any
        # streamers added or removed since the last refresh (via GUI
        # dialog, CLI, or import) are included in the rebuild.
        online = [s for s in self.all_streamers if s in self._last_online]
        offline = [s for s in self.all_streamers if s not in self._last_online]
        if online or offline:
            self.row_manager.update_rows(online, offline, self._last_info)
            self._start_avatar_downloads(online, offline)

    def _start_avatar_downloads(self, online, offline):
        """Kick off background avatar downloads for streamers that don't have
        a cached image yet."""
        if not getattr(self, "show_profile_pictures", True) or self.twitch is None:
            return
        self.twitch.download_avatars_background(
            online + offline, self.row_manager.set_avatar
        )

    def create_row(self, streamer, info):
        """Create an ActionRow with buttons and additional info."""
        return self.row_manager.create_row(streamer, info)

    def open_stream_in_browser(self, streamer):
        """Open the Twitch stream page in default browser."""
        url = f"https://twitch.tv/{streamer}"
        Gtk.show_uri(parent=self, uri=url, timestamp=0)

    def _show_error_dialog(self, heading, message):
        """Show error dialog with the given heading and message."""
        self.dialogs.show_error_dialog(heading, message)

    def unfollow_streamer(self, streamer):
        """Remove a streamer from the list."""
        if streamer in self.all_streamers:
            self.dialogs.show_unfollow_dialog(streamer, self._handle_unfollow)

    def _handle_unfollow(self, dialog, response, streamer):
        """Handle unfollow action."""
        self.all_streamers.remove(streamer)
        self.settings.set_value("streamers", GLib.Variant("as", self.all_streamers))
        self.save_config()
        self.remove_streamer_row(streamer)

    def follow_streamer(self, *args):
        """Show dialog to follow new streamer."""
        self.dialogs.show_follow_dialog(self._handle_follow)

    def _handle_follow(self, username):
        """Handle follow dialog callback with support for multiple streamers."""
        # Split input by commas and strip whitespace
        usernames = [name.strip().lower() for name in username.split(",")]
        # Remove empty strings
        usernames = [name for name in usernames if name]

        if not usernames:
            return

        # Convert all existing streamers to lowercase for comparison
        existing_streamers_lower = [s.lower() for s in self.all_streamers]

        # Separate new candidates from already-followed ones
        new_candidates = []
        already_following = []

        for name in usernames:
            if name not in existing_streamers_lower:
                new_candidates.append(name)
            else:
                original_index = existing_streamers_lower.index(name)
                already_following.append(self.all_streamers[original_index])

        # Validate new candidates against the Twitch API before adding
        invalid = []
        if new_candidates and self.twitch is not None:
            try:
                self.twitch.get_users(new_candidates)
            except Exception:
                # If API call fails, skip validation and add all candidates
                pass
            else:
                invalid = [
                    name
                    for name in new_candidates
                    if name not in self.twitch.user_cache
                ]
                new_candidates = [
                    name for name in new_candidates if name not in invalid
                ]

        # Add valid new streamers
        for name in new_candidates:
            self.all_streamers.append(name)
            self.add_offline_streamer(name)

        # Start background avatar downloads for newly followed streamers
        if new_candidates:
            logger.info(
                "Following %d new streamer(s): %s",
                len(new_candidates),
                ", ".join(new_candidates),
            )
            self._start_avatar_downloads([], new_candidates)

        if new_candidates:
            self.settings.set_value("streamers", GLib.Variant("as", self.all_streamers))
            self.save_config()

        # Build a single consolidated toast.
        # Priority: always report invalid names; only mention already-followed
        # when it's the sole outcome (nothing added and nothing invalid).
        parts = []
        if new_candidates:
            if len(new_candidates) == 1:
                parts.append(_("Added {}").format(new_candidates[0]))
            else:
                parts.append(_("Added {} streamers").format(len(new_candidates)))
        if invalid:
            if len(invalid) == 1:
                parts.append(_("'{}' is invalid").format(invalid[0]))
            else:
                parts.append(_("{} invalid names").format(len(invalid)))
        if already_following and not parts:
            if len(already_following) == 1:
                parts.append(_("Already following {}").format(already_following[0]))
            else:
                parts.append(_("Already following"))

        if parts:
            self.show_toast(" · ".join(parts), 4)

    def quick_play(self, *args):
        """Show dialog to quickly play a stream."""
        self.dialogs.show_quick_play_dialog(self._handle_quick_play)

    def _handle_quick_play(self, username):
        """Handle quick play dialog callback."""
        try:
            self.player.play_content(f"twitch.tv/{username}", is_vod=False)
        except Exception as e:
            self.show_toast(_("Error: {}").format(str(e)), 4)

    def save_config(self):
        """Commit any pending GSettings changes to disk."""
        try:
            self.settings.apply()
        except Exception as e:
            logger.error("Failed to save settings: %s", e)
            self.dialogs.show_error_dialog(
                _("Error Saving Config"), _("Could not save configuration")
            )

    def show_preferences(self, *args):
        """Show preferences dialog. Re-syncs cached attrs from GSettings on close,
        since bindings in the dialog write directly to GSettings."""
        if not self._preferences:
            prefs = StreamlinePreferences(self)
            prefs.connect("closed", self._on_preferences_closed)
            self._preferences = prefs

        self._preferences.present(self)

    def _on_preferences_closed(self, dialog):
        self._preferences = None
        self._initialize_from_config()
        self._auto_refresher.start()

    def show_shortcuts(self, *args):
        """Show keyboard shortcuts dialog."""
        dialog = Adw.ShortcutsDialog()

        # Application section
        app_section = Adw.ShortcutsSection(title=_("Application"))
        app_section.add(Adw.ShortcutsItem.new(_("Preferences"), "<primary>comma"))
        app_section.add(Adw.ShortcutsItem.new(_("Show Shortcuts"), "<primary>question"))
        app_section.add(Adw.ShortcutsItem.new(_("Quit"), "<primary>q"))
        dialog.add(app_section)

        # Stream Management section
        stream_section = Adw.ShortcutsSection(title=_("Stream Management"))
        stream_section.add(Adw.ShortcutsItem.new(_("Follow"), "<primary>n"))
        stream_section.add(Adw.ShortcutsItem.new(_("Quick Play"), "<primary>p"))
        try:
            refresh_item = Adw.ShortcutsItem.new(_("Refresh Streams"), "<primary>r")
            refresh_item.set_property("accelerator2", "F5")
            stream_section.add(refresh_item)
        except (AttributeError, TypeError):
            stream_section.add(
                Adw.ShortcutsItem.new(_("Refresh Streams"), "<primary>r")
            )
            stream_section.add(Adw.ShortcutsItem.new(_("Refresh Streams"), "F5"))
        dialog.add(stream_section)

        dialog.present(self)

    def show_toast(self, text, timeout=2):
        """Show a toast notification."""
        toast = Adw.Toast.new(text)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    def _update_streams_cache(self, username, add=True):
        """Update streams cache without modifying timestamp."""
        if self.twitch:
            self.twitch.update_streams_cache(username, add)

    def _on_streamers_changed(self, settings, key):
        """Sync the UI when the streamer list changes externally (e.g. CLI)."""
        new_list = list(settings.get_strv(key))
        old_set = {s.lower() for s in self.all_streamers}
        new_set = {s.lower() for s in new_list}

        added = [s for s in new_list if s.lower() not in old_set]
        removed = [s for s in self.all_streamers if s.lower() not in new_set]

        if not added and not removed:
            return

        for name in removed:
            self.remove_streamer_row(name)
            logger.info("Removed streamer (external): %s", name)
        for name in added:
            self.add_offline_streamer(name)
            logger.info("Added streamer (external): %s", name)

        self.all_streamers = new_list
        self._start_avatar_downloads([], added)

    def add_offline_streamer(self, username):
        """Add new offline streamer row."""
        return self.row_manager.add_new_streamer(username)

    def remove_streamer_row(self, username):
        """Remove streamer row from UI."""
        self.row_manager.remove_streamer_row(username)

    def show_vods_page(self, streamer):
        """Show VODs page for the given streamer."""
        logger.info("Opening VOD page for %s", streamer)
        # Resolve display name from user cache
        display_name = streamer
        if self.twitch is not None:
            user_data = self.twitch.user_cache.get(streamer, {})
            display_name = user_data.get("name", streamer)
        page = VODPage(self, streamer, display_name, self.twitch, self.player)
        self.navigation_view.push(page)

    def show_chat_page(self, streamer):
        """Show chat page for the given streamer, reusing existing if open."""
        existing = self._active_chats.get(streamer)
        if existing is not None:
            if isinstance(existing, Adw.NavigationPage):
                self.navigation_view.push(existing)
            return

        # Resolve display name from user cache
        display_name = streamer
        if self.twitch is not None:
            user_data = self.twitch.user_cache.get(streamer, {})
            display_name = user_data.get("name", streamer)

        logger.info("Opening chat page for %s", streamer)
        from .chat.page import ChatPage

        page = ChatPage(
            self,
            streamer,
            display_name=display_name,
            alternating_bg=self.chat_alternating_bg,
            disable_emote_animations=self.chat_disable_emote_animations,
            theme=self.theme,
            twitch=self.twitch,
            enable_detach=True,
            highlight_first_msg=self.chat_highlight_first_msg,
            highlight_mod=self.chat_highlight_mod,
            highlight_vip=self.chat_highlight_vip,
            highlight_partner=self.chat_highlight_partner,
            highlight_broadcaster=self.chat_highlight_broadcaster,
        )
        page.connect(
            "hidden",
            lambda p, s=streamer: (
                self._active_chats.pop(s, None)
                if self._active_chats.get(s) is p
                else None
            ),
        )
        self._active_chats[streamer] = page
        self.navigation_view.push(page)

    def show_chat_popup(self, streamer):
        """Open chat in a separate pop-up window, reusing existing if open."""
        existing = self._active_chats.get(streamer)
        if existing is not None:
            existing.present()
            return

        logger.info("Opening chat popup for %s", streamer)
        from .chat.chat_window import ChatWindow

        # Resolve display name from user cache
        display_name = streamer
        if self.twitch is not None:
            user_data = self.twitch.user_cache.get(streamer, {})
            display_name = user_data.get("name", streamer)

        popup = ChatWindow(
            twitch=self.twitch,
            streamer=streamer,
            display_name=display_name,
            alternating_bg=self.chat_alternating_bg,
            disable_emote_animations=self.chat_disable_emote_animations,
            theme=self.theme,
            transient_for=self,
            highlight_first_msg=self.chat_highlight_first_msg,
            highlight_mod=self.chat_highlight_mod,
            highlight_vip=self.chat_highlight_vip,
            highlight_partner=self.chat_highlight_partner,
            highlight_broadcaster=self.chat_highlight_broadcaster,
        )
        popup.connect(
            "close-request",
            lambda w, s=streamer: (self._active_chats.pop(s, None), False)[-1],
        )
        self._active_chats[streamer] = popup
        popup.present()
