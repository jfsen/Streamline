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
        "first_msg_bg": "rgba(30,90,200,0.12)",  # blue tint for first message
        "first_msg_alt_bg": "rgba(30,90,200,0.09)",  # blue tint on alt row
        "mod_bg": "rgba(30,180,80,0.12)",  # green tint for moderator
        "mod_alt_bg": "rgba(30,180,80,0.09)",  # green tint on alt row
        "vip_bg": "rgba(245,50,155,0.12)",  # hot-pink tint for VIP
        "vip_alt_bg": "rgba(245,50,155,0.09)",  # hot-pink tint on alt row
        "partner_bg": "rgba(140,40,200,0.12)",  # purple tint for Partner
        "partner_alt_bg": "rgba(140,40,200,0.09)",  # purple tint on alt row
        "broadcaster_bg": "rgba(200,40,40,0.12)",  # red tint for broadcaster
        "broadcaster_alt_bg": "rgba(200,40,40,0.09)",  # red tint on alt row
    },
    "light": {
        "text_color": "#2e2e2e",
        "banner_bg": "rgba(255,255,255,0.70)",
        "banner_fg": "#121212",
        "row_color": "rgba(0,0,0,0.03)",  # alternating-bg stripes
        "first_msg_bg": "rgba(30,90,200,0.06)",  # blue tint for first message
        "first_msg_alt_bg": "rgba(30,90,200,0.04)",  # blue tint on alt row
        "mod_bg": "rgba(30,180,80,0.06)",  # green tint for moderator
        "mod_alt_bg": "rgba(30,180,80,0.04)",  # green tint on alt row
        "vip_bg": "rgba(245,50,155,0.06)",  # hot-pink tint for VIP
        "vip_alt_bg": "rgba(245,50,155,0.04)",  # hot-pink tint on alt row
        "partner_bg": "rgba(140,40,200,0.06)",  # purple tint for Partner
        "partner_alt_bg": "rgba(140,40,200,0.04)",  # purple tint on alt row
        "broadcaster_bg": "rgba(200,40,40,0.06)",  # red tint for broadcaster
        "broadcaster_alt_bg": "rgba(200,40,40,0.04)",  # red tint on alt row
    },
}
