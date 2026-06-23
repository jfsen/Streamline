"""Centralised tunables for the Streamline application."""

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
QUALITY_PRESETS = {
    "High": "1080p60,1080p,best,720p60,720p",
    "Medium": "720p,480p,720p60,best",
    "Low": "360p,480p,worst",
}

# ── Twitch API ──────────────────────────────────────────────
# HTTP request timeouts in seconds.

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

ROW_HIGHLIGHT_MS = 5000  # just-went-online highlight glow
PILL_SHOW_MS = 3000  # online-change pill display duration
PILL_FADE_MS = 2000  # pill fade-out animation duration
