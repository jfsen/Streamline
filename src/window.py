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

import gi
gi.require_version('Adw', '1')
gi.require_version('Gtk', '4.0')
from gi.repository import Adw
from gi.repository import Gtk
from gi.repository import GLib
from gi.repository import Pango
import subprocess
import os
import json
from pathlib import Path
from .preferences import StreamlinePreferences

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
        online_streamers, offline_streamers = self.get_streamers()
        self.update_action_rows(online_streamers, offline_streamers)

    def on_refresh_button_clicked(self, button):
        # Replace this with the actual function that returns the lists of online and offline streamers
        online_streamers, offline_streamers = self.get_streamers()
        self.update_action_rows(online_streamers, offline_streamers)

    def get_streamers(self):
        """Get list of streamers with all set to offline by default."""
        # Return all streamers as offline for now
        return [], self.all_streamers.copy()

    def update_action_rows(self, online_streamers, offline_streamers):
        """Update the online and offline streamer lists."""
        # Remove all rows from ListBoxes
        while self.online_list.get_last_child():
            self.online_list.remove(self.online_list.get_last_child())
        while self.offline_list.get_last_child():
            self.offline_list.remove(self.offline_list.get_last_child())

        # Sort streamers alphabetically (case-insensitive)
        online_streamers.sort(key=str.lower)
        offline_streamers.sort(key=str.lower)

        # Add new streamer rows
        for streamer in online_streamers:
            row = self.create_row(streamer)
            self.online_list.append(row)
        
        for streamer in offline_streamers:
            row = self.create_row(streamer)
            self.offline_list.append(row)

    def create_row(self, streamer):
        """Create an ExpanderRow with buttons and additional info."""
        row = Adw.ExpanderRow.new()
        row.set_title(streamer)
        row.set_title_lines(1)  # Prevent line wrapping for title

        # Create action buttons with tooltips and handlers
        buttons = [
            ("web-browser-symbolic", "Open in browser"),
            ("edit-delete-symbolic", "Unfollow"),
            ("video-display-symbolic", "Show VODs")
        ]

        # Create play button separately to add as prefix
        play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        play_button.add_css_class("flat")
        play_button.set_valign(Gtk.Align.CENTER)
        play_button.set_tooltip_text("Play stream")
        play_button.connect("clicked", lambda btn, s=streamer: self.play_stream(s))
        row.add_prefix(play_button)

        # Add remaining buttons as suffixes
        for icon_name, tooltip in buttons:
            button = Gtk.Button(icon_name=icon_name)
            button.add_css_class("flat")
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text(tooltip)
            
            # Add click handlers for buttons
            if tooltip == "Open in browser":
                button.connect("clicked", lambda btn, s=streamer: 
                             self.open_stream_in_browser(s))
            elif tooltip == "Unfollow":
                button.connect("clicked", lambda btn, s=streamer:
                             self.unfollow_streamer(s))
            
            row.add_suffix(button)

        # Add expanded content
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        info_box.add_css_class("boxed-list")
        info_box.set_margin_start(6)
        info_box.set_margin_end(6)
        info_box.set_margin_top(6)
        info_box.set_margin_bottom(6)

        # Add some example info (you can customize this)
        info_labels = [
            "Game: Just Chatting",
            "Title: Stream title goes hereeeeeeeeee eeeeeee eeeeee eeeeeeeee eeeeeeeeee eeeee eee eeeeeeeee eeeeeeeeeeeeeee eeeeee eeeee eeeeeeee eeeeeee eeeeeeeee eeeeee",
            "Viewers: 1,234",
            "Uptime: 2h 30m"
        ]

        for info in info_labels:
            label = Gtk.Label(label=info, xalign=0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_tooltip_text(info)
            info_box.append(label)

        row.add_row(info_box)

        return row

    def open_stream_in_browser(self, streamer):
        """Open the Twitch stream page in default browser."""
        url = f"https://twitch.tv/{streamer}"
        Gtk.show_uri(parent=self, uri=url, timestamp=0)

    def play_stream(self, streamer):
        """Open the stream using streamlink."""
        try:
            # Get streamlink and player commands
            streamlink_cmd, player_cmd = self._get_required_executables()
            
            # Show the toast BEFORE starting streamlink
            self.show_toast(f"Checking stream: {streamer}")

            # Build command with or without flatpak-spawn
            if os.path.exists('/.flatpak-info'):
                cmd = ['flatpak-spawn', '--host']
            else:
                cmd = []
                
            cmd.extend([
                streamlink_cmd,
                f"twitch.tv/{streamer}",
                self.stream_quality,
                '--twitch-disable-ads',
                f'--player={player_cmd}'
            ])

            # Run streamlink and capture initial output for error checking
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True
            )

            # Check for immediate errors (wait briefly)
            try:
                stdout, stderr = process.communicate(timeout=2)
                if process.returncode != None and process.returncode != 0:
                    if "error: No playable streams found" in stderr:
                        raise subprocess.SubprocessError(f"Stream is offline: {streamer}")
                    else:
                        raise subprocess.SubprocessError(stderr.strip())
                else:
                    # Stream likely starting successfully
                    self.show_toast(f"Starting stream: {streamer}")
            except subprocess.TimeoutExpired:
                # No error within timeout, assume stream is starting
                self.show_toast(f"Starting stream: {streamer}")
                # Detach the process
                process.stdout.close()
                process.stderr.close()

        except FileNotFoundError:
            message = (
                "Could not find streamlink or mpv.\n"
                "Please make sure they are installed on your system:\n\n"
                "For Arch Linux:\n"
                "   sudo pacman -S streamlink mpv\n\n"
                "For Ubuntu/Debian:\n"
                "   sudo apt install streamlink mpv\n\n"
                "For Fedora:\n"
                "   sudo dnf install streamlink mpv"
            )
            self._show_error_dialog("Missing Dependencies", message)
        except subprocess.SubprocessError as e:
            self.show_toast(f"Error: {str(e)}", 3)

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
        """Find executable in various locations."""
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
                return path
                
        return None

    def _show_error_dialog(self, heading, message):
        """Show error dialog with the given heading and message."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=heading,
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def unfollow_streamer(self, streamer):
        """Remove a streamer from the list."""
        if streamer in self.all_streamers:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading=f"Unfollow {streamer}?",
                body="Are you sure you want to unfollow this streamer?"
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("unfollow", "Unfollow")
            dialog.set_response_appearance("unfollow", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", self._on_unfollow_response, streamer)
            dialog.present()

    def _on_unfollow_response(self, dialog, response, streamer):
        """Handle unfollow dialog response."""
        if response == "unfollow":
            self.all_streamers.remove(streamer)
            self.save_config()
            # Refresh the lists
            online_streamers, offline_streamers = self.get_streamers()
            self.update_action_rows(online_streamers, offline_streamers)

    def show_follow_dialog(self, *args):
        """Show dialog to follow new streamer."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Follow Streamer",
            body="Enter the Twitch username of the streamer you want to follow:"
        )

        # Create a box for the entry with proper spacing and width
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_margin_start(20)
        content_box.set_margin_end(20)
        content_box.set_margin_top(10)
        content_box.set_margin_bottom(10)

        # Add entry with minimum width and activate signal
        entry = Gtk.Entry()
        entry.set_width_chars(30)
        entry.set_hexpand(True)
        entry.connect("activate", lambda w: dialog.response("follow"))
        content_box.append(entry)

        dialog.set_extra_child(content_box)

        # Add dialog buttons
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("follow", "Follow")
        dialog.set_response_appearance("follow", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("follow")

        # Handle response
        dialog.connect("response", self._on_follow_response, entry)
        dialog.present()
        # Focus the entry
        entry.grab_focus()

    def _on_follow_response(self, dialog, response, entry):
        """Handle follow dialog response."""
        if response == "follow":
            username = entry.get_text().strip()
            if username:
                if username not in self.all_streamers:
                    self.all_streamers.append(username)
                    self.save_config()
                    # Refresh the lists
                    online_streamers, offline_streamers = self.get_streamers()
                    self.update_action_rows(online_streamers, offline_streamers)
                else:
                    error = Adw.MessageDialog(
                        transient_for=self,
                        heading="Already Following",
                        body=f"You are already following {username}"
                    )
                    error.add_response("ok", "OK")
                    error.present()
        dialog.close()

    def show_quick_play_dialog(self, *args):
        """Show dialog to quickly play a stream."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Quick Play Stream",
            body="Enter the Twitch username of the streamer:"
        )

        # Create a box for the entry
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_margin_start(20)
        content_box.set_margin_end(20)
        content_box.set_margin_top(10)
        content_box.set_margin_bottom(10)

        # Add entry with minimum width and activate signal
        entry = Gtk.Entry()
        entry.set_width_chars(30)
        entry.set_hexpand(True)
        entry.connect("activate", lambda w: dialog.response("play"))
        content_box.append(entry)

        dialog.set_extra_child(content_box)

        # Add dialog buttons
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("play", "Play")
        dialog.set_response_appearance("play", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("play")

        # Handle response
        dialog.connect("response", self._on_quick_play_response, entry)
        dialog.present()
        # Focus the entry
        entry.grab_focus()

    def _on_quick_play_response(self, dialog, response, entry):
        """Handle quick play dialog response."""
        if response == "play":
            username = entry.get_text().strip()
            if username:
                self.play_stream(username)
        dialog.close()

    def get_config_path(self):
        """Get the path to the config file."""
        config_dir = Path.home() / ".config" / "Streamline"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"

    def load_config(self):
        """Load configuration from file."""
        config_path = self.get_config_path()
        default_config = {
            "streamers": [],
            "streamlink_path": "/usr/bin/streamlink",
            "mpv_path": "/usr/bin/mpv",
            "vlc_path": "/usr/bin/vlc",  # Add VLC path
            "player_type": "mpv",
            "custom_player_path": "",
            "stream_quality": "best",  # Add default quality
        }

        try:
            if config_path.exists():
                with open(config_path, 'r') as f:
                    return json.load(f)
            else:
                # Create default config file
                with open(config_path, 'w') as f:
                    json.dump(default_config, f, indent=4)
                return default_config
        except (json.JSONDecodeError, OSError):
            return default_config

    def save_config(self):
        """Save current configuration to file."""
        config = {
            "streamers": self.all_streamers,
            "streamlink_path": self.streamlink_path,
            "mpv_path": self.mpv_path,
            "vlc_path": self.vlc_path,  # Add VLC path
            "player_type": self.player_type,
            "custom_player_path": self.custom_player_path,
            "stream_quality": self.stream_quality,  # Add quality to saved config
        }

        try:
            with open(self.get_config_path(), 'w') as f:
                json.dump(config, f, indent=4)
        except OSError as e:
            error_dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Error Saving Config",
                body=f"Could not save configuration: {str(e)}"
            )
            error_dialog.add_response("ok", "OK")
            error_dialog.present()

    def show_preferences(self, *args):
        """Show the preferences window."""
        prefs = StreamlinePreferences(self)
        prefs.present()

    def show_toast(self, text, timeout=2):
        """Show a toast notification."""
        toast = Adw.Toast.new(text)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)
