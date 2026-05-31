import gettext
import logging

import gi

gi.require_version("Gio", "2.0")

from gi.repository import Gio, GLib  # noqa: E402

_ = gettext.gettext

logger = logging.getLogger("Config")


class ConfigManager:
    def __init__(self):
        self.settings = Gio.Settings.new("io.github.jfsen.Streamline")

    def load(self):
        """Load config as a dict (compatible with old interface)."""
        return {
            "streamers": list(self.settings.get_value("streamers")),
            "player_type": self.settings.get_string("player-type"),
            "custom_player_path": self.settings.get_string("custom-player-path"),
            "stream_quality": self.settings.get_string("stream-quality"),
            "custom_quality": self.settings.get_string("custom-quality"),
            "twitch_client_id": self.settings.get_string("twitch-client-id"),
            "twitch_client_secret": self.settings.get_string("twitch-client-secret"),
            "theme": self.settings.get_string("theme"),
            "low_latency": self.settings.get_boolean("low-latency"),
            "chat_alternating_bg": self.settings.get_boolean(
                "chat-alternating-background"
            ),
            "chat_pause_emotes": self.settings.get_boolean("chat-pause-emotes"),
        }

    def save(self, config):
        """Save config from a dict into GSettings."""
        self.settings.set_value(
            "streamers", GLib.Variant("as", config.get("streamers", []))
        )
        self.settings.set_string("player-type", config.get("player_type", "mpv"))
        self.settings.set_string(
            "custom-player-path", config.get("custom_player_path", "")
        )
        self.settings.set_string("stream-quality", config.get("stream_quality", "High"))
        self.settings.set_string("custom-quality", config.get("custom_quality", "best"))
        self.settings.set_string("twitch-client-id", config.get("twitch_client_id", ""))
        self.settings.set_string(
            "twitch-client-secret", config.get("twitch_client_secret", "")
        )
        self.settings.set_string("theme", config.get("theme", "system"))
        self.settings.set_boolean("low-latency", config.get("low_latency", True))
        self.settings.set_boolean(
            "chat-alternating-background", config.get("chat_alternating_bg", True)
        )
        self.settings.set_boolean(
            "chat-pause-emotes", config.get("chat_pause_emotes", False)
        )
        try:
            self.settings.apply()
            return True
        except Exception as e:
            logger.debug("Failed to save settings: %s", e)
            return False

    def create_config_dict(self, window):
        """Create config dict from window attributes."""
        return {
            "twitch_client_id": window.client_id,
            "twitch_client_secret": window.client_secret,
            "player_type": window.player_type,
            "custom_player_path": window.custom_player_path,
            "streamers": window.all_streamers,
            "stream_quality": window.stream_quality,
            "custom_quality": window.custom_quality,
            "theme": window.theme,
            "low_latency": window.low_latency,
            "chat_alternating_bg": window.chat_alternating_bg,
            "chat_pause_emotes": window.chat_pause_emotes,
        }
