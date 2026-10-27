"""Bot manager: event stream, challenge queue, games, matchmaking."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import Counter, deque
from typing import Callable

import httpx

from .challenges import Challenge, check
from .config import Config, token_shape
from .game import GameSession, LagTracker
from .lichess import Lichess, LichessError

LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


def speed_of(limit: int, inc: int) -> str:
    est = limit + 40 * inc
    if est < 30:
        return "ultraBullet"
    if est < 180:
        return "bullet"
    if est < 480:
        return "blitz"
    if est < 1500:
        return "rapid"
    return "classical"


def parse_tc(tc: str) -> tuple[int, int]:
    """'3+2' -> (180, 2); '0.5+0' -> (30, 0)."""
    base, _, inc = tc.partition("+")
    return int(round(float(base) * 60)), int(inc or 0)


def fmt_tc(limit: int, inc: int) -> str:
    """(180, 2) -> '3+2'; (30, 0) -> '0.5+0'."""
    return f"{limit / 60:g}+{inc}"


KINDS = ("bot", "human")


class BotManager:
    def __init__(self, cfg: Config, url: str | None = None, echo: Callable[[str], None] | None = None,
                 offline: bool = False):
        self.cfg = cfg
        self.offline_only = offline                 # never connect to Lichess (local games and analysis only)
        self.online = False                         # logged in to Lichess
        self.base_url = (url or cfg.get("url")).rstrip("/")
        self.web_url = self.base_url
        self.echo = echo
        self.events: deque[tuple[float, str, str]] = deque(maxlen=2000)
        self.events_total = 0
        self._setup_file_log()
        self.li = Lichess(self.base_url, cfg.token(), self.log)
        self.username = "?"
        self.user_id = "?"
        self.account: dict = {}
        self.games: dict[str, GameSession] = {}
        self.finished: deque[GameSession] = deque(maxlen=50)
        self.queue: list[Challenge] = []
        self.pending_accept: dict[str, tuple[float, str]] = {}   # challenge id -> (accepted at, "bot" | "human")
        self.kind_hint: dict[str, str] = {}         # game id -> "bot" | "human", known from its challenge
        self.links: dict[str, dict] = {}            # open "challenge a friend" links, see create_link
        self.outgoing: dict[str, tuple[Challenge, float]] = {}
        self.results: Counter = Counter()
        self.started = time.time()
        self.stream_ok = False
        self.last_event_at = 0.0
        self.last_idle_start = time.monotonic()
        self.last_mm_attempt = 0.0
        self.mm_avoid: dict[str, float] = {}
        self.quitting: str | None = None           # None | "graceful" | "now"
        self.restart_requested = False             # exit with code 75: a wrapper (android/run.sh) updates + restarts
        self.stopped = asyncio.Event()
        self.fatal: str | None = None
        self.tasks: list[asyncio.Task] = []
        self.web = None                             # WebServer (browser dashboard), see web.py
        self.tunnel = None                          # Tunnel (public telemetry), see tunnel.py
        self._tunnel_task: asyncio.Task | None = None
        self.lag = LagTracker()                     # network lag per move, shared by all games
        self.local_seq = 0                          # local games (local.py): ids local1, local2, ...
        self.analysis = None                        # Analysis (analysis.py), created when first used

    # ---- logging -------------------------------------------------------------
    def _setup_file_log(self) -> None:
        path = self.cfg.resolve(self.cfg.get("log.file"))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file_log = logging.getLogger("claudybot")
        self.file_log.setLevel(logging.DEBUG)
        if not self.file_log.handlers:
            h = logging.FileHandler(path, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.file_log.addHandler(h)

    def log(self, level: str, msg: str) -> None:
        self.file_log.log(LEVELS.get(level, 20), msg)
        if LEVELS.get(level, 20) < LEVELS.get(self.cfg.get("log.level"), 20):
            return
        self.events.append((time.time(), level, msg))
        self.events_total += 1
        if self.echo:
            self.echo(f"{time.strftime('%H:%M:%S')} {level.upper():7} {msg}")

    # ---- state -----------------------------------------------------------------
    @property
    def paused(self) -> bool:
        return not self.cfg.get("challenge.accept")

    def active_count(self) -> int:
        return len(self.games) + len(self.pending_accept)

    # ---- slots: concurrency (all games) and optional separate limits for bots and humans ------------
    def game_kind(self, g: GameSession) -> str:
        if g.opponent_is_bot() or self.kind_hint.get(g.id) == "bot":
            return "bot"
        return "human"

    def slot_cap(self, kind: str) -> int:
        total = int(self.cfg.get("challenge.concurrency"))
        cap = int(self.cfg.get(f"challenge.concurrency_{kind}"))
        return total if cap < 0 else min(cap, total)

    def slot_usage(self) -> dict[str, int]:
        use = {k: 0 for k in KINDS}
        for g in self.games.values():
            use[self.game_kind(g)] += 1
        for _, kind in self.pending_accept.values():
            use[kind] += 1
        return use

    def has_slot(self, kind: str) -> bool:
        use = self.slot_usage()
        return sum(use.values()) < int(self.cfg.get("challenge.concurrency")) and use[kind] < self.slot_cap(kind)

    def split_slots(self) -> bool:
        return any(int(self.cfg.get(f"challenge.concurrency_{k}")) >= 0 for k in KINDS)

    def slots_text(self) -> str:
        """'2/3 (bots 2/2, humans 0/3)' - the per-kind part only when a separate limit is set."""
        use = self.slot_usage()
        out = f"{sum(use.values())}/{self.cfg.get('challenge.concurrency')}"
        if self.split_slots():
            out += f" (bots {use['bot']}/{self.slot_cap('bot')}, humans {use['human']}/{self.slot_cap('human')})"
        return out

    def game_list(self) -> list[GameSession]:
        return list(self.games.values())

    # ---- lifecycle ---------------------------------------------------------------
    async def run(self) -> None:
        if self.web is not None and self.web.server is None:
            try:
                await self.web.start()
            except OSError as e:
                self.log("error", f"web dashboard: cannot listen on {self.web.host}:{self.web.port} ({e.strerror or e})")
            if self.web.pub_server and self.cfg.get("web.tunnel") and self.tunnel is None:
                from .tunnel import Tunnel
                self.tunnel = Tunnel(self, int(self.cfg.get("web.public_port")), str(self.cfg.get("web.tunnel_exe")),
                                     str(self.cfg.get("web.tunnel_gist") or ""))
                self._tunnel_task = asyncio.create_task(self.tunnel.start(), name="tunnel")
        self.tasks = [asyncio.create_task(self._housekeeping(), name="housekeeping")]
        if self.offline_only:
            self.log("warning", "offline mode: no Lichess connection - play the engine (`play`) or analyse "
                                "positions (`analyze`) on this device")
        elif not await self._login():
            if self.stopped.is_set():
                return
            self.log("warning", f"cannot reach {self.base_url}: working offline (local games and analysis work); "
                                f"trying again every minute")
            self.tasks.append(asyncio.create_task(self._login_retry(), name="login"))
        await self.stopped.wait()

    async def _login_retry(self) -> None:
        while not self.stopped.is_set():
            await asyncio.sleep(60)
            if await self._login():
                return

    async def _login(self) -> bool:
        """Log in, resume running games, start the event stream. False when Lichess cannot be reached."""
        try:
            acct = await self.li.account()
        except LichessError as e:
            if e.status == 401:
                tok, src = self.cfg.token_source()
                self.fatal = (f"token rejected by Lichess (HTTP 401): {token_shape(tok)}, from {src}. "
                              f"A personal token usually looks like lip_ + 20 letters/digits, created while "
                              f"logged in as the BOT account (python -m claudybot --check shows details)")
                self.log("error", self.fatal)
                self.stopped.set()
                return False
            self.log("debug", f"login failed: {e}")
            return False
        except httpx.HTTPError as e:
            self.log("debug", f"login failed: {e!r}")
            return False
        self.online = True
        self.account = acct
        self.username = acct.get("username", "?")
        self.user_id = acct.get("id", self.username.lower())
        self.li.set_username(self.username)
        if acct.get("title") != "BOT":
            self.log("warning", f"{self.username} is not a BOT account - the Bot API will refuse to play")
        self.log("info", f"logged in as {self.username} on {self.base_url}")
        self.load_links()
        try:
            for g in await self.li.playing():
                gid = g.get("gameId") or g.get("id")
                if gid and gid not in self.games:
                    self.log("info", f"resuming ongoing game {gid}")
                    self._start_game(gid, g)
        except (LichessError, httpx.HTTPError) as e:
            self.log("warning", f"could not list ongoing games: {e}")
        self.tasks.append(asyncio.create_task(self._event_loop(), name="events"))
        return True

    async def close(self) -> None:
        for g in list(self.games.values()):
            g.cancel()
        for t in self.tasks:
            t.cancel()
        # give cancelled games a moment to kill their engines
        for _ in range(30):
            if not self.games:
                break
            await asyncio.sleep(0.1)
        for g in list(self.games.values()):
            if g.engine:
                await g.engine.quit()
        if self.analysis is not None:
            await self.analysis.close()
        await self.li.close()
        if self.tunnel is not None:
            if self._tunnel_task and not self._tunnel_task.done():
                self._tunnel_task.cancel()
            await self.tunnel.stop()
        if self.web is not None:
            await self.web.stop()

    def online_games(self) -> list[GameSession]:
        return [g for g in self.games.values() if not g.local]

    async def quit(self, now: bool = False) -> None:
        if now or not self.online_games():
            self.quitting = "now"
            self.stopped.set()
            return
        self.quitting = "graceful"
        self.log("info", f"quitting after {len(self.online_games())} running game(s) finish; new challenges are "
                         f"declined")
        for ch in list(self.queue):
            await self.decline(ch.id, "later")

    # ---- event stream ----------------------------------------------------------------
    async def _event_loop(self) -> None:
        failures = 0
        while not self.stopped.is_set():
            try:
                async for ev in self.li.event_stream():
                    if not self.stream_ok:
                        self.log("info", "event stream connected")
                    self.stream_ok = True
                    failures = 0
                    self.last_event_at = time.monotonic()
                    if ev:
                        try:
                            await self._on_event(ev)
                        except Exception as e:
                            self.log("error", f"event handling failed: {e!r} ({ev.get('type')})")
            except LichessError as e:
                if e.status == 401:
                    self.fatal = "token rejected (HTTP 401)"
                    self.log("error", self.fatal)
                    self.stopped.set()
                    return
                self.log("warning", f"event stream: {e}")
            except (httpx.HTTPError, OSError) as e:
                self.log("warning", f"event stream disconnected: {e!r}")
            self.stream_ok = False
            failures += 1
            await asyncio.sleep(min(2 ** min(failures, 5), 60))

    async def _on_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "challenge":
            await self._on_challenge(ev["challenge"])
        elif t == "challengeCanceled":
            cid = ev["challenge"]["id"]
            self.queue = [c for c in self.queue if c.id != cid]
            self.outgoing.pop(cid, None)
            if self.links.pop(cid, None):
                self._save_links()
                self.log("info", f"link {cid} closed")
            else:
                self.log("info", f"challenge {cid} canceled")
        elif t == "challengeDeclined":
            c = ev["challenge"]
            cid = c["id"]
            ch = self.outgoing.pop(cid, None)
            who = ch[0].challenger if ch else (c.get("destUser") or {}).get("name", "?")
            self.log("info", f"{who} declined our challenge: {c.get('declineReason') or c.get('declineReasonKey', '')}")
            self.mm_avoid[who.lower()] = time.monotonic() + 3600
        elif t == "gameStart":
            g = ev["game"]
            gid = g.get("gameId") or g.get("id")
            self.pending_accept.pop(gid, None)
            self.outgoing.pop(gid, None)
            link = self.links.pop(gid, None)
            if link:
                self._save_links()
                who = (g.get("opponent") or {}).get("username") or "an anonymous player"
                self.log("info", f"link {gid} ({link['tc']}) was taken by {who}")
            if gid not in self.games:
                game = self._start_game(gid, g)
                if game and link and link.get("elo"):
                    game.limit_elo = int(link["elo"])      # applied when its engine starts
                    game.preset_limit = True
                    self.log("info", f"link game {gid}: rating limit {game.limit_elo}")
        elif t == "gameFinish":
            g = ev["game"]
            gid = g.get("gameId") or g.get("id")
            self.pending_accept.pop(gid, None)

    def _start_game(self, gid: str, start_event: dict | None) -> GameSession | None:
        if self.stopped.is_set():
            return None
        g = GameSession(self, gid, start_event)
        self.games[gid] = g
        opp = (start_event or {}).get("opponent") or {}
        if str(opp.get("username") or "").startswith("BOT ") or opp.get("title") == "BOT":
            self.kind_hint[gid] = "bot"
        self.log("info", f"game {gid} started vs {opp.get('username') or 'anonymous'} ({self.web_url}/{gid})")
        g.start()
        return g

    def on_game_end(self, g: GameSession) -> None:
        self.games.pop(g.id, None)
        self.kind_hint.pop(g.id, None)
        self.finished.appendleft(g)
        if g.moves and not g.local:
            self.results[g.outcome_for_me()] += 1
        if not self.games:
            self.last_idle_start = time.monotonic()
        if self.quitting == "graceful" and not self.online_games():
            self.log("info", "all games finished; quitting")
            self.stopped.set()
            return
        asyncio.create_task(self._accept_from_queue())

    # ---- challenges ----------------------------------------------------------------
    async def _on_challenge(self, c: dict) -> None:
        ch = Challenge.from_event(c, self.user_id)
        if ch.id in self.links:
            return
        if ch.direction == "out" or ch.challenger_id == self.user_id:
            self.outgoing[ch.id] = (ch, time.monotonic())
            return
        self.log("info", f"challenge {ch.id} from {ch.label()}")
        if self.quitting:
            await self.decline(ch.id, "later")
            return
        if self.paused:
            if self.cfg.get("challenge.paused_action") == "decline":
                await self.decline(ch.id, "later")
            else:
                self.log("info", f"paused: leaving challenge {ch.id} unanswered")
            return
        vs = sum(1 for g in self.games.values() if (g.opponent.get("id") or "").lower() == ch.challenger_id)
        vs += sum(1 for q in self.queue if q.challenger_id == ch.challenger_id)
        ok, reason = check(ch, self.cfg.get("challenge"), vs)
        if not ok:
            await self.decline(ch.id, reason)
            return
        kind = "bot" if ch.is_bot else "human"
        if self.slot_cap(kind) <= 0:
            await self.decline(ch.id, "noBot" if ch.is_bot else "onlyBot")
            return
        if not self.has_slot(kind):
            # bots are told to come back later at once: a bot's challenge rarely waits long enough to be
            # accepted from the queue, and the queue stays free for humans
            if ch.is_bot or len(self.queue) >= self.cfg.get("challenge.queue_size"):
                self.log("info", f"no free {kind} slot: games {self.slots_text()}")
                await self.decline(ch.id, "later")
                return
        self.queue.append(ch)
        if self.cfg.get("challenge.sort") == "best":
            self.queue.sort(key=lambda q: -(q.rating or 0))
        await self._accept_from_queue()

    async def _accept_from_queue(self) -> None:
        while self.queue and not self.paused and not self.quitting:
            ch = next((q for q in self.queue if self.has_slot("bot" if q.is_bot else "human")), None)
            if ch is None:
                return
            self.queue.remove(ch)
            await self.accept(ch.id, "bot" if ch.is_bot else "human")

    async def accept(self, cid: str, kind: str = "human") -> bool:
        try:
            await self.li.accept(cid)
            self.pending_accept[cid] = (time.monotonic(), kind)
            self.kind_hint[cid] = kind
            self.log("info", f"accepted challenge {cid}")
            return True
        except LichessError as e:
            self.log("warning", f"accept {cid} failed: {e}")
            return False

    async def decline(self, cid: str, reason: str = "generic") -> None:
        self.queue = [c for c in self.queue if c.id != cid]
        try:
            await self.li.decline(cid, reason)
            self.log("info", f"declined challenge {cid} ({reason})")
        except LichessError as e:
            self.log("warning", f"decline {cid} failed: {e}")

    async def send_challenge(self, user: str, limit: int, inc: int, rated: bool, color: str = "random") -> None:
        try:
            r = await self.li.create_challenge(user, limit=limit, increment=inc, rated=rated, color=color)
        except LichessError as e:
            msg = e.json().get("error") or str(e)
            self.log("warning", f"challenge to {user} failed: {msg}")
            if e.status == 400:
                self.mm_avoid[user.lower()] = time.monotonic() + 3600
            return
        c = r.get("challenge", r)
        if "id" in c:
            ch = Challenge.from_event(c, self.user_id)
            self.outgoing[ch.id] = (ch, time.monotonic())
            self.kind_hint[ch.id] = "bot" if ch.is_bot else "human"
            self.log("info", f"challenged {user} ({limit // 60 if limit >= 60 else limit / 60:g}+{inc}, "
                             f"{'rated' if rated else 'casual'}) id {ch.id}")
        else:
            self.log("warning", f"unexpected challenge response: {str(r)[:200]}")

    async def cancel_challenge(self, cid: str) -> None:
        self.outgoing.pop(cid, None)
        try:
            await self.li.cancel_challenge(cid)
            self.log("info", f"canceled our challenge {cid}")
        except LichessError as e:
            self.log("warning", f"cancel {cid} failed: {e}")

    # ---- "challenge a friend" links -----------------------------------------------------------------
    # An open challenge (POST /api/challenge/open) that we join at once as its first player: whoever opens
    # the link afterwards plays us. Unlike a challenge made on the website it needs no open browser tab to
    # stay alive; it stays open until it is taken, expires (link.hours, at most 2 weeks) or is canceled.
    def _links_file(self):
        return self.cfg.resolve(self.cfg.get("log.file")).parent / "links.json"

    def _save_links(self) -> None:
        try:
            self._links_file().write_text(json.dumps(self.links, indent=1), encoding="utf-8")
        except OSError:
            pass

    def load_links(self) -> None:
        """Links outlive the bot: show the ones made by an earlier run (same server) again."""
        try:
            data = json.loads(self._links_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        now = time.time()
        self.links = {k: v for k, v in data.items()
                      if isinstance(v, dict) and v.get("expires", 0) > now and v.get("server") == self.base_url}

    async def create_link(self, limit: int, inc: int, rated: bool, color: str = "random",
                          hours: float = 24, elo: int | None = None) -> dict:
        if speed_of(limit, inc) == "ultraBullet":
            raise ValueError("BOT accounts cannot play UltraBullet")
        hours = max(0.1, min(float(hours), 335.9))
        expires = time.time() + hours * 3600
        if elo is not None and not 100 <= elo <= 3400:
            raise ValueError("rating must be 100-3400")
        if not self.online:
            raise ValueError("not connected to Lichess")
        tc = fmt_tc(limit, inc)
        mode = "rated" if rated else "casual"
        level = f" · plays at {elo}" if elo else ""
        r = await self.li.open_challenge(limit=limit, increment=inc, rated=rated,
                                         name=f"{self.username} {tc} {mode}{level}", expires_ms=int(expires * 1000))
        c = r.get("challenge", r)
        cid = c.get("id")
        if not cid:
            raise ValueError(f"unexpected answer from Lichess: {str(r)[:200]}")
        try:
            await self.li.accept(cid, None if color == "random" else color)
        except LichessError:
            try:
                await self.li.cancel_challenge(cid)
            except LichessError:
                pass
            raise
        url = c.get("url") or f"{self.web_url}/{cid}"
        link = {"id": cid, "url": url, "tc": tc, "rated": rated, "color": color, "elo": elo,
                "created": time.time(), "expires": expires, "server": self.base_url}
        self.links[cid] = link
        self._save_links()
        self.log("info", f"challenge link {tc} {mode}{level}, we play {color}, open for {hours:g} h: {url}")
        return link

    async def cancel_link(self, cid: str) -> bool:
        link = self.links.pop(cid, None)
        self._save_links()
        if not link:
            return False
        try:
            await self.li.cancel_challenge(cid)
            self.log("info", f"link {cid} canceled")
        except LichessError as e:
            self.log("warning", f"cancel link {cid}: {e}")
        return True

    # ---- games on this device (local.py) and analysis (analysis.py) ---------------------------------------
    def start_local(self, *, human_color: bool, limit_s: int | None, inc_s: int, elo: int | None,
                    fen: str | None = None) -> GameSession:
        from .local import LocalGame
        self.local_seq += 1
        gid = f"local{self.local_seq}"
        while gid in self.games or any(f.id == gid for f in self.finished):
            self.local_seq += 1
            gid = f"local{self.local_seq}"
        lc = self.cfg.get("local")
        g = LocalGame(self, gid, human_color=human_color, limit_s=limit_s, inc_s=inc_s, elo=elo, fen=fen,
                      movetime_ms=int(lc["movetime_ms"]), human_name=str(lc["name"] or "You"))
        self.games[gid] = g
        tc = "untimed" if limit_s is None else fmt_tc(limit_s, inc_s)
        self.log("info", f"local game {gid}: you play {'white' if human_color else 'black'}, {tc}, engine "
                         f"{'at ' + str(elo) if elo else 'at full strength'}")
        g.start()
        return g

    def get_analysis(self):
        if self.analysis is None:
            from .analysis import Analysis
            self.analysis = Analysis(self)
        return self.analysis

    # ---- matchmaking ------------------------------------------------------------------
    async def matchmake(self, force: bool = False) -> None:
        mm = self.cfg.get("matchmaking")
        self.last_mm_attempt = time.monotonic()
        try:
            bots = await self.li.online_bots(150)
        except (LichessError, httpx.HTTPError) as e:
            self.log("warning", f"matchmaking: cannot list online bots: {e}")
            return
        tc = random.choice(mm["tc"] or ["3+2"])
        limit, inc = parse_tc(tc)
        speed = speed_of(limit, inc)
        now = time.monotonic()
        block = {b.lower() for b in mm["block_list"]} | {b.lower() for b in self.cfg.get("challenge.block_list")}
        cands = []
        for b in bots:
            bid = b.get("id", "")
            if bid == self.user_id or bid in block or self.mm_avoid.get(bid, 0) > now or b.get("disabled"):
                continue
            perf = (b.get("perfs") or {}).get(speed) or {}
            r = perf.get("rating")
            if r is None or perf.get("prov") or not (mm["min_rating"] <= r <= mm["max_rating"]):
                continue
            cands.append((b.get("username", bid), r))
        if not cands:
            self.log("info", f"matchmaking: no suitable online bot for {speed} {tc}")
            return
        user, r = random.choice(cands)
        self.log("info", f"matchmaking: challenging {user} ({r}) {tc}")
        await self.send_challenge(user, limit, inc, mm["rated"])

    async def _housekeeping(self) -> None:
        while not self.stopped.is_set():
            await asyncio.sleep(2.0)
            now = time.monotonic()
            for cid, (t, _) in list(self.pending_accept.items()):
                if now - t > 30:
                    self.pending_accept.pop(cid, None)
                    self.log("warning", f"accepted challenge {cid} never started")
            timeout = self.cfg.get("matchmaking.timeout_seconds")
            for cid, (ch, t) in list(self.outgoing.items()):
                if now - t > timeout:
                    self.mm_avoid[ch.challenger.lower()] = now + 1800
                    await self.cancel_challenge(cid)
            if self.queue and not self.paused:
                await self._accept_from_queue()
            expired = [cid for cid, ln in self.links.items() if ln["expires"] < time.time()]
            for cid in expired:
                self.links.pop(cid, None)
                self.log("info", f"link {cid} expired")
            if expired:
                self._save_links()
            mm = self.cfg.get("matchmaking")
            if (mm["enabled"] and not self.paused and not self.quitting and self.active_count() == 0
                    and self.has_slot("bot")
                    and not self.outgoing and not self.queue and self.stream_ok
                    and now - self.last_idle_start > mm["idle_seconds"]
                    and now - self.last_mm_attempt > max(30, mm["idle_seconds"] / 2)):
                await self.matchmake()
