import requests
from datetime import datetime, timezone
from time import time

class TwitchAPI:
    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        print(f"[Twitch] Initializing API (client_id: {client_id[:5]}...)")
        self.access_token = self._get_access_token()

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
            print("[Twitch] Access token obtained successfully")
            return response.json()['access_token']
        except requests.exceptions.RequestException as e:
            print(f"[Twitch] Failed to get access token: {str(e)}")
            raise

    def get_streams(self, usernames):
        """Get stream information for multiple users."""
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
                    streamer_info[user_login] = {
                        "game": stream['game_name'],
                        "title": stream['title'],
                        "viewers": stream['viewer_count'],
                        "uptime": self._calculate_uptime(stream['started_at'])
                    }
                    print(f"[Twitch] Live: {user_login} playing {stream['game_name']} ({stream['viewer_count']} viewers)")

                offline_streamers_batch = [s for s in batch if s not in online_streamers]
                offline_streamers.extend(offline_streamers_batch)
                if offline_streamers_batch:
                    print(f"[Twitch] Offline: {', '.join(offline_streamers_batch)}")

            except requests.exceptions.RequestException as e:
                print(f"[Twitch] API request failed: {str(e)}")
                raise

        elapsed = time() - start_time
        print(f"[Twitch] Completed in {elapsed:.2f}s - {len(online_streamers)} online, {len(offline_streamers)} offline")
        return online_streamers, offline_streamers, streamer_info

    def _calculate_uptime(self, start_time):
        """Calculate stream uptime."""
        start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        uptime = now - start_time
        hours, remainder = divmod(uptime.total_seconds(), 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{int(hours)}h {int(minutes)}m"