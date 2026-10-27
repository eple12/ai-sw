"""Claudy Hub - ClaudyEngine(체스) / ClaudyFive(오목) / shape(도형 CNN)를 한 화면에서 실행하는 런처."""
import atexit, json, mimetypes, os, subprocess, sys, threading, time, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APPS = os.path.join(HERE, 'apps')
PORT, CHESS_PORT = 8800, 8080
MOUNT = {'/five/': os.path.join(APPS, 'five'), '/shape/': os.path.join(APPS, 'shape')}
ALLOW = {'.html', '.js', '.css', '.bin', '.json', '.png', '.svg', '.ico', '.woff2'}
BOT = os.path.join(APPS, 'chess', 'bot')
proc, lock = None, threading.Lock()


def chess_up():
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{CHESS_PORT}/', timeout=.6).close()
        return True
    except Exception:
        return False


def chess_state():
    if chess_up(): return 'running'
    return 'starting' if proc and proc.poll() is None else 'stopped'


def chess_start():
    global proc
    with lock:
        if chess_state() != 'stopped': return
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        proc = subprocess.Popen([sys.executable, '-m', 'claudybot', '--offline', '--web', '--headless'], cwd=BOT,
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)


def chess_stop():
    global proc
    with lock:
        if proc and proc.poll() is None:
            if os.name == 'nt': subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)
            else: proc.kill()
        proc = None


atexit.register(chess_stop)


class H(BaseHTTPRequestHandler):
    def send(s, body, ct='application/json', code=200):
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        s.send_response(code); s.send_header('Content-Type', ct); s.send_header('Content-Length', str(len(body)))
        s.send_header('Cache-Control', 'no-cache'); s.end_headers(); s.wfile.write(body)

    def do_GET(s):
        p = s.path.split('?')[0]
        if p in ('/', '/index.html'): return s.send(open(os.path.join(HERE, 'index.html'), 'rb').read(), 'text/html; charset=utf-8')
        if p == '/api/chess': return s.send({'state': chess_state()})
        for pre, d in MOUNT.items():
            if p.startswith(pre) or p + '/' == pre:
                rel = p[len(pre):] or 'index.html'
                f = os.path.normpath(os.path.join(d, rel.lstrip('/')))
                if f.startswith(d + os.sep) and os.path.isfile(f) and os.path.splitext(f)[1] in ALLOW:
                    return s.send(open(f, 'rb').read(), (mimetypes.guess_type(f)[0] or 'application/octet-stream') + '; charset=utf-8')
        s.send(b'not found', 'text/plain', 404)

    def do_POST(s):
        if s.path == '/api/chess/start': chess_start()
        elif s.path == '/api/chess/stop': chess_stop()
        else: return s.send(b'not found', 'text/plain', 404)
        s.send({'state': chess_state()})

    def log_message(s, *a): pass


if __name__ == '__main__':
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), H)
    print(f'Claudy Hub  http://127.0.0.1:{PORT}  (Ctrl+C 로 종료)')
    if '--no-browser' not in sys.argv: threading.Timer(.6, webbrowser.open, [f'http://127.0.0.1:{PORT}/']).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
