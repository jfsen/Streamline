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

class ChatPage(Adw.NavigationPage):
    def __init__(self, streamer):
        super().__init__(title=f"{streamer}'s Chat")
        self.streamer = streamer
        self.running = True
        self.irc = None  # Initialize IRC socket reference
        self.autoscroll = True
        self.max_messages = 300
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
        
        # Add connection status indicator to header
        self.status_button = Gtk.Button()
        self.status_button.add_css_class("flat")
        self.status_button.add_css_class("circular")
        self.status_button.set_sensitive(False)  # Make it non-clickable
        self.status_button.set_tooltip_text("Disconnected")
        self._set_connection_status(False)  # Start with disconnected state
        self.header.pack_start(self.status_button)
        
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
                font-size: 12pt;
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
            .success {
                color: @success_color;
            }
            .error {
                color: @error_color;
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
        
        self.running = True  # Add flag to control IRC thread
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5  # seconds
        self.last_received = 0  # timestamp of last received data
        self.last_ping_sent = 0  # timestamp of last ping sent
        self.ping_interval = 30  # send ping every 30 seconds
        self.ping_timeout = 2   # wait 2 seconds for pong response
        self.waiting_for_pong = False
        self.watchdog_running = True
        self.max_attempts_reached = False  # Add new flag
        
        self.message_queue = []
        self.queue_timer = None
        self.batch_delay = 100  # ms
        
        self.last_cleanup = 0
        self.cleanup_interval = 5  # seconds
        
        # Connect to destroy signal before starting any threads/timers
        self.connect('destroy', self._on_destroy)
        
        # Connect to navigation signals
        self.connect('hidden', self._on_hidden)
        
        # Start threads and timers only after destroy handler is connected
        self._start_chat_system()
    
    def _start_chat_system(self):
        """Initialize chat system components"""
        # Start IRC connection
        self.connect_to_chat()
        
        # Start watchdog thread
        self.watchdog_thread = threading.Thread(target=self._connection_watchdog)
        self.watchdog_thread.daemon = True
        self.watchdog_thread.start()
    
    def connect_to_chat(self, reset_counter=True):
        """Initialize IRC connection in a separate thread"""
        if reset_counter:
            self.reconnect_attempts = 0
            self.max_attempts_reached = False
            self.watchdog_running = True
            
        # Start new IRC worker thread
        thread = threading.Thread(target=self._irc_worker)
        thread.daemon = True
        thread.start()
    
    def _irc_worker(self):
        """Handle IRC connection and message processing"""
        try:
            print(f"[DEBUG] Starting IRC connection for {self.streamer}'s chat")
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
            
            # Update status to connected
            GLib.idle_add(self._set_connection_status, True)
            print("[DEBUG] Successfully connected to IRC")
            
            # Reset reconnect counter on successful connection
            self.reconnect_attempts = 0
            
            # Message processing loop
            while self.running:
                try:
                    data = self.irc.recv(4096).decode('utf-8')
                    self.last_received = GLib.get_monotonic_time() / 1000000
                    
                    if not data:
                        print("[DEBUG] No data received, connection might be closed")
                        break
                    
                    # Handle PING/PONG
                    if data.startswith('PING'):
                        print("[DEBUG] Received PING, sending PONG")
                        self.irc.send('PONG\r\n'.encode())
                    elif 'PONG' in data:
                        print("[DEBUG] Received PONG response")
                        self.waiting_for_pong = False
                    
                    # Process chat messages
                    if 'PRIVMSG' in data:
                        self._process_message(data)
                        
                except socket.timeout:
                    print("[DEBUG] Socket timeout, continuing...")
                    continue
                    
        except Exception as e:
            if self.running:
                print(f"[DEBUG] IRC worker error: {e}")
                GLib.idle_add(self._handle_disconnect)
    
    def _process_message(self, data):
        """Process and display chat messages"""
        if match := re.search(r':(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :(.*)', data):
            username, message = match.groups()
            # Remove control characters
            message = re.sub(r'[\x00-\x1F\x7F]', '', message)
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
        #self.chat_buffer.insert_with_tags_by_name(end, f"[{msg.timestamp}] ", "timestamp")
        self.chat_buffer.insert_with_tags_by_name(end, f"{msg.username}: ", "username")

        # Split message and check for emotes
        words = msg.message.split()
        for word in words:
            # Emotes are loaded on demand when they appear
            if pixbuf := self.emote_cache.get_emote_pixbuf(word):
                image = Gtk.Image.new_from_pixbuf(pixbuf)
                image.set_size_request(28, 28)
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
    
    def _set_connection_status(self, connected: bool):
        """Update the connection status indicator"""
        if connected:
            self.status_button.set_icon_name("network-transmit-receive-symbolic")
            self.status_button.add_css_class("success")
            self.status_button.remove_css_class("error")
            self.status_button.set_tooltip_text(f"Connected to {self.streamer}'s chat")
        else:
            self.status_button.set_icon_name("network-offline-symbolic")
            self.status_button.add_css_class("error")
            self.status_button.remove_css_class("success")
            self.status_button.set_tooltip_text("Disconnected")
    
    def _connection_watchdog(self):
        """Monitor connection status using PING/PONG"""
        print("[DEBUG] Starting connection watchdog")
        while self.watchdog_running:
            if self.irc and self.running:
                current_time = GLib.get_monotonic_time() / 1000000
                time_since_last_data = current_time - self.last_received
                                
                # Only send ping if we haven't received data within ping interval
                if not self.waiting_for_pong and time_since_last_data > self.ping_interval:
                    print("[DEBUG] No data for {:.1f}s - sending PING".format(time_since_last_data))
                    try:
                        self.irc.send("PING :tmi.twitch.tv\r\n".encode())
                        self.last_ping_sent = current_time
                        self.waiting_for_pong = True
                    except Exception as e:
                        print(f"[DEBUG] Failed to send PING: {e}")
                        self.show_toast("Connection lost - attempting to reconnect", 4)
                        GLib.idle_add(self._handle_disconnect)
                
                # Check for PONG timeout
                elif self.waiting_for_pong and (current_time - self.last_ping_sent) > self.ping_timeout:
                    print("[DEBUG] PONG response timeout - connection lost")
                    self.show_toast("Connection lost - attempting to reconnect", 4)
                    GLib.idle_add(self._handle_disconnect)
                    
            GLib.usleep(1000000)  # Check every second
        print("[DEBUG] Watchdog thread stopping")

    def _handle_disconnect(self):
        """Handle disconnection and attempt reconnect"""
        # Don't handle new disconnects if we've already hit max attempts
        if self.max_attempts_reached:
            return False

        print(f"[DEBUG] Handling disconnect (attempt {self.reconnect_attempts + 1}/{self.max_reconnect_attempts})")
        self._set_connection_status(False)
        self._cleanup_socket()
        
        self.reconnect_attempts += 1
        
        if self.reconnect_attempts <= self.max_reconnect_attempts:
            print(f"[DEBUG] Scheduling reconnect in {self.reconnect_delay} seconds")
            # Schedule reconnect
            GLib.timeout_add_seconds(self.reconnect_delay, self._attempt_reconnect)
        else:
            print("[DEBUG] Max reconnection attempts reached")
            self.max_attempts_reached = True
            self.watchdog_running = False
            
        return False

    def _attempt_reconnect(self):
        """Attempt to reconnect to chat"""
        if not self.running:
            print("[DEBUG] Not reconnecting - page is shutting down")
            return False
            
        print("[DEBUG] Attempting to reconnect")
        self.connect_to_chat(reset_counter=False)
        return False

    def _cleanup_socket(self):
        """Centralized socket cleanup"""
        try:
            if self.irc:
                self.irc.shutdown(socket.SHUT_RDWR)
                self.irc.close()
                self.irc = None
                self.waiting_for_pong = False
        except Exception:
            self.irc = None
            
    def _on_hidden(self, page):
        """Called when page is hidden (navigated away from)"""
        print("[DEBUG] Chat page hidden, initiating cleanup")
        self._on_destroy()
        
    def _on_destroy(self, *args):
        """Cleanup when page is destroyed"""
        # Prevent multiple cleanup calls
        if not hasattr(self, 'running') or not self.running:
            return
            
        print("[DEBUG] Cleaning up chat page")
        
        # Stop watchdog first
        self.watchdog_running = False
        if hasattr(self, 'watchdog_thread'):
            self.watchdog_thread.join(timeout=1.0)
        
        # Stop IRC connection
        self.running = False
        if hasattr(self, 'irc') and self.irc:
            try:
                self.irc.shutdown(socket.SHUT_RDWR)
                self.irc.close()
            except:
                pass
            self.irc = None
        
        # Stop any pending timers
        if hasattr(self, 'queue_timer') and self.queue_timer:
            GLib.source_remove(self.queue_timer)
            self.queue_timer = None
            
        if hasattr(self, 'animation_timer') and self.animation_timer:
            GLib.source_remove(self.animation_timer)
            self.animation_timer = None
        
        # Stop animation timers
        for anim_state in self.emote_animations.values():
            if anim_state['timer_id']:
                GLib.source_remove(anim_state['timer_id'])
        self.emote_animations.clear()
        
        # Clear all collections
        if hasattr(self, 'message_queue'):
            self.message_queue.clear()
        if hasattr(self, 'animated_emotes'):
            self.animated_emotes.clear()
        if hasattr(self, 'emote_lookup_cache'):
            self.emote_lookup_cache.clear()
        
        # Clear text buffer last
        if hasattr(self, 'chat_buffer'):
            self.chat_buffer.set_text("")
        
        print("[DEBUG] Chat page cleanup complete")