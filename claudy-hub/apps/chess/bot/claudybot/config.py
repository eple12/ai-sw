"""Configuration: defaults, YAML loading/saving and typed runtime changes (`set key value`)."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

BOT_DIR = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "token": "",                      # or env LICHESS_BOT_TOKEN, or token_file
    "token_file": "",
    "url": "https://lichess.org",
    "engine": {
        "path": "../claudy.exe",      # relative to the bot/ folder
        "ponder": True,
        "first_move_ms": 3000,        # our first move (the clock is not running yet)
        "lag_compensation": True,     # measure the network lag per move and give it to the engine
        "options": {
            "Hash": 256,
            "Threads": 2,
            "Move Overhead": 100,
            "SyzygyPath": "",
        },
    },
    "challenge": {
        "accept": True,               # False = paused (see paused_action)
        "paused_action": "decline",   # decline | ignore  (what to do with challenges while paused)
        "concurrency": 2,             # simultaneous games (all opponents together)
        "concurrency_bot": -1,        # at most this many of them against bots (-1 = no separate limit)
        "concurrency_human": -1,      # at most this many against humans (-1 = no separate limit)
                                      # e.g. concurrency 3 + concurrency_bot 2: one slot always stays free for humans
        "queue_size": 4,              # accepted-later challenges waiting for a free slot
        "variants": ["standard"],     # standard, fromPosition
        "speeds": ["bullet", "blitz", "rapid", "classical"],
        "modes": ["rated", "casual"],
        "min_base": 0,                # seconds
        "max_base": 3600,
        "min_inc": 0,
        "max_inc": 180,
        "bot_bullet_needs_inc": False,
        "accept_bots": True,
        "accept_humans": True,
        "min_rating": 0,
        "max_rating": 4000,
        "max_per_opponent": 1,        # simultaneous games against the same opponent
        "allow_list": [],             # non-empty = only these users
        "block_list": [],
        "sort": "first",              # first | best  (queue order)
    },
    "matchmaking": {
        "enabled": False,
        "idle_seconds": 90,           # challenge a bot after being idle this long
        "tc": ["3+2", "5+3", "1+1"],
        "rated": True,
        "min_rating": 1800,
        "max_rating": 3500,
        "timeout_seconds": 45,        # cancel our challenge if not answered
        "block_list": [],
    },
    "game": {
        "abort_seconds": 30,          # abort if the opponent does not make its first move
        "claim_victory": True,        # when the opponent left the game
        "resign_enabled": False,
        "resign_score": -1000,        # cp, our point of view
        "resign_moves": 5,            # consecutive own moves at or below resign_score
        "draw_accept": True,
        "draw_accept_score": -30,     # accept draw offers when our eval <= this (cp)
        "draw_offer": False,
        "draw_offer_score": 8,        # offer when |eval| <= this for draw_offer_moves own moves
        "draw_offer_moves": 10,
        "draw_offer_min_ply": 80,
        "greeting": "Hi {opponent}! I'm {me}, running {engine}. Good luck! Type !help for commands.",
        "greeting_spectators": "Hi! I'm {me}, running {engine}. Type !eval to see my evaluation.",
        "goodbye": "Good game, {opponent}!",
        "chat_commands": True,
        "pgn_dir": "games",
    },
    "link": {                         # "challenge a friend" links (command `link`, dashboard button)
        "tc": "5+3",                  # default clock (minutes+increment)
        "rated": False,
        "color": "random",            # our color: random | white | black
        "hours": 24,                  # the link stays open this long (Lichess allows up to 2 weeks = 336 h)
    },
    "local": {                        # games against the engine on this device (command `play`, dashboard)
        "name": "You",                # your name in the game and its PGN
        "tc": "10+5",                 # default clock (minutes+increment) or "untimed"
        "color": "random",            # your color: random | white | black
        "elo": 1500,                  # the engine's default rating (0 = full strength)
        "movetime_ms": 2000,          # engine time per move in untimed games
    },
    "analysis": {                     # analysis board (command `analyze`, dashboard)
        "multipv": 3,                 # engine lines shown
        "threads": 0,                 # engine threads (0 = engine.options.Threads)
        "hash": 0,                    # MB (0 = engine.options.Hash)
        "game_ms": 1000,              # time per position when a whole game is analysed
    },
    "strength": {                     # rating-limited games: the opponent types "!diff <rating>" in the chat
        "accept": True,               # accept such requests at all
        "modes": "casual",            # casual | rated | both
        "opponents": "human",         # human | bot | both
        "min": 100,                   # lowest rating offered
        "max": 3400,                  # highest (the engine's full strength is 3400)
        "step": 1,                    # available ratings: min, min+step, min+2*step, ... (1 = every rating)
        "announce": True,             # tell eligible opponents about !diff when the game starts
    },
    "ui": {
        "pieces": "image",            # image | sprites | letters | unicode
        "piece_set": "caliente",      # folder in bot/assets (image mode)
        "cell_aspect": 2.12,          # physical cell height / width (Windows Terminal + Cascadia Mono)
        "refresh_ms": 250,
    },
    "web": {
        "enabled": False,             # browser dashboard (also: --web)
        "host": "127.0.0.1",          # 0.0.0.0 = reachable from other devices (then an access key is required)
        "port": 8080,
        "key": "",                    # access key for network use; generated at start when empty
        "public": False,              # serve /api/public/state (read-only, any origin) for a mirror page
        "public_port": 0,             # >0: separate listener with only /api/public/state (safe to put behind a tunnel)
        "tunnel": False,              # start a Cloudflare quick tunnel to public_port (needs cloudflared)
        "tunnel_exe": "cloudflared",  # path of cloudflared(.exe)
        "tunnel_gist": "",            # gist id where the tunnel address is published for the viewer (needs gh)
    },
    "log": {
        "file": "logs/claudybot.log",
        "level": "info",
    },
}

# Types for keys whose default does not tell (None) or that accept several forms.
LIST_KEYS = {"challenge.variants", "challenge.speeds", "challenge.modes", "challenge.allow_list",
             "challenge.block_list", "matchmaking.tc", "matchmaking.block_list"}
CHOICES = {
    "challenge.paused_action": ["decline", "ignore"],
    "challenge.sort": ["first", "best"],
    "ui.pieces": ["image", "sprites", "letters", "unicode"],
    "link.color": ["random", "white", "black"],
    "local.color": ["random", "white", "black"],
    "strength.modes": ["casual", "rated", "both"],
    "strength.opponents": ["human", "bot", "both"],
    "log.level": ["debug", "info", "warning"],
}
SECRET_KEYS = {"token", "token_file", "web.key"}


def clean_token(raw: str) -> str:
    """Lichess tokens are plain ASCII (lip_ + letters/digits). Drop what copy & paste tends to add:
    spaces, line breaks, BOM / zero-width / non-breaking characters, bracketed-paste markers."""
    s = raw.replace("[200~", "").replace("[201~", "")
    return "".join(ch for ch in s if ch.isascii() and ch.isprintable() and not ch.isspace())


def token_shape(tok: str) -> str:
    """Describe a token without revealing it (for error messages)."""
    if not tok:
        return "no token"
    fmt = "starts with lip_" if tok.startswith("lip_") else "does NOT start with lip_"
    odd = sum(1 for ch in tok if not (ch.isalnum() or ch == "_"))
    return f"{len(tok)} characters, {fmt}" + (f", {odd} unusual character(s)" if odd else "")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def parse_bool(s: str) -> bool:
    t = s.strip().lower()
    if t in ("1", "true", "yes", "on", "y"):
        return True
    if t in ("0", "false", "no", "off", "n"):
        return False
    raise ValueError(f"not a boolean: {s}")


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (BOT_DIR / "config.yml")
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            self.data = deep_merge(DEFAULTS, user)

    # ---- dotted access -------------------------------------------------
    def get(self, key: str) -> Any:
        node: Any = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(key)
            node = node[part]
        return node

    def _parent(self, key: str) -> tuple[dict, str]:
        parts = key.split(".")
        node = self.data
        for part in parts[:-1]:
            if part not in node or not isinstance(node[part], dict):
                raise KeyError(key)
            node = node[part]
        return node, parts[-1]

    def coerce(self, key: str, raw: str) -> Any:
        """Convert a string typed by the user into the type of the key."""
        if key in LIST_KEYS:
            items = [x.strip() for x in raw.replace(" ", ",").split(",")]
            return [x for x in items if x]
        current = self.get(key)
        if key in CHOICES:
            v = raw.strip().lower() if key != "challenge.sort" else raw.strip()
            if v not in CHOICES[key]:
                raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
            return v
        if isinstance(current, bool):
            return parse_bool(raw)
        if isinstance(current, int):
            return int(raw)
        if isinstance(current, float):
            return float(raw)
        if isinstance(current, dict):
            raise ValueError(f"{key} is a section; set one of its keys")
        return raw

    def set(self, key: str, raw: str) -> Any:
        if key.startswith("engine.options."):
            # engine options are free-form: keep ints as ints, bools as bools
            name = key[len("engine.options."):]
            opts = self.data["engine"]["options"]
            val: Any = raw
            for conv in (int, parse_bool):
                try:
                    val = conv(raw)
                    break
                except ValueError:
                    pass
            opts[name] = val
            return val
        parent, leaf = self._parent(key)
        if leaf not in parent:
            raise KeyError(key)
        val = self.coerce(key, raw)
        parent[leaf] = val
        return val

    def flat(self, prefix: str = "") -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []

        def walk(node: dict, pre: str) -> None:
            for k, v in node.items():
                key = f"{pre}{k}"
                if isinstance(v, dict):
                    walk(v, key + ".")
                else:
                    out.append((key, v))
        walk(self.data, "")
        return [(k, v) for k, v in out if k.startswith(prefix) and k not in SECRET_KEYS]

    def save(self) -> Path:
        data = copy.deepcopy(self.data)
        # never write a token that came from the environment into the file
        if os.environ.get("LICHESS_BOT_TOKEN") and data.get("token") == os.environ.get("LICHESS_BOT_TOKEN"):
            data["token"] = ""
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        return self.path

    # ---- helpers ---------------------------------------------------------
    def token(self) -> str:
        return self.token_source()[0]

    def token_source(self) -> tuple[str, str]:
        """(token, where it came from)."""
        tok = str(self.data.get("token") or "").strip()
        if tok and not tok.startswith("<"):
            return clean_token(tok), f"`token:` in {self.path.name}"
        tok = os.environ.get("LICHESS_BOT_TOKEN", "").strip()
        if tok:
            return clean_token(tok), "LICHESS_BOT_TOKEN"
        tf = str(self.data.get("token_file") or "").strip()
        if tf:
            p = Path(tf)
            if not p.is_absolute():
                p = BOT_DIR / p
            if p.exists():
                return clean_token(p.read_text(encoding="utf-8-sig", errors="replace")), str(p)
        return "", ""

    def resolve(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (BOT_DIR / p).resolve()
