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

import os
import subprocess
import json
import requests
import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
gi.require_version('WebKit', '6.0')
from gi.repository import Adw
from gi.repository import Gtk
from gi.repository import Pango
from gi.repository import GLib
from gi.repository import WebKit
from pathlib import Path
from datetime import datetime, timezone

from .preferences import StreamlinePreferences
from .twitch import TwitchAPI
from .stream_player import StreamPlayer
from .vod_page import VODPage
from .icon_names import IconNames
from .config import ConfigManager
from .dialogs import StreamlineDialogs
from .rows import StreamerRowManager
from .chat_page import ChatPage


@Gtk.Template(resource_path='/io/github/jfsen/Streamline/window.ui')
class StreamlineWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'StreamlineWindow'

    preferences_page = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()
    online_group = Gtk.Template.Child()
    offline_group = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    quick_play_button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Initialize config manager first
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()
        
        # Initialize window size attribute
        self.narrow_mode = self.config.get("narrow_mode", False)
        
        # Set initial window size based on preference
        if self.narrow_mode:
            self.set_default_size(360, 600)
        
        # Initialize other managers
        self.dialogs = StreamlineDialogs(self)
        
        # Load remaining config values
        self._initialize_from_config(self.config)

        # Create ListBoxes for online and offline streamers
        self.online_list = Gtk.ListBox()
        self.online_list.add_css_class("boxed-list")
        self.online_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.online_group.add(self.online_list)
        
        self.offline_list = Gtk.ListBox()
        self.offline_list.add_css_class("boxed-list")
        self.offline_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.offline_group.add(self.offline_list)

        # Now initialize row manager after lists are created
        self.row_manager = StreamerRowManager(self)
        
        # Initialize API-related attributes
        self.twitch = None
        
        # Check credentials and initialize API
        if not self.client_id or not self.client_secret:
            self._show_error_dialog(
                "Missing Credentials",
                "Twitch API credentials not found. Please add them in preferences."
            )
        else:
            try:
                self.twitch = TwitchAPI(self.client_id, self.client_secret)
            except Exception as e:
                self._show_error_dialog(
                    "API Error",
                    "Failed to initialize Twitch API. Please check your credentials."
                )
        
        # Initialize StreamPlayer
        self.player = StreamPlayer(self)
        
        # Dictionary to store row references
        self.streamer_rows = {}
        
        # Load config
        config = self.load_config()

        # Set player config
        self.player_type = config.get("player_type", "mpv")
        self.custom_player_path = config.get("custom_player_path", "")

        # Set paths from config
        self.streamlink_path = config.get("streamlink_path", "/usr/bin/streamlink")
        self.mpv_path = config.get("mpv_path", "/usr/bin/mpv")
        self.vlc_path = config.get("vlc_path", "/usr/bin/vlc")

        # Add stream quality setting
        self.stream_quality = config.get("stream_quality", "best")

        # Fetch streamer list from config
        self.all_streamers = config.get("streamers", [])

        # Get initial streamer data
        online_streamers, offline_streamers, streamer_info = self.get_streamers()
        self.update_action_rows(online_streamers, offline_streamers, streamer_info)

        # Connect headerbar buttons
        self.refresh_button.connect("clicked", self.on_refresh_button_clicked)
        self.quick_play_button.connect("clicked", self.quick_play)

        # Add navigation view
        self.navigation_view = Adw.NavigationView()
        self.main_content = self.get_content()
        self.set_content(self.navigation_view)

        # Add main page
        self.main_page = Adw.NavigationPage(
            title="Streamline",
            child=self.main_content
        )
        self.navigation_view.add(self.main_page)
        self._preferences = None

    def _initialize_from_config(self, config):
        """Initialize all attributes from config in one pass"""
        self.client_id = config.get("twitch_client_id", "")
        self.client_secret = config.get("twitch_client_secret", "")
        self.player_type = config.get("player_type", "mpv")
        self.custom_player_path = config.get("custom_player_path", "")
        self.streamlink_path = config.get("streamlink_path", "/usr/bin/streamlink")
        self.mpv_path = config.get("mpv_path", "/usr/bin/mpv")
        self.vlc_path = config.get("vlc_path", "/usr/bin/vlc")
        self.all_streamers = config.get("streamers", [])
        self.stream_quality = config.get("stream_quality", "best")
        self.narrow_mode = config.get("narrow_mode", False)

    def on_refresh_button_clicked(self, button):
        """Refresh streamer data."""
        if not self.twitch:
            self.show_toast("API not available")
            return

        # Check cache status
        cached_data, seconds_until_refresh = self.twitch._load_streams_cache()
        
        if cached_data is not None:
            # Data is still cached, inform user how long until refresh is available
            self.show_toast(f"Please wait {seconds_until_refresh}s before refreshing again")
            return
            
        # Cache is expired, do refresh
        self.show_toast("Stream data refreshed")
        online_streamers, offline_streamers, streamer_info = self.get_streamers()
        self.update_action_rows(online_streamers, offline_streamers, streamer_info)

    def get_streamers(self):
        """Get streamer information with error handling."""
        if not self.twitch:
            # Don't show error dialog here - just return empty results
            # The update_action_rows method will show a toast instead
            return [], self.all_streamers, {}
            
        try:
            return self.twitch.get_streams(self.all_streamers)
        except requests.ConnectionError:
            # Only show error dialog if it's a new connection error
            self._show_error_dialog(
                "Connection Error",
                "Could not fetch streamer data. Please check your internet connection."
            )
            return [], self.all_streamers, {}
        except Exception as e:
            # Only show error for unexpected errors
            self._show_error_dialog(
                "API Error",
                "Failed to fetch streamer data. Please try again later."
            )
            return [], self.all_streamers, {}

    def update_action_rows(self, online_streamers, offline_streamers, streamer_info):
        """Update the online and offline streamer lists."""
        if not self.twitch:
            self.show_toast("API not available - showing all streamers as offline", 4)
        
        self.row_manager.update_rows(online_streamers, offline_streamers, streamer_info)

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
        self.save_config()
        self.remove_streamer_row(streamer)

    def follow_streamer(self, *args):
        """Show dialog to follow new streamer."""
        self.dialogs.show_follow_dialog(self._handle_follow)

    def _handle_follow(self, username):
        """Handle follow dialog callback."""
        if username not in self.all_streamers:
            self.all_streamers.append(username)
            self.save_config()
            self.add_offline_streamer(username)
        else:
            self.dialogs.show_already_following_dialog(username)

    def quick_play(self, *args):
        """Show dialog to quickly play a stream."""
        self.dialogs.show_quick_play_dialog(self._handle_quick_play)

    def _handle_quick_play(self, username):
        """Handle quick play dialog callback."""
        try:
            if self.player.play_content(f"twitch.tv/{username}", is_vod=False):
                self.show_toast("Playback starting...", 2)
        except Exception as e:
            self.show_toast(f"Error: {str(e)}", 4)

    def load_config(self):
        """Load configuration from file."""
        return self.config_manager.load()

    def save_config(self):
        """Save current configuration to file."""
        config = self.config_manager.create_config_dict(self)
        if not self.config_manager.save(config):
            self.dialogs.show_error_dialog(
                "Error Saving Config",
                "Could not save configuration"
            )

    def show_preferences(self, *args):
        """Show preferences window."""
        if not self._preferences:
            prefs = StreamlinePreferences(self)
            prefs.connect('destroy', lambda w: setattr(self, '_preferences', None))
            self._preferences = prefs
            
        self._preferences.present()

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
        return self.row_manager.add_offline_streamer(username)

    def remove_streamer_row(self, username):
        """Remove streamer row from UI."""
        self.row_manager.remove_streamer_row(username)

    def show_vods_page(self, streamer):
        """Show VODs page for the given streamer."""
        page = VODPage(self, streamer, self.twitch, self.player)
        # Store weak reference to track the current VOD page
        from weakref import proxy
        self._current_vod_page = proxy(page)
        self.navigation_view.push(page)

    def _cleanup_vod_page(self, page):
        """Clean up VOD page references"""
        if hasattr(self, '_current_vod_page'):
            delattr(self, '_current_vod_page')

    def _create_input_dialog(self, heading, body, default_response="ok"):
        """Create reusable input dialog"""
        return self.dialogs.create_input_dialog(heading, body, default_response)

    def show_chat_page(self, streamer):
        """Show chat page for the given streamer."""
        page = ChatPage(streamer)
        self.navigation_view.push(page)