"""Twitch IRC chat client (read-only, anonymous)."""

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
    TWITCH_EMOTE_CDN,
)

logger = logging.getLogger("IRCChat")

# Reconnection backoff limits (seconds)
_RECONNECT_BASE_DELAY = 2
_RECONNECT_MAX_DELAY = 120

# Hardcoded fallback IPs for irc.chat.twitch.tv (TCP/6667)
# Used when DNS resolution fails (e.g. after network outage)
_IRC_FALLBACK_IPS = (
    "34.212.92.60",
    "44.227.173.36",
    "44.237.40.50",
)

# Known badge names — only these are rendered from the IRC badges tag.
# Keys are IRC badge IDs; values are the display name used in tooltips.
_BADGE_NAMES = {
    "broadcaster": "Broadcaster",
    "moderator": "Moderator",
    "vip": "VIP",
    "subscriber": "Subscriber",
    "founder": "Founder",
    "partner": "Partner",
    "staff": "Staff",
    "admin": "Admin",
    "global_mod": "Global Mod",
    "no_audio": "No Audio",
    "no_video": "No Video",
}

# Twitch IRC tags for extracting display name and color
_TAG_RE = re.compile(r"@([^ ]+) ")
_COLOR_RE = re.compile(r"color=#([0-9A-Fa-f]{6})")
_DISPLAY_NAME_RE = re.compile(r"display-name=([^;]+)")
_EMOTES_RE = re.compile(r"emotes=([^;]+)")
_BADGES_RE = re.compile(r"badges=([^;]+)")


class TwitchChat:
    """Connects to Twitch IRC and emits messages via a callback.

    Automatically reconnects with exponential backoff when the
    connection drops (e.g. internet outage, system suspend).
    """

    def __init__(self, channel, on_message, on_connected=None, on_disconnected=None):
        self._channel = channel.lstrip("#").lower()
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._sock = None
        self._running = False
        self._reconnect_delay = 0  # current backoff, reset on success
        self._justinfan = f"justinfan{random.randint(10000, 99999)}"
        self._wake_event = threading.Event()  # pulsed by reconnect()

    def start(self):
        self._running = True
        threading.Thread(target=self._connect_loop, daemon=True).start()

    def stop(self):
        self._running = False
        self._close_socket()

    def reconnect(self):
        """Force a reconnection (e.g. after system wake).

        Closes the current socket so the read loop exits, and the
        outer reconnect loop will pick it up on its own.
        """
        self._reconnect_delay = 0  # reset backoff — immediate retry
        self._justinfan = f"justinfan{random.randint(10000, 99999)}"
        self._wake_event.set()  # interrupt any sleep in _connect_loop
        self._close_socket()

    def _close_socket(self):
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def _create_irc_connection(self):
        """Connect to Twitch IRC, falling back to hardcoded IPs if DNS fails."""
        # Try DNS first
        try:
            return socket.create_connection((IRC_HOST, IRC_PORT), timeout=10)
        except socket.gaierror as e:
            logger.debug("DNS failed for %s: %s, trying fallback IPs", IRC_HOST, e)
        except OSError as e:
            # Non-DNS error (e.g. network unreachable) — don't bother with fallback
            raise

        # DNS failed — try each fallback IP
        for ip in _IRC_FALLBACK_IPS:
            try:
                sock = socket.create_connection((ip, IRC_PORT), timeout=10)
                logger.debug("Connected via fallback IP %s", ip)
                return sock
            except OSError as e:
                logger.debug("Fallback IP %s failed: %s", ip, e)

        # All fallbacks exhausted — raise the original DNS error
        raise socket.gaierror(
            f"DNS and all fallback IPs failed for {IRC_HOST}"
        ) from None

    def _connect_loop(self):
        """Outer loop: keep (re)connecting while _running is True."""
        first_connect = True
        while self._running:
            if not first_connect:
                # Apply exponential backoff before each retry
                delay = self._reconnect_delay
                if delay == 0:
                    delay = _RECONNECT_BASE_DELAY
                else:
                    delay = min(delay * 2, _RECONNECT_MAX_DELAY)
                self._reconnect_delay = delay
                logger.debug("Reconnecting to #%s in %ds", self._channel, delay)
                # Sleep in small increments so we can react to stop() quickly
                self._wake_event.clear()
                deadline = time.monotonic() + delay
                while self._running and time.monotonic() < deadline:
                    if self._wake_event.wait(0.5):
                        break  # reconnect() was called — retry immediately
                if not self._running:
                    break
            first_connect = False

            if self._connect_once():
                # Connection succeeded and then dropped — signal and retry
                self._reconnect_delay = max(
                    self._reconnect_delay or _RECONNECT_BASE_DELAY,
                    _RECONNECT_BASE_DELAY,
                )
            # else: connect itself failed — the delay above will handle backoff

    def _connect_once(self):
        """Attempt a single connection + read loop. Returns True if we
        connected, authenticated, and then the socket dropped (so we
        should retry). Returns False only if the initial connect/join
        itself failed."""
        # Always clean up any previous socket before attempting
        self._close_socket()

        logger.debug("Connecting to IRC for #%s", self._channel)
        try:
            self._sock = self._create_irc_connection()
            # Read timeout: if we get no data for this long, the connection
            # is considered dead.  Twitch IRC sends PING every ~5 min, so a
            # 30 s gap means the server has stopped talking to us.
            self._sock.settimeout(30)
            self._send_raw("PASS", "justinfan12345")
            self._send_raw("NICK", self._justinfan)
            self._send_raw("JOIN", f"#{self._channel}")
            self._send_raw("CAP REQ", "twitch.tv/tags")
            self._send_raw("CAP REQ", "twitch.tv/commands")
            logger.debug("Joined #%s", self._channel)

            if self._on_connected:
                GLib.idle_add(self._on_connected)

            buf = b""
            timeout_count = 0
            while self._running:
                try:
                    data = self._sock.recv(4096)
                    if not data:
                        break
                    timeout_count = 0  # reset — data is flowing
                    buf += data
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        self._handle_line(line.decode("utf-8"))
                except socket.timeout:
                    timeout_count += 1
                    # Twitch IRC pings every ~5 min. Two consecutive
                    # timeouts (60 s total) means the connection is dead.
                    if timeout_count >= 2:
                        logger.debug(
                            "Read timeout for #%s — connection dead",
                            self._channel,
                        )
                        break
                    continue
                except (OSError, UnicodeDecodeError):
                    break

            # Read loop exited — socket is dead
            was_running = self._running
            self._close_socket()

            if was_running:
                logger.debug("Socket dropped for #%s", self._channel)
                if self._on_disconnected:
                    GLib.idle_add(self._on_disconnected)

            return True
        except OSError as e:
            logger.debug("Failed to connect to IRC for #%s: %s", self._channel, e)
            self._close_socket()
            return False

    def _send_raw(self, command, *args):
        if self._sock:
            msg = f"{command} {' '.join(args)}\r\n"
            self._sock.sendall(msg.encode())

    def _handle_line(self, line):
        if line.startswith("PING"):
            self._send_raw("PONG", line[5:])
            return

        msg = self._parse_privmsg(line)
        if msg:
            GLib.idle_add(self._on_message, msg)

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

        # Detect CTCP ACTION (/me) — IRC convention used by Twitch
        action = False
        if text.startswith("\x01ACTION ") and text.endswith("\x01"):
            action = True
            text = text[8:-1]  # strip \x01ACTION and trailing \x01

        display_name = None
        color = FALLBACK_USER_COLOR

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
                emotes = _parse_emotes(emotes_match.group(1), text)
            badges_match = _BADGES_RE.search(tags)
            if badges_match:
                badges = _parse_badges(badges_match.group(1))

        if display_name is None:
            user_part = line.split("!", 1)[0] if "!" in line else ""
            display_name = user_part.lstrip(":").strip()

        return {
            "user": display_name or user.strip(),
            "text": text.strip(),
            "color": color,
            "emotes": emotes,
            "badges": badges,
            "action": action,
        }


def _parse_badges(tag_value):
    """Parse the badges tag into a list of [display_name, raw_id] pairs."""
    result = []
    for badge in tag_value.split(","):
        badge = badge.strip()
        if "/" in badge:
            name, _version = badge.split("/", 1)
            display = _BADGE_NAMES.get(name)
            if display:
                result.append([display, name])
    return result


def _parse_emotes(tag_value, text):
    """Parse the emotes tag into a list of {source, name, url, positions}."""
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
                    "url": TWITCH_EMOTE_CDN.format(id=emote_id),
                    "positions": positions,
                }
            )
    return result
