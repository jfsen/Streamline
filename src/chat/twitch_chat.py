# twitch_chat.py
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

"""Twitch IRC chat client (read-only, anonymous)."""

import enum
import gettext
import logging
import random
import re
import socket
import threading
import time

from gi.repository import GLib

from .config import (
    FALLBACK_USER_COLOR,
    IRC_HOST,
    IRC_PORT,
    PING_CHECK_INTERVAL,
    PING_INTERVAL,
    PING_TIMEOUT,
    RECONNECT_BASE_DELAY,
    RECONNECT_JITTER,
    RECONNECT_MAX_ATTEMPTS,
    RECONNECT_MAX_DELAY,
    TWITCH_EMOTE_CDN,
    TWITCH_EMOTE_CDN_STATIC,
)

logger = logging.getLogger("IRCChat")

_ = gettext.gettext


class ConnectionState(enum.Enum):
    """Public connection states emitted via ``on_state_change``."""

    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DISCONNECTED = "disconnected"


# Twitch IRC tags for extracting display name and color
_TAG_RE = re.compile(r"@([^ ]+) ")
_COLOR_RE = re.compile(r"color=#([0-9A-Fa-f]{6})")
_DISPLAY_NAME_RE = re.compile(r"display-name=([^;]+)")
_EMOTES_RE = re.compile(r"emotes=([^;]+)")
_BADGES_RE = re.compile(r"badges=([^;]+)")
_BADGE_INFO_RE = re.compile(r"badge-info=([^;]+)")
_FIRST_MSG_RE = re.compile(r"first-msg=([^;]+)")
# Single-pass tag parser — replaces per-key regex compilation.
_TAGS_PARSE_RE = re.compile(r"([^;=\s]+)=([^;]*)")

# Known badge names — only these are rendered from the IRC badges tag.
# Keys are IRC badge IDs; values are the display name used in tooltips.
# Not included: "bits"
_BADGE_NAMES = {
    "broadcaster": "Broadcaster",
    "moderator": "Moderator",
    "vip": "VIP",
    "subscriber": "Subscriber",
    "founder": "Founder",
    "partner": "Partner",
    "staff": "Staff",
    "admin": "Admin",
    "global_mod": "Global Moderator",
    "no_audio": "No Audio",
    "no_video": "No Video",
    "premium": "Free Subscription Tier",
}

# IRC-thread batching: dispatch parsed messages to the main thread
# in bulk instead of one GLib.idle_add per line.
_IRC_BATCH_SIZE = 20
_IRC_BATCH_MS = 0.1  # 100 ms


class TwitchChat:
    """Connects to Twitch IRC and emits messages via a callback.

    Automatically reconnects on connection loss with exponential
    back-off and jitter.  Gives up after ``RECONNECT_MAX_ATTEMPTS``
    consecutive failures and transitions to ``DISCONNECTED``.
    """

    def __init__(
        self,
        channel,
        on_message,
        prefer_static_emotes=False,
        on_state_change=None,
        on_roomstate=None,
    ):
        self._channel = channel.lstrip("#").lower()
        self._on_message = on_message
        self._prefer_static_emotes = prefer_static_emotes
        self._on_state_change = on_state_change
        self._on_roomstate = on_roomstate
        self._sock = None
        self._socket_lock = threading.Lock()
        self._running = False
        self._retry_count = 0
        self._state = ConnectionState.DISCONNECTED
        # IRC-thread batching: accumulate parsed messages here and
        # dispatch them to the main thread in bulk instead of one
        # GLib.idle_add per line.
        self._irc_batch: list = []
        self._irc_batch_time = 0.0
        # threading.Event used for interruptible sleep between retries.
        self._wake_event = threading.Event()

    # ── Public API ─────────────────────────────────────────

    @property
    def state(self):
        """Current ``ConnectionState``."""
        return self._state

    def start(self):
        """Begin connecting.  Idempotent — safe to call multiple times."""
        if self._running:
            return
        self._running = True
        self._wake_event.clear()
        self._retry_count = 0
        self._set_state(ConnectionState.CONNECTING)
        threading.Thread(target=self._connect, daemon=True).start()

    def stop(self):
        """Disconnect and stop retrying.  Idempotent.

        Transitions to ``DISCONNECTED`` without firing the
        ``on_state_change`` callback — the caller is expected to
        be tearing down the UI and any ``GLib.idle_add`` callback
        would race with widget destruction.
        """
        self._running = False
        self._wake_event.set()  # interrupt any sleep
        self._close_socket()
        self._state = ConnectionState.DISCONNECTED

    def reconnect(self):
        """Manual reconnect after the client has given up.

        Resets the retry counter so that a fresh round of attempts
        begins.  No-op unless the current state is ``DISCONNECTED``.
        """
        if self._state != ConnectionState.DISCONNECTED:
            return
        self._running = True
        self._wake_event.clear()
        self._retry_count = 0
        self._set_state(ConnectionState.CONNECTING)
        threading.Thread(target=self._connect, daemon=True).start()

    # ── Internal helpers ───────────────────────────────────

    def _set_state(self, state):
        """Thread-safe state transition that fires the callback on the
        GLib main loop."""
        self._state = state
        if self._on_state_change is not None:
            GLib.idle_add(self._on_state_change, state, self._retry_count)

    def _close_socket(self):
        with self._socket_lock:
            if self._sock is None:
                return
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _send_raw(self, command, *args):
        if self._sock:
            msg = f"{command} {' '.join(args)}\r\n"
            self._sock.sendall(msg.encode())

    # ── Connection loop ────────────────────────────────────

    def _connect(self):
        """Reconnection loop — blocks until ``_running`` is cleared."""
        while self._running:
            try:
                self._sock = socket.create_connection((IRC_HOST, IRC_PORT), timeout=10)
                # Short timeout so we can send proactive PINGs.
                self._sock.settimeout(PING_CHECK_INTERVAL)
                self._send_raw("PASS", "justinfan12345")
                self._send_raw("NICK", "justinfan12345")
                self._send_raw("CAP REQ", ":twitch.tv/tags")
                self._send_raw("CAP REQ", ":twitch.tv/commands")
                self._send_raw("JOIN", f"#{self._channel}")

                self._retry_count = 0  # reset on success
                self._set_state(ConnectionState.CONNECTED)
                self._irc_batch = []
                self._irc_batch_time = time.monotonic()

                buf = b""
                last_data = time.monotonic()
                ping_sent_at: float | None = None

                while self._running:
                    try:
                        data = self._sock.recv(4096)
                        if not data:
                            self._flush_irc_batch()
                            break
                        last_data = time.monotonic()
                        ping_sent_at = None  # any data counts as a response
                        buf += data
                        while b"\r\n" in buf:
                            line, buf = buf.split(b"\r\n", 1)
                            parsed = self._handle_line(line.decode("utf-8"))
                            if parsed is not None:
                                kind, data = parsed
                                if kind == "msg":
                                    self._irc_batch.append(data)
                                elif kind == "roomstate" and self._on_roomstate is not None:
                                    GLib.idle_add(self._on_roomstate, data)
                                now = time.monotonic()
                                if (len(self._irc_batch) >= _IRC_BATCH_SIZE or
                                    (now - self._irc_batch_time >= _IRC_BATCH_MS)):
                                    self._flush_irc_batch()
                    except socket.timeout:
                        now = time.monotonic()
                        idle = now - last_data
                        if ping_sent_at is None:
                            # No PING sent yet — send one once we've been
                            # idle long enough.
                            if idle >= PING_INTERVAL:
                                self._send_raw("PING", ":tmi.twitch.tv")
                                ping_sent_at = now
                        elif (now - ping_sent_at) >= PING_INTERVAL:
                            # PING sent but no response — connection dead.
                            self._flush_irc_batch()
                            break
                        # Absolute maximum silence.
                        if idle >= PING_TIMEOUT:
                            self._flush_irc_batch()
                            break
                    except (OSError, UnicodeDecodeError):
                        break
            except OSError:
                pass
            finally:
                self._close_socket()

            if not self._running:
                break

            self._retry_count += 1
            if self._retry_count > RECONNECT_MAX_ATTEMPTS:
                self._set_state(ConnectionState.DISCONNECTED)
                self._running = False
                break

            self._set_state(ConnectionState.RECONNECTING)

            # Exponential back-off with jitter.
            base = RECONNECT_BASE_DELAY * (2 ** (self._retry_count - 1))
            delay = min(base, RECONNECT_MAX_DELAY)
            jitter = delay * RECONNECT_JITTER * (random.random() * 2 - 1)
            delay = max(0.5, delay + jitter)

            # Interruptible sleep.
            self._wake_event.wait(delay)
            if not self._running:
                break

    # ── Line handlers ──────────────────────────────────────

    @staticmethod
    def _empty_msg(text):
        """Return a minimal system-message dict with the given text."""
        return {
            "user": "",
            "text": text,
            "color": FALLBACK_USER_COLOR,
            "emotes": [],
            "badges": [],
            "action": False,
            "first_msg": False,
            "mod": False,
            "vip": False,
            "partner": False,
            "broadcaster": False,
            "system": True,
        }

    def _parse_clearchat(self, line):
        """Parse a CLEARCHAT line (timeout / ban), or return None."""
        parts = line.split("CLEARCHAT #", 1)
        if len(parts) != 2:
            return None

        tags_part = parts[0]
        body = parts[1]
        if " :" not in body:
            return None
        _channel, target = body.split(" :", 1)
        target = target.strip()

        ban_duration = None
        tag_match = _TAG_RE.match(tags_part)
        if tag_match:
            tags = tag_match.group(1)
            dur_match = re.search(r"ban-duration=([^;]+)", tags)
            if dur_match:
                try:
                    ban_duration = int(dur_match.group(1))
                except ValueError:
                    pass

        if ban_duration is not None:
            minutes = ban_duration // 60
            if minutes == 1:
                text = _("{target} was timed out (1 minute)").format(target=target)
            else:
                text = _("{target} was timed out ({minutes} minutes)").format(
                    target=target, minutes=minutes
                )
        else:
            text = _("{target} was banned").format(target=target)

        return self._empty_msg(text)

    @staticmethod
    def _parse_tags(tags_str: str) -> dict[str, str]:
        """Parse an IRC tags string into a dict.

        Values have ``\\s`` escape sequences replaced with spaces.
        Called once per USERNOTICE / PRIVMSG instead of compiling
        a fresh regex for every individual key lookup.
        """
        result: dict[str, str] = {}
        for m in _TAGS_PARSE_RE.finditer(tags_str):
            result[m.group(1)] = m.group(2).replace("\\s", " ")
        return result

    @staticmethod
    def _tier_label(plan_id):
        """Convert a sub-plan ID to a human-readable label."""
        if not plan_id:
            return "?"
        if plan_id == "Prime":
            return "Prime"
        if plan_id == "1000":
            return "Tier 1"
        if plan_id == "2000":
            return "Tier 2"
        if plan_id == "3000":
            return "Tier 3"
        return "Tier 1"

    def _build_sub_msg(self, tags: dict, is_resub: bool):
        """Build a subscription message from parsed USERNOTICE tags."""
        name = tags.get("display-name") or "Someone"
        tier = self._tier_label(tags.get("msg-param-sub-plan"))

        if not is_resub:
            return self._empty_msg(
                _("{name} subscribed with {tier}!").format(name=name, tier=tier)
            )

        months = tags.get("msg-param-cumulative-months")
        streak = tags.get("msg-param-streak-months")
        share = tags.get("msg-param-should-share-streak")

        base = _("{name} subscribed for {months} months").format(
            name=name, months=months or "?"
        )
        if share == "1" and streak and streak != "0" and streak != months:
            base += _(" ({streak} streak)").format(streak=streak)
        return self._empty_msg(_("{base} with {tier}!").format(base=base, tier=tier))

    def _build_subgift_msg(self, tags: dict, is_anon: bool):
        """Build a gift-sub message from parsed USERNOTICE tags."""
        recipient = tags.get("msg-param-recipient-display-name") or "Someone"
        tier = self._tier_label(tags.get("msg-param-sub-plan"))

        if is_anon:
            return self._empty_msg(
                _("Anonymous gifted {tier} to {recipient}!").format(
                    tier=tier, recipient=recipient
                )
            )

        gifter = tags.get("display-name") or "Someone"
        return self._empty_msg(
            _("{gifter} gifted {tier} to {recipient}!").format(
                gifter=gifter, tier=tier, recipient=recipient
            )
        )

    def _parse_usernotice(self, line):
        """Parse a USERNOTICE line (sub / raid / …), or return None."""
        parts = line.split("USERNOTICE #", 1)
        if len(parts) != 2:
            return None

        tags_part = parts[0]

        tag_match = _TAG_RE.match(tags_part)
        if not tag_match:
            return None
        tags = self._parse_tags(tag_match.group(1))

        msg_id = tags.get("msg-id")
        if not msg_id:
            return None

        if msg_id in ("sub", "resub"):
            return self._build_sub_msg(tags, msg_id == "resub")

        if msg_id in ("subgift", "anonsubgift"):
            return self._build_subgift_msg(tags, msg_id == "anonsubgift")

        if msg_id == "raid":
            vc = tags.get("msg-param-viewerCount")
            if vc and int(vc) < 10:
                return None
            name = tags.get("msg-param-displayName") or "Someone"
            count = vc or "?"
            return self._empty_msg(
                _("{name} is raiding with {count} viewers!").format(
                    name=name, count=count
                )
            )

        if msg_id in ("bitsbadgetier", "viewermilestone"):
            return None

        if msg_id == "announcement":
            # /announce — body text follows "#channel :"
            body = parts[1].split(" :", 1)[1] if " :" in parts[1] else ""
            name = tags.get("display-name") or "Someone"
            return self._empty_msg(_("📢 {name}: {body}").format(name=name, body=body))

        sys_msg = tags.get("system-msg")
        if sys_msg:
            return self._empty_msg(sys_msg)

        return None

    def _parse_roomstate(self, line):
        """Parse a ROOMSTATE line, returning a dict of mode=value
        integers, or None if the line doesn't match.

        Modes: emote-only, followers-only, r9k, slow, subs-only.
        followers-only: -1 disabled, 0 all followers, >0 minutes.
        """
        parts = line.split("ROOMSTATE #", 1)
        if len(parts) != 2:
            return None

        tags_part = parts[0]
        tag_match = _TAG_RE.match(tags_part)
        if not tag_match:
            return None

        tags = tag_match.group(1)
        state = {}
        for key in ("emote-only", "followers-only", "r9k", "slow", "subs-only"):
            match = re.search(rf"\b{key}=(-?\d+)", tags)
            if match:
                try:
                    state[key] = int(match.group(1))
                except ValueError:
                    pass
        return state if state else None

    def _handle_line(self, line):
        """Parse one IRC line, returning (kind, data) or None."""
        if line.startswith("PING"):
            self._send_raw("PONG", line[5:])
            return None

        msg = self._parse_privmsg(line)
        if msg:
            return ("msg", msg)

        msg = self._parse_clearchat(line)
        if msg:
            return ("msg", msg)

        msg = self._parse_usernotice(line)
        if msg:
            return ("msg", msg)

        state = self._parse_roomstate(line)
        if state:
            return ("roomstate", state)

        return None

    def _flush_irc_batch(self):
        """Dispatch the accumulated IRC batch to the main thread."""
        if not self._irc_batch or self._on_message is None:
            self._irc_batch = []
            return
        batch = self._irc_batch
        self._irc_batch = []
        self._irc_batch_time = time.monotonic()
        GLib.idle_add(self._dispatch_batch, batch)

    def _dispatch_batch(self, batch):
        """Deliver a batch of messages on the main thread."""
        cb = self._on_message
        if cb is None:
            return GLib.SOURCE_REMOVE
        for msg in batch:
            cb(msg)
        return GLib.SOURCE_REMOVE

    def _parse_privmsg(self, line):
        """Parse a PRIVMSG line into a dict, or return None."""
        parts = line.split("PRIVMSG #", 1)
        if len(parts) != 2:
            return None

        tags_part = parts[0]
        body = parts[1]
        if " :" not in body:
            return None
        user, text = body.split(" :", 1)

        # Detect CTCP ACTION (/me) — IRC convention used by Twitch.
        action = False
        action_offset = 0
        if text.startswith("\x01ACTION ") and text.endswith("\x01"):
            action = True
            action_offset = len("\x01ACTION ")

        display_name = None
        color = FALLBACK_USER_COLOR
        first_msg = False

        tag_match = _TAG_RE.match(tags_part)
        emotes = []
        badges = []
        if tag_match:
            tags = tag_match.group(1)
            color_match = _COLOR_RE.search(tags)
            if color_match:
                color = f"#{color_match.group(1)}"
            name_match = _DISPLAY_NAME_RE.search(tags)
            if name_match:
                raw = name_match.group(1)
                display_name = raw.replace("\\s", " ")
            emotes_match = _EMOTES_RE.search(tags)
            if emotes_match:
                emotes = _parse_emotes(
                    emotes_match.group(1), text, self._prefer_static_emotes
                )
            badges_match = _BADGES_RE.search(tags)
            if badges_match:
                badge_info_value = None
                badge_info_match = _BADGE_INFO_RE.search(tags)
                if badge_info_match:
                    badge_info_value = badge_info_match.group(1)
                badges = _parse_badges(badges_match.group(1), badge_info_value)
            first_msg_match = _FIRST_MSG_RE.search(tags)
            first_msg = first_msg_match is not None and first_msg_match.group(1) == "1"

        if display_name is None:
            user_part = line.split("!", 1)[0] if "!" in line else ""
            display_name = user_part.lstrip(":").strip()

        # Strip ACTION wrapper from display text and adjust emote positions
        if action:
            text = text[action_offset:-1]
            for em in emotes:
                em["positions"] = [
                    (s - action_offset, e - action_offset) for s, e in em["positions"]
                ]

        mod = any(b[1] == "moderator" for b in badges)
        vip = any(b[1] == "vip" for b in badges)
        partner = any(b[1] == "partner" for b in badges)
        broadcaster = any(b[1] == "broadcaster" for b in badges)

        return {
            "user": display_name or user.strip(),
            "text": text.strip(),
            "color": color,
            "emotes": emotes,
            "badges": badges,
            "action": action,
            "first_msg": first_msg,
            "mod": mod,
            "vip": vip,
            "partner": partner,
            "broadcaster": broadcaster,
        }


def _parse_badges(tag_value, badge_info_value=None):
    """Parse the badges tag into a list of [display_name, raw_id, tenure] triples."""
    sub_tenure = None
    if badge_info_value:
        for bi in badge_info_value.split(","):
            bi = bi.strip()
            if bi.startswith("subscriber/"):
                try:
                    sub_tenure = int(bi.split("/", 1)[1])
                except ValueError:
                    pass
                break

    result = []
    for badge in tag_value.split(","):
        badge = badge.strip()
        if "/" in badge:
            name, version = badge.split("/", 1)
            if name == "predictions":
                # Version is the colour + tier, e.g. "blue-1", "pink-2".
                # SVG files are named predictions-blue-1.svg etc.
                badge_id = f"{name}-{version}"
                result.append(["Prediction", badge_id, None])
                continue
            display = _BADGE_NAMES.get(name)
            if display:
                tenure = sub_tenure if name == "subscriber" else None
                result.append([display, name, tenure])
    return result


def _parse_emotes(tag_value, text, prefer_static=False):
    """Parse the emotes tag into a list of {source, name, url, positions}."""
    if prefer_static:
        cdn = TWITCH_EMOTE_CDN_STATIC
    else:
        cdn = TWITCH_EMOTE_CDN
    result = []
    for emote in tag_value.split("/"):
        if ":" not in emote:
            continue
        emote_id, positions_str = emote.split(":", 1)
        positions = []
        name = None
        for pos in positions_str.split(","):
            if "-" in pos:
                start, end = pos.split("-", 1)
                start, end = int(start), int(end)
                positions.append((start, end))
                if name is None and start < len(text) and end < len(text):
                    name = text[start : end + 1]
        if positions:
            result.append(
                {
                    "source": "Twitch",
                    "name": name or emote_id,
                    "url": cdn.format(id=emote_id),
                    "positions": positions,
                }
            )
    return result
