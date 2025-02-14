from gi.repository import Adw, Gtk, GLib
from .icon_names import IconNames

class StreamerRowManager:
    def __init__(self, window):
        self.window = window
        self.streamer_rows = {}

    def create_row(self, streamer, info):
        """Create an ActionRow with buttons and additional info."""
        row = Adw.ActionRow.new()
        
        # Set streamer name
        display_name = self.window.twitch.display_name_cache.get(streamer) if self.window.twitch else None
        row.set_title(display_name or streamer)
        row.set_title_lines(1)

        if info:  # Online streamer
            viewers = info.get('viewers', 'N/A')
            game = info.get('game', 'Unknown')
            row.set_subtitle(f"{game} • {viewers} viewers")
            row.set_subtitle_lines(1)

            title = GLib.markup_escape_text(info.get('title', 'No title'))
            uptime = info.get('uptime', 'N/A')
            tooltip = f"Title: {title}\nUptime: {uptime}"
            row.set_tooltip_text(tooltip)

        # Add buttons
        self._add_row_buttons(row, streamer)
        return row

    def _add_row_buttons(self, row, streamer):
        """Add action buttons to the row"""
        # Implementation of button creation and connection...