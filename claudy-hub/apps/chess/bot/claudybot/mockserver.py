"""A small fake Lichess server for testing ClaudyBot without a token or network.

    python -m claudybot.mockserver [--port 8765] [--engine ../claudy.exe] [--every 20]

It implements the subset of the Bot API ClaudyBot uses (event stream, game streams, moves, challenges,
chat, draw / abort / resign / claim-victory, online bots) with real clocks, flagging, repetition /
50-move / material draws, and a few opponent personalities: normal, chatty (chats and offers draws),
noshow (never moves: tests aborting) and leaver (disappears mid-game: tests claiming victory).
Open challenges ("challenge a friend" links) are joined by a fake visitor after --link-join seconds.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
import time
from pathlib import Path
from urllib.parse import parse_qs

import chess
import chess.engine

FAKE_PLAYERS = [
    ("PawnStorm", 1850, False), ("Knightmare", 2210, False), ("EndgameEddie", 1640, False),
    ("sicilian_sam", 2005, False), ("maia-ish", 1500, True), ("TurboBot", 2600, True),
    ("SlowButSteady", 1950, True), ("gambit_gary", 1720, False), ("ZugzwangZoe", 2380, False),
    ("RandomMover", 800, True),
]
TCS = [(60, 0), (60, 1), (120, 1), (180, 0), (180, 2), (300, 3), (600, 5), (15, 0), (1800, 20)]


def gid() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=8))


def speed_of(limit: int, inc: int) -> str:
    est = limit + 40 * inc
    return ("ultraBullet" if est < 30 else "bullet" if est < 180 else "blitz" if est < 480
            else "rapid" if est < 1500 else "classical")


class MockGame:
    def __init__(self, srv: "MockLichess", ch: dict, bot_white: bool, behavior: str):
        self.srv = srv
        self.id = ch["id"]
        self.bot_color = chess.WHITE if bot_white else chess.BLACK
        self.opp = ch["challenger"] if ch["direction"] == "in" else ch["destUser"]
        self.behavior = behavior
        self.diff_sent = False
        tc = ch["timeControl"]
        self.initial = tc["limit"] * 1000
        self.inc = tc["increment"] * 1000
        self.rated = ch["rated"]
        self.speed = ch["speed"]
        self.board = chess.Board()
        self.wtime = self.btime = self.initial
        self.last = time.monotonic()
        self.status = "started"
        self.winner: str | None = None
        self.wdraw = self.bdraw = False
        self.subs: list[asyncio.Queue] = []
        self.chat: list[dict] = []
        self.gone = False
        self.claim_at = 0.0
        self.turn_event = asyncio.Event()
        self.tasks = [asyncio.create_task(self._opponent()), asyncio.create_task(self._flag())]

    @property
    def moves(self) -> str:
        return " ".join(m.uci() for m in self.board.move_stack)

    def running_clock(self) -> bool:
        return len(self.board.move_stack) >= 2 and self.status == "started"

    def state(self) -> dict:
        w, b = self.wtime, self.btime
        st = {"type": "gameState", "moves": self.moves, "wtime": int(w), "btime": int(b),
              "winc": self.inc, "binc": self.inc, "status": self.status, "wdraw": self.wdraw, "bdraw": self.bdraw}
        if self.winner:
            st["winner"] = self.winner
        return st

    def player(self, color: bool) -> dict:
        if color == self.bot_color:
            return {"id": self.srv.bot["id"], "name": self.srv.bot["username"], "title": "BOT", "rating": 2450}
        return {"id": self.opp["id"], "name": self.opp["name"], "title": self.opp.get("title"),
                "rating": self.opp.get("rating")}

    def full(self) -> dict:
        return {"type": "gameFull", "id": self.id, "rated": self.rated,
                "variant": {"key": "standard", "name": "Standard", "short": "Std"},
                "clock": {"initial": self.initial, "increment": self.inc}, "speed": self.speed,
                "perf": {"name": self.speed}, "createdAt": int(time.time() * 1000),
                "white": self.player(chess.WHITE), "black": self.player(chess.BLACK),
                "initialFen": "startpos", "state": self.state()}

    def game_event(self) -> dict:
        return {"gameId": self.id, "fullId": self.id + "abcd", "id": self.id,
                "color": "white" if self.bot_color == chess.WHITE else "black",
                "fen": self.board.fen(), "hasMoved": False, "isMyTurn": self.board.turn == self.bot_color,
                "lastMove": "", "opponent": {"id": self.opp["id"], "username": self.opp["name"],
                                              "rating": self.opp.get("rating")},
                "perf": self.speed, "rated": self.rated, "secondsLeft": self.initial // 1000,
                "source": "friend", "speed": self.speed, "variant": {"key": "standard", "name": "Standard"},
                "compat": {"bot": True, "board": False}, "status": {"name": self.status}, "winner": self.winner}

    def broadcast(self, msg: dict | None) -> None:
        for q in self.subs:
            q.put_nowait(msg)

    def finish(self, status: str, winner: str | None) -> None:
        if self.status != "started":
            return
        self.status, self.winner = status, winner
        self.srv.log(f"game {self.id} over: {status} {winner or ''}")
        self.broadcast(self.state())
        self.broadcast(None)
        self.srv.event({"type": "gameFinish", "game": self.game_event()})
        for t in self.tasks:
            if t is not asyncio.current_task():
                t.cancel()

    def push(self, uci: str) -> str | None:
        """Apply a move by the side to move; returns an error string or None."""
        if self.status != "started":
            return "game is over"
        try:
            mv = chess.Move.from_uci(uci)
        except ValueError:
            return "bad move"
        if mv not in self.board.legal_moves:
            return f"illegal move {uci}"
        now = time.monotonic()
        mover = self.board.turn
        if self.running_clock():
            elapsed = (now - self.last) * 1000
            if mover == chess.WHITE:
                self.wtime -= elapsed
            else:
                self.btime -= elapsed
            if (self.wtime if mover == chess.WHITE else self.btime) <= 0:
                self.finish("outoftime", "black" if mover == chess.WHITE else "white")
                return "out of time"
        if len(self.board.move_stack) >= 1:          # increment once the clock runs
            if mover == chess.WHITE:
                self.wtime += self.inc
            else:
                self.btime += self.inc
        self.board.push(mv)
        self.last = now
        # a move by the side that did not offer cancels the other's draw offer
        if mover == chess.WHITE:
            self.bdraw = False
        else:
            self.wdraw = False
        out = self.board.outcome(claim_draw=True)
        if out:
            if out.termination == chess.Termination.CHECKMATE:
                self.finish("mate", "white" if out.winner == chess.WHITE else "black")
            elif out.termination == chess.Termination.STALEMATE:
                self.finish("stalemate", None)
            else:
                self.finish("draw", None)
        else:
            self.broadcast(self.state())
        self.turn_event.set()
        return None

    async def _flag(self) -> None:
        while self.status == "started":
            await asyncio.sleep(0.1)
            if not self.running_clock():
                continue
            side = self.board.turn
            left = (self.wtime if side == chess.WHITE else self.btime) - (time.monotonic() - self.last) * 1000
            if left <= 0:
                if side == chess.WHITE:
                    self.wtime = 0
                else:
                    self.btime = 0
                self.finish("outoftime", "black" if side == chess.WHITE else "white")
            if self.gone and self.claim_at and time.monotonic() > self.claim_at + 60:
                self.finish("timeout", "white" if self.bot_color == chess.WHITE else "black")

    async def _opponent(self) -> None:
        eng = None
        try:
            if self.srv.engine_path:
                _, eng = await chess.engine.popen_uci(str(self.srv.engine_path))
                await eng.configure({"Hash": 8, "Threads": 1})
            nodes = max(200, int(10 ** (self.opp.get("rating", 1500) / 900)))
            while self.status == "started":
                if self.board.turn == self.bot_color:
                    self.turn_event.clear()
                    await self.turn_event.wait()
                    continue
                ply = len(self.board.move_stack)
                if self.behavior == "noshow" and ply < 2:
                    await asyncio.sleep(3600)
                if self.behavior == "leaver" and ply >= 12 and not self.gone:
                    self.gone = True
                    self.claim_at = time.monotonic() + 10
                    self.broadcast({"type": "opponentGone", "gone": True, "claimWinInSeconds": 10})
                    self.srv.log(f"game {self.id}: opponent left")
                    await asyncio.sleep(3600)
                if self.behavior in ("diff", "latediff") and not self.diff_sent and (ply < 2) == (self.behavior == "diff"):
                    self.diff_sent = True
                    self.say("player", "!diff 50")                       # no such level
                    await asyncio.sleep(0.5)
                    self.say("player", f"!diff {random.choice([400, 900, 1500, 2100])}")
                    await asyncio.sleep(1.5)
                if self.behavior == "chatty" and ply in (3, 4):
                    self.say("player", "hi! good luck")
                    self.say("spectator", "!eval")
                if self.behavior == "chatty" and ply in (30, 31):
                    if self.bot_color == chess.WHITE:
                        self.bdraw = True
                    else:
                        self.wdraw = True
                    self.broadcast(self.state())
                clock = self.wtime if self.board.turn == chess.WHITE else self.btime
                think = random.uniform(0.2, 1.2) if ply >= 2 else random.uniform(0.5, 2.0)
                think = min(think, max(0.05, clock / 1000 / 30))
                t0 = time.monotonic()
                if eng:
                    res = await eng.play(self.board, chess.engine.Limit(nodes=nodes), info=chess.engine.INFO_SCORE)
                    mv = res.move
                    score = res.info.get("score")
                    if score and score.relative.score(mate_score=100000) < -900 and random.random() < 0.3:
                        self.finish("resign", "white" if self.bot_color == chess.WHITE else "black")
                        break
                else:
                    moves = list(self.board.legal_moves)
                    caps = [m for m in moves if self.board.is_capture(m)]
                    mv = random.choice(caps or moves)
                rest = think - (time.monotonic() - t0)
                if rest > 0:
                    await asyncio.sleep(rest)
                if self.status != "started":
                    break
                self.push(mv.uci())
        except asyncio.CancelledError:
            pass
        finally:
            if eng:
                try:
                    await eng.quit()
                except Exception:
                    pass

    def say(self, room: str, text: str) -> None:
        msg = {"type": "chatLine", "room": room, "username": self.opp["name"], "text": text}
        self.broadcast(msg)


class MockLichess:
    def __init__(self, engine: Path | None, every: float, max_games: int, behaviors: list[str] | None = None):
        self.behaviors = behaviors
        self.engine_path = engine
        self.every = every
        self.max_games = max_games
        self.bot = {"id": "claudytest", "username": "ClaudyTest", "title": "BOT",
                    "perfs": {"bullet": {"rating": 2400}, "blitz": {"rating": 2450}, "rapid": {"rating": 2500}}}
        self.event_subs: list[asyncio.Queue] = []
        self.lag_ms = 0.0               # simulated network round trip (--lag): half on each direction
        self.rated_prob = 0.6
        self.force_tc: tuple[int, int] | None = None   # --tc: every incoming challenge uses this clock
        self.challenges: dict[str, dict] = {}
        self.games: dict[str, MockGame] = {}
        self.link_join = 8.0            # --link-join: a visitor takes an open link after this many seconds (0 = never)
        self.open_scope = True          # --no-open-scope: the token may not create open challenges (HTTP 403)
        self.anon_visitor = False       # --anon-visitor: links are taken by an anonymous player

    async def net_delay(self) -> None:
        """One network trip: half the round trip, with jitter and an occasional spike."""
        if self.lag_ms > 0:
            spike = 3.0 if random.random() < 0.05 else 1.0
            await asyncio.sleep(self.lag_ms / 2000 * random.uniform(0.8, 1.4) * spike)

    def log(self, msg: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} mock: {msg}", flush=True)

    def event(self, ev: dict) -> None:
        for q in self.event_subs:
            q.put_nowait(ev)

    # ---- challenge generation -------------------------------------------------------------------
    def make_challenge(self, name: str, rating: int, is_bot: bool, limit: int, inc: int, rated: bool,
                       variant: str = "standard", direction: str = "in") -> dict:
        me = {"id": self.bot["id"], "name": self.bot["username"], "title": "BOT", "rating": 2450}
        other = {"id": name.lower(), "name": name, "title": "BOT" if is_bot else None, "rating": rating}
        ch = {"id": gid(), "url": f"http://mock/{name}", "status": "created",
              "challenger": other if direction == "in" else me, "destUser": me if direction == "in" else other,
              "variant": {"key": variant, "name": variant.title(), "short": variant[:3]},
              "rated": rated, "speed": speed_of(limit, inc),
              "timeControl": {"type": "clock", "limit": limit, "increment": inc, "show": f"{limit // 60}+{inc}"},
              "color": "random", "finalColor": random.choice(["white", "black"]),
              "perf": {"icon": "", "name": speed_of(limit, inc)}, "direction": direction,
              "behavior": (random.choice(self.behaviors) if self.behaviors else
                           random.choices(["normal", "chatty", "noshow", "leaver"], [70, 18, 6, 6])[0])}
        self.challenges[ch["id"]] = ch
        return ch

    async def generator(self) -> None:
        await asyncio.sleep(3)
        while True:
            live = sum(1 for g in self.games.values() if g.status == "started")
            if live < self.max_games and self.event_subs:
                name, rating, is_bot = random.choice(FAKE_PLAYERS)
                limit, inc = self.force_tc or random.choice(TCS)
                variant = "standard" if self.force_tc else random.choices(["standard", "chess960"], [9, 1])[0]
                ch = self.make_challenge(name, rating, is_bot, limit, inc, random.random() < self.rated_prob, variant)
                self.log(f"challenge {ch['id']} from {name} {limit}+{inc} {variant} ({ch['behavior']})")
                self.event({"type": "challenge", "challenge": {k: v for k, v in ch.items() if k != "behavior"}})
                asyncio.create_task(self._expire(ch["id"]))
            await asyncio.sleep(random.uniform(0.5, 1.5) * self.every)

    async def _expire(self, cid: str) -> None:
        await asyncio.sleep(40)
        ch = self.challenges.pop(cid, None)
        if ch and ch["status"] == "created":
            self.event({"type": "challengeCanceled", "challenge": ch})

    def start_game(self, ch: dict, bot_white: bool | None = None) -> MockGame:
        ch["status"] = "accepted"
        if bot_white is None:
            bot_white = random.random() < 0.5
        g = MockGame(self, ch, bot_white, ch.get("behavior", "normal"))
        self.games[g.id] = g
        self.log(f"game {g.id} starts: bot plays {'white' if bot_white else 'black'} vs {g.opp['name']}")
        self.event({"type": "gameStart", "game": g.game_event()})
        return g

    # ---- HTTP -------------------------------------------------------------------------------------
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            req = await reader.readline()
            if not req:
                return
            method, target, _ = req.decode().split(" ", 2)
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                k, _, v = line.decode().partition(":")
                headers[k.strip().lower()] = v.strip()
            body = b""
            if "content-length" in headers:
                body = await reader.readexactly(int(headers["content-length"]))
            path, _, query = target.partition("?")
            params = {k: v[0] for k, v in parse_qs(query).items()}
            form = {k: v[0] for k, v in parse_qs(body.decode()).items()} if body else {}
            form["_raw"] = body.decode(errors="replace")
            authed = headers.get("authorization", "").startswith("Bearer ")
            if not authed and not (method == "POST" and path == "/api/challenge/open"):
                await self.send_json(writer, 401, {"error": "No such token"})
                return
            if authed and path == "/api/challenge/open" and not self.open_scope:
                await self.send_json(writer, 403, {"error": "Missing scope challenge:write"})
                return
            await self.route(method, path, params, form, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def send_json(self, w: asyncio.StreamWriter, status: int, obj) -> None:
        body = json.dumps(obj).encode()
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
                  404: "Not Found"}.get(status, "OK")
        w.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n".encode() + body)
        await w.drain()

    async def send_ndjson(self, w: asyncio.StreamWriter, lines: list[dict]) -> None:
        body = "".join(json.dumps(x) + "\n" for x in lines).encode()
        w.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nContent-Length: {len(body)}\r\n"
                f"Connection: close\r\n\r\n".encode() + body)
        await w.drain()

    async def stream(self, w: asyncio.StreamWriter, first: list[dict], q: asyncio.Queue) -> None:
        w.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/x-ndjson\r\nTransfer-Encoding: chunked\r\n\r\n")

        def chunk(data: bytes) -> None:
            w.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        for x in first:
            chunk((json.dumps(x) + "\n").encode())
        await w.drain()
        while True:
            try:
                item = await asyncio.wait_for(q.get(), 6)
            except asyncio.TimeoutError:
                chunk(b"\n")
                await w.drain()
                continue
            if item is None:
                break
            await self.net_delay()
            chunk((json.dumps(item) + "\n").encode())
            await w.drain()
        w.write(b"0\r\n\r\n")
        await w.drain()

    async def route(self, method: str, path: str, params: dict, form: dict, w: asyncio.StreamWriter) -> None:
        p = path.strip("/").split("/")
        ok = {"ok": True}
        if method == "GET" and path == "/api/account":
            return await self.send_json(w, 200, self.bot)
        if method == "POST" and path == "/api/token/test":
            tok = form.get("_raw", "").strip()
            return await self.send_json(w, 200, {tok: {"scopes": "bot:play,challenge:read,challenge:write",
                                                       "userId": self.bot["id"], "expires": None}})
        if method == "POST" and path == "/api/bot/account/upgrade":
            self.bot["title"] = "BOT"
            self.log("account upgraded to BOT")
            return await self.send_json(w, 200, {"ok": True})
        if method == "GET" and path == "/api/account/playing":
            return await self.send_json(w, 200, {"nowPlaying": [g.game_event() for g in self.games.values()
                                                                if g.status == "started"]})
        if method == "GET" and path == "/api/stream/event":
            q: asyncio.Queue = asyncio.Queue()
            self.event_subs.append(q)
            first = [{"type": "gameStart", "game": g.game_event()} for g in self.games.values()
                     if g.status == "started"]
            try:
                await self.stream(w, first, q)
            finally:
                self.event_subs.remove(q)
            return
        if method == "GET" and path == "/api/bot/online":
            bots = []
            for name, rating, is_bot in FAKE_PLAYERS:
                if is_bot:
                    bots.append({"id": name.lower(), "username": name, "title": "BOT",
                                 "perfs": {s: {"rating": rating + random.randint(-50, 50), "games": 500}
                                           for s in ("bullet", "blitz", "rapid", "classical")}})
            return await self.send_ndjson(w, bots)
        if method == "GET" and p[:4] == ["api", "bot", "game", "stream"]:
            g = self.games.get(p[4])
            if not g:
                return await self.send_json(w, 404, {"error": "Not found"})
            q = asyncio.Queue()
            if g.status != "started":
                return await self.stream(w, [g.full()], _closed_queue())
            g.subs.append(q)
            try:
                await self.stream(w, [g.full()], q)
            finally:
                if q in g.subs:
                    g.subs.remove(q)
            return
        if method == "POST" and path == "/api/challenge/open":
            limit, inc = int(form.get("clock.limit", 300)), int(form.get("clock.increment", 3))
            ch = {"id": gid(), "url": f"http://mock/{len(self.challenges)}", "status": "created", "challenger": None,
                  "destUser": None, "variant": {"key": "standard", "name": "Standard", "short": "Std"},
                  "rated": form.get("rated") == "true", "speed": speed_of(limit, inc),
                  "timeControl": {"type": "clock", "limit": limit, "increment": inc, "show": f"{limit // 60}+{inc}"},
                  "color": "random", "finalColor": "white", "perf": {"icon": "", "name": speed_of(limit, inc)},
                  "direction": "out", "open": {}, "name": form.get("name", ""), "behavior": "normal",
                  "expiresAt": int(form.get("expiresAt", 0) or 0)}
            ch["url"] = f"http://127.0.0.1/{ch['id']}"
            self.challenges[ch["id"]] = ch
            self.log(f"open challenge {ch['id']} {limit}+{inc} created ({form.get('name', '')})")
            return await self.send_json(w, 200, {**{k: v for k, v in ch.items() if k != "behavior"},
                                                 "urlWhite": ch["url"] + "?color=white",
                                                 "urlBlack": ch["url"] + "?color=black"})
        if method == "POST" and p[:2] == ["api", "challenge"]:
            if len(p) == 4:
                cid, action = p[2], p[3]
                ch = self.challenges.get(cid)
                if not ch:
                    return await self.send_json(w, 404, {"error": "Challenge not found"})
                if action == "accept" and "open" in ch and ch["challenger"] is None:
                    ch["challenger"] = {"id": self.bot["id"], "name": self.bot["username"], "title": "BOT"}
                    ch["seat"] = params.get("color")
                    self.log(f"bot took its seat in {cid} ({ch['seat'] or 'random'})")
                    if self.link_join > 0:
                        asyncio.create_task(self._visit(ch))
                    return await self.send_json(w, 200, ok)
                if action == "accept":
                    self.challenges.pop(cid)
                    self.start_game(ch)
                elif action == "decline":
                    self.challenges.pop(cid)
                    self.log(f"challenge {cid} declined: {form.get('reason', 'generic')}")
                elif action == "cancel":
                    self.challenges.pop(cid)
                    self.log(f"challenge {cid} canceled by bot")
                return await self.send_json(w, 200, ok)
            if len(p) == 3:                                   # bot challenges someone
                user = p[2]
                known = {n.lower(): (n, r, b) for n, r, b in FAKE_PLAYERS}
                if user.lower() not in known:
                    return await self.send_json(w, 400, {"error": f"{user} does not exist"})
                n, r, b = known[user.lower()]
                ch = self.make_challenge(n, r, b, int(form.get("clock.limit", 180)),
                                         int(form.get("clock.increment", 2)), form.get("rated") == "true",
                                         direction="out")
                ch["behavior"] = "normal"
                self.event({"type": "challenge", "challenge": ch})
                asyncio.create_task(self._answer(ch))
                return await self.send_json(w, 200, {"challenge": ch})
        if method == "POST" and p[:3] == ["api", "bot", "game"] and len(p) >= 5:
            g = self.games.get(p[3])
            if not g:
                return await self.send_json(w, 404, {"error": "Not found"})
            action = p[4]
            bot_str = "white" if g.bot_color == chess.WHITE else "black"
            opp_str = "black" if g.bot_color == chess.WHITE else "white"
            if action == "move":
                await self.net_delay()
                if g.board.turn != g.bot_color:
                    return await self.send_json(w, 400, {"error": "Not your turn, or game already over"})
                err = g.push(p[5])
                if err:
                    return await self.send_json(w, 400, {"error": err})
                if params.get("offeringDraw") == "true" and g.status == "started":
                    self._bot_draw(g)
                return await self.send_json(w, 200, ok)
            if action == "resign":
                g.finish("resign", opp_str)
            elif action == "abort":
                if len(g.board.move_stack) >= 2:
                    return await self.send_json(w, 400, {"error": "game cannot be aborted"})
                g.finish("aborted", None)
            elif action == "draw":
                if p[5] == "yes":
                    self._bot_draw(g)
                else:
                    g.wdraw = g.bdraw = False
                    g.broadcast(g.state())
                    self.log(f"game {g.id}: bot declined the draw")
            elif action == "claim-victory":
                if not (g.gone and time.monotonic() >= g.claim_at):
                    return await self.send_json(w, 400, {"error": "cannot claim yet"})
                g.finish("timeout", bot_str)
            elif action == "chat":
                self.log(f"game {g.id} chat ({form.get('room')}): {form.get('text')}")
                g.broadcast({"type": "chatLine", "room": form.get("room", "player"),
                             "username": self.bot["username"], "text": form.get("text", "")})
            elif action == "takeback":
                pass
            return await self.send_json(w, 200, ok)
        await self.send_json(w, 404, {"error": f"no route {method} {path}"})

    def _bot_draw(self, g: MockGame) -> None:
        opp_offered = g.wdraw if g.bot_color == chess.BLACK else g.bdraw
        if opp_offered:
            self.log(f"game {g.id}: draw agreed")
            g.finish("draw", None)
            return
        if g.bot_color == chess.WHITE:
            g.wdraw = True
        else:
            g.bdraw = True
        g.broadcast(g.state())
        self.log(f"game {g.id}: bot offers a draw")

        async def reply() -> None:
            await asyncio.sleep(1.5)
            if g.status != "started":
                return
            if random.random() < 0.5:
                g.finish("draw", None)
            else:
                g.wdraw = g.bdraw = False
                g.broadcast(g.state())
        asyncio.create_task(reply())

    async def _visit(self, ch: dict) -> None:
        """Someone opens the link and joins."""
        await asyncio.sleep(self.link_join)
        if self.challenges.get(ch["id"]) is not ch:
            return
        name, rating, _ = random.choice([x for x in FAKE_PLAYERS if not x[2]])
        ch["destUser"] = {"id": name.lower(), "name": name, "title": None, "rating": rating}
        if self.anon_visitor:
            ch["destUser"] = {"id": None, "name": None, "title": None, "rating": None}
            name = "an anonymous player"
        self.challenges.pop(ch["id"])
        seat = ch.get("seat")
        self.log(f"{name} opened link {ch['id']}")
        self.start_game(ch, None if not seat else seat == "white")

    async def _answer(self, ch: dict) -> None:
        await asyncio.sleep(2)
        if ch["id"] not in self.challenges:
            return
        if random.random() < 0.7:
            self.challenges.pop(ch["id"])
            self.start_game(ch)
        else:
            self.challenges.pop(ch["id"])
            ch["status"] = "declined"
            ch["declineReason"] = "I'm not accepting challenges at the moment."
            ch["declineReasonKey"] = "generic"
            self.event({"type": "challengeDeclined", "challenge": ch})


def _closed_queue() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    q.put_nowait(None)
    return q


async def amain(a) -> None:
    srv = MockLichess(Path(a.engine).resolve() if a.engine else None, a.every, a.max_games,
                      a.behavior.split(",") if a.behavior else None)
    srv.lag_ms = a.lag
    srv.link_join = a.link_join
    srv.open_scope = not a.no_open_scope
    srv.anon_visitor = a.anon_visitor
    srv.rated_prob = a.rated
    if a.tc:
        base, _, inc = a.tc.partition("+")
        srv.force_tc = (int(base), int(inc or 0))
    if a.human:
        srv.bot["title"] = None
        srv.bot["count"] = {"all": 0}
    server = await asyncio.start_server(srv.handle, "127.0.0.1", a.port)
    srv.log(f"listening on http://127.0.0.1:{a.port}  (opponent engine: {a.engine or 'random mover'})")
    gen = asyncio.create_task(srv.generator()) if a.every > 0 else None
    async with server:
        await server.serve_forever()
    if gen:
        gen.cancel()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--engine", default=None, help="UCI engine for the fake opponents (default: random mover)")
    ap.add_argument("--every", type=float, default=20, help="seconds between incoming challenges (0 = none)")
    ap.add_argument("--max-games", type=int, default=4)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--behavior", default=None, help="force opponent personalities, e.g. noshow,leaver,chatty")
    ap.add_argument("--human", action="store_true", help="start with a non-BOT account (to test --upgrade)")
    ap.add_argument("--lag", type=float, default=0, help="simulated network round trip in ms (e.g. 300)")
    ap.add_argument("--rated", type=float, default=0.6, help="share of rated incoming challenges")
    ap.add_argument("--tc", default=None, help="force the clock of incoming challenges, seconds: e.g. 30+1")
    ap.add_argument("--link-join", type=float, default=8, help="seconds until a visitor takes an open link (0 = never)")
    ap.add_argument("--anon-visitor", action="store_true", help="links are taken by anonymous players")
    ap.add_argument("--no-open-scope", action="store_true",
                    help="refuse open challenges made with the token (the bot must fall back to anonymous)")
    a = ap.parse_args()
    if a.seed is not None:
        random.seed(a.seed)
    try:
        asyncio.run(amain(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
