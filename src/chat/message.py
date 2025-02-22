import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Pango
from dataclasses import dataclass, field
from typing import Optional, List, Callable
from .emote_store import EmoteStore
from .emote_renderer import EmoteRenderer

@dataclass
class ChatMessage:
    """Represents a single chat message with associated metadata"""
    timestamp: str
    username: str
    message: str
    emotes: dict = field(default_factory=dict)
    _words: Optional[List[str]] = None

    @property
    def words(self) -> List[str]:
        """Get cached word list"""
        if self._words is None:
            self._words = self.message.split()
        return self._words

    @classmethod
    def from_irc_data(cls, username: str, message: str, emote_data: str = '') -> 'ChatMessage':
        """Create a message from IRC data"""
        timestamp = GLib.DateTime.new_now_local().format("%H:%M")
        msg = cls(
            timestamp=timestamp,
            username=username,
            message=message,
            emotes=cls._parse_emotes(emote_data)
        )
        # Pre-split words for efficiency
        msg.words
        return msg

    @staticmethod
    def _parse_emotes(emote_data: str) -> dict:
        """Parse Twitch emote data into a usable format"""
        emotes = {}
        if not emote_data:
            return emotes
            
        for emote in emote_data.split('/'):
            if ':' not in emote:
                continue
            emote_id, positions = emote.split(':')
            emotes[emote_id] = [
                tuple(map(int, pos.split('-')))
                for pos in positions.split(',')
            ]
        return emotes

class MessageQueue:
    def __init__(self, batch_delay: int = 100):
        self.messages: List[ChatMessage] = []
        self.batch_delay = batch_delay
        self.timer_id = None
        
    def append(self, msg: ChatMessage, process_callback) -> None:
        """Add message and schedule processing if needed"""
        self.messages.append(msg)
        
        if not self.timer_id:
            self.timer_id = GLib.timeout_add(
                self.batch_delay, 
                self._process_batch,
                process_callback
            )

    def _process_batch(self, callback) -> bool:
        """Process all queued messages"""
        self.timer_id = None
        if not self.messages:
            return False
            
        # Process all queued messages
        messages = self.messages.copy()
        self.messages.clear()
        
        callback(messages)
        return False

    def clear(self) -> None:
        """Clear message queue and cancel timer"""
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = None
        self.messages.clear()

class MessageBuffer:
    """Handles chat message display and management"""
    def __init__(self, text_view: Gtk.TextView, max_messages: int = 300):
        self.text_view = text_view
        self.buffer = text_view.get_buffer()
        self.max_messages = max_messages
        self.message_count = 0
        self._setup_tags()

    def _setup_tags(self):
        """Create text buffer tags for message formatting"""
        self.buffer.create_tag("timestamp", foreground="gray")
        self.buffer.create_tag("username", weight=Pango.Weight.BOLD)
        self.buffer.create_tag("message")
        self.buffer.create_tag("error", foreground="red")

    def append_messages(self, messages: List[ChatMessage], 
                       emote_store: EmoteStore,
                       emote_renderer: EmoteRenderer,
                       animate_emotes: bool) -> Gtk.TextIter:
        """Process and append a batch of messages efficiently"""
        self.buffer.begin_user_action()
        self._cleanup_old_messages()
        
        end = self.buffer.get_end_iter()
        for msg in messages:
            # Add username with timestamp
            self.buffer.insert_with_tags_by_name(
                end, 
                f"{msg.username}: ", 
                "username"
            )
            
            # Process words
            for word in msg.words:
                emote = emote_store.get_emote(word)
                if emote:
                    image = emote_renderer.create_emote_picture(emote, animate_emotes)
                    anchor = self.buffer.create_child_anchor(end)
                    self.text_view.add_child_at_anchor(image, anchor)
                    self.buffer.insert(end, " ")
                else:
                    self.buffer.insert_with_tags_by_name(end, f"{word} ", "message")
            
            self.buffer.insert(end, "\n")
            self.message_count += 1
        
        self.buffer.end_user_action()
        return self.buffer.get_end_iter()

    def _cleanup_old_messages(self) -> None:
        """Remove old messages when limit is reached"""
        if self.message_count <= self.max_messages:
            return
            
        cleanup_count = self.max_messages // 3
        while self.message_count > (self.max_messages - cleanup_count):
            start = self.buffer.get_start_iter()
            if not start.forward_line():
                break
            
            self.buffer.delete(
                self.buffer.get_start_iter(),
                start
            )
            self.message_count -= 1

    def show_error(self, message: str) -> None:
        """Display an error message in the chat"""
        end = self.buffer.get_end_iter()
        self.buffer.insert_with_tags_by_name(end, f"Error: {message}\n", "error")

    def clear(self) -> None:
        """Clear all messages"""
        self.buffer.set_text("")
        self.message_count = 0