import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib, Gdk

from .page import ChatPage

class ChatWindow(Adw.Window):
    """A pop-out window containing a chat page"""
    
    def __init__(self, streamer, parent_window):
        super().__init__()
        
        # Store references
        self.streamer = streamer
        self.parent_window = parent_window
        
        # Configure window
        self.set_title(f"{streamer}'s Chat")
        self.set_default_size(360, 600)
        self.set_size_request(300, 400)
        
        # Copy animation settings from parent window
        self.animate_emotes = parent_window.animate_emotes if hasattr(parent_window, 'animate_emotes') else True
        
        # Create a toast overlay for the window
        self.toast_overlay = Adw.ToastOverlay()
        
        # Create a new ChatPage instance
        self.chat_page = ChatPage(streamer)
        
        # Set up the window content
        self.toast_overlay.set_child(self.chat_page)
        self.set_content(self.toast_overlay)
        
        # Connect signals
        self.connect("close-request", self.on_close_request)
        
        # Connect to page signals we need to relay
        self.chat_page.play_button.connect("clicked", self.on_play_clicked)
        self.chat_page.browser_button.connect("clicked", self.on_browser_clicked)
    
    def on_close_request(self, *args):
        """Handle window close event"""
        # Ensure the chat page cleans up properly
        self.chat_page._on_destroy()
        return False  # False to allow window destruction
    
    def show_toast(self, text, timeout=3):
        """Show a toast notification in the popout window"""
        toast = Adw.Toast.new(text)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)
        
    def on_play_clicked(self, button):
        """Relay play button clicks to parent window"""
        try:
            if self.parent_window.player.play_content(f"twitch.tv/{self.streamer}", is_vod=False):
                self.show_toast("Playback starting...", 2)
        except Exception as e:
            self.show_toast(f"Error: {str(e)}", 4)
    
    def on_browser_clicked(self, button):
        """Relay browser button clicks to parent window"""
        self.parent_window.open_stream_in_browser(self.streamer)
        
    def player_play_content(self, *args, **kwargs):
        """Forward play content requests to parent window"""
        return self.parent_window.player.play_content(*args, **kwargs)
        
    def open_stream_in_browser(self, streamer):
        """Forward browser open requests to parent window"""
        self.parent_window.open_stream_in_browser(streamer)