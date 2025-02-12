from gi.repository import Adw, Gtk

@Gtk.Template(resource_path='/io/github/jfsen/Streamline/preferences.ui')
class StreamlinePreferences(Adw.PreferencesWindow):
    __gtype_name__ = 'StreamlinePreferences'

    # Template children
    streamlink_entry = Gtk.Template.Child()
    mpv_entry = Gtk.Template.Child()
    vlc_entry = Gtk.Template.Child()
    player_row = Gtk.Template.Child()
    custom_player_row = Gtk.Template.Child()
    quality_row = Gtk.Template.Child()

    def __init__(self, parent, **kwargs):
        super().__init__(**kwargs)
        
        self.set_transient_for(parent)
        self.parent = parent
        
        # Setup player selection
        self.player_row.set_model(Gtk.StringList.new(["MPV", "VLC", "Custom"]))
        if self.parent.player_type == "mpv":
            self.player_row.set_selected(0)
        elif self.parent.player_type == "vlc":
            self.player_row.set_selected(1)
        else:
            self.player_row.set_selected(2)
        
        # Setup quality selection
        qualities = ["best", "1080p60", "1080p", "720p60", "720p", "480p", "360p", "worst"]
        self.quality_row.set_model(Gtk.StringList.new(qualities))
        
        # Set current values
        self.streamlink_entry.set_text(parent.streamlink_path)
        self.mpv_entry.set_text(parent.mpv_path)
        self.vlc_entry.set_text(parent.vlc_path)
        self.custom_player_row.set_text(parent.custom_player_path)
        self.custom_player_row.set_sensitive(parent.player_type == "custom")
        
        # Set current quality
        if parent.stream_quality in qualities:
            self.quality_row.set_selected(qualities.index(parent.stream_quality))
        else:
            self.quality_row.set_selected(0)  # Default to "best"
        
        # Connect signals
        self.streamlink_entry.connect('changed', self._on_path_changed)
        self.mpv_entry.connect('changed', self._on_path_changed)
        self.vlc_entry.connect('changed', self._on_path_changed)
        self.player_row.connect('notify::selected', self._on_player_changed)
        self.custom_player_row.connect('changed', self._on_custom_path_changed)
        self.quality_row.connect('notify::selected', self._on_quality_changed)

    def _on_path_changed(self, entry):
        """Save changes when paths are modified."""
        self.parent.streamlink_path = self.streamlink_entry.get_text()
        self.parent.mpv_path = self.mpv_entry.get_text()
        self.parent.vlc_path = self.vlc_entry.get_text()
        self.parent.save_config()

    def _on_player_changed(self, row, *args):
        player_types = ["mpv", "vlc", "custom"]
        selected = player_types[row.get_selected()]
        self.parent.player_type = selected
        self.custom_player_row.set_sensitive(selected == "custom")
        self.parent.save_config()

    def _on_custom_path_changed(self, row, *args):
        self.parent.custom_player_path = row.get_text()
        self.parent.save_config()

    def _on_quality_changed(self, row, *args):
        """Handle stream quality change."""
        qualities = ["best", "1080p60", "1080p", "720p60", "720p", "480p", "360p", "worst"]
        selected = qualities[row.get_selected()]
        self.parent.stream_quality = selected
        self.parent.save_config()