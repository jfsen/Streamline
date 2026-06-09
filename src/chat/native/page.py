"""Alternative chat page using native GTK ListView widgets instead of WebKit.

Memory model (vs. WebKit): only visible rows exist as GTK widgets.  Emote
images are downloaded once, decoded to Gdk.Texture, and shared across all
rows referencing the same URL.  Animated emotes render as static first-frame
— acceptable for a resource-conscious "alt" implementation.
"""

import gettext
import hashlib
import logging
import tempfile as _tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import gi
import requests
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from ..config import (
    CULL_CHUNK,
    FALLBACK_USER_COLOR,
    FLUSH_MS,
    MAX_MESSAGES,
)
from ..emotes import ThirdPartyEmotes
from ..twitch_chat import TwitchChat
from .config import NATIVE_STYLE

_ = gettext.gettext
logger = logging.getLogger("NativeChatPage")

# ── Badge SVGs (loaded once at module level) ───────────────

_BADGE_DIR = Path(__file__).parent.parent / "badges"
_BADGE_SVGS = {}
for _f in _BADGE_DIR.glob("*.svg"):
    _BADGE_SVGS[_f.stem] = _f.read_text()

# ── Emote image cache ──────────────────────────────────────────

_EMOTE_IMAGE_DIR = Path(GLib.get_user_cache_dir()) / "Streamline" / "emotes" / "images"
_MAX_EMOTE_TEXTURES = 500


class EmoteTextureCache:
    """Downloads emote images and caches them as Gdk.Texture.

    Two-tier caching:

    * *In-memory LRU* — the most recently used textures (up to
      ``_MAX_EMOTE_TEXTURES``) are kept decoded for instant reuse.
      Excess entries are evicted oldest-first.  Since message culling
      removes rows from the list model, emotes that no longer appear in
      any visible message naturally become eviction candidates.

    * *On-disk* — raw image bytes are stored at
      ``~/.cache/Streamline/emotes/images/<url-hash>`` so a texture
      survives app restarts without re-downloading.

    Decodes via Pillow so every format (WebP, GIF, PNG, JPEG) works
    regardless of which gdk-pixbuf loaders the system ships.
    """

    def __init__(self):
        self._textures: OrderedDict[str, Gdk.Texture] = OrderedDict()
        self._pending: dict[str, list[tuple[Gtk.Widget, bool]]] = {}
        self._lock = threading.Lock()
        _EMOTE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    # ── URL → stable disk filename ──────────────────

    @staticmethod
    def _url_hash(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _disk_path(self, url: str) -> Path:
        return _EMOTE_IMAGE_DIR / self._url_hash(url)

    # ── Public API ───────────────────────────────────

    def request(self, url: str, widget: Gtk.Widget) -> None:
        with self._lock:
            # In-memory hit — move to MRU position and apply
            if url in self._textures:
                texture = self._textures.pop(url)
                self._textures[url] = texture
                GLib.idle_add(_apply_texture, widget, texture)
                return

            # Already being fetched — queue for when data arrives
            if url in self._pending:
                self._pending[url].append((widget, False))
                return

            self._pending[url] = [(widget, False)]

            # Disk hit — load bytes and decode in background
            disk_path = self._disk_path(url)
            if disk_path.exists():
                target = ("disk", disk_path)
            else:
                target = ("net", url)

        kind, arg = target
        if kind == "disk":
            threading.Thread(
                target=self._load_from_disk, args=(url, arg), daemon=True
            ).start()
        else:
            threading.Thread(target=self._download, args=(url,), daemon=True).start()

    # ── Background loaders ──────────────────────────

    def _load_from_disk(self, url: str, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.debug("Failed to read emote from disk %s: %s", path, exc)
            data = None
        if data is not None:
            GLib.idle_add(self._on_data, url, data)
        else:
            self._download(url)

    def _download(self, url: str) -> None:
        data = None
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.content
            # Persist raw bytes to disk so future requests skip the network
            try:
                self._disk_path(url).write_bytes(data)
            except OSError as exc:
                logger.debug("Failed to write emote to disk %s: %s", url, exc)
        except Exception as exc:
            logger.debug("Failed to download emote %s: %s", url, exc)

        GLib.idle_add(self._on_data, url, data)

    # ── Completion callback (always called on the main thread) ────

    def _on_data(self, url: str, data: bytes | None) -> bool:
        texture = None
        if data:
            texture = self._decode(data)
            if texture is None:
                logger.debug("Failed to decode emote %s", url)

        with self._lock:
            if texture is not None:
                # Insert / move to MRU position
                self._textures[url] = texture
                self._textures.move_to_end(url)
                # LRU eviction
                while len(self._textures) > _MAX_EMOTE_TEXTURES:
                    self._textures.popitem(last=False)
            widgets = self._pending.pop(url, [])

        if texture is not None:
            for widget, _replaced in widgets:
                _apply_texture(widget, texture)

        return GLib.SOURCE_REMOVE

    @staticmethod
    def _decode(data: bytes) -> Gdk.Texture | None:
        """Decode any image format to a Gdk.Texture via Pillow → PNG."""
        from io import BytesIO

        from PIL import Image

        # Pillow is extremely verbose about PNG chunk metadata on stderr;
        # suppress its logger so the terminal stays readable.
        logging.getLogger("PIL").setLevel(logging.WARNING)

        try:
            img = Image.open(BytesIO(data))
            png_buf = BytesIO()
            img.save(png_buf, format="PNG")
            return Gdk.Texture.new_from_bytes(GLib.Bytes.new(png_buf.getvalue()))
        except Exception as exc:
            logger.debug("Emote decode failed: %s", exc)
            return None


def _apply_texture(widget: Gtk.Widget, texture: Gdk.Texture) -> bool:
    """Set the texture on an emote widget (always called on the main thread)."""
    widget.set_paintable(texture)
    widget.queue_resize()
    return GLib.SOURCE_REMOVE


# ── Shared emote cache (one per process) ────────────────────

_EMOTE_CACHE = EmoteTextureCache()

# ── Helpers ──────────────────────────────────────────────────


def _clamp_color(hex_color: str, dark: bool) -> Gdk.RGBA:
    """Clamp a username colour so it remains legible on the current
    background.  Logic mirrors the JS ``clampColor()`` in the WebKit
    chat page."""

    hex_clean = hex_color.lstrip("#")
    r = int(hex_clean[0:2], 16) / 255.0
    g = int(hex_clean[2:4], 16) / 255.0
    b = int(hex_clean[4:6], 16) / 255.0

    max_c = max(r, g, b)
    min_c = min(r, g, b)
    l = (max_c + min_c) / 2.0

    if max_c == min_c:
        h = 0.0
        s = 0.0
    else:
        d = max_c - min_c
        s = d / (2.0 - max_c - min_c) if l > 0.5 else d / (max_c + min_c)
        if max_c == r:
            h = ((g - b) / d + (6.0 if g < b else 0.0)) / 6.0
        elif max_c == g:
            h = ((b - r) / d + 2.0) / 6.0
        else:
            h = ((r - g) / d + 4.0) / 6.0

    # Clamp lightness
    l = max(l, 0.78) if dark else min(l, 0.28)

    # HSL → RGB
    if s == 0:
        rr = gg = bb = l
    else:
        c = (1.0 - abs(2.0 * l - 1.0)) * s
        x = c * (1.0 - abs((h * 6.0) % 2.0 - 1.0))
        m = l - c / 2.0
        if h < 1.0 / 6.0:
            rr, gg, bb = c, x, 0.0
        elif h < 2.0 / 6.0:
            rr, gg, bb = x, c, 0.0
        elif h < 3.0 / 6.0:
            rr, gg, bb = 0.0, c, x
        elif h < 4.0 / 6.0:
            rr, gg, bb = 0.0, x, c
        elif h < 5.0 / 6.0:
            rr, gg, bb = x, 0.0, c
        else:
            rr, gg, bb = c, 0.0, x
        rr, gg, bb = rr + m, gg + m, bb + m

    rgba = Gdk.RGBA()
    rgba.red = max(0.0, min(1.0, rr))
    rgba.green = max(0.0, min(1.0, gg))
    rgba.blue = max(0.0, min(1.0, bb))
    rgba.alpha = 1.0
    return rgba


def _build_segments(text: str, emotes: list[dict]) -> list[dict]:
    """Interleave plain-text and emote segments.

    Returns a list of dicts::

        {"type": "text", "content": "…"}
        {"type": "emote", "url": "https://…", "name": "Kappa", "source": "Twitch"}

    Emote positions are Python code-point offsets (from Twitch IRC and
    the third-party trie scanner) — no UTF-16 conversion needed here.
    """
    if not emotes:
        return [{"type": "text", "content": text}]

    # Collect, deduplicate overlapping, and sort
    raw_positions: list[tuple[int, int, str, str, str]] = []
    for em in emotes:
        for start, end in em["positions"]:
            raw_positions.append(
                (start, end, em["url"], em.get("name", ""), em.get("source", ""))
            )
    raw_positions.sort(key=lambda x: x[0])

    segments: list[dict] = []
    cursor = 0
    for start, end, url, name, source in raw_positions:
        if start < cursor:
            continue  # overlapping emote — skip
        if start > cursor:
            segments.append({"type": "text", "content": text[cursor:start]})
        segments.append(
            {
                "type": "emote",
                "url": url,
                "name": name or "Emote",
                "source": source or "?",
            }
        )
        cursor = end + 1

    if cursor < len(text):
        segments.append({"type": "text", "content": text[cursor:]})

    return segments


# ── Data model ───────────────────────────────────────────────


class _ChatMsg(GObject.Object):
    """GObject wrapper so Gio.ListStore can hold message data."""

    user: str
    text: str
    color: str
    segments: list
    badges: list
    action: bool
    _dark: bool
    _alternating: bool

    def __init__(self, **kwargs):
        super().__init__()
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)


# ── Row factory ──────────────────────────────────────────────


def _row_setup(factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
    """Build the reusable widget template once per recycled row.

    Template references are stored on the list_item so ``_row_bind`` can
    update content without rebuilding widgets.
    """
    ns = NATIVE_STYLE

    # ── Root row box ────────────────────────────────────────
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    list_item._row_provider = Gtk.CssProvider()
    # Static CSS — applied once per widget, never changes.
    list_item._row_provider.load_from_data(
        "box, box:hover { background: transparent; }", -1
    )
    row.get_style_context().add_provider(
        list_item._row_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    # ── Card (rounded frame) ────────────────────────────────
    list_item._card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    list_item._card.add_css_class("msg-card")
    list_item._card_provider = Gtk.CssProvider()
    list_item._card.get_style_context().add_provider(
        list_item._card_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    # Track last card-bg variant so _row_bind can skip redundant reloads.
    list_item._last_card_bg = None

    # ── Identity row (badges + username) ────────────────────
    list_item._identity = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=int(ns["badge_spacing"]),
    )
    list_item._identity.set_valign(Gtk.Align.START)
    list_item._identity.set_margin_bottom(ns["identity_margin_bottom"])

    list_item._user_label = Gtk.Label()
    list_item._user_label.set_halign(Gtk.Align.START)
    list_item._user_label.set_valign(Gtk.Align.START)
    list_item._identity.append(list_item._user_label)

    list_item._card.append(list_item._identity)

    # ── Message body (TextView) ─────────────────────────────
    list_item._text_view = Gtk.TextView()
    list_item._text_view.set_editable(False)
    list_item._text_view.set_cursor_visible(False)
    list_item._text_view.set_wrap_mode(Gtk.WrapMode.WORD)
    list_item._text_view.set_halign(Gtk.Align.FILL)
    list_item._text_view.set_valign(Gtk.Align.FILL)
    # Minimum width prevents the text view from computing an absurdly tall
    # layout when the initial measure pass has a narrow-or-zero width guess.
    list_item._text_view.set_size_request(300, -1)

    tv_provider = Gtk.CssProvider()
    tv_provider.load_from_data(
        f"textview {{ background: transparent; font: {ns['font_size']} {ns['font_family']}; padding: 0; }}"
        "textview text { background: transparent; }",
        -1,
    )
    list_item._text_view.get_style_context().add_provider(
        tv_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    # Create a reusable text tag for message body colour (one per buffer).
    # Avoids calling create_tag on every bind, which would warn and skip
    # property updates when a tag with the same name already exists.
    list_item._body_tag = list_item._text_view.get_buffer().create_tag("body")

    list_item._card.append(list_item._text_view)
    row.append(list_item._card)
    list_item.set_child(row)


def _row_bind(
    factory: Gtk.SignalListItemFactory,
    list_item: Gtk.ListItem,
) -> None:
    """Populate a recycled row with message data (widget template is reused)."""
    msg: _ChatMsg | None = list_item.get_item()
    if msg is None:
        return

    identity = list_item._identity
    user_label = list_item._user_label
    text_view = list_item._text_view
    tag = list_item._body_tag
    card_provider = list_item._card_provider

    dark = getattr(msg, "_dark", False)
    alternating = getattr(msg, "_alternating", False)
    ns = NATIVE_STYLE
    theme = ns["dark"] if dark else ns["light"]

    # ── Card background / radius / padding ──────────────────
    # Alternate the card background based on the row's position in the
    # list store (even positions get card_bg, odd get alt_row).  Suppress
    # hover effects from the theme so the card colour stays stable.
    pos = list_item.get_position()
    use_alt = alternating and pos >= 0 and pos % 2 == 1
    card_bg = theme["alt_row"] if use_alt else theme["card_bg"]
    # Skip reload when the same variant was applied last bind —
    # load_from_data is a parse + re-validate that isn't free.
    if card_bg != getattr(list_item, "_last_card_bg", None):
        list_item._last_card_bg = card_bg
        card_provider.load_from_data(
            f".msg-card {{"
            f"  background: {card_bg};"
            f"  border-radius: {ns['card_radius']}px;"
            f"  margin: {ns['card_margin']};"
            f"  padding: {ns['card_padding']};"
            f"}}"
            f".msg-card:hover {{ background: {card_bg}; }}",
            -1,
        )

    # ── Badges (remove old, add fresh) ──────────────────────
    while True:
        child: Gtk.Widget | None = identity.get_first_child()
        if child is None or child == user_label:
            break
        identity.remove(child)

    last_badge = None
    for display_name, badge_id in getattr(msg, "badges", []):
        svg_data: str | None = _BADGE_SVGS.get(badge_id)
        if svg_data is None:
            continue
        gfile = _make_badge_tempfile(badge_id, svg_data)
        if gfile is not None:
            badge = Gtk.Picture.new_for_file(gfile)
            badge.set_size_request(int(ns["badge_size"]), int(ns["badge_size"]))
            badge.set_valign(Gtk.Align.START)
            badge.set_tooltip_text(display_name)
            identity.insert_child_after(badge, last_badge)
            last_badge = badge

    # ── Username label ──────────────────────────────────────
    color_str = getattr(msg, "color", FALLBACK_USER_COLOR)
    clamped = _clamp_color(color_str, dark)
    user_name = getattr(msg, "user", "")
    is_action = getattr(msg, "action", False)
    user_label.set_markup(
        f'<span font_weight="{ns["user_weight"]}" '
        f'foreground="{_rgba_to_hex(clamped)}">'
        f"{GLib.markup_escape_text(user_name)}"
        f"{'' if is_action else ':'}"
        f"</span>"
    )

    # ── Message body text + emotes ──────────────────────────
    buffer = text_view.get_buffer()
    buffer.set_text("", 0)

    tag.set_property("foreground", theme["text_color"])

    text_view.remove_css_class("italic")
    if is_action:
        text_view.add_css_class("italic")

    segments = getattr(
        msg,
        "segments",
        [{"type": "text", "content": getattr(msg, "text", "")}],
    )

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
            pic.set_valign(Gtk.Align.CENTER)
            tooltip = f"{seg['name']} ({seg['source']})"
            pic.set_tooltip_text(tooltip)
            text_view.add_child_at_anchor(pic, anchor)
            _EMOTE_CACHE.request(seg["url"], pic)

    # After rewriting the buffer the row height may have changed (e.g.
    # recycled from a tall multi-line message to a short single-line one).
    # Without a resize the ListView keeps the old measured height, leaving
    # blank space until the next batch forces a global relayout.
    text_view.queue_resize()


# ── Temporary badge files ────────────────────────────────────
# Gtk.Picture can render SVGs when loaded from a file, but not from
# in-memory bytes.  We write each badge SVG to a temp file once.

_badge_tempdir_path: str | None = None


def _badge_tempdir() -> str:
    global _badge_tempdir_path
    if _badge_tempdir_path is None:
        _badge_tempdir_path = _tempfile.mkdtemp(prefix="streamline-badges-")
    return _badge_tempdir_path


def _make_badge_tempfile(badge_id: str, svg_data: str) -> Gio.File | None:
    d = _badge_tempdir()
    path = Path(d) / f"{badge_id}.svg"
    if not path.exists():
        try:
            path.write_text(svg_data)
        except OSError:
            return None
    return Gio.File.new_for_path(str(path))


def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
    r = int(rgba.red * 255)
    g = int(rgba.green * 255)
    b = int(rgba.blue * 255)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── The page ─────────────────────────────────────────────────


class NativeChatPage(Adw.NavigationPage):
    """A read-only Twitch chat page rendered with native GTK ListView.

    Constructor signature matches ``ChatPage`` so callers can
    trivially switch between the two implementations.
    """

    def __init__(
        self,
        parent,
        streamer,
        display_name=None,
        alternating_bg=False,
        disable_emote_animations=False,
        theme="system",
        twitch=None,
        enable_detach=False,
    ):
        super().__init__(title=_("Chat: {}").format(display_name or streamer))

        from weakref import proxy

        self.parent = proxy(parent) if parent is not None else None
        self._streamer = streamer
        self._alternating_bg = alternating_bg
        self._disable_emote_animations = disable_emote_animations
        self._twitch = twitch
        self._enable_detach = enable_detach
        self._chat: TwitchChat | None = None
        self._third_party_emotes: ThirdPartyEmotes | None = None
        self._dark = theme != "light"
        self._auto_scroll = True
        self._batch_flush_id: int | None = None
        self._msg_batch: list[dict] = []
        self._item_count = 0

        # Style manager for theme changes
        self._style_manager = Adw.StyleManager.get_default()
        self._style_manager.connect("notify::dark", self._on_theme_changed)

        # ── Data model ──────────────────────────────────────
        self._store = Gio.ListStore.new(_ChatMsg)

        # ── ListView factory ────────────────────────────────
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", _row_setup)
        factory.connect("bind", _row_bind)
        self._list_view = Gtk.ListView.new(Gtk.NoSelection.new(self._store), factory)
        self._list_view.set_vexpand(True)
        self._list_view.set_hexpand(True)

        # ── Scrolled window ─────────────────────────────────
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        self._scrolled.set_child(self._list_view)

        vadjustment = self._scrolled.get_vadjustment()
        vadjustment.connect("value-changed", self._on_scroll_value_changed)

        scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_ctrl.connect("scroll", self._on_scroll_event)
        self._scrolled.add_controller(scroll_ctrl)

        # ── "More messages below" banner ────────────────────
        self._more_button = Gtk.Button(
            child=Gtk.Label(label=_("More messages below")),
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
            visible=False,
        )
        self._more_button.add_css_class("more-msg-banner")
        self._more_button.add_css_class("suggested-action")
        self._more_button.set_margin_bottom(8)
        self._more_button.connect("clicked", self._on_more_clicked)

        overlay = Gtk.Overlay()
        overlay.set_child(self._scrolled)
        overlay.add_overlay(self._more_button)
        overlay.set_measure_overlay(self._more_button, True)

        self._apply_banner_style()

        # ── Toolbar ─────────────────────────────────────────
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_back_button(True)

        if enable_detach:
            detach_button = Gtk.Button(
                icon_name="window-new-symbolic",
                tooltip_text=_("Detach chat"),
            )
            detach_button.add_css_class("flat")
            detach_button.connect("clicked", self._on_detach)
            header.pack_end(detach_button)

        toolbar.add_top_bar(header)
        toolbar.set_content(overlay)
        self.set_child(toolbar)

        self.connect("hidden", self._on_hidden)
        self.connect("map", self._on_map)

        # ── Load emotes → start IRC ─────────────────────────
        user_id = None
        twitch_api = self._twitch
        if twitch_api is None and self.parent is not None:
            twitch_api = getattr(self.parent, "twitch", None)
        if twitch_api is not None:
            user_cache = getattr(twitch_api, "user_cache", {})
            user_id = user_cache.get(streamer, {}).get("id")

        self._third_party_emotes = ThirdPartyEmotes(
            user_id,
            prefer_static=self._disable_emote_animations,
        )

        def _load_then_connect():
            try:
                self._third_party_emotes.load()
            except Exception as exc:
                logger.warning("Emote loading failed for #%s: %s", streamer, exc)
            self._chat = TwitchChat(
                streamer,
                on_message=self._on_message,
                prefer_static_emotes=self._disable_emote_animations,
            )
            self._chat.start()

        threading.Thread(target=_load_then_connect, daemon=True).start()

    # ── Message processing (same logic as ChatPage) ──────────

    def _on_message(self, msg: dict) -> None:
        self._item_count += 1

        # Cull oldest rows when past the limit
        if self._item_count > MAX_MESSAGES:
            excess = self._item_count - MAX_MESSAGES
            to_remove = min(excess + CULL_CHUNK, CULL_CHUNK * 4)
            for _ in range(to_remove):
                if self._store.get_n_items() > 0:
                    self._store.remove(0)
            self._item_count -= to_remove

        emotes = list(msg["emotes"])
        if self._third_party_emotes:
            emotes.extend(self._third_party_emotes.find_emotes(msg["text"]))

        segments = _build_segments(msg["text"], emotes)

        self._msg_batch.append(
            dict(
                user=msg["user"],
                text=msg["text"],
                color=msg["color"],
                segments=segments,
                badges=msg.get("badges", []),
                action=msg.get("action", False),
                _dark=self._dark,
                _alternating=self._alternating_bg,
            )
        )

        if self._batch_flush_id is None:
            self._batch_flush_id = GLib.timeout_add(FLUSH_MS, self._flush_messages)

    def _flush_messages(self) -> bool:
        if not self._msg_batch:
            self._batch_flush_id = None
            return GLib.SOURCE_REMOVE

        batch = self._msg_batch
        self._msg_batch = []
        self._batch_flush_id = None

        for kwargs in batch:
            self._store.append(_ChatMsg(**kwargs))

        if self._auto_scroll:
            # Retry scroll every ~16 ms until the adjustment upper settles.
            # New rows need a measure pass (text wrap, emote sizes) before the
            # upper is final; jumping once to a stale value causes bounce.
            GLib.timeout_add(16, self._scroll_to_bottom)

        return GLib.SOURCE_REMOVE

    # ── Scrolling ───────────────────────────────────────────

    def _on_scroll_value_changed(self, adjustment: Gtk.Adjustment) -> None:
        at_bottom = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 2.0
        )
        if at_bottom:
            self._auto_scroll = True
            self._more_button.set_visible(False)

    def _on_scroll_event(
        self,
        controller: Gtk.EventControllerScroll,
        dx: float,
        dy: float,
    ) -> bool:
        if dy > 0:  # scrolling *down*
            adj = self._scrolled.get_vadjustment()
            if adj.get_value() + adj.get_page_size() >= adj.get_upper() - 2.0:
                self._auto_scroll = True
                self._more_button.set_visible(False)
        elif dy < 0:  # scrolling *up*
            self._auto_scroll = False
            self._more_button.set_visible(True)
        return False  # don't swallow the event

    def _scroll_to_bottom(self) -> bool:
        adj = self._scrolled.get_vadjustment()
        target = adj.get_upper() - adj.get_page_size()
        if target > adj.get_value() + 1.0:
            adj.set_value(target)
            return GLib.SOURCE_CONTINUE
        return GLib.SOURCE_REMOVE

    def _on_more_clicked(self, button: Gtk.Button) -> None:
        self._auto_scroll = True
        self._more_button.set_visible(False)
        GLib.idle_add(self._scroll_to_bottom, priority=GLib.PRIORITY_LOW)

    # ── Theme ───────────────────────────────────────────────

    def _on_theme_changed(self, style_manager: Adw.StyleManager, _pspec) -> None:
        dark = style_manager.get_dark()
        self._dark = dark
        self._apply_banner_style()
        # Replace all stored messages with fresh instances carrying the
        # updated theme flag.  Using splice() with new GObjects forces
        # Gtk.ListView to re-bind every visible row (items_changed with
        # the same object pointers skips bind).  Restore scroll position
        # afterwards since the list view anchor is invalidated.
        n = self._store.get_n_items()
        if n == 0:
            return
        vadj = self._scrolled.get_vadjustment()
        saved_scroll = vadj.get_value()
        new_items = []
        for i in range(n):
            item = self._store.get_item(i)
            if item is None:
                continue
            new_items.append(
                _ChatMsg(
                    user=getattr(item, "user", ""),
                    text=getattr(item, "text", ""),
                    color=getattr(item, "color", ""),
                    segments=getattr(item, "segments", []),
                    badges=getattr(item, "badges", []),
                    action=getattr(item, "action", False),
                    _dark=dark,
                    _alternating=getattr(item, "_alternating", False),
                )
            )
        self._store.splice(0, n, new_items)
        GLib.idle_add(lambda: vadj.set_value(saved_scroll))

    def _apply_banner_style(self) -> None:
        ns = NATIVE_STYLE
        theme = ns["dark"] if self._dark else ns["light"]
        provider = Gtk.CssProvider()
        provider.load_from_data(
            f".more-msg-banner {{ "
            f"  font: {ns['banner_font']}; "
            f"  padding: {ns['banner_padding']}; "
            f"  background: {theme['banner_bg']}; "
            f"  color: {theme['banner_fg']}; "
            f"}}",
            -1,
        )
        ctx = self._more_button.get_style_context()
        ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # Also set text color on the label child
        if self._more_button.get_child():
            self._more_button.get_child().set_css_classes([])

    # ── Lifecycle ───────────────────────────────────────────

    def _on_map(self, _widget) -> None:
        pass  # no deferred signal connection needed here

    def _on_hidden(self, page) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Stop chat and release resources.  Idempotent."""
        if self._style_manager is not None:
            self._style_manager.disconnect_by_func(self._on_theme_changed)
            self._style_manager = None
        if self._batch_flush_id is not None:
            GLib.source_remove(self._batch_flush_id)
            self._batch_flush_id = None
            self._flush_messages()
        if self._chat:
            self._chat.stop()
            self._chat = None
        if self._list_view:
            self._list_view.set_model(None)
            self._list_view = None
        self._store.remove_all()

    def _on_detach(self, button: Gtk.Button) -> None:
        """Open the chat in a separate window and pop this page."""
        if self.parent is None:
            return
        parent = self.parent
        root = self.get_root()
        from ..chat_window import ChatWindow as ChatWindow

        popup = ChatWindow(
            twitch=getattr(parent, "twitch", None),
            streamer=self._streamer,
            alternating_bg=self._alternating_bg,
            disable_emote_animations=self._disable_emote_animations,
            theme="dark" if self._dark else "light",
            transient_for=root,
            native_engine=True,
        )
        popup.connect(
            "close-request",
            lambda w, s=self._streamer: (
                getattr(parent, "_active_chats", {}).pop(s, None),
                False,
            )[-1],
        )
        getattr(parent, "_active_chats", {})[self._streamer] = popup
        popup.present()

        parent.navigation_view.pop()
