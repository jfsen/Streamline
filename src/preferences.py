from gi.repository import Adw, Gdk, Gtk


@Gtk.Template(resource_path="/io/github/jfsen/Streamline/preferences.ui")
class StreamlinePreferences(Adw.PreferencesWindow):
    __gtype_name__ = "StreamlinePreferences"

    # Template children
    player_row = Gtk.Template.Child()
    custom_player_row = Gtk.Template.Child()
    quality_row = Gtk.Template.Child()
    custom_quality_row = Gtk.Template.Child()
    window_size_row = Gtk.Template.Child()
    theme_row = Gtk.Template.Child()

    low_latency_switch = Gtk.Template.Child()
    copy_streamers_button = Gtk.Template.Child()

    def __init__(self, parent, **kwargs):
        super().__init__(**kwargs)
        self.set_transient_for(parent)

        # Store parent reference directly since window lifecycle is managed by GTK
        self.parent = parent

        # Define quality presets with fallbacks
        self._quality_presets = {
            "High": "1080p60,1080p,720p60,720p,best",
            "Medium": "720p60,720p,480p,best",
            "Low": "480p,360p,best",
            "Custom": parent.custom_quality
            if hasattr(parent, "custom_quality")
            else "best",
        }

        # Setup models
        self._player_model = Gtk.StringList.new(["MPV", "VLC", "Custom"])
        self._quality_model = Gtk.StringList.new(list(self._quality_presets.keys()))

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

        # Set current theme
        self.theme_row.set_selected(["system", "light", "dark"].index(parent.theme))

        # Connect theme change signal
        self.theme_row.connect("notify::selected", self.on_theme_changed)

        # Initialize low latency switch
        self.low_latency_switch.set_active(parent.low_latency)
        self.low_latency_switch.connect("notify::active", self.on_low_latency_toggled)

        # Connect the copy streamers button
        self.copy_streamers_button.connect("clicked", self.on_save_streamers_clicked)

    def _setup_models(self):
        """Setup models for dropdowns with null checks."""
        # Check if widgets are properly bound
        if not self.player_row:
            print("Warning: player_row not bound from template")
            return

        if not self._player_model:
            print("Warning: player_model not initialized")
            return

        # Setup player selection
        try:
            self.player_row.set_model(self._player_model)
            if self.parent.player_type == "mpv":
                self.player_row.set_selected(0)
            elif self.parent.player_type == "vlc":
                self.player_row.set_selected(1)
            else:
                self.player_row.set_selected(2)
        except Exception as e:
            print(f"Error setting up player model: {e}")

        # Setup quality selection
        if self.quality_row and self._quality_model:
            self.quality_row.set_model(self._quality_model)

    def _setup_values(self):
        # Set current values
        self.custom_player_row.set_text(self.parent.custom_player_path)
        self.custom_player_row.set_visible(self.parent.player_type == "custom")

        # Initialize quality selection
        quality_presets = list(self._quality_presets.keys())
        try:
            preset_index = quality_presets.index(self.parent.stream_quality)
            self.quality_row.set_selected(preset_index)
        except ValueError:
            self.quality_row.set_selected(0)  # Default to High

        # Initialize custom quality
        self.custom_quality_row.set_text(self.parent.custom_quality)
        self.custom_quality_row.set_visible(self.parent.stream_quality == "Custom")

    def _connect_signals(self):
        """Connect signals to handlers."""
        self.player_row.connect("notify::selected", self._on_player_changed)
        self.custom_player_row.connect("notify::text", self._on_custom_path_changed)
        self.quality_row.connect("notify::selected", self._on_quality_changed)
        self.custom_quality_row.connect("notify::text", self._on_custom_quality_changed)
        self.window_size_row.connect("notify::selected", self._on_window_size_changed)
        self.theme_row.connect("notify::selected", self._on_theme_changed)

        # Connect to destroy signal instead of close-request
        self.connect("destroy", self._on_destroy)

    def _on_player_changed(self, row, *args):
        player_types = ["mpv", "vlc", "custom"]
        selected = player_types[row.get_selected()]
        self.parent.player_type = selected

        # Show/hide the custom player entry instead of just disabling it
        self.custom_player_row.set_visible(selected == "custom")

        self.parent.save_config()

    def _on_custom_path_changed(self, row, *args):
        self.parent.custom_player_path = row.get_text()
        self.parent.save_config()

    def _on_quality_changed(self, row, *args):
        """Handle stream quality change."""
        quality_presets = list(self._quality_presets.keys())
        selected = quality_presets[row.get_selected()]
        self.parent.stream_quality = selected

        # Show custom quality entry if "Custom" is selected
        if selected == "Custom":
            self.custom_quality_row.set_visible(True)
        else:
            self.custom_quality_row.set_visible(False)

        self.parent.save_config()

    def _on_custom_quality_changed(self, entry, *args):
        """Handle custom quality change."""
        self.parent.custom_quality = entry.get_text()
        self.parent.save_config()

    def _on_window_size_changed(self, row, _):
        """Handle window size preference change"""
        narrow_mode = row.get_selected() == 1
        self.parent.narrow_mode = narrow_mode
        if narrow_mode:
            self.parent.set_default_size(360, 700)
            self.set_default_size(360, 500)
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

    def on_theme_changed(self, row, *args):
        self.parent.theme = ["system", "light", "dark"][row.get_selected()]
        self.parent.save_config()

    def on_low_latency_toggled(self, switch_row, *args):
        """Handle low latency toggle"""
        self.parent.low_latency = switch_row.get_active()
        self.parent.save_config()

    def on_save_streamers_clicked(self, button):
        """Save all streamers as a comma-separated list to a text file."""
        if not self.parent or not hasattr(self.parent, "all_streamers"):
            return

        # Get comma-separated list of streamers
        streamers_text = ", ".join(sorted(self.parent.all_streamers))

        # Create a file save dialog
        dialog = Gtk.FileChooserNative.new(
            title="Export Streamers List",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
            accept_label="_Save",
            cancel_label="_Cancel",
        )

        # Set default file name
        dialog.set_current_name("streamline-backup.txt")

        # Add filters to only show text files
        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        filter_text.add_pattern("*.txt")
        dialog.add_filter(filter_text)

        # Show the dialog and wait for user response
        dialog.connect("response", self._on_save_streamers_response, streamers_text)
        dialog.show()

    def _on_save_streamers_response(self, dialog, response, streamers_text):
        """Handle the file chooser dialog response."""
        if response == Gtk.ResponseType.ACCEPT:
            file_path = dialog.get_file().get_path()

            try:
                with open(file_path, "w") as file:
                    file.write(streamers_text)

                # Show success toast
                toast = Adw.Toast.new(f"Streamers list saved to {file_path}")
                toast.set_timeout(3)
                self.add_toast(toast)
            except Exception as e:
                # Show error toast
                toast = Adw.Toast.new(f"Error saving file: {str(e)}")
                toast.set_timeout(4)
                self.add_toast(toast)

        # Destroy the dialog
        dialog.destroy()
