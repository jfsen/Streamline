from pathlib import Path
import requests
from gi.repository import GdkPixbuf
from typing import Dict, Optional
import json
import time
from .global_twitch_emotes import GLOBAL_EMOTES

class EmoteCache:
    def __init__(self):
        self.cache_dir = Path.home() / ".cache" / "Streamline"
        self.emotes_dir = self.cache_dir / "emotes"
        self.bttv_cache_dir = self.cache_dir / "bttv"
        # Create all required directories
        for directory in [self.emotes_dir, self.bttv_cache_dir]:
            directory.mkdir(parents=True, exist_ok=True)
        self.emote_urls: Dict[str, str] = {}
        self.pixbufs: Dict[str, GdkPixbuf.Pixbuf] = {}
        self.user_id_cache = self._load_user_id_cache()
        self._fetch_global_emotes()
        self._fetch_global_bttv_emotes()

    def _load_user_id_cache(self):
            """Load user IDs from cache file."""
            cache_path = Path.home() / ".cache" / "Streamline" / "user_ids.json"
            try:
                with open(cache_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError, FileNotFoundError):
                return {}

    def _fetch_global_emotes(self):
        """Fetch global Twitch emotes"""
        try:
            print("[DEBUG] Using hardcoded global Twitch emotes")
            for name, id in GLOBAL_EMOTES.items():
                url = f"https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/1.0"
                self.emote_urls[name] = url
        except Exception as e:
            print(f"[DEBUG] Error setting up global Twitch emotes: {e}")

    def _fetch_global_bttv_emotes(self):
        """Fetch global BTTV emotes"""
        try:
            # Check cache first
            cache_data = self._load_bttv_cache("global")
            if cache_data:
                print("[DEBUG] Using cached global BTTV emotes")
                for emote in cache_data["emotes"]:
                    name = emote["code"]
                    url = f"https://cdn.betterttv.net/emote/{emote['id']}/1x"
                    self.emote_urls[name] = url
                return

            print("[DEBUG] Fetching global BTTV emotes...")
            response = requests.get("https://api.betterttv.net/3/cached/emotes/global")
            
            if response.status_code != 200:
                print(f"[DEBUG] Failed to fetch global BTTV emotes: {response.status_code}")
                return
            
            emotes = response.json()
            print(f"[DEBUG] Found {len(emotes)} global BTTV emotes")
            
            # Cache the emotes
            cache_data = {
                "timestamp": time.time(),
                "emotes": [{"code": e["code"], "id": e["id"]} for e in emotes]
            }
            self._save_bttv_cache("global", cache_data)
            
            for emote in emotes:
                name = emote["code"]
                url = f"https://cdn.betterttv.net/emote/{emote['id']}/1x"
                self.emote_urls[name] = url
            
        except Exception as e:
            print(f"[DEBUG] Error fetching global BTTV emotes: {e}")

    def _load_bttv_cache(self, channel: str) -> Optional[dict]:
        """Load BTTV emotes cache for a specific channel"""
        try:
            cache_file = self.bttv_cache_dir / f"{channel}.json"
            if cache_file.exists():
                data = json.loads(cache_file.read_text())
                # Different cache durations for global vs channel emotes
                cache_duration = 604800 if channel == "global" else 259200  # 7 days vs 3 days
                if time.time() - data["timestamp"] < cache_duration:
                    return data
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def _save_bttv_cache(self, channel: str, data: dict):
        """Save BTTV emotes cache for a specific channel"""
        try:
            # Only store essential data: emote code, id and timestamp
            data = {
                "timestamp": data["timestamp"],
                "emotes": [{"code": e["code"], "id": e["id"]} for e in data["emotes"]]
            }
            cache_file = self.bttv_cache_dir / f"{channel}.json"
            cache_file.write_text(json.dumps(data, indent=4))
        except OSError as e:
            print(f"[DEBUG] Error saving BTTV cache for {channel}: {e}")

    def fetch_emote_data(self, channel: str) -> None:
        """Fetch emote metadata using BTTV's API"""
        try:
            # Check cache first
            cache_data = self._load_bttv_cache(channel)
            if cache_data:
                print(f"[DEBUG] Using cached BTTV emotes for {channel}")
                for emote in cache_data["emotes"]:
                    self.emote_urls[emote["code"]] = f"https://cdn.betterttv.net/emote/{emote['id']}/1x"
                return

            # Fetch new data if not cached or expired
            print(f"[DEBUG] Fetching user_id for channel: {channel}")
            user_id = self.user_id_cache.get(channel)
            if not user_id:
                print(f"[DEBUG] No user ID found for {channel}")
                return

            print(f"[DEBUG] Fetching BTTV emotes for {channel}")
            bttv_url = f"https://api.betterttv.net/3/cached/users/twitch/{user_id}"
            response = requests.get(bttv_url)

            if response.status_code != 200:
                print(f"[DEBUG] Failed to fetch BTTV emotes: {response.status_code}")
                return

            data = response.json()
            channel_emotes = data.get("channelEmotes", [])
            shared_emotes = data.get("sharedEmotes", [])
            all_emotes = channel_emotes + shared_emotes

            # Update cache
            cache_data = {
                "timestamp": time.time(),
                "emotes": all_emotes
            }
            self._save_bttv_cache(channel, cache_data)

            print(f"[DEBUG] Found {len(channel_emotes)} channel BTTV emotes")
            print(f"[DEBUG] Found {len(shared_emotes)} shared BTTV emotes")

            for emote in all_emotes:
                name = emote["code"]
                url = f"https://cdn.betterttv.net/emote/{emote['id']}/1x"
                self.emote_urls[name] = url

        except Exception as e:
            print(f"[DEBUG] Error fetching emotes: {e}")

    def get_emote_pixbuf(self, name: str) -> Optional[GdkPixbuf.Pixbuf]:
        """Get or load emote pixbuf"""
        if name in self.pixbufs:
            return self.pixbufs[name]
            
        if name in self.emote_urls:
            try:
                path = self.emotes_dir / f"{name}.webp"
                url = self.emote_urls[name]
                
                if not path.exists():
                    print(f"[DEBUG] Downloading emote: {name} from {url}")
                    response = requests.get(url)
                    if response.status_code == 200:
                        path.write_bytes(response.content)
                        print(f"[DEBUG] Successfully saved emote to {path}")
                    else:
                        print(f"[DEBUG] Failed to download emote {name}: HTTP {response.status_code}")
                        print(f"[DEBUG] Response content: {response.text[:200]}")  # First 200 chars of error
                        return None
                        
                print(f"[DEBUG] Loading emote from {path}")
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
                self.pixbufs[name] = pixbuf
                return pixbuf
                
            except requests.RequestException as e:
                print(f"[DEBUG] Network error downloading emote {name}: {e}")
            except Exception as e:
                print(f"[DEBUG] Error loading emote {name} from {self.emote_urls[name]}: {e}")
                
        return None