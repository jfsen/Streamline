# auto_refresh.py
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

"""Periodic background refresh with desktop notifications."""

import gettext
import logging
import threading
from pathlib import Path

from gi.repository import Gio, GLib

_ = gettext.gettext
logger = logging.getLogger(__name__)


class AutoRefresher:
    """Periodically refreshes stream data and notifies when streamers go live.

    Attaches to a ``StreamlineWindow`` and reacts to GSettings changes
    for the ``auto-refresh`` and ``auto-refresh-interval`` keys.
    """

    def __init__(self, window):
        self._window = window
        self._timer_id = None

        settings = window.settings
        settings.connect("changed::auto-refresh", self._on_setting_changed)
        settings.connect("changed::auto-refresh-interval", self._on_setting_changed)

    # ── Public ────────────────────────────────────────────────

    def start(self):
        """(Re)start the timer based on current settings.  No-op when
        auto-refresh is disabled or the Twitch API is unavailable."""
        self._stop_timer()
        w = self._window
        if not w.auto_refresh or not w.twitch:
            return
        interval_ms = w.auto_refresh_interval * 1000
        self._timer_id = GLib.timeout_add(interval_ms, self._tick)
        logger.info(
            "Auto-refresh enabled — every %d minutes",
            w.auto_refresh_interval // 60,
        )

    def stop(self):
        """Stop the timer and release the reference.  Called during shutdown."""
        self._stop_timer()
        self._window = None

    # ── GSettings callback ────────────────────────────────────

    def _on_setting_changed(self, settings, key):
        w = self._window
        w.auto_refresh = settings.get_boolean("auto-refresh")
        w.auto_refresh_interval = settings.get_int("auto-refresh-interval")
        self.start()

    # ── Timer ─────────────────────────────────────────────────

    def _stop_timer(self):
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self):
        """Timer callback: refresh in background, respecting cache cooldown."""
        w = self._window
        if not w.twitch:
            self._timer_id = None
            return GLib.SOURCE_REMOVE
        # Respect the 60 s cache cooldown — skip when data is still fresh.
        _cached, seconds_until_refresh = w.twitch._load_streams_cache()
        if seconds_until_refresh > 0:
            return GLib.SOURCE_CONTINUE
        threading.Thread(target=self._background_refresh, daemon=True).start()
        return GLib.SOURCE_CONTINUE

    def _background_refresh(self):
        """Fetch streams on a background thread."""
        w = self._window
        assert w.twitch is not None
        try:
            online, offline, info = w.twitch.get_streams(w.all_streamers)
            GLib.idle_add(self._on_complete, online, offline, info)
        except Exception as e:
            logger.warning("Auto-refresh failed: %s", e)

    def _on_complete(self, online, offline, info):
        """Update UI silently and notify for newly-online streamers."""
        w = self._window
        new_online = [s for s in online if s not in w._last_online]
        w.update_action_rows(online, offline, info)
        w._start_avatar_downloads(online, offline)
        if new_online:
            self._notify(new_online, info)

    # ── Notifications ─────────────────────────────────────────

    def _notify(self, streamers, info):
        """Send a desktop notification for newly-online streamers."""
        if len(streamers) == 1:
            name = streamers[0]
            game = info.get(name, {}).get("game", "")
            title = _("{} is now live").format(name)
            body = _("Playing {}").format(game) if game else ""
        else:
            names = ", ".join(streamers)
            title = _("{} streamers are now live").format(len(streamers))
            body = names

        notification = Gio.Notification.new(title)
        if body:
            notification.set_body(body)

        # Include the streamer's avatar as the notification icon.
        # Use BytesIcon (raw bytes) so the XDG desktop portal can
        # render it even inside Flatpak.
        if len(streamers) == 1:
            avatar_path = (
                Path(GLib.get_user_cache_dir())
                / "Streamline"
                / "avatars"
                / f"{streamers[0]}.jpg"
            )
            if avatar_path.exists():
                try:
                    icon = Gio.BytesIcon.new(GLib.Bytes.new(avatar_path.read_bytes()))
                    notification.set_icon(icon)
                except OSError:
                    pass  # race: file deleted between exists() and read
        else:
            # Multiple streamers — show the app icon explicitly since
            # notification daemons don't always infer it from the sender.
            notification.set_icon(Gio.ThemedIcon.new("org.jfsen.Streamline"))

        self._window.get_application().send_notification(None, notification)
