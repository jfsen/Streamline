"""Twitch IRC chat client (read-only, anonymous)."""

import logging
import re
import socket
import threading

from gi.repository import GLib

from .config import (
    FALLBACK_USER_COLOR,
    IRC_HOST,
    IRC_PORT,
    TWITCH_EMOTE_CDN,
    TWITCH_EMOTE_CDN_STATIC,
)

logger = logging.getLogger("IRCChat")

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
_FIRST_MSG_RE = re.compile(r"first-msg=([^;]+)")


class TwitchChat:
    """Connects to Twitch IRC and emits messages via a callback."""

    def __init__(self, channel, on_message, prefer_static_emotes=False):
        self._channel = channel.lstrip("#").lower()
        self._on_message = on_message
        self._prefer_static_emotes = prefer_static_emotes
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

        # Detect CTCP ACTION (/me) — IRC convention used by Twitch.
        # Save the offset so emote positions can be adjusted after parsing,
        # since the IRC tag references the original (pre-strip) text.
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
                badges = _parse_badges(badges_match.group(1))
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

        # Detect moderator from badges (avoids parsing the tags again).
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
