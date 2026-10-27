"""Asynchronous UCI engine process wrapper with `info` parsing."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

InfoCallback = Callable[[dict], None]
LineCallback = Callable[[str, str], None]    # (direction ">" or "<", text)


def parse_info(line: str) -> dict[str, Any]:
    """Parse a UCI `info ...` line into a dict (depth, seldepth, cp/mate, bound, nodes, nps, ...)."""
    tok = line.split()
    out: dict[str, Any] = {}
    i = 1
    n = len(tok)
    ints = {"depth", "seldepth", "multipv", "nodes", "nps", "hashfull", "tbhits", "time",
            "currmovenumber", "cpuload"}
    while i < n:
        t = tok[i]
        if t in ints and i + 1 < n:
            try:
                out[t] = int(tok[i + 1])
            except ValueError:
                pass
            i += 2
        elif t == "score" and i + 2 < n:
            kind, val = tok[i + 1], tok[i + 2]
            try:
                out["mate" if kind == "mate" else "cp"] = int(val)
            except ValueError:
                pass
            i += 3
            if i < n and tok[i] in ("lowerbound", "upperbound"):
                out["bound"] = tok[i]
                i += 1
        elif t == "wdl" and i + 3 < n:
            try:
                out["wdl"] = (int(tok[i + 1]), int(tok[i + 2]), int(tok[i + 3]))
            except ValueError:
                pass
            i += 4
        elif t == "currmove" and i + 1 < n:
            out["currmove"] = tok[i + 1]
            i += 2
        elif t == "pv":
            out["pv"] = tok[i + 1:]
            break
        elif t == "string":
            out["string"] = " ".join(tok[i + 1:])
            break
        else:
            i += 1
    return out


class EngineError(Exception):
    pass


class UciEngine:
    def __init__(self, path: Path, options: dict[str, Any], on_line: LineCallback | None = None):
        self.path = Path(path)
        self.options = dict(options)
        self.on_line = on_line or (lambda d, s: None)
        self.on_info: InfoCallback | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.name = self.path.stem
        self.author = ""
        self.declared: dict[str, str] = {}   # lower-case option name -> declared name
        self.searching = False
        self._bestmove: asyncio.Future | None = None
        self._uciok: asyncio.Future | None = None
        self._readyok: asyncio.Future | None = None
        self._reader_task: asyncio.Task | None = None
        self.go_time = 0.0

    # ---- process -----------------------------------------------------------
    async def start(self) -> None:
        if not self.path.exists():
            raise EngineError(f"engine not found: {self.path}")
        self.proc = await asyncio.create_subprocess_exec(
            str(self.path), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, cwd=str(self.path.parent), limit=1 << 20)
        self._reader_task = asyncio.create_task(self._reader())
        loop = asyncio.get_running_loop()
        self._uciok = loop.create_future()
        await self.send("uci")
        await asyncio.wait_for(self._uciok, 15)
        for name, value in self.options.items():
            await self.setoption(name, value)
        await self.isready()

    async def setoption(self, name: str, value: Any) -> bool:
        real = self.declared.get(name.lower())
        if real is None:
            return False
        if isinstance(value, bool):
            value = "true" if value else "false"
        if value is None or value == "":
            return False
        await self.send(f"setoption name {real} value {value}")
        return True

    async def isready(self, timeout: float = 30) -> None:
        self._readyok = asyncio.get_running_loop().create_future()
        await self.send("isready")
        await asyncio.wait_for(self._readyok, timeout)

    async def send(self, cmd: str) -> None:
        if not self.proc or not self.proc.stdin or self.proc.returncode is not None:
            raise EngineError("engine process is not running")
        self.on_line(">", cmd)
        self.proc.stdin.write((cmd + "\n").encode())
        await self.proc.stdin.drain()

    async def _reader(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            raw = await self.proc.stdout.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip()
            if not line:
                continue
            self.on_line("<", line)
            if line.startswith("info "):
                if self.on_info:
                    info = parse_info(line)
                    if info:
                        self.on_info(info)
            elif line.startswith("bestmove"):
                parts = line.split()
                best = parts[1] if len(parts) > 1 else "(none)"
                ponder = parts[3] if len(parts) > 3 and parts[2] == "ponder" else None
                self.searching = False
                if self._bestmove and not self._bestmove.done():
                    self._bestmove.set_result((best, ponder))
            elif line == "readyok":
                if self._readyok and not self._readyok.done():
                    self._readyok.set_result(True)
            elif line == "uciok":
                if self._uciok and not self._uciok.done():
                    self._uciok.set_result(True)
            elif line.startswith("option name "):
                rest = line[len("option name "):]
                name = rest.split(" type ")[0].strip()
                self.declared[name.lower()] = name
            elif line.startswith("id name "):
                self.name = line[8:].strip()
            elif line.startswith("id author "):
                self.author = line[10:].strip()
        # process ended: fail anything that is waiting
        self.searching = False
        for fut in (self._bestmove, self._uciok, self._readyok):
            if fut and not fut.done():
                fut.set_exception(EngineError("engine process exited"))

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def quit(self) -> None:
        if not self.proc:
            return
        try:
            if self.alive:
                if self.searching:
                    await self.send("stop")
                await self.send("quit")
                await asyncio.wait_for(self.proc.wait(), 3)
        except Exception:
            pass
        if self.proc.returncode is None:
            try:
                self.proc.kill()
                await asyncio.wait_for(self.proc.wait(), 3)
            except Exception:
                pass
        if self._reader_task:
            self._reader_task.cancel()

    # ---- search --------------------------------------------------------------
    async def new_game(self) -> None:
        await self.send("ucinewgame")
        await self.isready()

    async def go(self, position: str, go: str, on_info: InfoCallback | None) -> None:
        """Start a search; await `wait_bestmove()` for the result."""
        if self.searching:
            raise EngineError("search already running")
        self._bestmove = asyncio.get_running_loop().create_future()
        self.on_info = on_info
        await self.send(position)
        self.searching = True
        self.go_time = time.monotonic()
        await self.send(go)

    async def wait_bestmove(self, timeout: float | None = None) -> tuple[str, str | None]:
        assert self._bestmove is not None
        return await asyncio.wait_for(asyncio.shield(self._bestmove), timeout)

    async def stop(self) -> tuple[str, str | None] | None:
        """Stop the running search (if any) and wait for its bestmove."""
        if self._bestmove is None or self._bestmove.done():
            return None
        await self.send("stop")
        try:
            return await self.wait_bestmove(10)
        except asyncio.TimeoutError:
            raise EngineError("engine did not answer stop")

    async def ponderhit(self) -> None:
        await self.send("ponderhit")
