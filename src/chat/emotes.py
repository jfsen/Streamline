"""BTTV and 7TV emote provider — fetches global and channel emotes."""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from gi.repository import GLib

logger = logging.getLogger("Emotes")

# ── API endpoints ───────────────────────────────────────────
_BTTV_GLOBAL = "https://api.betterttv.net/3/cached/emotes/global"
_BTTV_CHANNEL = "https://api.betterttv.net/3/cached/users/twitch/{user_id}"
_SEVENTV_GLOBAL = "https://7tv.io/v3/emote-sets/global"
_SEVENTV_CHANNEL = "https://7tv.io/v3/users/twitch/{user_id}"

# ── Cache ───────────────────────────────────────────────────
_CACHE_DIR = Path(GLib.get_user_cache_dir()) / "Streamline" / "emotes"
_CACHE_TTL = 3600  # 1 hour


def _cache_path(source, identifier):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{source}-{identifier}.json"


def _load_cache(source, identifier):
    path = _cache_path(source, identifier)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        age = datetime.now(timezone.utc) - datetime.fromisoformat(data["ts"])
        if age.total_seconds() > _CACHE_TTL:
            logger.debug("Cache expired: %s/%s", source, identifier)
            return None
        logger.debug(
            "Cache hit: %s/%s (%s emotes)", source, identifier, len(data["emotes"])
        )
        return data["emotes"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _save_cache(source, identifier, emotes):
    try:
        with open(_cache_path(source, identifier), "w") as f:
            json.dump(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "emotes": emotes,
                },
                f,
            )
    except OSError:
        pass


# ── Fetching ────────────────────────────────────────────────


def _fetch_json(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.debug("Failed to fetch %s: %s", url, e)
        return None


def _fetch_bttv_global():
    cached = _load_cache("bttv", "global")
    if cached is not None:
        return cached
    logger.debug("Cache miss: bttv/global, fetching")
    data = _fetch_json(_BTTV_GLOBAL)
    if not data:
        _save_cache("bttv", "global", {})
        return {}
    emotes = {
        e["code"]: f"https://cdn.betterttv.net/emote/{e['id']}/1x"
        for e in data
        if e.get("code")
    }
    _save_cache("bttv", "global", emotes)
    return emotes


def _fetch_bttv_channel(user_id):
    cached = _load_cache("bttv", user_id)
    if cached is not None:
        return cached
    logger.debug("Cache miss: bttv/%s, fetching", user_id)
    data = _fetch_json(_BTTV_CHANNEL.format(user_id=user_id))
    if not data:
        _save_cache("bttv", user_id, {})
        return {}
    shared = data.get("sharedEmotes", [])
    channel = data.get("channelEmotes", [])
    emotes = {}
    for e in shared + channel:
        code = e.get("code")
        if code:
            emotes[code] = f"https://cdn.betterttv.net/emote/{e['id']}/1x"
    _save_cache("bttv", user_id, emotes)
    return emotes


def _fetch_7tv_global():
    cached = _load_cache("7tv", "global")
    if cached is not None:
        return cached
    logger.debug("Cache miss: 7tv/global, fetching")
    data = _fetch_json(_SEVENTV_GLOBAL)
    if not data:
        _save_cache("7tv", "global", {})
        return {}
    emotes = {}
    for e in data.get("emotes", []):
        name = e.get("name")
        emote_id = e.get("id")
        if name and emote_id:
            host = e.get("host", {})
            files = host.get("files", [])
            url = files[0]["name"] if files else f"{emote_id}/1x.webp"
            emotes[name] = f"https:{host.get('url', '//cdn.7tv.app/emote')}/{url}"
    _save_cache("7tv", "global", emotes)
    return emotes


def _fetch_7tv_channel(user_id):
    cached = _load_cache("7tv", user_id)
    if cached is not None:
        return cached
    logger.debug("Cache miss: 7tv/%s, fetching", user_id)
    data = _fetch_json(_SEVENTV_CHANNEL.format(user_id=user_id))
    if not data:
        _save_cache("7tv", user_id, {})
        return {}
    emotes = {}
    for es in data.get("emote_set", {}).get("emotes", []):
        name = es.get("name")
        emote_id = es.get("id")
        if name and emote_id:
            host = es.get("data", {}).get("host", {})
            files = host.get("files", [])
            url = files[0]["name"] if files else f"{emote_id}/1x.webp"
            emotes[name] = f"https:{host.get('url', '//cdn.7tv.app/emote')}/{url}"
    _save_cache("7tv", user_id, emotes)
    return emotes


# ── Public API ──────────────────────────────────────────────


class ThirdPartyEmotes:
    """Fetches and caches BTTV/7TV emotes for a channel."""

    def __init__(self, user_id):
        self._user_id = user_id
        self._emotes = {}  # name → url
        self._trie = {}
        self._lock = threading.Lock()

    def load(self):
        """Fetch all emote sets in parallel, building the trie incrementally."""
        fetchers = [_fetch_bttv_global, _fetch_7tv_global]
        if self._user_id:
            fetchers.append(lambda: _fetch_bttv_channel(self._user_id))
            fetchers.append(lambda: _fetch_7tv_channel(self._user_id))

        threads = []
        for fetch in fetchers:
            t = threading.Thread(
                target=self._fetch_and_merge, args=(fetch,), daemon=True
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        logger.debug(
            "Loaded %s third-party emotes for %s",
            len(self._emotes),
            self._user_id or "global-only",
        )

    def _fetch_and_merge(self, fetch_fn):
        """Fetch one emote set and merge it into the global dict."""
        result = fetch_fn()
        with self._lock:
            self._emotes.update(result)
            self._build_trie()

    def _build_trie(self):
        """Build a trie from emote names for fast text scanning."""
        self._trie = {}
        for name, url in self._emotes.items():
            node = self._trie
            for ch in name:
                node = node.setdefault(ch, {})
            node[""] = url  # sentinel for end-of-word

    def find_emotes(self, text):
        """Scan text for known emote names using a trie, returning position data."""
        if not self._trie:
            return []
        result = []
        i = 0
        n = len(text)
        while i < n:
            node = self._trie
            best_url = None
            best_end = i
            j = i
            while j < n and text[j] in node:
                node = node[text[j]]
                j += 1
                if "" in node:
                    best_url = node[""]
                    best_end = j
            if best_url:
                before = i == 0 or text[i - 1].isspace()
                after = best_end == n or text[best_end].isspace()
                if before and after:
                    result.append(
                        {
                            "url": best_url,
                            "positions": [(i, best_end - 1)],
                        }
                    )
                    i = best_end
                    continue
            i += 1
        return result
