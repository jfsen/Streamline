"""Centralised tunables for the chat module."""

# ── Visual ──────────────────────────────────────────────────
#
# CSS-level appearance of the chat WebView.
# Consumed by:  chat_page._build_html()  and  chat_page._on_theme_changed()

STYLE = {
    # --- message row -------------------------------------------------
    "font_size": "15px",
    "font_family": "Inter, Emoji, sans-serif",
    "row_padding": "4px 8px",
    "line_height": "1.4",
    "user_weight": "700",
    "user_margin": "4px",
    # --- "more messages" banner --------------------------------------
    "banner_font": "bold 14px Inter, sans-serif",
    "banner_padding": "8px 12px",
    # --- body --------------------------------------------------------
    "body_padding_top": "4px",
    "body_padding_horiz": "8px",
    # --- auto-scroll -------------------------------------------------
    # Distance (px) from the bottom at which the view is considered
    # “at the bottom” and new messages auto-scroll.
    "scroll_threshold": 30,
    # --- colour palettes (dark / light) ------------------------------
    "dark": {
        "text_color": "#dedede",
        "banner_bg": "rgba(0,0,0,0.60)",
        "banner_fg": "#C7C7C7",
        "row_color": "rgba(255,255,255,0.03)",  # alternating-bg stripes
    },
    "light": {
        "text_color": "#2e2e2e",
        "banner_bg": "rgba(255,255,255,0.70)",
        "banner_fg": "#121212",
        "row_color": "rgba(0,0,0,0.03)",  # alternating-bg stripes
    },
}

# ── Behaviour ───────────────────────────────────────────────
#
# Runtime behaviour tunables — message limits, flush batching,
# and process lifetime.
#
# Consumers:  chat_page.py  (all four),  emotes.py  (CACHE_TTL)

# Messages beyond this count trigger culling from the DOM.
MAX_MESSAGES = 500

# Number of oldest messages removed in one pass when MAX_MESSAGES
# is exceeded.
CULL_CHUNK = 100

# Incoming messages are batched for this many milliseconds before
# a single DOM injection.  Higher values improve throughput at the
# cost of perceived latency.
FLUSH_MS = 300

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
