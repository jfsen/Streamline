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

logger = logging.getLogger("Window")

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk

from .config import ConfigManager
from .dialogs import StreamlineDialogs
from .preferences import StreamlinePreferences
from .rows import StreamerRowManager
from .stream_player import StreamPlayer
from .twitch import TwitchAPI
from .vod_page import VODPage


@Gtk.Template(resource_path="/io/github/jfsen/Streamline/window.ui")
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

        # Initialize config manager first
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()

        # Initialize all attributes from config
        self._initialize_from_config(self.config)

        # Store CSS provider for theme overrides
        self._theme_css_provider = None

        # Get and store style manager reference
        self.style_manager = Adw.StyleManager.get_default()

        # Apply the current theme (handles bronze via CSS, otherwise normal Adw)
        self._apply_theme()

        # Set minimum window size
        self.set_size_request(320, 400)

        # Default to a compact window
        self.set_default_size(360, 700)

        # Initialize dialogs manager (lightweight)
        self.dialogs = StreamlineDialogs(self)

        # Create ListBoxes for online and offline streamers
        self.online_list = Gtk.ListBox()
        self.online_list.add_css_class("boxed-list")
        self.online_list.add_css_class("streamer-list")
        self.online_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.online_group.add(self.online_list)

        self.offline_list = Gtk.ListBox()
        self.offline_list.add_css_class("boxed-list")
        self.offline_list.add_css_class("streamer-list")
        self.offline_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.offline_group.add(self.offline_list)

        # Row manager (CSS loading deferred until first row is created)
        self.row_manager = StreamerRowManager(self)

        # Initialize API-related attributes
        self.twitch = None
        self._credentials_prompt_shown = False
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

        # ── Phase 2: deferred heavy work — runs after the first frame paints ──
        GLib.idle_add(self._complete_startup, priority=GLib.PRIORITY_LOW)

    def _complete_startup(self):
        """Finish initialization that can wait until the window is on screen."""
        if self._startup_complete:
            return GLib.SOURCE_REMOVE
        self._startup_complete = True

        # Check credentials and initialize API
        if not self.client_id or not self.client_secret:
            self._prompt_for_credentials()
        else:
            self._init_twitch_api()

        # If neither API nor credentials are available, show streamers as offline
        if not self.twitch and not self._credentials_prompt_shown:
            self.update_action_rows([], self.all_streamers, {})

        return GLib.SOURCE_REMOVE

    def _initialize_from_config(self, config):
        """Initialize all attributes from config in one pass"""
        self.client_id = config.get("twitch_client_id", "")
        self.client_secret = config.get("twitch_client_secret", "")
        self.player_type = config.get("player_type", "mpv")
        self.custom_player_path = config.get("custom_player_path", "")
        self.all_streamers = config.get("streamers", [])
        self.stream_quality = config.get("stream_quality", "High")
        self.custom_quality = config.get("custom_quality", "best")
        self.theme = config.get("theme", "system")
        self.low_latency = config.get("low_latency", True)

    def _init_twitch_api(self):
        """Initialize the Twitch API with current credentials.

        Shows cached streamer data immediately so the window opens fast,
        then fetches fresh data in a background thread.
        """
        self.twitch = None
        try:
            self.twitch = TwitchAPI(self.client_id, self.client_secret)
        except Exception as e:
            error_msg = str(e)
            # Show prompt to re-enter credentials on auth failure
            if (
                "401" in error_msg
                or "Unauthorized" in error_msg
                or "access_token" in error_msg
            ):
                self._prompt_for_credentials()
            else:
                self.dialogs.show_credentials_dialog(self._handle_credentials)
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

    def _background_fetch_streams(self):
        """Fetch stream data in a background thread, then update UI on main thread."""
        try:
            online, offline, info = self.twitch.get_streams(self.all_streamers)
            GLib.idle_add(self._on_streams_fetched, online, offline, info)
        except requests.ConnectionError:
            GLib.idle_add(
                self._show_error_dialog,
                _("Connection Error"),
                _(
                    "Could not fetch streamer data. Please check your internet connection."
                ),
            )
        except requests.HTTPError as e:
            status = e.response.status_code if e.response else None
            if status == 401:
                GLib.idle_add(self._prompt_for_credentials)
            elif status == 429:
                GLib.idle_add(
                    self._show_error_dialog,
                    _("API Rate Limit"),
                    _(
                        "Twitch API rate limit reached. Please wait a few minutes before refreshing again."
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
        except Exception as e:
            GLib.idle_add(
                self._show_error_dialog,
                _("API Error"),
                _("Failed to fetch streamer data: {}").format(str(e)[:100]),
            )

    def _on_streams_fetched(self, online, offline, info):
        """Callback from background thread: update UI with fresh stream data."""
        self.update_action_rows(online, offline, info)

    def _prompt_for_credentials(self):
        """Show dialog to prompt user for Twitch API credentials."""
        if self._credentials_prompt_shown:
            return
        self._credentials_prompt_shown = True
        self.dialogs.show_credentials_dialog(self._handle_credentials)

    def _handle_credentials(self, client_id, client_secret):
        """Handle credentials submitted by user."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.save_config()
        self._credentials_prompt_shown = False
        self._init_twitch_api()

    def on_refresh_button_clicked(self, button):
        """Refresh streamer data in a background thread."""
        if not self.twitch:
            self.show_toast(_("API not available"))
            return

        # Check cache status
        cached_data, seconds_until_refresh = self.twitch._load_streams_cache()

        if cached_data is not None:
            # Data is still cached, inform user how long until refresh is available
            self.show_toast(
                _("Please wait {}s before refreshing").format(seconds_until_refresh)
            )
            return

        # Cache is expired, do refresh in background thread
        self.refresh_button.set_sensitive(False)
        threading.Thread(target=self._background_refresh, daemon=True).start()

    def _background_refresh(self):
        """Perform refresh in background thread, then update UI on main thread."""
        try:
            online, offline, info = self.twitch.get_streams(self.all_streamers)
            GLib.idle_add(self._on_refresh_complete, online, offline, info)
        except requests.ConnectionError:
            GLib.idle_add(
                self._on_refresh_error,
                _("Connection Error"),
                _(
                    "Could not fetch streamer data. Please check your internet connection."
                ),
            )
        except requests.HTTPError as e:
            status = e.response.status_code if e.response else None
            if status == 401:
                GLib.idle_add(self._prompt_for_credentials)
            elif status == 429:
                GLib.idle_add(
                    self._on_refresh_error,
                    _("API Rate Limit"),
                    _(
                        "Twitch API rate limit reached. Please wait a few minutes before refreshing again."
                    ),
                )
            else:
                GLib.idle_add(
                    self._on_refresh_error,
                    _("API Error"),
                    _("HTTP error {} occurred while fetching streamer data.").format(
                        status
                    ),
                )
        except Exception as e:
            GLib.idle_add(
                self._on_refresh_error,
                _("API Error"),
                _("Failed to fetch streamer data: {}").format(str(e)[:100]),
            )

    def _on_refresh_complete(self, online, offline, info):
        """Callback from background thread: update UI with fresh stream data."""
        self.refresh_button.set_sensitive(True)
        self.show_toast(_("Stream data refreshed"))
        self.update_action_rows(online, offline, info)

    def _on_refresh_error(self, heading, message):
        """Callback from background thread: show error and re-enable refresh."""
        self.refresh_button.set_sensitive(True)
        # Expire cache cooldown so next manual refresh attempt is not blocked
        self.twitch._invalidate_streams_cache()
        self._show_error_dialog(heading, message)

    def update_action_rows(self, online_streamers, offline_streamers, streamer_info):
        """Update the online and offline streamer lists."""
        self.row_manager.update_rows(online_streamers, offline_streamers, streamer_info)

    def create_row(self, streamer, info):
        """Create an ActionRow with buttons and additional info."""
        return self.row_manager.create_row(streamer, info)

    def open_stream_in_browser(self, streamer):
        """Open the Twitch stream page in default browser."""
        url = f"https://twitch.tv/{streamer}"
        Gtk.show_uri(parent=self, uri=url, timestamp=0)

    def _load_theme_css(self, resource_path):
        """Load a custom CSS resource file as a theme provider.

        Args:
            resource_path: GResource path like
                          '/io/github/jfsen/Streamline/css/bronze.css'
        """
        provider = Gtk.CssProvider()
        provider.load_from_resource(resource_path)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._theme_css_provider = provider

    def _apply_theme(self):
        """Apply the current theme, including custom CSS themes if selected."""
        logger.debug("Applying theme: %s", self.theme)
        # Remove any previously applied custom theme CSS
        if self._theme_css_provider is not None:
            Gtk.StyleContext.remove_provider_for_display(
                self.get_display(), self._theme_css_provider
            )
            self._theme_css_provider = None

        _RESOURCE_BASE = "/io/github/jfsen/Streamline/css"
        THEME_CSS = {
            "bronze": f"{_RESOURCE_BASE}/bronze.css",
            "anthracite": f"{_RESOURCE_BASE}/anthracite.css",
            "red": f"{_RESOURCE_BASE}/red.css",
        }

        if self.theme in THEME_CSS:
            self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            self._load_theme_css(THEME_CSS[self.theme])
        elif self.theme == "light":
            self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif self.theme == "dark":
            self.style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            self.style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

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
        self.save_config()
        self.remove_streamer_row(streamer)

    def follow_streamer(self, *args):
        """Show dialog to follow new streamer."""
        self.dialogs.show_follow_dialog(self._handle_follow)

    def _handle_follow(self, username):
        """Handle follow dialog callback with support for multiple streamers."""
        # Split input by commas and strip whitespace
        usernames = [name.strip().lower() for name in username.split(",")]

        # Track newly added streamers
        added = []
        already_following = []

        # Convert all existing streamers to lowercase for comparison
        existing_streamers_lower = [s.lower() for s in self.all_streamers]

        for name in usernames:
            if not name:  # Skip empty names
                continue

            if name not in existing_streamers_lower:
                # Always add as lowercase
                self.all_streamers.append(name)
                self.add_offline_streamer(name)
                added.append(name)
            else:
                # Find the original case version for display in the message
                original_index = existing_streamers_lower.index(name)
                already_following.append(self.all_streamers[original_index])

        if added:
            self.save_config()
            if len(added) == 1:
                self.show_toast(_("Now following {}").format(added[0]))
            else:
                self.show_toast(_("Added {} new streamers").format(len(added)))

        if already_following:
            if len(already_following) == 1:
                self.show_toast(_("Already following {}").format(already_following[0]))
            else:
                self.show_toast(
                    _("Already following: {}").format(", ".join(already_following))
                )

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
        """Save current configuration to file."""
        config = self.config_manager.create_config_dict(self)
        if not self.config_manager.save(config):
            self.dialogs.show_error_dialog(
                _("Error Saving Config"), _("Could not save configuration")
            )

    def show_preferences(self, *args):
        """Show preferences dialog."""
        if not self._preferences:
            prefs = StreamlinePreferences(self)
            prefs.connect("closed", lambda w: setattr(self, "_preferences", None))
            self._preferences = prefs

        self._preferences.present(self)

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
        stream_section.add(
            Adw.ShortcutsItem.new(_("Follow New Streamer"), "<primary>n")
        )
        stream_section.add(Adw.ShortcutsItem.new(_("Quick Play Stream"), "<primary>p"))
        stream_section.add(Adw.ShortcutsItem.new(_("Refresh Streams"), "<primary>r"))
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

    def add_offline_streamer(self, username):
        """Add new offline streamer row."""
        return self.row_manager.add_new_streamer(username)

    def remove_streamer_row(self, username):
        """Remove streamer row from UI."""
        self.row_manager.remove_streamer_row(username)

    def show_vods_page(self, streamer):
        """Show VODs page for the given streamer."""
        logger.debug("Opening VOD page for %s", streamer)
        page = VODPage(self, streamer, self.twitch, self.player)
        # Store weak reference to track the current VOD page
        from weakref import proxy

        self._current_vod_page = proxy(page)
        self.navigation_view.push(page)

    def _cleanup_vod_page(self, page):
        """Clean up VOD page references"""
        if hasattr(self, "_current_vod_page"):
            delattr(self, "_current_vod_page")

    def _create_input_dialog(self, heading, body, default_response="ok"):
        """Create reusable input dialog"""
        return self.dialogs.create_input_dialog(heading, body, default_response)
