import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, GLib, Pango
from typing import Optional

from .message import ChatMessage, MessageQueue, MessageBuffer
from .irc_client import TwitchIRCClient, ConnectionState
from .emote_service import EmoteService
from .emote_store import EmoteStore
from .emote_renderer import EmoteRenderer

class ChatPage(Adw.NavigationPage):
    def __init__(self, streamer: str):
        super().__init__(title=f"{streamer}'s Chat")
        self.streamer = streamer
        
        # Initialize emote handling
        self.emote_service = EmoteService()
        self.emote_store = EmoteStore()
        self.emote_renderer = EmoteRenderer()
        
        # Initialize chat components
        self.message_queue = MessageQueue(batch_delay=100)
        
        # Initialize UI state
        self.autoscroll = True
        
        # Setup UI components
        self._setup_ui()
        
        # Create message buffer
        self.message_buffer = MessageBuffer(self.chat_view)
        
        # Setup IRC client
        self.irc_client = TwitchIRCClient(
            channel=streamer,
            on_message=self._on_irc_message,
            on_state_changed=self._on_connection_state_changed
        )
        
        # Connect signals
        self.connect('destroy', self._on_destroy)
        self.connect('hidden', self._on_hidden)
        
        # Start chat system
        self.emote_service.fetch_emotes(streamer)
        self.irc_client.start()

    def _setup_ui(self):
        """Initialize UI components"""
        # Create toast overlay
        self.toast_overlay = Adw.ToastOverlay()
        
        # Setup header bar with status and play buttons
        self.header = Adw.HeaderBar()
        self.header.add_css_class("flat")
        
        # Status button (left side)
        self.status_button = Gtk.Button()
        self.status_button.add_css_class("flat")
        self.status_button.add_css_class("circular")
        self.status_button.set_sensitive(False)
        self._set_connection_status(False)
        self.header.pack_start(self.status_button)
        
        # Play button (right side)
        self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_button.add_css_class("flat")
        self.play_button.set_tooltip_text("Play this stream")
        self.play_button.connect("clicked", self._on_play_clicked)
        self.header.pack_end(self.play_button)

        # Browser button (right side, before play button)
        self.browser_button = Gtk.Button(icon_name="web-browser-symbolic")
        self.browser_button.add_css_class("flat")
        self.browser_button.set_tooltip_text("Open in browser")
        self.browser_button.connect("clicked", self._on_browser_clicked)
        self.header.pack_end(self.browser_button)

        # Setup chat view
        self._setup_chat_view()
        
        # Create main layout
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_box.append(self.header)
        self.content_box.append(self.overlay)
        
        # Set final layout
        self.toast_overlay.set_child(self.content_box)
        self.set_child(self.toast_overlay)

    def _setup_chat_view(self):
        """Initialize chat view components"""
        # Create chat view
        self.chat_view = Gtk.TextView()
        self.chat_view.set_editable(False)
        self.chat_view.set_cursor_visible(False)
        self.chat_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.chat_view.set_overflow(Gtk.Overflow.HIDDEN)
        
        # Add CSS styling
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data('''
            textview {
                font-size: 12pt;
                padding: 6px;
            }
            textview text {
                background: none;
            }
            textview > * {
                background-clip: padding-box;
            }
        '''.encode())
        
        self.chat_view.get_style_context().add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Create scrolled window
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_child(self.chat_view)
        
        # Setup scroll handling
        self.vadj = self.scrolled.get_vadjustment()
        self.vadj.connect('value-changed', self._on_scroll_changed)
        self.vadj.connect('changed', self._on_adjustment_changed)
        
        # Setup scroll button
        self._setup_scroll_button()

    def _setup_scroll_button(self):
        """Setup scroll to bottom button"""
        self.scroll_button = Gtk.Button(icon_name="go-bottom-symbolic")
        self.scroll_button.add_css_class("circular")
        self.scroll_button.add_css_class("floating")
        self.scroll_button.set_margin_end(18)
        self.scroll_button.set_margin_bottom(18)
        self.scroll_button.set_halign(Gtk.Align.END)
        self.scroll_button.set_valign(Gtk.Align.END)
        self.scroll_button.set_visible(False)
        self.scroll_button.connect("clicked", self._on_scroll_button_clicked)
        
        # Create overlay for scroll button
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.scrolled)
        self.overlay.add_overlay(self.scroll_button)

    def _on_scroll_changed(self, adj):
        """Handle scroll value changes"""
        # Calculate if we're near the bottom
        value = adj.get_value()
        upper = adj.get_upper()
        page_size = adj.get_page_size()
        max_value = upper - page_size
        
        # Toggle autoscroll based on scroll position
        self.autoscroll = (value >= max_value - 10)
        
        # Show/hide scroll button
        near_bottom = (value >= max_value - (page_size * 0.5))
        self.scroll_button.set_visible(not near_bottom)

    def _on_adjustment_changed(self, adj):
        """Handle changes to scroll adjustment bounds"""
        if self.autoscroll:
            # Maintain scroll position at bottom
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def _on_scroll_button_clicked(self, button):
        """Handle scroll-to-bottom button click"""
        self.autoscroll = True
        adj = self.scrolled.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())

    def _on_irc_message(self, username: str, message: str, tags: dict) -> None:
        """Handle incoming IRC message"""
        msg = ChatMessage.from_irc_data(username, message, tags)
        self.message_queue.append(msg, self._process_message_batch)

    def _process_message_batch(self, messages: list[ChatMessage]) -> None:
        """Process a batch of messages"""
        end_iter = self.message_buffer.append_messages(
            messages,
            self.emote_store,
            self.emote_renderer,
            self.get_root().animate_emotes
        )
        
        # Handle autoscroll
        if self.autoscroll:
            mark = self.message_buffer.buffer.create_mark(None, end_iter, False)
            self.chat_view.scroll_mark_onscreen(mark)

    def _on_connection_state_changed(self, state: ConnectionState) -> None:
        """Handle IRC connection state changes"""
        GLib.idle_add(self._set_connection_status, state == ConnectionState.CONNECTED)

    def _set_connection_status(self, connected: bool):
        """Update connection status indicator"""
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

    def _on_destroy(self, *args):
        """Cleanup when page is destroyed"""
        self.irc_client.stop()
        self.message_queue.clear()
        self.message_buffer.clear()
        
        # Clean up emote components
        self.emote_store.clear()

    def _on_hidden(self, page):
        """Called when page is hidden"""
        print("[DEBUG] Chat page hidden, initiating cleanup")
        self._on_destroy()

    def show_toast(self, text: str, timeout: int = 3):
        """Show a toast notification"""
        toast = Adw.Toast.new(text)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    def _on_play_clicked(self, button):
        """Handle play button click"""
        try:
            window = self.get_root()
            if window.player.play_content(f"twitch.tv/{self.streamer}", is_vod=False):
                self.show_toast("Playback starting...", 2)
        except Exception as e:
            self.show_toast(f"Error: {str(e)}", 4)

    def _show_error(self, message: str):
        """Show error message in chat view"""
        self.message_buffer.show_error(message)

    def _on_browser_clicked(self, button):
        """Handle browser button click"""
        window = self.get_root()
        window.open_stream_in_browser(self.streamer)