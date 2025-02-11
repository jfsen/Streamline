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
import subprocess
import os

@Gtk.Template(resource_path='/io/github/jfsen/Streamline/window.ui')
class StreamlineWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'StreamlineWindow'

    preferences_page = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()
    online_group = Gtk.Template.Child()
    offline_group = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        # Find streamlink and mpv paths
        try:
            # Try /usr/bin first (common on Arch)
            if os.path.exists('/usr/bin/streamlink') and os.path.exists('/usr/bin/mpv'):
                self.streamlink_path = '/usr/bin/streamlink'
                self.mpv_path = '/usr/bin/mpv'
            else:
                # Fall back to which
                self.streamlink_path = subprocess.check_output(['which', 'streamlink']).decode().strip()
                self.mpv_path = subprocess.check_output(['which', 'mpv']).decode().strip()
        except subprocess.SubprocessError:
            # Fall back to common locations
            self.streamlink_path = '/usr/bin/streamlink'
            self.mpv_path = '/usr/bin/mpv'
        
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
        # Get initial streamers
        online_streamers, offline_streamers = self.get_streamers()
        self.update_action_rows(online_streamers, offline_streamers)

    def on_refresh_button_clicked(self, button):
        # Replace this with the actual function that returns the lists of online and offline streamers
        online_streamers, offline_streamers = self.get_streamers()
        self.update_action_rows(online_streamers, offline_streamers)

    def get_streamers(self):
        """Generate random streamer names for testing."""
        import random
        
        # Generate 2-4 online streamers
        num_online = random.randint(2, 4)
        online_streamers = [f"Streamer{random.randint(1, 100)}" for _ in range(num_online)]
        
        # Generate 1-3 offline streamers
        num_offline = random.randint(1, 3)
        offline_streamers = [f"Streamer{random.randint(1, 100)}" for _ in range(num_offline)]
        
        return online_streamers, offline_streamers

    def update_action_rows(self, online_streamers, offline_streamers):
        """Update the online and offline streamer lists."""
        # Remove all rows from ListBoxes
        while self.online_list.get_last_child():
            self.online_list.remove(self.online_list.get_last_child())
        while self.offline_list.get_last_child():
            self.offline_list.remove(self.offline_list.get_last_child())

        # Add new streamer rows
        for streamer in online_streamers:
            row = self.create_row(streamer)
            self.online_list.append(row)
        
        for streamer in offline_streamers:
            row = self.create_row(streamer)
            self.offline_list.append(row)

    def create_row(self, streamer):
        """Create an ActionRow with buttons for a streamer."""
        row = Adw.ActionRow.new()
        row.set_title(streamer)
        row.set_activatable(True)

        # Create action buttons with tooltips and handlers
        buttons = [
            ("media-playback-start-symbolic", "Play stream"),
            ("edit-delete-symbolic", "Unfollow"),
            ("web-browser-symbolic", "Open in browser"),
            ("video-display-symbolic", "Show VODs")
        ]

        # Add buttons to row
        for icon_name, tooltip in buttons:
            button = Gtk.Button(icon_name=icon_name)
            button.add_css_class("flat")
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text(tooltip)
            
            # Add click handlers for buttons
            if tooltip == "Open in browser":
                button.connect("clicked", lambda btn, s=streamer: 
                             self.open_stream_in_browser(s))
            elif tooltip == "Play stream":
                button.connect("clicked", lambda btn, s=streamer:
                             self.play_stream(s))
            
            row.add_suffix(button)

        return row

    def open_stream_in_browser(self, streamer):
        """Open the Twitch stream page in default browser."""
        url = f"https://twitch.tv/{streamer}"
        Gtk.show_uri(parent=self, uri=url, timestamp=0)

    def play_stream(self, streamer):
        """Open the stream using streamlink."""
        try:
            # Check if paths exist before trying to use them
            if not (os.path.exists(self.streamlink_path) and os.path.exists(self.mpv_path)):
                raise FileNotFoundError
                
            subprocess.Popen([
                self.streamlink_path,
                f"twitch.tv/{streamer}",
                "best",
                f"--player={self.mpv_path}"
            ])
        except FileNotFoundError:
            message = (
                "Could not find streamlink or mpv.\n"
                "Please make sure they are installed:\n\n"
                "sudo pacman -S streamlink mpv"
            )
            error_dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Error",
                body=message
            )
            error_dialog.add_response("ok", "OK")
            error_dialog.present()
        except subprocess.SubprocessError as e:
            error_dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Error",
                body=str(e)
            )
            error_dialog.add_response("ok", "OK")
            error_dialog.present()
