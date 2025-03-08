import os
import subprocess
import threading
from pathlib import Path

class StreamPlayer:
    def __init__(self, window):
        self.window = window
        self._executable_cache = {}
        self._current_process = None

    def play_content(self, url, is_vod=False):
        """Play a stream or VOD using streamlink to get URL and launch player directly."""
        try:
            # Quick check for player executable (cached)
            player_cmd = self._get_player_executable()
            if not player_cmd:
                self._show_missing_deps_error()
                return False

            # Prepare base player command
            cmd = ['flatpak-spawn', '--host'] if os.path.exists('/.flatpak-info') else []
            cmd.extend([player_cmd])
            
            # Add player-specific title parameter
            title = f'Streamline - {url}'
            if self.window.player_type == "mpv":
                cmd.extend([f'--force-media-title=' + title])
            elif self.window.player_type == "vlc":
                cmd.extend(['--input-title-format=' + title])

            def start_stream_thread():
                try:
                    # Start streamlink process
                    streamlink_proc = self._start_streamlink(url, is_vod)
                    if not streamlink_proc:
                        return

                    # Get stream URL
                    stream_url = streamlink_proc.stdout.readline().strip()
                    print(f"DEBUG: Got stream URL: {stream_url}")
                    if not stream_url:
                        print("DEBUG: No stream URL received")
                        return

                    # Start the player process
                    player_cmd = cmd.copy()
                    player_cmd.append(stream_url)
                    print(f"DEBUG: Running player command: {' '.join(player_cmd)}")
                    
                    self._current_process = subprocess.Popen(
                        player_cmd,
                        start_new_session=True
                    )

                    # Check for immediate failures
                    try:
                        self._current_process.wait(timeout=1)
                        if self._current_process.returncode != 0:
                            print(f"DEBUG: Player failed with return code: {self._current_process.returncode}")
                            raise subprocess.SubprocessError()
                    except subprocess.TimeoutExpired:
                        print("DEBUG: Player started successfully")
                        pass  # Process still running, this is good

                except Exception as e:
                    print(f"DEBUG: Error in stream thread: {str(e)}")
                finally:
                    if 'streamlink_proc' in locals():
                        print("DEBUG: Cleaning up streamlink process")
                        streamlink_proc.terminate()

            # Start everything in background thread
            thread = threading.Thread(
                target=start_stream_thread,
                daemon=True
            )
            thread.start()
            return True

        except Exception as e:
            print(f"DEBUG: Error in play_content: {str(e)}")
            return False

    def _start_streamlink(self, url, is_vod):
        """Start streamlink process and return it."""
        # Always use flatpak-spawn --host to find streamlink on the host system
        try:
            result = subprocess.run(
                ['flatpak-spawn', '--host', 'which', 'streamlink'],
                capture_output=True,
                text=True,
                check=True
            )
            streamlink_cmd = result.stdout.strip()
        except subprocess.SubprocessError:
            streamlink_cmd = None

        print(f"DEBUG: Using streamlink at: {streamlink_cmd}")
        if not streamlink_cmd:
            raise FileNotFoundError("Could not find streamlink")

        cmd = ['flatpak-spawn', '--host']
        cmd.extend([
            streamlink_cmd,
            '--stream-url',
            url,
            self.window.stream_quality
        ])

        if not is_vod:
            cmd.extend(['--twitch-disable-ads'])
            if self.window.low_latency:
                cmd.append('--twitch-low-latency')

        print(f"DEBUG: Running streamlink command: {' '.join(cmd)}")
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            # Add error output reading
            error = process.stderr.readline().strip()
            if error:
                print(f"DEBUG: Streamlink error: {error}")
            return process
        except subprocess.SubprocessError as e:
            print(f"DEBUG: Subprocess error: {str(e)}")
            return None

    def _get_player_executable(self):
        """Get path for selected player."""
        if self.window.player_type == "custom":
            return self.window.custom_player_path
        return self._find_executable(self.window.player_type)

    def _find_executable(self, name):
        """Find executable in various locations."""
        if name in self._executable_cache:
            return self._executable_cache[name]

        # Inside Flatpak, check app paths first
        if os.path.exists('/.flatpak-info'):
            flatpak_paths = [
                f"/app/bin/{name}",
                f"/app/local/bin/{name}"
            ]
            for path in flatpak_paths:
                if os.path.exists(path) and os.access(path, os.X_OK):
                    self._executable_cache[name] = path
                    return path

            # If not found in Flatpak paths, try host system
            try:
                result = subprocess.run(
                    ['flatpak-spawn', '--host', 'which', name],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    self._executable_cache[name] = result.stdout.strip()
                    return self._executable_cache[name]
            except subprocess.SubprocessError:
                pass
        
        # Standard system paths
        paths = [
            f"/usr/bin/{name}",
            f"/usr/local/bin/{name}",
            f"{str(Path.home())}/.local/bin/{name}"
        ]
        
        for path in paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                self._executable_cache[name] = path
                return path
                
        return None

    def _show_missing_deps_error(self):
        player = self.window.player_type
        if player == "custom":
            message = (
                "Could not find the custom media player specified in preferences.\n"
                "Please make sure the path is correct and the player is installed."
            )
        else:
            message = (
                f"Could not find {player} or streamlink.\n"
                "Please make sure they are installed on your system:\n\n"
                "For Arch Linux:\n"
                f"   sudo pacman -S {player} streamlink\n\n"
                "For Ubuntu/Debian:\n"
                f"   sudo apt install {player} streamlink\n\n"
                "For Fedora:\n"
                f"   sudo dnf install {player} streamlink"
            )
        self.window._show_error_dialog("Missing Dependencies", message)