import os
import subprocess
from pathlib import Path

class StreamPlayer:
    def __init__(self, window):
        self.window = window
        self._executable_cache = {}

    def play_stream(self, streamer):
        """Play a stream using streamlink and configured player."""
        try:
            streamlink_cmd, player_cmd = self._get_required_executables()

            cmd = ['flatpak-spawn', '--host'] if os.path.exists('/.flatpak-info') else []
            cmd.extend([
                streamlink_cmd,
                f"twitch.tv/{streamer}",
                self.window.stream_quality,
                '--twitch-disable-ads',
                f'--player={player_cmd}',
                '--player-no-close'  # Keep player open on error
            ])

            # Don't capture output - let streamlink show its own errors
            process = subprocess.Popen(
                cmd,
                start_new_session=True
            )

            # Just wait a moment to catch immediate startup failures
            try:
                process.wait(timeout=1)
                if process.returncode != 0:
                    raise subprocess.SubprocessError("Failed to start streamlink")
            except subprocess.TimeoutExpired:
                # Process is still running - this is good
                self.window.show_toast(f"Starting stream: {streamer}")

        except FileNotFoundError:
            self._show_missing_deps_error()
        except subprocess.SubprocessError as e:
            self.window.show_toast(str(e), 3)

    def _get_required_executables(self):
        """Get paths for streamlink and selected player."""
        # Find streamlink
        streamlink_cmd = self._find_executable('streamlink')
        if not streamlink_cmd:
            raise FileNotFoundError("Could not find streamlink")

        # Get player command based on preferences
        if self.window.player_type == "mpv":
            player_cmd = self._find_executable('mpv')
        elif self.window.player_type == "vlc":
            player_cmd = self._find_executable('vlc')
        else:  # custom
            player_cmd = self.window.custom_player_path

        if not player_cmd:
            raise FileNotFoundError(f"Could not find player: {self.window.player_type}")
            
        return streamlink_cmd, player_cmd

    def _find_executable(self, name):
        """Find executable in various locations."""
        if os.path.exists('/.flatpak-info'):
            try:
                result = subprocess.run(
                    ['flatpak-spawn', '--host', 'which', name],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except subprocess.SubprocessError:
                pass
        
        paths = [
            f"/usr/bin/{name}",
            f"/usr/local/bin/{name}",
            f"/app/bin/{name}",
            f"{str(Path.home())}/.local/bin/{name}"
        ]
        
        for path in paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                return path
                
        return None

    def _show_missing_deps_error(self):
        message = (
            "Could not find streamlink or mpv.\n"
            "Please make sure they are installed on your system:\n\n"
            "For Arch Linux:\n"
            "   sudo pacman -S streamlink mpv\n\n" 
            "For Ubuntu/Debian:\n"
            "   sudo apt install streamlink mpv\n\n"
            "For Fedora:\n"
            "   sudo dnf install streamlink mpv"
        )
        self.window._show_error_dialog("Missing Dependencies", message)