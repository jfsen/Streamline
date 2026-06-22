"""Centralised tunables for the Streamline application."""

import os

# Detects whether the app is running inside a Flatpak sandbox.
IS_FLATPAK = os.path.exists("/.flatpak-info")

# ── Application ─────────────────────────────────────────────
#
# Core application identity.
# Consumers:  main.py  (APP_ID, VERSION),  window.py  (APP_ID via GSettings)

APP_ID = "org.jfsen.Streamline"
VERSION = "3.4.0"

# ── Window ──────────────────────────────────────────────────
#
# Default geometry of the main window.
# Consumer:  window.py

MIN_WIDTH = 320
MIN_HEIGHT = 400
DEFAULT_WIDTH = 360
DEFAULT_HEIGHT = 700

# GResource prefix used by all bundled assets (CSS, UI templates).
# Consumer:  window.py  (_load_theme_css, _apply_theme)
RESOURCE_BASE = "/org/jfsen/Streamline"

# ── Themes ──────────────────────────────────────────────────
#
# Ordered lists drive the combo rows in the preferences dialog.
# Consumer:  preferences.py  (combo row models)

THEME_KEYS = [
    "system",
    "light",
    "dark",
    "anthracite",
    "justin",
    "oxide",
]

# Maps a theme key to its custom CSS stylesheet (bundled via GResource).
# Themes *not* listed here use Adwaita's built-in light / dark palettes.
# Consumer:  window.py  (_apply_theme)
THEME_CSS = {
    "anthracite": f"{RESOURCE_BASE}/css/anthracite.css",
    "justin": f"{RESOURCE_BASE}/css/justin.css",
    "oxide": f"{RESOURCE_BASE}/css/oxide.css",
}

# ── Player ──────────────────────────────────────────────────
#
# Media player backend selection and streamlink quality presets.

# Ordered list drives the player combo row in preferences.
# Consumer:  preferences.py
PLAYER_KEYS = ["mpv", "vlc", "custom"]

# Ordered list drives the quality combo row in preferences.
# Consumer:  preferences.py
QUALITY_KEYS = ["High", "Medium", "Low", "Custom"]

# Streamlink quality strings keyed by the user-facing QUALITY_KEYS
# labels.  ``best`` is always appended as a final fallback unless
# the user supplies a Custom quality string.
# Consumer:  stream_player.py
QUALITY_PRESETS = {
    "High": "1080p60,1080p,best,720p60,720p",
    "Medium": "720p,480p,720p60,best",
    "Low": "360p,480p,worst",
}

# Base command used to invoke streamlink.
# Uses flatpak-spawn inside the Flatpak sandbox, streamlink directly otherwise.
# Consumer:  stream_player.py
if IS_FLATPAK:
    STREAMLINK_CMD = ["flatpak-spawn", "--host", "streamlink"]
else:
    STREAMLINK_CMD = ["streamlink"]

# ── Twitch API ──────────────────────────────────────────────
#
# Endpoints and network behaviour for the Twitch Helix API.
# Consumer:  twitch.py

# OAuth 2.0 client-credentials token endpoint.
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# Base Helix API URL template (path is appended per endpoint).
TWITCH_API_BASE = "https://api.twitch.tv/helix"

# HTTP request timeouts in seconds.
TWITCH_TOKEN_TIMEOUT = 10
TWITCH_STREAMS_TIMEOUT = 30
TWITCH_USERS_TIMEOUT = 10
TWITCH_VODS_TIMEOUT = 15

# Minimum seconds between stream-data refreshes.
STREAMS_CACHE_COOLDOWN = 60

# ── VODs ───────────────────────────────────────────────────
#
# Past-broadcast listing behaviour.
# Consumers:  twitch.py  (VOD_FETCH_LIMIT),
#             vod_page.py  (VOD_CACHE_TTL, VOD_REFRESH_COOLDOWN)

# Number of VODs requested from the Twitch API per fetch.
VOD_FETCH_LIMIT = 100

# VOD cache lifetime in seconds.
VOD_CACHE_TTL = 3600  # 1 hour

# Minimum seconds between manual VOD refreshes.
VOD_REFRESH_COOLDOWN = 60

# ── Rows ────────────────────────────────────────────────────
#
# Streamer-list row UI timing (milliseconds).
# Consumer:  rows.py

# How long the "just went online" highlight glow stays on a row.
ROW_HIGHLIGHT_MS = 5000

# How long the online/offline-change pill badge is shown before fading.
PILL_SHOW_MS = 3000

# Duration of the pill fade-out animation.
PILL_FADE_MS = 2000
