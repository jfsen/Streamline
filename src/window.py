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
from gi.repository import Adw
from gi.repository import Gtk
from gi.repository import Pango
from gi.repository import GLib
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
        
        # Initialize managers
        self.config_manager = ConfigManager()
        self.dialogs = StreamlineDialogs(self)
        self.row_manager = StreamerRowManager(self)
        
        # Load config once and store all values
        self.config = self.config_manager.load()
        self._initialize_from_config(self.config)
        
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
        self.vlc_path = config.get("vlc_path", "/usr/bin/vlc")  # Add VLC path
        self.all_streamers = config.get("streamers", [])

        # Add stream quality setting
        self.stream_quality = config.get("stream_quality", "best")

        # Create ListBoxes for online and offline streamers
        self.online_list = Gtk.ListBox()
        self.online_list.add_css_class("boxed-list")
        self.online_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.online_group.add(self.online_list)
        
        self.offline_list = Gtk.ListBox()
        self.offline_list.add_css_class("boxed-list")
        self.offline_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.offline_group.add(self.offline_list)
        
        self.refresh_button.connect("clicked", self.on_refresh_button_clicked)
        self.quick_play_button.connect("clicked", self.show_quick_play_dialog)
        # Get initial streamers
        online_streamers, offline_streamers, streamer_info = self.get_streamers()
        self.update_action_rows(online_streamers, offline_streamers, streamer_info)

        # Add navigation view
        self.navigation_view = Adw.NavigationView()
        self.main_content = self.get_content()  # Save current content
        self.set_content(self.navigation_view)

        # Add main page
        self.main_page = Adw.NavigationPage(
            title="Streamline",
            child=self.main_content
        )
        self.navigation_view.add(self.main_page)
        self._executable_cache = {}
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
        self.show_toast("Refreshing streamer data...")
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
        # Show warning if API is not working
        if not self.twitch:
            self.show_toast("API not available - showing all streamers as offline", 4)
        
        # Clear existing rows
        def clear_list(list_box):
            while row := list_box.get_first_child():
                list_box.remove(row)
                
        clear_list(self.online_list)
        clear_list(self.offline_list)

        # Sort streamers alphabetically (case-insensitive)
        online_streamers.sort(key=str.lower)
        offline_streamers.sort(key=str.lower)

        # Add new streamer rows and store references
        for streamer in online_streamers:
            row = self.create_row(streamer, streamer_info.get(streamer, {}))
            self.online_list.append(row)
            self.streamer_rows[streamer] = row
        
        for streamer in offline_streamers:
            row = self.create_row(streamer, {})
            self.offline_list.append(row)
            self.streamer_rows[streamer] = row

    def create_row(self, streamer, info):
        """Create an ActionRow with buttons and additional info."""
        return self.row_manager.create_row(streamer, info)

    def open_stream_in_browser(self, streamer):
        """Open the Twitch stream page in default browser."""
        url = f"https://twitch.tv/{streamer}"
        Gtk.show_uri(parent=self, uri=url, timestamp=0)

    def _get_required_executables(self):
        """Get paths for streamlink and selected player."""
        # Find streamlink
        streamlink_cmd = self._find_executable('streamlink')
        if not streamlink_cmd:
            raise FileNotFoundError("Could not find streamlink")

        # Get player command based on preferences
        if self.player_type == "mpv":
            player_cmd = self._find_executable('mpv')
        elif self.player_type == "vlc":
            player_cmd = self._find_executable('vlc')
        else:  # custom
            player_cmd = self.custom_player_path

        if not player_cmd:
            raise FileNotFoundError(f"Could not find player: {self.player_type}")
            
        return streamlink_cmd, player_cmd

    def _find_executable(self, name):
        """Find executable with caching"""
        if name in self._executable_cache:
            return self._executable_cache[name]
            
        # Check if running in flatpak
        if os.path.exists('/.flatpak-info'):
            # Try host system path first
            try:
                result = subprocess.run(
                    ['flatpak-spawn', '--host', 'which', name],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    self._executable_cache[name] = result.stdout.strip()
                    return result.stdout.strip()
            except subprocess.SubprocessError:
                pass
        
        # Try direct path
        paths = [
            f"/usr/bin/{name}",
            f"/usr/local/bin/{name}",
            f"/app/bin/{name}",
            f"{str(Path.home())}/.local/bin/{name}"
        ]
        
        for path in paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                self._executable_cache[name] = path
                return path
                
        self._executable_cache[name] = None
        return None

    def _show_error_dialog(self, heading, message):
        """Show error dialog with the given heading and message."""
        self.dialogs.show_error_dialog(heading, message)

    def unfollow_streamer(self, streamer):
        """Remove a streamer from the list."""
        if streamer in self.all_streamers:
            self.dialogs.show_unfollow_dialog(streamer, self._on_unfollow_response)

    def _on_unfollow_response(self, dialog, response, streamer):
        """Handle unfollow dialog response."""
        if response == "unfollow":
            self.all_streamers.remove(streamer)
            self.save_config()
            self.remove_streamer_row(streamer)

    def show_follow_dialog(self, *args):
        """Show dialog to follow new streamer."""
        dialog, entry = self._create_input_dialog(
            heading="Follow Streamer",
            body="Enter the Twitch username of the streamer you want to follow:",
            default_response="follow"
        )

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("follow", "Follow")
        dialog.set_response_appearance("follow", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("follow")

        dialog.connect("response", self._on_follow_response, entry)
        dialog.present()
        entry.grab_focus()

    def _on_follow_response(self, dialog, response, entry):
        """Handle follow dialog response."""
        if response == "follow":
            username = entry.get_text().strip()
            if username:
                if username not in self.all_streamers:
                    self.all_streamers.append(username)
                    self.save_config()
                    self.add_offline_streamer(username)
                else:
                    self.dialogs.show_already_following_dialog(username)
        dialog.close()

    def show_quick_play_dialog(self, *args):
        """Show dialog to quickly play a stream."""
        dialog, entry = self._create_input_dialog(
            heading="Quick Play Stream",
            body="Enter the Twitch username of the streamer:",
            default_response="play"
        )

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("play", "Play")
        dialog.set_response_appearance("play", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("play")

        dialog.connect("response", self._on_quick_play_response, entry)
        dialog.present()
        entry.grab_focus()

    def _on_quick_play_response(self, dialog, response, entry):
        """Handle quick play dialog response."""
        if response == "play":
            username = entry.get_text().strip()
            if username:
                self.play_stream(username)
        dialog.close()

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

    def add_offline_streamer(self, username):
        """Create and add row for new offline streamer."""
        row = self.create_row(username, {})
        self.offline_list.append(row)
        self.streamer_rows[username] = row
        return row

    def remove_streamer_row(self, username):
        """Remove streamer row from UI."""
        if username in self.row_manager.streamer_rows:
            row = self.row_manager.streamer_rows[username]
            parent = row.get_parent()
            if parent:
                parent.remove(row)
            del self.row_manager.streamer_rows[username]

    def play_stream(self, streamer):
        """Play a stream for the given streamer."""
        self.player.play_stream(streamer)

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