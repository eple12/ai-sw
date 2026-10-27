"""Entry point: python -m claudybot [--config FILE] [--url URL] [--headless]"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from pathlib import Path

from rich.text import Text

from .config import Config


async def run_headless(mgr) -> None:
    from .commands import Commands
    cmds = Commands(mgr)
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str] = asyncio.Queue()

    def reader() -> None:
        for line in sys.stdin:
            loop.call_soon_threadsafe(lines.put_nowait, line)

    threading.Thread(target=reader, daemon=True).start()

    async def consume() -> None:
        while True:
            line = await lines.get()
            for out in await cmds.execute(line):
                print(Text.from_markup(out).plain, flush=True)

    consumer = asyncio.create_task(consume())
    try:
        await mgr.run()
    finally:
        consumer.cancel()
        await mgr.close()


def detect_graphics() -> bool:
    """True when the terminal can display images (Sixel or the Kitty graphics protocol)."""
    if os.environ.get("CLAUDYBOT_GRAPHICS") in ("0", "1"):
        force = os.environ["CLAUDYBOT_GRAPHICS"] == "1"
        if force:
            import textual_image.widget  # noqa: F401
        return force
    try:
        from textual_image import renderable as r
        import textual_image.widget  # noqa: F401  (probes the cell size while we still own the terminal)
        return r.Image in (r.SixelImage, r.TGPImage)
    except Exception:
        return False


TOKEN_URL = "https://lichess.org/account/oauth/token/create?scopes[]=bot:play&description=ClaudyBot"


def account_tool(token: str, url: str, upgrade: bool, source: str = "") -> int:
    """--check / --upgrade: verify the token, show the account, optionally upgrade it to BOT."""
    import httpx

    from .config import token_shape
    print(f"token        : {token_shape(token)}" + (f"  (from {source})" if source else ""))
    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=20) as c:
        info = c.post("/api/token/test", content=token).json().get(token)
        if not info:
            print("token is not valid: Lichess does not know it (mistyped / cut off, revoked, or expired).")
            print("  A personal token is usually lip_ followed by 20 letters and digits (24 characters).")
            print("  Create a new one while logged in as the BOT account (bot:play is preselected here):")
            print(f"  {TOKEN_URL}")
            return 1
        scopes = [x for x in (info.get("scopes") or "").split(",") if x]
        print(f"token scopes : {', '.join(scopes) or '-'}")
        if "bot:play" not in scopes:
            print("the token needs the bot:play scope - create a new token")
            return 1
        acct = c.get("/api/account").json()
        games = (acct.get("count") or {}).get("all", 0)
        print(f"account      : {acct.get('username')}  title: {acct.get('title') or '-'}  games played: {games}")
        if not upgrade:
            print("ok" if acct.get("title") == "BOT" else "not a BOT account yet: run with --upgrade")
            return 0
        if acct.get("title") == "BOT":
            print("already a BOT account")
            return 0
        if games:
            print("warning: Lichess only upgrades accounts that have not played any game")
        name = acct.get("username", "")
        ans = input(f"Upgrade {name} to a BOT account? This cannot be undone. Type the username to confirm: ")
        if ans.strip().lower() != name.lower():
            print("cancelled")
            return 1
        r = c.post("/api/bot/account/upgrade")
        if r.status_code != 200:
            print(f"upgrade failed: HTTP {r.status_code} {r.text[:200]}")
            return 1
        title = c.get("/api/account").json().get("title")
        print(f"upgraded - title is now {title}")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="claudybot", description="Lichess BOT client with a console dashboard")
    ap.add_argument("--config", type=Path, default=None, help="config file (default: bot/config.yml)")
    ap.add_argument("--url", default=None, help="server URL override (e.g. the local mock server)")
    ap.add_argument("--headless", action="store_true", help="plain log output, commands from stdin")
    ap.add_argument("--web", nargs="?", type=int, const=0, default=None, metavar="PORT",
                    help="also serve the browser dashboard (default port: web.port, 8080)")
    ap.add_argument("--web-host", default=None, help="web dashboard address (default 127.0.0.1)")
    ap.add_argument("--offline", action="store_true",
                    help="do not connect to Lichess: play the engine and analyse on this device only")
    ap.add_argument("--check", action="store_true", help="verify the token and show the account, then exit")
    ap.add_argument("--upgrade", action="store_true", help="upgrade the account to a BOT account (irreversible)")
    a = ap.parse_args()

    cfg = Config(a.config)
    url = a.url or cfg.get("url")
    local = url.startswith("http://127.0.0.1") or url.startswith("http://localhost")
    offline = a.offline
    if not cfg.token():
        if local:
            cfg.data["token"] = "mock-token"
        elif a.check or a.upgrade:
            print("No Lichess token: set LICHESS_BOT_TOKEN, or `token:` / `token_file:` in bot/config.yml",
                  file=sys.stderr)
            return 2
        else:
            print("No Lichess token (LICHESS_BOT_TOKEN, or token: / token_file: in bot/config.yml): starting "
                  "offline - local games and analysis only", file=sys.stderr)
            offline = True

    if a.check or a.upgrade:
        try:
            tok, src = cfg.token_source()
            return account_tool(tok, url, a.upgrade, src)
        except Exception as e:
            print(f"error: {e!r}")
            return 1

    from .manager import BotManager

    def attach_web(mgr) -> None:
        if a.web is None and not cfg.get("web.enabled") and not int(cfg.get("web.public_port") or 0):
            return
        from .web import WebServer
        mgr.web = WebServer(mgr, a.web_host or cfg.get("web.host"), a.web or int(cfg.get("web.port")),
                            str(cfg.get("web.key") or ""), int(cfg.get("web.public_port") or 0))

    if a.headless:
        try:
            sys.stdout.reconfigure(errors="replace")
        except AttributeError:
            pass
        mgr = BotManager(cfg, url, echo=print, offline=offline)
        attach_web(mgr)
        try:
            asyncio.run(run_headless(mgr))
        except KeyboardInterrupt:
            pass
        finally:
            if mgr.tunnel is not None:
                mgr.tunnel.kill_now()
        if mgr.fatal:
            return 1
        return 75 if mgr.restart_requested else 0

    from .tui import ClaudyApp
    graphics = detect_graphics()           # must run before Textual takes over the terminal
    mgr = BotManager(cfg, url, offline=offline)
    attach_web(mgr)
    mgr.log("info", "board pictures: " + ("terminal graphics (Sixel/Kitty)" if graphics else
                                          "not supported by this terminal - using block sprites"))
    app = ClaudyApp(mgr, graphics=graphics)
    try:
        app.run()
    finally:
        # never leave engine / tunnel processes behind
        if mgr.tunnel is not None:
            mgr.tunnel.kill_now()
        for g in list(mgr.games.values()):
            if g.engine and g.engine.proc and g.engine.proc.returncode is None:
                try:
                    g.engine.proc.kill()
                except ProcessLookupError:
                    pass
    if mgr.fatal:
        print(f"error: {mgr.fatal}", file=sys.stderr)
        return 1
    return 75 if mgr.restart_requested else 0


if __name__ == "__main__":
    sys.exit(main())
