"""One Lichess game: stream handling, engine driving (with pondering), draw/resign policy, PGN."""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import time
import traceback
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import chess
import chess.engine
import chess.pgn
import httpx

from . import __version__
from .engine import EngineError, UciEngine
from .lichess import LichessError

if TYPE_CHECKING:
    from .manager import BotManager

DRAW_STATUSES = {"draw", "stalemate"}
MATE_CP = 100000


def win_percent(cp: float) -> float:
    """Lichess' win-chance model (0..100) for a centipawn score."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def score_cp(info: dict) -> int | None:
    """Score as a single int (mates mapped to +-MATE_CP - n)."""
    if "mate" in info:
        m = info["mate"]
        return MATE_CP - abs(m) if m > 0 else -(MATE_CP - abs(m))
    return info.get("cp")


def fmt_score(cp: int | None, mate: int | None = None) -> str:
    if mate is not None:
        return f"#{mate}" if mate > 0 else f"#-{abs(mate)}"
    if cp is None:
        return "?"
    if abs(cp) >= MATE_CP - 1000:
        n = MATE_CP - abs(cp)
        return f"#{n}" if cp > 0 else f"#-{n}"
    return f"{cp / 100:+.2f}"


def fmt_ms(ms: float | None) -> str:
    if ms is None:
        return "--:--"
    ms = max(0, ms)
    s = ms / 1000
    if s < 10:
        return f"0:{s:04.1f}"
    s = int(s)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class LagTracker:
    """Network lag per move as Lichess sees it, shared by all games of the bot.

    Lichess charges our clock from the moment it sends the opponent's move until our move arrives.
    lag = (clock before + increment - clock after) - (our own time from receiving the move to sending ours),
    i.e. the two network trips (plus server time), which the engine never sees. Smoothed like TCP's RTT."""

    def __init__(self) -> None:
        self.mean: float | None = None
        self.dev = 0.0
        self.samples: deque[int] = deque(maxlen=30)

    def add(self, ms: float) -> None:
        ms = max(0.0, ms)
        self.samples.append(int(ms))
        if self.mean is None:
            self.mean, self.dev = ms, ms / 2
        else:
            self.dev = 0.75 * self.dev + 0.25 * abs(self.mean - ms)
            self.mean = 0.75 * self.mean + 0.25 * ms

    def overhead(self, base: int) -> int:
        """Move Overhead for the engine: the configured base (local work) + typical lag, 10 ms steps."""
        return int(base + round((self.mean or 0) / 10) * 10)

    def margin(self) -> int:
        """Extra time kept back for lag spikes (taken off our clock once, not per future move)."""
        return int(min(1500, 3 * self.dev))

    def text(self) -> str:
        if self.mean is None:
            return "lag: not measured yet"
        return f"lag {self.mean:.0f}±{self.dev:.0f} ms (last {self.samples[-1]} ms)"


@dataclass
class EvalPoint:
    ply: int                 # plies played before our move
    san: str                 # our move
    white_cp: int | None     # white point of view (mates as +-MATE_CP - n)
    depth: int
    seldepth: int
    nodes: int
    nps: int
    time_ms: int
    tbhits: int
    hashfull: int
    pv_san: str
    ponderhit: bool


class GameSession:
    local = False                                   # LocalGame (local.py): played on this device, not on Lichess

    def __init__(self, mgr: "BotManager", game_id: str, start_event: dict | None = None):
        self.mgr = mgr
        self.li = mgr.li
        self.cfg = mgr.cfg
        self.id = game_id
        self.url = f"{mgr.web_url}/{game_id}"
        self.status = "starting"
        self.over = False
        self.winner: str | None = None
        self.color: bool = chess.WHITE
        self.white: dict = {}
        self.black: dict = {}
        self.rated = False
        self.speed = "?"
        self.variant = "standard"
        self.initial_fen = "startpos"
        self.clock_initial: int | None = None     # ms
        self.clock_inc: int | None = None
        self.board = chess.Board()
        self.start_board = chess.Board()
        self.moves: list[str] = []
        self.sans: list[str] = []
        self.wtime: int | None = None
        self.btime: int | None = None
        self.winc = 0
        self.binc = 0
        self.state_at = time.monotonic()
        self.wdraw = False
        self.bdraw = False
        self.opp_gone = False
        self.claim_at: float | None = None
        self.chat: deque[tuple[str, str, str, str]] = deque(maxlen=200)
        self.raw: deque[tuple[float, str, str]] = deque(maxlen=3000)
        self.raw_total = 0
        self.debug_io = mgr.cfg.get("log.level") == "debug"
        # engine / search state
        self.engine: UciEngine | None = None
        self.search_kind: str | None = None        # "think" | "ponder" | None
        self.search_started = 0.0
        self.search_ply = 0
        self.ponder_base: list[str] | None = None  # moves (incl. our move) the ponder search starts from
        self.ponder_move: str | None = None
        self.pending_ponder: tuple[list[str], str] | None = None
        self.ponder_hits = 0
        self.ponder_total = 0
        self.rows: list[dict] = []                 # iterations of the current / last search
        self.live: dict[str, Any] = {}              # latest info (any kind)
        self.evals: list[EvalPoint] = []
        self.our_scores: list[int] = []            # our POV, one per own move
        self.handled_ply = -1
        self.draw_seen_ply = -1
        self.offer_draw_next = False
        self.started = time.time()
        self.ended: float | None = None
        self.turn_since = time.monotonic()
        self.greeted = False
        self.changed = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.pgn_path: str | None = None
        self.terminated_by_us = ""
        self._takeback_seen = False
        self._lag_probe: tuple[int, float, int, float] | None = None   # (ply, t received, clock before, t sent)
        self.limit_elo: int | None = None          # rating limit for this game (None = full strength)
        self._limit_pending: int | None = None     # requested limit, applied by the engine driver (0 = off)
        self.overhead_now: int | None = None
        self.ponder_ok = True                      # False: never ponder (e.g. untimed local games)
        self.preset_limit = False                  # limit_elo came with the challenge link
        self._last_len = -1
        if start_event:
            opp = start_event.get("opponent") or {}
            self.color = chess.WHITE if start_event.get("color") == "white" else chess.BLACK
            me = {"name": mgr.username, "id": mgr.user_id, "title": "BOT"}
            other = {"name": opp.get("username"), "id": opp.get("id"), "rating": opp.get("rating")}
            self.white, self.black = (me, other) if self.color == chess.WHITE else (other, me)
            self.speed = start_event.get("speed", self.speed)
            self.rated = bool(start_event.get("rated"))

    # ---- helpers used by the UI ----------------------------------------------
    @property
    def me(self) -> dict:
        return self.white if self.color == chess.WHITE else self.black

    @property
    def opponent(self) -> dict:
        return self.black if self.color == chess.WHITE else self.white

    def player_label(self, p: dict) -> str:
        if p.get("aiLevel"):
            return f"Stockfish level {p['aiLevel']}"
        title = f"{p['title']} " if p.get("title") else ""
        rating = f" ({p['rating']}{'?' if p.get('provisional') else ''})" if p.get("rating") else ""
        return f"{title}{p.get('name') or ('?' if p.get('id') else 'Anonymous')}{rating}"

    @property
    def my_turn(self) -> bool:
        return self.status == "started" and self.board.turn == self.color

    @property
    def tc(self) -> str:
        if self.clock_initial is None:
            return "∞"
        base = self.clock_initial / 60000
        return f"{base:g}+{(self.clock_inc or 0) // 1000}"

    def clock(self, color: bool) -> float | None:
        ms = self.wtime if color == chess.WHITE else self.btime
        if ms is None:
            return None
        if (self.status == "started" and self.board.turn == color and len(self.moves) >= 2):
            ms -= (time.monotonic() - self.state_at) * 1000
        return ms

    def last_eval_white(self) -> int | None:
        return self.evals[-1].white_cp if self.evals else None

    def current_eval_white(self) -> int | None:
        """Latest engine score (think or ponder), white point of view."""
        if self.rows:
            s = score_cp(self.rows[-1])
            if s is not None:
                return s if self.color == chess.WHITE else -s
        return self.last_eval_white()

    def result(self) -> str:
        if self.winner == "white":
            return "1-0"
        if self.winner == "black":
            return "0-1"
        if self.status in DRAW_STATUSES or (self.over and self.status not in ("aborted", "noStart")
                                            and self.winner is None and self.status != "started"):
            return "1/2-1/2"
        return "*"

    def outcome_for_me(self) -> str:
        r = self.result()
        if r == "1/2-1/2":
            return "draw"
        if r == "*":
            return self.status
        mine = "1-0" if self.color == chess.WHITE else "0-1"
        return "win" if r == mine else "loss"

    def log(self, level: str, msg: str) -> None:
        self.mgr.log(level, f"[{self.id}] {msg}")

    def _raw(self, direction: str, text: str) -> None:
        self.raw.append((time.time(), direction, text))
        self.raw_total += 1
        if self.debug_io:
            self.mgr.file_log.debug(f"[{self.id}] {direction} {text}")

    # ---- main --------------------------------------------------------------
    def start(self) -> None:
        self.tasks.append(asyncio.create_task(self._run(), name=f"game-{self.id}"))

    async def _run(self) -> None:
        player = asyncio.create_task(self._player())
        watchdog = asyncio.create_task(self._watchdog())
        self.tasks += [player, watchdog]
        try:
            await self._stream_loop()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log("error", "game loop crashed:\n" + traceback.format_exc())
        finally:
            self.over = True
            self.changed.set()
            for t in (player, watchdog):
                t.cancel()
            await self._finish()

    async def _stream_loop(self) -> None:
        failures = 0
        while not self.over:
            try:
                async for msg in self.li.game_stream(self.id):
                    failures = 0
                    if msg:
                        await self._handle(msg)
                    if self.over:
                        return
                # stream closed by the server
                if self.status not in ("started", "starting", "created"):
                    self.over = True
                    return
            except LichessError as e:
                if e.status in (400, 404):
                    self.log("warning", f"game stream unavailable ({e.status}); leaving the game")
                    self.over = True
                    return
                self.log("warning", f"game stream error: {e}")
            except (httpx.HTTPError, OSError) as e:
                self.log("warning", f"game stream disconnected: {e!r}")
            failures += 1
            if failures > 30:
                self.log("error", "giving up on this game stream")
                return
            await asyncio.sleep(min(1.0 * failures, 5.0))

    async def _handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "gameFull":
            await self._on_full(msg)
        elif t == "gameState":
            self._on_state(msg)
        elif t == "chatLine":
            await self._on_chat(msg)
        elif t == "opponentGone":
            self.opp_gone = bool(msg.get("gone"))
            secs = msg.get("claimWinInSeconds")
            self.claim_at = time.monotonic() + secs if self.opp_gone and secs is not None else None
            self.log("info", "opponent left the game" if self.opp_gone else "opponent is back")
        self.changed.set()

    async def _on_full(self, msg: dict) -> None:
        self.white = msg.get("white") or {}
        self.black = msg.get("black") or {}
        my = self.mgr.user_id
        self.color = chess.WHITE if (self.white.get("id") or "").lower() == my else chess.BLACK
        self.rated = bool(msg.get("rated"))
        self.speed = msg.get("speed", self.speed)
        self.variant = (msg.get("variant") or {}).get("key", "standard")
        self.initial_fen = msg.get("initialFen") or "startpos"
        clock = msg.get("clock") or {}
        self.clock_initial = clock.get("initial")
        self.clock_inc = clock.get("increment")
        self.start_board = chess.Board() if self.initial_fen == "startpos" else chess.Board(self.initial_fen)
        self.board = self.start_board.copy()
        self.moves, self.sans = [], []
        self._on_state(msg.get("state") or {})
        if self.engine is None and not self.over:
            await self._start_engine()
        if not self.greeted:
            self.greeted = True
            if len(self.moves) < 2 and self.status == "started":
                await self._say_hello()

    def _on_state(self, st: dict) -> None:
        moves = (st.get("moves") or "").split()
        if moves[:len(self.moves)] != self.moves:        # takeback: rebuild
            self.board = self.start_board.copy()
            self.moves, self.sans = [], []
        for uci in moves[len(self.moves):]:
            try:
                mv = chess.Move.from_uci(uci)
                self.sans.append(self.board.san(mv))
                self.board.push(mv)
                self.moves.append(uci)
            except (ValueError, AssertionError):
                self.log("error", f"illegal move from server: {uci}")
                break
        for k in ("wtime", "btime"):
            if k in st:
                setattr(self, k, st[k])
        self.winc = st.get("winc", self.winc)
        self.binc = st.get("binc", self.binc)
        self.state_at = time.monotonic()
        self._measure_lag()
        self.wdraw = bool(st.get("wdraw"))
        self.bdraw = bool(st.get("bdraw"))
        prev = self.status
        self.status = st.get("status", self.status)
        self.winner = st.get("winner", self.winner)
        if len(moves) != self._last_len:
            self._last_len = len(moves)
            self.turn_since = time.monotonic()
        if self.status != "started" and prev in ("started", "starting", "created"):
            self.over = True
            self.ended = time.time()
            self.log("info", f"game over: {self.status} {self.result()} ({self.outcome_for_me()})")
        opp_takeback = bool(st.get("btakeback" if self.color == chess.WHITE else "wtakeback"))
        if opp_takeback and not self._takeback_seen and not self.over:
            asyncio.create_task(self._safe(self.li.takeback(self.id, False), "decline takeback"))
        self._takeback_seen = opp_takeback

    async def _safe(self, coro, what: str) -> bool:
        try:
            await coro
            return True
        except Exception as e:
            self.log("warning", f"{what} failed: {e}")
            return False

    # ---- engine ------------------------------------------------------------
    async def _start_engine(self) -> None:
        ecfg = self.cfg.get("engine")
        opts = dict(ecfg["options"])
        opts["Ponder"] = bool(ecfg["ponder"])
        self.engine = UciEngine(self.cfg.resolve(ecfg["path"]), opts, on_line=self._raw)
        try:
            await self.engine.start()
            if self.limit_elo:
                await self.engine.setoption("UCI_LimitStrength", True)
                await self.engine.setoption("UCI_Elo", self.limit_elo)
            await self.engine.new_game()
        except Exception as e:
            self.log("error", f"engine failed to start: {e}")
            self.engine = None
            if self.status in ("started", "starting", "created"):
                if len(self.moves) < 2:
                    await self._safe(self.li.abort(self.id), "abort")
                else:
                    await self._safe(self.li.resign(self.id), "resign")

    def _measure_lag(self) -> None:
        probe = self._lag_probe
        if probe is None or len(self.moves) <= probe[0]:
            return
        self._lag_probe = None
        ply, t_recv, before, t_sent = probe
        after = self.wtime if self.color == chess.WHITE else self.btime
        inc = self.winc if self.color == chess.WHITE else self.binc
        if after is None or ply < 2:
            return
        lag = (before + (inc or 0) - after) - (t_sent - t_recv) * 1000
        if -100 < lag < 10000:          # outside: time given by the opponent, takeback, berserk ...
            self.mgr.lag.add(lag)
            self._raw("#", f"lag {lag:.0f} ms -> {self.mgr.lag.text()}")

    def _base_overhead(self) -> int:
        try:
            return int(self.cfg.get("engine.options").get("Move Overhead", 100))
        except (TypeError, ValueError, AttributeError):
            return 100

    async def _update_overhead(self) -> None:
        """Adapt the engine's Move Overhead to the measured lag (only while the engine is idle)."""
        eng = self.engine
        if eng is None or eng.searching:
            return
        base = self._base_overhead()
        want = self.mgr.lag.overhead(base) if self.cfg.get("engine.lag_compensation") else base
        if self.overhead_now is None or abs(want - self.overhead_now) >= 20:
            if await eng.setoption("Move Overhead", want):
                self.overhead_now = want

    def _position_cmd(self, moves: list[str]) -> str:
        base = "startpos" if self.initial_fen == "startpos" else f"fen {self.initial_fen}"
        return f"position {base} moves {' '.join(moves)}" if moves else f"position {base}"

    def _clock_args(self, elapsed_ms: int = 0) -> str:
        w, b = self.wtime or 0, self.btime or 0
        keep = elapsed_ms + (self.mgr.lag.margin() if self.cfg.get("engine.lag_compensation") else 0)
        if self.color == chess.WHITE:
            w = max(1, w - keep)
        else:
            b = max(1, b - keep)
        return f"wtime {w} btime {b} winc {self.winc} binc {self.binc}"

    def _on_info(self, info: dict) -> None:
        info["t"] = time.monotonic()
        self.live.update({k: v for k, v in info.items() if k != "pv"})
        if "pv" in info and ("cp" in info or "mate" in info) and info.get("multipv", 1) == 1:
            if info.get("bound"):
                return
            self.rows.append(info)
            if len(self.rows) > 120:
                del self.rows[:-120]

    def _new_search(self, kind: str, ply: int) -> None:
        self.search_kind = kind
        self.search_started = time.monotonic()
        self.search_ply = ply
        self.rows = []
        self.live = {}

    async def _player(self) -> None:
        while not self.over:
            await self.changed.wait()
            self.changed.clear()
            if self.over or self.engine is None:
                continue
            if self._limit_pending is not None:
                await self._apply_limit()
            try:
                if self.my_turn:
                    if len(self.moves) != self.handled_ply:
                        self.handled_ply = len(self.moves)
                        await self._think()
                elif (self.status == "started" and self.pending_ponder
                      and self.pending_ponder[0] == self.moves and not self.engine.searching):
                    await self._start_ponder()
                await self._maybe_answer_draw()
            except EngineError as e:
                self.log("error", f"engine error: {e}; restarting engine")
                await self._restart_engine()
            except LichessError as e:
                self.log("warning", f"{e}")

    # ---- rating limit ------------------------------------------------------------
    def opponent_is_bot(self) -> bool:
        p = self.opponent
        return p.get("title") == "BOT" or bool(p.get("aiLevel"))

    def limit_levels(self) -> tuple[int, int, int]:
        s = self.cfg.get("strength")
        lo, hi = int(s["min"]), int(s["max"])
        return lo, hi, max(1, int(s["step"]))

    def limit_eligible(self) -> str | None:
        """None when this game may be played with a rating limit, else the reason why not."""
        s = self.cfg.get("strength")
        if not s["accept"]:
            return "rating limits are switched off"
        if s["modes"] == "casual" and self.rated:
            return "rating limits are for casual games only"
        if s["modes"] == "rated" and not self.rated:
            return "rating limits are for rated games only"
        bot = self.opponent_is_bot()
        if s["opponents"] == "human" and bot:
            return "rating limits are for human opponents only"
        if s["opponents"] == "bot" and not bot:
            return "rating limits are for bots only"
        return None

    def _opponent_has_moved(self) -> bool:
        opp_first = 0 if self.start_board.turn != self.color else 1   # ply of the opponent's first move
        return len(self.moves) > opp_first

    def diff_request(self, arg: str) -> str:
        """Handle `!diff <rating>` from the opponent; returns the chat reply."""
        lo, hi, step = self.limit_levels()
        levels = f"{lo}-{hi}" + (f" in steps of {step}" if step > 1 else "")
        why = self.limit_eligible()
        if why:
            return f"Sorry, {why}."
        if not arg:
            now = f"now {self.limit_elo}" if self.limit_elo else "now full strength"
            return f"Type !diff <rating> ({levels}) before your first move to play me at that rating ({now})."
        if self.engine is not None and "uci_elo" not in self.engine.declared:
            return "Sorry, my engine cannot play at a rating limit."
        if self._opponent_has_moved():
            return "Too late: the rating limit has to be set before your first move."
        try:
            elo = int(arg)
        except ValueError:
            return f"Usage: !diff <rating>, e.g. !diff 1500 ({levels})."
        if not lo <= elo <= hi or (elo - lo) % step:
            return f"There is no {elo} level: choose {levels}."
        self._limit_pending = elo
        self.changed.set()
        self.log("info", f"opponent asked for rating limit {elo}")
        return f"OK! This game I play at about {elo} rating. Good luck!"

    def set_limit(self, elo: int | None) -> None:
        """Operator override (console `diff` command): any time, 0/None = full strength."""
        self._limit_pending = elo or 0
        self.changed.set()

    async def _apply_limit(self) -> None:
        want, self._limit_pending = self._limit_pending, None
        eng = self.engine
        if eng is None or want is None:
            return
        if eng.searching:
            await eng.stop()
            self.search_kind = None
        self.pending_ponder = None
        if want:
            await eng.setoption("UCI_LimitStrength", True)
            await eng.setoption("UCI_Elo", want)
            self.limit_elo = want
            self.log("info", f"playing at rating limit {want}")
        else:
            await eng.setoption("UCI_LimitStrength", False)
            self.limit_elo = None
            self.log("info", "rating limit removed - full strength")
        await eng.isready()
        self.handled_ply = -1            # (re)think if it is our turn
        self.changed.set()

    async def _restart_engine(self) -> None:
        if self.engine:
            await self.engine.quit()
        self.engine = None
        await self._start_engine()
        self.handled_ply = -1
        self.pending_ponder = None
        self.changed.set()

    async def _start_ponder(self) -> None:
        assert self.engine and self.pending_ponder
        base, pmove = self.pending_ponder
        self.pending_ponder = None
        self.ponder_base, self.ponder_move = list(base), pmove
        await self._update_overhead()
        self._new_search("ponder", len(base) + 1)
        await self.engine.go(self._position_cmd(base + [pmove]), "go ponder " + self._clock_args(), self._on_info)

    async def _think(self) -> None:
        eng = self.engine
        assert eng is not None
        ply = len(self.moves)
        hit = False
        if eng.searching and self.search_kind == "ponder":
            self.ponder_total += 1
            if self.ponder_base is not None and self.moves == self.ponder_base + [self.ponder_move]:
                hit = True
                self.ponder_hits += 1
                self.search_kind = "think"
                await eng.ponderhit()
            else:
                await eng.stop()
        self.pending_ponder = None
        t_recv = self.state_at
        clock_before = (self.wtime if self.color == chess.WHITE else self.btime) or 0
        if not hit:
            if eng.searching:
                await eng.stop()
            await self._update_overhead()
            self._new_search("think", ply)
            if ply < 2:
                go = f"go movetime {self.cfg.get('engine.first_move_ms')}"
            else:
                elapsed = int((time.monotonic() - self.state_at) * 1000)
                go = "go " + self._clock_args(elapsed)
            await eng.go(self._position_cmd(self.moves), go, self._on_info)
        my_ms = (self.wtime if self.color == chess.WHITE else self.btime) or 60000
        best, ponder = await eng.wait_bestmove(timeout=my_ms / 1000 + 30)
        self.search_kind = None
        if self.over or len(self.moves) != ply:
            return
        if best in ("(none)", "0000"):
            return
        try:
            mv = chess.Move.from_uci(best)
            if mv not in self.board.legal_moves:
                raise ValueError
        except ValueError:
            self.log("error", f"engine returned an illegal move {best}")
            return
        self._record_eval(best, hit)
        if self._should_resign():
            self.log("info", "resigning (score below threshold)")
            self.terminated_by_us = "resign"
            await self._safe(self.li.resign(self.id), "resign")
            return
        offer = self._should_offer_draw()
        self._lag_probe = (ply, t_recv, clock_before, time.monotonic())
        try:
            await self.li.move(self.id, best, offer_draw=offer)
        except LichessError as e:
            if e.status in (0, 429) or e.status >= 500:
                self.log("warning", f"move {best} not delivered ({e}); searching again")
                self.handled_ply = -1
                self.changed.set()
                return
            raise
        if offer:
            self.log("info", "offered a draw with the move")
        if self.cfg.get("engine.ponder") and ponder and not self.limit_elo and self.ponder_ok:
            self.pending_ponder = (self.moves + [best], ponder)
        self.changed.set()

    def _record_eval(self, best: str, hit: bool) -> None:
        info = self.rows[-1] if self.rows else {}
        s = score_cp(info)
        our = s if s is not None else (self.our_scores[-1] if self.our_scores else 0)
        self.our_scores.append(our)
        white = None if s is None else (s if self.color == chess.WHITE else -s)
        pv = info.get("pv") or []
        pv_san = ""
        try:
            b = self.board.copy()
            parts = []
            for u in pv[:8]:
                m = chess.Move.from_uci(u)
                if m not in b.legal_moves:
                    break
                parts.append(b.san(m))
                b.push(m)
            pv_san = " ".join(parts)
        except ValueError:
            pass
        san = self.board.san(chess.Move.from_uci(best))
        self.evals.append(EvalPoint(len(self.moves), san, white, info.get("depth", 0), info.get("seldepth", 0),
                                    info.get("nodes", 0), info.get("nps", 0), info.get("time", 0),
                                    info.get("tbhits", 0), info.get("hashfull", 0), pv_san, hit))

    def _should_resign(self) -> bool:
        if self.limit_elo:
            return False
        g = self.cfg.get("game")
        n = g["resign_moves"]
        if not g["resign_enabled"] or len(self.our_scores) < n or len(self.moves) < 20:
            return False
        return all(s <= g["resign_score"] for s in self.our_scores[-n:])

    def _should_offer_draw(self) -> bool:
        g = self.cfg.get("game")
        if self.offer_draw_next:
            self.offer_draw_next = False
            return True
        n = g["draw_offer_moves"]
        if self.limit_elo:
            return False
        if not g["draw_offer"] or len(self.moves) < g["draw_offer_min_ply"] or len(self.our_scores) < n:
            return False
        return all(abs(s) <= g["draw_offer_score"] for s in self.our_scores[-n:])

    async def _maybe_answer_draw(self) -> None:
        offered = self.bdraw if self.color == chess.WHITE else self.wdraw
        if not offered or self.over or self.draw_seen_ply == len(self.moves):
            return
        self.draw_seen_ply = len(self.moves)
        g = self.cfg.get("game")
        our = self.our_scores[-1] if self.our_scores else 0
        pieces = chess.popcount(self.board.occupied)
        accept = g["draw_accept"] and (our <= g["draw_accept_score"] or (abs(our) <= 5 and pieces <= 6))
        if self.limit_elo:
            accept = False               # evaluations are scrambled on purpose in rating-limited games
        self.log("info", f"opponent offers a draw; our eval {fmt_score(our)} -> {'accept' if accept else 'decline'}")
        await self._safe(self.li.draw(self.id, accept), "answer draw offer")

    # ---- timers ------------------------------------------------------------
    async def _watchdog(self) -> None:
        g = self.cfg.get("game")
        while not self.over:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            if (self.status == "started" and len(self.moves) < 2 and not self.my_turn
                    and now - self.turn_since > g["abort_seconds"]):
                self.log("info", "opponent did not move; aborting")
                self.terminated_by_us = "abort"
                await self._safe(self.li.abort(self.id), "abort")
                self.turn_since = now + 30       # do not spam
            if self.opp_gone and self.claim_at and now >= self.claim_at and g["claim_victory"]:
                self.claim_at = None
                self.log("info", "claiming victory (opponent left)")
                await self._safe(self.li.claim_victory(self.id), "claim victory")

    # ---- chat --------------------------------------------------------------
    def _fmt(self, text: str) -> str:
        return text.format(me=self.mgr.username, opponent=self.opponent.get("name") or "there",
                           engine=self.engine.name if self.engine else "my engine")

    async def _say_hello(self) -> None:
        g = self.cfg.get("game")
        if g["greeting"]:
            await self._safe(self.li.chat(self.id, "player", self._fmt(g["greeting"])), "chat")
        if g["greeting_spectators"]:
            await self._safe(self.li.chat(self.id, "spectator", self._fmt(g["greeting_spectators"])), "chat")
        if self.preset_limit and self.limit_elo:
            await self._safe(self.li.chat(self.id, "player", f"This game I play at about {self.limit_elo} rating "
                                                             f"(set by the challenge link). Have fun!"), "chat")
        elif (self.cfg.get("strength.announce") and self.limit_eligible() is None and not self._opponent_has_moved()
              and self.opponent.get("id")):          # anonymous players' chat never reaches a bot
            lo, hi, step = self.limit_levels()
            await self._safe(self.li.chat(self.id, "player",
                                          f"Want an easier game? Type !diff <rating> ({lo}-{hi}) before your first "
                                          f"move and I will play at that rating."), "chat")

    async def _on_chat(self, msg: dict) -> None:
        room, user, text = msg.get("room", "player"), msg.get("username", "?"), msg.get("text", "")
        self.chat.append((dt.datetime.now().strftime("%H:%M:%S"), room, user, text))
        if user.lower() in (self.mgr.user_id, "lichess"):
            return
        self.log("info", f"chat {room} <{user}> {text}")
        if not text.startswith("!"):
            return
        parts = text[1:].strip().split()
        if parts and parts[0].lower() == "diff":
            opp = (self.opponent.get("name") or "").lower()
            if room == "player" and user.lower() == opp:
                await self._safe(self.li.chat(self.id, room, self.diff_request(parts[1] if len(parts) > 1 else "")),
                                 "chat")
            return
        if not self.cfg.get("game.chat_commands"):
            return
        cmd = text[1:].strip().lower()
        reply = None
        if cmd == "help":
            reply = "Commands: !eval, !name, !engine" + (", !diff <rating>" if self.limit_eligible() is None else "")
        elif cmd in ("name", "engine"):
            eng = self.engine
            who = f"{eng.name}" + (f" by {eng.author}" if eng and eng.author else "") if eng else "a UCI engine"
            reply = f"I'm {self.mgr.username}: {who}, playing through ClaudyBot {__version__}."
        elif cmd == "eval":
            if self.limit_elo:
                reply = f"Rating-limited game ({self.limit_elo}): my evaluation is scrambled on purpose."
            elif room == "player" and not self.over:
                reply = "I don't tell that to my opponent, sorry."
            else:
                e = self.evals[-1] if self.evals else None
                if e and e.white_cp is not None:
                    our = e.white_cp if self.color == chess.WHITE else -e.white_cp
                    reply = (f"My eval: {fmt_score(our)} (depth {e.depth}), win chance "
                             f"{win_percent(max(-2000, min(2000, our))):.0f}%")
                else:
                    reply = "No evaluation yet."
        if reply:
            await self._safe(self.li.chat(self.id, room, reply), "chat")

    # ---- user actions --------------------------------------------------------
    async def resign(self) -> None:
        self.terminated_by_us = "resign"
        await self.li.resign(self.id)

    async def abort(self) -> None:
        self.terminated_by_us = "abort"
        await self.li.abort(self.id)

    async def draw(self) -> None:
        """Offer (or accept) a draw right now."""
        await self.li.draw(self.id, True)

    async def say(self, text: str, room: str = "player") -> None:
        await self.li.chat(self.id, room, text)

    # ---- end -------------------------------------------------------------------
    async def _finish(self) -> None:
        if self.ended is None:
            self.ended = time.time()
        if self.engine:
            await self.engine.quit()
        g = self.cfg.get("game")
        if self.status not in ("aborted", "noStart", "started", "starting") and g["goodbye"]:
            await self._safe(self.li.chat(self.id, "player", self._fmt(g["goodbye"])), "chat")
        try:
            self.save_pgn()
        except Exception as e:
            self.log("warning", f"could not save PGN: {e}")
        self.mgr.on_game_end(self)

    def pgn(self) -> chess.pgn.Game:
        game = chess.pgn.Game()
        if self.initial_fen != "startpos":
            game.setup(self.start_board)
        h = game.headers
        h["Event"] = f"{'Rated' if self.rated else 'Casual'} {self.speed} game"
        h["Site"] = self.url
        h["Date"] = dt.datetime.fromtimestamp(self.started).strftime("%Y.%m.%d")
        h["White"] = self.white.get("name") or f"AI level {self.white.get('aiLevel', '?')}"
        h["Black"] = self.black.get("name") or f"AI level {self.black.get('aiLevel', '?')}"
        if self.white.get("rating"):
            h["WhiteElo"] = str(self.white["rating"])
        if self.black.get("rating"):
            h["BlackElo"] = str(self.black["rating"])
        h["Result"] = self.result()
        if self.clock_initial is not None:
            h["TimeControl"] = f"{self.clock_initial // 1000}+{(self.clock_inc or 0) // 1000}"
        h["Termination"] = self.status
        h["Annotator"] = f"ClaudyBot {__version__}"
        if self.limit_elo:
            h["ClaudyRatingLimit"] = str(self.limit_elo)
        evals = {e.ply: e for e in self.evals}
        node: chess.pgn.GameNode = game
        for i, uci in enumerate(self.moves):
            node = node.add_variation(chess.Move.from_uci(uci))
            e = evals.get(i)
            if e and e.white_cp is not None:
                node.set_eval(chess.engine.PovScore(_to_score(e.white_cp), chess.WHITE), e.depth)
                node.comment += f" {e.time_ms / 1000:.1f}s {e.nodes} nodes"
        return game

    def save_pgn(self) -> None:
        if not self.moves:
            return
        d = self.cfg.resolve(self.cfg.get("game.pgn_dir"))
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{dt.datetime.fromtimestamp(self.started):%Y-%m-%d}.pgn"
        with open(path, "a", encoding="utf-8") as f:
            print(self.pgn(), file=f, end="\n\n")
        self.pgn_path = str(path)

    def cancel(self) -> None:
        for t in self.tasks:
            t.cancel()


def _to_score(white_cp: int) -> chess.engine.Score:
    if abs(white_cp) >= MATE_CP - 1000:
        n = MATE_CP - abs(white_cp)
        return chess.engine.Mate(n if white_cp > 0 else -n)
    return chess.engine.Cp(white_cp)
