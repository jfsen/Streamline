# config.py
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

"""Centralised tunables for the Streamline application."""

import os

# ── Window ──────────────────────────────────────────────────
# Default geometry of the main window.

MIN_WIDTH = 320
MIN_HEIGHT = 400
DEFAULT_WIDTH = 360
DEFAULT_HEIGHT = 700

# ── Themes ──────────────────────────────────────────────────
# Builtin themes use Adwaita palettes; custom ones use .css files
# bundled in the GResource.  To add a new custom theme, create its
# .css file in src/css/, add it to streamline.gresource.xml, and
# append its base name (without .css) to _CUSTOM_THEME_FILES here.

_CUSTOM_THEME_FILES = ["anthracite", "justin", "oxide"]
THEME_KEYS = ["system", "light", "dark"] + _CUSTOM_THEME_FILES

# ── Player ──────────────────────────────────────────────────
# Media player backend selection and streamlink quality presets.

PLAYER_KEYS = ["mpv", "vlc", "custom"]
QUALITY_KEYS = ["High", "Medium", "Low", "Custom"]
AUTO_REFRESH_KEYS = [180, 300, 600, 900]  # seconds
QUALITY_PRESETS = {
    "High": "1080p60,1080p,720p60,720p,best",
    "Medium": "720p,720p60,480p,best",
    "Low": "480p,360p,worst",
}

# ── Twitch API ──────────────────────────────────────────────
# Credentials — set via environment variables (works everywhere:
# source builds, Flatpak, development) or via a local config file.
# Environment variables take precedence.
#
# Env vars:
#   STREAMLINE_TWITCH_CLIENT_ID
#   STREAMLINE_TWITCH_CLIENT_SECRET
#
# Config file (source builds only):
#   Copy credentials.example.py to credentials.py
#   and fill in your keys.  This file is gitignored.

TWITCH_CLIENT_ID = os.environ.get("STREAMLINE_TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("STREAMLINE_TWITCH_CLIENT_SECRET", "")

if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
    try:
        from .credentials import (  # type: ignore[import-untyped]
            TWITCH_CLIENT_ID as _cid,
        )
        from .credentials import (
            TWITCH_CLIENT_SECRET as _csec,
        )

        if not TWITCH_CLIENT_ID:
            TWITCH_CLIENT_ID = _cid
        if not TWITCH_CLIENT_SECRET:
            TWITCH_CLIENT_SECRET = _csec
    except ImportError:
        pass

TWITCH_TOKEN_TIMEOUT = 10
TWITCH_STREAMS_TIMEOUT = 30
TWITCH_USERS_TIMEOUT = 10
TWITCH_VODS_TIMEOUT = 15
STREAMS_CACHE_COOLDOWN = 60

# ── VODs ───────────────────────────────────────────────────

VOD_FETCH_LIMIT = 100
VOD_CACHE_TTL = 3600  # 1 hour
VOD_REFRESH_COOLDOWN = 60

# ── Rows ────────────────────────────────────────────────────
# Streamer-list row UI timing (milliseconds).

ROW_HIGHLIGHT_MS = 10000  # just-went-online static highlight duration
PILL_SHOW_MS = 10000  # online-change pill static display duration
PILL_FADE_MS = 2000  # fade-out animation duration (shared by highlight and pill)
