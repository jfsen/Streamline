"""Alternative chat page using native GTK widgets instead of WebKit.

Cards are appended directly to a Gtk.Box inside a ScrolledWindow — no
ListView, no row recycling.  This avoids the measurement / resize-timing
bugs that plague Gtk.TextView inside recycled list rows.
"""

import gettext
import hashlib
import logging
import tempfile as _tempfile
import threading
from collections import OrderedDict, deque
from pathlib import Path

import requests
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

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
      Excess entries are evicted oldest-first.

    * *On-disk* — raw image bytes are stored at
      ``~/.cache/Streamline/emotes/images/<url-hash>`` so a texture
      survives app restarts without re-downloading.

    Decodes via Pillow so every format (WebP, GIF, PNG, JPEG) works
    regardless of which gdk-pixbuf loaders the system ships.
    """

    def __init__(self):
        # Cached value is either a Gdk.Texture (static) or a
        # list of (Gdk.Texture, delay_ms) tuples (animated).
        self._textures: OrderedDict[str, Gdk.Texture | list] = OrderedDict()
        self._pending: dict[str, list[tuple[Gtk.Widget, bool]]] = {}
        self._lock = threading.Lock()
        _EMOTE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _url_hash(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _disk_path(self, url: str) -> Path:
        return _EMOTE_IMAGE_DIR / self._url_hash(url)

    def request(self, url: str, widget: Gtk.Widget) -> None:
        with self._lock:
            if url in self._textures:
                texture = self._textures.pop(url)
                self._textures[url] = texture
                GLib.idle_add(_apply_texture, widget, url, texture)
                return

            if url in self._pending:
                self._pending[url].append((widget, False))
                return

            self._pending[url] = [(widget, False)]

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
            try:
                self._disk_path(url).write_bytes(data)
            except OSError as exc:
                logger.debug("Failed to write emote to disk %s: %s", url, exc)
        except Exception as exc:
            logger.debug("Failed to download emote %s: %s", url, exc)

        GLib.idle_add(self._on_data, url, data)

    def _on_data(self, url: str, data: bytes | None) -> bool:
        decoded = None
        if data:
            decoded = self._decode(data)
            if decoded is None:
                logger.debug("Failed to decode emote %s", url)

        with self._lock:
            if decoded is not None:
                self._textures[url] = decoded
                self._textures.move_to_end(url)
                while len(self._textures) > _MAX_EMOTE_TEXTURES:
                    self._textures.popitem(last=False)
            widgets = self._pending.pop(url, [])

        if decoded is not None:
            for widget, _replaced in widgets:
                _apply_texture(widget, url, decoded)

        return GLib.SOURCE_REMOVE

    @staticmethod
    def _decode(data: bytes) -> Gdk.Texture | list | None:
        """Decode to a static Gdk.Texture, or a list of
        (Gdk.Texture, delay_ms) frames for animated images."""
        from io import BytesIO

        from PIL import Image

        logging.getLogger("PIL").setLevel(logging.WARNING)

        try:
            img = Image.open(BytesIO(data))
        except Exception as exc:
            logger.debug("Emote decode failed: %s", exc)
            return None

        # Animated (GIF / APNG / WebP) — extract all frames.
        # Falls back to a single static frame if Pillow cannot
        # iterate the animation on this platform.
        if getattr(img, "is_animated", False):
            return _decode_animated_frames(img)

        # Static image — encode as PNG for Gdk.Texture
        png_buf = BytesIO()
        img.save(png_buf, format="PNG")
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(png_buf.getvalue()))


def _apply_texture(widget: Gtk.Widget, url: str, data: Gdk.Texture | list) -> bool:
    """Set an emote's texture; *data* is either a static Gdk.Texture
    or a list of (texture, delay_ms) tuples for animated GIFs.

    Returns immediately if the widget has been removed from the
    tree (e.g. culled while the emote was downloading).
    """
    if not widget.get_root():
        return GLib.SOURCE_REMOVE

    if isinstance(data, Gdk.Texture):
        widget.set_paintable(data)
    else:
        _anim_register(widget, url, data)
    widget.queue_resize()
    return GLib.SOURCE_REMOVE


# ── Animated GIF frame extraction ─────────────────────────────


def _decode_animated_frames(img) -> list:
    """Extract every frame from an animated GIF via Pillow.

    Returns a list of (Gdk.Texture, delay_ms).  Frame delays
    below 20 ms are clamped to 50 ms to avoid burning CPU.
    """
    from io import BytesIO

    frames = []
    try:
        while True:
            delay_cs = img.info.get("duration", 100)
            if delay_cs <= 0:
                delay_cs = 100
            delay_ms = max(int(delay_cs), 50)

            png_buf = BytesIO()
            img.save(png_buf, format="PNG")
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png_buf.getvalue()))
            frames.append((texture, delay_ms))

            img.seek(img.tell() + 1)
    except EOFError:
        pass
    except Exception as exc:
        logger.debug("Failed to extract GIF frame: %s", exc)

    return frames if frames else []


_EMOTE_CACHE = EmoteTextureCache()


# ── Shared animation registry ─────────────────────────────────
# One timer per unique emote URL; all visible instances stay
# frame-synced and only one timeout is active per URL.

_anim_registry: dict[str, dict] = {}


def _is_scrolled_visible(widget: Gtk.Widget) -> bool:
    """Return True if *widget* intersects the visible area of its
    associated GtkScrolledWindow.

    Caches the last result on ``widget._last_visible`` to avoid
    repeated coordinate translation on every frame tick.
    """
    scrolled = getattr(widget, "_scrolled_window", None)
    if scrolled is None:
        return True  # no scrolled window set — assume visible

    if not scrolled.get_realized():
        return getattr(widget, "_last_visible", True)

    result = widget.translate_coordinates(scrolled, 0, 0)
    if not result:
        return getattr(widget, "_last_visible", True)

    # PyGObject returns a plain (x, y), (ok, (x, y)), or
    # (ok, x, y) depending on the binding version.
    if len(result) == 2:
        a, b = result
        if isinstance(b, (tuple, list)) and len(b) == 2:
            ok, (x, y) = result
        else:
            ok, x, y = True, a, b
    elif len(result) == 3:
        ok, x, y = result
    else:
        return getattr(widget, "_last_visible", True)

    if not ok:
        return getattr(widget, "_last_visible", True)

    widget_h = widget.get_allocated_height()
    scrolled_h = scrolled.get_allocated_height()
    visible = y + widget_h > 0 and y < scrolled_h
    widget._last_visible = visible
    return visible


def _anim_shared_tick(url: str) -> bool:
    """Advance the shared animation for *url* by one frame."""
    info = _anim_registry.get(url)
    if info is None:
        return GLib.SOURCE_REMOVE

    frames = info["frames"]
    idx = (info["frame_idx"] + 1) % len(frames)
    info["frame_idx"] = idx
    texture, delay = frames[idx]

    dead = []
    for widget in info["widgets"]:
        if getattr(widget, "_anim_paused", False):
            continue
        if not _is_scrolled_visible(widget):
            continue
        try:
            widget.set_paintable(texture)
        except Exception:
            dead.append(widget)
    for w in dead:
        info["widgets"].discard(w)

    if not info["widgets"]:
        _anim_registry.pop(url, None)
        return GLib.SOURCE_REMOVE

    if delay > 0:
        info["timer_id"] = GLib.timeout_add(delay, _anim_shared_tick, url)
    return GLib.SOURCE_REMOVE


def _anim_register(widget: Gtk.Picture, url: str, frames: list) -> None:
    """Register *widget* in the shared animation for *url*."""
    if not frames:
        return

    widget._anim_url = url
    widget._anim_paused = False

    if url not in _anim_registry:
        _anim_registry[url] = {
            "frames": frames,
            "widgets": {widget},
            "timer_id": None,
            "frame_idx": 0,
        }
        texture, delay = frames[0]
        widget.set_paintable(texture)
        if delay > 0:
            _anim_registry[url]["timer_id"] = GLib.timeout_add(
                delay, _anim_shared_tick, url
            )
    else:
        info = _anim_registry[url]
        info["widgets"].add(widget)
        texture, _ = frames[info["frame_idx"]]
        widget.set_paintable(texture)

    # One-shot signal wiring (skip duplicate connections).
    for func in (_on_anim_map, _on_anim_unmap, _on_anim_destroy):
        try:
            widget.disconnect_by_func(func)
        except TypeError:
            pass
    widget.connect("map", _on_anim_map)
    widget.connect("unmap", _on_anim_unmap)
    widget.connect("destroy", _on_anim_destroy)


def _on_anim_map(widget: Gtk.Picture) -> None:
    """Sync widget to current shared frame when becoming visible."""
    if not getattr(widget, "_anim_paused", False):
        return
    widget._anim_paused = False
    url = getattr(widget, "_anim_url", None)
    if url is None:
        return
    info = _anim_registry.get(url)
    if info is not None:
        texture, _ = info["frames"][info["frame_idx"]]
        widget.set_paintable(texture)


def _on_anim_unmap(widget: Gtk.Picture) -> None:
    """Mark widget paused — shared timer will skip it."""
    widget._anim_paused = True


def _on_anim_destroy(widget: Gtk.Picture) -> None:
    """Remove widget from shared animation; stop timer if last."""
    _anim_unregister(widget)


def _anim_unregister(widget: Gtk.Widget) -> None:
    """Explicitly remove *widget* from the shared animation
    registry.  Idempotent — safe to call on any widget.

    Used when culling or cleaning up widgets that haven't been
    fully destroyed (``unrealize`` doesn't emit ``destroy``).
    """
    url = getattr(widget, "_anim_url", None)
    if url is None:
        return
    info = _anim_registry.get(url)
    if info is None:
        return
    info["widgets"].discard(widget)
    if not info["widgets"]:
        tid = info.get("timer_id")
        if tid is not None:
            GLib.source_remove(tid)
        _anim_registry.pop(url, None)


def _anim_unregister_tree(root: Gtk.Widget) -> None:
    """Recursively walk *root*'s descendants and unregister
    any animated emote Picture widgets from ``_anim_registry``."""
    _anim_unregister(root)
    child = root.get_first_child()
    while child is not None:
        _anim_unregister_tree(child)
        child = child.get_next_sibling()


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
    lightness = (max_c + min_c) / 2.0

    if max_c == min_c:
        h = 0.0
        s = 0.0
    else:
        d = max_c - min_c
        s = d / (2.0 - max_c - min_c) if lightness > 0.5 else d / (max_c + min_c)
        if max_c == r:
            h = ((g - b) / d + (6.0 if g < b else 0.0)) / 6.0
        elif max_c == g:
            h = ((b - r) / d + 2.0) / 6.0
        else:
            h = ((r - g) / d + 4.0) / 6.0

    lightness = max(lightness, 0.78) if dark else min(lightness, 0.28)

    if s == 0:
        rr = gg = bb = lightness
    else:
        c = (1.0 - abs(2.0 * lightness - 1.0)) * s
        x = c * (1.0 - abs((h * 6.0) % 2.0 - 1.0))
        m = lightness - c / 2.0
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
    """
    if not emotes:
        return [{"type": "text", "content": text}]

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
            continue
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


# ── Temporary badge files ────────────────────────────────────

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
    """A read-only Twitch chat page rendered with native GTK widgets.

    Constructor signature matches ``ChatPage`` so callers can trivially
    switch between the two implementations.
    """

    # ── constructor ─────────────────────────────────────────

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
        self._display_name = display_name
        self._alternating_bg = alternating_bg
        self._disable_emote_animations = disable_emote_animations

        self._chat: TwitchChat | None = None
        self._third_party_emotes: ThirdPartyEmotes | None = None
        self._dark = theme != "light"
        self._batch_flush_id: int | None = None
        self._msg_batch: list[dict] = []
        self._item_count = 0
        self._next_is_alt = False  # alternating bg toggle

        # ── Scroll state ────────────────────────────────────
        # ``_auto_scroll``: True when the viewport is pinned to
        # the bottom (default).  Set to False by the scroll
        # controller when the user scrolls up past a threshold;
        # set back to True by ``_on_scroll_value_changed`` when
        # the viewport reaches the bottom again.
        #
        # ``_scroll_gen``: incremented before every scroll
        # operation (flush, "More" click).  Retry callbacks
        # carry a copy of the generation at scheduling time and
        # bail out if it no longer matches — this prevents stale
        # retries from fighting a newer operation.
        #
        # ``_suppress_scroll_signal``: True while
        # ``adj.set_value()`` is called programmatically.
        # ``_on_scroll_value_changed`` checks this flag and
        # returns early so programmatic adjustments never
        # accidentally toggle ``_auto_scroll``.
        self._auto_scroll = True
        self._scroll_gen = 0
        self._suppress_scroll_signal = False
        self._cull_in_progress = False  # guards auto-scroll re-enable during culling

        # Store all card widgets so we can update them on theme change.
        self._cards: deque[Gtk.Widget] = deque()

        # Style manager for theme changes
        self._style_manager = Adw.StyleManager.get_default()
        self._style_manager.connect("notify::dark", self._on_theme_changed)

        # ── Shared CSS providers ────────────────────────────
        # Updated on theme change; all cards / text-views reuse
        # these instead of creating a provider per widget.
        self._card_css_provider = Gtk.CssProvider()
        self._tv_css_provider = Gtk.CssProvider()
        self._tv_css_provider.load_from_data(
            "textview { background: transparent; }"
            "textview text { background: transparent; }",
            -1,
        )

        # ── Message container (plain vertical Box) ───────────
        self._msg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._msg_box.set_halign(Gtk.Align.FILL)

        # ── Scrolled window ─────────────────────────────────
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        self._scrolled.set_child(self._msg_box)

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
        self._update_card_css()

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
        twitch_api = twitch
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

    # ── Card CSS ─────────────────────────────────────────────

    def _update_card_css(self) -> None:
        """Rebuild the shared card CSS provider for the current theme."""
        ns = NATIVE_STYLE
        theme = ns["dark"] if self._dark else ns["light"]
        # Use two classes so alternating rows can pick a different bg.
        self._card_css_provider.load_from_data(
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
            f".msg-card:hover {{ background: {theme['card_bg']}; }}"
            f".msg-card-alt:hover {{ background: {theme['alt_row']}; }}",
            -1,
        )

    # ── Card builder ─────────────────────────────────────────

    def _build_card(self, msg: dict) -> Gtk.Widget:
        """Create one message card widget from the raw message dict."""
        ns = NATIVE_STYLE
        theme = ns["dark"] if self._dark else ns["light"]

        # ── Card frame ───────────────────────────────────────
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("msg-card")
        card.get_style_context().add_provider(
            self._card_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # ── Identity (badges + username) ────────────────────
        identity = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=int(ns["badge_spacing"]),
        )
        identity.set_valign(Gtk.Align.START)

        # Badges
        for display_name, badge_id in msg.get("badges", []):
            svg_data: str | None = _BADGE_SVGS.get(badge_id)
            if svg_data is None:
                continue
            gfile = _make_badge_tempfile(badge_id, svg_data)
            if gfile is not None:
                badge = Gtk.Picture.new_for_file(gfile)
                badge.set_size_request(int(ns["badge_size"]), int(ns["badge_size"]))
                badge.set_valign(Gtk.Align.START)
                badge.set_tooltip_text(display_name)
                identity.append(badge)

        # Username
        color_str = msg.get("color", FALLBACK_USER_COLOR)
        clamped = _clamp_color(color_str, self._dark)
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

        # ── Body (TextView with inline emote anchors) ─────────
        segments = msg.get("segments", [{"type": "text", "content": msg["text"]}])

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        text_view.set_halign(Gtk.Align.FILL)
        text_view.set_valign(Gtk.Align.FILL)
        text_view.set_top_margin(2)
        text_view.set_bottom_margin(2)

        text_view.get_style_context().add_provider(
            self._tv_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
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
                pic.set_valign(Gtk.Align.CENTER)
                # Stash the scrolled window so the animation tick can
                # skip frames when this widget is scrolled out of view.
                pic._scrolled_window = self._scrolled
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

    # ── Message processing ────────────────────────────────────

    def _on_message(self, msg: dict) -> None:
        self._item_count += 1
        # Culling is deferred to ``_flush_messages`` so that removal
        # and addition happen in the same operation with a single
        # scroll-to-bottom, eliminating the jitter caused by a
        # separate cull pass shrinking the content between frames.

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
            )
        )

        if self._batch_flush_id is None:
            self._batch_flush_id = GLib.timeout_add(FLUSH_MS, self._flush_messages)

    def _flush_messages(self) -> bool:
        """Cull excess messages then append batched cards.

        Culling is done here (rather than in ``_on_message``) so that
        removal from the top and addition at the bottom are a single
        logical operation with one scroll-to-bottom at the end.
        """
        if not self._msg_batch:
            self._batch_flush_id = None
            return GLib.SOURCE_REMOVE

        batch = self._msg_batch
        self._msg_batch = []
        self._batch_flush_id = None

        self._scroll_gen += 1
        gen = self._scroll_gen
        was_auto = self._auto_scroll

        # ── Cull excess before adding ──────────────────────────
        if self._item_count > MAX_MESSAGES:
            adj = self._scrolled.get_vadjustment()
            if not was_auto:
                pre_value = adj.get_value()

            self._suppress_scroll_signal = True
            self._cull_in_progress = True
            try:
                culled_total_height = 0
                while self._item_count > MAX_MESSAGES:
                    culled = 0
                    while culled < CULL_CHUNK:
                        first = self._msg_box.get_first_child()
                        if first is None:
                            break
                        culled_total_height += first.get_allocated_height()
                        self._msg_box.remove(first)
                        if self._cards and self._cards[0] is first:
                            self._cards.popleft()
                        elif first in self._cards:
                            self._cards.remove(first)
                        _anim_unregister_tree(first)
                        first.unrealize()
                        self._item_count -= 1
                        culled += 1
                    if culled == 0:
                        break

                if not was_auto and culled_total_height > 0:
                    target = max(0.0, pre_value - culled_total_height)
                    adj.set_value(target)
            finally:
                self._cull_in_progress = False
                self._suppress_scroll_signal = False

        # ── Append new cards ─────────────────────────────────
        for msg_data in batch:
            card = self._build_card(msg_data)

            # Alternating background: simple toggle, independent of
            # culling or global indices.
            if self._alternating_bg and self._next_is_alt:
                card.remove_css_class("msg-card")
                card.add_css_class("msg-card-alt")
            self._next_is_alt = not self._next_is_alt

            self._msg_box.append(card)
            self._cards.append(card)

        # ── Resize + scroll ──────────────────────────────────
        GLib.idle_add(self._msg_box.queue_resize)

        if was_auto:
            GLib.timeout_add(16, self._scroll_to_bottom, gen, 0)

        return GLib.SOURCE_REMOVE

    # ── Scrolling ───────────────────────────────────────────

    def _on_scroll_value_changed(self, adjustment: Gtk.Adjustment) -> None:
        """Re-enable auto-scroll when the viewport reaches the bottom.

        Only *enables* auto-scroll — never disables it.  Disabling is
        done by ``_on_scroll_event`` (user-initiated scroll-up).

        Returns immediately when ``_suppress_scroll_signal`` is True so
        that programmatic ``adj.set_value()`` calls during culling or
        scroll-to-bottom retries don't accidentally toggle state.
        """
        if self._suppress_scroll_signal:
            return

        at_bottom = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 2.0
        )
        if at_bottom and not self._auto_scroll and not self._cull_in_progress:
            self._auto_scroll = True
            self._more_button.set_visible(False)

    def _on_scroll_event(
        self,
        controller: Gtk.EventControllerScroll,
        dx: float,
        dy: float,
    ) -> bool:
        """Disable auto-scroll when the user scrolls up past a threshold.

        A small threshold (30 px) prevents tiny trackpad bumps or
        kinetic-scroll noise from yanking the viewport out of
        auto-scroll mode.
        """
        if dy < 0:
            adj = self._scrolled.get_vadjustment()
            dist_from_bottom = adj.get_upper() - (adj.get_value() + adj.get_page_size())
            if dist_from_bottom > 30.0 and self._auto_scroll:
                self._auto_scroll = False
                self._more_button.set_visible(True)
        return False

    def _scroll_to_bottom(self, gen: int, retry: int) -> bool:
        """Retry-based scroll-to-bottom for the given *gen*.

        Retries up to 3 times (≈48 ms total at 16 ms intervals) so the
        GTK layout phase has time to settle the adjustment's upper bound.
        """
        if gen != self._scroll_gen:
            return GLib.SOURCE_REMOVE
        if retry >= 3:
            return GLib.SOURCE_REMOVE

        self._suppress_scroll_signal = True
        try:
            adj = self._scrolled.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
        finally:
            self._suppress_scroll_signal = False

        GLib.timeout_add(16, self._scroll_to_bottom, gen, retry + 1)
        return GLib.SOURCE_REMOVE

    def _on_more_clicked(self, button: Gtk.Button) -> None:
        """Jump back to the bottom and resume auto-scroll."""
        self._auto_scroll = True
        self._more_button.set_visible(False)

        self._scroll_gen += 1
        gen = self._scroll_gen

        # Immediate scroll (layout is already settled).
        self._suppress_scroll_signal = True
        try:
            adj = self._scrolled.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
        finally:
            self._suppress_scroll_signal = False

        # Retries in case late-loading emotes resize the content.
        GLib.timeout_add(16, self._scroll_to_bottom, gen, 1)

    # ── Theme ───────────────────────────────────────────────

    def _on_theme_changed(self, style_manager: Adw.StyleManager, _pspec) -> None:
        self._dark = style_manager.get_dark()
        self._apply_banner_style()
        self._update_card_css()
        # Rebuild all cards with updated username colours and text colour.
        self._rebuild_all_cards()

    def _rebuild_all_cards(self) -> None:
        """Re-style every visible card for the new theme."""
        for card in self._cards:
            identity = card.get_first_child()
            if identity is not None:
                self._restyle_identity(identity)
            body = identity.get_next_sibling() if identity else None
            if body is not None:
                self._restyle_body(body)

    def _restyle_identity(self, identity: Gtk.Box) -> None:
        """Update the username label colour for the current theme."""
        ns = NATIVE_STYLE

        child = identity.get_last_child()
        if child is None or not isinstance(child, Gtk.Label):
            return

        user_name = getattr(identity, "_user_name", None)
        color_str = getattr(identity, "_user_color", None)
        is_action = getattr(identity, "_is_action", False)
        if user_name is None or color_str is None:
            return

        clamped = _clamp_color(color_str, self._dark)
        child.set_markup(
            f'<span font_weight="{ns["user_weight"]}" '
            f'foreground="{_rgba_to_hex(clamped)}">'
            f"{GLib.markup_escape_text(user_name)}"
            f"{'' if is_action else ':'}"
            f"</span>"
        )

    def _restyle_body(self, text_view: Gtk.TextView) -> None:
        """Update text colour in the TextView body for the new theme."""
        theme = NATIVE_STYLE["dark"] if self._dark else NATIVE_STYLE["light"]
        tag = text_view.get_buffer().get_tag_table().lookup("body")
        if tag is not None:
            tag.set_property("foreground", theme["text_color"])

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
        if self._more_button.get_child():
            self._more_button.get_child().set_css_classes([])

    # ── Lifecycle ───────────────────────────────────────────

    def _on_map(self, _widget) -> None:
        """Reset scroll state when the page becomes visible.

        Called after construction and after the page is re-mapped
        (e.g. returning from a detached window).  Scrolls to the
        bottom so the user sees fresh content immediately.
        """
        self._auto_scroll = True
        self._scroll_gen += 1
        self._suppress_scroll_signal = False
        self._more_button.set_visible(False)
        GLib.idle_add(self._scroll_to_bottom, self._scroll_gen, 0)

    def _on_hidden(self, page) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Stop chat and release resources.  Idempotent."""
        # Invalidate all pending scroll retries before tearing down.
        self._scroll_gen += 1
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
        # Unregister animated emotes and drop all cards.
        while True:
            child = self._msg_box.get_first_child()
            if child is None:
                break
            _anim_unregister_tree(child)
            self._msg_box.remove(child)
        self._cards.clear()
        self._item_count = 0
        self._next_is_alt = False

    def _on_detach(self, button: Gtk.Button) -> None:
        """Open the chat in a separate window and pop this page."""
        if self.parent is None:
            return
        parent = self.parent
        root = self.get_root()
        from ..chat_window import ChatWindow

        popup = ChatWindow(
            twitch=getattr(parent, "twitch", None),
            streamer=self._streamer,
            display_name=self._display_name,
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
