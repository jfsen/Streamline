"""Chat page widget with embedded WebKit view."""

import gettext
import json
import logging
import threading

import gi

gi.require_version("WebKit", "6.0")

from gi.repository import Adw, Gdk, GLib, Gtk, WebKit

from .emotes import ThirdPartyEmotes
from .twitch_chat import TwitchChat

_ = gettext.gettext
logger = logging.getLogger("ChatPage")

_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><style>
  :root { color-scheme: light; }
  body {
    margin: 0; padding: 4px 8px;
    background: transparent;
    font: 15px Inter, sans-serif;
    overflow-wrap: break-word;
    color: COLORTEXT;
  }
  .msg { margin: 1px 0; line-height: 1.4; }
  .user { font-weight: 700; margin-right: 4px; }
  .text {}
  #more-msg {
    position: fixed; bottom: 8px; left: 50%; transform: translateX(-50%);
    padding: 4px 12px; border-radius: 999px; z-index: 99;
    font: bold 13px Inter, sans-serif; cursor: pointer;
    white-space: nowrap;
    background: COLORPILLBG; color: COLORPILLFG;
  }
  ROWCSS
</style></head><body><div id="chat"></div>
<script>
(function() {
  var chat = document.getElementById('chat');
  var paused = false;

  window.addEventListener('scroll', function() {
    var atBottom = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
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
        btn.textContent = 'More messages below';
        btn.onclick = function() {
          paused = false;
          window.scrollTo(0, document.body.scrollHeight);
        };
        document.body.appendChild(btn);
      }
      btn.style.display = 'block';
    }
  });

  // Resume auto-scroll if images load and we're near the bottom
  new MutationObserver(function() {
    if (!paused) window.scrollTo(0, document.body.scrollHeight);
  }).observe(chat, {childList: true, subtree: true});

  // Pause animated emotes outside the viewport
  var io = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) {
      if (e.isIntersecting) {
        if (e.target.dataset.src) e.target.src = e.target.dataset.src;
      } else {
        if (!e.target.dataset.src) e.target.dataset.src = e.target.src;
        e.target.src = '';
      }
    });
  });

  // Pause all animated emotes when the page is hidden
  document.addEventListener('visibilitychange', function() {
    var imgs = document.querySelectorAll('img.emote');
    if (document.hidden) {
      imgs.forEach(function(img) {
        if (!img.dataset.src) img.dataset.src = img.src;
        img.src = '';
      });
    } else {
      imgs.forEach(function(img) {
        if (img.dataset.src) img.src = img.dataset.src;
      });
    }
  });

  window.chat = function(user, text, color, emotes) {
    var div = document.createElement('div');
    div.className = 'msg';
    var u = document.createElement('span');
    u.className = 'user';
    u.style.color = color;
    u.textContent = user + ':';
    div.appendChild(u);
    if (emotes && emotes.length) {
      var pm = {};
      emotes.forEach(function(e) {
        e.positions.forEach(function(p) { pm[p[0]] = {end: p[1], url: e.url}; });
      });
      var i = 0, n = text.length;
      while (i < n) {
        if (pm[i]) {
          var img = document.createElement('img');
          img.src = pm[i].url;
          img.className = 'emote';
          img.style.height = '1.2em';
          img.style.verticalAlign = 'middle';
          div.appendChild(img);
          io.observe(img);
          i = pm[i].end + 1;
        } else {
          var end = i;
          while (end < n && !pm[end]) end++;
          var t = document.createElement('span');
          t.className = 'text';
          t.textContent = text.substring(i, end);
          div.appendChild(t);
          i = end;
        }
      }
    } else {
      var t = document.createElement('span');
      t.className = 'text';
      t.textContent = text;
      div.appendChild(t);
    }
    chat.appendChild(div);
    if (!paused) window.scrollTo(0, document.body.scrollHeight);
  };
})();
</script></body></html>"""


def _build_html(alternating_bg, row_color, dark):
    """Build the chat HTML with theme-aware colors."""
    if dark:
        html = _HTML.replace("COLORTEXT", "#dedede")
        html = html.replace("COLORPILLBG", "rgba(255,255,255,0.88)")
        html = html.replace("COLORPILLFG", "#1a1a1a")
    else:
        html = _HTML.replace("COLORTEXT", "#2e2e2e")
        html = html.replace("COLORPILLBG", "rgba(0,0,0,0.82)")
        html = html.replace("COLORPILLFG", "#eee")
    if alternating_bg:
        html = html.replace(
            "ROWCSS",
            f".msg:nth-child(even) {{ background: {row_color}; }}",
        )
    else:
        html = html.replace("ROWCSS", "")
    return html


class ChatPage(Adw.NavigationPage):
    """A read-only Twitch chat page using an IRC connection."""

    def __init__(self, parent, streamer, alternating_bg=True, theme="system"):
        super().__init__(title=f"Chat: {streamer}")

        from weakref import proxy

        self.parent = proxy(parent)
        self._streamer = streamer
        self._chat = None
        self._msg_count = 0
        self._third_party_emotes = None
        self._alternating_bg = alternating_bg
        self._msg_batch = []
        self._batch_flush_id = None

        # Pick alternating row color based on theme
        if theme == "light":
            self._dark = False
            self._row_color = "rgba(0,0,0,0.03)"
        else:
            self._dark = True
            self._row_color = "rgba(255,255,255,0.04)"

        # Load BTTV/7TV emotes in background
        user_id = None
        twitch = getattr(self.parent, "twitch", None)
        if twitch is not None:
            user_cache = getattr(twitch, "user_cache", {})
            user_id = user_cache.get(streamer, {}).get("id")
        self._third_party_emotes = ThirdPartyEmotes(user_id)
        threading.Thread(target=self._third_party_emotes.load, daemon=True).start()

        # WebKit view
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
            _build_html(self._alternating_bg, self._row_color, self._dark), None
        )

        # Toolbar
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_back_button(True)
        toolbar.add_top_bar(header)
        toolbar.set_content(self._webview)
        self.set_child(toolbar)

        self.connect("hidden", self._on_hidden)

        # Start IRC connection
        self._chat = TwitchChat(
            streamer,
            on_message=self._on_message,
            on_connected=self._on_connected,
        )
        self._chat.start()

    def _on_connected(self):
        logger.debug("Connected to chat for %s", self._streamer)

    def _on_message(self, msg):
        self._msg_count += 1
        if self._msg_count > 500:
            self._msg_count -= 50
            self._webview.evaluate_javascript(
                "var c=document.getElementById('chat');for(var i=0;i<50&&c.firstChild;i++)c.removeChild(c.firstChild)",
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
        self._msg_batch.append((msg["user"], msg["text"], msg["color"], emotes))

        if self._batch_flush_id is None:
            self._batch_flush_id = GLib.timeout_add(50, self._flush_messages)

    def _flush_messages(self):
        """Inject all queued messages into the WebView in one IPC call."""
        batch = json.dumps(self._msg_batch)
        self._msg_batch.clear()
        self._batch_flush_id = None
        self._webview.evaluate_javascript(
            f"(function(){{var b={batch};b.forEach(function(m){{chat(m[0],m[1],m[2],m[3])}})}})()",
            -1,
            None,
            None,
            None,
            None,
            None,
        )
        return GLib.SOURCE_REMOVE

    def _on_hidden(self, page):
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
