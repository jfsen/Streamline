# third_party_emotes.py
#
# Copyright 2025 jfsen
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""BTTV and 7TV emote provider — fetches global and channel emotes."""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from gi.repository import GLib

from .config import (
    _BTTV_CHANNEL,
    _BTTV_GLOBAL,
    _FFZ_CHANNEL,
    _FFZ_GLOBAL,
    _SEVENTV_CHANNEL,
    _SEVENTV_GLOBAL,
    EMOTE_CACHE_TTL,
)

logger = logging.getLogger(__name__)

# Global emote endpoints should never 404 — a 404 here means the
# third-party service itself is down or the API changed.
_GLOBAL_URLS = {_BTTV_GLOBAL, _SEVENTV_GLOBAL, _FFZ_GLOBAL}

# ── Cache ───────────────────────────────────────────────────
_CACHE_DIR = Path(GLib.get_user_cache_dir()) / "Streamline" / "emotes"


def _cache_path(source, identifier, prefer_static=False):
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "-static" if prefer_static else ""
    return _CACHE_DIR / f"{source}-{identifier}{suffix}.json"


def _load_cache(source, identifier, prefer_static=False):
    path = _cache_path(source, identifier, prefer_static)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        age = datetime.now(timezone.utc) - datetime.fromisoformat(data["ts"])
        scope = "global" if identifier == "global" else "channel"
        if age.total_seconds() > EMOTE_CACHE_TTL[source][scope]:
            logger.debug("Cache expired: %s/%s", source, identifier)
            return None
        emotes = data["emotes"]
        # Invalidate old-format caches (plain string URLs instead of {url, source})
        if emotes and isinstance(next(iter(emotes.values())), str):
            logger.debug("Invalidating old-format cache: %s/%s", source, identifier)
            return None
        logger.debug("Cache hit: %s/%s (%s emotes)", source, identifier, len(emotes))
        return emotes
    except (json.JSONDecodeError, KeyError) as e:
        logger.debug("Corrupt emote cache %s/%s: %s", source, identifier, e)
        return None
    except OSError as e:
        logger.debug("Failed to read emote cache %s/%s: %s", source, identifier, e)
        return None


def _save_cache(source, identifier, emotes, prefer_static=False):
    try:
        with open(_cache_path(source, identifier, prefer_static), "w") as f:
            json.dump(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "emotes": emotes,
                },
                f,
                indent=2,
            )
    except OSError:
        logger.debug("Failed to save emote cache %s/%s", source, identifier)
        pass


# ── Fetching ────────────────────────────────────────────────


def _fetch_json(url):
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            if url in _GLOBAL_URLS:
                # Global endpoint 404 — the service itself may be down.
                logger.warning("Global emote endpoint returned 404: %s", url)
            else:
                # Channel-specific 404 — streamer simply doesn't use this service.
                logger.debug("Streamer does not use this emote service: %s", url)
        else:
            logger.warning("HTTP error fetching %s: %s", url, e)
        return None
    except requests.RequestException as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return None
        return None


def _bttv_url(emote_id, prefer_static):
    """Build a BTTV emote URL. Append .png to force static when requested."""
    url = f"https://cdn.betterttv.net/emote/{emote_id}/1x"
    if prefer_static:
        url += ".png"
    return url


def _fetch_bttv_global(prefer_static=False):
    cached = _load_cache("bttv", "global", prefer_static)
    if cached is not None:
        return cached
    logger.debug("Cache miss: bttv/global, fetching")
    data = _fetch_json(_BTTV_GLOBAL)
    if data is None:
        return {}  # API error — don't overwrite any existing cache
    emotes = {
        e["code"]: {
            "url": _bttv_url(e["id"], prefer_static),
            "source": "BTTV",
        }
        for e in data
        if e.get("code")
    }
    _save_cache("bttv", "global", emotes, prefer_static)
    return emotes


def _fetch_bttv_channel(user_id, prefer_static=False):
    cached = _load_cache("bttv", user_id, prefer_static)
    if cached is not None:
        return cached
    logger.debug("Cache miss: bttv/%s, fetching", user_id)
    data = _fetch_json(_BTTV_CHANNEL.format(user_id=user_id))
    if data is None:
        return {}  # API error — don't overwrite any existing cache
    shared = data.get("sharedEmotes", [])
    channel = data.get("channelEmotes", [])
    emotes = {}
    for e in shared + channel:
        code = e.get("code")
        if code:
            emotes[code] = {
                "url": _bttv_url(e["id"], prefer_static),
                "source": "BTTV",
            }
    _save_cache("bttv", user_id, emotes, prefer_static)
    return emotes


def _pick_7tv_file(files, prefer_static):
    """Pick the best file from 7TV's files array.

    When prefer_static is True, prefer ``static_name`` when available,
    then fall back to files with ``frame_count == 0``.
    Otherwise use the first file (typically the highest quality
    animated version).
    """
    if not files:
        return None
    if prefer_static:
        for f in files:
            if f.get("static_name"):
                return f["static_name"]
        for f in files:
            if not f.get("frame_count"):
                return f["name"]
    return files[0]["name"]


def _fetch_7tv_global(prefer_static=False):
    cached = _load_cache("7tv", "global", prefer_static)
    if cached is not None:
        return cached
    logger.debug("Cache miss: 7tv/global, fetching")
    data = _fetch_json(_SEVENTV_GLOBAL)
    if data is None:
        return {}  # API error — don't overwrite any existing cache
    emotes = {}
    for e in data.get("emotes", []):
        name = e.get("name")
        emote_id = e.get("id")
        if name and emote_id:
            host = e.get("data", {}).get("host", {})
            files = host.get("files", [])
            url = _pick_7tv_file(files, prefer_static) or f"{emote_id}/1x.webp"
            emotes[name] = {
                "url": f"https:{host.get('url', '//cdn.7tv.app/emote')}/{url}",
                "source": "7TV",
            }
    _save_cache("7tv", "global", emotes, prefer_static)
    return emotes


def _fetch_7tv_channel(user_id, prefer_static=False):
    cached = _load_cache("7tv", user_id, prefer_static)
    if cached is not None:
        return cached
    logger.debug("Cache miss: 7tv/%s, fetching", user_id)
    data = _fetch_json(_SEVENTV_CHANNEL.format(user_id=user_id))
    if data is None:
        return {}  # API error — don't overwrite any existing cache
    emotes = {}
    for es in data.get("emote_set", {}).get("emotes", []):
        name = es.get("name")
        emote_id = es.get("id")
        if name and emote_id:
            host = es.get("data", {}).get("host", {})
            files = host.get("files", [])
            url = _pick_7tv_file(files, prefer_static) or f"{emote_id}/1x.webp"
            emotes[name] = {
                "url": f"https:{host.get('url', '//cdn.7tv.app/emote')}/{url}",
                "source": "7TV",
            }
    _save_cache("7tv", user_id, emotes, prefer_static)
    return emotes


def _fetch_ffz_global(prefer_static=False):
    """Fetch FFZ global emotes."""
    cached = _load_cache("ffz", "global", prefer_static)
    if cached is not None:
        return cached
    logger.debug("Cache miss: ffz/global, fetching")
    data = _fetch_json(_FFZ_GLOBAL)
    if data is None:
        return {}  # API error — don't overwrite any existing cache
    emotes = {}
    for set_id in data.get("default_sets", []):
        for e in data.get("sets", {}).get(str(set_id), {}).get("emoticons", []):
            name = e.get("name")
            eid = e.get("id")
            if name and eid and not e.get("hidden") and not e.get("modifier"):
                emotes[name] = {
                    "url": f"https://cdn.frankerfacez.com/emoticon/{eid}/1",
                    "source": "FFZ",
                }
    _save_cache("ffz", "global", emotes, prefer_static)
    return emotes


def _fetch_ffz_channel(user_id, prefer_static=False):
    """Fetch FFZ channel emotes."""
    cached = _load_cache("ffz", user_id, prefer_static)
    if cached is not None:
        return cached
    logger.debug("Cache miss: ffz/%s, fetching", user_id)
    data = _fetch_json(_FFZ_CHANNEL.format(user_id=user_id))
    if data is None:
        return {}  # API error — don't overwrite any existing cache
    emotes = {}
    room = data.get("room", {})
    room_set = room.get("set")
    if room_set:
        for e in data.get("sets", {}).get(str(room_set), {}).get("emoticons", []):
            name = e.get("name")
            eid = e.get("id")
            if name and eid and not e.get("hidden") and not e.get("modifier"):
                emotes[name] = {
                    "url": f"https://cdn.frankerfacez.com/emoticon/{eid}/1",
                    "source": "FFZ",
                }
    _save_cache("ffz", user_id, emotes, prefer_static)
    return emotes


# ── Public API ──────────────────────────────────────────────


class ThirdPartyEmotes:
    """Fetches and caches BTTV/7TV/FFZ emotes for a channel."""

    def __init__(self, user_id, prefer_static=False):
        self._user_id = user_id
        self._prefer_static = prefer_static
        self._emotes = {}  # name → url
        self._trie = {}
        self._lock = threading.Lock()

    def load(self):
        """Fetch all emote sets in parallel, building the trie incrementally.

        Order matters for priority: later updates overwrite earlier ones.
        Priority (lowest→highest): FFZ < 7TV < BTTV, channel > global.
        """
        ps = self._prefer_static
        fetchers = [
            lambda: _fetch_ffz_global(ps),
            lambda: _fetch_7tv_global(ps),
            lambda: _fetch_bttv_global(ps),
        ]
        if self._user_id:
            fetchers.append(lambda: _fetch_ffz_channel(self._user_id, ps))
            fetchers.append(lambda: _fetch_7tv_channel(self._user_id, ps))
            fetchers.append(lambda: _fetch_bttv_channel(self._user_id, ps))

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
        new_trie = {}
        for name, data in self._emotes.items():
            node = new_trie
            for ch in name:
                node = node.setdefault(ch, {})
            node[""] = data  # sentinel holds {url, source}
        self._trie = new_trie

    def find_emotes(self, text):
        """Scan text for known emote names using a trie, returning position data."""
        if not self._trie:
            return []
        result = []
        i = 0
        n = len(text)
        while i < n:
            node = self._trie
            best_data = None
            best_end = i
            j = i
            while j < n and text[j] in node:
                node = node[text[j]]
                j += 1
                if "" in node:
                    best_data = node[""]
                    best_end = j
            if best_data:
                before = i == 0 or text[i - 1].isspace()
                after = best_end == n or text[best_end].isspace()
                if before and after:
                    result.append(
                        {
                            "source": best_data["source"],
                            "name": text[i:best_end],
                            "url": best_data["url"],
                            "positions": [(i, best_end - 1)],
                        }
                    )
                    i = best_end
                    continue
            i += 1
        return result
