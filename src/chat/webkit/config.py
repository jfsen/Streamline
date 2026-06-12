"""CSS-level appearance tunables for the WebKit chat page.

Extends the shared ``CHAT_THEME`` from ``chat.config`` with
engine-specific keys (row spacing, body padding, etc.)
so tweaks to one engine never affect the other.
"""

from ..config import CHAT_THEME

STYLE = {
    **CHAT_THEME,
    # WebKit needs an explicit Emoji fallback; GTK pulls from the
    # system emoji font automatically via Pango.
    "font_family": "Inter, Emoji, sans-serif",
    # --- message row (WebKit-only) -----------------------------------
    "row_padding": "4px 8px",
    "user_margin": "4px",
    # --- body (WebKit-only) ------------------------------------------
    "body_padding_top": "4px",
    "body_padding_horiz": "8px",
    # --- colour palettes (dark / light) ------------------------------
    "dark": {
        **CHAT_THEME["dark"],
        "row_color": "rgba(255,255,255,0.03)",  # alternating-bg stripes
    },
    "light": {
        **CHAT_THEME["light"],
        "row_color": "rgba(0,0,0,0.03)",  # alternating-bg stripes
    },
}
