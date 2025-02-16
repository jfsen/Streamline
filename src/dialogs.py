from gi.repository import Adw, Gtk

class StreamlineDialogs:
    def __init__(self, parent_window):
        self.parent = parent_window

    def create_input_dialog(self, heading, body, default_response="ok"):
        """Create reusable input dialog"""
        dialog = Adw.MessageDialog(
            transient_for=self.parent,
            heading=heading,
            body=body
        )
        
        content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=20,
            margin_end=20,
            margin_top=10,
            margin_bottom=10
        )
        
        entry = Gtk.Entry(
            width_chars=30,
            hexpand=True,
            activates_default=True
        )
        
        content_box.append(entry)
        dialog.set_extra_child(content_box)
        dialog.set_default_response(default_response)
        
        return dialog, entry

    def show_error_dialog(self, heading, message):
        """Show error dialog with the given heading and message."""
        dialog = Adw.MessageDialog(
            transient_for=self.parent,
            heading=heading,
            body=message
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def show_unfollow_dialog(self, streamer, callback):
        """Show confirmation dialog for unfollowing a streamer."""
        dialog = Adw.MessageDialog.new(
            self.parent,  # Fix: Changed from self.window to self.parent
            _("Unfollow Streamer?"),
            _("Are you sure you want to unfollow {}?").format(streamer)
        )
        
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("unfollow", _("Unfollow"))
        dialog.set_response_appearance("unfollow", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_unfollow_response, streamer, callback)
        dialog.present()

    def _on_unfollow_response(self, dialog, response, streamer, callback):
        """Handle unfollow dialog response."""
        if response == "unfollow":
            callback(dialog, response, streamer)
        dialog.close()

    def show_already_following_dialog(self, username):
        """Show error dialog for already following streamer."""
        error = Adw.MessageDialog(
            transient_for=self.parent,
            heading="Already Following",
            body=f"You are already following {username}"
        )
        error.add_response("ok", "OK")
        error.present()

    def show_follow_dialog(self, callback):
        """Show dialog to follow new streamer."""
        dialog, entry = self.create_input_dialog(
            heading="Follow Streamer",
            body="Enter the Twitch username of the streamer you want to follow:",
            default_response="follow"
        )

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("follow", "Follow")
        dialog.set_response_appearance("follow", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("follow")

        dialog.connect("response", self._on_follow_response, entry, callback)
        dialog.present()
        entry.grab_focus()

    def _on_follow_response(self, dialog, response, entry, callback):
        """Handle follow dialog response."""
        if response == "follow":
            username = entry.get_text().strip()
            if username:
                callback(username)
        dialog.close()

    def show_quick_play_dialog(self, callback):
        """Show dialog to quickly play a stream."""
        dialog, entry = self.create_input_dialog(
            heading="Quick Play Stream",
            body="Enter the Twitch username of the streamer:",
            default_response="play"
        )

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("play", "Play")
        dialog.set_response_appearance("play", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("play")

        dialog.connect("response", self._on_quick_play_response, entry, callback)
        dialog.present()
        entry.grab_focus()

    def _on_quick_play_response(self, dialog, response, entry, callback):
        """Handle quick play dialog response."""
        if response == "play":
            username = entry.get_text().strip()
            if username:
                callback(username)
        dialog.close()