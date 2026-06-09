import gettext
import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from weakref import proxy

import requests
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .config import VOD_CACHE_TTL, VOD_REFRESH_COOLDOWN

_ = gettext.gettext

logger = logging.getLogger("VODPage")


@Gtk.Template(resource_path="/io/github/jfsen/Streamline/vod_page.ui")
class VODPage(Adw.NavigationPage):
    __gtype_name__ = "VODPage"

    # Template children
    list_box = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()

    def __init__(self, parent, streamer, display_name, twitch, player):
        super().__init__(title=_("{}'s VODs").format(display_name))

        # Use weak reference for parent
        self.parent = proxy(parent)
        self.streamer = streamer
        self.twitch = twitch
        self.player = player

        # Setup list box properties
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("boxed-list")
        self.list_box.add_css_class("streamer-list")

        # Connect cleanup signal
        self.connect("hidden", self._on_hidden)

        # Connect refresh button
        self.refresh_button.connect("clicked", self._on_refresh_clicked)

        # Read thumbnail preference
        settings = Gio.Settings.new("io.github.jfsen.Streamline")
        self._show_thumbnails = settings.get_boolean("show-vod-thumbnails")
        settings.connect(
            "changed::show-vod-thumbnails", self._on_thumbnail_setting_changed
        )

        # Load VODs (show spinner immediately, fetch in background)
        self._load_vods()

    def show_toast(self, message, timeout=2):
        """Show a toast notification in the VOD page."""
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    def _on_hidden(self, page):
        """Clean up when page is hidden"""
        while row := self.list_box.get_first_child():
            self.list_box.remove(row)

    def get_cache_path(self):
        """Get path to VOD cache file for this streamer."""
        cache_dir = Path(GLib.get_user_cache_dir()) / "Streamline" / "vods"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"{self.streamer}.json"

    def load_cached_vods(self):
        """Load VODs from cache if available and not expired."""
        cache_path = self.get_cache_path()
        if not cache_path.exists():
            return None

        try:
            with open(cache_path) as f:
                cache_data = json.load(f)

            # Check if cache is expired (1 hour)
            cache_time = datetime.fromisoformat(cache_data["timestamp"])
            now = datetime.now(timezone.utc)
            if (now - cache_time).total_seconds() > VOD_CACHE_TTL:
                return None

            return cache_data["vods"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def save_vods_cache(self, vods):
        """Save VODs to cache with timestamp."""
        try:
            cache_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "vods": vods,
            }
            with open(self.get_cache_path(), "w") as f:
                json.dump(cache_data, f, indent=4)
        except OSError as e:
            logger.debug("Failed to write VOD cache: %s", e)

    def _show_spinner(self):
        """Show a loading spinner in the list."""
        while row := self.list_box.get_first_child():
            self.list_box.remove(row)
        row = Adw.ActionRow(title=_("Loading VODs…"))
        spinner = Gtk.Spinner(spinning=True)
        row.add_prefix(spinner)
        self.list_box.append(row)

    def _load_vods(self):
        """Load and display VODs — cache check is instant, network is async."""
        cached_vods = self.load_cached_vods()
        if cached_vods is not None:
            logger.debug("Using %s cached VODs for %s", len(cached_vods), self.streamer)
            self.display_vods(cached_vods)
            return

        # Show spinner while fetching from network in background
        logger.debug("Cache miss for %s, fetching from API", self.streamer)
        self._show_spinner()
        threading.Thread(target=self._fetch_vods_thread, daemon=True).start()

    def _fetch_vods_thread(self):
        """Fetch VODs from the Twitch API in a background thread."""
        try:
            vods = self.twitch.get_user_vods(self.streamer)
            self.save_vods_cache(vods)
            GLib.idle_add(self.display_vods, vods)
        except requests.ConnectionError:
            self._invalidate_vods_cache()
            GLib.idle_add(
                self._show_error_row,
                _("Error Loading VODs"),
                _("No internet connection. Check your network and try again."),
            )
        except Exception as e:
            self._invalidate_vods_cache()
            GLib.idle_add(
                self._show_error_row,
                _("Error Loading VODs"),
                GLib.markup_escape_text(str(e)),
            )

    def _show_error_row(self, title, subtitle):
        """Display an error row that can be clicked to retry."""
        while row := self.list_box.get_first_child():
            self.list_box.remove(row)
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        row.set_activatable(True)
        row.add_prefix(Gtk.Image.new_from_icon_name("network-error-symbolic"))
        row.connect("activated", lambda r: self._retry_load_vods())
        self.list_box.append(row)

    def _invalidate_vods_cache(self):
        """Delete VOD cache for this streamer so retry fetches from network."""
        try:
            self.get_cache_path().unlink(missing_ok=True)
        except OSError:
            pass

    def _retry_load_vods(self):
        """Retry loading VODs after an error."""
        self._load_vods()

    def _on_refresh_clicked(self, button):
        """Refresh VODs from network, with a 60s cooldown."""
        cache_path = self.get_cache_path()
        if cache_path.exists():
            mtime = datetime.fromtimestamp(cache_path.stat().st_mtime, tz=timezone.utc)
            now = datetime.now(timezone.utc)
            remaining = VOD_REFRESH_COOLDOWN - (now - mtime).total_seconds()
            if remaining > 0:
                self.show_toast(
                    _("Please wait {}s before refreshing").format(int(remaining))
                )
                return

        # Expired or no cache — force a fresh fetch
        self._invalidate_vods_cache()
        self._load_vods()

    # ── Thumbnail preference ───────────────────────────────

    def _on_thumbnail_setting_changed(self, settings, key):
        """Reload the VOD display when the thumbnail toggle changes."""
        self._show_thumbnails = settings.get_boolean(key)
        cached = self.load_cached_vods()
        if cached is not None:
            self.display_vods(cached)

    @staticmethod
    def _thumbnail_cache_dir():
        d = Path(GLib.get_user_cache_dir()) / "Streamline" / "vods" / "thumbnails"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _thumbnail_path(self, url):
        if not url:
            return None
        name = hashlib.sha256(url.encode()).hexdigest()[:16] + ".jpg"
        return self._thumbnail_cache_dir() / name

    def _build_vod_card(self, vod, created_at, duration):
        """Build a card-style row with thumbnail, title, metadata, and buttons."""
        # ── Root card (vertical) ────────────────────────────
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add_css_class("card")

        # ── Thumbnail ───────────────────────────────────────
        thumb_url = vod.get("thumbnail_url", "")
        thumb_path = self._thumbnail_path(thumb_url) if thumb_url else None
        if thumb_path and thumb_path.exists():
            picture = Gtk.Picture.new_for_filename(str(thumb_path))
        else:
            icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            icon = icon_theme.lookup_icon(
                "video-x-generic-symbolic",
                None,
                64,
                1,
                Gtk.TextDirection.NONE,
                Gtk.IconLookupFlags.FORCE_SYMBOLIC,
            )
            if icon:
                picture = Gtk.Picture.new_for_paintable(icon)
            else:
                picture = Gtk.Picture.new()
        picture.set_hexpand(True)
        picture.set_size_request(-1, 120)
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.add_css_class("thumbnail")
        row.append(picture)

        # Start background download if not cached
        if thumb_url and thumb_path and not thumb_path.exists():
            threading.Thread(
                target=self._download_thumbnail,
                args=(thumb_url, thumb_path, picture),
                daemon=True,
            ).start()

        # ── Text area ───────────────────────────────────────
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text_box.set_margin_start(10)
        text_box.set_margin_end(10)
        text_box.set_margin_top(8)
        text_box.set_margin_bottom(8)

        title_label = Gtk.Label(
            label=GLib.markup_escape_text(vod["title"]),
            xalign=0,
            wrap=True,
            wrap_mode=2,  # WORD
            max_width_chars=50,
        )
        title_label.add_css_class("heading")
        title_label.set_tooltip_text(vod["title"])
        text_box.append(title_label)

        # ── Meta row ────────────────────────────────────────
        meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        meta_row.set_margin_top(2)

        meta_label = Gtk.Label(
            label=f"{created_at} • {duration}",
            xalign=0,
            hexpand=True,
        )
        meta_label.add_css_class("dim-label")
        meta_label.add_css_class("caption")
        meta_row.append(meta_label)

        play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        play_button.add_css_class("flat")
        play_button.set_valign(Gtk.Align.CENTER)
        play_button.set_tooltip_text(_("Play VOD"))
        play_button.connect("clicked", lambda btn, v=vod: self.play_vod(v))
        meta_row.append(play_button)

        browser_button = Gtk.Button(icon_name="web-browser-symbolic")
        browser_button.add_css_class("flat")
        browser_button.set_valign(Gtk.Align.CENTER)
        browser_button.set_tooltip_text(_("Open VOD in browser"))
        browser_button.connect("clicked", lambda btn, v=vod: self.open_in_browser(v))
        meta_row.append(browser_button)

        text_box.append(meta_row)
        row.append(text_box)

        return row

    def _download_thumbnail(self, url, path, picture):
        """Download a thumbnail to disk and update the picture."""
        try:
            sized_url = url.replace("%{width}", "640").replace("%{height}", "360")
            r = requests.get(sized_url, timeout=10)
            r.raise_for_status()
            path.write_bytes(r.content)
            GLib.idle_add(self._apply_thumbnail, picture, path)
        except Exception as e:
            logger.debug("Thumbnail download failed: %s", e)

    def _apply_thumbnail(self, picture, path):
        """Replace placeholder with the downloaded thumbnail."""
        if path.exists():
            texture = Gdk.Texture.new_from_filename(str(path))
            if texture:
                pic = Gtk.Picture.new_for_paintable(texture)
                pic.set_hexpand(True)
                pic.set_size_request(-1, 120)
                pic.set_content_fit(Gtk.ContentFit.COVER)
                pic.add_css_class("thumbnail")
                # Replace in parent
                parent = picture.get_parent()
                if parent and isinstance(parent, Gtk.Box):
                    # Find index of old picture
                    idx = -1
                    child = parent.get_first_child()
                    i = 0
                    while child:
                        if child == picture:
                            idx = i
                            break
                        child = child.get_next_sibling()
                        i += 1
                    if idx >= 0:
                        parent.remove(picture)
                        parent.insert_child_after(
                            pic, None if idx == 0 else parent.get_first_child()
                        )
        return GLib.SOURCE_REMOVE

    def display_vods(self, vods):
        """Display VODs in the list box."""
        self.list_box.set_visible(False)
        while row := self.list_box.get_first_child():
            self.list_box.remove(row)

        # Toggle boxed-list: cards when thumbnails are on, compact list when off
        if self._show_thumbnails:
            self.list_box.remove_css_class("boxed-list")
        else:
            self.list_box.add_css_class("boxed-list")

        if not vods:
            self.list_box.add_css_class("boxed-list")
            row = Adw.ActionRow(
                title=_("No VODs found"),
                subtitle=_("This channel has no recent VODs available"),
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("video-x-generic-symbolic"))
            self.list_box.append(row)
            self.list_box.set_visible(True)
            return

        for vod in vods:
            created_at = vod["created_at"]
            if created_at.startswith("20"):  # raw ISO timestamp
                created_at = self.twitch._format_date(created_at)
            duration = vod["duration"]
            if re.search(r"\dh", duration):  # raw Twitch format like "2h29m45s"
                duration = self.twitch._format_duration(duration)

            if self._show_thumbnails:
                row = self._build_vod_card(vod, created_at, duration)
            else:
                row = Adw.ActionRow(
                    title=GLib.markup_escape_text(vod["title"]),
                    subtitle=f"{created_at} • {duration}",
                )
                row.set_title_lines(1)
                row.set_tooltip_text(vod["title"])

                play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
                play_button.add_css_class("flat")
                play_button.set_valign(Gtk.Align.CENTER)
                play_button.set_tooltip_text(_("Play VOD"))
                play_button.connect("clicked", lambda btn, v=vod: self.play_vod(v))
                row.add_suffix(play_button)

                browser_button = Gtk.Button(icon_name="web-browser-symbolic")
                browser_button.add_css_class("flat")
                browser_button.set_valign(Gtk.Align.CENTER)
                browser_button.set_tooltip_text(_("Open VOD in browser"))
                browser_button.connect(
                    "clicked", lambda btn, v=vod: self.open_in_browser(v)
                )
                row.add_suffix(browser_button)

            self.list_box.append(row)

        self.list_box.set_visible(True)

    def play_vod(self, vod):
        """Play VOD using streamlink."""
        try:
            self.player.play_content(vod["url"], is_vod=True)
            self.show_toast(_("Starting VOD: {}").format(vod["title"]))
        except Exception:
            self.show_toast(_("Error playing VOD: player failed to start"), 4)

    def open_in_browser(self, vod):
        """Open VOD in web browser."""
        # Get the top-level window
        root = self.get_root()
        Gtk.show_uri(parent=root, uri=vod["url"], timestamp=0)
