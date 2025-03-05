import requests
import json
from datetime import datetime, timezone, timedelta
from time import time
from pathlib import Path

class TwitchAPI:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self.token_expires_at = None
        print(f"[Twitch] Initializing API (client_id: {client_id[:5]}...)")
        self.user_cache = self._load_user_cache()  # Combined cache
        self._load_token_cache()

    def _get_access_token(self):
        url = 'https://id.twitch.tv/oauth2/token'
        print(f"[Twitch] Requesting access token from {url}")
        params = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'client_credentials'
        }
        try:
            response = requests.post(url, params=params)
            response.raise_for_status()
            data = response.json()
            self.access_token = data['access_token']
            # Set expiration time (token is valid for 4 hours, we'll set it to 3.5 hours to be safe)
            self.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=3.5)
            self._save_token_cache()
            print("[Twitch] Access token obtained successfully")
            return self.access_token
        except requests.exceptions.RequestException as e:
            print(f"[Twitch] Failed to get access token: {str(e)}")
            raise

    def _ensure_access_token(self):
        """Ensure we have a valid access token before making API calls."""
        if (self.access_token is None or 
            self.token_expires_at is None or 
            datetime.now(timezone.utc) >= self.token_expires_at):
            self._get_access_token()

    def _get_token_cache_path(self):
        """Get path to token cache file."""
        cache_dir = Path.home() / ".cache" / "Streamline"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "token.json"

    def _load_token_cache(self):
        """Load access token from cache if available and not expired."""
        try:
            with open(self._get_token_cache_path()) as f:
                cache_data = json.load(f)
                expires_at = datetime.fromisoformat(cache_data['expires_at'])
                if datetime.now(timezone.utc) < expires_at:
                    self.access_token = cache_data['access_token']
                    self.token_expires_at = expires_at
                    print("[Twitch] Loaded valid token from cache")
        except (json.JSONDecodeError, KeyError, OSError, FileNotFoundError):
            pass

    def _save_token_cache(self):
        """Save access token to cache with expiration."""
        try:
            cache_data = {
                'access_token': self.access_token,
                'expires_at': self.token_expires_at.isoformat()
            }
            with open(self._get_token_cache_path(), 'w') as f:
                json.dump(cache_data, f, indent=4)
        except OSError:
            print("[Twitch] Failed to save token cache")

    def _get_user_cache_path(self):
        """Get path to user cache file."""
        cache_dir = Path.home() / ".cache" / "Streamline"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "users.json"

    def _load_user_cache(self):
        """Load user data from cache file."""
        try:
            with open(self._get_user_cache_path()) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            return {'ids': {}, 'names': {}}  # Combined structure

    def _save_user_cache(self):
        """Save user data to cache file."""
        try:
            with open(self._get_user_cache_path(), 'w') as f:
                json.dump(self.user_cache, f, indent=4)
        except OSError:
            print("[Twitch] Failed to save user cache")

    def _get_streams_cache_path(self):
        """Get path to streams cache file."""
        cache_dir = Path.home() / ".cache" / "Streamline"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "streams.json"

    def _load_streams_cache(self):
        """Load streams data from cache if available and not expired."""
        try:
            with open(self._get_streams_cache_path()) as f:
                cache_data = json.load(f)
            
            # Check if cache is expired (3 minutes)
            cache_time = datetime.fromisoformat(cache_data['timestamp'])
            now = datetime.now(timezone.utc)
            seconds_until_refresh = 180 - (now - cache_time).total_seconds() #TIMER
            
            if seconds_until_refresh > 0:
                return cache_data['data'], int(seconds_until_refresh)
                
            return None, 0
        except (json.JSONDecodeError, KeyError, OSError, FileNotFoundError):
            return None, 0

    def _save_streams_cache(self, data):
        """Save streams data to cache with timestamp."""
        try:
            cache_data = {
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'data': data
            }
            with open(self._get_streams_cache_path(), 'w') as f:
                json.dump(cache_data, f, indent=4)
        except OSError:
            print("[Twitch] Failed to save streams cache")

    def update_streams_cache(self, username, add=True):
        """Update streams cache without modifying timestamp."""
        try:
            with open(self._get_streams_cache_path()) as f:
                cache_data = json.load(f)
                
            if 'data' in cache_data:
                if add:
                    if username not in cache_data['data']['offline']:
                        cache_data['data']['offline'].append(username)
                        cache_data['data']['offline'].sort(key=str.lower)
                else:
                    cache_data['data']['online'] = [s for s in cache_data['data']['online'] if s != username]
                    cache_data['data']['offline'] = [s for s in cache_data['data']['offline'] if s != username]
                    if username in cache_data['data']['info']:
                        del cache_data['data']['info'][username]
                
                with open(self._get_streams_cache_path(), 'w') as f:
                    json.dump(cache_data, f, indent=4)
                    
        except (OSError, json.JSONDecodeError):
            pass  # Silently fail if cache update fails

    def get_streams(self, usernames):
        """Get stream information for multiple users."""
        # Try to load from cache first
        cached_data, seconds_until_refresh = self._load_streams_cache()
        if cached_data is not None:
            print(f"[Twitch] Using cached stream data (refresh in {seconds_until_refresh}s)")
            return cached_data['online'], cached_data['offline'], cached_data['info']

        # Only get access token if we need to make API calls
        self._ensure_access_token()
        
        start_time = time()
        print(f"[Twitch] Fetching streams for {len(usernames)} users")
        
        headers = {
            'Client-ID': self.client_id,
            'Authorization': f'Bearer {self.access_token}'
        }
        
        online_streamers = []
        offline_streamers = []
        streamer_info = {}

        for i in range(0, len(usernames), 100):
            batch = usernames[i:i+100]
            print(f"[Twitch] Processing batch {i//100 + 1} ({len(batch)} users)")
            user_logins = '&user_login='.join(batch)
            url = f'https://api.twitch.tv/helix/streams?user_login={user_logins}'
            
            try:
                response = requests.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                
                for stream in data.get('data', []):
                    user_login = stream['user_login']
                    online_streamers.append(user_login)
                    # Cache the user ID and display name
                    self.user_cache['ids'][user_login] = stream['user_id']
                    self.user_cache['names'][user_login] = stream['user_name']
                    streamer_info[user_login] = {
                        "game": stream['game_name'],
                        "title": stream['title'],
                        "viewers": stream['viewer_count'],
                        "uptime": self._calculate_uptime(stream['started_at'])
                    }
                    print(f"[Twitch] Live: {stream['user_name']} playing {stream['game_name']} ({stream['viewer_count']} viewers)")

                offline_streamers_batch = [s for s in batch if s not in online_streamers]
                offline_streamers.extend(offline_streamers_batch)
                if offline_streamers_batch:
                    print(f"[Twitch] Offline: {', '.join(offline_streamers_batch)}")

                # Save both caches after updating
                self._save_user_cache()

            except requests.exceptions.RequestException as e:
                print(f"[Twitch] API request failed: {str(e)}")
                raise

        elapsed = time() - start_time
        print(f"[Twitch] Completed in {elapsed:.2f}s - {len(online_streamers)} online, {len(offline_streamers)} offline")
        
        # Save to cache
        cache_data = {
            'online': online_streamers,
            'offline': offline_streamers,
            'info': streamer_info
        }
        self._save_streams_cache(cache_data)
        
        return online_streamers, offline_streamers, streamer_info

    def get_user_vods(self, username, limit=20):
        """Get recent VODs for a user."""
        print(f"[Twitch] Fetching VODs for {username}")
        
        # Only get access token if we need to make API calls
        self._ensure_access_token()
        
        headers = {
            'Client-ID': self.client_id,
            'Authorization': f'Bearer {self.access_token}'
        }
        
        # Try to get user ID from cache first
        user_id = self.user_cache['ids'].get(username)
        
        if not user_id:
            # If not in cache, fetch it from API
            user_url = f'https://api.twitch.tv/helix/users?login={username}'
            try:
                response = requests.get(user_url, headers=headers)
                response.raise_for_status()
                user_data = response.json()['data']
                if not user_data:
                    return []
                    
                user_id = user_data[0]['id']
                # Cache the user ID and save to file
                self.user_cache['ids'][username] = user_id
                self._save_user_cache()
                
            except requests.exceptions.RequestException as e:
                print(f"[Twitch] Failed to fetch user ID: {str(e)}")
                raise

        # Now get VODs
        vods_url = f'https://api.twitch.tv/helix/videos?user_id={user_id}&first={limit}&type=archive'
        try:
            response = requests.get(vods_url, headers=headers)
            response.raise_for_status()
            vods = response.json()['data']
            
            formatted_vods = []
            for vod in vods:
                formatted_vods.append({
                    'id': vod['id'],
                    'title': vod['title'],
                    'url': vod['url'],
                    'duration': vod['duration'],
                    'created_at': self._format_date(vod['created_at']),
                    'view_count': vod['view_count']
                })
            
            print(f"[Twitch] Found {len(formatted_vods)} VODs for {username}")
            return formatted_vods
            
        except requests.exceptions.RequestException as e:
            print(f"[Twitch] Failed to fetch VODs: {str(e)}")
            raise

    def _format_date(self, date_str):
        """Format date string to readable format."""
        date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return date.strftime('%Y-%m-%d %H:%M')

    def _calculate_uptime(self, start_time):
        """Calculate stream uptime."""
        start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        uptime = now - start_time
        hours, remainder = divmod(uptime.total_seconds(), 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{int(hours)}h {int(minutes)}m"