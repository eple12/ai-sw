"""Incoming challenge filtering."""
from __future__ import annotations

from dataclasses import dataclass, field
import time

# Lichess decline reason keys: generic later tooFast tooSlow timeControl rated casual standard variant noBot onlyBot
SPEEDS = ["ultraBullet", "bullet", "blitz", "rapid", "classical", "correspondence"]


@dataclass
class Challenge:
    id: str
    challenger: str
    challenger_id: str
    rating: int | None
    title: str | None
    is_bot: bool
    rated: bool
    speed: str
    variant: str
    limit: int | None           # seconds
    increment: int | None
    days: int | None
    color: str
    direction: str              # in | out
    raw: dict = field(repr=False, default_factory=dict)
    received: float = field(default_factory=time.monotonic)

    @classmethod
    def from_event(cls, c: dict, my_id: str) -> "Challenge":
        ch = c.get("challenger") or {}
        dest = c.get("destUser") or {}
        tc = c.get("timeControl") or {}
        direction = c.get("direction") or ("out" if ch.get("id") == my_id else "in")
        return cls(
            id=c["id"],
            challenger=(dest if direction == "out" else ch).get("name", "?"),
            challenger_id=(dest if direction == "out" else ch).get("id", ""),
            rating=(dest if direction == "out" else ch).get("rating"),
            title=(dest if direction == "out" else ch).get("title"),
            is_bot=(dest if direction == "out" else ch).get("title") == "BOT",
            rated=bool(c.get("rated")),
            speed=c.get("speed", "?"),
            variant=(c.get("variant") or {}).get("key", "standard"),
            limit=tc.get("limit"),
            increment=tc.get("increment"),
            days=tc.get("daysPerTurn"),
            color=c.get("finalColor") or c.get("color", "random"),
            direction=direction,
            raw=c,
        )

    @property
    def tc(self) -> str:
        if self.limit is not None:
            base = self.limit / 60
            b = f"{base:g}" if base >= 1 else {15: "¼", 30: "½", 45: "¾"}.get(self.limit, f"{base:.2g}")
            return f"{b}+{self.increment or 0}"
        if self.days:
            return f"{self.days}d"
        return "∞"

    def label(self) -> str:
        who = f"{self.title + ' ' if self.title else ''}{self.challenger}"
        rating = f" ({self.rating})" if self.rating else ""
        return f"{who}{rating} {self.tc} {'rated' if self.rated else 'casual'} {self.variant}"


def check(ch: Challenge, cfg: dict, games_vs: int) -> tuple[bool, str]:
    """Return (acceptable, decline_reason)."""
    c = cfg
    name = ch.challenger_id.lower()
    allow = [x.lower() for x in c["allow_list"]]
    if allow and name not in allow:
        return False, "generic"
    if name in [x.lower() for x in c["block_list"]]:
        return False, "generic"
    if ch.is_bot and not c["accept_bots"]:
        return False, "noBot"
    if not ch.is_bot and not c["accept_humans"]:
        return False, "onlyBot"
    if ch.variant not in c["variants"]:
        return False, "standard" if "standard" in c["variants"] else "variant"
    if ch.variant == "fromPosition":
        fen = ch.raw.get("initialFen", "")
        try:
            import chess
            if fen and chess.Board(fen) != chess.Board(fen, chess960=True):
                return False, "variant"      # Chess960-style castling is not supported
        except ValueError:
            return False, "variant"
    if ch.speed not in c["speeds"]:
        rank = {s: i for i, s in enumerate(SPEEDS)}
        ok = [rank[s] for s in c["speeds"] if s in rank]
        r = rank.get(ch.speed, -1)
        if ok and r > max(ok):
            return False, "tooSlow"
        if ok and 0 <= r < min(ok):
            return False, "tooFast"
        return False, "timeControl"
    if ch.limit is None:
        return False, "tooSlow"          # correspondence / unlimited are not supported
    inc = ch.increment or 0
    if ch.limit < c["min_base"]:
        return False, "tooFast"
    if ch.limit > c["max_base"]:
        return False, "tooSlow"
    min_inc = c["min_inc"]
    if ch.is_bot and ch.speed == "bullet" and c["bot_bullet_needs_inc"]:
        min_inc = max(min_inc, 1)
    if inc < min_inc:
        return False, "tooFast"
    if inc > c["max_inc"]:
        return False, "tooSlow"
    mode = "rated" if ch.rated else "casual"
    if mode not in c["modes"]:
        return False, "casual" if ch.rated else "rated"
    if ch.rating is not None and not (c["min_rating"] <= ch.rating <= c["max_rating"]):
        return False, "generic"
    if games_vs >= c["max_per_opponent"]:
        return False, "later"
    return True, ""
