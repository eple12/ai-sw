"""Analysis board: a position or a game, a line of moves you can step through or change, and the engine's
best lines (MultiPV) for the position shown. Optionally a whole-game pass: an evaluation for every
position and the moves that lost the most (?!, ?, ??). Runs its own engine process; works offline.

One instance per bot (BotManager.get_analysis()); the console and the browser dashboard share it.
"""
from __future__ import annotations

import asyncio
import io
import time
from typing import TYPE_CHECKING, Any

import chess
import chess.engine
import chess.pgn

from .engine import EngineError, UciEngine
from .game import MATE_CP, GameSession, fmt_score, score_cp, win_percent
from .local import parse_move

if TYPE_CHECKING:
    from .manager import BotManager

IDLE_PAUSE_S = 20          # stop the engine when no dashboard has looked at the analysis for this long
# win% drop of the side that moved (Lichess: 0.1 / 0.2 / 0.3 winning chances = 5 / 10 / 15 points)
MARKS = ((15, "??"), (10, "?"), (5, "?!"))


def _white_cp(info: dict, turn: bool) -> int | None:
    s = score_cp(info)
    if s is None:
        return None
    return s if turn == chess.WHITE else -s


def _clip(cp: int | None) -> int | None:
    return None if cp is None else max(-MATE_CP, min(MATE_CP, cp))


def _win(cp: int | None) -> float | None:
    return None if cp is None else win_percent(max(-2000, min(2000, cp)))


class Analysis:
    def __init__(self, mgr: "BotManager"):
        self.mgr = mgr
        self.cfg = mgr.cfg
        self.start = chess.Board()
        self.moves: list[chess.Move] = []
        self.sans: list[str] = []
        self.cursor = 0                      # plies of `moves` on the board shown
        self.title = "start position"
        self.headers: dict[str, str] = {}
        self.multipv = max(1, int(self.cfg.get("analysis.multipv")))
        self.enabled = True                  # the engine analyses while someone looks
        self.flip = False
        self.engine: UciEngine | None = None
        self.engine_name = ""
        self.lines: dict[int, dict] = {}     # multipv index -> latest info
        self.live: dict[str, Any] = {}
        self.searching_key: tuple | None = None
        self.evals: dict[int, dict] = {}     # whole-game pass: ply -> {"cp": white cp, "best": san, "depth": d}
        self.pass_task: asyncio.Task | None = None
        self.pass_done = 0
        self.pass_total = 0
        self.error = ""
        self.last_seen = 0.0
        self.version = 0                     # bumps on every change of line / position
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None

    # ---- position ---------------------------------------------------------------------------------
    def _position(self, n: int) -> str:
        """UCI position after n plies, with the moves (so the engine sees repetitions)."""
        moves = " ".join(m.uci() for m in self.moves[:n])
        return f"position fen {self.start.fen()}" + (f" moves {moves}" if moves else "")

    def board_at(self, n: int) -> chess.Board:
        b = self.start.copy(stack=False)
        for mv in self.moves[:n]:
            b.push(mv)
        return b

    @property
    def board(self) -> chess.Board:
        return self.board_at(self.cursor)

    def _resan(self) -> None:
        b = self.start.copy(stack=False)
        self.sans = []
        for mv in self.moves:
            self.sans.append(b.san(mv))
            b.push(mv)

    def _changed(self, keep_evals: bool = True) -> None:
        self.version += 1
        if not keep_evals:
            self.evals = {}
        self.lines = {}
        self.live = {}

    async def load(self, board: chess.Board | None = None, moves: list[chess.Move] | None = None,
                   cursor: int | None = None, title: str = "", headers: dict | None = None) -> None:
        await self._cancel_pass()
        self.start = (board or chess.Board()).copy(stack=False)
        self.moves = list(moves or [])
        self._resan()
        self.cursor = len(self.moves) if cursor is None else max(0, min(cursor, len(self.moves)))
        self.title = title or ("start position" if self.start == chess.Board() and not self.moves else "position")
        self.headers = dict(headers or {})
        self._changed(keep_evals=False)
        await self.refresh()

    async def load_text(self, text: str) -> str:
        """A FEN or a PGN (the first game in it). Returns a short description."""
        t = text.strip()
        if not t:
            await self.load()
            return "start position"
        try:
            b = chess.Board(t)
        except ValueError:
            b = None
        if b is None:
            game = chess.pgn.read_game(io.StringIO(t))
            if game is None:
                raise ValueError("no game found in the PGN")
            if game.errors:
                raise ValueError(f"PGN error: {game.errors[0]}")
            moves = list(game.mainline_moves())
            h = dict(game.headers)
            w, b = h.get("White", "?"), h.get("Black", "?")
            title = "game" if (w, b) == ("?", "?") else f"{w} - {b} {h.get('Result', '*')}".strip()
            if not moves and "[" not in t:
                raise ValueError("not a FEN or PGN")
            await self.load(game.board(), moves, 0, title, h)
            return f"{title}, {len(moves)} plies"
        if not b.is_valid():
            raise ValueError("that position is not legal")
        await self.load(b, [], 0, "position")
        return "position loaded"

    async def load_game(self, g: GameSession) -> str:
        moves = [chess.Move.from_uci(u) for u in g.moves]
        white = g.white.get("name") or "?"
        black = g.black.get("name") or "?"
        title = f"{white} - {black} {g.result() if g.over else '(running)'}"
        await self.load(g.start_board, moves, len(moves), title,
                        {"White": white, "Black": black, "Result": g.result(), "Site": g.url or "local"})
        return f"{title}, {len(moves)} plies"

    async def play(self, text: str) -> str:
        """A move on the board shown: follows the line when it is its next move, else replaces the rest."""
        b = self.board
        mv = parse_move(b, text)
        if self.cursor < len(self.moves) and self.moves[self.cursor] == mv:
            self.cursor += 1
            self._changed()
        else:
            if self.cursor < len(self.moves):
                await self._cancel_pass()
                self.evals = {k: v for k, v in self.evals.items() if k <= self.cursor}
            self.moves = self.moves[:self.cursor] + [mv]
            self._resan()
            self.cursor += 1
            self._changed()
        await self.refresh()
        return self.sans[self.cursor - 1]

    async def goto(self, n: int) -> None:
        n = max(0, min(n, len(self.moves)))
        if n != self.cursor:
            self.cursor = n
            self._changed()
            await self.refresh()

    async def set_multipv(self, n: int) -> None:
        self.multipv = max(1, min(int(n), 10))
        self._changed()
        await self.refresh(force=True)

    async def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if on:
            await self.refresh(force=True)
        else:
            await self._stop_search()

    def touch(self) -> None:
        """A dashboard is showing the analysis: keep (or start) the engine."""
        self.last_seen = time.monotonic()
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle())
        if self.enabled and self.searching_key is None and not self._pass_running():
            asyncio.create_task(self.refresh())

    async def _idle(self) -> None:
        while True:
            await asyncio.sleep(5)
            if time.monotonic() - self.last_seen > IDLE_PAUSE_S:
                await self._stop_search()
                return

    # ---- engine -----------------------------------------------------------------------------------
    async def _engine(self) -> UciEngine:
        if self.engine is not None and self.engine.alive:
            return self.engine
        ecfg = self.cfg.get("engine")
        opts = dict(ecfg["options"])
        a = self.cfg.get("analysis")
        if int(a["threads"]) > 0:
            opts["Threads"] = int(a["threads"])
        if int(a["hash"]) > 0:
            opts["Hash"] = int(a["hash"])
        opts["Ponder"] = False
        eng = UciEngine(self.cfg.resolve(ecfg["path"]), opts)
        await eng.start()
        self.engine = eng
        self.engine_name = eng.name
        if "multipv" not in eng.declared:
            self.error = f"{eng.name} has no MultiPV option: one line only"
        return eng

    async def _stop_search(self) -> None:
        if self._pass_running():         # the game pass owns the engine until it is done
            return
        async with self._lock:
            await self._stop_locked()

    async def _stop_locked(self) -> None:
        self.searching_key = None
        if self.engine is not None and self.engine.searching:
            try:
                await self.engine.stop()
            except EngineError:
                await self.engine.quit()
                self.engine = None

    def _pass_running(self) -> bool:
        return self.pass_task is not None and not self.pass_task.done()

    async def refresh(self, force: bool = False) -> None:
        """(Re)start the infinite search on the position shown, if it is not already running there."""
        if not self.enabled or self._pass_running():
            return
        if time.monotonic() - self.last_seen > IDLE_PAUSE_S:
            return
        b = self.board
        key = (b.fen(), self.multipv, self.version)
        if key == self.searching_key and not force:
            return
        async with self._lock:
            if key == self.searching_key and not force:
                return
            await self._stop_locked()
            if b.is_game_over():
                self.lines = {}
                return
            try:
                eng = await self._engine()
                await eng.setoption("MultiPV", self.multipv)
                self.lines = {}
                self.live = {}
                turn = b.turn
                ver = self.version

                def on_info(info: dict) -> None:
                    if self.version != ver:
                        return
                    info["t"] = time.monotonic()
                    self.live.update({k: v for k, v in info.items() if k != "pv"})
                    if "pv" in info and ("cp" in info or "mate" in info) and not info.get("bound"):
                        info["white_cp"] = _white_cp(info, turn)
                        self.lines[info.get("multipv", 1)] = info

                await eng.go(self._position(self.cursor), "go infinite", on_info)
                self.searching_key = key
                self.error = self.error if "MultiPV" in self.error else ""
            except (EngineError, OSError, asyncio.TimeoutError) as e:
                self.error = f"engine: {e}"
                self.searching_key = None

    # ---- whole game ----------------------------------------------------------------------------------
    async def analyze_game(self, ms: int | None = None) -> str:
        if not self.moves:
            raise ValueError("no moves to analyse")
        await self._cancel_pass()
        ms = int(ms or self.cfg.get("analysis.game_ms"))
        self.pass_total = len(self.moves) + 1
        self.pass_done = 0
        self.evals = {}
        self.pass_task = asyncio.create_task(self._pass(ms))
        return f"analysing {len(self.moves)} plies, {ms / 1000:g} s per position"

    async def _cancel_pass(self) -> None:
        if self._pass_running():
            assert self.pass_task is not None
            self.pass_task.cancel()
            try:
                await self.pass_task
            except (asyncio.CancelledError, Exception):
                pass
        self.pass_task = None

    async def _pass(self, ms: int) -> None:
        try:
            async with self._lock:
                await self._stop_locked()
                eng = await self._engine()
                await eng.setoption("MultiPV", 1)
                boards = [self.start.copy(stack=False)]
                for mv in self.moves:
                    nb = boards[-1].copy(stack=False)
                    nb.push(mv)
                    boards.append(nb)
                # last position first, as Lichess' fishnet does: the hash table then already knows how the
                # game went on when the earlier positions are searched (sharper, more consistent evaluations)
                for i in range(len(self.moves), -1, -1):
                    b = boards[i]
                    out = b.outcome(claim_draw=False)
                    if out is not None:
                        cp = 0 if out.winner is None else (MATE_CP if out.winner == chess.WHITE else -MATE_CP)
                        self.evals[i] = {"cp": cp, "best": "", "depth": 0}
                    else:
                        last: dict = {}

                        def on_info(info: dict) -> None:
                            if "pv" in info and ("cp" in info or "mate" in info) and not info.get("bound"):
                                last.clear()
                                last.update(info)

                        await eng.go(self._position(i), f"go movetime {ms}", on_info)
                        best, _ = await eng.wait_bestmove(timeout=ms / 1000 + 30)
                        try:
                            san = b.san(chess.Move.from_uci(best))
                        except (ValueError, AssertionError):
                            san = best
                        self.evals[i] = {"cp": _white_cp(last, b.turn), "best": san, "depth": last.get("depth", 0)}
                    self.pass_done += 1
                    self.version += 1
        except asyncio.CancelledError:
            if self.engine is not None and self.engine.searching:
                try:
                    await self.engine.stop()
                except EngineError:
                    pass
            raise
        except (EngineError, OSError, asyncio.TimeoutError) as e:
            self.error = f"game analysis stopped: {e}"
        self.mgr.log("info", f"analysis: game pass done ({self.pass_done}/{self.pass_total} positions)")
        asyncio.create_task(self.refresh(force=True))

    def marks(self) -> dict[int, str]:
        """ply of a move -> ?!, ?, ?? (from the whole-game pass)."""
        out: dict[int, str] = {}
        for i in range(len(self.moves)):
            a, b = self.evals.get(i), self.evals.get(i + 1)
            if not a or not b or a["cp"] is None or b["cp"] is None:
                continue
            mover_white = self.board_at(i).turn == chess.WHITE
            wa, wb = _win(a["cp"]), _win(b["cp"])
            drop = (wa - wb) if mover_white else (wb - wa)
            for limit, sym in MARKS:
                if drop >= limit and a.get("best") != self.sans[i]:
                    out[i] = sym
                    break
        return out

    async def close(self) -> None:
        await self._cancel_pass()
        if self._idle_task:
            self._idle_task.cancel()
        if self.engine is not None:
            await self.engine.quit()
            self.engine = None

    # ---- views ------------------------------------------------------------------------------------------
    def pgn(self) -> str:
        game = chess.pgn.Game()
        if self.start != chess.Board():
            game.setup(self.start)
        for k, v in self.headers.items():
            if k not in ("FEN", "SetUp"):
                game.headers[k] = v
        node: chess.pgn.GameNode = game
        for i, mv in enumerate(self.moves):
            node = node.add_variation(mv)
            e = self.evals.get(i + 1)
            if e and e["cp"] is not None:
                node.set_eval(chess.engine.PovScore(_score(e["cp"]), chess.WHITE), e["depth"] or None)
        return str(game)

    def line_list(self) -> list[dict]:
        b = self.board
        out = []
        for k in sorted(self.lines):
            info = self.lines[k]
            san, bb = [], b.copy(stack=False)
            for u in info.get("pv", [])[:14]:
                try:
                    mv = chess.Move.from_uci(u)
                except ValueError:
                    break
                if mv not in bb.legal_moves:
                    break
                san.append(bb.san(mv))
                bb.push(mv)
            wcp = info.get("white_cp")
            out.append({"k": k, "cp": _clip(wcp), "score": fmt_score(wcp), "depth": info.get("depth", 0),
                        "sd": info.get("seldepth", 0), "move": (info.get("pv") or [""])[0], "san": san,
                        "first_move": b.fullmove_number, "white_first": b.turn == chess.WHITE})
        return out

    def state(self) -> dict:
        b = self.board
        last = self.moves[self.cursor - 1] if self.cursor else None
        check = chess.square_name(b.king(b.turn)) if b.is_check() and b.king(b.turn) is not None else None
        lines = self.line_list()
        top = lines[0]["cp"] if lines else (self.evals.get(self.cursor) or {}).get("cp")
        out = b.outcome(claim_draw=True)
        marks = self.marks()
        return {
            "version": self.version, "title": self.title, "headers": self.headers,
            "fen": b.fen(), "board_fen": b.board_fen(), "turn": "white" if b.turn == chess.WHITE else "black",
            "last": [chess.square_name(last.from_square), chess.square_name(last.to_square)] if last else None,
            "check": check, "cursor": self.cursor, "total": len(self.moves),
            "sans": self.sans, "start_white": self.start.turn == chess.WHITE,
            "start_move": self.start.fullmove_number,
            "legal": [m.uci() for m in b.legal_moves],
            "outcome": (out.result() + " " + out.termination.name.lower().replace("_", " ")) if out else "",
            "enabled": self.enabled, "multipv": self.multipv, "engine": self.engine_name,
            "searching": self.searching_key is not None, "lines": lines,
            "live": {"depth": self.live.get("depth"), "nodes": self.live.get("nodes", 0), "nps": self.live.get("nps", 0),
                     "time": self.live.get("time", 0)},
            "eval": _clip(top), "eval_text": fmt_score(top), "win": round(_win(top), 1) if top is not None else None,
            "evals": [{"ply": i, "cp": _clip(e["cp"]), "best": e["best"],
                       "text": ("1-0" if e["cp"] > 0 else "0-1") if e["cp"] is not None and abs(e["cp"]) == MATE_CP
                       else fmt_score(e["cp"]),
                       "mark": marks.get(i - 1, "")} for i, e in sorted(self.evals.items())],
            "marks": {str(k): v for k, v in marks.items()},
            "pass": {"done": self.pass_done, "total": self.pass_total, "running": self._pass_running()},
            "error": self.error, "flip": self.flip, "pgn": self.pgn(),
        }


def _score(white_cp: int) -> chess.engine.Score:
    if abs(white_cp) >= MATE_CP - 1000:
        n = MATE_CP - abs(white_cp)
        return chess.engine.Mate(n if white_cp > 0 else -n)
    return chess.engine.Cp(white_cp)
