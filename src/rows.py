from html import unescape

from gi.repository import Adw, Gio, GLib, Gtk


class StreamerRowManager:
    _CSS_LOADED = False  # class-level flag — only load once ever

    def __init__(self, window):
        self.window = window
        self.streamer_rows = {}
        self.online_list = window.online_list
        self.offline_list = window.offline_list

    @classmethod
    def _ensure_css(cls, display):
        """Load common CSS from GResource — only once per process."""
        if cls._CSS_LOADED:
            return
        cls._CSS_LOADED = True
        css_provider = Gtk.CssProvider()
        css_provider.load_from_resource(
            "/io/github/jfsen/Streamline/css/streamline.css"
        )
        Gtk.StyleContext.add_provider_for_display(
            display, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def create_row(self, streamer, info):
        """Create an ActionRow with buttons and additional info."""
        self._ensure_css(self.window.get_display())
        row = Adw.ActionRow.new()

        # Store the login name in the row data for sorting
        row.login_name = streamer

        # Set streamer name using combined cache
        display_name = self.window.twitch.user_cache["names"].get(streamer)

        # Check if display name has non-ASCII characters
        if display_name and any(ord(c) > 127 for c in display_name):
            # For non-ASCII display names, include login name in parentheses
            row.set_title(f"{display_name} ({streamer})")
        else:
            row.set_title(display_name or streamer)

        row.set_title_lines(1)

        # Store whether streamer is online in the row data
        row.is_online = bool(info)

        # Apply online/offline visual indicator
        if info:  # Online streamer
            row.add_css_class("online-row")
            viewers = info.get("viewers", "N/A")
            game = info.get("game", "Unknown")
            row.set_subtitle(GLib.markup_escape_text(f"{game}\n{viewers} viewers"))

            raw_title = info.get("title", "No title")
            title = unescape(raw_title)  # Unescape special characters
            uptime = info.get("uptime", "N/A")
            tooltip = f"Title: {title}\nUptime: {uptime}"
            row.set_tooltip_text(tooltip)
        else:  # Offline streamer
            row.add_css_class("offline-row")

        # Add buttons
        self._add_row_buttons(row, streamer)
        return row

    def _add_row_buttons(self, row, streamer):
        """Add buttons and dropdown menu to the row."""
        row.add_css_class("action-row")

        # Create play button as prefix
        play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        play_button.add_css_class("flat")
        if not row.is_online:
            play_button.add_css_class("offline-stream-button")
        play_button.set_valign(Gtk.Align.CENTER)
        play_button.set_tooltip_text("Play stream")
        play_button.connect(
            "clicked",
            lambda btn: self.window.player.play_content(
                f"twitch.tv/{streamer}", is_vod=False
            ),
        )
        row.add_prefix(play_button)

        # Create browser button
        browser_button = Gtk.Button(icon_name="web-browser-symbolic")
        browser_button.add_css_class("flat")
        browser_button.set_valign(Gtk.Align.CENTER)
        browser_button.set_tooltip_text("Open in browser")
        browser_button.connect(
            "clicked", lambda btn: self.window.open_stream_in_browser(streamer)
        )
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
            ("Unfollow", "unfollow", self.window.unfollow_streamer),
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

        # Find insertion point to maintain alphabetical order by login name
        index = 0
        child = self.offline_list.get_first_child()
        while child is not None:
            # Compare using the stored login name
            if child.login_name.lower() > username.lower():
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
