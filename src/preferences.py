from gi.repository import Adw, GLib, Gtk


@Gtk.Template(resource_path="/io/github/jfsen/Streamline/preferences.ui")
class StreamlinePreferences(Adw.PreferencesDialog):
    __gtype_name__ = "StreamlinePreferences"

    # Template children — Appearance page
    theme_row = Gtk.Template.Child()

    # Template children — Playback page
    player_row = Gtk.Template.Child()
    custom_player_row = Gtk.Template.Child()
    quality_row = Gtk.Template.Child()
    custom_quality_row = Gtk.Template.Child()
    low_latency_switch = Gtk.Template.Child()
    export_button = Gtk.Template.Child()
    import_button = Gtk.Template.Child()

    # Template children — Account page
    client_id_row = Gtk.Template.Child()
    client_secret_row = Gtk.Template.Child()

    # Mapping of preset names to their stream quality strings
    _QUALITY_KEYS = ("High", "Medium", "Low", "Custom")
    _THEME_KEYS = ("system", "light", "dark", "bronze", "anthracite", "red")

    def __init__(self, parent, **kwargs):
        super().__init__(**kwargs)
        self.parent = parent
        self._save_debounce_id = None

        # Build models
        player_model = Gtk.StringList.new(["MPV", "VLC", "Custom"])
        quality_model = Gtk.StringList.new(list(self._QUALITY_KEYS))

        # ── Set up all rows in one pass ──

        # Player
        self.player_row.set_model(player_model)
        self._select_by_value(
            self.player_row, ("mpv", "vlc", "custom"), parent.player_type
        )
        self.player_row.connect("notify::selected", self._on_player_changed)

        self.custom_player_row.set_text(parent.custom_player_path)
        self.custom_player_row.set_visible(parent.player_type == "custom")
        self.custom_player_row.connect("notify::text", self._on_custom_path_changed)

        # Quality
        self.quality_row.set_model(quality_model)
        self._select_by_value(
            self.quality_row, self._QUALITY_KEYS, parent.stream_quality
        )
        self.quality_row.connect("notify::selected", self._on_quality_changed)

        self.custom_quality_row.set_text(parent.custom_quality)
        self.custom_quality_row.set_visible(parent.stream_quality == "Custom")
        self.custom_quality_row.connect("notify::text", self._on_custom_quality_changed)

        # Low latency
        self.low_latency_switch.set_active(parent.low_latency)
        self.low_latency_switch.connect("notify::active", self._on_low_latency_toggled)

        # Export / Import
        self.export_button.connect("clicked", self._on_export_clicked)
        self.import_button.connect("clicked", self._on_import_clicked)

        # Theme
        self._select_by_value(self.theme_row, self._THEME_KEYS, parent.theme)
        self.theme_row.connect("notify::selected", self._on_theme_changed)

        # Account — credentials
        self.client_id_row.set_text(parent.client_id)
        self.client_id_row.connect("notify::text", self._on_client_id_changed)
        self.client_secret_row.set_text(parent.client_secret)
        self.client_secret_row.connect("notify::text", self._on_client_secret_changed)

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _select_by_value(combo_row, values, current):
        """Set combo row selection by matching a value string, defaulting to 0."""
        try:
            combo_row.set_selected(values.index(current))
        except ValueError:
            combo_row.set_selected(0)

    def _debounced_save(self):
        """Schedule a save after 300ms of inactivity on text fields."""
        if self._save_debounce_id:
            GLib.source_remove(self._save_debounce_id)
        self._save_debounce_id = GLib.timeout_add(300, self._do_save)

    def _do_save(self):
        """Actually persist the config."""
        self.parent.save_config()
        self._save_debounce_id = None
        return GLib.SOURCE_REMOVE

    def _on_player_changed(self, row, *_):
        types = ("mpv", "vlc", "custom")
        idx = row.get_selected()
        if idx < 0 or idx >= len(types):
            return
        selected = types[idx]
        self.parent.player_type = selected
        self.custom_player_row.set_visible(selected == "custom")
        self.parent.save_config()

    def _on_custom_path_changed(self, row, *_):
        self.parent.custom_player_path = row.get_text()
        self._debounced_save()

    def _on_quality_changed(self, row, *_):
        idx = row.get_selected()
        if idx < 0 or idx >= len(self._QUALITY_KEYS):
            return
        selected = self._QUALITY_KEYS[idx]
        self.parent.stream_quality = selected
        self.custom_quality_row.set_visible(selected == "Custom")
        self.parent.save_config()

    def _on_custom_quality_changed(self, entry, *_):
        self.parent.custom_quality = entry.get_text()
        self._debounced_save()

    def _on_low_latency_toggled(self, switch_row, *_):
        self.parent.low_latency = switch_row.get_active()
        self.parent.save_config()

    def _on_theme_changed(self, row, *_):
        idx = row.get_selected()
        if idx < 0 or idx >= len(self._THEME_KEYS):
            return
        self.parent.theme = self._THEME_KEYS[idx]
        self.parent._apply_theme()
        self.parent.save_config()

    def _on_client_id_changed(self, entry, *_):
        self.parent.client_id = entry.get_text()
        self._debounced_save()

    def _on_client_secret_changed(self, entry, *_):
        self.parent.client_secret = entry.get_text()
        self._debounced_save()

    # ── Export streamers ─────────────────────────────────────

    def _on_export_clicked(self, button):
        """Save all streamers as a comma-separated list to a text file."""
        if not self.parent or not hasattr(self.parent, "all_streamers"):
            return

        streamers_text = ", ".join(sorted(self.parent.all_streamers))

        dialog = Gtk.FileChooserNative.new(
            title="Export Streamers List",
            parent=self.parent,
            action=Gtk.FileChooserAction.SAVE,
            accept_label="_Save",
            cancel_label="_Cancel",
        )
        dialog.set_current_name("streamline-backup.txt")

        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        filter_text.add_pattern("*.txt")
        dialog.add_filter(filter_text)

        dialog.connect("response", self._on_export_response, streamers_text)
        dialog.show()

    def _on_export_response(self, dialog, response, streamers_text):
        """Handle the file chooser dialog response."""
        if response == Gtk.ResponseType.ACCEPT:
            file_path = dialog.get_file().get_path()
            try:
                with open(file_path, "w") as file:
                    file.write(streamers_text)
                self.add_toast(Adw.Toast.new(f"Streamers list saved to {file_path}"))
            except Exception as e:
                self.add_toast(Adw.Toast.new(f"Error saving file: {str(e)}"))
        dialog.destroy()

    def _on_import_clicked(self, button):
        """Open a file chooser to import streamers list."""
        dialog = Gtk.FileChooserNative.new(
            title="Import Streamers List",
            parent=self.parent,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="_Import",
            cancel_label="_Cancel",
        )

        filter_text = Gtk.FileFilter()
        filter_text.set_name("Text files")
        filter_text.add_mime_type("text/plain")
        filter_text.add_pattern("*.txt")
        dialog.add_filter(filter_text)

        dialog.connect("response", self._on_import_response)
        dialog.show()

    def _on_import_response(self, dialog, response):
        """Handle the file chooser response and follow imported streamers."""
        if response == Gtk.ResponseType.ACCEPT:
            file_path = dialog.get_file().get_path()
            try:
                with open(file_path) as file:
                    content = file.read()

                # Parse comma- and newline-separated names
                names = []
                for part in content.split(","):
                    for line in part.split("\n"):
                        name = line.strip()
                        if name:
                            names.append(name)

                if not names:
                    self.add_toast(Adw.Toast.new("No streamer names found in file"))
                    dialog.destroy()
                    return

                # Compute how many are new vs duplicates for the toast
                existing = [s.lower() for s in self.parent.all_streamers]
                new_count = sum(1 for n in names if n.lower() not in existing)
                dup_count = len(names) - new_count

                # Reuse the follow logic (adds new streamers, skips duplicates)
                self.parent._handle_follow(", ".join(names))

                # Show feedback on the dialog since main window may be covered
                if new_count > 0 and dup_count > 0:
                    self.add_toast(
                        Adw.Toast.new(
                            f"Added {new_count} streamer(s) ({dup_count} already followed)"
                        )
                    )
                elif new_count > 0:
                    self.add_toast(Adw.Toast.new(f"Added {new_count} streamer(s)"))
                else:
                    self.add_toast(Adw.Toast.new("All streamers already followed"))
            except Exception as e:
                self.add_toast(Adw.Toast.new(f"Error importing file: {str(e)}"))
        dialog.destroy()
