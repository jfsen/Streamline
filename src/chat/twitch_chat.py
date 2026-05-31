"""Twitch IRC chat client (read-only, anonymous)."""

import logging
import re
import socket
import threading

from gi.repository import GLib

from .config import (
    BADGE_NAMES,
    FALLBACK_USER_COLOR,
    IRC_HOST,
    IRC_PORT,
    TWITCH_EMOTE_CDN,
)

logger = logging.getLogger("IRCChat")

# Twitch IRC tags for extracting display name and color
_TAG_RE = re.compile(r"@([^ ]+) ")
_COLOR_RE = re.compile(r"color=#([0-9A-Fa-f]{6})")
_DISPLAY_NAME_RE = re.compile(r"display-name=([^;]+)")
_EMOTES_RE = re.compile(r"emotes=([^;]+)")
_BADGES_RE = re.compile(r"badges=([^;]+)")


class TwitchChat:
    """Connects to Twitch IRC and emits messages via a callback."""

    def __init__(self, channel, on_message, on_connected=None):
        self._channel = channel.lstrip("#").lower()
        self._on_message = on_message
        self._on_connected = on_connected
        self._sock = None
        self._running = False

    def start(self):
        self._running = True
        threading.Thread(target=self._connect, daemon=True).start()

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def _connect(self):
        logger.debug("Connecting to IRC for #%s", self._channel)
        try:
            self._sock = socket.create_connection((IRC_HOST, IRC_PORT), timeout=10)
            self._sock.settimeout(None)
            self._send_raw("PASS", "justinfan12345")
            self._send_raw("NICK", "justinfan12345")
            self._send_raw("JOIN", f"#{self._channel}")
            self._send_raw("CAP REQ", "twitch.tv/tags")
            logger.debug("Joined #%s", self._channel)

            if self._on_connected:
                GLib.idle_add(self._on_connected)

            buf = b""
            while self._running:
                try:
                    data = self._sock.recv(4096)
                    if not data:
                        break
                    buf += data
                    while b"\r\n" in buf:
                        line, buf = buf.split(b"\r\n", 1)
                        self._handle_line(line.decode("utf-8"))
                except (OSError, UnicodeDecodeError):
                    break
        except OSError:
            logger.debug("Failed to connect to IRC for #%s", self._channel)

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
        }


def _parse_badges(tag_value):
    """Parse the badges tag into a list of badge names."""
    result = []
    for badge in tag_value.split(","):
        badge = badge.strip()
        if "/" in badge:
            name, _version = badge.split("/", 1)
            if name in BADGE_NAMES:
                result.append(name)
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
