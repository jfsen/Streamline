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

    def show_unfollow_dialog(self, streamer, on_response):
        """Show unfollow confirmation dialog."""
        dialog = Adw.MessageDialog(
            transient_for=self.parent,
            heading=f"Unfollow {streamer}?",
            body="Are you sure you want to unfollow this streamer?"
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("unfollow", "Unfollow")
        dialog.set_response_appearance("unfollow", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", on_response, streamer)
        dialog.present()

    def show_already_following_dialog(self, username):
        """Show error dialog for already following streamer."""
        error = Adw.MessageDialog(
            transient_for=self.parent,
            heading="Already Following",
            body=f"You are already following {username}"
        )
        error.add_response("ok", "OK")
        error.present()