"""Chess piece sprites drawn with Unicode quadrant blocks (U+2580-U+259F).

Each terminal cell is split into 2x2 "pixels", so a square of W x H cells is a (2W) x (2H) bitmap.
Block elements are drawn by the terminal itself (Windows Terminal, most Linux/macOS terminals), so the
pieces are exactly centred and take the colour we give them, unlike the chess glyphs of a fallback font.
"""
from __future__ import annotations

import chess

# bitmaps: width 2*cols, height 2*rows; '#' = piece
SPRITES: dict[tuple[int, int], dict[int, list[str]]] = {
    (6, 3): {
        chess.PAWN: ["............",
                     ".....##.....",
                     "....####....",
                     ".....##.....",
                     "....####....",
                     "...######..."],
        chess.KNIGHT: ["...######...",
                       "..##.#####..",
                       ".#########..",
                       ".###..####..",
                       "...#######..",
                       "..#########."],
        chess.BISHOP: [".....##.....",
                       "....#.##....",
                       "....####....",
                       ".....##.....",
                       "....####....",
                       "...######..."],
        chess.ROOK: ["...#.##.#...",
                     "...######...",
                     "....####....",
                     "....####....",
                     "....####....",
                     "...######..."],
        chess.QUEEN: ["..#.#..#.#..",
                      "..########..",
                      "...######...",
                      "....####....",
                      "...######...",
                      "..########.."],
        chess.KING: [".....##.....",
                     "....####....",
                     ".....##.....",
                     "..########..",
                     "....####....",
                     "...######..."],
    },
    (8, 4): {
        chess.PAWN: ["................",
                     ".......##.......",
                     "......####......",
                     "......####......",
                     ".......##.......",
                     "......####......",
                     ".....######.....",
                     "....########...."],
        chess.KNIGHT: ["......#.#.......",
                       ".....######.....",
                       "....##.#####....",
                       "...#########....",
                       "...###..####....",
                       ".......#####....",
                       ".....#######....",
                       "....#########..."],
        chess.BISHOP: [".......##.......",
                       "......#####.....",
                       ".....##.####....",
                       ".....#.#####....",
                       "......#####.....",
                       ".......###......",
                       ".....#######....",
                       "....#########..."],
        chess.ROOK: ["....##.##.##....",
                     "....########....",
                     ".....######.....",
                     ".....######.....",
                     ".....######.....",
                     ".....######.....",
                     "....########....",
                     "...##########..."],
        chess.QUEEN: ["..#...#..#...#..",
                      "..##.##..##.##..",
                      "...##########...",
                      "....########....",
                      ".....######.....",
                      ".....######.....",
                      "....########....",
                      "...##########..."],
        chess.KING: [".......##.......",
                     "......####......",
                     ".......##.......",
                     "...##########...",
                     "....########....",
                     ".....######.....",
                     "....########....",
                     "...##########..."],
    },
}

_QUAD = {(0, 0, 0, 0): " ", (1, 0, 0, 0): "▘", (0, 1, 0, 0): "▝", (0, 0, 1, 0): "▖", (0, 0, 0, 1): "▗",
         (1, 1, 0, 0): "▀", (0, 0, 1, 1): "▄", (1, 0, 1, 0): "▌", (0, 1, 0, 1): "▐", (1, 0, 0, 1): "▚",
         (0, 1, 1, 0): "▞", (1, 1, 1, 0): "▛", (1, 1, 0, 1): "▜", (1, 0, 1, 1): "▙", (0, 1, 1, 1): "▟",
         (1, 1, 1, 1): "█"}

_CACHE: dict[tuple[int, int, int], list[str]] = {}


def cells(piece_type: int, cols: int, rows: int) -> list[str] | None:
    """Rows of quadrant characters for a piece in a cols x rows square, or None if no sprite size fits."""
    key = (piece_type, cols, rows)
    if key in _CACHE:
        return _CACHE[key]
    bitmap = SPRITES.get((cols, rows), {}).get(piece_type)
    if bitmap is None:
        return None
    out = []
    for r in range(rows):
        line = []
        for c in range(cols):
            bits = tuple(int(bitmap[2 * r + dy][2 * c + dx] == "#") for dy in (0, 1) for dx in (0, 1))
            line.append(_QUAD[bits])
        out.append("".join(line))
    _CACHE[key] = out
    return out
