from gi.repository import Adw, Gtk, GLib, Pango, GdkPixbuf
import socket
import ssl
import threading
import re
from dataclasses import dataclass
from typing import List, Dict
from .emote_cache import EmoteCache

@dataclass
class ChatMessage:
    timestamp: str
    username: str
    message: str

# Static chat store that persists across page instances
class ChatStore:
    _messages: Dict[str, List[ChatMessage]] = {}
    _max_messages = 500  # Match the ChatPage limit

    @classmethod
    def add_message(cls, channel: str, msg: ChatMessage) -> None:
        """Add a new message to the store"""
        if channel not in cls._messages:
            cls._messages[channel] = []
            
        messages = cls._messages[channel]
        
        # Don't add if message already exists
        if msg in messages:
            return
                
        messages.append(msg)
        if len(messages) > cls._max_messages:
            messages.pop(0)
    
    @classmethod
    def get_messages(cls, channel: str) -> List[ChatMessage]:
        return cls._messages.get(channel, [])

class ChatPage(Adw.NavigationPage):
    def __init__(self, streamer):
        super().__init__(title=f"{streamer}'s Chat")
        self.streamer = streamer
        self.running = True
        self.irc = None  # Initialize IRC socket reference
        self.autoscroll = True
        self.max_messages = 1000
        self.message_count = 0
        self.has_loaded_stored = False
        
        # Initialize EmoteCache first
        self.emote_cache = EmoteCache()
        # Only fetch metadata, not actual emotes
        self.emote_cache.fetch_emote_data(streamer)
        
        # Create toast overlay
        self.toast_overlay = Adw.ToastOverlay()
        
        # Create toolbar with back button and play button
        self.header = Adw.HeaderBar()
        self.header.add_css_class("flat")
        
        # Add play button to header
        self.play_button = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_button.add_css_class("flat")
        self.play_button.set_tooltip_text("Play stream")
        self.play_button.connect("clicked", self._on_play_clicked)
        self.header.pack_end(self.play_button)
        
        # Create main content box
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_box.append(self.header)
        
        # Create chat view
        self.chat_view = Gtk.TextView()
        self.chat_view.set_editable(False)
        self.chat_view.set_cursor_visible(False)
        self.chat_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        
        # Add CSS styling
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data('''
            textview {
                padding: 6px;
            }
            textview text {
                background: none;
            }
            .floating {
                margin: 6px;
                min-width: 36px;
                min-height: 36px;
                padding: 8px;
                background: @view_fg_color;
                color: @view_bg_color;
                opacity: 0.9;
            }
            .floating:hover {
                opacity: 1;
            }
        '''.encode())
        
        # Apply the CSS provider to the chat view
        self.chat_view.get_style_context().add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        # Create chat buffer
        self.chat_buffer = self.chat_view.get_buffer()
        
        # Create tags for formatting
        self.chat_buffer.create_tag("timestamp", foreground="gray")
        self.chat_buffer.create_tag("username", weight=Pango.Weight.BOLD)
        self.chat_buffer.create_tag("message")
        self.chat_buffer.create_tag("separator", 
            foreground="gray",
            justification=Gtk.Justification.CENTER,
            style=Pango.Style.ITALIC)
        
        # Create scrolled window
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_child(self.chat_view)
        
        # Connect scroll events
        self.vadj = self.scrolled.get_vadjustment()
        self.vadj.connect('value-changed', self._on_scroll_changed)
        self.vadj.connect('changed', self._on_adjustment_changed)
        
        # Create overlay for the scroll button
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.scrolled)
        
        # Create scroll to bottom button
        self.scroll_button = Gtk.Button(icon_name="go-bottom-symbolic")
        self.scroll_button.add_css_class("circular")
        self.scroll_button.add_css_class("floating")
        self.scroll_button.set_margin_end(18)
        self.scroll_button.set_margin_bottom(18)
        self.scroll_button.set_halign(Gtk.Align.END)
        self.scroll_button.set_valign(Gtk.Align.END)
        self.scroll_button.set_visible(False)  # Hidden by default
        self.scroll_button.connect("clicked", self._on_scroll_button_clicked)
        
        # Add button to overlay
        self.overlay.add_overlay(self.scroll_button)
        
        # Add to content box
        self.content_box.append(self.overlay)
        
        # Wrap content box in toast overlay
        self.toast_overlay.set_child(self.content_box)
        
        # Set the toast overlay as the page content
        self.set_child(self.toast_overlay)
        
        # IRC Connection settings
        self.irc_server = "irc.chat.twitch.tv"
        self.irc_port = 6697
        self.nickname = "justinfan" + str(GLib.random_int_range(1000, 99999))
        self.channel = f"#{streamer}"
        
        # Load existing messages after EmoteCache is initialized
        self._load_stored_messages()
        
        # Start IRC connection
        self.connect_to_chat()
        
        self.running = True  # Add flag to control IRC thread
    
    def connect_to_chat(self):
        """Initialize IRC connection in a separate thread"""
        thread = threading.Thread(target=self._irc_worker)
        thread.daemon = True
        thread.start()
    
    def _irc_worker(self):
        """Handle IRC connection and message processing"""
        try:
            # Create SSL context
            context = ssl.create_default_context()
            
            # Create socket and wrap with SSL
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.irc = context.wrap_socket(sock, server_hostname=self.irc_server)
            
            # Connect to server
            self.irc.connect((self.irc_server, self.irc_port))
            
            # Send registration messages
            self.irc.send(f"NICK {self.nickname}\r\n".encode())
            self.irc.send(f"JOIN {self.channel}\r\n".encode())
            
            # Message processing loop
            while self.running and self.get_root() is not None:
                data = self.irc.recv(4096).decode('utf-8')
                if not data:
                    break
                    
                # Handle PING/PONG
                if data.startswith('PING'):
                    self.irc.send('PONG\r\n'.encode())
                    continue
                
                # Process chat messages
                if 'PRIVMSG' in data:
                    self._process_message(data)
                    
        except Exception as e:
            if self.running:  # Only show error if we're still supposed to be running
                print(f"[DEBUG] IRC worker error: {e}")
                GLib.idle_add(self._show_error, str(e))
        finally:
            # Clean up socket
            try:
                if self.irc:
                    self.irc.shutdown(socket.SHUT_RDWR)
                    self.irc.close()
            except Exception:
                pass
    
    def _process_message(self, data):
        """Process and display chat messages"""
        if match := re.search(r':(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :(.*)', data):
            username, message = match.groups()
            timestamp = GLib.DateTime.new_now_local().format("%H:%M")
            msg = ChatMessage(timestamp, username, message)
            GLib.idle_add(self._append_message, msg)

    def _load_stored_messages(self):
        """Load existing messages from the store"""
        if self.has_loaded_stored:
            return
            
        messages = ChatStore.get_messages(self.streamer)
        if messages:
            # First load all stored messages
            for msg in messages:
                # Add to view without re-storing
                self._append_message(msg, store=False)
            
            # Then add a single separator after all messages
            end = self.chat_buffer.get_end_iter()
            self.chat_buffer.insert_with_tags_by_name(
                end,
                "\n─────── Previous Messages ───────\n\n",
                "separator"
            )
        
        self.has_loaded_stored = True

    def _append_message(self, msg: ChatMessage, store=True):
        """Append message to chat view"""
        if store:
            # Store the message in ChatStore
            ChatStore.add_message(self.streamer, msg)
    
        if self.message_count >= self.max_messages:
            start = self.chat_buffer.get_start_iter()
            end = start.copy()
            end.forward_line()
            self.chat_buffer.delete(start, end)
            self.message_count -= 1
    
        end = self.chat_buffer.get_end_iter()
        self.chat_buffer.insert_with_tags_by_name(end, f"[{msg.timestamp}] ", "timestamp")
        self.chat_buffer.insert_with_tags_by_name(end, f"{msg.username}: ", "username")
        
        # Split message and check for emotes
        words = msg.message.split()
        for word in words:
            # Emotes are loaded on demand when they appear
            if pixbuf := self.emote_cache.get_emote_pixbuf(word):
                image = Gtk.Image.new_from_pixbuf(pixbuf)
                anchor = self.chat_buffer.create_child_anchor(end)
                self.chat_view.add_child_at_anchor(image, anchor)
                self.chat_buffer.insert(end, " ")
            else:
                self.chat_buffer.insert_with_tags_by_name(end, f"{word} ", "message")
        
        # Add a newline after each message
        self.chat_buffer.insert(end, "\n")

        self.message_count += 1

        if self.autoscroll:
            mark = self.chat_buffer.create_mark(None, end, False)
            self.chat_view.scroll_mark_onscreen(mark)
        return False
    
    def _on_scroll_changed(self, adj):
        """Handle manual scrolling"""
        # Check if we're near the bottom
        max_val = adj.get_upper() - adj.get_page_size()
        is_at_bottom = abs(adj.get_value() - max_val) < 10
        
        # Update autoscroll state and button visibility
        self.autoscroll = is_at_bottom
        self.scroll_button.set_visible(not is_at_bottom)
    
    def _on_adjustment_changed(self, adj):
        """Handle content size changes"""
        if self.autoscroll:
            adj.set_value(adj.get_upper() - adj.get_page_size())
    
    def _on_scroll_button_clicked(self, button):
        """Handle scroll to bottom button click"""
        adj = self.scrolled.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
    
    def _show_error(self, message):
        """Show error message in chat view"""
        end = self.chat_buffer.get_end_iter()
        self.chat_buffer.insert_with_tags_by_name(
            end, 
            f"Error: {message}\n",
            "timestamp"
        )
        return False
    
    def _on_play_clicked(self, button):
        """Handle play button click"""
        try:
            window = self.get_root()
            if window.player.play_content(f"twitch.tv/{self.streamer}", is_vod=False):
                self.show_toast("Playback starting...", 2)
        except Exception as e:
            self.show_toast(f"Error: {str(e)}", 4)
    
    def show_toast(self, text, timeout=2):
        """Show a toast notification"""
        toast = Adw.Toast.new(text)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)
    
    def do_destroy(self):
        """Clean up when page is destroyed"""
        self.running = False  # Signal IRC thread to stop
        try:
            self.irc.shutdown(socket.SHUT_RDWR)
            self.irc.close()
        except Exception:
            pass
        super().do_destroy()