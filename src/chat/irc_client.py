from gi.repository import GLib
import socket
import ssl
import threading
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Optional

class ConnectionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()

class TwitchIRCClient:
    def __init__(self, 
                 channel: str,
                 on_message: Callable[[str, str, str], None],
                 on_state_changed: Callable[[ConnectionState], None]):
        """Initialize IRC client"""
        self.channel = f"#{channel}"
        self.on_message = on_message
        self.on_state_changed = on_state_changed
        
        # Connection settings
        self.server = "irc.chat.twitch.tv"
        self.port = 6697
        self.nickname = f"justinfan{GLib.random_int_range(1000, 99999)}"
        
        # Connection state
        self._irc: Optional[ssl.SSLSocket] = None
        self._running = False
        self._watchdog_running = False
        self._state = ConnectionState.DISCONNECTED
        
        # Reconnection settings
        self._reconnect_attempts = 0
        self.max_reconnect_attempts = 20
        self.base_reconnect_delay = 5
        self.max_reconnect_delay = 300  # 5 minutes max
        
        # Add connection error tracking
        self._last_error = None
        self._connection_errors = 0
        
        # Ping/Pong timestamps
        self._last_received = 0
        self._last_ping_sent = 0
        self._waiting_for_pong = False
        self.ping_interval = 30
        self.ping_timeout = 2

        self._connection_thread = None  # Add thread tracking

    def start(self):
        """Start IRC client and watchdog"""
        self._running = True
        self._watchdog_running = True
        self._reconnect_attempts = 0
        self.connect()
        
        # Start watchdog thread
        self._watchdog_thread = threading.Thread(target=self._connection_watchdog)
        self._watchdog_thread.daemon = True
        self._watchdog_thread.start()

    def stop(self):
        """Stop IRC client and cleanup"""
        print("[DEBUG] Stopping IRC client")
        self._running = False
        self._watchdog_running = False
        self._cleanup_socket()
        
        # Wait for connection thread to finish
        if self._connection_thread and self._connection_thread.is_alive():
            print("[DEBUG] Waiting for connection thread to finish")
            self._connection_thread.join(timeout=1.0)
        self._connection_thread = None

    def connect(self):
        """Start connection in a new thread"""
        if self._state != ConnectionState.DISCONNECTED:
            print("[DEBUG] Connect called while not disconnected, current state:", self._state)
            return
            
        # Stop any existing connection thread
        if self._connection_thread and self._connection_thread.is_alive():
            print("[DEBUG] Previous connection thread still alive, waiting for it to finish")
            self._connection_thread.join(timeout=1.0)
            
        print("[DEBUG] Starting new connection thread")
        self._connection_thread = threading.Thread(target=self._connect_worker)
        self._connection_thread.daemon = True
        self._connection_thread.start()

    def _connect_worker(self):
        """Handle IRC connection and message processing"""
        print("[DEBUG] Connection worker starting")
        try:
            self._set_state(ConnectionState.CONNECTING)
            
            # Resolve hostname first
            try:
                socket.getaddrinfo(self.server, self.port)
            except socket.gaierror as e:
                print(f"[DEBUG] DNS resolution failed: {e}")
                raise
            
            # Create new socket each attempt
            context = ssl.create_default_context()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(30)  # Add connection timeout
            
            try:
                self._irc = context.wrap_socket(sock, server_hostname=self.server)
                self._irc.connect((self.server, self.port))
            except (ssl.SSLError, socket.error) as e:
                print(f"[DEBUG] Connection failed: {e}")
                self._cleanup_socket()
                raise
            
            # Connection successful, setup IRC session
            self._irc.send(f"NICK {self.nickname}\r\n".encode())
            self._irc.send(f"JOIN {self.channel}\r\n".encode())
            
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_attempts = 0
            self._connection_errors = 0
            self._last_received = GLib.get_monotonic_time() / 1000000
            
            print(f"[DEBUG] Connected successfully, starting message loop")
            # Message processing loop
            while self._running:
                try:
                    data = self._irc.recv(4096).decode('utf-8')
                    self._last_received = GLib.get_monotonic_time() / 1000000
                    
                    if not data:
                        print("[DEBUG] No data received, connection lost")
                        raise ConnectionError("No data received")
                    
                    self._handle_data(data)
                        
                except socket.timeout:
                    continue
                    
        except Exception as e:
            print(f"[DEBUG] Connection worker error: {e}")
            if self._running:
                self._handle_disconnect()
        finally:
            print("[DEBUG] Connection worker exiting")

    def _handle_data(self, data: str) -> None:
        """Process received IRC data"""
        # Handle multi-line messages
        for line in data.split('\r\n'):
            if not line:
                continue
                
            # Handle PING/PONG
            if line.startswith('PING'):
                self._irc.send('PONG\r\n'.encode())
                continue
            elif 'PONG' in line:
                self._waiting_for_pong = False
                continue
                
            # Extract message tags if present
            tags = {}
            if line.startswith('@'):
                tags_str, line = line.split(' ', 1)
                for tag in tags_str[1:].split(';'):
                    if '=' in tag:
                        key, value = tag.split('=', 1)
                        tags[key] = value
            
            # Process chat messages
            if match := re.search(r':(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :(.*)', line):
                username, message = match.groups()
                # Remove control characters
                message = re.sub(r'[\x00-\x1F\x7F]', '', message)
                # Get emote data if present
                emote_data = tags.get('emotes', '') if tags else ''
                # Schedule callback on main thread
                GLib.idle_add(self.on_message, username, message, emote_data)

    def _connection_watchdog(self) -> None:
        """Monitor connection health with PING/PONG"""
        while self._watchdog_running:
            if self._irc and self._running and self._state == ConnectionState.CONNECTED:
                current_time = GLib.get_monotonic_time() / 1000000
                time_since_last = current_time - self._last_received
                
                if not self._waiting_for_pong and time_since_last > self.ping_interval:
                    try:
                        self._irc.send("PING :tmi.twitch.tv\r\n".encode())
                        self._last_ping_sent = current_time
                        self._waiting_for_pong = True
                    except Exception:
                        GLib.idle_add(self._handle_disconnect)
                
                elif self._waiting_for_pong:
                    if (current_time - self._last_ping_sent) > self.ping_timeout:
                        GLib.idle_add(self._handle_disconnect)
                    
            GLib.usleep(1000000)  # Sleep for 1 second

    def _handle_disconnect(self) -> None:
        """Handle disconnection with exponential backoff"""
        if self._state == ConnectionState.DISCONNECTED:
            print("[DEBUG] Already disconnected, skipping disconnect handler")
            return
            
        print("[DEBUG] Handling disconnect")
        self._set_state(ConnectionState.DISCONNECTED)
        self._cleanup_socket()
        
        if self._reconnect_attempts < self.max_reconnect_attempts:
            self._reconnect_attempts += 1
            
            # Calculate delay with exponential backoff
            delay = min(
                self.base_reconnect_delay * (2 ** (self._reconnect_attempts - 1)),
                self.max_reconnect_delay
            )
            
            print(f"[DEBUG] Scheduling reconnect attempt {self._reconnect_attempts} in {delay}s")
            GLib.timeout_add_seconds(delay, self._attempt_reconnect)
        else:
            print("[DEBUG] Max reconnection attempts reached")

    def _attempt_reconnect(self) -> bool:
        """Attempt to reconnect to chat"""
        if self._running:
            self.connect()
        return False

    def _cleanup_socket(self) -> None:
        """Clean up the socket connection"""
        if self._irc:
            try:
                self._irc.shutdown(socket.SHUT_RDWR)
                self._irc.close()
            except:
                pass
            finally:
                self._irc = None
                self._waiting_for_pong = False

    def _set_state(self, state: ConnectionState) -> None:
        """Update connection state and notify callback"""
        self._state = state
        GLib.idle_add(self.on_state_changed, state)