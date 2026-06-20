"""Twitch IRC chat client (read-only, anonymous)."""

import enum
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
    ):
        self._channel = channel.lstrip("#").lower()
        self._on_message = on_message
        self._prefer_static_emotes = prefer_static_emotes
        self._on_state_change = on_state_change
        self._sock = None
        self._socket_lock = threading.Lock()
        self._running = False
        self._retry_count = 0
        self._state = ConnectionState.DISCONNECTED
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

                buf = b""
                last_data = time.monotonic()
                ping_sent_at: float | None = None

                while self._running:
                    try:
                        data = self._sock.recv(4096)
                        if not data:
                            break
                        last_data = time.monotonic()
                        ping_sent_at = None  # any data counts as a response
                        buf += data
                        while b"\r\n" in buf:
                            line, buf = buf.split(b"\r\n", 1)
                            self._handle_line(line.decode("utf-8"))
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
                            break
                        # Absolute maximum silence.
                        if idle >= PING_TIMEOUT:
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
                text = f"{target} was timed out (1 minute)"
            else:
                text = f"{target} was timed out ({minutes} minutes)"
        else:
            text = f"{target} was banned"

        return self._empty_msg(text)

    @staticmethod
    def _tag_val(tags, key):
        """Extract a tag value from the tags string, or return None."""
        m = re.search(rf"{re.escape(key)}=([^;]+)", tags)
        return m.group(1).replace("\\s", " ") if m else None

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

    def _build_sub_msg(self, tags, is_resub):
        """Build a subscription message from USERNOTICE tags."""
        name = self._tag_val(tags, "display-name") or "Someone"
        tier = self._tier_label(self._tag_val(tags, "msg-param-sub-plan"))

        if not is_resub:
            return self._empty_msg(f"{name} subscribed with {tier}!")

        months = self._tag_val(tags, "msg-param-cumulative-months")
        streak = self._tag_val(tags, "msg-param-streak-months")
        share = self._tag_val(tags, "msg-param-should-share-streak")

        base = f"{name} subscribed for {months or '?'} months"
        if share == "1" and streak and streak != "0" and streak != months:
            base += f" ({streak} streak)"
        return self._empty_msg(f"{base} with {tier}!")

    def _build_subgift_msg(self, tags, is_anon):
        """Build a gift-sub message from USERNOTICE tags."""
        recipient = self._tag_val(tags, "msg-param-recipient-display-name") or "Someone"
        tier = self._tier_label(self._tag_val(tags, "msg-param-sub-plan"))

        if is_anon:
            return self._empty_msg(f"Anonymous gifted {tier} to {recipient}!")

        gifter = self._tag_val(tags, "display-name") or "Someone"
        return self._empty_msg(f"{gifter} gifted {tier} to {recipient}!")

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

        msg_id = self._tag_val(tags, "msg-id")
        if not msg_id:
            return None

        if msg_id in ("sub", "resub"):
            return self._build_sub_msg(tags, msg_id == "resub")

        if msg_id in ("subgift", "anonsubgift"):
            return self._build_subgift_msg(tags, msg_id == "anonsubgift")

        if msg_id == "raid":
            vc = self._tag_val(tags, "msg-param-viewerCount")
            if vc and int(vc) < 10:
                return None
            name = self._tag_val(tags, "msg-param-displayName") or "Someone"
            count = vc or "?"
            return self._empty_msg(f"{name} is raiding with {count} viewers!")

        if msg_id in ("bitsbadgetier", "viewermilestone"):
            return None

        if msg_id == "announcement":
            # /announce — body text follows "#channel :"
            body = parts[1].split(" :", 1)[1] if " :" in parts[1] else ""
            name = self._tag_val(tags, "display-name") or "Someone"
            return self._empty_msg(f"📢 {name}: {body}")

        sys_msg = self._tag_val(tags, "system-msg")
        if sys_msg:
            return self._empty_msg(sys_msg)

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
