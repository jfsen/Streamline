# main.py
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
import sys

import gi

_ = gettext.gettext

# Suppress noisy third-party loggers (keeps credentials out of console)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk

from .cli import CliHandler
from .window import StreamlineWindow


class StreamlineApplication(Adw.Application):
    """The main application singleton class."""

    def __init__(self, version):
        super().__init__(
            application_id="org.jfsen.Streamline",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._version = version
        self._cli = CliHandler(version)

    def do_startup(self):
        """Called when application is starting up."""
        Adw.Application.do_startup(self)
        Adw.init()

        # Add actions with accelerators
        self.create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self.create_action("about", self.on_about_action)
        self.create_action(
            "preferences", self.on_preferences_action, ["<primary>comma"]
        )

        # Add shortcuts action
        self.create_action("shortcuts", self.on_shortcuts_action, ["<primary>question"])

        # Add follow, quick play and refresh actions
        self.create_action("follow", self.on_follow_action, ["<primary>n"])
        self.create_action("quick-play", self.on_quick_play_action, ["<primary>p"])
        self.create_action("refresh", self.on_refresh_action, ["<primary>r", "F5"])

    def do_activate(self):
        """Called when the application is activated."""
        win = self.props.active_window
        if not win:
            win = StreamlineWindow(application=self)
        win.present()

    def on_about_action(self, *args):
        """Callback for the app.about action."""
        about = Adw.AboutDialog(
            application_name="Streamline",
            application_icon="org.jfsen.Streamline",
            developer_name="jfsen",
            version=self._version,
            developers=["jfsen"],
            copyright="© 2025 jfsen",
            license_type=Gtk.License.GPL_3_0,
        )
        # Translators: Replace "translator-credits" with your name/username, and optionally an email or URL.
        about.set_translator_credits(_("translator-credits"))
        about.present(self.props.active_window)

    def on_preferences_action(self, *args):
        """Show preferences dialog."""
        win = self.props.active_window
        if isinstance(win, StreamlineWindow):
            win.show_preferences()

    def on_shortcuts_action(self, *args):
        """Show keyboard shortcuts dialog."""
        win = self.props.active_window
        if isinstance(win, StreamlineWindow):
            win.show_shortcuts()

    def on_follow_action(self, *args):
        """Show follow dialog."""
        win = self.props.active_window
        if isinstance(win, StreamlineWindow):
            win.follow_streamer()

    def on_quick_play_action(self, *args):
        """Show quick play dialog."""
        win = self.props.active_window
        if isinstance(win, StreamlineWindow):
            win.quick_play()

    def on_refresh_action(self, *args):
        """Refresh streamer data."""
        win = self.props.active_window
        if isinstance(win, StreamlineWindow):
            win.on_refresh_button_clicked(None)

    def do_command_line(self, command_line):
        """Handle CLI arguments before activating the GUI."""
        argv = command_line.get_arguments()

        # Detect the positional shortcut: streamline <username>
        # Known subcommands and flags should not be treated as usernames.
        known_commands = {"play", "follow", "unfollow", "status"}
        if len(argv) >= 2 and not argv[1].startswith("-"):
            if argv[1] not in known_commands:
                # Rewrite: streamline shroud → streamline play shroud
                argv = [argv[0], "play"] + argv[1:]

        result = self._cli.parse_and_handle(argv)

        if result is not None:
            # CLI handled the request — exit without opening the GUI.
            return result

        # No CLI command; open the GUI as normal.
        self.activate()
        return 0

    def create_action(self, name, callback, shortcuts=None):
        """Add an application action.

        Args:
            name: the name of the action
            callback: the function to be called when the action is
              activated
            shortcuts: an optional list of accelerators
        """
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version):
    """The application's entry point.

    Args:
        version: Version string from the build system (meson @VERSION@).
    """
    app = StreamlineApplication(version)
    return app.run(sys.argv)
