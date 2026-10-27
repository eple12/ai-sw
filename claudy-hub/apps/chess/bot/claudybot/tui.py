"""Textual console dashboard."""
from __future__ import annotations

import shutil
import subprocess
import time
import traceback
from types import SimpleNamespace

import chess
from rich.console import Group
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.suggester import SuggestFromList
from textual.widgets import ContentSwitcher, Footer, Input, RichLog, Static

from . import __version__, render
from .commands import UI, Commands
from .sixelimg import image_widget
from .game import GameSession, fmt_score, win_percent
from .manager import BotManager

LEVEL_STYLE = {"error": "bold red", "warning": "yellow", "info": "", "debug": "dim"}


class CommandInput(Input):
    BINDINGS = [Binding("up", "hist_prev", show=False), Binding("down", "hist_next", show=False)]

    def __init__(self, **kw):
        super().__init__(**kw)
        self.history: list[str] = []
        self.pos = 0

    def push(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
        self.pos = len(self.history)

    def action_hist_prev(self) -> None:
        if self.history:
            self.pos = max(0, self.pos - 1)
            self.value = self.history[self.pos]
            self.cursor_position = len(self.value)

    def action_hist_next(self) -> None:
        if self.pos < len(self.history) - 1:
            self.pos += 1
            self.value = self.history[self.pos]
        else:
            self.pos = len(self.history)
            self.value = ""
        self.cursor_position = len(self.value)


class GameCard(Vertical):
    def __init__(self, gid: str, graphics: bool):
        super().__init__(id=f"card-{gid}", classes="card")
        self.gid = gid
        self.graphics = graphics
        self.img_key: tuple | None = None

    def compose(self) -> ComposeResult:
        yield Static(classes="c-top")
        yield Static(classes="c-board")
        if self.graphics:
            yield image_widget(classes="c-img")
        yield Static(classes="c-bottom")

    def on_click(self) -> None:
        self.app.watch_game(self.gid)  # type: ignore[attr-defined]


class Hooks(UI):
    def __init__(self, app: "ClaudyApp"):
        self.app = app

    @property
    def watched(self) -> str | None:  # type: ignore[override]
        return self.app.watched if self.app.view == "detail" else None

    @property
    def view(self) -> str:  # type: ignore[override]
        return self.app.view

    def watch(self, game: GameSession) -> None:
        self.app.watch_game(game.id)

    def analysis(self) -> None:
        self.app.show_analysis()

    def overview(self) -> None:
        self.app.action_overview()

    def clear_log(self) -> None:
        self.app.query_one("#log", RichLog).clear()

    def copy(self, text: str) -> None:
        """Put a link on the clipboard: OSC 52 (Windows Terminal and most others) and, in Termux,
        termux-clipboard-set (Termux:API) when installed."""
        try:
            self.app.copy_to_clipboard(text)
        except Exception:
            pass
        exe = shutil.which("termux-clipboard-set")
        if exe:
            try:
                subprocess.run([exe], input=text.encode(), timeout=5, check=False)
            except (OSError, subprocess.SubprocessError):
                pass


class ClaudyApp(App):
    TITLE = "ClaudyBot"
    CSS = """
    Screen { layout: vertical; }
    #topbar { height: 1; background: #1f3a5f; color: #ffffff; }
    #main { height: 1fr; }
    #overview { height: 1fr; }
    #cards-scroll { width: 1fr; }
    #cards { layout: grid; grid-size: 3; grid-rows: 14; grid-gutter: 0 1; height: auto; }
    .card { border: round #5a6b7a; padding: 0 1; height: 14; layout: vertical; }
    .c-top { height: 1; }
    .c-bottom { height: 3; }
    .c-board { height: auto; }
    .card.myturn { border: round #4caf50; }
    .card.done { border: round #555555; color: #9a9a9a; }
    #empty { padding: 2 4; color: #9a9a9a; }
    #side { width: 46; border-left: solid #3a4a5a; padding: 0 1; }
    #detail { height: 1fr; }
    #d-left { width: 38; }
    #d-boardbox { height: auto; border: round #5a6b7a; padding: 0 1; border-subtitle-align: left; }
    #d-board { height: auto; }
    #d-top { height: 1; }
    #d-evalbar { height: 2; }
    #d-info { height: 1fr; border: round #3a4a5a; padding: 0 1; }
    #d-right { width: 1fr; }
    #d-engine { height: 14; border: round #5a6b7a; padding: 0 1; }
    #d-mid { height: 11; }
    #d-chart { width: 1fr; height: 100%; border: round #3a4a5a; padding: 0 1; }
    #d-moves { width: 38; height: 100%; border: round #3a4a5a; padding: 0 1; }
    #d-raw { height: 1fr; border: round #3a4a5a; }
    #analysis { height: 1fr; }
    #a-left { width: 38; }
    #a-boardbox { height: auto; border: round #5a6b7a; padding: 0 1; }
    #a-board { height: auto; }
    #a-help { height: 1fr; border: round #3a4a5a; padding: 0 1; }
    #a-right { width: 1fr; }
    #a-lines { height: auto; min-height: 5; border: round #5a6b7a; padding: 0 1; }
    #a-mid { height: 1fr; }
    #a-chart { width: 1fr; height: 100%; border: round #3a4a5a; padding: 0 1; }
    #a-moves { width: 38; height: 100%; border: round #3a4a5a; padding: 0 1; }
    #log { height: 8; border-top: solid #3a4a5a; }
    #cmd { height: 3; }
    """
    BINDINGS = [
        Binding("f1", "help", "Help"),
        Binding("f2", "overview", "Overview"),
        Binding("f3", "prev_game", "Prev game"),
        Binding("f4", "next_game", "Next game"),
        Binding("f5", "toggle_pause", "Pause/Resume"),
        Binding("f6", "flip", "Flip board"),
        Binding("f7", "new_link", "Link"),
        Binding("f8", "analysis", "Analysis"),
        Binding("escape", "overview", "Back", show=False),
        Binding("ctrl+q", "quit_app", "Quit", priority=True),
    ]

    def __init__(self, mgr: BotManager, graphics: bool = False):
        super().__init__()
        self.graphics = graphics                  # terminal can show images (Sixel / Kitty)
        self.painter = None
        if graphics:                              # PIL only needed for terminal pictures
            from .boardimg import BoardPainter
            self.painter = BoardPainter(mgr.cfg.get("ui.piece_set"))
        self._dimg_key: tuple | None = None
        self._applied: dict[tuple[int, str], object] = {}
        self.mgr = mgr
        self.cfg = mgr.cfg
        self.hooks = Hooks(self)
        self.commands = Commands(mgr, self.hooks)
        self.view = "overview"
        self.watched: str | None = None
        self.flip = False
        self.events_seen = 0
        self.raw_seen = 0
        self.quit_armed = 0.0
        self._card_h: tuple[int, int] = (0, 0)

    @property
    def style(self) -> str:
        st = self.cfg.get("ui.pieces")
        if st == "image" and not (self.graphics and self.painter and self.painter.pieces.available):
            return "sprites"
        return st

    def _set(self, w, **styles) -> None:
        """Apply styles only when they change: every style write triggers a layout pass, and a layout pass
        repaints Sixel images (the cause of flicker)."""
        for k, v in styles.items():
            key = (id(w), k)
            if self._applied.get(key) != v:
                self._applied[key] = v
                setattr(w.styles, k, v)

    def _titles(self, w, title: str | None = None, subtitle: str | None = None) -> None:
        """Border titles only when they change: assigning one always repaints the whole widget,
        including any Sixel picture inside it."""
        for attr, val in (("border_title", title), ("border_subtitle", subtitle)):
            if val is None:
                continue
            key = (id(w), attr)
            if self._applied.get(key) != val:
                self._applied[key] = val
                setattr(w, attr, val)

    @staticmethod
    def _picture(img, pic) -> None:
        """Swap the picture of a Sixel widget in place (the widget's own setter recreates its child,
        which blanks the area for a frame)."""
        try:
            from textual_image.widget.sixel import _ImageSixelImpl
            impl = img.query_one(_ImageSixelImpl)
        except Exception:
            img.image = pic
            return
        img._image = pic
        impl.image = pic
        impl.refresh()

    @staticmethod
    def _show(w, flag: bool) -> None:
        if w.display != flag:
            w.display = flag

    def _text(self, w: Static, content, layout: bool = False) -> None:
        """Static.update without a layout pass (fixed-height widgets)."""
        w.update(content, layout=layout)

    def board_cells(self, rows: int) -> int:
        """Columns that make `rows` terminal rows look square (physical cell height / width)."""
        return max(8, round(rows * float(self.cfg.get("ui.cell_aspect"))))

    def cell_px(self) -> tuple[int, int]:
        from textual_image.widget import get_cell_size
        c = get_cell_size()
        return max(1, c.width), max(1, c.height)

    # ---- layout ----------------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static(id="topbar")
        with ContentSwitcher(initial="overview", id="main"):
            with Horizontal(id="overview"):
                with VerticalScroll(id="cards-scroll"):
                    yield Static("No games running - waiting for challenges.\n\n"
                                 "Type [b]help[/b] for commands, [b]pause[/b] / [b]resume[/b] to control challenges,\n"
                                 "[b]challenge <user> 3+2[/b] to start a game, or [b]match on[/b] for matchmaking.",
                                 id="empty")
                    yield Container(id="cards")
                yield Static(id="side")
            with Horizontal(id="detail"):
                with Vertical(id="d-left"):
                    with Vertical(id="d-boardbox"):
                        yield Static(id="d-top")
                        yield Static(id="d-board")
                        if self.graphics:
                            yield image_widget(id="d-img")
                        yield Static(id="d-evalbar")
                    yield Static(id="d-info")
                with Vertical(id="d-right"):
                    yield Static(id="d-engine")
                    with Horizontal(id="d-mid"):
                        yield Static(id="d-chart")
                        yield Static(id="d-moves")
                    yield RichLog(id="d-raw", max_lines=1500, wrap=False)
            with Horizontal(id="analysis"):
                with Vertical(id="a-left"):
                    with Vertical(id="a-boardbox"):
                        yield Static(id="a-top")
                        yield Static(id="a-board")
                        yield Static(id="a-evalbar")
                    yield Static(id="a-help")
                with Vertical(id="a-right"):
                    yield Static(id="a-lines")
                    with Horizontal(id="a-mid"):
                        yield Static(id="a-chart")
                        yield Static(id="a-moves")
        yield RichLog(id="log", max_lines=3000, wrap=True)
        names = self.commands.names + ["pause ignore", "match on", "match off", "match now", "quit now",
                                       "config challenge", "set challenge.", "set engine.options."]
        yield CommandInput(placeholder="command - help | pause | resume | <n> watch game | back | "
                                       "challenge <user> 3+2 | set <key> <value> | quit",
                           id="cmd", suggester=SuggestFromList(names, case_sensitive=False))
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#d-engine").border_title = "Engine"
        self.query_one("#d-chart").border_title = "Eval history - White win %"
        self.query_one("#d-moves").border_title = "Moves"
        self.query_one("#d-raw").border_title = "Engine I/O"
        self.query_one("#d-info").border_title = "Game"
        self.query_one("#side").border_title = "Queue"
        self.query_one("#a-lines").border_title = "Engine lines"
        self.query_one("#a-chart").border_title = "Whole game - White win %"
        self.query_one("#a-moves").border_title = "Moves"
        self.query_one("#a-help").border_title = "Analysis"
        self.query_one("#cmd").focus()
        self.set_interval(self.cfg.get("ui.refresh_ms") / 1000, self.tick)
        self.run_worker(self._run_manager(), exit_on_error=False)
        self.tick()

    async def _run_manager(self) -> None:
        try:
            await self.mgr.run()
        except Exception:
            self.mgr.log("error", "manager crashed:\n" + traceback.format_exc())
        if self.mgr.fatal:
            self.mgr.log("error", "press Ctrl+Q to exit")
            return
        await self.mgr.close()
        self.exit()

    # ---- periodic refresh ------------------------------------------------------------------
    def tick(self) -> None:
        try:
            self._topbar()
            self._log()
            if self.view == "overview":
                self._overview()
            elif self.view == "analysis":
                self._analysis()
            else:
                self._detail()
        except Exception:
            self.mgr.log("error", "UI refresh failed:\n" + traceback.format_exc())

    def _topbar(self) -> None:
        m = self.mgr
        c = self.cfg.get("challenge")
        t = Text(f" ClaudyBot {__version__} ", style="bold")
        t.append("│ ")
        t.append(f"{m.username} ", style="bold cyan")
        t.append("│ ")
        if m.fatal:
            t.append(f"ERROR: {m.fatal} ", style="bold white on red")
        elif not m.online:
            t.append("○ OFFLINE " if m.offline_only else "○ OFFLINE (retrying) ", style="bold yellow")
        elif m.quitting:
            t.append("● QUITTING ", style="bold magenta")
        elif m.paused:
            t.append("⏸ PAUSED ", style="bold yellow")
        else:
            t.append("● ACCEPTING ", style="bold green")
        use = m.slot_usage()
        t.append(f"│ games {sum(use.values())}/{c['concurrency']} ")
        if m.split_slots():
            t.append(f"(bot {use['bot']}/{m.slot_cap('bot')} human {use['human']}/{m.slot_cap('human')}) ",
                     style="dim")
        t.append(f"│ queue {len(m.queue)} │ out {len(m.outgoing)} │ ")
        if m.links:
            t.append(f"links {len(m.links)} │ ")
        t.append(f"MM {'on' if self.cfg.get('matchmaking.enabled') else 'off'} │ ")
        if m.online:
            t.append("stream ok " if m.stream_ok else "stream DOWN ", style="" if m.stream_ok else "bold red")
        r = m.results
        up = int(time.time() - m.started)
        t.append(f"│ +{r['win']} ={r['draw']} -{r['loss']} │ up {up // 3600}:{up % 3600 // 60:02d}:{up % 60:02d}")
        self._text(self.query_one("#topbar", Static), t)

    def _log(self) -> None:
        m = self.mgr
        new = m.events_total - self.events_seen
        if new <= 0:
            return
        log = self.query_one("#log", RichLog)
        for ts, level, msg in list(m.events)[-min(new, len(m.events)):]:
            line = Text(time.strftime("%H:%M:%S ", time.localtime(ts)), style="dim")
            line.append(msg, style=LEVEL_STYLE.get(level, ""))
            log.write(line)
        self.events_seen = m.events_total

    def _display_games(self) -> list[tuple[GameSession, int | None]]:
        out: list[tuple[GameSession, int | None]] = [(g, i) for i, g in enumerate(self.mgr.game_list(), 1)]
        now = time.time()
        out += [(g, None) for g in self.mgr.finished if g.ended and now - g.ended < 60][:6]
        return out

    def _overview(self) -> None:
        games = self._display_games()
        cards = self.query_one("#cards", Container)
        area = self.query_one("#cards-scroll").size
        style = self.style
        n = max(1, len(games))
        # biggest board that still shows every card without scrolling: (board cols, board rows, kind)
        options: list[tuple[int, int, str]] = []
        if style == "image":
            options = [(self.board_cells(r), r, "image") for r in (24, 16, 12, 9)]
        elif style == "sprites":
            options = [(48, 24, "text"), (24, 8, "text")]
        else:
            options = [(24, 8, "text")]
        bc, br, kind = options[-1]
        for oc, orr, ok in options:
            w, h = max(oc, 26) + 4, orr + 6
            per_row = max(1, (area.width - 1) // (w + 1))
            if -(-n // per_row) * h <= area.height:
                bc, br, kind = oc, orr, ok
                break
        card_w, card_h = max(bc, 26) + 4, br + 6
        cols = max(1, (area.width - 1) // (card_w + 1))
        self._set(cards, grid_size_columns=cols, grid_rows=str(card_h))
        for c in cards.query(GameCard):
            self._set(c, height=card_h, width=card_w)
        want = [g.id for g, _ in games]
        existing = {c.gid: c for c in cards.query(GameCard)}
        for gid, c in existing.items():
            if gid not in want:
                c.remove()
        text_style = style if style != "image" else "sprites"
        for g, idx in games:
            c = existing.get(g.id)
            if c is None:
                c = GameCard(g.id, self.graphics)
                self._set(c, height=card_h, width=card_w)
                cards.mount(c)
                continue                      # children exist after the next refresh
            inner = card_w - 4
            try:
                top, board_w, bottom = c.query_one(".c-top", Static), c.query_one(".c-board", Static), \
                    c.query_one(".c-bottom", Static)
            except Exception:
                continue
            self._text(top, render.card_top(g, inner))
            self._text(bottom, render.card_bottom(g, inner))
            white_bottom = g.color == chess.WHITE
            if kind == "image" and self.painter:
                img = c.query_one(".c-img")
                self._show(board_w, False)
                self._show(img, True)
                self._set(img, width=bc, height=br)
                pw, ph = self.cell_px()
                px = br * ph                   # square picture; the widget stretches it to the cell box
                key = (g.board.fen(), len(g.moves), white_bottom, px)
                if key != c.img_key:
                    c.img_key = key
                    self._picture(img, self.painter.render(g.board, white_bottom, px, coords=False))
            else:
                if self.graphics:
                    self._show(c.query_one(".c-img"), False)
                self._show(board_w, True)
                cw, ch = (6, 3) if br == 24 else (3, 1)
                board_w.update(render.board_text(g.board, white_bottom=white_bottom, cell_w=cw, cell_h=ch,
                                                 coords=False, style=text_style if br == 24 else
                                                 ("unicode" if style == "unicode" else "letters")))
            self._titles(c, f"[{idx}] {g.id}" if idx else f"{g.id} (ended)",
                         f"{'rated' if g.rated else 'casual'} {g.speed}")
            # a border colour change repaints the whole card (and its picture), so only for text boards
            c.set_class(kind != "image" and g.my_turn and not g.over, "myturn")
            c.set_class(g.over, "done")
        self._show(self.query_one("#empty"), not games)
        self._side()

    def _side(self) -> None:
        m = self.mgr
        t = Text()
        t.append("Incoming queue\n", style="bold underline")
        if not m.queue:
            t.append("  (empty)\n", style="dim")
        for i, ch in enumerate(m.queue, 1):
            t.append(f"  [{i}] ", style="cyan")
            t.append(f"{ch.label()}\n")
        if m.links:
            t.append("\nOpen links\n", style="bold underline")
            for i, ln in enumerate(m.links.values(), 1):
                left = max(0.0, ln["expires"] - time.time()) / 3600
                t.append(f"  [{i}] ", style="cyan")
                t.append(f"{ln['url']}\n      {ln['tc']} {'rated' if ln['rated'] else 'casual'} {ln['color']} "
                         f"{left:.1f}h\n")
        t.append("\nOutgoing challenges\n", style="bold underline")
        if not m.outgoing:
            t.append("  (none)\n", style="dim")
        for cid, (ch, ts) in m.outgoing.items():
            t.append(f"  {ch.challenger} {ch.tc} {time.monotonic() - ts:.0f}s\n")
        t.append("\nRecent games\n", style="bold underline")
        if not m.finished:
            t.append("  (none)\n", style="dim")
        for g in list(m.finished)[:8]:
            oc = g.outcome_for_me()
            style = {"win": "green", "loss": "red", "draw": "yellow"}.get(oc, "dim")
            t.append(f"  {oc:<5}", style=style)
            t.append(f" {g.result():<7} {g.opponent.get('name') or '?'} {g.tc}\n")
        c = self.cfg.get("challenge")
        mm = self.cfg.get("matchmaking")
        t.append("\nAccepting\n", style="bold underline")
        t.append(f"  {', '.join(c['speeds'])}\n  {', '.join(c['modes'])} · base {c['min_base']}-{c['max_base']}s "
                 f"· inc {c['min_inc']}-{c['max_inc']}s\n")
        t.append(f"  bots {'yes' if c['accept_bots'] else 'no'} · humans {'yes' if c['accept_humans'] else 'no'}"
                 f" · rating {c['min_rating']}-{c['max_rating']}\n")
        t.append(f"  matchmaking {'on' if mm['enabled'] else 'off'} ({', '.join(mm['tc'])})\n")
        eng = self.cfg.get("engine.options")
        t.append(f"\nEngine: Threads {eng.get('Threads')} · Hash {eng.get('Hash')} · "
                 f"ponder {'on' if self.cfg.get('engine.ponder') else 'off'}\n", style="dim")
        self._text(self.query_one("#side", Static), t)

    # ---- detail view -------------------------------------------------------------------------
    def _watched_game(self) -> GameSession | None:
        if not self.watched:
            return None
        g = self.mgr.games.get(self.watched)
        if g:
            return g
        return next((x for x in self.mgr.finished if x.id == self.watched), None)

    def watch_game(self, gid: str) -> None:
        self.watched = gid
        self.view = "detail"
        self.query_one("#main", ContentSwitcher).current = "detail"
        self._set(self.query_one("#log"), height=6)
        raw = self.query_one("#d-raw", RichLog)
        raw.clear()
        g = self._watched_game()
        if g:
            for item in list(g.raw)[-400:]:
                raw.write(self._raw_line(item))
            self.raw_seen = g.raw_total
        self.tick()

    @staticmethod
    def _raw_line(item: tuple[float, str, str]) -> Text:
        ts, d, text = item
        line = Text(time.strftime("%H:%M:%S ", time.localtime(ts)), style="dim")
        if d == ">":
            line.append("> " + text, style="bold cyan")
        elif text.startswith("bestmove"):
            line.append("< " + text, style="bold green")
        else:
            line.append("< " + text, style="" if not text.startswith("info") else "#b0b0b0")
        return line

    def _detail(self) -> None:
        g = self._watched_game()
        if g is None:
            self.action_overview()
            return
        style = self.style
        main_h = self.size.height - 1 - 6 - 3 - 1          # topbar, log (6 in this view), input, footer
        me = g.human_color if g.local else g.color
        white_bottom = (me == chess.WHITE) != self.flip
        top = chess.BLACK if white_bottom else chess.WHITE
        board_w = self.query_one("#d-board", Static)
        if style == "image":
            pw, ph = self.cell_px()
            rows = max(8, main_h - 10)
            cols = self.board_cells(rows)
            max_cols = max(20, self.size.width - 94)
            while cols > max_cols and rows > 8:
                rows -= 1
                cols = self.board_cells(rows)
            img = self.query_one("#d-img")
            self._show(board_w, False)
            self._show(img, True)
            self._set(img, width=cols, height=rows)
            self._set(self.query_one("#d-left"), width=cols + 4)
            bw = cols
            px = rows * ph
            key = (g.id, g.board.fen(), len(g.moves), white_bottom, px)
            if key != self._dimg_key:
                self._dimg_key = key
                self._picture(img, self.painter.render(g.board, white_bottom, px))
        else:
            if self.graphics:
                self._show(self.query_one("#d-img"), False)
            self._show(board_w, True)
            cw, ch = render.square_size(style, self.size.width - 90, main_h - 9)
            self._set(self.query_one("#d-left"), width=8 * cw + 6)
            bw = 8 * cw + 2
            board_w.update(render.board_text(g.board, white_bottom=white_bottom, cell_w=cw, cell_h=ch, style=style))
        bar = render.eval_bar(g.current_eval_white(), max(8, bw - 16))
        ev = g.current_eval_white()
        if ev is not None:
            bar.append(f" {fmt_score(ev):>6} W{win_percent(max(-2000, min(2000, ev))):3.0f}%", style="bold")
        bottom = render.player_line(g, not top, bw)
        bottom.append("\n")
        bottom.append_text(bar)
        self._text(self.query_one("#d-top", Static), render.player_line(g, top, bw))
        self._text(self.query_one("#d-evalbar", Static), bottom)
        box = self.query_one("#d-boardbox")
        self._titles(box, f"{g.id} · {'rated' if g.rated else 'casual'} {g.speed} {g.tc}")

        info = Text()
        if g.local:
            info.append("local game - type your moves (e4, Nf3, O-O); takeback, draw, resign\n", style="bold green")
        else:
            info.append(f"{g.url}\n", style="underline")
        info.append(f"move {g.board.fullmove_number}  ply {len(g.moves)}  {g.variant}\n")
        info.append_text(render.search_state(g))
        info.append("\n")
        if g.ponder_total:
            info.append(f"ponder hits {g.ponder_hits}/{g.ponder_total}\n", style="dim")
        info.append(f"{self.mgr.lag.text()}  overhead {g.overhead_now or '-'} ms\n", style="dim")
        if g.limit_elo:
            info.append(f"rating limit {g.limit_elo}\n", style="bold yellow")
        if g.wdraw or g.bdraw:
            info.append(f"draw offered by {'white' if g.wdraw else 'black'}\n", style="yellow")
        if g.opp_gone:
            left = max(0, (g.claim_at or time.monotonic()) - time.monotonic())
            info.append(f"opponent left - claim in {left:.0f}s\n", style="yellow")
        if g.chat:
            info.append("chat\n", style="bold")
            for ts, room, user, text in list(g.chat)[-6:]:
                info.append(f"{ts[:5]} {'S' if room == 'spectator' else 'P'} {user}: ", style="dim")
                info.append(f"{text}\n")
        self._text(self.query_one("#d-info", Static), info)

        eng = self.query_one("#d-engine", Static)
        self._titles(eng, f"Engine · {g.engine.name if g.engine else '-'}")
        rows = max(1, eng.size.height - 5)
        self._text(eng, Group(render.engine_summary(g),
                         render.search_table(g, rows, max(20, eng.size.width - 60))))
        chart = self.query_one("#d-chart", Static)
        self._text(chart, render.eval_chart(g, chart.size.width, max(3, chart.size.height - 1)))
        moves = self.query_one("#d-moves", Static)
        self._text(moves, render.move_list(g, max(1, moves.size.height)))

        new = g.raw_total - self.raw_seen
        if new > 0:
            raw = self.query_one("#d-raw", RichLog)
            for item in list(g.raw)[-min(new, len(g.raw)):]:
                raw.write(self._raw_line(item))
            self.raw_seen = g.raw_total

    # ---- input / actions -------------------------------------------------------------------
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        cmd = self.query_one("#cmd", CommandInput)
        line = event.value.strip()
        cmd.value = ""
        if not line:
            return
        cmd.push(line)
        log = self.query_one("#log", RichLog)
        log.write(Text(f"> {line}", style="bold cyan"))
        for out in await self.commands.execute(line):
            log.write(Text.from_markup(out))
        self.tick()

    def action_overview(self) -> None:
        self.view = "overview"
        self.query_one("#main", ContentSwitcher).current = "overview"
        self._set(self.query_one("#log"), height=8)
        self.query_one("#cmd").focus()

    def _cycle(self, step: int) -> None:
        games = self.mgr.game_list()
        if not games:
            return
        ids = [g.id for g in games]
        i = ids.index(self.watched) if self.watched in ids and self.view == "detail" else (-1 if step > 0 else 0)
        self.watch_game(ids[(i + step) % len(ids)])

    def action_next_game(self) -> None:
        if self.view == "analysis":
            an = self.mgr.get_analysis()
            self.run_worker(an.goto(an.cursor + 1), exit_on_error=False)
            return
        self._cycle(1)

    def action_prev_game(self) -> None:
        if self.view == "analysis":
            an = self.mgr.get_analysis()
            self.run_worker(an.goto(an.cursor - 1), exit_on_error=False)
            return
        self._cycle(-1)

    def action_analysis(self) -> None:
        self.show_analysis()

    def show_analysis(self) -> None:
        self.view = "analysis"
        self.query_one("#main", ContentSwitcher).current = "analysis"
        self._set(self.query_one("#log"), height=6)
        self.mgr.get_analysis().touch()
        self.query_one("#cmd").focus()
        self.tick()

    def _analysis(self) -> None:
        an = self.mgr.get_analysis()
        an.touch()
        style = "sprites" if self.style == "image" else self.style
        main_h = self.size.height - 1 - 6 - 3 - 1
        cw, ch = render.square_size(style, self.size.width - 90, main_h - 9)
        self._set(self.query_one("#a-left"), width=8 * cw + 6)
        bw = 8 * cw + 2
        b = an.board
        self.query_one("#a-board", Static).update(
            render.board_text(b, white_bottom=not an.flip, cell_w=cw, cell_h=ch, style=style))
        lines = an.line_list()
        top_cp = lines[0]["cp"] if lines else (an.evals.get(an.cursor) or {}).get("cp")
        i = an.cursor - 1
        shift = 0 if an.start.turn == chess.WHITE else 1
        where = (f"after {an.start.fullmove_number + (i + shift) // 2}{'...' if (i + shift) % 2 else '.'} "
                 f"{an.sans[i]}" if an.cursor else "start")
        head = Text(f"{an.title[:bw]}\n", style="bold")
        head.append(f"{where}  ply {an.cursor}/{len(an.moves)}  {'white' if b.turn else 'black'} to move", style="dim")
        self._text(self.query_one("#a-top", Static), head)
        bar = render.eval_bar(top_cp, max(8, bw - 16))
        if top_cp is not None:
            bar.append(f" {fmt_score(top_cp):>6} W{win_percent(max(-2000, min(2000, top_cp))):3.0f}%", style="bold")
        out = b.outcome(claim_draw=True)
        if out:
            bar.append(f"\n{out.result()} {out.termination.name.lower()}", style="bold yellow")
        self._text(self.query_one("#a-evalbar", Static), bar)

        t = Text()
        eng = an.engine_name or "engine"
        live = an.live
        if an.error:
            t.append(f"{an.error}\n", style="yellow")
        if not an.enabled:
            t.append("engine off - `an on`\n", style="dim")
        elif an.searching_key is None:
            t.append("starting...\n", style="dim")
        else:
            t.append(f"{eng}  depth {live.get('depth', 0)}  nodes {render.human(live.get('nodes', 0))}  "
                     f"nps {render.human(live.get('nps', 0))}\n", style="dim")
        for ln in lines:
            cp = ln["cp"]
            st = "bold green" if cp is not None and cp > 30 else "bold red" if cp is not None and cp < -30 else "bold"
            t.append(f"{ln['score']:>7} ", style=st)
            t.append(f"d{ln['depth']:<3}", style="dim")
            n, white = ln["first_move"], ln["white_first"]
            for k, m in enumerate(ln["san"][:12]):
                if white:
                    t.append(f"{n}.", style="dim")
                elif k == 0:
                    t.append(f"{n}...", style="dim")
                t.append(f"{m} ", style="bold" if k == 0 else "")
                if not white:
                    n += 1
                white = not white
            t.append("\n")
        lw = self.query_one("#a-lines", Static)
        self._titles(lw, f"Engine lines · {an.multipv}")
        self._text(lw, t, layout=True)

        marks = an.marks()
        evals = an.evals
        mt = Text()
        rows = []
        num = an.start.fullmove_number
        k = 0
        if shift and an.sans:
            rows.append((num, [(None, "..."), (0, an.sans[0])]))
            k, num = 1, num + 1
        while k < len(an.sans):
            rows.append((num, [(k, an.sans[k])] + ([(k + 1, an.sans[k + 1])] if k + 1 < len(an.sans) else [])))
            k += 2
            num += 1
        mh = max(3, self.query_one("#a-moves").size.height - 2)
        cur_row = next((r for r, (_, cells) in enumerate(rows) if any(c[0] == an.cursor - 1 for c in cells)), 0)
        first = max(0, min(cur_row - mh // 2, len(rows) - mh))
        for n, cells in rows[first:first + mh]:
            mt.append(f"{n:>3}. ", style="dim")
            for ply, san in cells:
                label = san + (marks.get(ply, "") if ply is not None else "")
                e = evals.get(ply + 1) if ply is not None else None
                style = "reverse bold" if ply is not None and ply == an.cursor - 1 else (
                    "bold red" if marks.get(ply) == "??" else "bold yellow" if marks.get(ply) in ("?", "?!") else "")
                mt.append(f"{label:<9}", style=style)
                mt.append(f"{fmt_score(e['cp']) if e and e['cp'] is not None else '':>6} ", style="cyan")
            mt.append("\n")
        if not an.sans:
            mt.append("no moves - type one (e4, Nf3) or load a game\n", style="dim")
        self._text(self.query_one("#a-moves", Static), mt)

        shim = SimpleNamespace(evals=[SimpleNamespace(ply=k, white_cp=v["cp"]) for k, v in sorted(evals.items())],
                               board=b)
        cw_ = self.query_one("#a-chart")
        pass_txt = (f" · analysing {an.pass_done}/{an.pass_total}" if an._pass_running()
                    else f" · {len(evals)} positions" if evals else " · `an game` to analyse all")
        self._titles(cw_, "Whole game - White win %" + pass_txt)
        self._text(cw_, render.eval_chart(shim, max(10, cw_.size.width - 4), max(3, cw_.size.height - 3)))

        hlp = Text()
        hlp.append("type a move (e4, Nf3) to play it\n")
        hlp.append("F3/F4 back/forward   an first/last\n")
        hlp.append("an lines 3 · an on/off · an flip\n")
        hlp.append("an game [1s] whole game\n")
        hlp.append("analyze fen <FEN> / pgn <file>\n")
        hlp.append("analyze game <n|id> (bot games)\n")
        hlp.append("an fen / an pgn: show + copy\n")
        hlp.append("F2 / Esc: overview\n", style="dim")
        self._text(self.query_one("#a-help", Static), hlp)

    def action_flip(self) -> None:
        self.flip = not self.flip
        self._dimg_key = None

    async def action_help(self) -> None:
        log = self.query_one("#log", RichLog)
        for out in await self.commands.execute("help"):
            log.write(Text.from_markup(out))

    def action_new_link(self) -> None:
        """Fill the command line with a link command using the defaults (edit, then Enter)."""
        d = self.cfg.get("link")
        cmd = self.query_one("#cmd", CommandInput)
        cmd.value = f"link {d['tc']} {'rated' if d['rated'] else 'casual'} {d['color']} {d['hours']:g}h"
        cmd.cursor_position = len(cmd.value)
        cmd.focus()

    async def action_toggle_pause(self) -> None:
        log = self.query_one("#log", RichLog)
        for out in await self.commands.execute("resume" if self.mgr.paused else "pause"):
            log.write(Text.from_markup(out))

    async def action_quit_app(self) -> None:
        if self.mgr.fatal or self.mgr.stopped.is_set():
            await self.mgr.close()
            self.exit()
            return
        if self.mgr.games and time.monotonic() - self.quit_armed > 3:
            self.quit_armed = time.monotonic()
            self.mgr.log("warning", f"{len(self.mgr.games)} game(s) running: press Ctrl+Q again within 3 s to quit "
                                    f"now, or type `quit` to finish them first")
            return
        await self.mgr.quit(now=True)
