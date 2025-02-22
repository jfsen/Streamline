"""Handles emote rendering and animation"""
from gi.repository import Gtk, GLib, GdkPixbuf, Gdk
from typing import Optional, Set, Dict
from dataclasses import dataclass

@dataclass
class EmoteState:
    """Tracks state of an animated emote"""
    iter: GdkPixbuf.PixbufAnimationIter
    current_texture: Gdk.Texture
    images: Set[Gtk.Picture]
    timer_id: Optional[int] = None

class EmoteRenderer:
    """Handles emote rendering and animations"""
    def __init__(self):
        self.animations: Dict[int, EmoteState] = {}
    
    def create_emote_picture(self, emote: GdkPixbuf.Pixbuf, animate: bool = True) -> Gtk.Picture:
        """Create a new emote picture widget"""
        image = Gtk.Picture()
        image.set_content_fit(Gtk.ContentFit.CONTAIN)  # Maintain aspect ratio
        
        # Calculate width based on aspect ratio, maintaining 28px height
        height = 28
        if isinstance(emote, GdkPixbuf.PixbufAnimation):
            pixbuf = emote.get_static_image()
        else:
            pixbuf = emote
        aspect_ratio = pixbuf.get_width() / pixbuf.get_height()
        width = int(height * aspect_ratio)
        
        image.set_size_request(width, height)

        if isinstance(emote, GdkPixbuf.PixbufAnimation) and animate:
            self._handle_animated_emote(image, emote)
        else:
            self._handle_static_emote(image, emote)

        return image

    def _handle_animated_emote(self, image: Gtk.Picture, emote: GdkPixbuf.PixbufAnimation) -> None:
        """Handle animated emote setup and tracking"""
        emote_id = id(emote)
        
        if emote_id not in self.animations:
            # First time seeing this animation
            anim_iter = emote.get_iter()
            texture = Gdk.Texture.new_for_pixbuf(anim_iter.get_pixbuf())
            
            self.animations[emote_id] = EmoteState(
                iter=anim_iter,
                current_texture=texture,
                images=set(),
                timer_id=GLib.timeout_add(
                    anim_iter.get_delay_time(),
                    self._advance_animation,
                    emote_id
                )
            )

        # Use current frame and add to tracked images
        anim_state = self.animations[emote_id]
        image.set_paintable(anim_state.current_texture)
        anim_state.images.add(image)

    def _handle_static_emote(self, image: Gtk.Picture, emote: GdkPixbuf.Pixbuf) -> None:
        """Handle static emote setup"""
        if isinstance(emote, GdkPixbuf.PixbufAnimation):
            # Get static frame for animated emotes
            static_pixbuf = emote.get_static_image()
            if not hasattr(static_pixbuf, '_texture'):
                static_pixbuf._texture = Gdk.Texture.new_for_pixbuf(static_pixbuf)
            image.set_paintable(static_pixbuf._texture)
        else:
            # Handle normal static emotes
            if not hasattr(emote, '_texture'):
                emote._texture = Gdk.Texture.new_for_pixbuf(emote)
            image.set_paintable(emote._texture)

    def _advance_animation(self, emote_id: int) -> bool:
        """Advance animation frame for all instances of an emote"""
        if emote_id not in self.animations:
            return False
            
        anim_state = self.animations[emote_id]
        
        # Remove destroyed images
        anim_state.images = {img for img in anim_state.images if img.get_parent()}
        
        if not anim_state.images:
            # No more visible instances of this emote
            del self.animations[emote_id]
            return False
            
        # Get next frame
        anim_state.iter.advance(None)
        texture = Gdk.Texture.new_for_pixbuf(anim_state.iter.get_pixbuf())
        anim_state.current_texture = texture
        
        # Update all instances
        for image in anim_state.images:
            image.set_paintable(texture)
        
        # Schedule next frame
        delay = anim_state.iter.get_delay_time()
        anim_state.timer_id = GLib.timeout_add(delay, self._advance_animation, emote_id)
        return False

    def cleanup(self) -> None:
        """Clean up any active animations"""
        for state in self.animations.values():
            if state.timer_id:
                GLib.source_remove(state.timer_id)
        self.animations.clear()