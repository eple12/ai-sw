"""Games against the engine on this device: no Lichess, no network.

A LocalBoard is the referee (legal moves, clocks, results). It speaks the part of the Lichess Bot API a
GameSession uses (game_stream, move, resign, abort, draw, ...), so a LocalGame - a GameSession - drives
the engine exactly as in an online game: same time management, pondering, rating limits, evaluations,
dashboards and PGN files. The person at the device moves through LocalGame.human_move().
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, AsyncIterator

import chess

from .game import GameSession
from .lichess import LichessError

if TYPE_CHECKING:
    from .manager import BotManager


def speed_name(limit_s: int | None, inc_s: int) -> str:
    if limit_s is None:
        return "untimed"
    est = limit_s + 40 * inc_s
    return ("ultraBullet" if est < 30 else "bullet" if est < 180 else "blitz" if est < 480
            else "rapid" if est < 1500 else "classical")


def parse_move(board: chess.Board, text: str) -> chess.Move:
    """SAN (e4, Nf3, exd5, O-O, e8=Q) or UCI (e2e4, e7e8q); raises ValueError."""
    t = text.strip()
    try:
        mv = chess.Move.from_uci(t.lower())
        if mv in board.legal_moves:
            return mv
    except ValueError:
        pass
    try:
        return board.parse_san(t)
    except ValueError:
        pass
    raise ValueError(f"{text} is not a legal move here")


class LocalBoard:
    """Referee of one local game (engine vs the person at this device)."""

    def __init__(self, gid: str, engine_color: bool, limit_ms: int | None, inc_ms: int, fen: str | None,
                 engine: dict, human: dict):
        self.id = gid
        self.engine_color = engine_color
        self.board = chess.Board(fen) if fen else chess.Board()
        self.initial_fen = self.board.fen() if fen else "startpos"
        self.timed = limit_ms is not None
        self.initial = limit_ms
        self.inc = inc_ms if self.timed else 0
        self.wtime = self.btime = limit_ms
        self.last = time.monotonic()
        self.status = "started"
        self.winner: str | None = None
        self.wdraw = self.bdraw = False
        self.players = {engine_color: engine, not engine_color: human}
        self.subs: list[asyncio.Queue] = []
        self.created = int(time.time() * 1000)
        self._flag_task = asyncio.create_task(self._flag()) if self.timed else None

    # ---- state ----------------------------------------------------------------------------------
    @property
    def moves(self) -> str:
        return " ".join(m.uci() for m in self.board.move_stack)

    def running_clock(self) -> bool:     # as on Lichess: the clocks start after both first moves
        return self.timed and len(self.board.move_stack) >= 2 and self.status == "started"

    def state(self) -> dict:
        st = {"type": "gameState", "moves": self.moves, "status": self.status,
              "winc": self.inc, "binc": self.inc, "wdraw": self.wdraw, "bdraw": self.bdraw}
        if self.timed:
            st["wtime"], st["btime"] = int(self.wtime), int(self.btime)
        if self.winner:
            st["winner"] = self.winner
        return st

    def full(self) -> dict:
        speed = speed_name(None if self.initial is None else self.initial // 1000, self.inc // 1000)
        return {"type": "gameFull", "id": self.id, "rated": False, "speed": speed, "createdAt": self.created,
                "variant": {"key": "standard", "name": "Standard", "short": "Std"},
                "clock": {"initial": self.initial, "increment": self.inc} if self.timed else None,
                "white": self.players[chess.WHITE], "black": self.players[chess.BLACK],
                "initialFen": self.initial_fen, "state": self.state()}

    def _broadcast(self, msg: dict | None) -> None:
        for q in self.subs:
            q.put_nowait(msg)

    def finish(self, status: str, winner: bool | None) -> None:
        if self.status != "started":
            return
        self.status = status
        self.winner = None if winner is None else ("white" if winner == chess.WHITE else "black")
        self._broadcast(self.state())
        self._broadcast(None)
        if self._flag_task and self._flag_task is not asyncio.current_task():
            self._flag_task.cancel()

    def push(self, mv: chess.Move) -> str | None:
        """Play a legal move for the side to move; returns an error or None."""
        if self.status != "started":
            return "the game is over"
        if mv not in self.board.legal_moves:
            return f"illegal move {mv.uci()}"
        now = time.monotonic()
        mover = self.board.turn
        if self.running_clock():
            left = (self.wtime if mover == chess.WHITE else self.btime) - (now - self.last) * 1000
            if left <= 0:
                self._flagged(mover)
                return "out of time"
            if mover == chess.WHITE:
                self.wtime = left
            else:
                self.btime = left
        if self.timed and len(self.board.move_stack) >= 1:
            if mover == chess.WHITE:
                self.wtime += self.inc
            else:
                self.btime += self.inc
        self.board.push(mv)
        self.last = now
        if mover == chess.WHITE:        # a move declines the other side's draw offer
            self.bdraw = False
        else:
            self.wdraw = False
        out = self.board.outcome(claim_draw=True)
        if out is None:
            self._broadcast(self.state())
        elif out.termination == chess.Termination.CHECKMATE:
            self.finish("mate", out.winner)
        elif out.termination == chess.Termination.STALEMATE:
            self.finish("stalemate", None)
        else:
            self.finish("draw", None)
        return None

    def _flagged(self, side: bool) -> None:
        if side == chess.WHITE:
            self.wtime = 0
        else:
            self.btime = 0
        # no mating material left for the other side: a draw, as on Lichess
        self.finish("outoftime", None if not self._can_mate(not side) else not side)

    def _can_mate(self, color: bool) -> bool:
        b = self.board
        return not b.has_insufficient_material(color)

    async def _flag(self) -> None:
        while self.status == "started":
            await asyncio.sleep(0.1)
            if not self.running_clock():
                continue
            side = self.board.turn
            left = (self.wtime if side == chess.WHITE else self.btime) - (time.monotonic() - self.last) * 1000
            if left <= 0:
                self._flagged(side)

    def clock_now(self, color: bool) -> float | None:
        if not self.timed:
            return None
        ms = self.wtime if color == chess.WHITE else self.btime
        if self.running_clock() and self.board.turn == color:
            ms -= (time.monotonic() - self.last) * 1000
        return max(0.0, ms)

    # ---- the Bot API subset used by GameSession (the engine's side) --------------------------------
    async def game_stream(self, gid: str) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.append(q)
        try:
            yield self.full()
            if self.status != "started":
                return
            while True:
                item = await q.get()
                if item is None:
                    return
                yield item
        finally:
            self.subs.remove(q)

    async def move(self, gid: str, uci: str, offer_draw: bool = False) -> None:
        if self.board.turn != self.engine_color:
            raise LichessError(400, "not the engine's turn", "local")
        err = self.push(chess.Move.from_uci(uci))
        if err:
            raise LichessError(400, err, "local")
        if offer_draw and self.status == "started":
            self._offer(self.engine_color)

    async def resign(self, gid: str) -> None:
        self.finish("resign", not self.engine_color)

    async def abort(self, gid: str) -> None:
        if len(self.board.move_stack) >= 2:
            raise LichessError(400, "the game cannot be aborted any more", "local")
        self.finish("aborted", None)

    async def draw(self, gid: str, accept: bool = True) -> None:
        human = not self.engine_color
        human_offer = self.wdraw if human == chess.WHITE else self.bdraw
        if accept and human_offer:
            self.finish("draw", None)
        elif accept:
            self._offer(self.engine_color)
        else:
            self.wdraw = self.bdraw = False
            self._broadcast(self.state())

    async def takeback(self, gid: str, accept: bool) -> None:
        pass

    async def claim_victory(self, gid: str) -> None:
        pass

    async def chat(self, gid: str, room: str, text: str) -> None:
        pass

    def _offer(self, color: bool) -> None:
        if color == chess.WHITE:
            self.wdraw = True
        else:
            self.bdraw = True
        self._broadcast(self.state())

    # ---- the person's side ----------------------------------------------------------------------
    def human_move(self, mv: chess.Move) -> str | None:
        if self.board.turn == self.engine_color:
            return "wait for the engine's move"
        return self.push(mv)

    def human_draw(self) -> str:
        """Offer a draw, or accept the engine's offer."""
        if self.status != "started":
            return "the game is over"
        engine_offer = self.wdraw if self.engine_color == chess.WHITE else self.bdraw
        if engine_offer:
            self.finish("draw", None)
            return "draw agreed"
        self._offer(not self.engine_color)
        return "draw offered"

    def human_resign(self) -> None:
        self.finish("resign", self.engine_color)

    def human_abort(self) -> str | None:
        if len(self.board.move_stack) >= 2:
            return "too late to abort - resign instead"
        self.finish("aborted", None)
        return None

    def takeback_plies(self) -> int:
        """Plies to take back so that it is the person's turn again before their last move."""
        n = len(self.board.move_stack)
        if self.status != "started" or n == 0:
            return 0
        k = 1 if self.board.turn == self.engine_color else 2
        return k if n >= k else 0

    def human_takeback(self) -> str | None:
        k = self.takeback_plies()
        if not k:
            return "nothing to take back"
        for _ in range(k):
            self.board.pop()
        self.wdraw = self.bdraw = False
        self.last = time.monotonic()
        self._broadcast(self.state())
        return None


class LocalGame(GameSession):
    """A GameSession whose server is a LocalBoard on this device."""
    local = True

    def __init__(self, mgr: "BotManager", gid: str, *, human_color: bool, limit_s: int | None, inc_s: int,
                 elo: int | None, fen: str | None, movetime_ms: int, human_name: str):
        super().__init__(mgr, gid, None)
        self.human_color = human_color
        self.color = not human_color
        self.movetime_ms = movetime_ms
        self.url = ""
        engine = {"id": mgr.user_id, "name": "Engine", "title": "BOT"}
        if elo:
            engine["rating"] = elo
        human = {"id": "local-player", "name": human_name}
        self.ref = LocalBoard(gid, self.color, None if limit_s is None else limit_s * 1000, inc_s * 1000, fen,
                              engine, human)
        self.li = self.ref                 # the engine's moves go to the local referee
        self.limit_elo = elo or None       # applied when the engine starts
        self.ponder_ok = limit_s is not None
        self.speed = speed_name(limit_s, inc_s)

    # ---- differences to an online game ------------------------------------------------------------
    async def _start_engine(self) -> None:
        await super()._start_engine()
        if self.engine:
            self.me["name"] = self.engine.name

    async def _say_hello(self) -> None:
        pass

    async def _watchdog(self) -> None:
        while not self.over:                 # no abort / claim timers: the person may take their time
            await asyncio.sleep(1.0)

    def _measure_lag(self) -> None:
        pass

    async def _update_overhead(self) -> None:
        pass

    def _clock_args(self, elapsed_ms: int = 0) -> str:
        if self.clock_initial is None:
            return f"movetime {self.movetime_ms}"
        w, b = self.wtime or 0, self.btime or 0
        if self.color == chess.WHITE:
            w = max(1, w - elapsed_ms)
        else:
            b = max(1, b - elapsed_ms)
        return f"wtime {w} btime {b} winc {self.winc} binc {self.binc}"

    def clock(self, color: bool) -> float | None:
        return self.ref.clock_now(color)

    def _on_state(self, st: dict) -> None:
        back = len((st.get("moves") or "").split()) < len(self.moves)
        super()._on_state(st)
        if back:                              # the person took moves back
            n = len(self.moves)
            self.evals = [e for e in self.evals if e.ply < n]
            self.our_scores = self.our_scores[:len(self.evals)]
            self.handled_ply = -1
            self.pending_ponder = None
            self.draw_seen_ply = -1
            if self.engine and self.engine.searching:
                asyncio.create_task(self._safe(self.engine.stop(), "stop search"))

    # ---- the person's actions (console / dashboard) --------------------------------------------------
    @property
    def human_turn(self) -> bool:
        return self.status == "started" and not self.over and self.board.turn == self.human_color

    def legal_moves(self) -> list[str]:
        return [m.uci() for m in self.board.legal_moves] if self.human_turn else []

    def human_move(self, text: str) -> str:
        """Returns an error message, or '' when the move was played."""
        if self.over:
            return "the game is over"
        if not self.human_turn:
            return "wait for the engine's move"
        try:
            mv = parse_move(self.ref.board, text)
        except ValueError as e:
            return str(e)
        return self.ref.human_move(mv) or ""

    def takeback(self) -> str:
        return self.ref.human_takeback() or ""

    async def resign(self) -> None:
        self.ref.human_resign()

    async def abort(self) -> None:
        err = self.ref.human_abort()
        if err:
            raise ValueError(err)

    async def draw(self) -> None:
        self.log("info", self.ref.human_draw())

    def outcome_for_me(self) -> str:
        """From the person's point of view (they are the 'me' of a local game in the dashboards)."""
        r = self.result()
        if r == "1/2-1/2":
            return "draw"
        if r == "*":
            return self.status
        mine = "1-0" if self.human_color == chess.WHITE else "0-1"
        return "win" if r == mine else "loss"

    def pgn(self):
        game = super().pgn()
        h = game.headers
        h["Event"] = f"Local {self.speed} game"
        h["Site"] = "ClaudyBot (this device)"
        if self.limit_elo:
            h["ClaudyRatingLimit"] = str(self.limit_elo)
        return game
