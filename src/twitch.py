import gettext
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import time

import requests
from gi.repository import GLib

_ = gettext.gettext

logger = logging.getLogger("Twitch")


class TwitchAPI:
    # Single cache directory, computed once per process
    _CACHE_DIR = Path(GLib.get_user_cache_dir()) / "Streamline"

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expires_at = None
        logger.debug("Initializing API (client_id: %s...)", client_id[:5])

        # Lazy-loaded caches — nothing read from disk yet
        self._user_cache = {}
        self._user_cache_loaded = False
        self._token_loaded = False

    def _get_access_token(self):
        url = "https://id.twitch.tv/oauth2/token"
        logger.debug("Requesting access token from %s", url)
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        try:
            response = requests.post(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            self.access_token = data["access_token"]
            # Use the expiration from the API (typically ~60 days)
            expires_in = data.get("expires_in", 3600)
            self.token_expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=expires_in
            )
            self._save_token_cache()
            logger.debug("Access token obtained successfully")
            return self.access_token
        except requests.exceptions.RequestException as e:
            logger.debug("Failed to get access token: %s", str(e))
            raise

    def _ensure_access_token(self):
        """Ensure we have a valid access token before making API calls."""
        self._load_token_cache()
        if (
            self.access_token is None
            or self.token_expires_at is None
            or datetime.now(timezone.utc) >= self.token_expires_at
        ):
            self._get_access_token()

    @property
    def user_cache(self):
        """Lazy-loaded combined cache of user IDs and names."""
        if not self._user_cache_loaded:
            self._user_cache = self._load_user_cache()
            self._user_cache_loaded = True
        return self._user_cache

    def _ensure_cache_dir(self):
        """Create the cache directory once (only when actually writing)."""
        self._CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _get_token_cache_path(self):
        """Get path to token cache file."""
        return self._CACHE_DIR / "token.json"

    def _load_token_cache(self):
        """Load access token from cache if available and not expired."""
        if self._token_loaded:
            return
        self._token_loaded = True
        try:
            with open(self._get_token_cache_path()) as f:
                cache_data = json.load(f)
                expires_at = datetime.fromisoformat(cache_data["expires_at"])
                if datetime.now(timezone.utc) < expires_at:
                    self.access_token = cache_data["access_token"]
                    self.token_expires_at = expires_at
                    logger.debug("Loaded valid token from cache")
        except (json.JSONDecodeError, KeyError, OSError, FileNotFoundError):
            pass

    def _save_token_cache(self):
        """Save access token to cache with expiration."""
        try:
            self._ensure_cache_dir()
            cache_data = {
                "access_token": self.access_token,
                "expires_at": self.token_expires_at.isoformat(),
            }
            with open(self._get_token_cache_path(), "w") as f:
                json.dump(cache_data, f, indent=4)
        except OSError:
            logger.debug("Failed to save token cache")

    def _get_user_cache_path(self):
        """Get path to user cache file."""
        return self._CACHE_DIR / "users.json"

    def _load_user_cache(self):
        """Load user data from cache file, migrating old format if needed."""
        try:
            with open(self._get_user_cache_path()) as f:
                data = json.load(f)
            # Migrate from old {"ids": {...}, "names": {...}} format
            if "ids" in data and "names" in data:
                logger.debug("Migrating old user cache format")
                migrated = {}
                for login in data["ids"]:
                    migrated[login] = {
                        "id": data["ids"][login],
                        "name": data["names"].get(login, login),
                    }
                return migrated
            return data
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            return {}

    def _save_user_cache(self):
        """Save user data to cache file."""
        try:
            self._ensure_cache_dir()
            with open(self._get_user_cache_path(), "w") as f:
                json.dump(self.user_cache, f, indent=4)
        except OSError:
            logger.debug("Failed to save user cache")

    def _get_streams_cache_path(self):
        """Get path to streams cache file."""
        return self._CACHE_DIR / "streams.json"

    def _load_streams_cache(self):
        """Load streams data from cache if available and not expired."""
        try:
            with open(self._get_streams_cache_path()) as f:
                cache_data = json.load(f)

            # Check if cache is expired (1 minute)
            cache_time = datetime.fromisoformat(cache_data["timestamp"])
            now = datetime.now(timezone.utc)
            seconds_until_refresh = 60 - (now - cache_time).total_seconds()  # TIMER

            if seconds_until_refresh > 0:
                return cache_data["data"], int(seconds_until_refresh)

            return None, 0
        except (json.JSONDecodeError, KeyError, OSError, FileNotFoundError):
            return None, 0

    def _save_streams_cache(self, data):
        """Save streams data to cache with timestamp."""
        try:
            self._ensure_cache_dir()
            cache_data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": data,
            }
            with open(self._get_streams_cache_path(), "w") as f:
                json.dump(cache_data, f, indent=4)
        except OSError:
            logger.debug("Failed to save streams cache")

    def _invalidate_streams_cache(self):
        """Delete streams cache so the next refresh is not blocked by cooldown."""
        try:
            self._get_streams_cache_path().unlink(missing_ok=True)
        except OSError:
            pass

    def update_streams_cache(self, username, add=True):
        """Update streams cache without modifying timestamp."""
        action = "adding" if add else "removing"
        logger.debug("Updating streams cache: %s %s", action, username)
        try:
            with open(self._get_streams_cache_path()) as f:
                cache_data = json.load(f)

            if "data" in cache_data:
                offline = cache_data["data"].get("offline", [])
                online = cache_data["data"].get("online", {})
                if add:
                    if username not in offline and username not in online:
                        offline.append(username)
                        offline.sort(key=str.lower)
                else:
                    cache_data["data"]["offline"] = [
                        s for s in offline if s != username
                    ]
                    if username in online:
                        del online[username]

                with open(self._get_streams_cache_path(), "w") as f:
                    json.dump(cache_data, f, indent=4)

        except (OSError, json.JSONDecodeError):
            pass  # Silently fail if cache update fails

    def get_streams(self, usernames):
        """Get stream information for multiple users."""
        # Try to load from cache first
        cached_data, seconds_until_refresh = self._load_streams_cache()
        if cached_data is not None:
            logger.debug(
                "Using cached stream data (refresh in %ss)", seconds_until_refresh
            )
            return (
                list(cached_data["online"].keys()),
                cached_data["offline"],
                cached_data["online"],
            )

        # Only get access token if we need to make API calls
        self._ensure_access_token()

        start_time = time()
        logger.debug("Fetching streams for %s users", len(usernames))

        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

        online_streamers = []
        offline_streamers = []
        streamer_info = {}

        for i in range(0, len(usernames), 100):
            batch = usernames[i : i + 100]
            logger.debug("Processing batch %s (%s users)", i // 100 + 1, len(batch))
            user_logins = "&user_login=".join(batch)
            url = f"https://api.twitch.tv/helix/streams?user_login={user_logins}"

            try:
                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                data = response.json()

                for stream in data.get("data", []):
                    user_login = stream["user_login"]
                    online_streamers.append(user_login)
                    # Cache the user ID and display name
                    self.user_cache[user_login] = {
                        "id": stream["user_id"],
                        "name": stream["user_name"],
                    }
                    streamer_info[user_login] = {
                        "game": stream["game_name"],
                        "title": stream["title"],
                        "viewers": stream["viewer_count"],
                        "started_at": stream["started_at"],
                    }
                    logger.debug(
                        "Live: %s playing %s (%s viewers)",
                        stream["user_name"],
                        stream["game_name"],
                        stream["viewer_count"],
                    )

                offline_streamers_batch = [
                    s for s in batch if s not in online_streamers
                ]
                offline_streamers.extend(offline_streamers_batch)
                if offline_streamers_batch:
                    logger.debug("Offline: %s", ", ".join(offline_streamers_batch))

                # Save both caches after updating
                self._save_user_cache()

            except requests.exceptions.RequestException as e:
                logger.debug("API request failed: %s", str(e))
                raise

        elapsed = time() - start_time
        logger.debug(
            "Completed in %.2fs - %s online, %s offline",
            elapsed,
            len(online_streamers),
            len(offline_streamers),
        )

        # Save to cache
        cache_data = {
            "online": streamer_info,
            "offline": offline_streamers,
        }
        self._save_streams_cache(cache_data)

        return online_streamers, offline_streamers, streamer_info

    def get_user_vods(self, username, limit=20):
        """Get recent VODs for a user."""
        start_time = time()
        logger.debug("Fetching VODs for %s", username)

        # Only get access token if we need to make API calls
        self._ensure_access_token()

        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }

        # Try to get user ID from cache first
        user_id = self.user_cache.get(username, {}).get("id")
        if user_id:
            logger.debug("Using cached user ID for %s: %s", username, user_id)

        if not user_id:
            # If not in cache, fetch it from API
            user_url = f"https://api.twitch.tv/helix/users?login={username}"
            try:
                response = requests.get(user_url, headers=headers, timeout=10)
                response.raise_for_status()
                user_data = response.json()["data"]
                if not user_data:
                    return []

                user_id = user_data[0]["id"]
                self.user_cache[username] = {
                    "id": user_id,
                    "name": user_data[0].get("display_name", username),
                }
                self._save_user_cache()

            except requests.exceptions.RequestException as e:
                logger.debug("Failed to fetch user ID: %s", str(e))
                raise

        # Now get VODs
        vods_url = f"https://api.twitch.tv/helix/videos?user_id={user_id}&first={limit}&type=archive"
        try:
            response = requests.get(vods_url, headers=headers, timeout=15)
            response.raise_for_status()
            vods = response.json()["data"]

            formatted_vods = []
            for vod in vods:
                formatted_vods.append(
                    {
                        "id": vod["id"],
                        "title": vod["title"],
                        "url": vod["url"],
                        "duration": vod["duration"],
                        "created_at": vod["created_at"],
                        "view_count": vod["view_count"],
                    }
                )

            logger.debug(
                "Found %s VODs for %s in %.2fs",
                len(formatted_vods),
                username,
                time() - start_time,
            )
            return formatted_vods

        except requests.exceptions.RequestException as e:
            logger.debug("Failed to fetch VODs: %s", str(e))
            raise

    def _format_date(self, date_str):
        """Format ISO date string to a human-friendly relative time."""
        date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - date

        if delta.total_seconds() < 0:
            return date.strftime("%b %d")

        minutes = int(delta.total_seconds() // 60)
        if minutes < 1:
            return _("Just now")
        if minutes < 60:
            return _("{}m ago").format(minutes)

        hours = minutes // 60
        if hours < 24:
            return _("{}h ago").format(hours)

        days = hours // 24
        if days < 7:
            return _("{}d ago").format(days)
        if days < 30:
            weeks = days // 7
            return _("{}w ago").format(weeks)

        return date.strftime("%b %d")

    def _format_duration(self, duration_str):
        """Convert Twitch duration string (e.g. '1h23m45s') to '1h 23m'."""
        h = re.search(r"(\d+)h", duration_str)
        m = re.search(r"(\d+)m", duration_str)
        parts = []
        if h:
            parts.append(_("{}h").format(h.group(1)))
        if m:
            parts.append(_("{}m").format(m.group(1)))
        if not parts:
            s = re.search(r"(\d+)s", duration_str)
            if s:
                parts.append(_("{}s").format(s.group(1)))
        return " ".join(parts) if parts else duration_str

    def _calculate_uptime(self, start_time):
        """Calculate stream uptime."""
        start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        uptime = now - start_time
        hours, remainder = divmod(uptime.total_seconds(), 3600)
        minutes, _secs = divmod(remainder, 60)
        return _("{}h {}m").format(int(hours), int(minutes))
