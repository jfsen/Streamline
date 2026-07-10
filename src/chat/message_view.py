# message_view.py
#
# Copyright 2025 jfsen
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Message card construction and theme-aware restyling."""

from __future__ import annotations

import gettext
import logging
import weakref

from gi.repository import GLib, Gtk, Pango

from .config import CHAT_STYLE, FALLBACK_USER_COLOR
from .emote_renderer import (
    _BADGE_SVGS,
    _EMOTE_CACHE,
    _clamp_color,
    _get_badge_texture,
    _rgba_to_hex,
)

_ = gettext.gettext
logger = logging.getLogger(__name__)


class MessageCardBuilder:
    """Constructs and restyles message card widgets on behalf of a ChatPage."""

    _REBUILD_CHUNK = 20  # cards per idle iteration during theme rebuild

    def __init__(self, page) -> None:
        self._page = page

    # ── Card CSS ─────────────────────────────────────────────

    def update_card_css(self) -> None:
        """Rebuild the shared card CSS provider for the current theme."""
        ns = CHAT_STYLE
        theme = ns["dark"] if self._page._dark else ns["light"]
        # Use two classes so alternating rows can pick a different bg.
        self._page._card_css_provider.load_from_data(
            f".msg-card {{"
            f"  background: {theme['card_bg']};"
            f"  border-radius: {ns['card_radius']}px;"
            f"  margin: {ns['card_margin']};"
            f"  padding: {ns['card_padding']};"
            f"}}"
            f".msg-card-alt {{"
            f"  background: {theme['alt_row']};"
            f"  border-radius: {ns['card_radius']}px;"
            f"  margin: {ns['card_margin']};"
            f"  padding: {ns['card_padding']};"
            f"}}"
            f".msg-card-first {{"
            f"  background: {theme['first_msg_bg']};"
            f"}}"
            f".msg-card-alt.msg-card-first {{"
            f"  background: {theme['first_msg_alt_bg']};"
            f"}}"
            f".msg-card-mod {{"
            f"  background: {theme['mod_bg']};"
            f"}}"
            f".msg-card-alt.msg-card-mod {{"
            f"  background: {theme['mod_alt_bg']};"
            f"}}"
            f".msg-card-vip {{"
            f"  background: {theme['vip_bg']};"
            f"}}"
            f".msg-card-alt.msg-card-vip {{"
            f"  background: {theme['vip_alt_bg']};"
            f"}}"
            f".msg-card-partner {{"
            f"  background: {theme['partner_bg']};"
            f"}}"
            f".msg-card-alt.msg-card-partner {{"
            f"  background: {theme['partner_alt_bg']};"
            f"}}"
            f".msg-card-broadcaster {{"
            f"  background: {theme['broadcaster_bg']};"
            f"}}"
            f".msg-card-alt.msg-card-broadcaster {{"
            f"  background: {theme['broadcaster_alt_bg']};"
            f"}}"
            f".msg-card:hover {{ background: {theme['card_bg']}; }}"
            f".msg-card-alt:hover {{ background: {theme['alt_row']}; }}"
            f".msg-card-first:hover {{ background: {theme['first_msg_bg']}; }}"
            f".msg-card-alt.msg-card-first:hover {{ background: {theme['first_msg_alt_bg']}; }}"
            f".msg-card-mod:hover {{ background: {theme['mod_bg']}; }}"
            f".msg-card-alt.msg-card-mod:hover {{ background: {theme['mod_alt_bg']}; }}"
            f".msg-card-vip:hover {{ background: {theme['vip_bg']}; }}"
            f".msg-card-alt.msg-card-vip:hover {{ background: {theme['vip_alt_bg']}; }}"
            f".msg-card-partner:hover {{ background: {theme['partner_bg']}; }}"
            f".msg-card-alt.msg-card-partner:hover {{ background: {theme['partner_alt_bg']}; }}"
            f".msg-card-broadcaster:hover {{ background: {theme['broadcaster_bg']}; }}"
            f".msg-card-alt.msg-card-broadcaster:hover {{ background: {theme['broadcaster_alt_bg']}; }}",
            -1,
        )

    # ── Card builder ─────────────────────────────────────────

    def build_card(self, msg: dict) -> Gtk.Widget:
        """Create one message card widget from the raw message dict."""
        ns = CHAT_STYLE
        theme = ns["dark"] if self._page._dark else ns["light"]

        # ── Card frame ───────────────────────────────────────
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("msg-card")
        card.get_style_context().add_provider(
            self._page._card_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        is_system = msg.get("system", False)

        if is_system:
            # System messages: italic text in a Label (wraps correctly,
            # far cheaper than Gtk.TextView for plain text).
            label = Gtk.Label()
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_xalign(0)
            label.set_selectable(True)
            label.set_halign(Gtk.Align.FILL)
            label.set_valign(Gtk.Align.FILL)
            label.set_margin_top(2)
            label.set_margin_bottom(2)
            label.set_markup(
                f'<span foreground="{theme["text_color"]}" style="italic">'
                f"{GLib.markup_escape_text(msg['text'])}"
                f"</span>"
            )

            # Stash for theme-change restyling.
            label._is_system = True
            label._body_text = msg["text"]
            card.append(label)
            return card

        # ── Identity (badges + username) ────────────────────
        identity = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=int(ns["badge_spacing"]),
        )
        identity.set_valign(Gtk.Align.START)

        # Badges — reuse textures across cards via a module-level
        # cache so repeated badges don't create duplicate GPU textures.
        for display_name, badge_id, tenure in msg.get("badges", []):
            svg_data = _BADGE_SVGS.get(badge_id)
            if svg_data is None:
                continue
            texture = _get_badge_texture(badge_id, svg_data)
            if texture is not None:
                badge = Gtk.Picture.new()
                badge.set_paintable(texture)
                badge.set_size_request(int(ns["badge_size"]), int(ns["badge_size"]))
                badge.set_valign(Gtk.Align.START)
                tooltip = f"{tenure}-month {display_name}" if tenure else display_name
                badge.set_tooltip_text(tooltip)
                identity.append(badge)

        # Username
        color_str = msg.get("color", FALLBACK_USER_COLOR)
        clamped = _clamp_color(color_str, self._page._dark)
        user_name = msg["user"]
        is_action = msg.get("action", False)
        user_label = Gtk.Label()
        user_label.set_markup(
            f'<span font_weight="{ns["user_weight"]}" '
            f'foreground="{_rgba_to_hex(clamped)}">'
            f"{GLib.markup_escape_text(user_name)}"
            f"{'' if is_action else ':'}"
            f"</span>"
        )
        user_label.set_halign(Gtk.Align.START)
        user_label.set_valign(Gtk.Align.START)
        identity.append(user_label)

        # Stash original data for theme-change restyling.
        identity._user_name = user_name
        identity._user_color = color_str
        identity._is_action = is_action

        card.append(identity)

        # ── Body (Label / FlowBox / TextView, cheapest first) ─
        segments = msg.get("segments", [{"type": "text", "content": msg["text"]}])

        has_text = any(seg["type"] == "text" for seg in segments)
        has_emotes = any(seg["type"] == "emote" for seg in segments)

        if not has_emotes:
            # Plain text — use a cheap Gtk.Label instead of a heavyweight
            # Gtk.TextView+TextBuffer.  Most channels are emote-heavy, but
            # when a message does land without emotes this avoids costly
            # Pango layout / buffer machinery.
            body_label = Gtk.Label()
            body_label.set_wrap(True)
            body_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body_label.set_xalign(0)
            body_label.set_selectable(True)
            body_label.set_halign(Gtk.Align.FILL)
            body_label.set_valign(Gtk.Align.FILL)
            body_label.set_margin_top(2)
            body_label.set_margin_bottom(2)
            body_label.set_markup(
                f'<span foreground="{theme["text_color"]}">'
                f"{GLib.markup_escape_text(msg['text'])}"
                f"</span>"
            )
            body_label._body_text = msg["text"]
            card.append(body_label)
            return card

        if has_emotes and not has_text:
            # Emote-only — use a Gtk.FlowBox.  No text means no
            # Pango.Layout, no TextBuffer, no child anchors — just
            # inline-flowed Picture widgets.
            flow = Gtk.FlowBox()
            flow.set_halign(Gtk.Align.FILL)
            flow.set_valign(Gtk.Align.FILL)
            flow.set_selection_mode(Gtk.SelectionMode.NONE)
            flow.set_activate_on_single_click(False)
            flow.set_margin_top(2)
            flow.set_margin_bottom(2)
            flow.set_column_spacing(2)
            flow.set_row_spacing(2)
            flow.set_min_children_per_line(1)
            flow.set_max_children_per_line(100)

            for seg in segments:
                if seg["type"] != "emote":
                    continue
                pic = Gtk.Picture()
                pic.set_size_request(28, 28)
                pic.set_can_shrink(False)
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                pic._page_ref = weakref.ref(self._page)
                pic._card = weakref.ref(card)
                pic.set_tooltip_text(f"{seg['name']} ({seg['source']})")
                flow.insert(pic, -1)
                _EMOTE_CACHE.request(seg["url"], pic)

            card.append(flow)
            return card

        # Mixed text+emotes — Gtk.TextView with child anchors.
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_view.set_halign(Gtk.Align.FILL)
        text_view.set_valign(Gtk.Align.FILL)
        text_view.set_top_margin(2)
        text_view.set_bottom_margin(2)

        text_view.get_style_context().add_provider(
            self._page._tv_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        buffer = text_view.get_buffer()
        tag = buffer.create_tag("body", foreground=theme["text_color"])

        for seg in segments:
            if seg["type"] == "text":
                buffer.insert_with_tags(buffer.get_end_iter(), seg["content"], tag)
            elif seg["type"] == "emote":
                end_iter = buffer.get_end_iter()
                anchor = buffer.create_child_anchor(end_iter)
                pic = Gtk.Picture()
                pic.set_size_request(28, 28)
                pic.set_can_shrink(False)
                pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                pic._page_ref = weakref.ref(self._page)
                pic._card = weakref.ref(card)
                tooltip = f"{seg['name']} ({seg['source']})"
                pic.set_tooltip_text(tooltip)
                text_view.add_child_at_anchor(pic, anchor)
                _EMOTE_CACHE.request(seg["url"], pic)

        # The buffer-changed signal doesn't always queue a resize when
        # the widget isn't yet in the tree (text-only messages).
        # Explicitly invalidate so the card gets its real height.
        text_view.queue_resize()

        card.append(text_view)
        return card

    # ── Theme ───────────────────────────────────────────────

    def on_theme_changed(self, style_manager, _pspec) -> None:
        self._page._dark = style_manager.get_dark()
        self.apply_banner_style()
        self.update_card_css()
        # Rebuild newest cards first — they're what the user sees.
        cards = self._page._cards
        if cards:
            self.rebuild_cards_chunked(len(cards) - 1)

    def rebuild_cards_chunked(self, idx: int) -> bool:
        """Re-style cards from *idx* downward in chunks."""
        if self._page._cleaned_up:
            return GLib.SOURCE_REMOVE
        cards = list(self._page._cards)
        end = max(idx - self._REBUILD_CHUNK + 1, 0)
        for i in range(idx, end - 1, -1):
            card = cards[i]
            identity = card.get_first_child()
            if identity is not None and isinstance(identity, (Gtk.TextView, Gtk.Label)):
                self.restyle_body(identity)
                continue
            if identity is not None:
                self.restyle_identity(identity)
            body = identity.get_next_sibling() if identity else None
            if body is not None:
                self.restyle_body(body)
        if end > 0:
            GLib.idle_add(self.rebuild_cards_chunked, end - 1)
        return GLib.SOURCE_REMOVE

    def restyle_identity(self, identity: Gtk.Box) -> None:
        """Update the username label colour for the current theme."""
        ns = CHAT_STYLE

        child = identity.get_last_child()
        if child is None or not isinstance(child, Gtk.Label):
            return

        user_name = getattr(identity, "_user_name", None)
        color_str = getattr(identity, "_user_color", None)
        is_action = getattr(identity, "_is_action", False)
        if user_name is None or color_str is None:
            return

        clamped = _clamp_color(color_str, self._page._dark)
        child.set_markup(
            f'<span font_weight="{ns["user_weight"]}" '
            f'foreground="{_rgba_to_hex(clamped)}">'
            f"{GLib.markup_escape_text(user_name)}"
            f"{'' if is_action else ':'}"
            f"</span>"
        )

    def restyle_body(self, widget: Gtk.Widget) -> None:
        """Update text colour in the body for the new theme.

        Handles Gtk.FlowBox (emote-only — no text to recolor),
        Gtk.Label (text-only / system messages), and
        Gtk.TextView (messages containing emotes).
        """
        if isinstance(widget, Gtk.FlowBox):
            return  # emote-only — no text to update
        theme = CHAT_STYLE["dark"] if self._page._dark else CHAT_STYLE["light"]
        if isinstance(widget, Gtk.Label):
            body_text = getattr(widget, "_body_text", "")
            is_system = getattr(widget, "_is_system", False)
            if is_system:
                widget.set_markup(
                    f'<span foreground="{theme["text_color"]}" style="italic">'
                    f"{GLib.markup_escape_text(body_text)}"
                    f"</span>"
                )
            else:
                widget.set_markup(
                    f'<span foreground="{theme["text_color"]}">'
                    f"{GLib.markup_escape_text(body_text)}"
                    f"</span>"
                )
            return
        tag = widget.get_buffer().get_tag_table().lookup("body")
        if tag is not None:
            tag.set_property("foreground", theme["text_color"])

    def apply_banner_style(self) -> None:
        ns = CHAT_STYLE
        theme = ns["dark"] if self._page._dark else ns["light"]

        # Remove previous provider to avoid accumulating on theme switches.
        if self._page._banner_css_provider is not None:
            self._page._more_button.get_style_context().remove_provider(
                self._page._banner_css_provider
            )
            self._page._reconnect_revealer.get_style_context().remove_provider(
                self._page._banner_css_provider
            )

        provider = Gtk.CssProvider()
        provider.load_from_data(
            f".more-msg-banner {{ "
            f"  font: {ns['banner_font']}; "
            f"  padding: {ns['banner_padding']}; "
            f"  background: {theme['banner_bg']}; "
            f"  color: {theme['banner_fg']}; "
            f"}}"
            f".reconnect-banner {{ "
            f"  font: {ns['banner_font']}; "
            f"  padding: {ns['banner_padding']}; "
            f"  background: {theme['banner_bg']}; "
            f"  color: {theme['banner_fg']}; "
            f"}}",
            -1,
        )
        self._page._banner_css_provider = provider

        ctx = self._page._more_button.get_style_context()
        ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if self._page._more_button.get_child():
            self._page._more_button.get_child().set_css_classes([])
        rctx = self._page._reconnect_revealer.get_style_context()
        rctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
