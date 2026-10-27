"""Asynchronous Lichess API client (BOT API subset). Docs: https://lichess.org/api"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Callable

import httpx

from . import __version__

MAX_CHAT = 140


class LichessError(Exception):
    def __init__(self, status: int, text: str, path: str):
        super().__init__(f"HTTP {status} on {path}: {text[:200]}")
        self.status = status
        self.text = text
        self.path = path

    def json(self) -> dict:
        try:
            return json.loads(self.text)
        except ValueError:
            return {}


class Lichess:
    def __init__(self, base_url: str, token: str, log: Callable[[str, str], None]):
        self.base_url = base_url.rstrip("/")
        self.log = log
        self.headers = {"Authorization": f"Bearer {token}",
                        "User-Agent": f"ClaudyBot/{__version__}"}
        # Keep idle connections for a while: httpx closes them after 5 s by default, and then every move
        # (sent after the opponent thought > 5 s) paid a fresh TCP + TLS handshake, 3 round trips to Lichess.
        self.client = httpx.AsyncClient(base_url=self.base_url, headers=self.headers,
                                        timeout=httpx.Timeout(10.0, connect=10.0),
                                        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10,
                                                            keepalive_expiry=75.0))
        # Lichess asks clients to wait a full minute after HTTP 429
        self.blocked_until: dict[str, float] = {}

    async def close(self) -> None:
        await self.client.aclose()

    def set_username(self, username: str) -> None:
        self.headers["User-Agent"] = f"ClaudyBot/{__version__} user:{username}"
        self.client.headers["User-Agent"] = self.headers["User-Agent"]

    # ---- low level ---------------------------------------------------------
    async def _request(self, method: str, path: str, *, key: str | None = None, data: Any = None,
                       params: dict | None = None, retries: int = 3) -> httpx.Response:
        key = key or path
        wait = self.blocked_until.get(key, 0) - time.monotonic()
        if wait > 0:
            raise LichessError(429, f"rate limited, retry in {wait:.0f}s", path)
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                r = await self.client.request(method, path, data=data, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last_exc = e
                # a move on a connection the server has just closed: retry at once on a new one
                await asyncio.sleep(0 if key == "move" and attempt == 0 else 0.5 * (attempt + 1))
                continue
            if r.status_code == 429:
                if key == "move" and attempt + 1 < retries:
                    await asyncio.sleep(1.0)          # never park the move endpoint for a minute
                    continue
                self.blocked_until[key] = time.monotonic() + (1 if key == "move" else 60)
                self.log("warning", f"rate limited on {path}; pausing that endpoint")
                raise LichessError(429, r.text, path)
            if r.status_code >= 500 and attempt + 1 < retries:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise LichessError(r.status_code, r.text, path)
            return r
        raise LichessError(0, f"connection failed: {last_exc!r}", path)

    async def get_json(self, path: str, **kw) -> Any:
        return (await self._request("GET", path, **kw)).json()

    async def post(self, path: str, **kw) -> Any:
        r = await self._request("POST", path, **kw)
        try:
            return r.json()
        except ValueError:
            return {}

    async def stream(self, path: str, read_timeout: float = 30.0) -> AsyncIterator[dict]:
        """Yield ndjson objects; `{}` for keep-alive newlines. Raises on disconnect."""
        timeout = httpx.Timeout(10.0, read=read_timeout)
        async with self.client.stream("GET", path, timeout=timeout) as r:
            if r.status_code >= 400:
                body = (await r.aread()).decode(errors="replace")
                if r.status_code == 429:
                    self.blocked_until[path] = time.monotonic() + 60
                raise LichessError(r.status_code, body, path)
            async for line in r.aiter_lines():
                line = line.strip()
                if not line:
                    yield {}
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    self.log("warning", f"bad json from {path}: {line[:120]}")

    # ---- endpoints -----------------------------------------------------------
    async def account(self) -> dict:
        return await self.get_json("/api/account")

    async def playing(self) -> list[dict]:
        return (await self.get_json("/api/account/playing")).get("nowPlaying", [])

    def event_stream(self) -> AsyncIterator[dict]:
        return self.stream("/api/stream/event")

    def game_stream(self, game_id: str) -> AsyncIterator[dict]:
        return self.stream(f"/api/bot/game/stream/{game_id}")

    async def accept(self, challenge_id: str, color: str | None = None) -> None:
        """`color` only for open challenges: take that seat (the first to accept an open challenge becomes
        its challenger, the second one starts the game)."""
        params = {"color": color} if color in ("white", "black") else None
        await self.post(f"/api/challenge/{challenge_id}/accept", key="accept", params=params)

    async def open_challenge(self, *, limit: int, increment: int, rated: bool, name: str = "",
                             expires_ms: int | None = None) -> dict:
        """Create an open challenge ("challenge a friend" link). Needs the challenge:write scope, which BOT
        tokens often lack; an open challenge may also be created anonymously, so fall back to that."""
        data = {"rated": "true" if rated else "false", "clock.limit": str(limit), "clock.increment": str(increment)}
        if name:
            data["name"] = name
        if expires_ms:
            data["expiresAt"] = str(expires_ms)
        try:
            return await self.post("/api/challenge/open", key="open", data=data, retries=1)
        except LichessError as e:
            if e.status not in (401, 403):
                raise
        async with httpx.AsyncClient(base_url=self.base_url, timeout=httpx.Timeout(10.0),
                                     headers={"User-Agent": self.headers["User-Agent"]}) as anon:
            r = await anon.post("/api/challenge/open", data=data)
        if r.status_code >= 400:
            raise LichessError(r.status_code, r.text, "/api/challenge/open")
        return r.json()

    async def decline(self, challenge_id: str, reason: str = "generic") -> None:
        await self.post(f"/api/challenge/{challenge_id}/decline", key="decline", data={"reason": reason})

    async def create_challenge(self, username: str, *, limit: int, increment: int, rated: bool,
                               color: str = "random", variant: str = "standard") -> dict:
        data = {"rated": "true" if rated else "false", "clock.limit": str(limit),
                "clock.increment": str(increment), "color": color, "variant": variant}
        return await self.post(f"/api/challenge/{username}", key="challenge", data=data, retries=1)

    async def cancel_challenge(self, challenge_id: str) -> None:
        await self.post(f"/api/challenge/{challenge_id}/cancel", key="cancel")

    async def move(self, game_id: str, uci: str, offer_draw: bool = False) -> None:
        params = {"offeringDraw": "true"} if offer_draw else None
        await self.post(f"/api/bot/game/{game_id}/move/{uci}", key="move", params=params, retries=4)

    async def resign(self, game_id: str) -> None:
        await self.post(f"/api/bot/game/{game_id}/resign", key="resign")

    async def abort(self, game_id: str) -> None:
        await self.post(f"/api/bot/game/{game_id}/abort", key="abort")

    async def draw(self, game_id: str, accept: bool = True) -> None:
        await self.post(f"/api/bot/game/{game_id}/draw/{'yes' if accept else 'no'}", key="draw")

    async def takeback(self, game_id: str, accept: bool) -> None:
        await self.post(f"/api/bot/game/{game_id}/takeback/{'yes' if accept else 'no'}", key="takeback")

    async def claim_victory(self, game_id: str) -> None:
        await self.post(f"/api/bot/game/{game_id}/claim-victory", key="claim")

    async def chat(self, game_id: str, room: str, text: str) -> None:
        text = text[:MAX_CHAT]
        await self.post(f"/api/bot/game/{game_id}/chat", key="chat", data={"room": room, "text": text})

    async def online_bots(self, nb: int = 100) -> list[dict]:
        r = await self._request("GET", "/api/bot/online", params={"nb": str(nb)})
        out = []
        for line in r.text.splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out
