# cli.py
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

"""CLI command handlers for Streamline.

Provides headless play, follow/unfollow, and status commands so the
application can be scripted and used from the terminal.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib

from .config import (
    QUALITY_PRESETS,
    TWITCH_CLIENT_ID,
    TWITCH_CLIENT_SECRET,
)
from .twitch import TwitchAPI

# Cache directory (shared with TwitchAPI._CACHE_DIR).
_CACHE_DIR = Path(GLib.get_user_cache_dir()) / "Streamline"

# Detects whether the app is running inside a Flatpak sandbox.
IS_FLATPAK = os.path.exists("/.flatpak-info")

# Base command used to invoke streamlink.
STREAMLINK_CMD = ["streamlink"]


class CliHandler:
    """Parses CLI arguments and dispatches to the appropriate subcommand handler.

    Each handler returns an exit code (0 = success, non-zero = failure).
    """

    def __init__(self, version):
        self.version = version
        self.settings = Gio.Settings.new("org.jfsen.Streamline")

    # ── Argument parsing ──────────────────────────────────────

    def parse_and_handle(self, argv):
        """Parse CLI args and dispatch to the correct handler.

        Returns the process exit code.  Returns ``None`` when no CLI
        command was detected and the GUI should be shown instead.
        """
        parser = self._build_parser()
        try:
            args = parser.parse_args(argv[1:])
        except SystemExit:
            # argparse already printed the help / error message.
            return 2

        if args.command is None:
            return None

        return args.func(args)

    def _build_parser(self):
        parser = argparse.ArgumentParser(
            prog="streamline",
            description="Watch Twitch streams in your local media player.",
            epilog="Shortcut:  streamline USERNAME  (equivalent to 'streamline play USERNAME')",
        )
        parser.add_argument(
            "-V", "--version", action="version", version=f"streamline {self.version}"
        )
        parser.add_argument(
            "-d",
            "--debug",
            action="store_true",
            help="Enable debug logging (or set STREAMLINE_DEBUG=1)",
        )

        sub = parser.add_subparsers(dest="command", title="commands")

        # ── play ──
        p_play = sub.add_parser("play", help="Play a stream")
        p_play.add_argument("username", help="Twitch username to watch")
        p_play.set_defaults(func=self._handle_play)

        # ── follow ──
        p_follow = sub.add_parser("follow", help="Follow one or more streamers")
        p_follow.add_argument(
            "usernames",
            help="Comma-separated Twitch usernames (e.g. shroud,lirik)",
        )
        p_follow.set_defaults(func=self._handle_follow)

        # ── unfollow ──
        p_unfollow = sub.add_parser("unfollow", help="Unfollow one or more streamers")
        p_unfollow.add_argument(
            "usernames",
            help="Comma-separated Twitch usernames (e.g. shroud,lirik)",
        )
        p_unfollow.set_defaults(func=self._handle_unfollow)

        # ── status ──
        p_status = sub.add_parser("status", help="Show status of followed streamers")
        p_status.add_argument(
            "--json",
            action="store_true",
            dest="json_output",
            help="Output in machine-parseable JSON format",
        )
        p_status.set_defaults(func=self._handle_status)

        return parser

    # ── Command error helpers ─────────────────────────────────

    @staticmethod
    def _err(msg, code=1):
        print(msg, file=sys.stderr)
        return code

    def _get_player_settings(self):
        """Read player-related settings from GSettings."""
        return {
            "player_type": self.settings.get_string("player-type"),
            "custom_player_path": self.settings.get_string("custom-player-path"),
            "stream_quality": self.settings.get_string("stream-quality"),
            "custom_quality": self.settings.get_string("custom-quality"),
            "low_latency": self.settings.get_boolean("low-latency"),
        }

    def _find_executable(self, name):
        """Find an executable on the host system (Flatpak-aware)."""
        if IS_FLATPAK:
            try:
                result = subprocess.run(
                    ["flatpak-spawn", "--host", "which", name],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except subprocess.SubprocessError:
                pass
            return None
        return shutil.which(name)

    def _get_player_executable(self, settings):
        """Return the path to the configured media player, or None if not found."""
        if settings["player_type"] == "custom":
            path = settings["custom_player_path"]
            if not path:
                return None
            return self._find_executable(path)
        return self._find_executable(settings["player_type"])

    # ── Twitch API helper ─────────────────────────────────────

    def _init_api(self):
        """Create a TwitchAPI instance if credentials are configured."""
        if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
            return None
        try:
            return TwitchAPI(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
        except Exception:
            return None

    @staticmethod
    def _patch_streams_cache(username, remove=False):
        """Add or remove a single username in the streams cache on disk.

        Does not require a TwitchAPI instance — operates on the JSON
        file directly so lightweight CLI commands stay fast.
        """
        cache_path = _CACHE_DIR / "streams.json"
        try:
            with open(cache_path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        cache = data.get("data")
        if not cache:
            return

        offline = cache.get("offline", [])
        online = cache.get("online", {})

        if remove:
            cache["offline"] = [s for s in offline if s != username]
            if username in online:
                del online[username]
        else:
            if username not in offline and username not in online:
                offline.append(username)
                offline.sort(key=str.lower)

        try:
            with open(cache_path, "w") as f:
                json.dump(data, f, indent=4)
        except OSError:
            pass

    # ── play ──────────────────────────────────────────────────

    def _handle_play(self, args):
        username = args.username.lower().strip()
        url = f"https://www.twitch.tv/{username}"

        settings = self._get_player_settings()
        player_cmd = self._get_player_executable(settings)
        if not player_cmd:
            player = settings["player_type"]
            if player == "custom":
                path = settings["custom_player_path"]
                if path:
                    msg = f"Could not find {path} on your system."
                else:
                    msg = "No custom player executable configured."
            else:
                msg = f"Could not find {player} on your system. Please install it."
            return self._err(msg)

        # Build quality string
        if settings["stream_quality"] == "Custom":
            quality = settings["custom_quality"]
        else:
            quality = QUALITY_PRESETS[settings["stream_quality"]]

        cmd = list(STREAMLINK_CMD)

        if settings["low_latency"]:
            cmd.append("--twitch-low-latency")

        if IS_FLATPAK:
            cmd.extend(
                [
                    "--title",
                    f"Streamline - {username}",
                    "--player",
                    "flatpak-spawn",
                    "--player-args",
                    f"--host {player_cmd}",
                    url,
                    quality,
                ]
            )
        else:
            cmd.extend(
                [
                    "--title",
                    f"Streamline - {username}",
                    "--player",
                    player_cmd,
                    url,
                    quality,
                ]
            )

        print(f"Starting stream: {username} ({quality})")
        try:
            proc = subprocess.run(cmd, start_new_session=True)
            return proc.returncode
        except FileNotFoundError:
            return self._err(
                "Streamlink not found. Please install it:\n  pip install streamlink"
            )
        except KeyboardInterrupt:
            return 0

    # ── follow ────────────────────────────────────────────────

    def _handle_follow(self, args):
        api = self._init_api()
        raw = [u.strip() for u in args.usernames.split(",") if u.strip()]
        if not raw:
            return self._err("No usernames provided.")

        usernames = [u.lower() for u in raw]

        # Validate usernames against Twitch API
        valid = set()
        invalid = []
        if api:
            try:
                api.get_users(usernames)
                for u in usernames:
                    if u in api.user_cache:
                        valid.add(u)
                    else:
                        invalid.append(u)
            except Exception:
                return self._err(
                    "Could not reach the Twitch API. Check your internet connection."
                )
        else:
            return self._err(
                "Twitch API credentials not configured.\n"
                "Copy src/config_credentials.example.py to "
                "src/config_credentials.py and fill in your keys."
            )

        # Read current streamers from GSettings
        current = list(self.settings.get_strv("streamers"))
        existing = [s.lower() for s in current]
        added = []
        skipped = []

        for u in sorted(valid):
            if u.lower() in existing:
                skipped.append(u)
            else:
                current.append(u)
                added.append(u)
                existing.append(u.lower())

        self.settings.set_strv("streamers", current)

        # Update the streams cache so the new streamers show in
        # `status` immediately instead of only after the 60 s cooldown.
        if api:
            for u in added:
                api.update_streams_cache(u, add=True)

        # Report
        if added:
            print(f"Followed: {', '.join(added)}")
        if skipped:
            print(f"Already followed: {', '.join(skipped)}")
        if invalid:
            print(f"Not found on Twitch: {', '.join(invalid)}", file=sys.stderr)
        if not added and not skipped and invalid:
            return 1
        return 0

    # ── unfollow ──────────────────────────────────────────────

    def _handle_unfollow(self, args):
        raw = [u.strip() for u in args.usernames.split(",") if u.strip()]
        if not raw:
            return self._err("No usernames provided.")

        usernames = [u.lower() for u in raw]

        current = list(self.settings.get_strv("streamers"))
        lower_current = [s.lower() for s in current]

        removed = []
        not_followed = []
        remaining = []

        for s in current:
            if s.lower() in usernames:
                removed.append(s)
            else:
                remaining.append(s)

        for u in usernames:
            if u not in lower_current:
                not_followed.append(u)

        self.settings.set_strv("streamers", remaining)

        # Update the streams cache so removed streamers disappear from
        # `status` immediately instead of only after the 60 s cooldown.
        for u in removed:
            self._patch_streams_cache(u, remove=True)

        if removed:
            print(f"Unfollowed: {', '.join(removed)}")
        if not_followed:
            print(f"Not currently followed: {', '.join(not_followed)}", file=sys.stderr)
        return 0

    # ── status ────────────────────────────────────────────────

    def _handle_status(self, args):
        api = self._init_api()
        current = list(self.settings.get_strv("streamers"))

        if not current:
            if args.json_output:
                print(json.dumps({"online": [], "offline": []}))
            else:
                print("No streamers followed yet.")
            return 0

        if not api:
            return self._err(
                "Twitch API credentials not configured.\n"
                "Copy src/config_credentials.example.py to "
                "src/config_credentials.py and fill in your keys."
            )

        try:
            online_usernames, offline_usernames, streamer_info = api.get_streams(
                current
            )
        except Exception:
            return self._err(
                "Could not reach the Twitch API. Check your internet connection."
            )

        # Display names come from the user cache (persisted to disk and
        # populated by get_streams above when the cache is stale).

        if args.json_output:
            self._print_status_json(
                online_usernames, offline_usernames, streamer_info, api
            )
        else:
            self._print_status_human(
                online_usernames, offline_usernames, streamer_info, api
            )

        return 0

    @staticmethod
    def _print_status_human(online, offline, info, api):
        """Print human-friendly status table."""
        if online:
            print("\033[1m  ONLINE\033[0m")
            for u in online:
                display = api.user_cache.get(u, {}).get("name", u)
                data = info.get(u, {})
                game = data.get("game", "")
                viewers = data.get("viewers", 0)
                parts = [display]
                if game:
                    parts.append(f"▶ {game}")
                if viewers:
                    parts.append(f"👤 {viewers:,}")
                print("  " + "  •  ".join(parts))
            print()

        if offline:
            print("\033[1m  OFFLINE\033[0m")
            names = [api.user_cache.get(u, {}).get("name", u) for u in sorted(offline)]
            if names:
                term_width = shutil.get_terminal_size((80, 24)).columns
                col_width = max(len(n) for n in names) + 3
                ncols = max(1, (term_width - 2) // col_width)
                for i in range(0, len(names), ncols):
                    row = names[i : i + ncols]
                    line = "  " + "".join(n.ljust(col_width) for n in row)
                    print(line.rstrip())
            print()

        total = len(online) + len(offline)
        print(
            f"Total: {len(online)} online, {len(offline)} offline (of {total} followed)"
        )

    @staticmethod
    def _print_status_json(online, offline, info, api):
        """Print status as JSON for scripting."""
        online_data = []
        for u in online:
            entry = {
                "username": u,
                "display_name": api.user_cache.get(u, {}).get("name", u),
            }
            data = info.get(u, {})
            if "game" in data:
                entry["game"] = data["game"]
            if "title" in data:
                entry["title"] = data["title"]
            if "viewers" in data:
                entry["viewers"] = data["viewers"]
            online_data.append(entry)

        offline_data = []
        for u in sorted(offline):
            offline_data.append(
                {
                    "username": u,
                    "display_name": api.user_cache.get(u, {}).get("name", u),
                }
            )

        print(
            json.dumps(
                {"online": online_data, "offline": offline_data},
                indent=2,
            )
        )
