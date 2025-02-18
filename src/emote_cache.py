from pathlib import Path
import requests
from gi.repository import GdkPixbuf
from typing import Dict, Optional
import json
import time

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
            # Hard-coded list of (some) global Twitch emotes
            # to avoid making an API request
            emotes = {
                "KKona": "14",
                "Kappa": "25",
                "DansGame": "33",
                "SwiftRage": "34",
                "PJSalt": "36",
                "Kreygasm": "41",
                "SMOrc": "52",
                "FrankerZ": "65",
                "BloodTrail": "69",
                "PogChamp": "88",
                "BibleThump": "86",
                "4Head": "354",
                "FailFish": "360",
                "BrokeBack": "4057",
                "EleGiggle": "4339",
                "BabyRage": "22639",
                "WutFace": "28087",
                "ResidentSleeper": "245",
                "SeemsGood": "64138",
                "MingLee": "68856",
                "VoHiYo": "81274",
                "cmonBruh": "84608",
                "KappaPride": "55338",
                "NotLikeThis": "58765",
                "OpieOP": "100590",
                "Jebaited": "114836",
                "TriHard": "120232",
                "CoolStoryBob": "123171",
                "PunOko": "160401",
                "TehePelo": "160404",
                "TPFufun": "508650",
                "KEKW": "581875",
                "OMEGALUL": "583089",
                "LUL": "425618",
                "PepeLaugh": "897723",
                "POGGERS": "897724",
                "monkaS": "897726",
                "COPIUM": "897727",
                "AYAYA": "897731",
                "Pepega": "897734",
                "monkaW": "897736",
                "WeirdChamp": "897738",
                "PepeHands": "897739",
                "5Head": "897740",
                "widepeepoHappy": "897741",
                "monkaHmm": "897742",
                "HeyGuys": "30259",
                "PowerUpL": "425688",
                "PowerUpR": "425671",
                "GlitchCat": "304486301",
                "TwitchUnity": "196892",
                "PopCorn": "724216",
                "StinkyGlitch": "304486324",
                "TheIlluminati": "145315",
                "TwitchVotes": "479745",
                "RuleFive": "107030",
                "YEA": "479743",
                "NAY": "479744",
            }
            for name, id in emotes.items():
                url = f"https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/1.0"
                self.emote_urls[name] = url
        except Exception as e:
            print(f"[DEBUG] Error setting up global emotes: {e}")

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
                if not path.exists():
                    print(f"[DEBUG] Downloading emote: {name}")
                    response = requests.get(self.emote_urls[name])
                    if response.status_code == 200:
                        path.write_bytes(response.content)
                        print(f"[DEBUG] Saved emote to {path}")
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(path))
                self.pixbufs[name] = pixbuf
                return pixbuf
            except Exception as e:
                print(f"[DEBUG] Error loading emote {name}: {e}")
                
        return None