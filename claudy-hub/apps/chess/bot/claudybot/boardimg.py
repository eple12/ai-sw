"""Board pictures (PIL) with a real piece set, shown through terminal graphics (Sixel) by textual-image."""
from __future__ import annotations

import io
from collections import OrderedDict
from pathlib import Path

import chess
from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"

LIGHT = (240, 217, 181)        # lichess "brown" board
DARK = (181, 136, 99)
LIGHT_HL = (205, 210, 106)     # last move
DARK_HL = (170, 162, 58)

try:
    import resvg_py            # crisp rendering straight from the SVG files when available
except ImportError:            # pragma: no cover
    resvg_py = None


class PieceSet:
    def __init__(self, name: str):
        self.name = name
        self.dir = ASSETS / name
        self._cache: dict[tuple[str, int], Image.Image] = {}

    @property
    def available(self) -> bool:
        return (self.dir / "wK.png").exists() or (self.dir / "wK.svg").exists()

    def get(self, piece: chess.Piece, size: int) -> Image.Image:
        code = ("w" if piece.color == chess.WHITE else "b") + piece.symbol().upper()
        key = (code, size)
        img = self._cache.get(key)
        if img is None:
            svg = self.dir / f"{code}.svg"
            if resvg_py is not None and svg.exists():
                data = resvg_py.svg_to_bytes(svg_string=svg.read_text(encoding="utf-8"), width=size, height=size)
                img = Image.open(io.BytesIO(bytes(data))).convert("RGBA")
            else:
                img = Image.open(self.dir / f"{code}.png").convert("RGBA").resize((size, size), Image.LANCZOS)
            self._cache[key] = img
        return img


_FONTS: dict[int, ImageFont.ImageFont] = {}


def _font(px: int) -> ImageFont.ImageFont:
    if px not in _FONTS:
        for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
            try:
                _FONTS[px] = ImageFont.truetype(name, px)
                break
            except OSError:
                continue
        else:
            _FONTS[px] = ImageFont.load_default()
    return _FONTS[px]


class BoardPainter:
    def __init__(self, piece_set: str = "caliente"):
        self.pieces = PieceSet(piece_set)
        self._cache: OrderedDict[tuple, Image.Image] = OrderedDict()

    def render(self, board: chess.Board, white_bottom: bool, px: int, coords: bool = True) -> Image.Image:
        """Square board image of about px x px pixels (a multiple of 8)."""
        last = board.move_stack[-1] if board.move_stack else None
        key = (board.board_fen(), board.turn, last, white_bottom, px, coords)
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
            return img
        sq = max(8, px // 8)
        img = Image.new("RGB", (8 * sq, 8 * sq), LIGHT)
        d = ImageDraw.Draw(img)
        hl = {last.from_square, last.to_square} if last else set()
        check = board.king(board.turn) if board.is_check() else None
        font = _font(max(8, sq // 5))
        for r in range(8):
            for f in range(8):
                s = chess.square(f, r)
                x = (f if white_bottom else 7 - f) * sq
                y = (7 - r if white_bottom else r) * sq
                light = (r + f) % 2 == 1
                col = (LIGHT_HL if light else DARK_HL) if s in hl else (LIGHT if light else DARK)
                d.rectangle([x, y, x + sq - 1, y + sq - 1], fill=col)
                if s == check:                       # red glow like lichess
                    for i in range(sq // 2, 0, -1):
                        a = 1 - i / (sq / 2)
                        c = tuple(int(col[k] * (1 - a) + (255, 0, 0)[k] * a) for k in range(3))
                        d.ellipse([x + sq / 2 - i, y + sq / 2 - i, x + sq / 2 + i, y + sq / 2 + i], fill=c)
                if coords:
                    other = DARK if light else LIGHT
                    if (r == 0 and white_bottom) or (r == 7 and not white_bottom):
                        d.text((x + sq - sq // 5, y + sq - sq // 4), "abcdefgh"[f], font=font, fill=other)
                    if (f == 0 and white_bottom) or (f == 7 and not white_bottom):
                        d.text((x + 2, y + 1), str(r + 1), font=font, fill=other)
                p = board.piece_at(s)
                if p:
                    pic = self.pieces.get(p, sq)
                    img.paste(pic, (x, y), pic)
        self._cache[key] = img
        if len(self._cache) > 64:
            self._cache.popitem(last=False)
        return img
