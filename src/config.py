import os
import json
from pathlib import Path

class ConfigManager:
    def __init__(self):
        self.config_path = self._get_config_path()
        self.default_config = {
            "streamers": [],
            "streamlink_path": "/usr/bin/streamlink",
            "mpv_path": "/usr/bin/mpv",
            "vlc_path": "/usr/bin/vlc",
            "player_type": "mpv",
            "custom_player_path": "",
            "stream_quality": "best",
            "twitch_client_id": "",
            "twitch_client_secret": ""
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

    def load(self):
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