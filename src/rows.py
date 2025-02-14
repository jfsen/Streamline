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
        # Create play button as prefix
        play_button = Gtk.Button(icon_name=IconNames.PLAY)
        play_button.add_css_class("flat")
        play_button.set_valign(Gtk.Align.CENTER)
        play_button.set_tooltip_text("Play stream")
        play_button.connect("clicked", lambda btn: self.window.play_stream(streamer))
        row.add_prefix(play_button)

        # Create action buttons with tooltips and handlers
        buttons = [
            (IconNames.BROWSER, "Open in browser", self.window.open_stream_in_browser),
            (IconNames.UNFOLLOW, "Unfollow", self.window.unfollow_streamer),
            (IconNames.VODS, "Show VODs", self.window.show_vods_page)
        ]

        for icon_name, tooltip, handler in buttons:
            button = Gtk.Button(icon_name=icon_name)
            button.add_css_class("flat")
            button.set_valign(Gtk.Align.CENTER)
            button.set_tooltip_text(tooltip)
            button.connect("clicked", lambda btn, h=handler: h(streamer))
            row.add_suffix(button)