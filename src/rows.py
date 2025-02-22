from gi.repository import Adw, Gtk, GLib
from .icon_names import IconNames
from html import unescape

class StreamerRowManager:
    def __init__(self, window):
        self.window = window
        self.streamer_rows = {}
        self.online_list = window.online_list
        self.offline_list = window.offline_list

        # Add CSS provider for hover effects
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data('''
            .hide-on-leave {
                opacity: 0;
                transition: opacity 200ms ease;
            }
            .action-row:hover .hide-on-leave,
            .hide-on-leave:hover {
                opacity: 1;
            }
        '''.encode())
        
        # Apply CSS to window
        Gtk.StyleContext.add_provider_for_display(
            window.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

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
            row.set_subtitle(f"{game}\n{viewers} viewers")

            # Get raw title and decode HTML entities without re-escaping
            raw_title = info.get('title', 'No title')
            title = unescape(raw_title)  # This will convert &apos; to ' etc.
            uptime = info.get('uptime', 'N/A')
            tooltip = f"Title: {title}\nUptime: {uptime}"
            row.set_tooltip_text(tooltip)

        # Add buttons
        self._add_row_buttons(row, streamer)
        return row

    def _add_row_buttons(self, row, streamer):
        """Add action buttons to the row"""
        row.add_css_class("action-row")
        
        # Create play button as prefix
        play_button = Gtk.Button(icon_name=IconNames.PLAY)
        play_button.add_css_class("flat")
        play_button.set_valign(Gtk.Align.CENTER)
        play_button.set_tooltip_text("Play stream")
        
        def on_play_clicked(btn):
            try:
                if self.window.player.play_content(f"twitch.tv/{streamer}", is_vod=False):
                    self.window.show_toast("Playback starting...", 2)
            except Exception as e:
                self.window.show_toast(f"Error: {str(e)}", 4)
        
        play_button.connect("clicked", on_play_clicked)
        row.add_prefix(play_button)

        # Create action buttons with tooltips and handlers
        buttons = [
            (IconNames.UNFOLLOW, "Unfollow", self.window.unfollow_streamer, 
             self.window.show_unfollow_button, False),
            (IconNames.BROWSER, "Open in browser", self.window.open_stream_in_browser, 
             self.window.show_weblink_button, False),
            ("chat-message-new-symbolic", "Open Chat", self.window.show_chat_page, 
             self.window.show_chat_button, False),
            (IconNames.VODS, "Show VODs", self.window.show_vods_page, 
             self.window.show_vods_button, False)
        ]

        for icon_name, tooltip, handler, show, hide_on_leave in buttons:
            if show:
                button = Gtk.Button(icon_name=icon_name)
                button.add_css_class("flat")
                if hide_on_leave:
                    button.add_css_class("hide-on-leave")  # Not currently used
                button.set_valign(Gtk.Align.CENTER)
                button.set_tooltip_text(tooltip)
                button.connect("clicked", lambda btn, h=handler: h(streamer))
                row.add_suffix(button)

    def add_offline_streamer(self, username):
        """Create and add row for new offline streamer."""
        row = self.create_row(username, {})
        
        # Find insertion point to maintain alphabetical order
        index = 0
        child = self.offline_list.get_first_child()
        while child is not None:
            title = child.get_title()
            if title.lower() > username.lower():
                break
            index += 1
            child = child.get_next_sibling()
        
        self.offline_list.insert(row, index)
        self.streamer_rows[username] = row
        self.window._update_streams_cache(username, add=True)
        return row

    def remove_streamer_row(self, username):
        """Remove streamer row from UI."""
        if username in self.streamer_rows:
            row = self.streamer_rows[username]
            parent = row.get_parent()
            if parent:
                parent.remove(row)
            # Clean up row reference
            del self.streamer_rows[username]
            self.window._update_streams_cache(username, add=False)

    def update_rows(self, online_streamers, offline_streamers, streamer_info):
        """Update all streamer rows."""
        # Clear existing rows
        def clear_list(list_box):
            while row := list_box.get_first_child():
                list_box.remove(row)
                
        clear_list(self.online_list)
        clear_list(self.offline_list)
        self.streamer_rows.clear()

        # Sort streamers alphabetically (case-insensitive)
        online_streamers.sort(key=str.lower)
        offline_streamers.sort(key=str.lower)

        # Add new streamer rows
        for streamer in online_streamers:
            row = self.create_row(streamer, streamer_info.get(streamer, {}))
            self.online_list.append(row)
            self.streamer_rows[streamer] = row
        
        for streamer in offline_streamers:
            row = self.create_row(streamer, {})
            self.offline_list.append(row)
            self.streamer_rows[streamer] = row