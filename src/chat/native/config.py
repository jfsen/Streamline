"""Visual tunables for the native GTK ListView chat page.

Mirrors the structure of ``config.STYLE`` used by the WebKit chat so
settings are easy to compare side-by-side, but lives in its own file
so tweaks to one engine never affect the other.
"""

# ── Visual ──────────────────────────────────────────────────

NATIVE_STYLE = {
    # --- message card --------------------------------------------------
    "card_radius": 8,  # px – corner rounding
    "card_margin": "3px 6px",  # spacing between cards
    "card_padding": "5px 10px",  # internal padding
    # --- identity (badges + username) ----------------------------------
    "font_size": "15px",
    "font_family": "Inter, sans-serif",
    "line_height": "1.4",
    "user_weight": "700",
    "badge_spacing": 2,  # px between adjacent badges and badges / name
    "badge_size": 18,  # px square
    "identity_margin_bottom": 6,  # px gap between identity row and body
    # --- "more messages" banner --------------------------------------
    "banner_font": "bold 14px Inter, sans-serif",
    "banner_padding": "8px 12px",
    # --- colour palettes (dark / light) ------------------------------
    "dark": {
        "card_bg": "rgba(255,255,255,0.06)",
        "card_sep": "rgba(255,255,255,0.10)",
        "text_color": "#dedede",
        "banner_bg": "rgba(0,0,0,0.60)",
        "banner_fg": "#C7C7C7",
        "alt_row": "rgba(255,255,255,0.02)",  # alternating row tint
    },
    "light": {
        "card_bg": "rgba(0,0,0,0.04)",
        "card_sep": "rgba(0,0,0,0.08)",
        "text_color": "#2e2e2e",
        "banner_bg": "rgba(255,255,255,0.70)",
        "banner_fg": "#121212",
        "alt_row": "rgba(0,0,0,0.02)",  # alternating row tint
    },
}
