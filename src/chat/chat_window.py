"""Standalone chat window for detached chat."""

import gettext
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw

_ = gettext.gettext
logger = logging.getLogger("ChatWindow")


class ChatWindow(Adw.Window):
    """A self-contained window that hosts a read-only Twitch chat."""

    def __init__(
        self,
        twitch,
        streamer,
        display_name=None,
        alternating_bg=False,
        disable_emote_animations=False,
        theme="system",
        transient_for=None,
        highlight_first_msg=True,
        highlight_mod=True,
        highlight_vip=True,
        highlight_partner=True,
        highlight_broadcaster=True,
    ):
        super().__init__(
            title=_("Chat: {}").format(display_name or streamer),
        )
        self.set_default_size(360, 520)

        if transient_for:
            self.set_transient_for(transient_for)

        from .page import ChatPage

        self._chat_page = ChatPage(
            parent=None,
            streamer=streamer,
            display_name=display_name,
            alternating_bg=alternating_bg,
            disable_emote_animations=disable_emote_animations,
            theme=theme,
            twitch=twitch,
            enable_detach=False,
            highlight_first_msg=highlight_first_msg,
            highlight_mod=highlight_mod,
            highlight_vip=highlight_vip,
            highlight_partner=highlight_partner,
            highlight_broadcaster=highlight_broadcaster,
        )
        self.set_content(self._chat_page)

        self.connect("close-request", self._on_close_request)

    def _on_close_request(self, window):
        """Clean up the chat connection when the window is closed."""
        self._chat_page.cleanup()
        return False  # Allow the window to close normally
