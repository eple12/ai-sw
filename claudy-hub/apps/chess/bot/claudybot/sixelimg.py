"""Flicker-free Sixel pictures for Textual.

textual-image's Sixel widget paints its whole area with blank cells and then sends the Sixel data. Terminals
erase picture pixels under any text they write, so every repaint shows an empty frame first (the flicker when a
move is made). Our board pictures are opaque and cover the widget area exactly, so the blank cells are not
needed: the new picture can be drawn straight over the old one.
"""
from __future__ import annotations

from rich.segment import ControlType, Segment
from textual.geometry import Region
from textual.strip import Strip

_PLAIN = None      # no SGR codes around our cursor-control segments


def image_widget(**kw):
    """Best image widget for this terminal (call only after textual_image has probed the terminal)."""
    import os
    from textual_image.widget import Image as AutoImage
    from textual_image.widget import SixelImage
    if AutoImage is SixelImage or os.environ.get("CLAUDYBOT_FORCE_SIXEL") == "1":
        return _steady_class()(**kw)
    return AutoImage(**kw)


_STEADY = None


def _steady_class():
    global _STEADY
    if _STEADY is None:
        _STEADY = _classes()
    return _STEADY


def _classes():
    from textual_image.widget.sixel import Image as SixelImage
    from textual_image.widget.sixel import _ImageSixelImpl, _NoopRenderable

    class _SteadyImpl(_ImageSixelImpl):
        def render_lines(self, crop: Region) -> list[Strip]:
            lines = super().render_lines(crop)
            if not lines:
                return lines
            # Textual writes the strips of a screen line back to back after a single cursor move, so every
            # strip must advance the cursor by exactly its width. Instead of printing blanks (which erase the
            # picture) we move the cursor forward. On the last line the Sixel data is sent with the cursor
            # saved / restored around it, so the next widget on that line starts in the right place.
            # All escape sequences are "control" segments (zero cell length for Rich), so when Textual crops or
            # divides a line for a partial update they are kept whole or dropped, never split mid-sequence.
            w = crop.width
            skip = Segment(f"\x1b[{w}C", _PLAIN, ((ControlType.CURSOR_FORWARD, w),))
            _clear, to_origin, sixel, _to_end = list(lines[-1])
            to_origin = Segment(to_origin.text, _PLAIN, ((ControlType.HOME,),))
            save = Segment("\x1b7", _PLAIN, ((ControlType.HOME,),))
            restore = Segment("\x1b8", _PLAIN, ((ControlType.HOME,),))
            body = [Strip([skip], w) for _ in range(len(lines) - 1)]
            return body + [Strip([skip, save, to_origin, sixel, restore], w)]

    class SteadySixelImage(SixelImage, Renderable=_NoopRenderable):
        def compose(self):
            yield _SteadyImpl(self.image, self._sixel_options)

    return SteadySixelImage
