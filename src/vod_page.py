import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from weakref import proxy

import requests
from gi.repository import Adw, GLib, Gtk


@Gtk.Template(resource_path="/io/github/jfsen/Streamline/vod_page.ui")
class VODPage(Adw.NavigationPage):
    __gtype_name__ = "VODPage"

    # Template children
    list_box = Gtk.Template.Child()
    toast_overlay = Gtk.Template.Child()
    refresh_button = Gtk.Template.Child()

    def __init__(self, parent, streamer, twitch, player):
        # Create navigation page with proper title
        super().__init__(title=f"{streamer}'s VODs")

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
        cache_dir = Path.home() / ".cache" / "Streamline" / "vods"
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
            if (now - cache_time).total_seconds() > 3600:
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
            print(f"[DEBUG] Failed to write VOD cache: {e}")

    def _show_spinner(self):
        """Show a loading spinner in the list."""
        while row := self.list_box.get_first_child():
            self.list_box.remove(row)
        row = Adw.ActionRow(title="Loading VODs…")
        spinner = Gtk.Spinner(spinning=True)
        row.add_prefix(spinner)
        self.list_box.append(row)

    def _load_vods(self):
        """Load and display VODs — cache check is instant, network is async."""
        # Try cache first (fast, no spinner needed if hit)
        cached_vods = self.load_cached_vods()
        if cached_vods is not None:
            self.display_vods(cached_vods)
            return

        # Show spinner while fetching from network in background
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
                "Error Loading VODs",
                "No internet connection. Check your network and try again.",
            )
        except Exception as e:
            self._invalidate_vods_cache()
            GLib.idle_add(
                self._show_error_row,
                "Error Loading VODs",
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
            remaining = 60 - (now - mtime).total_seconds()
            if remaining > 0:
                self.show_toast(f"Please wait {int(remaining)}s before refreshing")
                return

        # Expired or no cache — force a fresh fetch
        self._invalidate_vods_cache()
        self._load_vods()

    def display_vods(self, vods):
        """Display VODs in the list box."""
        # Clear existing rows using the same pattern as _on_hidden
        while row := self.list_box.get_first_child():
            self.list_box.remove(row)

        if not vods:
            row = Adw.ActionRow(
                title="No VODs found",
                subtitle="This channel has no recent VODs available",
            )
            row.add_prefix(Gtk.Image.new_from_icon_name("video-x-generic-symbolic"))
            self.list_box.append(row)
            return

        for vod in vods:
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(vod["title"]),
                subtitle=f"{vod['created_at']} • {vod['duration']}",
            )
            row.set_title_lines(1)
            row.set_tooltip_text(vod["title"])

            play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
            play_button.add_css_class("flat")
            play_button.set_valign(Gtk.Align.CENTER)
            play_button.set_tooltip_text("Play VOD")
            play_button.connect("clicked", lambda btn, v=vod: self.play_vod(v))
            row.add_suffix(play_button)

            browser_button = Gtk.Button(icon_name="web-browser-symbolic")
            browser_button.add_css_class("flat")
            browser_button.set_valign(Gtk.Align.CENTER)
            browser_button.set_tooltip_text("Open VOD in browser")
            browser_button.connect(
                "clicked", lambda btn, v=vod: self.open_in_browser(v)
            )
            row.add_suffix(browser_button)

            self.list_box.append(row)

    def play_vod(self, vod):
        """Play VOD using streamlink."""
        try:
            self.player.play_content(vod["url"], is_vod=True)
            self.show_toast(f"Starting VOD: {vod['title']}")
        except Exception:
            self.show_toast("Error playing VOD: player failed to start", 4)

    def open_in_browser(self, vod):
        """Open VOD in web browser."""
        # Get the top-level window
        root = self.get_root()
        Gtk.show_uri(parent=root, uri=vod["url"], timestamp=0)
