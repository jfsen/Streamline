import os
import subprocess
import threading
from pathlib import Path
from gi.repository import GLib

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

            # Build streamlink command with flatpak-spawn
            cmd = ['flatpak-spawn', '--host', 'streamlink']

            # Add streamlink options based on content type
            if is_vod:
                # Enable HLS passthrough for proper seeking in VODs
                cmd.append('--player-passthrough=hls')
            else:
                cmd.extend(['--twitch-disable-ads'])
                if self.window.low_latency:
                    cmd.append('--twitch-low-latency')

            # Get quality string from preset or use custom
            quality_presets = {
                "High": "1080p60,1080p,720p60,720p,best",
                "Medium": "720p60,720p,480p,best", 
                "Low": "480p,360p,best",
                "Custom": self.window.custom_quality
            }

            quality = quality_presets.get(self.window.stream_quality, "best")

            # Always ensure 'best' is available as a final fallback
            if not quality.endswith("best") and ",best" not in quality:
                quality += ",best"

            # Set title and final arguments
            cmd.extend([
                '--title', f'Streamline - {url}',
                '--player', player_cmd,
                url,
                quality
            ])

            def start_stream_thread():
                """Handle stream process and output monitoring."""
                try:
                    print(f"DEBUG: Running command: {' '.join(cmd)}")
                    self._current_process = subprocess.Popen(
                        cmd,
                        start_new_session=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )

                    for line in self._current_process.stdout:
                        if "Waiting for pre-roll ads to finish" in line:
                            GLib.idle_add(self.window.show_toast, "Waiting for ads to finish...", 3)
                        elif "Opening stream" in line:
                            GLib.idle_add(self.window.show_toast, "Playback starting...", 2)
                        elif "No playable streams found on this URL" in line:
                            GLib.idle_add(self.window.show_toast, "Stream not available", 3)
                            return False

                    if self._current_process.poll() is not None:
                        print(f"DEBUG: Process ended with return code: {self._current_process.returncode}")
                        return False

                except Exception as e:
                    print(f"DEBUG: Error in stream thread: {str(e)}")
                    return False

                return True

            # Start in background thread
            thread = threading.Thread(
                target=start_stream_thread,
                daemon=True
            )
            thread.start()
            return True

        except Exception as e:
            print(f"DEBUG: Error in play_content: {str(e)}")
            return False

    def _get_player_executable(self):
        """Get path for selected player."""
        if self.window.player_type == "custom":
            return self.window.custom_player_path
        return self._find_executable(self.window.player_type)

    def _find_executable(self, name):
        """Find executable on the host system using flatpak-spawn."""
        if name in self._executable_cache:
            return self._executable_cache[name]

        try:
            result = subprocess.run(
                ['flatpak-spawn', '--host', 'which', name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                self._executable_cache[name] = path
                return path
        except subprocess.SubprocessError:
            pass
            
        return None

    def _show_missing_deps_error(self):
        """Show error dialog for missing dependencies."""
        player = self.window.player_type
        message = (
            f"Could not find {player} on your system.\n"
            "Please install it using your distribution's package manager:\n\n"
            f"• Arch: sudo pacman -S {player}\n"
            f"• Ubuntu/Debian: sudo apt install {player}\n"
            f"• Fedora: sudo dnf install {player}"
        )
        self.window._show_error_dialog("Missing Player", message)