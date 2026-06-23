import gettext
import logging

from gi.repository import Adw, Gio, Gtk

from .config import PLAYER_KEYS, QUALITY_KEYS, THEME_KEYS

_ = gettext.gettext
logger = logging.getLogger("Preferences")


@Gtk.Template(resource_path="/org/jfsen/Streamline/preferences.ui")
class StreamlinePreferences(Adw.PreferencesDialog):
    __gtype_name__ = "StreamlinePreferences"

    # Template children — Appearance page
    theme_row = Gtk.Template.Child()
    profile_pictures_switch = Gtk.Template.Child()
    vod_thumbnails_switch = Gtk.Template.Child()

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

    # Template children — Chat page
    chat_alternating_bg_switch = Gtk.Template.Child()
    chat_disable_emote_animations_switch = Gtk.Template.Child()

    # Template children — Chat Highlighting page
    chat_highlight_first_msg_switch = Gtk.Template.Child()
    chat_highlight_mod_switch = Gtk.Template.Child()
    chat_highlight_vip_switch = Gtk.Template.Child()
    chat_highlight_partner_switch = Gtk.Template.Child()
    chat_highlight_broadcaster_switch = Gtk.Template.Child()

    def __init__(self, parent, **kwargs):
        super().__init__(**kwargs)
        self.parent = parent
        settings = parent.settings

        # ── Combo row models (labels only — values live in GSettings) ──
        self.player_row.set_model(Gtk.StringList.new(["MPV", "VLC", _("Custom")]))
        self.quality_row.set_model(
            Gtk.StringList.new([_("High"), _("Medium"), _("Low"), _("Custom")])
        )

        # ── Combo rows — widget ↔ GSettings (manual two-way sync) ──
        for key, row, keys in (
            ("player-type", self.player_row, PLAYER_KEYS),
            ("stream-quality", self.quality_row, QUALITY_KEYS),
            ("theme", self.theme_row, THEME_KEYS),
        ):
            # Widget → GSettings
            row.connect(
                "notify::selected",
                self._on_combo_changed,
                settings,
                key,
                keys,
            )
            # GSettings → widget (initial)
            try:
                row.set_selected(keys.index(settings.get_string(key)))
            except ValueError:
                row.set_selected(0)

        # ── Entry bindings (string ↔ string, direct match) ──
        for key, row in (
            ("custom-player-path", self.custom_player_row),
            ("custom-quality", self.custom_quality_row),
            ("twitch-client-id", self.client_id_row),
            ("twitch-client-secret", self.client_secret_row),
        ):
            settings.bind(key, row, "text", Gio.SettingsBindFlags.DEFAULT)

        # ── Switch bindings (boolean ↔ boolean, direct match) ──
        for key, row in (
            ("low-latency", self.low_latency_switch),
            ("chat-alternating-background", self.chat_alternating_bg_switch),
            (
                "chat-disable-emote-animations",
                self.chat_disable_emote_animations_switch,
            ),
            ("show-profile-pictures", self.profile_pictures_switch),
            ("show-vod-thumbnails", self.vod_thumbnails_switch),
            ("chat-highlight-first-msg", self.chat_highlight_first_msg_switch),
            ("chat-highlight-mod", self.chat_highlight_mod_switch),
            ("chat-highlight-vip", self.chat_highlight_vip_switch),
            ("chat-highlight-partner", self.chat_highlight_partner_switch),
            (
                "chat-highlight-broadcaster",
                self.chat_highlight_broadcaster_switch,
            ),
        ):
            settings.bind(key, row, "active", Gio.SettingsBindFlags.DEFAULT)

        # ── Conditional visibility (custom-path / custom-quality) ──
        settings.connect("changed::player-type", self._sync_visibility)
        settings.connect("changed::stream-quality", self._sync_visibility)
        self._sync_visibility(settings, None)

        # ── Export / Import (non-settings functionality) ──
        self.export_button.connect("clicked", self._on_export_clicked)
        self.import_button.connect("clicked", self._on_import_clicked)

    # ── Visibility helpers ───────────────────────────────────

    @staticmethod
    def _on_combo_changed(row, _pspec, settings, key, keys):
        """Widget → GSettings: write combo selection back to the schema."""
        idx = row.get_selected()
        if 0 <= idx < len(keys):
            settings.set_string(key, keys[idx])

    def _sync_visibility(self, settings, _key):
        self.custom_player_row.set_visible(
            settings.get_string("player-type") == "custom"
        )
        self.custom_quality_row.set_visible(
            settings.get_string("stream-quality") == "Custom"
        )

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
                self.add_toast(
                    Adw.Toast.new(_("Streamers list saved to {}").format(file_path))
                )
            except Exception as e:
                logger.debug("Error saving file: %s", e)
                self.add_toast(Adw.Toast.new(_("Error saving file: {}").format(str(e))))
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
                    self.add_toast(Adw.Toast.new(_("No streamer names found in file")))
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
                            _("Added {} streamer(s) ({} already followed)").format(
                                new_count, dup_count
                            )
                        )
                    )
                elif new_count > 0:
                    self.add_toast(
                        Adw.Toast.new(_("Added {} streamer(s)").format(new_count))
                    )
                else:
                    self.add_toast(Adw.Toast.new(_("All streamers already followed")))
            except Exception as e:
                logger.debug("Error importing file: %s", e)
                self.add_toast(
                    Adw.Toast.new(_("Error importing file: {}").format(str(e)))
                )
        dialog.destroy()
