from pathlib import Path
import requests
from gi.repository import GdkPixbuf, Gio, GLib, Gdk
from typing import Dict, Optional, Union

class EmoteStore:
    def __init__(self):
        self.cache_dir = Path.home() / ".cache" / "Streamline"
        self.emotes_dir = self.cache_dir / "emotes"
        self.emotes_dir.mkdir(parents=True, exist_ok=True)
        self.pixbufs: Dict[str, Union[GdkPixbuf.Pixbuf, GdkPixbuf.PixbufAnimation]] = {}

    def get_emote(self, name: str, url: Optional[str] = None) -> Optional[Union[GdkPixbuf.Pixbuf, GdkPixbuf.PixbufAnimation]]:
        """Get or download and load an emote"""
        if name in self.pixbufs:
            return self.pixbufs[name]
            
        # Try to load from cache first
        pixbuf = self._load_cached_emote(name)
        if pixbuf:
            self.pixbufs[name] = pixbuf
            return pixbuf
            
        # Download if URL provided and not in cache
        if url:
            pixbuf = self._download_and_load_emote(name, url)
            if pixbuf:
                self.pixbufs[name] = pixbuf
            return pixbuf
            
        return None

    def _download_and_load_emote(self, name: str, url: str) -> Optional[Union[GdkPixbuf.Pixbuf, GdkPixbuf.PixbufAnimation]]:
        """Download and load an emote from URL"""
        try:
            print(f"[DEBUG] Downloading emote: {name} from {url}")
            response = requests.get(url)
            if response.status_code != 200:
                print(f"[DEBUG] Failed to download emote {name}: HTTP {response.status_code}")
                return None

            # Get file extension from content-type
            content_type = response.headers.get('content-type', '')
            if 'gif' in content_type:
                ext = '.gif'
            elif 'png' in content_type:
                ext = '.png'
            else:
                ext = '.webp'  # default to webp
            
            path = self.emotes_dir / f"{name}{ext}"
            path.write_bytes(response.content)
            print(f"[DEBUG] Successfully saved emote to {path}")
            
            return self._load_cached_emote(name)
            
        except Exception as e:
            print(f"[DEBUG] Error downloading emote {name}: {e}")
            return None

    def _load_cached_emote(self, name: str) -> Optional[Union[GdkPixbuf.Pixbuf, GdkPixbuf.PixbufAnimation]]:
        """Load an emote from the cache"""
        try:
            # Check for existing file with any supported extension
            for ext in ['.gif', '.png', '.webp']:
                path = self.emotes_dir / f"{name}{ext}"
                if path.exists():
                    print(f"[DEBUG] Loading emote from {path}")
                    if ext == '.gif':
                        return GdkPixbuf.PixbufAnimation.new_from_file(str(path))
                    else:
                        return GdkPixbuf.Pixbuf.new_from_file(str(path))
                        
        except Exception as e:
            print(f"[DEBUG] Error loading cached emote {name}: {e}")
            
        return None

    def clear(self):
        """Clear cached pixbufs"""
        self.pixbufs.clear()