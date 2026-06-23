import gettext
import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .config import VOD_CACHE_TTL, VOD_REFRESH_COOLDOWN

_ = gettext.gettext

logger = logging.getLogger("VODPage")


@Gtk.Template(resource_path="/org/jfsen/Streamline/vod_page.ui")
class VODPage(Adw.NavigationPage):
    __gtype_name__ = "VODPage"

    VODS_PER_PAGE = 10

    # Template children
    list_box = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()

    def __init__(self, parent, streamer, display_name, twitch, player):
        super().__init__(title=_("{}'s VODs").format(display_name))

        self.parent = parent
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
        settings = Gio.Settings.new("org.jfsen.Streamline")
        self._show_thumbnails = settings.get_boolean("show-vod-thumbnails")
        settings.connect(
            "changed::show-vod-thumbnails", self._on_thumbnail_setting_changed
        )

        # Load VODs (show spinner immediately, fetch in background)
        self._all_vods = []
        self._shown_count = 0
        self._show_more_row = None
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
            GLib.idle_add(self.display_vods, vods, True)
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
    def _thumbnail_cache_dir(streamer):
        d = (
            Path(GLib.get_user_cache_dir())
            / "Streamline"
            / "vods"
            / "thumbnails"
            / streamer
        )
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _thumbnail_path(self, streamer, url):
        if not url:
            return None
        name = hashlib.sha256(url.encode()).hexdigest()[:16] + ".jpg"
        return self._thumbnail_cache_dir(streamer) / name

    def _purge_thumbnails(self, streamer, vods):
        """Delete cached thumbnails that are no longer in the VOD list."""
        keep = set()
        for vod in vods:
            url = vod.get("thumbnail_url", "")
            if url:
                path = self._thumbnail_path(streamer, url)
                if path:
                    keep.add(path.name)
        cache_dir = self._thumbnail_cache_dir(streamer)
        try:
            for f in cache_dir.iterdir():
                if f.name not in keep:
                    f.unlink()
                    logger.debug("Purged stale thumbnail: %s", f.name)
        except OSError:
            pass

    def _build_vod_card(self, vod, created_at, duration):
        """Build a card-style row with thumbnail, title, metadata, and buttons."""
        # ── Root card (vertical) ────────────────────────────
        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add_css_class("card")

        # ── Thumbnail ───────────────────────────────────────
        thumb_url = vod.get("thumbnail_url", "")
        thumb_path = (
            self._thumbnail_path(self.streamer, thumb_url) if thumb_url else None
        )
        if thumb_path and thumb_path.exists():
            picture = Gtk.Picture.new_for_filename(str(thumb_path))
            picture.set_content_fit(Gtk.ContentFit.COVER)
        else:
            picture = Gtk.Box()
            picture.add_css_class("thumbnail-placeholder")
            icon = Gtk.Image.new_from_icon_name("camera-video-symbolic")
            icon.set_pixel_size(48)
            icon.set_opacity(0.25)
            icon.set_halign(Gtk.Align.CENTER)
            icon.set_valign(Gtk.Align.CENTER)
            icon.set_hexpand(True)
            icon.set_vexpand(True)
            picture.append(icon)
        picture.set_hexpand(True)
        picture.set_size_request(-1, 120)
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
            label=vod["title"],
            xalign=0,
            wrap=True,
            wrap_mode=2,  # WORD
            max_width_chars=50,
            ellipsize=3,  # END
            lines=2,
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
        except requests.HTTPError as e:
            # 403 is expected for still-processing VODs — suppress the noise
            if e.response is not None and e.response.status_code != 403:
                logger.debug("Thumbnail download failed: %s", e)
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
                    # Find the sibling before the old picture
                    prev = None
                    child = parent.get_first_child()
                    while child is not None:
                        if child == picture:
                            break
                        prev = child
                        child = child.get_next_sibling()
                    parent.remove(picture)
                    parent.insert_child_after(pic, prev)
        return GLib.SOURCE_REMOVE

    def display_vods(self, vods, purge=False):
        """Store VODs and render the first page.

        When purge=True, stale thumbnails are cleaned up (use on fresh API fetches).
        """
        self._all_vods = vods
        self._shown_count = 0
        self._show_more_row = None

        if purge:
            self._purge_thumbnails(self.streamer, vods)

        self._render_initial(vods)

    def _render_initial(self, vods):
        """Clear the list and render the first batch of VODs plus a Show-more
        button when there are more."""
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

        count = min(self.VODS_PER_PAGE, len(vods))
        for vod in vods[:count]:
            self.list_box.append(self._build_vod_row(vod))
        self._shown_count = count

        # When thumbnails are off there's no download to stagger — show
        # everything and its formatting is cheap.
        if not self._show_thumbnails:
            for vod in vods[count:]:
                self.list_box.append(self._build_vod_row(vod))
            self._shown_count = len(vods)
        elif count < len(vods):
            self._append_show_more()

        self.list_box.set_visible(True)

    def _on_show_more(self, *args):
        """Append the next batch of VODs without rebuilding, so scroll
        position is preserved."""
        if self._show_more_row:
            # Row is a ListBoxRow; remove it from the parent ListBox
            parent = self._show_more_row.get_parent()
            if parent:
                parent.remove(self._show_more_row)
            self._show_more_row = None

        start = self._shown_count
        end = min(start + self.VODS_PER_PAGE, len(self._all_vods))
        for vod in self._all_vods[start:end]:
            self.list_box.append(self._build_vod_row(vod))
        self._shown_count = end

        if end < len(self._all_vods):
            self._append_show_more()

    def _build_vod_row(self, vod):
        """Build a single VOD row (card or compact) and return it."""
        created_at = self.twitch.format_date(vod["created_at"])
        duration = self.twitch.format_duration(vod["duration"])

        if self._show_thumbnails:
            return self._build_vod_card(vod, created_at, duration)

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
        browser_button.connect("clicked", lambda btn, v=vod: self.open_in_browser(v))
        row.add_suffix(browser_button)
        return row

    def _build_show_more_row(self):
        """Build a card-styled row that loads more VODs when clicked."""
        label_text = _("Show more…")

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.add_css_class("card")
        row.add_css_class("show-more-card")

        inner = Gtk.Box(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        inner.set_size_request(-1, 48)

        label = Gtk.Label(label=label_text)
        label.set_halign(Gtk.Align.CENTER)
        inner.append(label)
        row.append(inner)

        click = Gtk.GestureClick.new()
        click.connect("released", lambda g, n, x, y: self._on_show_more())
        row.add_controller(click)
        return row

    def _append_show_more(self):
        """Append a Show-more button for the next unseen batch."""
        inner = self._build_show_more_row()
        row = Gtk.ListBoxRow()
        row.set_child(inner)
        self._show_more_row = row
        self.list_box.append(row)

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
