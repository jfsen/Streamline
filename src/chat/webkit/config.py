"""CSS-level appearance tunables for the WebKit chat page."""

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
