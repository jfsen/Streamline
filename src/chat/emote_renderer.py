"""Handles emote rendering and animation"""
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk
from typing import Optional, Set, Dict
from dataclasses import dataclass

@dataclass
class EmoteState:
    """Tracks state of an animated emote"""
    iter: GdkPixbuf.PixbufAnimationIter
    current_texture: Gdk.Texture
    frame_textures: Dict[int, Gdk.Texture]  # Cache textures per frame
    images: Set[Gtk.Picture]
    next_frame_time: int  # When to show next frame

class EmoteRenderer:
    """Handles emote rendering and animations"""
    def __init__(self):
        self.animations: Dict[int, EmoteState] = {}
        self.texture_cache: Dict[int, Gdk.Texture] = {}
        self.global_timer_id: Optional[int] = None
        self.frame_time = 50  # 20fps, adjust based on performance needs
        
    def start_animations(self):
        """Start global animation timer"""
        if not self.global_timer_id:
            self.global_timer_id = GLib.timeout_add(
                self.frame_time, 
                self._update_all_animations
            )
    
    def _update_all_animations(self) -> bool:
        """Update all visible animated emotes in one pass"""
        current_time = GLib.get_monotonic_time() // 1000  # ms
        
        for emote_id, state in list(self.animations.items()):
            # Remove destroyed images
            state.images = {img for img in state.images if img.get_parent()}
            
            if not state.images:
                del self.animations[emote_id]
                continue
                
            # Check if it's time to advance this animation
            if current_time >= state.next_frame_time:
                state.iter.advance(None)
                frame_hash = hash(state.iter.get_pixbuf().get_pixels())
                
                if frame_hash not in state.frame_textures:
                    state.frame_textures[frame_hash] = Gdk.Texture.new_for_pixbuf(
                        state.iter.get_pixbuf()
                    )
                
                state.current_texture = state.frame_textures[frame_hash]
                state.next_frame_time = current_time + state.iter.get_delay_time()
                
                for image in state.images:
                    image.set_paintable(state.current_texture)
        
        return bool(self.animations)  # Stop timer if no animations

    def create_emote_picture(self, emote: GdkPixbuf.Pixbuf, animate: bool = True) -> Gtk.Picture:
        """Create a new emote picture widget"""
        image = Gtk.Picture()
        image.set_size_request(1, 1)
        image.set_can_shrink(False)
        image.set_content_fit(Gtk.ContentFit.CONTAIN)

        # If it's an animated emote but animations are disabled,
        # get the static image instead
        if isinstance(emote, GdkPixbuf.PixbufAnimation) and not animate:
            emote = emote.get_static_image()
            self._handle_static_emote(image, emote)
        elif isinstance(emote, GdkPixbuf.PixbufAnimation) and animate:
            self._handle_animated_emote(image, emote)
        else:
            self._handle_static_emote(image, emote)

        return image

    def _handle_animated_emote(self, image: Gtk.Picture, emote: GdkPixbuf.PixbufAnimation) -> None:
        """Handle animated emote setup and tracking"""
        emote_id = id(emote)
        
        if emote_id not in self.animations:
            anim_iter = emote.get_iter()
            first_frame = anim_iter.get_pixbuf()
            frame_textures = {0: Gdk.Texture.new_for_pixbuf(first_frame)}
            
            self.animations[emote_id] = EmoteState(
                iter=anim_iter,
                current_texture=frame_textures[0],
                frame_textures=frame_textures,
                images=set(),
                next_frame_time=GLib.get_monotonic_time() // 1000 + anim_iter.get_delay_time()
            )
            
            # Start global timer if needed
            self.start_animations()

        anim_state = self.animations[emote_id]
        image.set_paintable(anim_state.current_texture)
        anim_state.images.add(image)

    def _handle_static_emote(self, image: Gtk.Picture, emote: GdkPixbuf.Pixbuf) -> None:
        """Handle static emote setup"""
        emote_id = id(emote)
        if emote_id not in self.texture_cache:
            self.texture_cache[emote_id] = Gdk.Texture.new_for_pixbuf(emote)
        image.set_paintable(self.texture_cache[emote_id])

    def cleanup(self) -> None:
        """Clean up any active animations"""
        for state in self.animations.values():
            if state.timer_id:
                GLib.source_remove(state.timer_id)
        self.animations.clear()