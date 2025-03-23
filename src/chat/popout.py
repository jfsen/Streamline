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
        
        # Create the main layout
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        
        # Set up our own headerbar
        self.setup_headerbar()
        
        # Create a new ChatPage instance without its headerbar
        self.chat_page = self.create_chat_page(streamer)
        
        # Add components to main layout
        self.main_box.append(self.headerbar)
        self.main_box.append(self.chat_page)
        
        # Set up the window content
        self.toast_overlay.set_child(self.main_box)
        self.set_content(self.toast_overlay)
        
        # Connect signals
        self.connect("close-request", self.on_close_request)
    
    def setup_headerbar(self):
        """Create a custom headerbar for the popout"""
        self.headerbar = Adw.HeaderBar()
        self.headerbar.add_css_class("flat")
        
        # Status button (left side) - will be updated by ChatPage
        self.status_button = Gtk.Button()
        self.status_button.add_css_class("flat")
        self.status_button.add_css_class("circular")
        self.status_button.set_sensitive(False)
        self.status_button.set_icon_name("network-offline-symbolic")
        self.status_button.add_css_class("error")
        self.headerbar.pack_start(self.status_button)
        
        # Play button (right side)
        self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_button.add_css_class("flat")
        self.play_button.set_tooltip_text("Play this stream")
        self.play_button.connect("clicked", self.on_play_clicked)
        self.headerbar.pack_end(self.play_button)

        # Browser button (right side, before play button)
        self.browser_button = Gtk.Button(icon_name="web-browser-symbolic")
        self.browser_button.add_css_class("flat")
        self.browser_button.set_tooltip_text("Open in browser")
        self.browser_button.connect("clicked", self.on_browser_clicked)
        self.headerbar.pack_end(self.browser_button)
    
    def create_chat_page(self, streamer):
        """Create a chat page without its own headerbar"""
        chat_page = ChatPage(streamer)
        
        # Remove the headerbar from ChatPage
        content_box = chat_page.content_box
        content_box.remove(chat_page.header)
        
        # Save a reference to the status button for updating connection status
        chat_page.status_button = self.status_button
        
        # Update the _set_connection_status method to use our status button
        original_set_status = chat_page._set_connection_status
        def new_set_status(connected):
            # Update our status button instead
            if connected:
                self.status_button.set_icon_name("network-transmit-receive-symbolic")
                self.status_button.set_tooltip_text("Connected")
                self.status_button.remove_css_class("error")
                self.status_button.add_css_class("success")
            else:
                self.status_button.set_icon_name("network-offline-symbolic")
                self.status_button.set_tooltip_text("Disconnected")
                self.status_button.remove_css_class("success")
                self.status_button.add_css_class("error")
        
        # Replace the method
        chat_page._set_connection_status = new_set_status
        
        return chat_page
    
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
        """Handle play button click in popout window"""
        try:
            if self.parent_window.player.play_content(f"twitch.tv/{self.streamer}", is_vod=False):
                self.show_toast("Playback starting...", 2)
        except Exception as e:
            self.show_toast(f"Error: {str(e)}", 4)
    
    def on_browser_clicked(self, button):
        """Handle browser button click in popout window"""
        self.parent_window.open_stream_in_browser(self.streamer)
        
    def player_play_content(self, *args, **kwargs):
        """Forward play content requests to parent window"""
        return self.parent_window.player.play_content(*args, **kwargs)
        
    def open_stream_in_browser(self, streamer):
        """Forward browser open requests to parent window"""
        self.parent_window.open_stream_in_browser(streamer)