import gettext
import logging
import shutil
import subprocess
import threading

from gi.repository import GLib

from .config import IS_FLATPAK, QUALITY_PRESETS, STREAMLINK_CMD

_ = gettext.gettext

logger = logging.getLogger("StreamPlayer")


class StreamPlayer:
    def __init__(self, window):
        self.window = window
        self._executable_cache = {}
        self._current_process = None

    def play_content(self, url, is_vod=False):
        """Play a stream or VOD using streamlink's built-in player launching."""
        try:
            # Quick check for player executable (cached)
            player_cmd = self._get_player_executable()
            if not player_cmd:
                self._show_missing_deps_error()
                return False

            # Show initial toast message
            self.window.show_toast(_("Connecting..."), 1)

            # Build streamlink command
            cmd = list(STREAMLINK_CMD)

            # Add streamlink options based on content type
            if is_vod:
                # Enable HLS passthrough for proper seeking in VODs
                cmd.append("--player-passthrough=hls")
            else:
                if self.window.low_latency:
                    cmd.append("--twitch-low-latency")

            # Get quality string from preset or use custom
            if self.window.stream_quality == "Custom":
                quality = self.window.custom_quality
            else:
                quality = QUALITY_PRESETS.get(self.window.stream_quality, "best")
                # Always ensure 'best' is available as a final fallback
                if not quality.endswith("best") and ",best" not in quality:
                    quality += ",best"

            # Set title and final arguments
            cmd.extend(
                ["--title", f"Streamline - {url}", "--player", player_cmd, url, quality]
            )

            def start_stream_thread():
                """Handle stream process and output monitoring."""
                try:
                    logger.debug("Running: %s", " ".join(cmd))
                    self._current_process = subprocess.Popen(
                        cmd,
                        start_new_session=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                    )

                    stream_not_available = False

                    for line in self._current_process.stdout:
                        logger.debug("Streamlink: %s", line.strip())

                        if "No playable streams found on this URL" in line:
                            stream_not_available = True
                            GLib.idle_add(
                                self.window.show_toast, _("Stream not available"), 3
                            )
                        elif "Waiting for pre-roll ads to finish" in line:
                            GLib.idle_add(
                                self.window.show_toast,
                                _("Waiting for ads to finish..."),
                                3,
                            )

                    if self._current_process.poll() is not None:
                        logger.debug(
                            "Process ended (code %s)", self._current_process.returncode
                        )
                        if (
                            self._current_process.returncode != 0
                            and not stream_not_available
                        ):
                            GLib.idle_add(
                                self.window.show_toast, _("Stream playback failed"), 3
                            )

                except Exception as e:
                    logger.debug("Error in stream thread: %s", str(e))

            # Start in background thread
            thread = threading.Thread(target=start_stream_thread, daemon=True)
            thread.start()
            return True

        except Exception as e:
            logger.debug("Error in play_content: %s", str(e))
            self.window.show_toast(_("Error starting playback"), 3)
            return False

    def _get_player_executable(self):
        """Get path for selected player, validating it exists on the host."""
        if self.window.player_type == "custom":
            path = self.window.custom_player_path
            if not path:
                return None
            return self._find_executable(path)
        return self._find_executable(self.window.player_type)

    def _find_executable(self, name):
        """Find executable on the host system."""
        if name in self._executable_cache:
            return self._executable_cache[name]

        if IS_FLATPAK:
            return self._find_via_flatpak_spawn(name)
        return self._find_via_which(name)

    def _find_via_flatpak_spawn(self, name):
        try:
            result = subprocess.run(
                ["flatpak-spawn", "--host", "which", name],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                self._executable_cache[name] = path
                return path
            else:
                self._executable_cache.pop(name, None)
        except subprocess.SubprocessError:
            self._executable_cache.pop(name, None)

        return None

    def _find_via_which(self, name):
        path = shutil.which(name)
        if path:
            self._executable_cache[name] = path
            return path
        self._executable_cache.pop(name, None)
        return None

    def _show_missing_deps_error(self):
        """Show error dialog for missing dependencies."""
        player = self.window.player_type
        if player == "custom":
            path = self.window.custom_player_path
            if path:
                message = _(
                    "Could not find {path} on your system.\n"
                    "Check the path in Preferences → Player → Custom player executable."
                ).format(path=path)
            else:
                message = _(
                    "No custom player executable configured.\n"
                    "Set the path to your player in Preferences → Player → Custom player executable."
                )
        else:
            message = _(
                "Could not find {player} on your system.\n"
                "Please install it using your distribution's package manager:\n\n"
                "• Arch: sudo pacman -S {player}\n"
                "• Ubuntu/Debian: sudo apt install {player}\n"
                "• Fedora: sudo dnf install {player}"
            ).format(player=player)
        self.window._show_error_dialog(_("Missing Player"), message)
