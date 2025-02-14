from gi.repository import Adw
from gi.repository import Gtk
from gi.repository import GLib
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from .icon_names import IconNames

@Gtk.Template(resource_path='/io/github/jfsen/Streamline/vod_page.ui')
class VODPage(Adw.NavigationPage):
    __gtype_name__ = 'VODPage'

    # Template children
    list_box = Gtk.Template.Child()
    scroll = Gtk.Template.Child()

    def __init__(self, parent, streamer, twitch, player):
        # Create navigation page with proper title
        super().__init__(title=f"{streamer}'s VODs")
        
        # Use weak reference for parent
        from weakref import proxy
        self.parent = proxy(parent)
        self.streamer = streamer
        self.twitch = twitch
        self.player = player
        
        # Setup list box properties
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("boxed-list")
        
        # Connect cleanup signal
        self.connect('hidden', self._on_hidden)
        
        # Load VODs
        self._load_vods()

    def _on_hidden(self, page):
        """Clean up when page is hidden"""
        # Clear list box children
        while row := self.list_box.get_first_child():
            self.list_box.remove(row)
        
        # Clear references
        self.list_box = None
        self.scroll = None
        self.content = None
        self.twitch = None
        self.player = None
        self.parent = None
        # Force cleanup
        import gc
        gc.collect()

    def on_back_clicked(self, button):
        """Handle back button click."""
        self.parent.navigation_view.pop()

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
            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            now = datetime.now(timezone.utc)
            if (now - cache_time).total_seconds() > 3600:
                return None
                
            return cache_data['vods']
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def save_vods_cache(self, vods):
        """Save VODs to cache with timestamp."""
        try:
            cache_data = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'vods': vods
            }
            with open(self.get_cache_path(), 'w') as f:
                json.dump(cache_data, f, indent=4)
        except OSError:
            pass  # Ignore cache write failures

    def _load_vods(self, force_refresh=False):
        """Load and display VODs."""
        try:
            # Try to load from cache first
            if not force_refresh:
                cached_vods = self.load_cached_vods()
                if cached_vods is not None:
                    self.display_vods(cached_vods)
                    return

            # Fetch fresh data
            vods = self.twitch.get_user_vods(self.streamer)
            self.save_vods_cache(vods)
            self.display_vods(vods)
                
        except Exception as e:
            row = Adw.ActionRow(
                title="Error loading VODs",
                subtitle=str(e)
            )
            self.list_box.append(row)

    def display_vods(self, vods):
        """Display VODs in the list box."""
        # Clear existing rows
        while True:
            row = self.list_box.get_first_child()
            if row is None:
                break
            self.list_box.remove(row)

        if not vods:
            row = Adw.ActionRow(
                title="No VODs found",
                subtitle="This channel has no recent VODs available"
            )
            self.list_box.append(row)
            return
            
        for vod in vods:
            row = Adw.ActionRow(
                title=GLib.markup_escape_text(vod['title']),
                subtitle=f"{vod['created_at']} • {vod['duration']} • {vod['view_count']} views"
            )
            
            # Update play button icon name
            play_button = Gtk.Button(icon_name=IconNames.PLAY)
            play_button.add_css_class("flat")
            play_button.set_valign(Gtk.Align.CENTER)
            play_button.set_tooltip_text("Play VOD")
            play_button.connect("clicked", lambda btn, v=vod: self.play_vod(v))
            row.add_suffix(play_button)
            
            # Update browser button icon name
            browser_button = Gtk.Button(icon_name=IconNames.BROWSER)
            browser_button.add_css_class("flat")
            browser_button.set_valign(Gtk.Align.CENTER)
            browser_button.set_tooltip_text("Open VOD in browser")
            browser_button.connect("clicked", lambda btn, v=vod: self.open_in_browser(v))
            row.add_suffix(browser_button)
            
            self.list_box.append(row)

    def on_refresh_clicked(self, button):
        """Handle refresh button click."""
        self._load_vods(force_refresh=True)

    def play_vod(self, vod):
        """Play VOD using streamlink."""
        try:
            streamlink_cmd, player_cmd = self.player._get_required_executables()
            
            cmd = ['flatpak-spawn', '--host'] if os.path.exists('/.flatpak-info') else []
            cmd.extend([
                streamlink_cmd,
                vod['url'],
                self.player.window.stream_quality,
                '--player-passthrough=hls',
                f'--player={player_cmd}'
            ])
            
            subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True
            )
            
            self.player.window.show_toast(f"Starting VOD playback")
            
        except Exception as e:
            self.player.window._show_error_dialog("Playback Error", str(e))
    
    def open_in_browser(self, vod):
        """Open VOD in web browser."""
        # Get the top-level window
        root = self.get_root()
        Gtk.show_uri(parent=root, uri=vod['url'], timestamp=0)