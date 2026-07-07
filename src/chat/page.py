# page.py
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

"""Chat page using native GTK widgets.

Cards are appended directly to a Gtk.Box inside a ScrolledWindow — no
ListView, no row recycling.  This avoids the measurement / resize-timing
bugs that plague Gtk.TextView inside recycled list rows.
"""

import gc
import gettext
import hashlib
import logging
import tempfile as _tempfile
import threading
from collections import OrderedDict, deque
from io import BytesIO
from pathlib import Path

import requests
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango
from PIL import Image

from .config import (
    CHAT_STYLE,
    CULL_CHUNK,
    FALLBACK_USER_COLOR,
    FLUSH_MS,
    MAX_MESSAGES,
)
from .emotes import ThirdPartyEmotes
from .twitch_chat import ConnectionState, TwitchChat

_ = gettext.gettext
logger = logging.getLogger("ChatPage")
logging.getLogger("PIL").setLevel(logging.WARNING)

# ── Badge SVGs (loaded once at module level) ───────────────

_BADGE_DIR = Path(__file__).parent / "badges"
_BADGE_SVGS = {}
for _f in _BADGE_DIR.glob("*.svg"):
    _BADGE_SVGS[_f.stem] = _f.read_text()

# ── Emote image cache ──────────────────────────────────────────

_EMOTE_IMAGE_DIR = Path(GLib.get_user_cache_dir()) / "Streamline" / "emotes" / "images"
_MAX_EMOTE_TEXTURES = 200  # individual Gdk.Texture objects (frames count separately)


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
        # Cached value is either a Gdk.Texture (static) or an
        # AnimatedFrames (animated, frames pre-decoded).
        self._textures: OrderedDict[str, Gdk.Texture | AnimatedFrames] = OrderedDict()
        self._texture_count = 0  # counts individual Gdk.Texture objects
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
                # Apply cached texture: use the synchronous fast-path
                # when the widget is already in the tree, otherwise
                # defer via idle_add (the widget hasn't been parented
                # yet, e.g. during _build_card).
                if widget.get_root() is not None:
                    _apply_texture(widget, url, texture)
                else:
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

    def evict_page(self, page) -> None:
        """Drop pending downloads for widgets belonging to *page*."""
        with self._lock:
            urls_to_drop = []
            for url, widgets in list(self._pending.items()):
                kept = [
                    (w, r) for w, r in widgets
                    if getattr(w, "_page", None) is not page
                ]
                if kept:
                    self._pending[url] = kept
                else:
                    urls_to_drop.append(url)
            for url in urls_to_drop:
                del self._pending[url]

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
                if isinstance(decoded, list):
                    self._texture_count += len(decoded)
                else:
                    # Gdk.Texture or AnimatedFrames — both count as one LRU slot.
                    self._texture_count += 1
                while self._texture_count > _MAX_EMOTE_TEXTURES:
                    _k, old = self._textures.popitem(last=False)
                    if isinstance(old, list):
                        self._texture_count -= len(old)
                    else:
                        self._texture_count -= 1
            widgets = self._pending.pop(url, [])

        if decoded is not None:
            for widget, _replaced in widgets:
                _apply_texture(widget, url, decoded)

        return GLib.SOURCE_REMOVE

    @staticmethod
    def _decode(data: bytes) -> Gdk.Texture | AnimatedFrames | None:
        """Decode to a static Gdk.Texture, or an AnimatedFrames for
        animated images (frames pre-decoded)."""
        try:
            img = Image.open(BytesIO(data))
        except Exception as exc:
            logger.debug("Emote decode failed: %s", exc)
            return None

        # Animated (GIF / APNG / WebP) — decode all frames eagerly
        # so the animation tick only does cheap GPU uploads.
        if getattr(img, "is_animated", False):
            img.close()
            return AnimatedFrames(data)

        # Static image — upload raw RGBA pixels.
        img = img.convert("RGBA")
        w, h = img.size
        return Gdk.MemoryTexture.new(
            w,
            h,
            Gdk.MemoryFormat.R8G8B8A8,
            GLib.Bytes.new(img.tobytes()),
            w * 4,
        )


# ── Lazy animated frame extractor ─────────────────────────────


class AnimatedFrames:
    """Eagerly decoded animated emote frames.

    All frames are decoded to raw RGBA pixel data during initial
    decode so that the animation tick only has to create
    Gdk.MemoryTexture objects (a cheap GPU upload) — no PIL
    seek/convert operations on the main thread.

    GPU textures are still created lazily in get_frame() so that
    frames that are never displayed never consume GPU memory.
    """

    def __init__(self, raw_data: bytes):
        self._rgba_frames: list[tuple[bytes, int, int, int]] = []
        self._textures: dict[int, Gdk.Texture] = {}
        self._frame_count = 0
        self._decode_all(raw_data)

    def _decode_all(self, raw_data: bytes) -> None:
        img = Image.open(BytesIO(raw_data))
        try:
            self._frame_count = getattr(img, "n_frames", 1)
            for i in range(self._frame_count):
                try:
                    img.seek(i)
                except Exception as exc:
                    logger.debug("Failed to seek frame %s: %s", i, exc)
                    break
                delay_cs = img.info.get("duration", 100)
                delay_ms = max(int(delay_cs), 20) if delay_cs > 0 else 100
                frame = img.convert("RGBA")
                w, h = frame.size
                self._rgba_frames.append((frame.tobytes(), w, h, delay_ms))
        finally:
            img.close()

    def get_frame(self, idx: int) -> tuple[Gdk.Texture, int]:
        if idx in self._textures:
            return self._textures[idx], self._rgba_frames[idx][3]
        if 0 <= idx < len(self._rgba_frames):
            rgba_bytes, w, h, delay_ms = self._rgba_frames[idx]
            tex = Gdk.MemoryTexture.new(
                w,
                h,
                Gdk.MemoryFormat.R8G8B8A8,
                GLib.Bytes.new(rgba_bytes),
                w * 4,
            )
            self._textures[idx] = tex
            return tex, delay_ms
        # Fallback: transparent 1x1 placeholder.
        tex = Gdk.MemoryTexture.new(
            1,
            1,
            Gdk.MemoryFormat.R8G8B8A8,
            GLib.Bytes.new(b"\x00\x00\x00\x00"),
            4,
        )
        return tex, 100

    def __len__(self) -> int:
        return self._frame_count

    def __iter__(self):
        return iter(range(self._frame_count))


_EMOTE_CACHE = EmoteTextureCache()

# ── Module-level helpers (dispatch via widget._page) ──────────


def _apply_texture(
    widget: Gtk.Picture, url: str, data: Gdk.Texture | AnimatedFrames
) -> bool:
    """Set an emote's texture; *data* is either a static Gdk.Texture
    or an AnimatedFrames for animated images (frames pre-decoded).

    Returns immediately if the widget has been removed from the
    tree (e.g. culled while the emote was downloading).
    """
    if not widget.get_root():
        return GLib.SOURCE_REMOVE
    if getattr(widget, "_unrealized", False):
        return GLib.SOURCE_REMOVE  # C object already destroyed

    if isinstance(data, Gdk.Texture):
        widget.set_paintable(data)
    else:
        page = getattr(widget, "_page", None)
        if page is not None:
            page._anim_register(url, widget, data)
    widget.queue_resize()
    return GLib.SOURCE_REMOVE


def _on_anim_destroy(widget: Gtk.Picture) -> None:
    """Remove widget from its page's animation registry."""
    page = getattr(widget, "_page", None)
    if page is not None:
        page._anim_unregister(widget)


def _teardown_subtree(root: Gtk.Widget, page) -> None:
    """Single-pass recursive teardown of *root* and all descendants.

    Combines what was previously three separate tree walks:

    1. Unregister animated emote widgets from the page's registry.
    2. Disconnect animation signal handlers (map/unmap/destroy).
    3. Clear Gtk.TextView text buffers to free held memory.

    Call this once per card during culling, and once on the root
    ``_msg_box`` during full cleanup.
    """
    # 1 – animation registry
    page._anim_unregister(root)

    # 2 – disconnect signal handlers
    try:
        root.disconnect_by_func(_on_anim_destroy)
    except TypeError:
        pass
    for attr in ("_anim_map_id", "_anim_unmap_id"):
        hid = getattr(root, attr, None)
        if hid is not None and root.handler_is_connected(hid):
            root.disconnect(hid)

    # 3 – clear text buffers
    if isinstance(root, Gtk.TextView):
        buf = root.get_buffer()
        if buf is not None:
            buf.set_text("")

    # Recurse — single walk for all three operations
    child = root.get_first_child()
    while child is not None:
        _teardown_subtree(child, page)
        child = child.get_next_sibling()


def _unrealize_widget(widget: Gtk.Widget) -> bool:
    """Unrealize a widget in an idle callback, safe to call on already-
    destroyed widgets (returns GLib.SOURCE_REMOVE).  Marks the Python
    wrapper as dead afterward so that no other idle callback tries to
    call GTK methods on the now-destroyed C object."""
    try:
        widget.unrealize()
    except Exception:
        pass
    widget._unrealized = True
    return GLib.SOURCE_REMOVE


def _gc_collect_idle() -> bool:
    """Run gc.collect() at idle priority so it doesn't block the UI."""
    gc.collect()
    return GLib.SOURCE_REMOVE


# ── Helpers ──────────────────────────────────────────────────


def _clamp_color(hex_color: str, dark: bool) -> Gdk.RGBA:
    """Clamp a username colour so it remains legible on the current
    background.

    Instead of a full RGB→HSL→RGB round-trip (called once per card),
    we scale the channel values directly toward white (dark theme) or
    black (light theme) when the simple HSL lightness is out of range.
    """
    hex_clean = hex_color.lstrip("#")
    r = int(hex_clean[0:2], 16) / 255.0
    g = int(hex_clean[2:4], 16) / 255.0
    b = int(hex_clean[4:6], 16) / 255.0

    max_c = max(r, g, b)
    min_c = min(r, g, b)
    lightness = (max_c + min_c) / 2.0

    if dark and lightness < 0.78:
        # Scale toward white:  new = old + (1-old) * t
        t = (0.78 - lightness) / (1.0 - lightness) if lightness < 1.0 else 0.0
        r += (1.0 - r) * t
        g += (1.0 - g) * t
        b += (1.0 - b) * t
    elif not dark and lightness > 0.28:
        # Scale toward black:  new = old * t
        t = 0.28 / lightness if lightness > 0.0 else 0.0
        r *= t
        g *= t
        b *= t

    rgba = Gdk.RGBA()
    rgba.red = max(0.0, min(1.0, r))
    rgba.green = max(0.0, min(1.0, g))
    rgba.blue = max(0.0, min(1.0, b))
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


class ChatPage(Adw.NavigationPage):
    """A read-only Twitch chat page rendered with native GTK widgets."""

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
        highlight_first_msg=True,
        highlight_mod=True,
        highlight_vip=True,
        highlight_partner=True,
        highlight_broadcaster=True,
    ):
        super().__init__(title=_("Chat: {}").format(display_name or streamer))

        from weakref import proxy

        self.parent = proxy(parent) if parent is not None else None
        self._streamer = streamer
        self._display_name = display_name
        self._alternating_bg = alternating_bg
        self._disable_emote_animations = disable_emote_animations
        self._highlight_first_msg = highlight_first_msg
        self._highlight_mod = highlight_mod
        self._highlight_vip = highlight_vip
        self._highlight_partner = highlight_partner
        self._highlight_broadcaster = highlight_broadcaster

        self._chat: TwitchChat | None = None
        self._cleaned_up = False
        self._banner_css_provider: Gtk.CssProvider | None = None
        self._third_party_emotes: ThirdPartyEmotes | None = None
        self._dark = (
            Adw.StyleManager.get_default().get_dark() if theme != "light" else False
        )
        self._batch_flush_id: int | None = None
        self._msg_batch: list[dict] = []
        self._item_count = 0
        self._next_is_alt = False  # alternating bg toggle
        self._anim_registry: dict[str, dict] = {}
        self._anim_tick_id: int | None = None
        self._toplevel_active_id: int | None = None
        self._cull_ops = 0
        self._room_state: dict[str, int] = {}
        self._roomstate_popover: Gtk.Popover | None = None

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
        overlay.set_vexpand(True)
        overlay.set_hexpand(True)
        overlay.set_child(self._scrolled)
        overlay.add_overlay(self._more_button)
        overlay.set_measure_overlay(self._more_button, True)

        # ── Reconnect banner ───────────────────────────────
        self._reconnect_revealer = Gtk.Revealer()
        self._reconnect_revealer.set_transition_type(
            Gtk.RevealerTransitionType.SLIDE_DOWN
        )
        self._reconnect_revealer.set_transition_duration(250)

        reconnect_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        reconnect_box.set_margin_start(12)
        reconnect_box.set_margin_end(12)
        reconnect_box.set_margin_top(4)
        reconnect_box.set_margin_bottom(4)
        reconnect_box.add_css_class("reconnect-banner")

        self._reconnect_spinner = Gtk.Spinner()
        self._reconnect_spinner.set_visible(False)
        reconnect_box.append(self._reconnect_spinner)

        self._reconnect_label = Gtk.Label()
        self._reconnect_label.set_halign(Gtk.Align.START)
        self._reconnect_label.set_hexpand(True)
        reconnect_box.append(self._reconnect_label)

        self._reconnect_button = Gtk.Button(label=_("Reconnect"))
        self._reconnect_button.set_visible(False)
        self._reconnect_button.connect("clicked", self._on_reconnect_clicked)
        reconnect_box.append(self._reconnect_button)

        self._reconnect_revealer.set_child(reconnect_box)

        self._apply_banner_style()
        self._update_card_css()

        # ── Content assembly ────────────────────────────────
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_vexpand(True)
        content_box.set_hexpand(True)
        content_box.append(self._reconnect_revealer)
        content_box.append(overlay)

        # ── Toolbar ─────────────────────────────────────────
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_back_button(True)

        info_button = Gtk.Button(
            icon_name="dialog-information-symbolic",
            tooltip_text=_("Room state"),
        )
        info_button.add_css_class("flat")
        info_button.connect("clicked", self._on_info_clicked)
        header.pack_start(info_button)

        if enable_detach:
            detach_button = Gtk.Button(
                icon_name="window-new-symbolic",
                tooltip_text=_("Detach chat"),
            )
            detach_button.add_css_class("flat")
            detach_button.connect("clicked", self._on_detach)
            header.pack_end(detach_button)

        toolbar.add_top_bar(header)
        toolbar.set_content(content_box)
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
            if self._cleaned_up:
                return  # page closed before we finished connecting
            self._chat = TwitchChat(
                streamer,
                on_message=self._on_message,
                prefer_static_emotes=self._disable_emote_animations,
                on_state_change=self._on_irc_state_change,
                on_roomstate=self._on_roomstate,
            )
            self._chat.start()

        threading.Thread(target=_load_then_connect, daemon=True).start()

    # ── Card CSS ─────────────────────────────────────────────

    def _update_card_css(self) -> None:
        """Rebuild the shared card CSS provider for the current theme."""
        ns = CHAT_STYLE
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

    def _build_card(self, msg: dict) -> Gtk.Widget:
        """Create one message card widget from the raw message dict."""
        ns = CHAT_STYLE
        theme = ns["dark"] if self._dark else ns["light"]

        # ── Card frame ───────────────────────────────────────
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("msg-card")
        card.get_style_context().add_provider(
            self._card_css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
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

        # Badges
        for display_name, badge_id, tenure in msg.get("badges", []):
            svg_data: str | None = _BADGE_SVGS.get(badge_id)
            if svg_data is None:
                continue
            gfile = _make_badge_tempfile(badge_id, svg_data)
            if gfile is not None:
                badge = Gtk.Picture.new_for_file(gfile)
                badge.set_size_request(int(ns["badge_size"]), int(ns["badge_size"]))
                badge.set_valign(Gtk.Align.START)
                tooltip = f"{tenure}-month {display_name}" if tenure else display_name
                badge.set_tooltip_text(tooltip)
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
                pic._page = self
                pic._card = card
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
                pic._page = self
                pic._card = card
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
        if self._cleaned_up:
            return
        self._item_count += 1

        is_system = msg.get("system", False)
        if is_system:
            # System messages carry no emotes / badges / user.
            segments = [{"type": "text", "content": msg["text"]}]
            self._msg_batch.append(
                dict(
                    user="",
                    text=msg["text"],
                    color=msg["color"],
                    segments=segments,
                    badges=[],
                    action=False,
                    first_msg=False,
                    mod=False,
                    vip=False,
                    partner=False,
                    broadcaster=False,
                    system=True,
                )
            )
        else:
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
                    first_msg=msg.get("first_msg", False),
                    mod=msg.get("mod", False),
                    vip=msg.get("vip", False),
                    partner=msg.get("partner", False),
                    broadcaster=msg.get("broadcaster", False),
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
                        if not was_auto:
                            culled_total_height += first.get_allocated_height()
                        _teardown_subtree(first, self)
                        self._msg_box.remove(first)
                        if self._cards and self._cards[0] is first:
                            self._cards.popleft()
                        elif first in self._cards:
                            self._cards.remove(first)
                        GLib.idle_add(_unrealize_widget, first)
                        self._item_count -= 1
                        culled += 1
                    if culled == 0:
                        break

                if not was_auto and culled_total_height > 0:
                    # Defer restoration until after the layout pass so
                    # GTK's adjustment has settled on the correct upper
                    # bound.  A synchronous set_value() here would be
                    # overwritten by the pending queue_resize.
                    GLib.idle_add(
                        self._restore_scroll_after_cull,
                        gen,
                        pre_value,
                        culled_total_height,
                    )
            finally:
                self._cull_in_progress = False
                self._suppress_scroll_signal = False
                self._cull_ops += 1
                if self._cull_ops % 5 == 0:
                    GLib.idle_add(_gc_collect_idle)

        # ── Append new cards ─────────────────────────────────
        # Build up to _BUILD_CHUNK cards per iteration so widget
        # creation (Gtk.TextView + child anchors + Pictures) doesn't
        # block the main loop for large batches.
        _BUILD_CHUNK = 5

        if len(batch) > _BUILD_CHUNK:
            chunk = batch[:_BUILD_CHUNK]
            for msg_data in chunk:
                self._append_one_card(msg_data)
            GLib.idle_add(self._continue_flush, batch[_BUILD_CHUNK:], gen, was_auto)
        else:
            for msg_data in batch:
                self._append_one_card(msg_data)
            # ── Scroll ──────────────────────────────────────────
            # The Box is already dirty from remove/append calls;
            # no need for a separate queue_resize — GTK coalesces it.

            if was_auto:
                GLib.timeout_add(16, self._scroll_to_bottom, gen, 0)

        return GLib.SOURCE_REMOVE

    def _append_one_card(self, msg_data: dict) -> None:
        """Build and append a single message card with styling."""
        card = self._build_card(msg_data)

        # Alternating background.
        if self._alternating_bg and self._next_is_alt:
            card.remove_css_class("msg-card")
            card.add_css_class("msg-card-alt")
        self._next_is_alt = not self._next_is_alt

        # Tint overrides — broadcaster > partner > VIP > mod > first-msg.
        msg_first = msg_data.get("first_msg", False)
        msg_mod = msg_data.get("mod", False)
        msg_vip = msg_data.get("vip", False)
        msg_partner = msg_data.get("partner", False)
        msg_bc = msg_data.get("broadcaster", False)
        if msg_bc and self._highlight_broadcaster:
            card.add_css_class("msg-card-broadcaster")
        elif msg_partner and self._highlight_partner:
            card.add_css_class("msg-card-partner")
        elif msg_vip and self._highlight_vip:
            card.add_css_class("msg-card-vip")
        elif msg_mod and self._highlight_mod:
            card.add_css_class("msg-card-mod")
        elif msg_first and self._highlight_first_msg:
            card.add_css_class("msg-card-first")

        self._msg_box.append(card)
        self._cards.append(card)

    def _continue_flush(self, batch: list, gen: int, was_auto: bool) -> bool:
        """Continue appending batch cards across idle iterations."""
        if self._cleaned_up or gen != self._scroll_gen:
            return GLib.SOURCE_REMOVE

        _BUILD_CHUNK = 5
        if len(batch) > _BUILD_CHUNK:
            chunk = batch[:_BUILD_CHUNK]
            for msg_data in chunk:
                self._append_one_card(msg_data)
            GLib.idle_add(self._continue_flush, batch[_BUILD_CHUNK:], gen, was_auto)
        else:
            for msg_data in batch:
                self._append_one_card(msg_data)
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

        Also returns early when auto-scroll is already active, since
        the only thing this handler does is re-enable auto-scroll.
        This saves three C getter calls per scroll event in the
        common auto-scrolling case.
        """
        if self._suppress_scroll_signal or self._auto_scroll:
            return

        at_bottom = (
            adjustment.get_value() + adjustment.get_page_size()
            >= adjustment.get_upper() - 2.0
        )
        if at_bottom and not self._cull_in_progress:
            self._auto_scroll = True
            self._more_button.set_visible(False)

    def _on_scroll_event(
        self,
        controller: Gtk.EventControllerScroll,
        dx: float,
        dy: float,
    ) -> bool:
        """Pause auto-scroll on any upward scroll.

        The moment the user scrolls up (dy < 0) while auto-scroll
        is active, we disable it and show the "more below" button.
        Auto-scroll resumes automatically when the viewport reaches
        the bottom again (see ``_on_scroll_value_changed``).
        """
        if dy < 0 and self._auto_scroll:
            self._auto_scroll = False
            self._more_button.set_visible(True)
        return False

    def _scroll_to_bottom(self, gen: int, retry: int) -> bool:
        """Retry-based scroll-to-bottom for the given *gen*.

        Retries up to 3 times (≈48 ms total at 16 ms intervals) so the
        GTK layout phase has time to settle the adjustment's upper bound.
        """
        if self._cleaned_up or self._scrolled is None:
            return GLib.SOURCE_REMOVE
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

    def _restore_scroll_after_cull(
        self, gen: int, old_value: float, culled_h: float
    ) -> bool:
        """Apply the cull scroll compensation after layout has settled.

        Runs via ``GLib.idle_add`` so that the pending ``queue_resize``
        from ``_flush_messages`` has already updated the adjustment's
        upper bound before we set the value.
        """
        if self._cleaned_up or self._scrolled is None:
            return GLib.SOURCE_REMOVE
        if gen != self._scroll_gen:
            return GLib.SOURCE_REMOVE

        target = max(0.0, old_value - culled_h)
        self._suppress_scroll_signal = True
        try:
            adj = self._scrolled.get_vadjustment()
            adj.set_value(target)
        finally:
            self._suppress_scroll_signal = False

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
            # System messages have only a Label/TextView, no identity row.
            if identity is not None and isinstance(identity, (Gtk.TextView, Gtk.Label)):
                self._restyle_body(identity)
                continue
            if identity is not None:
                self._restyle_identity(identity)
            body = identity.get_next_sibling() if identity else None
            if body is not None:
                self._restyle_body(body)

    def _restyle_identity(self, identity: Gtk.Box) -> None:
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

        clamped = _clamp_color(color_str, self._dark)
        child.set_markup(
            f'<span font_weight="{ns["user_weight"]}" '
            f'foreground="{_rgba_to_hex(clamped)}">'
            f"{GLib.markup_escape_text(user_name)}"
            f"{'' if is_action else ':'}"
            f"</span>"
        )

    def _restyle_body(self, widget: Gtk.Widget) -> None:
        """Update text colour in the body for the new theme.

        Handles Gtk.FlowBox (emote-only — no text to recolor),
        Gtk.Label (text-only / system messages), and
        Gtk.TextView (messages containing emotes).
        """
        if isinstance(widget, Gtk.FlowBox):
            return  # emote-only — no text to update
        theme = CHAT_STYLE["dark"] if self._dark else CHAT_STYLE["light"]
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

    def _apply_banner_style(self) -> None:
        ns = CHAT_STYLE
        theme = ns["dark"] if self._dark else ns["light"]

        # Remove previous provider to avoid accumulating on theme switches.
        if self._banner_css_provider is not None:
            self._more_button.get_style_context().remove_provider(
                self._banner_css_provider
            )
            self._reconnect_revealer.get_style_context().remove_provider(
                self._banner_css_provider
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
        self._banner_css_provider = provider

        ctx = self._more_button.get_style_context()
        ctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        if self._more_button.get_child():
            self._more_button.get_child().set_css_classes([])
        rctx = self._reconnect_revealer.get_style_context()
        rctx.add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

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
        self._anim_start_tick()
        # Watch toplevel focus — after workspace suspend/resume,
        # TextViews may have word-wrapped at a stale width.  Force
        # re-layout as soon as the window becomes active again.
        root = self.get_root()
        if root is not None:
            self._toplevel_active_id = root.connect(
                "notify::is-active", self._on_toplevel_active
            )

    def _on_hidden(self, page) -> None:
        self.cleanup()

    def _on_toplevel_active(self, window, _pspec) -> None:
        """Force re-layout when the window regains focus.

        After a workspace suspend/resume, GTK may have given
        widgets stale (zero-width) allocations.  TextViews that
        word-wrapped at that stale width end up super-tall.
        Queuing a resize here fixes them before the user notices.
        """
        if window.is_active() and self._msg_box is not None:
            self._msg_box.queue_resize()

    def cleanup(self) -> None:
        """Stop chat and release resources.  Idempotent."""
        self._cleaned_up = True
        # Invalidate all pending scroll retries before tearing down.
        self._scroll_gen += 1
        if self._style_manager is not None:
            self._style_manager.disconnect_by_func(self._on_theme_changed)
            self._style_manager = None
        if self._batch_flush_id is not None:
            GLib.source_remove(self._batch_flush_id)
            self._batch_flush_id = None
            self._flush_messages()
        # Stop animation tick and clear registry.
        self._anim_stop_tick()
        # Stop chat — null out the state-change callback first so
        # no idle_add callbacks can fire after we start tearing down.
        if self._chat:
            self._chat._on_state_change = None
            self._chat._on_roomstate = None
            self._chat.stop()
            self._chat = None
        # Disconnect toplevel-active watcher before clearing widgets,
        # so a stray notify::is-active can't touch a freed msg_box.
        if self._toplevel_active_id is not None:
            root = self.get_root()
            if root is not None:
                root.disconnect(self._toplevel_active_id)
            self._toplevel_active_id = None

        # Single-pass teardown of the entire message tree.
        root_box = self._msg_box
        if root_box is not None:
            _teardown_subtree(root_box, self)
            # Unparent all children by detaching from the scrolled window.
            if self._scrolled is not None:
                self._scrolled.set_child(None)

        self._cards.clear()
        self._item_count = 0
        self._next_is_alt = False
        # Drop pending emote downloads so the module-level cache
        # releases all references to this page's Picture widgets.
        _EMOTE_CACHE.evict_page(self)
        self._third_party_emotes = None
        self._card_css_provider = None
        self._tv_css_provider = None
        self._chat = None
        self._scrolled = None
        self._msg_box = None
        if self._roomstate_popover is not None:
            self._roomstate_popover.popdown()
            self._roomstate_popover = None
        GLib.idle_add(_gc_collect_idle)

    # ── Animated emote tick (per-page) ───────────────────────

    _ANIM_TICK_MS = 40  # 25 fps

    def _anim_start_tick(self) -> None:
        if self._anim_tick_id is None:
            self._anim_tick_id = GLib.timeout_add(
                self._ANIM_TICK_MS, self._anim_global_tick
            )

    def _anim_stop_tick(self) -> None:
        if self._anim_tick_id is not None:
            GLib.source_remove(self._anim_tick_id)
            self._anim_tick_id = None
        self._anim_registry.clear()

    def _anim_global_tick(self) -> bool:
        """Single timer for all animated emotes on this page.

        Iterates the page's registry once per tick.  Visibility
        checks use cached card allocations (no tree-walking).

        Bails out during culling — card allocations are in flux
        and the Box layout hasn't settled, so texture updates
        would flicker.
        """
        if self._cull_in_progress:
            return GLib.SOURCE_CONTINUE

        adj = self._scrolled.get_vadjustment()
        value = adj.get_value()
        page_size = adj.get_page_size()

        dead: list[str] = []
        for url, info in self._anim_registry.items():
            info["elapsed"] += self._ANIM_TICK_MS
            frames = info["frames"]
            idx = info["frame_idx"]
            _, delay = frames.get_frame(idx)

            if info["elapsed"] < delay:
                continue

            # Catch up if we fell behind multiple frames.
            while info["elapsed"] >= delay:
                info["elapsed"] -= delay
                info["frame_idx"] = (info["frame_idx"] + 1) % len(frames)
                idx = info["frame_idx"]
                _, delay = frames.get_frame(idx)

            texture, _ = frames.get_frame(idx)
            alive = False
            dead_widgets: list[Gtk.Widget] = []
            for widget in info["widgets"]:
                if getattr(widget, "_anim_paused", False):
                    alive = True
                    continue
                if getattr(widget, "_unrealized", False):
                    dead_widgets.append(widget)
                    continue
                card = getattr(widget, "_card", None)
                if card is not None:
                    alloc = card.get_allocation()
                    if not (
                        alloc.y + alloc.height > value and alloc.y < value + page_size
                    ):
                        alive = True
                        continue
                try:
                    widget.set_paintable(texture)
                    alive = True
                except Exception:
                    dead_widgets.append(widget)
            for w in dead_widgets:
                info["widgets"].discard(w)
            if not alive:
                dead.append(url)

        for url in dead:
            del self._anim_registry[url]

        return GLib.SOURCE_CONTINUE

    def _anim_register(
        self, url: str, widget: Gtk.Picture, frames: AnimatedFrames
    ) -> None:
        """Register *widget* for animated *url* on this page."""
        if not frames:
            return

        widget._anim_url = url
        widget._anim_paused = False

        if url not in self._anim_registry:
            self._anim_registry[url] = {
                "frames": frames,
                "widgets": {widget},
                "frame_idx": 0,
                "elapsed": 0,
            }
            texture, _ = frames.get_frame(0)
            widget.set_paintable(texture)
        else:
            info = self._anim_registry[url]
            info["widgets"].add(widget)
            texture, _ = frames.get_frame(info["frame_idx"])
            widget.set_paintable(texture)

        # One-shot signal wiring (skip duplicate connections).
        for attr in ("_anim_map_id", "_anim_unmap_id", "_anim_destroy_id"):
            hid = getattr(widget, attr, None)
            if hid is not None and widget.handler_is_connected(hid):
                widget.disconnect(hid)
        widget._anim_map_id = widget.connect("map", self._on_anim_map)
        widget._anim_unmap_id = widget.connect("unmap", self._on_anim_unmap)
        widget._anim_destroy_id = widget.connect("destroy", _on_anim_destroy)

    def _anim_unregister(self, widget: Gtk.Widget) -> None:
        """Remove *widget* from this page's animation registry."""
        url = getattr(widget, "_anim_url", None)
        if url is None:
            return
        info = self._anim_registry.get(url)
        if info is None:
            return
        info["widgets"].discard(widget)
        if not info["widgets"]:
            del self._anim_registry[url]

    def _on_anim_map(self, widget: Gtk.Picture) -> None:
        """Sync widget to current shared frame when becoming visible."""
        if getattr(widget, "_unrealized", False):
            return
        if not getattr(widget, "_anim_paused", False):
            return
        widget._anim_paused = False
        url = getattr(widget, "_anim_url", None)
        if url is None:
            return
        info = self._anim_registry.get(url)
        if info is not None:
            texture, _ = info["frames"].get_frame(info["frame_idx"])
            widget.set_paintable(texture)

    @staticmethod
    def _on_anim_unmap(widget: Gtk.Picture) -> None:
        """Mark widget paused — tick will skip it."""
        widget._anim_paused = True

    # ── Room state popover ────────────────────────────────────

    def _on_info_clicked(self, button: Gtk.Button) -> None:
        """Show or hide the room-state popover."""
        if self._roomstate_popover is not None and self._roomstate_popover.is_visible():
            self._roomstate_popover.popdown()
            return
        self._roomstate_popover = Gtk.Popover()
        self._roomstate_popover.set_has_arrow(False)
        self._roomstate_popover.set_parent(button)
        self._roomstate_popover.set_position(Gtk.PositionType.BOTTOM)
        self._roomstate_popover.connect("closed", self._on_popover_closed)
        self._roomstate_popover.set_child(self._build_roomstate_content())
        self._roomstate_popover.popup()

    def _on_popover_closed(self, popover: Gtk.Popover) -> None:
        self._roomstate_popover = None

    def _build_roomstate_content(self) -> Gtk.Widget:
        """Build the popover body showing current room modes."""
        theme = CHAT_STYLE["dark"] if self._dark else CHAT_STYLE["light"]

        modes = [
            ("emote-only", _("Emote-only")),
            ("followers-only", _("Followers-only")),
            ("slow", _("Slow mode")),
            ("subs-only", _("Subscribers-only")),
            ("r9k", _("Unique chat")),
        ]

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_size_request(220, -1)

        for key, label_text in modes:
            val = self._room_state.get(key, 0)
            row = self._make_roomstate_row(key, label_text, val, theme)
            box.append(row)

        return box

    def _make_roomstate_row(
        self, key: str, label_text: str, val: int, theme: dict
    ) -> Gtk.Widget:
        on = False
        detail = ""

        if key == "followers-only":
            on = val >= 0
            if val > 0:
                detail = _("{n} min").format(n=val)
            elif val == 0:
                detail = _("On")
            else:
                detail = _("Off")
        elif key == "slow":
            on = val > 0
            detail = _("{n} s").format(n=val) if on else _("Off")
        else:
            on = val == 1
            detail = _("On") if on else _("Off")

        fg = theme["text_color"]
        dim = "#888" if self._dark else "#666"
        indicator_color = "#3cb043" if on else dim

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(4)
        row.set_margin_bottom(4)

        indicator = Gtk.Label()
        indicator.set_markup(
            f'<span foreground="{indicator_color}" size="large">●</span>'
        )
        row.append(indicator)

        name_label = Gtk.Label(label=label_text)
        name_label.set_halign(Gtk.Align.START)
        name_label.set_hexpand(True)
        name_label.set_xalign(0)
        row.append(name_label)

        detail_label = Gtk.Label()
        detail_label.set_halign(Gtk.Align.END)
        detail_label.set_markup(
            f'<span foreground="{fg if on else dim}">'
            f"{GLib.markup_escape_text(detail)}"
            f"</span>"
        )
        row.append(detail_label)

        return row

    def _on_roomstate(self, state: dict) -> None:
        """Called (via GLib.idle_add) when a ROOMSTATE message arrives."""
        if self._cleaned_up:
            return
        self._room_state.update(state)
        # If the popover is currently open, rebuild its content.
        if self._roomstate_popover is not None:
            self._roomstate_popover.set_child(self._build_roomstate_content())

    # ── IRC state ───────────────────────────────────────────

    def _on_irc_state_change(self, state: ConnectionState, retry_count: int) -> None:
        """Update the reconnect banner in response to IRC state transitions."""
        if self._cleaned_up:
            return
        if state == ConnectionState.CONNECTING:
            self._reconnect_label.set_text(_("Connecting to chat…"))
            self._reconnect_spinner.set_visible(True)
            self._reconnect_spinner.start()
            self._reconnect_button.set_visible(False)
            self._reconnect_revealer.set_reveal_child(True)
        elif state == ConnectionState.CONNECTED:
            self._reconnect_spinner.stop()
            self._reconnect_revealer.set_reveal_child(False)
        elif state == ConnectionState.RECONNECTING:
            self._reconnect_label.set_text(
                _("Reconnecting… (attempt {})").format(retry_count)
            )
            self._reconnect_spinner.set_visible(True)
            self._reconnect_spinner.start()
            self._reconnect_button.set_visible(False)
            self._reconnect_revealer.set_reveal_child(True)
        elif state == ConnectionState.DISCONNECTED:
            self._reconnect_spinner.stop()
            self._reconnect_spinner.set_visible(False)
            self._reconnect_label.set_text(_("Disconnected."))
            self._reconnect_button.set_visible(True)
            self._reconnect_revealer.set_reveal_child(True)

    def _on_reconnect_clicked(self, button: Gtk.Button) -> None:
        """Initiate a manual reconnection from the DISCONNECTED state."""
        if self._chat is not None:
            self._chat.reconnect()

    def _on_detach(self, button: Gtk.Button) -> None:
        """Open the chat in a separate window and pop this page."""
        if self.parent is None:
            return
        parent = self.parent
        root = self.get_root()
        from .chat_window import ChatWindow

        popup = ChatWindow(
            twitch=getattr(parent, "twitch", None),
            streamer=self._streamer,
            display_name=self._display_name,
            alternating_bg=self._alternating_bg,
            disable_emote_animations=self._disable_emote_animations,
            theme="dark" if self._dark else "light",
            transient_for=root,
            highlight_first_msg=self._highlight_first_msg,
            highlight_mod=self._highlight_mod,
            highlight_vip=self._highlight_vip,
            highlight_partner=self._highlight_partner,
            highlight_broadcaster=self._highlight_broadcaster,
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
