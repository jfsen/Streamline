"""Visual tunables for the native GTK ListView chat page.

Extends the shared ``CHAT_THEME`` from ``chat.config`` with
engine-specific keys (card backgrounds, badge sizing, etc.)
so tweaks to one engine never affect the other.
"""

from ..config import CHAT_THEME

# ── Visual ──────────────────────────────────────────────────

NATIVE_STYLE = {
    **CHAT_THEME,
    # --- message card (GTK-only) -------------------------------------
    "card_radius": 8,  # px – corner rounding
    "card_margin": "3px 6px",  # spacing between cards
    "card_padding": "5px 10px",  # internal padding
    # --- identity (badges + username) ----------------------------------
    "badge_spacing": 2,  # px between adjacent badges and badges / name
    "badge_size": 18,  # px square
    # --- colour palettes (dark / light) ------------------------------
    "dark": {
        **CHAT_THEME["dark"],
        "card_bg": "rgba(255,255,255,0.06)",
        "card_sep": "rgba(255,255,255,0.10)",
        "alt_row": "rgba(255,255,255,0.02)",  # alternating row tint
    },
    "light": {
        **CHAT_THEME["light"],
        "card_bg": "rgba(0,0,0,0.04)",
        "card_sep": "rgba(0,0,0,0.08)",
        "alt_row": "rgba(0,0,0,0.02)",  # alternating row tint
    },
}
