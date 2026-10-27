"""Cloudflare quick tunnel for the read-only telemetry port (web.public_port).

`cloudflared tunnel --url http://127.0.0.1:<public_port>` gives a public https://<random>.trycloudflare.com
address without a Cloudflare account. Only the public-only listener is exposed: it answers nothing but
GET /api/public/state, so the dashboard and its commands stay unreachable from the internet.

The address changes every time the tunnel starts, so it is published to a GitHub gist (web.tunnel_gist,
file telemetry.json, written with the GitHub CLI `gh`), where the GitHub Pages viewer looks it up.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .manager import BotManager

URL_RE = re.compile(rb"https://[a-z0-9-]+\.trycloudflare\.com")


class Tunnel:
    def __init__(self, mgr: "BotManager", port: int, exe: str, gist: str):
        self.mgr = mgr
        self.port = port
        self.exe = exe or "cloudflared"
        self.gist = gist.strip()
        self.proc: asyncio.subprocess.Process | None = None
        self.url = ""
        self._drain: asyncio.Task | None = None

    def _exe(self) -> str | None:
        p = Path(self.exe)
        if p.is_file():
            return str(p)
        return shutil.which(self.exe)

    def _pidfile(self) -> Path:
        return self.mgr.cfg.resolve(self.mgr.cfg.get("log.file")).parent / "cloudflared.pid"

    def _kill_stale(self) -> None:
        """A bot that was killed cannot stop its tunnel; stop that old cloudflared (only if it still is one)."""
        pf = self._pidfile()
        try:
            pid = int(pf.read_text().strip())
        except (OSError, ValueError):
            return
        try:
            if sys.platform == "win32":
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                     capture_output=True, text=True, timeout=10).stdout.lower()
                if "cloudflared" in out:
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
            elif "cloudflared" in Path(f"/proc/{pid}/cmdline").read_text(errors="replace"):
                os.kill(pid, 15)
        except (OSError, subprocess.SubprocessError):
            pass
        pf.unlink(missing_ok=True)

    def kill_now(self) -> None:
        """Synchronous last resort at interpreter exit."""
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        self._pidfile().unlink(missing_ok=True)

    async def start(self) -> None:
        self._kill_stale()
        exe = self._exe()
        if not exe:
            self.mgr.log("warning", f"tunnel: {self.exe} not found (install cloudflared or set web.tunnel_exe)")
            return
        self.proc = await asyncio.create_subprocess_exec(
            exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            self._pidfile().write_text(str(self.proc.pid))
        except OSError:
            pass
        try:
            self.url = await asyncio.wait_for(self._find_url(), 90)
        except asyncio.TimeoutError:
            self.mgr.log("warning", "tunnel: cloudflared did not report an address within 90 s")
            return
        self._drain = asyncio.create_task(self._drain_stderr())
        self.mgr.log("info", f"tunnel: telemetry is public at {self.url}/api/public/state")
        if self.gist:
            await self.publish(self.url)

    async def _find_url(self) -> str:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                raise asyncio.TimeoutError
            m = URL_RE.search(line)
            if m:
                return m.group(0).decode()

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                break
            if b" ERR " in line:
                self.mgr.log("debug", "cloudflared: " + line.decode(errors="replace").strip()[:200])
        if self.proc.returncode not in (None, 0):
            self.mgr.log("warning", f"tunnel: cloudflared exited ({self.proc.returncode})")

    async def publish(self, url: str) -> None:
        """Write {"telemetry": url} into the gist (empty url = tunnel stopped)."""
        gh = shutil.which("gh")
        if not gh:
            self.mgr.log("warning", "tunnel: GitHub CLI (gh) not found - the viewer cannot find the address")
            return
        content = json.dumps({"telemetry": url, "updated": int(time.time())})
        proc = await asyncio.create_subprocess_exec(
            gh, "api", "-X", "PATCH", f"gists/{self.gist}", "-f", f"files[telemetry.json][content]={content}",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode:
            self.mgr.log("warning", f"tunnel: could not update gist {self.gist}: {err.decode(errors='replace')[:200]}")
        elif url:
            self.mgr.log("info", "tunnel: address published for the GitHub Pages viewer")

    async def stop(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()
        if self._drain:
            self._drain.cancel()
        self._pidfile().unlink(missing_ok=True)
        # clear the address only if it is still ours: another bot (PC / tablet) may have taken over the gist
        if self.gist and self.url and await self.published() in (self.url, None):
            await self.publish("")

    async def published(self) -> str | None:
        """The address currently in the gist ('' when cleared, None when it cannot be read)."""
        gh = shutil.which("gh")
        if not gh:
            return None
        proc = await asyncio.create_subprocess_exec(
            gh, "api", f"gists/{self.gist}", "--jq", '.files["telemetry.json"].content',
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), 20)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        if proc.returncode:
            return None
        try:
            return str(json.loads(out.decode() or "{}").get("telemetry") or "")
        except ValueError:
            return None
