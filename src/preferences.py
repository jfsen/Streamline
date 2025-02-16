from gi.repository import Adw, Gtk, Gdk

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
    window_size_row = Gtk.Template.Child()
    theme_row = Gtk.Template.Child()

    def __init__(self, parent, **kwargs):
        super().__init__(**kwargs)
        self.set_transient_for(parent)
        
        # Store parent reference directly since window lifecycle is managed by GTK
        self.parent = parent
        
        # Define quality options first
        self._qualities = ["best", "1080p60", "1080p", "720p60", 
                         "720p", "480p", "360p", "worst"]
        
        # Setup models
        self._player_model = Gtk.StringList.new(["MPV", "VLC", "Custom"])
        self._quality_model = Gtk.StringList.new(self._qualities)
        
        # Setup UI
        self._setup_models()
        self._setup_values()
        self._connect_signals()
        
        # Initialize window size preference using narrow_mode
        self.window_size_row.set_selected(1 if parent.narrow_mode else 0)
        
        # Use hide-on-close to let GTK manage window lifecycle
        self.set_hide_on_close(True)
        
        # Set up theme selection
        theme_style = Adw.StyleManager.get_default().get_color_scheme()
        if theme_style == Adw.ColorScheme.FORCE_LIGHT:
            self.theme_row.set_selected(1)
        elif theme_style == Adw.ColorScheme.FORCE_DARK:
            self.theme_row.set_selected(2)
        else:
            self.theme_row.set_selected(0)
        
    def _setup_models(self):
        # Setup player selection
        self.player_row.set_model(self._player_model)
        if self.parent.player_type == "mpv":
            self.player_row.set_selected(0)
        elif self.parent.player_type == "vlc":
            self.player_row.set_selected(1)
        else:
            self.player_row.set_selected(2)
        
        # Setup quality selection
        self.quality_row.set_model(self._quality_model)
        
    def _setup_values(self):
        # Set current values
        self.streamlink_entry.set_text(self.parent.streamlink_path)
        self.mpv_entry.set_text(self.parent.mpv_path)
        self.vlc_entry.set_text(self.parent.vlc_path)
        self.custom_player_row.set_text(self.parent.custom_player_path)
        self.custom_player_row.set_sensitive(self.parent.player_type == "custom")
        
        # Set current quality
        try:
            quality_index = self._qualities.index(self.parent.stream_quality)
            self.quality_row.set_selected(quality_index)
        except ValueError:
            self.quality_row.set_selected(0)  # Default to "best"
        
    def _connect_signals(self):
        """Connect signals to handlers."""
        self.streamlink_entry.connect('changed', self._on_path_changed)
        self.mpv_entry.connect('changed', self._on_path_changed)
        self.vlc_entry.connect('changed', self._on_path_changed)
        self.player_row.connect('notify::selected', self._on_player_changed)
        self.custom_player_row.connect('notify::text', self._on_custom_path_changed)
        self.quality_row.connect('notify::selected', self._on_quality_changed)
        self.window_size_row.connect('notify::selected', self._on_window_size_changed)
        self.theme_row.connect('notify::selected', self._on_theme_changed)
        
        # Connect to destroy signal instead of close-request
        self.connect('destroy', self._on_destroy)

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
    
    def _on_window_size_changed(self, row, _):
        """Handle window size preference change"""
        narrow_mode = row.get_selected() == 1
        self.parent.narrow_mode = narrow_mode
        if narrow_mode:
            self.parent.set_default_size(360, 600)
        self.parent.save_config()
    
    def _on_theme_changed(self, row, _):
        style_manager = Adw.StyleManager.get_default()
        selected = row.get_selected()
        if selected == 0:
            style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        elif selected == 1:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.parent.save_config()
    
    def _on_destroy(self, window):
        """Clean up resources when window is destroyed."""
        # Disconnect signals
        for handler_id in self._signal_handlers:
            source = self.get_object_for_signal_handler(handler_id)
            if source:
                source.disconnect(handler_id)
        
        # Clear models
        self.player_row.set_model(None)
        self.quality_row.set_model(None)
        
        # Clear references
        self._player_model = None
        self._quality_model = None
        self._signal_handlers = None
        self.parent = None