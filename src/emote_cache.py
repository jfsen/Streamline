from pathlib import Path
import requests
from gi.repository import GdkPixbuf, Gio, GLib, Gdk  # Add Gdk import
from typing import Dict, Optional, Union
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
        self.pixbufs: Dict[str, Union[GdkPixbuf.Pixbuf, Gdk.Texture]] = {}  # Update type hint
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
            
            # Cache empty result if no user_id found
            if not user_id:
                print(f"[DEBUG] No user ID found for {channel}")
                empty_cache = {
                    "timestamp": time.time(),
                    "emotes": []
                }
                self._save_bttv_cache(channel, empty_cache, force=False)
                return

            print(f"[DEBUG] Fetching BTTV emotes for {channel}")
            bttv_url = f"https://api.betterttv.net/3/cached/users/twitch/{user_id}"
            response = requests.get(bttv_url)

            # Cache empty result on API failure
            if response.status_code != 200:
                print(f"[DEBUG] Failed to fetch BTTV emotes: {response.status_code}")
                empty_cache = {
                    "timestamp": time.time(),
                    "emotes": []
                }
                self._save_bttv_cache(channel, empty_cache, force=False)
                return

            data = response.json()
            channel_emotes = data.get("channelEmotes", [])
            shared_emotes = data.get("sharedEmotes", [])
            all_emotes = channel_emotes + shared_emotes

            # Update cache (even if empty)
            cache_data = {
                "timestamp": time.time(),
                "emotes": all_emotes
            }
            self._save_bttv_cache(channel, cache_data, force=True)

            print(f"[DEBUG] Found {len(channel_emotes)} channel BTTV emotes")
            print(f"[DEBUG] Found {len(shared_emotes)} shared BTTV emotes")

            for emote in all_emotes:
                name = emote["code"]
                url = f"https://cdn.betterttv.net/emote/{emote['id']}/1x"
                self.emote_urls[name] = url

        except Exception as e:
            print(f"[DEBUG] Error fetching emotes: {e}")
            # Cache empty result on any error
            empty_cache = {
                "timestamp": time.time(),
                "emotes": []
            }
            self._save_bttv_cache(channel, empty_cache, force=False)

    def get_emote_pixbuf(self, name: str) -> Optional[Union[GdkPixbuf.Pixbuf, GdkPixbuf.PixbufAnimation]]:
        """Get or load emote"""
        if name in self.pixbufs:
            return self.pixbufs[name]
            
        if name in self.emote_urls:
            try:
                url = self.emote_urls[name]
                
                # First check if we already downloaded the emote
                for ext in ['.gif', '.png', '.webp']:
                    path = self.emotes_dir / f"{name}{ext}"
                    if path.exists():
                        break
                else:  # No existing file found, download it
                    print(f"[DEBUG] Downloading emote: {name} from {url}")
                    response = requests.get(url)
                    if response.status_code != 200:
                        print(f"[DEBUG] Failed to download emote {name}: HTTP {response.status_code}")
                        return None

                    # Get file extension from content-type
                    content_type = response.headers.get('content-type', '')
                    if 'gif' in content_type:
                        ext = '.gif'
                    elif 'png' in content_type:
                        ext = '.png'
                    else:
                        ext = '.webp'  # default to webp
                    
                    path = self.emotes_dir / f"{name}{ext}"
                    path.write_bytes(response.content)
                    print(f"[DEBUG] Successfully saved emote to {path}")
                
                print(f"[DEBUG] Loading emote from {path}")
                
                # Use GdkPixbufAnimation for GIFs
                if path.suffix == '.gif':
                    anim = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
                    self.pixbufs[name] = anim
                    return anim
                else:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
                    self.pixbufs[name] = pixbuf
                    return pixbuf

            except Exception as e:
                print(f"[DEBUG] Error loading emote {name}: {e}")
                
        return None