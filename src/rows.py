from gi.repository import Adw, Gtk, GLib, Gio
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
        """Add buttons and dropdown menu to the row."""
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

        # Create chat button
        chat_button = Gtk.Button(icon_name=IconNames.CHAT)
        chat_button.add_css_class("flat")
        chat_button.set_valign(Gtk.Align.CENTER)
        chat_button.set_tooltip_text("Open Chat")
        chat_button.connect("clicked", lambda btn: self.window.show_chat_page(streamer))
        row.add_suffix(chat_button)

        # Create browser button
        browser_button = Gtk.Button(icon_name=IconNames.BROWSER)
        browser_button.add_css_class("flat")
        browser_button.set_valign(Gtk.Align.CENTER)
        browser_button.set_tooltip_text("Open in browser")
        browser_button.connect("clicked", lambda btn: self.window.open_stream_in_browser(streamer))
        row.add_suffix(browser_button)

        # Create menu button
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("view-more-symbolic")
        menu_button.add_css_class("flat")
        menu_button.set_valign(Gtk.Align.CENTER)
        menu_button.set_tooltip_text("More")
        
        # Create menu model
        menu = Gio.Menu.new()
        
        # Add menu items - no icons needed
        menu_items = [
            ("Show VODs", "show-vods", self.window.show_vods_page),
            ("Unfollow", "unfollow", self.window.unfollow_streamer)
        ]

        for label, action_name, handler in menu_items:
            item = Gio.MenuItem.new(label, f"row.{action_name}")
            menu.append_item(item)

        # Create popup menu
        popover = Gtk.PopoverMenu.new_from_model(menu)
        menu_button.set_popover(popover)

        # Add actions to the row
        action_group = Gio.SimpleActionGroup.new()
        row.insert_action_group("row", action_group)

        # Create the actions
        for _, action_name, handler in menu_items:
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", lambda act, param, h=handler: h(streamer))
            action_group.add_action(action)

        row.add_suffix(menu_button)

    def add_new_streamer(self, username):
        """New streamers are added to the offline list."""
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