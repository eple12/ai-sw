"""Browser dashboard: the same bot, shown in a web page (real piece images, touch friendly).

A tiny HTTP server on the bot's own event loop (standard library only). The page polls two JSON
endpoints and sends console commands; nothing is pushed, so a phone that goes to sleep simply
catches up when it wakes.

    GET  /                    the page (web/index.html)
    GET  /static/<file>       web/app.js, web/app.css
    GET  /pieces/<code>.svg   piece images of ui.piece_set (wK.svg, bP.svg, ...)
    GET  /api/state?log=N     bot status, games, queue, log lines after N
    GET  /api/game/<id>?raw=N one game in detail (search table, evals, moves, raw engine I/O after N)
    POST /api/cmd             {"line": "...", "watched": "<game id>", "view": "..."} -> command output
    GET  /api/analysis        the analysis board (engine lines, moves, whole-game evaluations)
    POST /api/analysis        {"op": "load" | "move" | "goto" | "lines" | "engine" | "game" | "flip", ...}

By default it listens on 127.0.0.1 only. When it listens on the network (web.host 0.0.0.0) every
API call needs the access key (web.key, generated when empty), given once as ?key=... in the URL.
"""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import mimetypes
import secrets
import time
from html import escape as html_escape
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import chess
from rich.text import Text

from .commands import UI, Commands
from .engine import EngineError, UciEngine
from .game import MATE_CP, GameSession, fmt_score, score_cp, win_percent
from .render import pv_san, search_board

if TYPE_CHECKING:
    from .manager import BotManager

BOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BOT_DIR / "web"
ASSETS = BOT_DIR / "assets"
PIECE_CODES = {c + p for c in "wb" for p in "KQRBNP"}
STYLE_CLASSES = ("red", "green", "yellow", "cyan", "magenta", "blue", "bold", "dim")


def markup_html(s: str) -> str:
    """Rich console markup (`[red]x[/]`) -> HTML spans with the classes the page styles."""
    try:
        t = Text.from_markup(s)
    except Exception:
        return html_escape(s)
    plain = t.plain
    if not t.spans:
        return html_escape(plain)
    cls: list[set[str]] = [set() for _ in plain]
    for sp in t.spans:
        words = [{"b": "bold", "d": "dim"}.get(w, w) for w in str(sp.style).split()]
        names = {w for w in words if w in STYLE_CLASSES}
        for i in range(sp.start, min(sp.end, len(plain))):
            cls[i] |= names
    out, i = [], 0
    while i < len(plain):
        j = i
        while j < len(plain) and cls[j] == cls[i]:
            j += 1
        chunk = html_escape(plain[i:j])
        out.append(f'<span class="{" ".join(sorted(cls[i]))}">{chunk}</span>' if cls[i] else chunk)
        i = j
    return "".join(out)


def _player(g: GameSession, p: dict) -> dict:
    return {"name": p.get("name") or ("Stockfish" if p.get("aiLevel") else "?"),
            "title": p.get("title") or "", "rating": p.get("rating"),
            "provisional": bool(p.get("provisional")), "label": g.player_label(p)}


def _clip(cp: int | None) -> int | None:
    if cp is None:
        return None
    return max(-MATE_CP, min(MATE_CP, cp))


def game_summary(g: GameSession, idx: int) -> dict:
    b = g.board
    last = b.move_stack[-1] if b.move_stack else None
    check = chess.square_name(b.king(b.turn)) if b.is_check() and b.king(b.turn) is not None else None
    ev = g.current_eval_white()
    live = g.live
    running = None
    if g.status == "started" and len(g.moves) >= 2 and not g.over:
        running = "white" if b.turn == chess.WHITE else "black"
    ponder = ""
    if g.search_kind == "ponder" and g.ponder_move:
        ponder = g.ponder_move
        if g.ponder_base == g.moves:
            try:
                ponder = b.san(chess.Move.from_uci(g.ponder_move))
            except (ValueError, AssertionError):
                pass
    return {
        "id": g.id, "idx": idx, "url": g.url,
        "white": _player(g, g.white), "black": _player(g, g.black),
        "color": "white" if g.color == chess.WHITE else "black",
        "fen": b.board_fen(), "turn": "white" if b.turn == chess.WHITE else "black",
        "last": [chess.square_name(last.from_square), chess.square_name(last.to_square)] if last else None,
        "check": check, "ply": len(g.moves),
        "wclock": g.clock(chess.WHITE), "bclock": g.clock(chess.BLACK), "running": running,
        "tc": g.tc, "rated": g.rated, "speed": g.speed, "variant": g.variant,
        "status": g.status, "over": g.over, "result": g.result(),
        "outcome": g.outcome_for_me() if g.over else "",
        "search": g.search_kind or "", "search_s": round(time.monotonic() - g.search_started, 1)
        if g.search_kind else 0, "ponder": ponder, "my_turn": g.my_turn,
        "eval": _clip(ev), "eval_text": fmt_score(ev),
        "win": round(win_percent(max(-2000, min(2000, ev))), 1) if ev is not None else None,
        "depth": live.get("depth", g.evals[-1].depth if g.evals else 0),
        "nps": live.get("nps") or (g.evals[-1].nps if g.evals else 0),
        "wdraw": g.wdraw, "bdraw": g.bdraw, "opp_gone": g.opp_gone, "limit": g.limit_elo,
        "local": g.local, "human": ("white" if g.human_color == chess.WHITE else "black") if g.local else None,
        "legal": g.legal_moves() if g.local else [],
    }


def _replay(g: GameSession):
    b = g.start_board.copy()
    yield b, None
    for uci in g.moves:
        try:
            mv = chess.Move.from_uci(uci)
            b.push(mv)
        except ValueError:
            return
        yield b, mv


def _ply_fens(g: GameSession) -> list:
    return [b.board_fen() for b, _ in _replay(g)]


def _ply_lasts(g: GameSession) -> list:
    return [[chess.square_name(m.from_square), chess.square_name(m.to_square)] if m else None for _, m in _replay(g)]


def game_detail(g: GameSession, idx: int, raw_from: int) -> dict:
    d = game_summary(g, idx)
    rows = []
    board = None
    for r in g.rows[-40:][::-1]:
        if "san" not in r:
            board = board or search_board(g)
            r["san"] = pv_san(board, r.get("pv", []), limit=16)
        rows.append({"d": r.get("depth"), "sd": r.get("seldepth"), "score": fmt_score(r.get("cp"), r.get("mate")),
                     "cp": _clip(score_cp(r)), "bound": r.get("bound", ""), "time": r.get("time", 0),
                     "nodes": r.get("nodes", 0), "nps": r.get("nps", 0), "hash": r.get("hashfull", 0),
                     "tb": r.get("tbhits", 0), "pv": r["san"]})
    live = g.live
    evals = [{"ply": e.ply, "san": e.san, "cp": _clip(e.white_cp), "text": fmt_score(e.white_cp),
              "d": e.depth, "sd": e.seldepth, "nodes": e.nodes, "nps": e.nps, "time": e.time_ms,
              "hash": e.hashfull, "tb": e.tbhits, "pv": e.pv_san, "ph": e.ponderhit} for e in g.evals]
    first = g.raw_total - len(g.raw)
    start = max(raw_from, first, g.raw_total - 400)
    raw = [[round(t, 2), dr, tx] for (t, dr, tx) in list(g.raw)[start - first:]]
    d.update({
        "rows": rows, "evals": evals, "sans": list(g.sans),
        "fens": _ply_fens(g), "lasts": _ply_lasts(g),
        "start_white": g.start_board.turn == chess.WHITE, "start_move": g.start_board.fullmove_number,
        "live": {"nodes": live.get("nodes", 0), "nps": live.get("nps", 0), "hash": live.get("hashfull", 0),
                 "tb": live.get("tbhits", 0), "depth": live.get("depth"), "sd": live.get("seldepth")},
        "ponder_hits": g.ponder_hits, "ponder_total": g.ponder_total,
        "engine": g.engine.name if g.engine else "",
        "raw": raw, "raw_next": g.raw_total,
        "chat": [list(c) for c in g.chat][-60:],
        "pgn": g.pgn_path or "",
        "lag": "" if g.local else g.mgr.lag.text(), "overhead": g.overhead_now,
    })
    return d


class _RequestUI(UI):
    """Collects what a command asks the view to do (watch a game, go back, clear the log)."""

    def __init__(self, watched: str | None, view: str | None = None):
        self.watched = watched or None
        self.view = view or ("detail" if watched else "overview")
        self.go: str | None = None
        self.clear = False

    def watch(self, game: GameSession) -> None:
        self.go = game.id

    def analysis(self) -> None:
        self.go = "@analysis"

    def overview(self) -> None:
        self.go = ""

    def clear_log(self) -> None:
        self.clear = True


class WebServer:
    def __init__(self, mgr: "BotManager", host: str = "127.0.0.1", port: int = 8080, key: str = "",
                 public_port: int = 0):
        self.mgr = mgr
        self._hint_lock = asyncio.Lock()
        self.public_port = public_port   # extra listener that serves nothing but /api/public/state (for a tunnel)
        self.pub_server: asyncio.base_events.Server | None = None
        self.host = host
        self.port = port
        self.loopback = host in ("127.0.0.1", "localhost", "::1")
        self.key = key or ("" if self.loopback else secrets.token_urlsafe(12))
        self.server: asyncio.base_events.Server | None = None
        self.cmd_names = [n for n in Commands(mgr).names if len(n) > 2]

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}/" + (f"?key={self.key}" if self.key else "")

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, self.host, self.port)
        where = self.url if self.loopback else f"{self.url}  (network: use this machine's address)"
        self.mgr.log("info", f"web dashboard: {where}")
        if self.public_port:
            self.pub_server = await asyncio.start_server(
                lambda r, w: self._client(r, w, public_only=True), "127.0.0.1", self.public_port)
            self.mgr.log("info", f"public telemetry (read-only): http://127.0.0.1:{self.public_port}/api/public/state")

    async def stop(self) -> None:
        for srv in (self.server, self.pub_server):
            if srv:
                srv.close()
                try:
                    await asyncio.wait_for(srv.wait_closed(), 2)
                except (asyncio.TimeoutError, Exception):
                    pass

    # ---- HTTP plumbing ----------------------------------------------------------------------
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      public_only: bool = False) -> None:
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(), 60)
                if not line:
                    break
                parts = line.decode("latin-1").split()
                if len(parts) < 2:
                    break
                method, target = parts[0].upper(), parts[1]
                headers: dict[str, str] = {}
                while True:
                    h = await asyncio.wait_for(reader.readline(), 30)
                    if h in (b"\r\n", b"\n", b""):
                        break
                    k, _, v = h.decode("latin-1").partition(":")
                    headers[k.strip().lower()] = v.strip()
                n = int(headers.get("content-length") or 0)
                if n > 1 << 19:
                    await self._send(writer, 413, "text/plain", b"too large")
                    break
                body = await reader.readexactly(n) if n else b""
                cors = False
                try:
                    if public_only or target.startswith("/api/public/"):
                        status, ctype, data, cache = self._public(method, target, force=public_only)
                        cors = True
                    else:
                        status, ctype, data, cache = await self._route(method, target, headers, body)
                except Exception as e:           # never let a page request hurt the bot
                    self.mgr.log("warning", f"web: {method} {target}: {e!r}")
                    status, ctype, data, cache = 500, "text/plain", b"internal error", False
                await self._send(writer, status, ctype, data, cache, cors)
                if headers.get("connection", "").lower() == "close":
                    break
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ValueError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, status: int, ctype: str, data: bytes, cache: bool = False,
                    cors: bool = False) -> None:
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
                  413: "Payload Too Large", 500: "Internal Server Error"}.get(status, "OK")
        head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\n"
                f"Cache-Control: {'max-age=86400' if cache else 'no-store'}\r\n"
                f"X-Content-Type-Options: nosniff\r\nConnection: keep-alive\r\n"
                + ("Access-Control-Allow-Origin: *\r\n" if cors else "") + "\r\n")
        writer.write(head.encode("latin-1") + data)
        await writer.drain()

    def _host_ok(self, headers: dict[str, str]) -> bool:
        """Refuse other host names (DNS rebinding: a web site pointing its own name at 127.0.0.1)."""
        host = headers.get("host", "")
        name = host.rsplit(":", 1)[0].strip("[]") if host.count(":") <= 1 or host.startswith("[") else host
        if name in ("127.0.0.1", "localhost", "::1"):
            return True
        if self.loopback:
            return False
        try:                                     # on the network: IP addresses only, no host names
            ipaddress.ip_address(name)
            return True
        except ValueError:
            return False

    def _key_ok(self, headers: dict[str, str], query: dict[str, list[str]]) -> bool:
        if not self.key:
            return True
        given = headers.get("x-key") or (query.get("key") or [""])[0]
        return hmac.compare_digest(given.encode(), self.key.encode())

    @staticmethod
    def _json(obj) -> tuple[int, str, bytes, bool]:
        return 200, "application/json; charset=utf-8", json.dumps(obj, separators=(",", ":")).encode(), False

    async def _route(self, method: str, target: str, headers: dict[str, str], body: bytes):
        if not self._host_ok(headers):
            return 403, "text/plain", b"forbidden host", False
        u = urlsplit(target)
        path, query = u.path, parse_qs(u.query)
        if method == "GET" and path in ("/", "/index.html"):
            return 200, "text/html; charset=utf-8", (WEB_DIR / "index.html").read_bytes(), False
        if method == "GET" and path.startswith("/static/"):
            name = path[len("/static/"):]
            f = WEB_DIR / name
            if "/" in name or "\\" in name or ".." in name or not f.is_file():
                return 404, "text/plain", b"not found", False
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            return 200, ctype, f.read_bytes(), False
        if method == "GET" and path.startswith("/pieces/"):
            code = path[len("/pieces/"):].removesuffix(".svg")
            f = ASSETS / str(self.mgr.cfg.get("ui.piece_set")) / f"{code}.svg"
            if code not in PIECE_CODES or not f.is_file():
                return 404, "text/plain", b"not found", False
            return 200, "image/svg+xml", f.read_bytes(), True
        if not path.startswith("/api/"):
            return 404, "text/plain", b"not found", False
        if not self._key_ok(headers, query):
            return 403, "application/json", b'{"error":"access key required"}', False
        if method == "GET" and path == "/api/state":
            return self._json(self.state(int((query.get("log") or ["0"])[0] or 0)))
        if method == "GET" and path.startswith("/api/game/"):
            gid = path[len("/api/game/"):]
            g, idx = self._find(gid)
            if g is None:
                return 404, "application/json", b'{"error":"no such game"}', False
            return self._json(game_detail(g, idx, int((query.get("raw") or ["0"])[0] or 0)))
        if path == "/api/cmd":
            if method != "POST":
                return 405, "text/plain", b"POST only", False
            if "application/json" not in headers.get("content-type", ""):
                return 400, "text/plain", b"json body expected", False
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                return 400, "text/plain", b"bad json", False
            return self._json(await self.command(str(req.get("line", ""))[:2000], req.get("watched"),
                                                 req.get("view")))
        if path == "/api/hint" and method == "POST":
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                return 400, "text/plain", b"bad json", False
            return self._json(await self.hint(str(req.get("id", ""))))
        if path == "/api/analysis":
            an = self.mgr.get_analysis()
            if method == "GET":
                an.touch()
                return self._json(an.state())
            if "application/json" not in headers.get("content-type", ""):
                return 400, "text/plain", b"json body expected", False
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                return 400, "text/plain", b"bad json", False
            return self._json(await self.analysis_op(an, req))
        return 404, "text/plain", b"not found", False

    async def analysis_op(self, an, req: dict) -> dict:
        op = str(req.get("op", ""))
        an.touch()
        msg = ""
        try:
            if op == "load":
                msg = await an.load_text(str(req.get("text", "")))
            elif op == "game":
                g, _ = self._find(str(req.get("id", "")))
                if g is None:
                    raise ValueError("no such game")
                msg = await an.load_game(g)
            elif op == "move":
                msg = await an.play(str(req.get("move", "")))
            elif op == "goto":
                await an.goto(int(req.get("ply", 0)))
            elif op == "lines":
                await an.set_multipv(int(req.get("n", 1)))
            elif op == "engine":
                await an.set_enabled(bool(req.get("on")))
            elif op == "pass":
                msg = await an.analyze_game(int(req["ms"]) if req.get("ms") else None)
            elif op == "flip":
                an.flip = not an.flip
            else:
                raise ValueError(f"unknown op {op}")
        except (ValueError, KeyError, OSError) as e:
            return {"ok": False, "error": str(e) or type(e).__name__, "state": an.state()}
        return {"ok": True, "msg": msg, "state": an.state()}

    # ---- public read-only telemetry (web.public) --------------------------------------------------
    def _public(self, method: str, target: str, force: bool = False):
        """GET /api/public/state: every running game with its engine output, for a read-only mirror page
        (e.g. the GitHub Pages viewer reached through a tunnel). No commands, no log, no chat, no raw I/O,
        open to any origin; only when web.public is on."""
        if not (force or self.mgr.cfg.get("web.public")):
            return 404, "text/plain", b"not found", False
        if method != "GET" or urlsplit(target).path != "/api/public/state":
            return 404, "text/plain", b"not found", False
        m = self.mgr
        games = []
        for i, g in enumerate([x for x in m.game_list() if not x.local], 1):
            d = game_detail(g, i, g.raw_total)          # starting at the end: no raw lines
            for k in ("raw", "raw_next", "chat", "pgn"):
                d.pop(k, None)
            d["rows"] = d["rows"][:12]
            games.append(d)
        return self._json({"user": m.username, "stream": m.stream_ok, "paused": m.paused,
                           "limit": m.cfg.get("challenge.concurrency"), "lag": m.lag.text(),
                           "results": {"win": m.results["win"], "draw": m.results["draw"], "loss": m.results["loss"]},
                           "games": games, "time": time.time()})

    # ---- content -------------------------------------------------------------------------------
    def _find(self, gid: str) -> tuple[GameSession | None, int]:
        for i, g in enumerate(self.mgr.game_list(), 1):
            if g.id == gid:
                return g, i
        for g in self.mgr.finished:
            if g.id == gid:
                return g, 0
        return None, 0

    def state(self, log_from: int) -> dict:
        m = self.mgr
        c = m.cfg.get("challenge")
        first = m.events_total - len(m.events)
        start = max(log_from, first, m.events_total - 300)
        log = [[time.strftime("%H:%M:%S", time.localtime(t)), lvl, markup_html(msg)]
               for (t, lvl, msg) in list(m.events)[start - first:]]
        return {
            "user": m.username, "server": m.base_url, "up": int(time.time() - m.started),
            "stream": m.stream_ok, "paused": m.paused, "quitting": m.quitting or "",
            "online": m.online, "offline_only": m.offline_only, "local_defaults": m.cfg.get("local"),
            "limit": c["concurrency"], "matchmaking": bool(m.cfg.get("matchmaking.enabled")),
            "results": {"win": m.results["win"], "draw": m.results["draw"], "loss": m.results["loss"]},
            "games": [game_summary(g, i) for i, g in enumerate(m.game_list(), 1)],
            "recent": [{"id": g.id, "url": g.url, "outcome": g.outcome_for_me(), "result": g.result(), "status": g.status,
                        "local": g.local, "plies": len(g.moves),
                        "opponent": g.player_label(g.opponent), "tc": g.tc,
                        "color": "white" if g.color == chess.WHITE else "black"}
                       for g in list(m.finished)[:12]],
            "queue": [{"id": q.id, "who": q.challenger, "rating": q.rating, "title": q.title or "",
                       "speed": q.speed, "rated": q.rated, "tc": f"{(q.limit or 0) / 60:g}+{q.increment or 0}"}
                      for q in m.queue],
            "outgoing": len(m.outgoing),
            "slots": self.slots(),
            "links": [{"id": ln["id"], "url": ln["url"], "tc": ln["tc"], "rated": ln["rated"], "color": ln["color"],
                       "left": int(max(0, ln["expires"] - time.time()))} for ln in m.links.values()],
            "link_defaults": m.cfg.get("link"),
            "log": log, "log_next": m.events_total,
            "pieces": str(m.cfg.get("ui.piece_set")), "cmds": self.cmd_names,
        }

    def slots(self) -> dict:
        m = self.mgr
        use = m.slot_usage()
        out = {"total": [sum(use.values()), int(m.cfg.get("challenge.concurrency"))], "split": m.split_slots()}
        for k in ("bot", "human"):
            out[k] = [use[k], m.slot_cap(k)]
        return out

    async def hint(self, gid: str) -> dict:
        """Best move for the person to move in a local game: a separate engine at full strength, ~2 s."""
        g = self.mgr.games.get(gid)
        if g is None or not g.local or g.over or not g.human_turn:
            return {"error": "no hint now"}
        async with self._hint_lock:
            ecfg = self.mgr.cfg.get("engine")
            opts = {k: v for k, v in dict(ecfg["options"]).items() if k.lower() in ("threads", "hash", "syzygypath")}
            opts["Threads"] = min(int(opts.get("Threads", 2) or 2), 4)
            opts["Ponder"] = False
            eng = UciEngine(self.mgr.cfg.resolve(ecfg["path"]), opts)
            try:
                await eng.start()
                await eng.go(f"position fen {g.ref.board.fen()}", "go movetime 2000", None)
                best, _ = await eng.wait_bestmove(15)
            except (EngineError, asyncio.TimeoutError):
                return {"error": "engine failed"}
            finally:
                await eng.quit()
        return {"move": best} if best and best != "(none)" else {"error": "no move"}

    async def command(self, line: str, watched: str | None, view: str | None = None) -> dict:
        ui = _RequestUI(watched, view)
        out = await Commands(self.mgr, ui).execute(line)
        if line.strip():
            self.mgr.log("debug", f"web command: {line.strip()}")
        return {"lines": [markup_html(s) for s in out], "go": ui.go, "clear": ui.clear}
