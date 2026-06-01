"""Centralised tunables for the chat module — visual style and behaviour."""

# ── Visual ──────────────────────────────────────────────────

STYLE = {
    "font_size": "15px",
    "font_family": "Inter, Emoji, sans-serif",
    "row_padding": "4px 0",
    "line_height": "1.4",
    "user_weight": "700",
    "user_margin": "4px",
    "emote_height": "1.6em",
    "badge_height": "1.4em",
    "pill_font": "bold 13px Inter, sans-serif",
    "pill_bottom": "8px",
    "pill_padding": "4px 12px",
    "body_padding": "4px 8px",
    "scroll_threshold": 30,
    "max_messages": 1000,
    "cull_chunk": 100,
    "flush_ms": 500,
    "dark": {
        "text_color": "#dedede",
        "pill_bg": "rgba(255,255,255,0.18)",
        "pill_fg": "#ccc",
        "row_color": "rgba(255,255,255,0.04)",
    },
    "light": {
        "text_color": "#2e2e2e",
        "pill_bg": "rgba(0,0,0,0.14)",
        "pill_fg": "#555",
        "row_color": "rgba(0,0,0,0.03)",
    },
}

# ── IRC ─────────────────────────────────────────────────────

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6667
FALLBACK_USER_COLOR = "#9147ff"  # Twitch purple, used when IRC omits color tag

# ── Emote CDNs ──────────────────────────────────────────────

TWITCH_EMOTE_CDN = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/1.0"

# ── Emote API endpoints ─────────────────────────────────────

_BTTV_GLOBAL = "https://api.betterttv.net/3/cached/emotes/global"
_BTTV_CHANNEL = "https://api.betterttv.net/3/cached/users/twitch/{user_id}"
_SEVENTV_GLOBAL = "https://7tv.io/v3/emote-sets/global"
_SEVENTV_CHANNEL = "https://7tv.io/v3/users/twitch/{user_id}"
_FFZ_GLOBAL = "https://api.frankerfacez.com/v1/set/global"
_FFZ_CHANNEL = "https://api.frankerfacez.com/v1/room/id/{user_id}"

# ── Cache ───────────────────────────────────────────────────

CACHE_TTL = 3600  # 1 hour
