# emote_renderer.py
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

"""Emote and badge rendering — texture cache, animated frames, and badge SVG helpers."""

from __future__ import annotations

import gc
import gettext
import hashlib
import logging
import tempfile as _tempfile
import threading
from collections import OrderedDict
from io import BytesIO
from pathlib import Path

import requests
from gi.repository import Gdk, Gio, GLib, Gtk
from PIL import Image

_ = gettext.gettext
logger = logging.getLogger(__name__)

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
        # AnimatedFrames (animated, first frame pre-decoded).
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
            logger.warning("Failed to read emote from disk %s: %s", path, exc)
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
                logger.warning("Failed to write emote to disk %s: %s", url, exc)
        except Exception as exc:
            logger.warning("Failed to download emote %s: %s", url, exc)

        GLib.idle_add(self._on_data, url, data)

    def evict_page(self, page) -> None:
        """Drop pending downloads for widgets belonging to *page*."""
        with self._lock:
            urls_to_drop = []
            for url, widgets in list(self._pending.items()):
                kept = [(w, r) for w, r in widgets if _page_of(w) is not page]
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
                logger.warning("Failed to decode emote %s", url)

        with self._lock:
            if decoded is not None:
                self._textures[url] = decoded
                self._textures.move_to_end(url)
                if isinstance(decoded, AnimatedFrames):
                    # Count total frame count as proxy for potential
                    # GPU memory — a 60-frame emote counts 60x a static.
                    self._texture_count += max(1, len(decoded))
                elif isinstance(decoded, list):
                    self._texture_count += len(decoded)
                else:
                    self._texture_count += 1
                while self._texture_count > _MAX_EMOTE_TEXTURES:
                    _k, old = self._textures.popitem(last=False)
                    if isinstance(old, AnimatedFrames):
                        self._texture_count -= max(1, len(old))
                    elif isinstance(old, list):
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
        animated images (first frame pre-decoded, rest lazy)."""
        try:
            img = Image.open(BytesIO(data))
        except Exception as exc:
            logger.warning("Emote decode failed: %s", exc)
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
    """Animated emote frames — first frame pre-decoded, rest lazy.

    Frame 0 is decoded eagerly during __init__ so the initial
    display never hits PIL on the main thread.  Subsequent frames
    are decoded on demand via get_frame() with PIL seek/convert.

    Only frames that are actually displayed consume GPU memory
    (Gdk.Texture objects).  Decoded textures are retained in a
    dict; the PIL Image stays open for lazy access to later frames.
    """

    def __init__(self, raw_data: bytes):
        self._textures: dict[int, tuple[Gdk.Texture, int]] = {}
        self._img = Image.open(BytesIO(raw_data))
        self._frame_count = getattr(self._img, "n_frames", 1)
        # Pre-decode frame 0 — always needed for initial display.
        if self._frame_count > 0:
            try:
                self.get_frame(0)
            except Exception:
                pass

    def get_frame(self, idx: int) -> tuple[Gdk.Texture, int]:
        if idx in self._textures:
            return self._textures[idx]
        try:
            self._img.seek(idx)
        except Exception as exc:
            logger.warning("Failed to seek frame %s: %s", idx, exc)
            tex = Gdk.MemoryTexture.new(
                1,
                1,
                Gdk.MemoryFormat.R8G8B8A8,
                GLib.Bytes.new(b"\x00\x00\x00\x00"),
                4,
            )
            self._textures[idx] = (tex, 100)
            return tex, 100
        delay_cs = self._img.info.get("duration", 100)
        delay_ms = max(int(delay_cs), 20) if delay_cs > 0 else 100
        frame = self._img.convert("RGBA")
        w, h = frame.size
        texture = Gdk.MemoryTexture.new(
            w,
            h,
            Gdk.MemoryFormat.R8G8B8A8,
            GLib.Bytes.new(frame.tobytes()),
            w * 4,
        )
        self._textures[idx] = (texture, delay_ms)
        return texture, delay_ms

    def __len__(self) -> int:
        return self._frame_count

    def __iter__(self):
        return iter(range(self._frame_count))


_EMOTE_CACHE = EmoteTextureCache()


def _page_of(widget):
    """Return the ChatPage a widget belongs to via its weakref."""
    ref = getattr(widget, "_page_ref", None)
    return ref() if ref is not None else None


# ── Module-level helpers (dispatch via widget._page_ref) ──────


def _apply_texture(
    widget: Gtk.Picture, url: str, data: Gdk.Texture | AnimatedFrames
) -> bool:
    """Set an emote's texture; *data* is either a static Gdk.Texture
    or an AnimatedFrames for animated images (first frame pre-decoded,

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
        page_ref = getattr(widget, "_page_ref", None)
        page = page_ref() if page_ref is not None else None
        if page is not None:
            page._anim_register(url, widget, data)
    widget.queue_resize()
    return GLib.SOURCE_REMOVE


def _on_anim_destroy(widget: Gtk.Picture) -> None:
    """Remove widget from its page's animation registry."""
    page_ref = getattr(widget, "_page_ref", None)
    page = page_ref() if page_ref is not None else None
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


# ── Badge texture cache ─────────────────────────────────────
# Reuse Gdk.Texture objects across cards so that a broadcaster
# badge that appears on every message doesn't create 500 redundant
# GPU textures for the same SVG file.

_badge_texture_cache: dict[str, Gdk.Texture] = {}


def _get_badge_texture(badge_id: str, svg_data: str) -> Gdk.Texture | None:
    """Return a cached or newly-created Gdk.Texture for *badge_id*."""
    cached = _badge_texture_cache.get(badge_id)
    if cached is not None:
        return cached
    gfile = _make_badge_tempfile(badge_id, svg_data)
    if gfile is None:
        return None
    texture = Gdk.Texture.new_from_file(gfile)
    _badge_texture_cache[badge_id] = texture
    return texture


def _rgba_to_hex(rgba: Gdk.RGBA) -> str:
    r = int(rgba.red * 255)
    g = int(rgba.green * 255)
    b = int(rgba.blue * 255)
    return f"#{r:02x}{g:02x}{b:02x}"
