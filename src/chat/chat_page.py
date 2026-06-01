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

from .config import STYLE as _CHAT_STYLE
from .emotes import ThirdPartyEmotes
from .twitch_chat import TwitchChat

_ = gettext.gettext
logger = logging.getLogger("ChatPage")

_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><style>
  :root { }
  @font-face { font-family: 'Emoji'; src: local('Noto Color Emoji'); unicode-range: U+2600-26FF, U+2700-27BF, U+1F300-1F5FF, U+1F600-1F64F, U+1F680-1F6FF, U+1F900-1F9FF, U+1FA00-1FA6F, U+1FA70-1FAFF, U+231A-231B, U+2328, U+23CF, U+23E9-23F3, U+23F8-23FA, U+200D, U+FE0F; }
  body {
    margin: 0; padding: BODYTOPPAD 0;
    background: transparent;
    font: FONTSIZE FONTFAMILY;
    overflow-wrap: break-word;
    color: COLORTEXT;
  }
  .msg { padding: ROWPAD BODYHORIZPAD; line-height: LINEHEIGHT; contain: layout style paint; }
  .user { font-weight: USERWEIGHT; margin-right: USERMARGIN; }
  .text {}
  .badge { display: inline-block; vertical-align: middle; margin-right: 2px; pointer-events: bounding-box; contain: layout style paint; }
  .emote { vertical-align: middle; }
  #more-msg {
    position: fixed; bottom: PILLBOTTOM; left: 50%; transform: translateX(-50%);
    padding: PILLPAD; border-radius: 999px; z-index: 99;
    font: PILLFONT; cursor: pointer;
    white-space: nowrap;
    background: COLORPILLBG; color: COLORPILLFG;
  }
  ROWCSS
</style></head><body>
BADGE_SVGS
<div id="chat"></div>
<script>
var _moreMsg = MORE_MSG;
var _scrollThresh = SCROLL_THRESH;
var _maxMsgs = MAX_MSGS;
var _cullChunk = CULL_CHUNK;
(function() {
  var chat = document.getElementById('chat');
  var paused = false;

  window.addEventListener('scroll', function() {
    var atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - _scrollThresh;
    if (atBottom) {
      paused = false;
      var btn = document.getElementById('more-msg');
      if (btn) btn.style.display = 'none';
    } else if (!paused) {
      paused = true;
      var btn = document.getElementById('more-msg');
      if (!btn) {
        btn = document.createElement('div');
        btn.id = 'more-msg';
        btn.textContent = _moreMsg;
        btn.onclick = function() {
          paused = false;
          window.scrollTo(0, document.body.scrollHeight);
        };
        document.body.appendChild(btn);
      }
      btn.style.display = 'block';
    }
  });

  // Debounced scroll-to-bottom: at most one layout read+write per frame.
  // Called explicitly after message appends, image loads, and culling.
  var _scrollRaf = 0;
  function _scrollToBottom() {
    if (paused || _scrollRaf) return;
    _scrollRaf = requestAnimationFrame(function() {
      window.scrollTo(0, document.body.scrollHeight);
      _scrollRaf = 0;
    });
  }
  // Capture-phase listener: image loads can grow height after the message
  // was appended, so re-scroll when any <img> finishes loading.
  chat.addEventListener('load', function(e) {
    if (e.target.tagName === 'IMG') _scrollToBottom();
  }, true);

  // Always hide emotes when scrolled out of view
  var io = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) {
      e.target.querySelectorAll('img.emote').forEach(function(img) {
        if (e.isIntersecting) {
          if (img.dataset.src) { img.src = img.dataset.src; }
        } else {
          if (img.src) { img.dataset.src = img.src; img.src = ''; }
        }
      });
    });
  });

  // Minimal HTML escaping for user-supplied text
  function _esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Cached badge HTML fragments — each [id,display] pair is built once
  var _badgeTpl = {};
  function _badgeHTML(id, display) {
    var key = id + '|' + display;
    if (!_badgeTpl[key]) {
      _badgeTpl[key] = '<svg class="badge" width="18" height="18">'
        + '<title>' + display + '</title>'
        + '<use href="#badge-' + id + '"/>'
        + '</svg>';
    }
    return _badgeTpl[key];
  }

  window.chat = function(user, text, color, emotes, badges, action) {
    var html = '<div class="msg"' + (action ? ' style="font-style:italic"' : '') + '>';

    // Badges
    if (badges && badges.length) {
      for (var bi = 0; bi < badges.length; bi++) {
        html += _badgeHTML(badges[bi][1], badges[bi][0]);
      }
    }

    // User name
    html += '<span class="user" style="color:' + color + '">' + _esc(user) + (action ? '' : ':') + '</span>';

    // Message body: interleave emote <img> tags with text <span> segments
    if (emotes && emotes.length) {
      var pm = {};
      for (var ei = 0; ei < emotes.length; ei++) {
        var em = emotes[ei];
        for (var pi = 0; pi < em.positions.length; pi++) {
          var pos = em.positions[pi];
          pm[pos[0]] = {end: pos[1], url: em.url, name: em.name, source: em.source};
        }
      }
      var i = 0, n = text.length;
      while (i < n) {
        if (pm[i]) {
          var ed = pm[i];
          html += '<img class="emote" src="' + ed.url + '" title="' + _esc((ed.name || 'Emote') + ' (' + (ed.source || '?') + ')') + '" decoding="async" loading="lazy">';
          i = ed.end + 1;
        } else {
          var end = i;
          while (end < n && !pm[end]) end++;
          html += '<span class="text">' + _esc(text.substring(i, end)) + '</span>';
          i = end;
        }
      }
    } else {
      html += '<span class="text">' + _esc(text) + '</span>';
    }

    html += '</div>';
    chat.insertAdjacentHTML('beforeend', html);
    io.observe(chat.lastElementChild);
    _scrollToBottom();
  };
})();
</script></body></html>"""


# ── Badge SVGs (loaded once at module level) ────────────────

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


def _build_html(alternating_bg, dark):
    """Build the chat HTML with theme-aware colors."""
    s = _CHAT_STYLE
    theme = s["dark"] if dark else s["light"]
    html = _HTML
    html = html.replace("COLORTEXT", theme["text_color"])
    html = html.replace("COLORPILLBG", theme["pill_bg"])
    html = html.replace("COLORPILLFG", theme["pill_fg"])
    html = html.replace("FONTSIZE", s["font_size"])
    html = html.replace("FONTFAMILY", s["font_family"])
    html = html.replace("BODYTOPPAD", s["body_padding_top"])
    html = html.replace("BODYHORIZPAD", s["body_padding_horiz"])
    html = html.replace("ROWPAD", s["row_padding"])
    html = html.replace("LINEHEIGHT", s["line_height"])
    html = html.replace("USERWEIGHT", s["user_weight"])
    html = html.replace("USERMARGIN", s["user_margin"])
    html = html.replace("PILLFONT", s["pill_font"])
    html = html.replace("PILLBOTTOM", s["pill_bottom"])
    html = html.replace("PILLPAD", s["pill_padding"])
    html = html.replace("MORE_MSG", json.dumps(_("More messages below")))
    html = html.replace("SCROLL_THRESH", str(s["scroll_threshold"]))
    html = html.replace("MAX_MSGS", str(s["max_messages"]))
    html = html.replace("CULL_CHUNK", str(s["cull_chunk"]))
    html = html.replace("BADGE_SVGS", _badge_svg_defs())
    if alternating_bg:
        html = html.replace(
            "ROWCSS",
            f".msg:nth-child(even) {{ background: {theme['row_color']}; }}",
        )
    else:
        html = html.replace("ROWCSS", "")
    return html


class ChatPage(Adw.NavigationPage):
    """A read-only Twitch chat page using an IRC connection."""

    def __init__(
        self,
        parent,
        streamer,
        alternating_bg=False,
        theme="system",
        twitch=None,
        enable_detach=False,
    ):
        super().__init__(title=_("Chat: {}").format(streamer))

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
        self._third_party_emotes = ThirdPartyEmotes(user_id)

        # Load emotes first, then start IRC in the same thread
        def _load_then_connect():
            self._third_party_emotes.load()
            self._chat = TwitchChat(
                streamer,
                on_message=self._on_message,
                on_connected=self._on_connected,
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

        self._webview.load_html(_build_html(self._alternating_bg, self._dark), None)

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

    def _on_connected(self):
        logger.debug("Connected to chat for %s", self._streamer)

    def _on_theme_changed(self, style_manager, _pspec):
        """Re-inject CSS colors when dark/light mode changes."""
        if not self._webview:
            return
        dark = style_manager.get_dark()
        theme = _CHAT_STYLE["dark"] if dark else _CHAT_STYLE["light"]
        js = (
            f"document.body.style.color='{theme['text_color']}';"
            f"var p=document.getElementById('more-msg');"
            f"if(p){{p.style.background='{theme['pill_bg']}';p.style.color='{theme['pill_fg']}'}}"
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
        """Clear emote image sources when the window is suspended
        (minimised or on a different workspace), restoring them on resume."""
        suspended = window.props.suspended
        logger.debug("Suspend changed: suspended=%s", suspended)
        if suspended:
            self._webview.evaluate_javascript(
                "var imgs=document.querySelectorAll('img.emote');var n=0;for(var i=0;i<imgs.length;i++){if(imgs[i].src){imgs[i].dataset.src=imgs[i].src;imgs[i].src='';n++}};n",
                -1,
                None,
                None,
                None,
                self._on_suspend_done,
                ("suspended",),
            )
        else:
            self._webview.evaluate_javascript(
                "var imgs=document.querySelectorAll('img.emote');var n=0;for(var i=0;i<imgs.length;i++){if(!imgs[i].src&&imgs[i].dataset.src){imgs[i].src=imgs[i].dataset.src;n++}};n",
                -1,
                None,
                None,
                None,
                self._on_suspend_done,
                ("resumed",),
            )

    def _on_suspend_done(self, source, result, action):
        """Log the result of a suspend/resume JS evaluation."""
        action = action[0] if isinstance(action, tuple) else action
        try:
            js_value = self._webview.evaluate_javascript_finish(result)
            count = js_value.to_int32()
            logger.debug("Suspend %s: %d emotes affected", action, count)
        except Exception as e:
            logger.debug("Suspend %s: could not read result (%s)", action, e)

    def _on_message(self, msg):
        self._msg_count += 1
        if self._msg_count > _CHAT_STYLE["max_messages"]:
            self._msg_count -= _CHAT_STYLE["cull_chunk"]
            self._webview.evaluate_javascript(
                f"var c=document.getElementById('chat');for(var i=0;i<{_CHAT_STYLE['cull_chunk']}&&c.firstChild;i++)c.removeChild(c.firstChild);_scrollToBottom()",
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
            self._batch_flush_id = GLib.timeout_add(
                _CHAT_STYLE["flush_ms"], self._flush_messages
            )

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
