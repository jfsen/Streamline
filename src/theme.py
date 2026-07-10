# theme.py
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

"""Application theme management — system, light, dark, and custom CSS."""

import logging

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk

from .config import THEME_KEYS

logger = logging.getLogger(__name__)

APP_ID = "org.jfsen.Streamline"

# Map custom theme keys (everything except system/light/dark) to
# their bundled CSS resource paths.
THEME_CSS = {
    k: f"/{APP_ID.replace('.', '/')}/css/{k}.css"
    for k in THEME_KEYS
    if k not in ("system", "light", "dark")
}


class ThemeManager:
    """Manages the application colour scheme and custom CSS providers."""

    def __init__(self, window, settings):
        self._window = window
        self._settings = settings
        self._provider = None
        self._style_manager = Adw.StyleManager.get_default()

    def apply(self):
        """Apply the current theme from GSettings."""
        theme = self._settings.get_string("theme")
        logger.debug("Applying theme: %s", theme)

        # Remove previously applied custom theme CSS.
        if self._provider is not None:
            Gtk.StyleContext.remove_provider_for_display(
                self._window.get_display(),
                self._provider,
            )
            self._provider = None

        if theme in THEME_CSS:
            self._style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            self._provider = self._load_css(THEME_CSS[theme])
        elif theme == "light":
            self._style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif theme == "dark":
            self._style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            self._style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

    def _load_css(self, resource_path):
        provider = Gtk.CssProvider()
        provider.load_from_resource(resource_path)
        Gtk.StyleContext.add_provider_for_display(
            self._window.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        return provider
