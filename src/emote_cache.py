from pathlib import Path
import requests
from gi.repository import GdkPixbuf
from typing import Dict, Optional
import json

class EmoteCache:
    def __init__(self):
        self.cache_dir = Path.home() / ".cache" / "Streamline" / "emotes"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.emote_urls: Dict[str, str] = {}
        self.pixbufs: Dict[str, GdkPixbuf.Pixbuf] = {}
        self.user_id_cache = self._load_user_id_cache()

    def _load_user_id_cache(self):
        """Load user IDs from cache file."""
        cache_path = Path.home() / ".cache" / "Streamline" / "user_ids.json"
        try:
            with open(cache_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            return {}

    def fetch_emote_data(self, channel: str) -> None:
        """Fetch emote metadata using BTTV's API"""
        try:
            user_id = self.user_id_cache.get(channel)
            if not user_id:
                return
            
            # Fetch BTTV emotes for the channel
            bttv_url = f"https://api.betterttv.net/3/cached/users/twitch/{user_id}"
            response = requests.get(bttv_url)
            
            if response.status_code != 200:
                return
            
            data = response.json()
            emotes = data.get("channelEmotes", []) + data.get("sharedEmotes", [])
            
            for emote in emotes:
                name = emote["code"]
                url = f"https://cdn.betterttv.net/emote/{emote['id']}/1x"
                self.emote_urls[name] = url
        except Exception as e:
            print(f"[DEBUG] Error fetching emote data: {e}")

    def get_emote_pixbuf(self, name: str) -> Optional[GdkPixbuf.Pixbuf]:
        """Get or load emote pixbuf"""
        if name in self.pixbufs:
            return self.pixbufs[name]
            
        if name in self.emote_urls:
            try:
                path = self.cache_dir / f"{name}.webp"
                if not path.exists():
                    response = requests.get(self.emote_urls[name])
                    if response.status_code == 200:
                        path.write_bytes(response.content)
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
                self.pixbufs[name] = pixbuf
                return pixbuf
            except Exception as e:
                pass
                
        return None