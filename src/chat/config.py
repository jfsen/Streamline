"""Centralised tunables for the chat module."""

# ── Shared theme ───────────────────────────────────────────
#
# Typography, banner, and colour-palette constants shared by
# both the native GTK and WebKit chat engines.  Each engine's
# own config.py imports this and layers engine-specific keys
# (card backgrounds, row spacing, etc.) on top.
#
# Consumers:  native/config.py, webkit/config.py

CHAT_THEME = {
    # --- typography -------------------------------------------------
    "font_size": "15px",
    "font_family": "Inter, sans-serif",
    "line_height": "1.4",
    "user_weight": "700",
    # --- "more messages" banner --------------------------------------
    "banner_font": "bold 14px Inter, sans-serif",
    "banner_padding": "8px 12px",
    # --- colour palettes (dark / light) ------------------------------
    "dark": {
        "text_color": "#dedede",
        "banner_bg": "rgba(0,0,0,0.60)",
        "banner_fg": "#C7C7C7",
        "first_msg_bg": "rgba(30,90,200,0.20)",
        "first_msg_alt_bg": "rgba(30,90,200,0.25)",
        "mod_bg": "rgba(30,180,80,0.20)",
        "mod_alt_bg": "rgba(30,180,80,0.25)",
        "vip_bg": "rgba(255,100,168,0.15)",
        "vip_alt_bg": "rgba(255,100,168,0.20)",
        "partner_bg": "rgba(140,40,200,0.20)",
        "partner_alt_bg": "rgba(140,40,200,0.25)",
        "broadcaster_bg": "rgba(200,40,40,0.25)",
        "broadcaster_alt_bg": "rgba(200,40,40,0.30)",
    },
    "light": {
        "text_color": "#2e2e2e",
        "banner_bg": "rgba(255,255,255,0.70)",
        "banner_fg": "#121212",
        "first_msg_bg": "rgba(30,90,200,0.20)",
        "first_msg_alt_bg": "rgba(30,90,200,0.15)",
        "mod_bg": "rgba(30,180,80,0.20)",
        "mod_alt_bg": "rgba(30,180,80,0.15)",
        "vip_bg": "rgba(255,100,168,0.15)",
        "vip_alt_bg": "rgba(255,100,168,0.10)",
        "partner_bg": "rgba(140,40,200,0.20)",
        "partner_alt_bg": "rgba(140,40,200,0.15)",
        "broadcaster_bg": "rgba(200,40,40,0.25)",
        "broadcaster_alt_bg": "rgba(200,40,40,0.20)",
    },
}

# ── Behaviour ───────────────────────────────────────────────
#
# Runtime behaviour tunables — message limits, flush batching,
# and process lifetime.
#
# Consumers:  webkit/page.py, native/page.py,  emotes.py  (CACHE_TTL)

# Messages beyond this count trigger culling from the DOM.
MAX_MESSAGES = 500

# Number of oldest messages removed in one pass when MAX_MESSAGES
# is exceeded.
CULL_CHUNK = 50

# Incoming messages are batched for this many milliseconds before
# a single DOM injection.  Higher values improve throughput at the
# cost of perceived latency.
FLUSH_MS = 250

# ── Emote caches ───────────────────────────────────────────
#
# Per-service, per-scope cache lifetimes in seconds.
# ``global`` covers site-wide emotes; ``channel`` covers
# emotes specific to a streamer.
# Consumer:  emotes.py  (_load_cache)

EMOTE_CACHE_TTL = {
    "bttv": {"global": 86400, "channel": 3600},
    "7tv": {"global": 86400, "channel": 3600},
    "ffz": {"global": 86400, "channel": 3600},
}

# ── IRC ─────────────────────────────────────────────────────
#
# Twitch IRC connection parameters.
# Consumer:  twitch_chat.py

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6667

# Fallback username colour used when the IRC tags don't include a
# ``color`` attribute.
FALLBACK_USER_COLOR = "#9147ff"  # Twitch purple

# ── Emote CDNs ──────────────────────────────────────────────
#
# URL templates for Twitch-hosted emotes.  ``{id}`` is replaced
# with the emote ID at render time.
# Consumer:  twitch_chat.py

# Default: animated (GIF / APNG), dark-background variant.
TWITCH_EMOTE_CDN = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/1.0"

# Static (PNG), light-background variant — used when the
# "Disable Emote Animations" preference is turned on.
TWITCH_EMOTE_CDN_STATIC = (
    "https://static-cdn.jtvnw.net/emoticons/v2/{id}/static/light/1.0"
)

# ── Emote API endpoints ─────────────────────────────────────
#
# Third-party CDN API URLs.  ``{user_id}`` placeholders are
# filled with the Twitch user ID of the streamer whose channel
# emotes are being fetched.
# Consumer:  emotes.py

_BTTV_GLOBAL = "https://api.betterttv.net/3/cached/emotes/global"
_BTTV_CHANNEL = "https://api.betterttv.net/3/cached/users/twitch/{user_id}"
_SEVENTV_GLOBAL = "https://7tv.io/v3/emote-sets/global"
_SEVENTV_CHANNEL = "https://7tv.io/v3/users/twitch/{user_id}"
_FFZ_GLOBAL = "https://api.frankerfacez.com/v1/set/global"
_FFZ_CHANNEL = "https://api.frankerfacez.com/v1/room/id/{user_id}"
