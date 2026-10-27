"""Rich renderables for the dashboard: boards, eval chart, search table, move list."""
from __future__ import annotations

import time

import chess
from rich.table import Table
from rich.text import Text
from rich import box

from textual.markup import escape as escape_markup

from . import sprites
from .game import MATE_CP, GameSession, fmt_ms, fmt_score, score_cp, win_percent

LIGHT = "#a9b8c3"
DARK = "#768c9b"
LIGHT_HL = "#c9c77a"
DARK_HL = "#a6a24f"
CHECK = "#d9534f"
WHITE_PIECE = "bold #ffffff"
BLACK_PIECE = "bold #111111"
SPRITE_WHITE = "#f4f4f4"
SPRITE_BLACK = "#141414"
CHIP_WHITE = "#f0f0f0"
CHIP_BLACK = "#1c1c1c"
CHIP_WHITE_HL = "#f3e98a"      # piece that just moved
CHIP_BLACK_HL = "#4a4418"
GLYPH = {chess.PAWN: "♟\ufe0e", chess.KNIGHT: "♞", chess.BISHOP: "♝", chess.ROOK: "♜", chess.QUEEN: "♛",
         chess.KING: "♚"}
LETTER = {chess.PAWN: "P", chess.KNIGHT: "N", chess.BISHOP: "B", chess.ROOK: "R", chess.QUEEN: "Q", chess.KING: "K"}


def human(n: float) -> str:
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return f"{int(n)}"


def square_size(style: str, width: int, height: int) -> tuple[int, int]:
    """Largest square (cols, rows) for a board of `style` that fits width x height cells (incl. coords)."""
    if style == "sprites":
        for cw, ch in ((8, 4), (6, 3)):
            if 8 * cw + 2 <= width and 8 * ch + 1 <= height:
                return cw, ch
    if style in ("sprites", "letters") and 8 * 5 + 2 <= width and 8 * 3 + 1 <= height:
        return 5, 3
    return 3, 1


def _piece_cell(p: chess.Piece, style: str, cw: int, ch: int, line: int, bg: str, last_to: bool,
                checked: bool) -> tuple[str | Text, str]:
    """Text and style of one row of a square holding piece p."""
    white = p.color == chess.WHITE
    if style == "sprites":
        rows = sprites.cells(p.piece_type, cw, ch)
        if rows is not None:
            return rows[line], f"{SPRITE_WHITE if white else SPRITE_BLACK} on {bg}"
    if style in ("sprites", "letters"):
        mid = ch // 2
        if line != mid:
            return " " * cw, f"on {bg}"
        chip = (CHIP_WHITE_HL if white else CHIP_BLACK_HL) if last_to else (CHIP_WHITE if white else CHIP_BLACK)
        if checked:
            chip = CHECK
        text = Text()
        pad = (cw - 3) // 2
        text.append(" " * pad, style=f"on {bg}")
        text.append("▐", style=f"{chip} on {bg}")
        text.append(LETTER[p.piece_type], style=f"bold {'#101010' if white else '#f4f4f4'} on {chip}")
        text.append("▌", style=f"{chip} on {bg}")
        text.append(" " * (cw - 3 - pad), style=f"on {bg}")
        return text, ""
    # unicode glyphs (font dependent)
    if line != ch // 2:
        return " " * cw, f"on {bg}"
    left = (cw - 1) // 2
    return (" " * left + GLYPH[p.piece_type] + " " * (cw - 1 - left),
            f"{WHITE_PIECE if white else BLACK_PIECE} on {bg}")


def board_text(board: chess.Board, *, white_bottom: bool = True, cell_w: int = 3, cell_h: int = 1,
               coords: bool = True, style: str = "sprites") -> Text:
    last = board.move_stack[-1] if board.move_stack else None
    hl = {last.from_square, last.to_square} if last else set()
    king_in_check = board.king(board.turn) if board.is_check() else None
    ranks = range(7, -1, -1) if white_bottom else range(8)
    files = range(8) if white_bottom else range(7, -1, -1)
    out = Text()
    for r in ranks:
        for line in range(cell_h):
            if coords:
                out.append(f"{r + 1} " if line == cell_h // 2 else "  ", style="dim")
            for f in files:
                sq = chess.square(f, r)
                light = (r + f) % 2 == 1
                bg = (LIGHT_HL if light else DARK_HL) if sq in hl else (LIGHT if light else DARK)
                if sq == king_in_check:
                    bg = CHECK
                p = board.piece_at(sq)
                if p:
                    text, st = _piece_cell(p, style, cell_w, cell_h, line, bg,
                                           last is not None and sq == last.to_square, sq == king_in_check)
                    if isinstance(text, Text):
                        out.append_text(text)
                    else:
                        out.append(text, style=st)
                else:
                    out.append(" " * cell_w, style=f"on {bg}")
            out.append("\n")
    if coords:
        out.append("  ")
        for f in files:
            left = (cell_w - 1) // 2
            out.append(" " * left + "abcdefgh"[f] + " " * (cell_w - 1 - left), style="dim")
        out.append("\n")
    return out


def clock_style(g: GameSession, color: bool) -> str:
    ms = g.clock(color)
    running = g.status == "started" and g.board.turn == color
    if ms is not None and ms < 10000 and running:
        return "bold white on red"
    return "bold black on #e0e0e0" if running else "bold"


def player_line(g: GameSession, color: bool, width: int) -> Text:
    p = g.white if color == chess.WHITE else g.black
    to_move = g.status == "started" and g.board.turn == color
    t = Text()
    t.append("● " if to_move else "○ ", style="green" if to_move else "dim")
    name = g.player_label(p)
    clock = f" {fmt_ms(g.clock(color))} "
    room = max(4, width - 2 - len(clock))
    if len(name) > room:
        name = name[:room - 1] + "…"
    style = "bold cyan" if color == g.color else "bold"
    t.append(name.ljust(room), style=style)
    t.append(clock, style=clock_style(g, color))
    return t


def player_markup(g: GameSession, color: bool) -> str:
    """Player name + clock as Textual markup (used as a border title)."""
    p = g.white if color == chess.WHITE else g.black
    to_move = g.status == "started" and g.board.turn == color
    ms = g.clock(color)
    dot = "[green]●[/]" if to_move else "[dim]○[/]"
    name = escape_markup(g.player_label(p))
    name = f"[bold cyan]{name}[/]" if color == g.color else f"[bold]{name}[/]"
    if to_move and ms is not None and ms < 10000:
        clk = f"[bold white on red] {fmt_ms(ms)} [/]"
    elif to_move:
        clk = f"[bold black on #e0e0e0] {fmt_ms(ms)} [/]"
    else:
        clk = f"[bold] {fmt_ms(ms)} [/]"
    return f"{dot}{clk}{name}"   # clock first: a narrow panel truncates the name, not the clock


def eval_bar(white_cp: int | None, width: int) -> Text:
    t = Text()
    if white_cp is None:
        return t.append("─" * width, style="dim")
    wp = win_percent(max(-2000, min(2000, white_cp)))
    filled = round(wp / 100 * width)
    t.append("█" * filled, style="#f0f0f0")
    t.append("█" * (width - filled), style="#303030")
    return t


def search_state(g: GameSession) -> Text:
    t = Text()
    if g.over:
        oc = g.outcome_for_me()
        style = {"win": "bold green", "loss": "bold red", "draw": "bold yellow"}.get(oc, "dim")
        return t.append(f"{g.result()} {oc} ({g.status})", style=style)
    if g.search_kind == "think":
        t.append("THINKING ", style="bold green")
        t.append(f"{time.monotonic() - g.search_started:.1f}s")
    elif g.search_kind == "ponder":
        t.append("PONDERING ", style="bold magenta")
        mv = g.ponder_move or ""
        if g.ponder_move and g.ponder_base == g.moves:
            try:
                mv = g.board.san(chess.Move.from_uci(g.ponder_move))
            except (ValueError, AssertionError):
                pass
        t.append(mv)
    elif g.status == "started":
        t.append("waiting for opponent" if not g.my_turn else "to move", style="dim")
    else:
        t.append(g.status, style="dim")
    return t


def card_top(g: GameSession, width: int) -> Text:
    return player_line(g, not g.color, width)


def card_bottom(g: GameSession, width: int) -> Text:
    t = player_line(g, g.color, width)
    t.append("\n")
    ev = g.current_eval_white()
    live = g.live
    t.append(f"{fmt_score(ev):>6} ", style="bold")
    t.append(f"d{live.get('depth', g.evals[-1].depth if g.evals else 0)} ")
    nps = live.get("nps") or (g.evals[-1].nps if g.evals else 0)
    t.append(f"{human(nps)}nps ", style="dim")
    t.append(f"{g.tc}", style="dim")
    t.append("\n")
    t.append_text(search_state(g))
    return t


def card(g: GameSession, idx: int, style: str, width: int = 26, cw: int = 3, ch: int = 1) -> Text:
    t = Text()
    top, bottom = (not g.color, g.color)
    t.append_text(player_line(g, top, width))
    t.append("\n")
    t.append_text(board_text(g.board, white_bottom=g.color == chess.WHITE, cell_w=cw, cell_h=ch, coords=False,
                             style=style if (cw, ch) != (3, 1) or style == "unicode" else "letters"))
    t.append_text(player_line(g, bottom, width))
    t.append("\n")
    ev = g.current_eval_white()
    live = g.live
    t.append(f"{fmt_score(ev):>6} ", style="bold")
    t.append(f"d{live.get('depth', g.evals[-1].depth if g.evals else 0)} ")
    nps = live.get("nps") or (g.evals[-1].nps if g.evals else 0)
    t.append(f"{human(nps)}nps ", style="dim")
    t.append(f"{g.tc}", style="dim")
    t.append("\n")
    t.append_text(search_state(g))
    return t


def pv_san(board: chess.Board, pv: list[str], limit: int = 12) -> str:
    b = board.copy(stack=False)
    parts = []
    for u in pv[:limit]:
        try:
            m = chess.Move.from_uci(u)
            if m not in b.legal_moves:
                break
            if b.turn == chess.WHITE:
                parts.append(f"{b.fullmove_number}.{b.san(m)}")
            elif not parts:
                parts.append(f"{b.fullmove_number}...{b.san(m)}")
            else:
                parts.append(b.san(m))
            b.push(m)
        except ValueError:
            break
    return " ".join(parts)


def search_board(g: GameSession) -> chess.Board:
    b = g.board.copy(stack=False)
    if g.search_kind == "ponder" and g.ponder_move and g.ponder_base == g.moves:
        try:
            b.push_uci(g.ponder_move)
        except ValueError:
            pass
    return b


def search_table(g: GameSession, max_rows: int, pv_width: int) -> Table:
    tbl = Table(box=box.SIMPLE_HEAD, expand=True, padding=(0, 1), show_edge=False)
    tbl.add_column("Depth", justify="right", no_wrap=True)
    tbl.add_column("Score", justify="right", no_wrap=True)
    tbl.add_column("Time", justify="right", no_wrap=True)
    tbl.add_column("Nodes", justify="right", no_wrap=True)
    tbl.add_column("NPS", justify="right", no_wrap=True)
    tbl.add_column("Hash", justify="right", no_wrap=True)
    tbl.add_column("TB", justify="right", no_wrap=True)
    tbl.add_column("PV", no_wrap=True, overflow="ellipsis", ratio=1)
    rows = g.rows[-max_rows:][::-1]
    board = None
    for i, r in enumerate(rows):
        if "san" not in r:
            board = board or search_board(g)
            r["san"] = pv_san(board, r.get("pv", []))
        s = score_cp(r)
        sc = fmt_score(r.get("cp"), r.get("mate"))
        style = "green" if (s or 0) > 30 else "red" if (s or 0) < -30 else ""
        tbl.add_row(f"{r.get('depth', '')}/{r.get('seldepth', '')}", Text(sc, style=style),
                    f"{r.get('time', 0) / 1000:.2f}", human(r.get("nodes", 0)), human(r.get("nps", 0)),
                    f"{r.get('hashfull', 0) / 10:.0f}%", human(r.get("tbhits", 0)),
                    Text(r["san"], style="bold" if i == 0 else "dim"))
    return tbl


def engine_summary(g: GameSession) -> Text:
    t = search_state(g)
    r = g.rows[-1] if g.rows else None
    if r:
        s = score_cp(r)
        t.append("   ")
        t.append(f"score {fmt_score(r.get('cp'), r.get('mate'))}", style="bold")
        if s is not None and abs(s) < MATE_CP - 1000:
            t.append(f" (win {win_percent(s):.0f}%)", style="dim")
        t.append(f"   depth {r.get('depth')}/{r.get('seldepth')}")
    t.append("\n")
    live = g.live
    if live:
        t.append(f"nodes {human(live.get('nodes', 0))}  nps {human(live.get('nps', 0))}"
                 f"  hash {live.get('hashfull', 0) / 10:.0f}%  tbhits {human(live.get('tbhits', 0))}  ", style="dim")
    if g.ponder_total:
        t.append(f"ponderhit {g.ponder_hits}/{g.ponder_total}", style="dim")
    return t


def eval_chart(g: GameSession, width: int, height: int) -> Text:
    """Lichess-style area chart of White's winning chance over the game (one column per own move)."""
    pts = [e for e in g.evals if e.white_cp is not None]
    t = Text()
    width = max(10, width - 5)
    if not pts:
        return t.append("no evaluations yet", style="dim")
    vals = [win_percent(max(-2000, min(2000, e.white_cp))) for e in pts]
    n = len(vals)
    if n > width:                               # compress: sample evenly
        cols = [vals[int(i * n / width)] for i in range(width - 1)] + [vals[-1]]
    else:
        rep = max(1, min(3, width // n))
        cols = [v for v in vals for _ in range(rep)]
    levels = height * 8
    blocks = " ▁▂▃▄▅▆▇█"
    mid = height // 2
    for row in range(height - 1, -1, -1):
        label = {height - 1: "100", mid: " 50", 0: "  0"}.get(row, "   ")
        t.append(f"{label} ", style="dim")
        for v in cols:
            fill = v / 100 * levels - row * 8
            if fill >= 8:
                t.append("█", style="#e8e8e8 on #3a3a3a")
            elif fill > 0:
                t.append(blocks[int(fill)], style="#e8e8e8 on #3a3a3a")
            else:
                t.append("·" if row == mid else " ", style="#777777 on #3a3a3a")
        t.append("\n")
    last = pts[-1]
    t.append(f"    last {fmt_score(last.white_cp)} (white)   moves {g.board.fullmove_number}   "
             f"min {fmt_score(min(e.white_cp for e in pts))} max {fmt_score(max(e.white_cp for e in pts))}",
             style="dim")
    return t


def move_list(g: GameSession, height: int) -> Text:
    evals = {e.ply: e for e in g.evals}
    lines = []
    start_black = g.start_board.turn == chess.BLACK
    num = g.start_board.fullmove_number
    i = 0
    sans = g.sans
    if start_black and sans:
        lines.append((num, "...", None, sans[0], evals.get(0)))
        i, num = 1, num + 1
    while i < len(sans):
        w = sans[i]
        b = sans[i + 1] if i + 1 < len(sans) else ""
        lines.append((num, w, evals.get(i), b, evals.get(i + 1)))
        i += 2
        num += 1
    t = Text()
    for num, w, we, b, be in lines[-height:]:
        t.append(f"{num:>3}. ", style="dim")
        t.append(f"{w:<7}", style="bold" if we else "")
        t.append(f"{fmt_score(we.white_cp) if we else '':>6} ", style="cyan")
        t.append(f"{b:<7}", style="bold" if be else "")
        t.append(f"{fmt_score(be.white_cp) if be else '':>6}", style="cyan")
        t.append("\n")
    return t
