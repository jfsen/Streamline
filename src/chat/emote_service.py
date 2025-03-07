"""Handles fetching emotes from Twitch and BTTV"""
from pathlib import Path
import requests
import json
import time
from typing import Dict, Optional
from .global_twitch_emotes import GLOBAL_EMOTES

class EmoteService:
    def __init__(self):
        self.cache_dir = Path.home() / ".cache" / "Streamline"
        self.bttv_cache_dir = self.cache_dir / "bttv"
        self.bttv_cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_id_cache = self._load_user_id_cache()
        self.emote_urls: Dict[str, str] = {}
        
    def fetch_emotes(self, channel: str) -> None:
        """Fetch all emotes for a channel"""
        print(f"[DEBUG] Fetching emotes for channel: {channel}")
        # Start with global emotes
        self._fetch_global_twitch_emotes()
        self._fetch_global_bttv_emotes()
        
        # Then fetch channel-specific emotes
        user_id = self._get_user_id(channel)
        if user_id:
            self._fetch_bttv_channel_emotes(channel, user_id)
    
    def _fetch_global_twitch_emotes(self):
        """Fetch global Twitch emotes"""
        print("[DEBUG] Using hardcoded global Twitch emotes")
        for name, emote_id in GLOBAL_EMOTES.items():
            url = f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/default/dark/1.0"
            self.emote_urls[name] = url
    
    def _fetch_global_bttv_emotes(self):
        """Fetch global BTTV emotes"""
        try:
            cached = self._load_bttv_cache("global")
            if cached:
                print("[DEBUG] Using cached global BTTV emotes")
                self.emote_urls.update(cached)
                return
                
            print("[DEBUG] Fetching global BTTV emotes")
            response = requests.get("https://api.betterttv.net/3/cached/emotes/global")
            if response.status_code == 200:
                emotes = {e['code']: f"https://cdn.betterttv.net/emote/{e['id']}/3x"
                         for e in response.json()}
                self.emote_urls.update(emotes)
                self._save_bttv_cache("global", emotes)
                
        except Exception as e:
            print(f"[DEBUG] Error fetching global BTTV emotes: {e}")

    def _fetch_bttv_channel_emotes(self, channel: str, user_id: str):
        """Fetch channel BTTV emotes"""
        try:
            cached = self._load_bttv_cache(channel)
            if cached:
                print(f"[DEBUG] Using cached BTTV emotes for {channel}")
                self.emote_urls.update(cached)
                return
                
            print(f"[DEBUG] Fetching BTTV emotes for {channel}")
            response = requests.get(f"https://api.betterttv.net/3/cached/users/twitch/{user_id}")
            if response.status_code == 200:
                data = response.json()
                emotes = {}
                
                # Channel emotes
                for e in data.get('channelEmotes', []):
                    emotes[e['code']] = f"https://cdn.betterttv.net/emote/{e['id']}/1x"
                    
                # Shared emotes
                for e in data.get('sharedEmotes', []):
                    emotes[e['code']] = f"https://cdn.betterttv.net/emote/{e['id']}/1x"
                    
                print(f"[DEBUG] Found {len(emotes)} channel BTTV emotes")
                self.emote_urls.update(emotes)
                self._save_bttv_cache(channel, emotes)
                
        except Exception as e:
            print(f"[DEBUG] Error fetching BTTV emotes for {channel}: {e}")

    def _load_user_id_cache(self):
        """Load user IDs from cache file."""
        cache_path = Path.home() / ".cache" / "Streamline" / "users.json"
        try:
            with open(cache_path) as f:
                data = json.load(f)
                # Convert from new combined cache format to simple user_id cache
                return data.get('ids', {})
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            return {}

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

    def _save_bttv_cache(self, channel: str, data: dict, force: bool = False):
        """Save BTTV emotes cache for a specific channel"""
        try:
            cache_file = self.bttv_cache_dir / f"{channel}.json"
            
            # If not forcing and file exists, keep existing data on failures
            if not force and cache_file.exists():
                try:
                    existing = json.loads(cache_file.read_text())
                    if "emotes" in existing and len(existing["emotes"]) > 0:
                        print(f"[DEBUG] Keeping existing cache for {channel}")
                        return
                except (json.JSONDecodeError, OSError):
                    pass
            
            # Only store essential data: emote code, id and timestamp
            data = {
                "timestamp": data["timestamp"],
                "emotes": [{"code": e["code"], "id": e["id"]} for e in data["emotes"]]
            }
            cache_file.write_text(json.dumps(data, indent=4))
        except OSError as e:
            print(f"[DEBUG] Error saving BTTV cache for {channel}: {e}")

    def _get_user_id(self, channel: str) -> Optional[str]:
        """Get user ID for a channel"""
        return self.user_id_cache.get(channel)