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
    timer_id: Optional[int] = None

class EmoteRenderer:
    """Handles emote rendering and animations"""
    def __init__(self):
        self.animations: Dict[int, EmoteState] = {}
        self.texture_cache: Dict[int, Gdk.Texture] = {}
    
    def create_emote_picture(self, emote: GdkPixbuf.Pixbuf, animate: bool = True) -> Gtk.Picture:
        """Create a new emote picture widget"""
        image = Gtk.Picture()
        image.set_size_request(1, 1)
        image.set_can_shrink(False)
        image.set_content_fit(Gtk.ContentFit.CONTAIN)

        if isinstance(emote, GdkPixbuf.PixbufAnimation) and animate:
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
                timer_id=GLib.timeout_add(
                    anim_iter.get_delay_time(),
                    self._advance_animation,
                    emote_id
                )
            )

        anim_state = self.animations[emote_id]
        image.set_paintable(anim_state.current_texture)
        anim_state.images.add(image)

    def _handle_static_emote(self, image: Gtk.Picture, emote: GdkPixbuf.Pixbuf) -> None:
        """Handle static emote setup"""
        emote_id = id(emote)
        if emote_id not in self.texture_cache:
            self.texture_cache[emote_id] = Gdk.Texture.new_for_pixbuf(emote)
        image.set_paintable(self.texture_cache[emote_id])

    def _advance_animation(self, emote_id: int) -> bool:
        """Advance animation frame for all instances of an emote"""
        if emote_id not in self.animations:
            return False
            
        anim_state = self.animations[emote_id]
        anim_state.images = {img for img in anim_state.images if img.get_parent()}
        
        if not anim_state.images:
            del self.animations[emote_id]
            return False
            
        # Get next frame
        anim_state.iter.advance(None)
        frame_hash = hash(anim_state.iter.get_pixbuf().get_pixels())
        
        # Reuse texture if we've seen this frame before
        if frame_hash not in anim_state.frame_textures:
            anim_state.frame_textures[frame_hash] = Gdk.Texture.new_for_pixbuf(
                anim_state.iter.get_pixbuf()
            )
        
        anim_state.current_texture = anim_state.frame_textures[frame_hash]
        
        for image in anim_state.images:
            image.set_paintable(anim_state.current_texture)
        
        delay = anim_state.iter.get_delay_time()
        anim_state.timer_id = GLib.timeout_add(delay, self._advance_animation, emote_id)
        return False

    def cleanup(self) -> None:
        """Clean up any active animations"""
        for state in self.animations.values():
            if state.timer_id:
                GLib.source_remove(state.timer_id)
        self.animations.clear()