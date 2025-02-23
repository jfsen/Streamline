import os
import json
from pathlib import Path
from gi.repository import Adw

class ConfigManager:
    def __init__(self):
        self.config_path = self.get_config_path()
        self.default_config = {
            "streamers": [],
            "streamlink_path": "/usr/bin/streamlink",
            "mpv_path": "/usr/bin/mpv",
            "vlc_path": "/usr/bin/vlc",
            "player_type": "mpv",
            "custom_player_path": "",
            "stream_quality": "best",
            "twitch_client_id": "",
            "twitch_client_secret": "",
            "narrow_mode": False,
            "theme": "system",
            "animate_emotes": False,
        }

    def _get_config_path(self):
        """Get the path to the config file."""
        if os.path.exists('/.flatpak-info'):
            config_dir = Path(os.environ.get('XDG_CONFIG_HOME', 
                        Path.home() / '.var/app/io.github.jfsen.Streamline/config')) / "Streamline"
        else:
            config_dir = Path.home() / ".config" / "Streamline"
        
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"

    def get_config_path(self):
        """Get the path to the config file."""
        # Check if running in Flatpak
        if os.path.exists('/.flatpak-info'):
            config_dir = Path(os.environ.get('XDG_CONFIG_HOME', 
                        Path.home() / '.var/app/io.github.jfsen.Streamline/config')) / "Streamline"
        else:
            config_dir = Path.home() / ".config" / "Streamline"
        
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "config.json"

    def create_config_dict(self, window):
        config = {
            "twitch_client_id": window.client_id,
            "twitch_client_secret": window.client_secret,
            "player_type": window.player_type,
            "custom_player_path": window.custom_player_path,
            "streamlink_path": window.streamlink_path,
            "mpv_path": window.mpv_path,
            "vlc_path": window.vlc_path,
            "streamers": window.all_streamers,
            "stream_quality": window.stream_quality,
            "narrow_mode": window.narrow_mode,
            "theme": window.theme,
            "animate_emotes": window.animate_emotes,
        }
        return config

    def load(self):
        config = self._load_from_file()
        config.setdefault("animate_emotes", False)
        config.setdefault("theme", "system")
        
        return config

    def _load_from_file(self):
        """Load configuration from file."""
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r') as f:
                    return json.load(f)
            else:
                with open(self.config_path, 'w') as f:
                    json.dump(self.default_config, f, indent=4)
                return self.default_config
        except (json.JSONDecodeError, OSError):
            return self.default_config

    def save(self, config):
        """Save configuration to file."""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=4)
            return True
        except OSError:
            return False