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
_BADGE_INFO_RE = re.compile(r"badge-info=([^;]+)")
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
            self._send_raw("CAP REQ", ":twitch.tv/tags")
            logger.debug("Requested twitch.tv/tags")
            self._send_raw("CAP REQ", ":twitch.tv/commands")
            logger.debug("Requested twitch.tv/commands")
            self._send_raw("JOIN", f"#{self._channel}")
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
                text = f"{target} was timed out (1 minute)"
            else:
                text = f"{target} was timed out ({minutes} minutes)"
        else:
            text = f"{target} was banned"

        return self._empty_msg(text)

    def _parse_usernotice(self, line):
        """Parse a USERNOTICE line (sub / raid / …), or return None."""
        parts = line.split("USERNOTICE #", 1)
        if len(parts) != 2:
            return None

        tags_part = parts[0]

        tag_match = _TAG_RE.match(tags_part)
        if not tag_match:
            return None
        tags = tag_match.group(1)

        msg_id_match = re.search(r"msg-id=([^;]+)", tags)
        if not msg_id_match:
            return None
        msg_id = msg_id_match.group(1)

        # Prefer the human-readable system-msg when available.
        sys_msg_match = re.search(r"system-msg=([^;]+)", tags)
        if sys_msg_match and sys_msg_match.group(1):
            text = sys_msg_match.group(1).replace("\\s", " ")
            return self._empty_msg(text)

        # Build a fallback message for raid (system-msg is often absent).
        if msg_id == "raid":
            dn_match = re.search(r"msg-param-displayName=([^;]+)", tags)
            vc_match = re.search(r"msg-param-viewerCount=([^;]+)", tags)
            name = dn_match.group(1).replace("\\s", " ") if dn_match else "Someone"
            count = vc_match.group(1) if vc_match else "?"
            text = f"{name} is raiding with {count} viewers!"
            return self._empty_msg(text)

        return None

    def _handle_line(self, line):
        if line.startswith("PING"):
            self._send_raw("PONG", line[5:])
            return

        msg = self._parse_privmsg(line)
        if msg:
            GLib.idle_add(self._on_message, msg)
            return

        msg = self._parse_clearchat(line)
        if msg:
            logger.debug("CLEARCHAT → %r", msg["text"])
            GLib.idle_add(self._on_message, msg)
            return

        msg = self._parse_usernotice(line)
        if msg:
            logger.debug("USERNOTICE → %r", msg["text"])
            GLib.idle_add(self._on_message, msg)
            return

        # Log unhandled lines (ROOMSTATE, USERSTATE, NOTICE, etc.) at
        # debug level so we can see what Twitch is actually sending.
        if not line.startswith("PONG") and "PRIVMSG" not in line:
            logger.debug("Unhandled IRC: %s", line)

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


def _parse_badges(tag_value, badge_info_value=None):
    """Parse the badges tag into a list of [display_name, raw_id, tenure] triples.

    tenure is an integer month count for subscriber badges (from badge-info),
    or None for all other badge types.
    """
    # Extract subscriber tenure from badge-info if present.
    # badge-info format: "subscriber/12" or "subscriber/12,founder/0"
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
            name, _version = badge.split("/", 1)
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
