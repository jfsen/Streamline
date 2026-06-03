"""Chat page widget with embedded WebKit view."""

import gettext
import json
import logging
import re
import threading
from pathlib import Path

import gi

gi.require_version("WebKit", "6.0")

from gi.repository import Adw, Gdk, GLib, Gtk, WebKit

from .config import (
    CULL_CHUNK,
    FLUSH_MS,
    MAX_MESSAGES,
)
from .config import (
    STYLE as _CHAT_STYLE,
)
from .emotes import ThirdPartyEmotes
from .twitch_chat import TwitchChat

_ = gettext.gettext
logger = logging.getLogger("ChatPage")

# ── HTML template (loaded once at module level) ────────

_HTML = (Path(__file__).parent / "chat_page.html").read_text()


# ── Badge SVGs (loaded once at module level) ───────────

_BADGE_DIR = Path(__file__).parent / "badges"
_BADGE_SVGS = {}
for _f in _BADGE_DIR.glob("*.svg"):
    _BADGE_SVGS[_f.stem] = _f.read_text()


def _badge_svg_defs():
    """Build an inline SVG defs block so <use href="#badge-X"/> works."""
    if not _BADGE_SVGS:
        return ""
    parts = ["<svg style='display:none' xmlns='http://www.w3.org/2000/svg'>"]
    for name, svg in _BADGE_SVGS.items():
        # Strip outer <svg> tag and add id (handles single-line and multi-line tags)
        inner = re.sub(r"<svg\b", f'<symbol id="badge-{name}"', svg, count=1).replace(
            "</svg>", "</symbol>"
        )
        parts.append(inner)
    parts.append("</svg>")
    return "\n".join(parts)


def _build_html(alternating_bg, dark, disable_emote_animations=False):
    """Build the chat HTML with theme-aware colors."""
    s = _CHAT_STYLE
    theme = s["dark"] if dark else s["light"]
    page = _HTML
    page = page.replace("COLORTEXT", theme["text_color"])
    page = page.replace("COLORBANNERBG", theme["banner_bg"])
    page = page.replace("COLORBANNERFG", theme["banner_fg"])
    page = page.replace("FONTSIZE", s["font_size"])
    page = page.replace("FONTFAMILY", s["font_family"])
    page = page.replace("BODYTOPPAD", s["body_padding_top"])
    page = page.replace("BODYHORIZPAD", s["body_padding_horiz"])
    page = page.replace("ROWPAD", s["row_padding"])
    page = page.replace("LINEHEIGHT", s["line_height"])
    page = page.replace("USERWEIGHT", s["user_weight"])
    page = page.replace("USERMARGIN", s["user_margin"])
    page = page.replace("BANNERFONT", s["banner_font"])
    page = page.replace("BANNERPAD", s["banner_padding"])
    page = page.replace("MORE_MSG", json.dumps(_("More messages below")))
    page = page.replace("BODYCLASS", "dark" if dark else "light")
    page = page.replace("BADGE_SVGS", _badge_svg_defs())
    if alternating_bg:
        page = page.replace(
            "ROWCSS",
            f".msg:nth-child(even) {{ background: {theme['row_color']}; }}",
        )
    else:
        page = page.replace("ROWCSS", "")
    return page


def _to_js_positions(text, positions):
    """Convert Python code-point positions to JavaScript UTF-16 code-unit
    positions so ``text.substring(start, end)`` slices correctly."""
    result = []
    for start, end in positions:
        js_start = len(text[:start].encode("utf-16-le")) // 2
        js_end = len(text[: end + 1].encode("utf-16-le")) // 2 - 1
        result.append((js_start, js_end))
    return result


class ChatPage(Adw.NavigationPage):
    """A read-only Twitch chat page using an IRC connection."""

    def __init__(
        self,
        parent,
        streamer,
        display_name=None,
        alternating_bg=False,
        disable_emote_animations=False,
        theme="system",
        twitch=None,
        enable_detach=False,
    ):
        super().__init__(title=_("Chat: {}").format(display_name or streamer))

        from weakref import proxy

        if parent is not None:
            self.parent = proxy(parent)
        else:
            self.parent = None
        self._streamer = streamer
        self._chat = None
        self._msg_count = 0
        self._third_party_emotes = None
        self._alternating_bg = alternating_bg
        self._disable_emote_animations = disable_emote_animations
        self._msg_batch = []
        self._batch_flush_id = None
        self._suspend_signal_id = None
        self._suspend_window = None
        self._suspend_scheduled = False
        self._twitch = twitch
        self._dark = theme != "light"

        # Load BTTV/7TV emotes in background
        user_id = None
        twitch_api = self._twitch
        if twitch_api is None and self.parent is not None:
            twitch_api = getattr(self.parent, "twitch", None)
        if twitch_api is not None:
            user_cache = getattr(twitch_api, "user_cache", {})
            user_id = user_cache.get(streamer, {}).get("id")
        self._third_party_emotes = ThirdPartyEmotes(
            user_id,
            prefer_static=self._disable_emote_animations,
        )

        # Load emotes first, then start IRC in the same thread
        def _load_then_connect():
            self._third_party_emotes.load()
            self._chat = TwitchChat(
                streamer,
                on_message=self._on_message,
                prefer_static_emotes=self._disable_emote_animations,
            )
            self._chat.start()

        threading.Thread(target=_load_then_connect, daemon=True).start()
        self._webview = WebKit.WebView()
        self._webview.set_vexpand(True)
        self._webview.set_hexpand(True)
        self._webview.set_background_color(Gdk.RGBA(0, 0, 0, 0))
        settings = self._webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_webaudio(False)
        settings.set_enable_webgl(False)

        # Bridge for adding messages from GLib callbacks
        user_content = self._webview.get_user_content_manager()
        user_content.register_script_message_handler("chat")
        user_content.connect("script-message-received::chat", lambda *a: None)

        self._webview.load_html(
            _build_html(
                self._alternating_bg,
                self._dark,
                self._disable_emote_animations,
            ),
            None,
        )

        # Block context menu (Reload blanks load_html pages; Ctrl+C still works)
        self._webview.connect("context-menu", lambda *a: True)

        # Update chat colors when the system theme changes
        self._style_manager = Adw.StyleManager.get_default()
        self._style_manager.connect("notify::dark", self._on_theme_changed)

        # Toolbar
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_back_button(True)

        # Detach button (only in the main window, not in pop-ups)
        if enable_detach:
            detach_button = Gtk.Button(
                icon_name="window-new-symbolic",
                tooltip_text=_("Detach chat"),
            )
            detach_button.add_css_class("flat")
            detach_button.connect("clicked", self._on_detach)
            header.pack_end(detach_button)

        toolbar.add_top_bar(header)
        toolbar.set_content(self._webview)
        self.set_child(toolbar)

        self.connect("hidden", self._on_hidden)
        self.connect("map", self._on_map)

    def _on_theme_changed(self, style_manager, _pspec):
        """Re-inject CSS colors and re-clamp user name colours when dark/light mode changes."""
        if not self._webview:
            return
        dark = style_manager.get_dark()
        theme = _CHAT_STYLE["dark"] if dark else _CHAT_STYLE["light"]
        js = (
            f"document.body.style.color='{theme['text_color']}';"
            f"var p=document.getElementById('more-msg');"
            f"if(p){{p.style.background='{theme['banner_bg']}';p.style.color='{theme['banner_fg']}'}}"
        )
        if self._alternating_bg:
            js += (
                f"var s=document.createElement('style');"
                f"s.textContent='.msg:nth-child(even){{background:{theme['row_color']}}}';"
                f"s.id='row-color';"
                f"var old=document.getElementById('row-color');"
                f"if(old)old.remove();"
                f"document.head.appendChild(s);"
            )
        # Update body class and re-clamp all existing user name colours
        js += (
            f"document.body.className='{'dark' if dark else 'light'}';"
            f"document.querySelectorAll('.user').forEach(function(el){{"
            f"  el.style.color = clampColor(el.dataset.originalColor, {'true' if dark else 'false'});"
            f"}});"
        )
        self._webview.evaluate_javascript(js, -1, None, None, None, None, None)

    def _on_map(self, _widget):
        """Connect to window suspend signal once the page is in the widget tree."""
        if self._suspend_scheduled:
            return
        self._suspend_scheduled = True
        GLib.idle_add(self._connect_suspend)

    def _connect_suspend(self):
        """Deferred connection to window suspend signal."""
        toplevel = self.get_root()
        if toplevel:
            self._suspend_window = toplevel
            self._suspend_signal_id = toplevel.connect(
                "notify::suspended", self._on_suspend_changed
            )
        return GLib.SOURCE_REMOVE

    def _on_suspend_changed(self, window, _pspec):
        """When the window is suspended (minimised or on a different workspace),
        clear emote src attributes to free image memory. On resume, restore them."""
        suspended = window.props.suspended
        logger.debug("Suspend changed: suspended=%s", suspended)

        if suspended:
            self._webview.evaluate_javascript(
                "var imgs=document.querySelectorAll('img.emote');var n=0;for(var i=0;i<imgs.length;i++){if(imgs[i].src){imgs[i].dataset.src=imgs[i].src;imgs[i].src='';n++}};n",
                -1,
                None,
                None,
                None,
                None,
                None,
            )
        else:
            self._webview.evaluate_javascript(
                "var imgs=document.querySelectorAll('img.emote');var n=0;for(var i=0;i<imgs.length;i++){if(!imgs[i].src&&imgs[i].dataset.src){imgs[i].src=imgs[i].dataset.src;n++}};n",
                -1,
                None,
                None,
                None,
                None,
                None,
            )

    def _on_message(self, msg):
        self._msg_count += 1
        if self._msg_count > MAX_MESSAGES:
            self._msg_count -= CULL_CHUNK
            self._webview.evaluate_javascript(
                f"var c=document.getElementById('chat');var rh=0;for(var i=0;i<{CULL_CHUNK}&&c.firstChild;i++){{rh+=c.firstChild.offsetHeight||0;c.removeChild(c.firstChild);}}window.scrollTo(0,Math.max(0,window.scrollY-rh));_scrollToBottom()",
                -1,
                None,
                None,
                None,
                None,
                None,
            )

        emotes = list(msg["emotes"])
        if self._third_party_emotes:
            emotes.extend(self._third_party_emotes.find_emotes(msg["text"]))
        # Convert Python code-point positions to JS UTF-16 code-unit positions
        for em in emotes:
            em["positions"] = _to_js_positions(msg["text"], em["positions"])
        self._msg_batch.append(
            (
                msg["user"],
                msg["text"],
                msg["color"],
                emotes,
                msg.get("badges", []),
                msg.get("action", False),
            )
        )

        if self._batch_flush_id is None:
            self._batch_flush_id = GLib.timeout_add(FLUSH_MS, self._flush_messages)

    def _flush_messages(self):
        """Inject all queued messages into the WebView in one IPC call."""
        if not self._msg_batch:
            self._batch_flush_id = None
            return GLib.SOURCE_REMOVE
        batch = json.dumps(self._msg_batch)
        self._msg_batch.clear()
        self._batch_flush_id = None
        self._webview.evaluate_javascript(
            f"(function(){{var b={batch};b.forEach(function(m){{chat(m[0],m[1],m[2],m[3],m[4])}})}})()",
            -1,
            None,
            None,
            None,
            None,
            None,
        )
        return GLib.SOURCE_REMOVE

    def cleanup(self):
        """Stop chat and release resources. Idempotent."""
        if self._style_manager is not None:
            self._style_manager.disconnect_by_func(self._on_theme_changed)
            self._style_manager = None
        if self._suspend_signal_id is not None and self._suspend_window is not None:
            self._suspend_window.disconnect(self._suspend_signal_id)
            self._suspend_signal_id = None
            self._suspend_window = None
        if self._batch_flush_id is not None:
            GLib.source_remove(self._batch_flush_id)
            self._flush_messages()
        if self._chat:
            self._chat.stop()
            self._chat = None
        if self._webview:
            self._webview.stop_loading()
            try:
                self._webview.terminate_web_process()
            except Exception:
                pass
            self._webview = None

    def _on_hidden(self, page):
        self.cleanup()

    def _on_detach(self, button):
        """Open the chat in a separate window and go back to the streamer list."""
        if self.parent is None:
            return
        parent = self.parent
        root = self.get_root()
        from .chat_window import ChatWindow

        popup = ChatWindow(
            twitch=getattr(parent, "twitch", None),
            streamer=self._streamer,
            alternating_bg=self._alternating_bg,
            disable_emote_animations=self._disable_emote_animations,
            theme=getattr(parent, "theme", "system"),
            transient_for=root,
        )
        popup.connect(
            "close-request",
            lambda w, s=self._streamer: (
                getattr(parent, "_active_chats", {}).pop(s, None),
                False,
            )[-1],
        )
        getattr(parent, "_active_chats", {})[self._streamer] = popup
        popup.present()

        # Pop this page from the navigation view, returning to the streamer list
        parent.navigation_view.pop()
