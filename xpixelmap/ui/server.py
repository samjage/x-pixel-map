"""Local web UI: python3 server.py [--simulate] [--new] [--port 8765]  ->  http://127.0.0.1:8765
Listens on 127.0.0.1 only. Serves the page, the session state (polled), images and pixel close-ups from this project.
"""
import argparse, glob, io, json, os, shutil, threading, time, sys, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# These modules import each other by plain name (engine, camera, recipe, sort), so the UI
# directory must be on sys.path whether the server is run as `python3 server.py` or
# `python3 -m xpixelmap.ui`. The project root is needed by the tools camera.py loads.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))
from xpixelmap import firmware
import camera as K
import recipe as R
import window as W
from engine import Session

STATIC = Path(__file__).resolve().parent / 'static'
SESSIONS = K.PROJECT / 'xpixelmap' / 'data' / 'ui_sessions'
SIM_START = K.BACKUPS / 'cfgdata_20261105T185329Z-backup_02d9b2a70b6a.bin'   # v11b: focus pixel still hot
SIM_V12 = K.BACKUPS / 'cfgdata_20261105T194324Z-backup_37d02ff25784.bin'
TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
         '.png': 'image/png', '.svg': 'image/svg+xml'}


def recipe_of(folder, state=None):
    """Recipe name for a session folder; older folders only carry it in their name."""
    if state and state.get('recipe'): return state['recipe']
    return 'rollback' if '_rollback' in Path(folder).name else 'map'


def session_info(folder):
    """One row for the home menu: folder, recipe, created, step index/total, finished."""
    folder = Path(folder); f = folder / 'state.json'
    if not f.is_file(): return None
    try: st = json.loads(f.read_text())
    except Exception: return None
    steps = st.get('steps') or []
    index = int(st.get('index') or 0)
    recipe = recipe_of(folder, st)
    title = steps[index].get('title') if 0 <= index < len(steps) else None
    resumable = True
    recipe_ids = [x.id for x in R.RECIPES[recipe]()] if recipe in R.RECIPES else []
    step_ids = [s.get('id') for s in steps]
    if step_ids != recipe_ids:
        resumable = False
    if title is None and 0 <= index < len(steps):
        sid = steps[index].get('id')
        try: title = next((x.title for x in R.RECIPES[recipe]() if x.id == sid), None)
        except Exception: title = None
    ctx = st.get('ctx') or {}
    rep, cand = ctx.get('report') or {}, ctx.get('candidate') or {}
    summary = {'fixed': rep.get('fixed'), 'total': rep.get('total'), 'written': cand.get('sha12'),
               'changes': len(cand.get('changes') or []), 'chosen': (ctx.get('chosen') or {}).get('name')}
    return {'folder': folder.name, 'recipe': recipe, 'summary': summary,
            'created': st.get('created') or '', 'index': index, 'total': len(steps),
            'finished': index >= len(steps), 'step': title, 'simulate': bool(st.get('simulate')),
            'modified': f.stat().st_mtime, 'resumable': resumable}


def dir_bytes(path):
    """Disk used by a folder; hard-linked files (the demo reuses saved frames) are not counted, they cost nothing."""
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                st = os.lstat(os.path.join(root, f))
                if st.st_nlink == 1: total += st.st_size
            except OSError: pass
    return total


def finished_sessions(simulate):
    return [x for x in list_sessions() if x['finished'] and bool(x['simulate']) == bool(simulate)]


def list_sessions():
    SESSIONS.mkdir(parents=True, exist_ok=True)
    out = [info for info in (session_info(p) for p in SESSIONS.iterdir() if p.is_dir()) if info]
    return sorted(out, key=lambda x: (x['modified'], x['folder']), reverse=True)


def latest_unfinished(simulate):
    return next((x for x in list_sessions() if x['simulate'] == bool(simulate) and not x['finished'] and x['resumable']), None)


def home_view(simulate):
    """What the menu needs before a session exists: connected camera and what it holds."""
    mode = 'normal' if simulate else K.camera_mode()
    device = {'model': 'X-T1', 'firmware': '5.52', 'mode': mode} if simulate else K.device(None, mode)
    try: holds = K.holds(None)
    except Exception: holds = {'now': None, 'rollback': None}
    return {'home': True, 'simulate': simulate, 'camera': mode, 'device': device, 'holds': holds,
            'firmware': {'ready': simulate or firmware.ready(), 'url': firmware.FW_URL},
            'recipe': None, 'index': 0, 'steps': [], 'ctx': {}, 'log': [], 'activity': {}, 'created': None}


def lock_of(sess):
    """Exit lock: while any step runs the software is talking to the camera; a running write step must never be cut off."""
    if sess is None: return {'locked': False, 'writing': False, 'reason': ''}
    st, cur = sess.current()
    if cur is None or cur['status'] != 'running': return {'locked': False, 'writing': False, 'reason': ''}
    if st.writes:
        return {'locked': True, 'writing': True,
                'reason': 'Writing to the camera. Do not close this page, unplug the cable or turn the camera off.'}
    return {'locked': True, 'writing': False, 'reason': f'{st.title} is running. Wait for it to finish.'}


def open_session(simulate, new, recipe='map', folder=None, view=False):
    """Resume the newest unfinished session of this recipe + mode, or start a new one.
    folder= opens exactly that saved session (used by POST /api/resume)."""
    SESSIONS.mkdir(parents=True, exist_ok=True)
    tag = ('_rollback' if recipe == 'rollback' else '') + ('_sim' if simulate else '')
    if folder:
        folder = Path(folder)
    else:
        old = sorted(p for p in glob.glob(str(SESSIONS / '*')) if Path(p).name[15:] == tag and (Path(p) / 'state.json').exists())
        found = None
        if old and not new:
            for p in reversed(old):
                try:
                    st = json.loads((Path(p) / 'state.json').read_text())
                    if st['index'] < len(st['steps']):
                        found = p
                        break
                except Exception:
                    continue  # corrupted state.json; skip this session
        if found:
            folder = Path(found)
        else:
            stem = time.strftime('%Y%m%dT%H%M%S') + tag
            folder = SESSIONS / stem
            n = 2
            while folder.exists():                       # two new sessions in the same second must not share state
                folder = SESSIONS / f'{stem}-{n}'; n += 1
    holder = {}

    def mode():
        if not simulate: return K.camera_mode()
        st, _ = holder['s'].current()
        return (st.wait_for if st is not None and st.wait_for else 'normal')
    if simulate and not (folder / 'sim_backups').exists():
        (folder / 'sim_backups').mkdir(parents=True)
        starts = [SIM_START] + ([SIM_V12] if recipe == 'rollback' and SIM_V12.exists() else [])
        for i, f in enumerate(starts):                        # oldest first, so the newest copy is "what the camera holds"
            dst = folder / 'sim_backups' / f.name; shutil.copy2(f, dst); os.utime(dst, (time.time() - 100 + i, time.time() - 100 + i))
    s = Session(folder, R.RECIPES[recipe](), mode, simulate, view=view); holder['s'] = s; s.recipe = recipe   # ValueError: not resumable
    if s.state.get('recipe') != recipe:
        s.state['recipe'] = recipe; s.save()
    s.autostart()
    return s


class Handler(BaseHTTPRequestHandler):
    session = None
    simulate = False

    def log_message(self, *a): pass

    def send(self, code, body, ctype='application/json'):
        b = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        try:
            self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass                      # the browser closed the tab or cancelled an image request

    def fail(self, e):
        """Turn an unexpected error into a JSON 500 instead of a traceback in the terminal."""
        try: self.send(500, {'error': f'{type(e).__name__}: {e}'})
        except Exception: pass

    def allowed(self, p):
        p = Path(p).resolve()
        roots = [K.PROJECT / 'captures'] + ([self.session.dir] if self.session else [])
        return any(p.is_relative_to(r.resolve()) for r in roots)

    @staticmethod
    def ints(q, *names):
        try:
            return tuple(int(q[n][0]) for n in names)
        except (KeyError, IndexError, ValueError):
            return None

    def do_GET(self):
        try:
            return self.get()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            return self.fail(e)

    def get(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == '/': return self.send(200, (STATIC / 'index.html').read_bytes(), TYPES['.html'])
        if u.path.startswith('/static/'):
            f = (STATIC / u.path[8:]).resolve()
            if str(f).startswith(str(STATIC.resolve())) and f.exists(): return self.send(200, f.read_bytes(), TYPES.get(f.suffix, 'application/octet-stream'))
        if u.path == '/api/sessions':
            done = finished_sessions(self.simulate)
            return self.send(200, {'sessions': list_sessions(), 'simulate': self.simulate,
                                   'history': {'count': len(done), 'bytes': sum(dir_bytes(SESSIONS / x['folder']) for x in done)}})
        if u.path == '/api/state':
            if self.session is None: return self.send(200, dict(home_view(self.simulate), lock=lock_of(None)))
            v = self.session.view()
            v['lock'] = lock_of(self.session)
            try: v['camera_image'] = K.camera_image(self.session)[1]
            except Exception: v['camera_image'] = None
            try: v['holds'] = K.holds(self.session)
            except Exception: v['holds'] = {'now': None, 'rollback': None}
            v['device'] = K.device(self.session, v['camera'])
            v['recipe'] = getattr(self.session, 'recipe', 'map')
            return self.send(200, v)
        if u.path == '/api/file':
            p = q.get('p', [''])[0]
            if p and self.allowed(p) and Path(p).exists(): return self.send(200, Path(p).read_bytes(), TYPES.get(Path(p).suffix, 'application/octet-stream'))
        if u.path in ('/api/overview', '/api/region'):
            n = q.get('set', [''])[0]
            if u.path == '/api/overview': return self.send(200, overview(self.session, n), 'image/png')
            xywh = self.ints(q, 'x', 'y', 'w', 'h')
            if xywh is None: return self.send(400, {'error': 'x, y, w and h are required integers'})
            return self.send(200, region(self.session, n, *xywh), 'image/png')
        if u.path == '/api/tile':
            xy = self.ints(q, 'x', 'y')
            if xy is None: return self.send(400, {'error': 'x and y are required integers'})
            return self.send(200, tile(self.session, q.get('set', [''])[0], *xy), 'image/png')
        return self.send(404, {'error': 'not found'})

    def do_POST(self):
        try:
            return self.post()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            return self.fail(e)

    def post(self):
        n = int(self.headers.get('Content-Length', 0))
        if urlparse(self.path).path == '/api/firmware':
            if n > 64 << 20: return self.send(413, {'ok': False, 'error': 'That file is too big to be the firmware.'})
            try: firmware.install(self.rfile.read(n))
            except firmware.FirmwareError as e: return self.send(400, {'ok': False, 'error': str(e)})
            return self.send(200, {'ok': True})
        if n > 1048576:
            return self.send(413, {'error': 'request body too large'})
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
            if not isinstance(body, dict): raise ValueError
        except ValueError:
            return self.send(400, {'error': 'body must be a JSON object'})
        if urlparse(self.path).path == '/api/act':
            if self.session is None: return self.send(400, {'ok': False, 'error': 'no session is open'})
            return self.send(200, self.session.act(body.pop('action', ''), **body))
        if urlparse(self.path).path == '/api/new':
            st, cur = self.session.current() if self.session else (None, None)
            if cur is not None and cur['status'] == 'running':
                return self.send(409, {'ok': False, 'error': 'wait until the current step has finished'})
            recipe = body.get('recipe', 'map')
            if recipe not in R.RECIPES: return self.send(400, {'ok': False, 'error': 'unknown recipe'})
            Handler.session = open_session(self.simulate, True, recipe)
            return self.send(200, {'ok': True, 'folder': Handler.session.dir.name, 'recipe': recipe})
        if urlparse(self.path).path == '/api/resume':
            st, cur = self.session.current() if self.session else (None, None)
            if cur is not None and cur['status'] == 'running':
                return self.send(409, {'ok': False, 'error': 'wait until the current step has finished'})
            target = (SESSIONS / str(body.get('folder', ''))).resolve()
            if target.parent != SESSIONS.resolve() or not (target / 'state.json').is_file():
                return self.send(400, {'ok': False, 'error': 'that saved session was not found'})
            try: saved = json.loads((target / 'state.json').read_text())
            except Exception:
                return self.send(400, {'ok': False, 'error': 'that saved session cannot be read'})
            if bool(saved.get('simulate')) != bool(self.simulate):
                return self.send(400, {'ok': False, 'error': 'that session was recorded in the other mode'})
            state = saved.get('steps') or []
            if int(saved.get('index') or 0) >= len(state) and not body.get('view'):
                return self.send(400, {'ok': False, 'error': 'that session is already finished'})   # history opens it with view=true
            recipe = recipe_of(target, saved)
            try: opened = open_session(self.simulate, False, recipe, folder=target, view=bool(body.get('view')))
            except ValueError as e: return self.send(400, {'ok': False, 'error': str(e)})
            Handler.session = opened
            return self.send(200, {'ok': True, 'folder': target.name, 'recipe': recipe})
        if urlparse(self.path).path == '/api/history/clear':
            lock = lock_of(self.session)
            if lock['locked']: return self.send(409, {'ok': False, 'error': lock['reason']})
            if body.get('confirm') is not True: return self.send(400, {'ok': False, 'error': 'confirmation missing'})
            removed, freed = 0, 0
            for x in finished_sessions(self.simulate):          # finished sessions of this mode only; never a backup, never an open run
                target = (SESSIONS / x['folder']).resolve()
                if target.parent != SESSIONS.resolve(): continue
                freed += dir_bytes(target); shutil.rmtree(target, ignore_errors=True); removed += 1
                if self.session is not None and self.session.dir.resolve() == target: Handler.session = None
            return self.send(200, {'ok': True, 'removed': removed, 'freed': freed})
        if urlparse(self.path).path == '/api/home':
            st, cur = self.session.current() if self.session else (None, None)
            if cur is not None and cur['status'] == 'running':
                return self.send(409, {'ok': False, 'error': 'wait until the current step has finished'})
            Handler.session = None
            return self.send(200, {'ok': True})
        if urlparse(self.path).path == '/api/shutdown':
            lock = lock_of(self.session)
            if lock['locked']: return self.send(409, {'ok': False, 'error': lock['reason']})
            self.send(200, {'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()   # shutdown() waits for this request to end
            return
        if urlparse(self.path).path == '/api/state':
            if self.session is None or not self.session.simulate:
                return self.send(403, {'error': 'jumping between steps is only allowed in simulation'})
            new_index = body.get('index')
            if new_index is None:
                return self.send(400, {'error': 'index required'})
            try:
                new_index = int(new_index)
            except (ValueError, TypeError):
                return self.send(400, {'error': 'index must be an integer'})
            steps = self.session.state.get('steps') or []
            if not (0 <= new_index < len(steps)):
                return self.send(400, {'error': f'index out of range (0-{len(steps) - 1})'})
            self.session.state['index'] = new_index
            return self.send(200, self.session.view())
        return self.send(404, {'error': 'not found'})


def _mean(sess, set_name):
    import numpy as np, hotscan as H
    info = sess.ctx.get('sets', {}).get(set_name, {})
    files = [p for p in info.get('files', []) if Path(p).is_file()]
    key = (str(sess.dir), set_name)
    if key in _MEANS: return _MEANS[key]
    if not files:        # the RAW frames were cleared after the session: use the compressed copy kept for history
        import archive as AR
        a = AR.load_mean(sess.dir, set_name, info.get('kind') == 'flat', K.BLACK)
        if a is not None: _MEANS[key] = a
        return a
    _MEANS[key] = np.mean([H.load(p).astype(np.float32) for p in files], axis=0)
    return _MEANS[key]


_MEANS = {}


def _tone(a, flat):
    """Dark frames: black level 0, shadows lifted x4 (single hot pixels stand out). White frames: 1-99.5 percentile stretch."""
    import numpy as np
    if flat:
        lo, hi = np.percentile(a[::7, ::7], 1), np.percentile(a[::7, ::7], 99.5); v = np.clip((a - lo) / max(hi - lo, 1), 0, 1)
    else:
        v = np.clip((a - K.BLACK) / (K.WHITE - K.BLACK) * 4, 0, 1)
    return (np.where(v <= 0.0031308, 12.92 * v, 1.055 * v ** (1 / 2.4) - 0.055) * 255).astype(np.uint8)


def overview(sess, set_name):
    """Whole sensor, 1/3 size; each output pixel = brightest of its 3x3 block so single hot pixels survive. Cached."""
    import numpy as np
    from PIL import Image
    f = sess.dir / 'tiles' / f'overview_{set_name}.png'
    if f.exists(): return f.read_bytes()
    a = _mean(sess, set_name)
    if a is None: return b''
    h, w = a.shape; a = a[:h // 3 * 3, :w // 3 * 3].reshape(h // 3, 3, w // 3, 3).max((1, 3))
    flat = sess.ctx['sets'][set_name]['kind'] == 'flat'
    b = io.BytesIO(); Image.fromarray(_tone(a, flat)).save(b, 'PNG'); f.parent.mkdir(exist_ok=True); f.write_bytes(b.getvalue())
    return b.getvalue()


def region(sess, set_name, x, y, w, h):
    """Full-resolution patch for the zoomed viewer (one PNG pixel = one sensor pixel), at most 1200 x 1200."""
    from PIL import Image
    a = _mean(sess, set_name)
    if a is None: return b''
    w, h = min(max(w, 1), 1200), min(max(h, 1), 1200); x, y = max(0, x), max(0, y)
    flat = sess.ctx['sets'][set_name]['kind'] == 'flat'
    b = io.BytesIO(); Image.fromarray(_tone(a[y:y + h, x:x + w], flat)).save(b, 'PNG'); return b.getvalue()


def tile(sess, set_name, x, y, half=16, zoom=6):
    """A pixel close-up: the mean of the set's frames around (x, y), 6x, ring on the pixel. Cached."""
    import numpy as np, hotscan as H
    from PIL import Image, ImageDraw
    cache = sess.dir / 'tiles'; cache.mkdir(exist_ok=True); f = cache / f'{set_name}_{x}_{y}.png'
    if f.exists(): return f.read_bytes()
    files = [p for p in sess.ctx.get('sets', {}).get(set_name, {}).get('files', []) if Path(p).is_file()]
    if files:
        a = np.mean([H.load(p)[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1].astype(float) for p in files], axis=0)
    else:                # cleared frames: cut the close-up from the compressed copy
        m = _mean(sess, set_name)
        if m is None: return b''
        a = m[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1].astype(float)
    lo, hi = np.percentile(a, 1), max(np.percentile(a, 99.9), np.percentile(a, 1) + 8)
    im = Image.fromarray((np.clip((a - lo) / (hi - lo), 0, 1) ** 0.6 * 255).astype(np.uint8)).convert('RGB')
    im = im.resize((im.width * zoom, im.height * zoom), Image.NEAREST)
    c = (x - max(0, x - half)) * zoom + zoom // 2, (y - max(0, y - half)) * zoom + zoom // 2
    ImageDraw.Draw(im).ellipse([c[0] - 14, c[1] - 14, c[0] + 14, c[1] + 14], outline=(255, 80, 80), width=2)
    b = io.BytesIO(); im.save(b, 'PNG'); f.write_bytes(b.getvalue()); return b.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(description='X Pixel Map session UI (local web page)')
    ap.add_argument('--simulate', action='store_true', help='no camera: replay saved frames, fake the saves')
    ap.add_argument('--new', action='store_true', help='show the home menu instead of resuming the last unfinished session')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--browser', action='store_true', help='serve only and print the address (no app window, no browser opened)')
    ap.add_argument('--recipe', choices=sorted(R.RECIPES), default='map', help='recipe opened by --new/auto-resume: map = full pixel mapping; rollback = put a locked image back')
    a = ap.parse_args(argv)
    Handler.simulate = a.simulate
    Handler.session = None
    if not a.new:
        info = latest_unfinished(a.simulate)
        if info:
            try: Handler.session = open_session(a.simulate, False, info['recipe'], folder=SESSIONS / info['folder'])
            except ValueError as e: print(f'X Pixel Map: not resuming {info["folder"]}: {e}')    # home menu instead
    try: srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    except OSError as e:
        # never fall back to another port by itself: two copies must not both talk to one camera
        raise SystemExit(f'X Pixel Map: port {a.port} is already in use, most likely by another copy of X Pixel Map at '
                         f'http://127.0.0.1:{a.port}. Close that one first (its Exit button), or start this one with --port <other number>. ({e})')
    url = f'http://127.0.0.1:{a.port}'
    where = f'session {Handler.session.dir.name}' if Handler.session else 'home menu (no session open)'
    print(f'X Pixel Map: {url}  ({where}{", SIMULATION" if a.simulate else ""})')
    use_window = not a.browser and W.available()
    if not a.browser and not use_window:
        print('X Pixel Map: no app window on this system (needs pywebview + GTK WebKit); opening the browser instead.')
        webbrowser.open(url)
    try:
        if use_window:
            serving = threading.Thread(target=srv.serve_forever, name='http', daemon=True); serving.start()
            W.run(url, serving, srv.shutdown, lambda: lock_of(Handler.session)['reason'])
            if serving.is_alive(): srv.shutdown()        # the window went away some other way
        else:
            srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        wait_for_step()
        srv.server_close()
    print('X Pixel Map: server stopped.' + ('' if use_window else ' You can close the browser tab.'))


def wait_for_step():
    """Ctrl+C (or Stop server) never cuts a running step off: a write in progress finishes first."""
    sess = Handler.session
    w = sess and sess.worker
    if not (w and w.is_alive()): return
    print(f'X Pixel Map: {lock_of(sess)["reason"] or "a step is running."} Stopping when it has finished...')
    while w.is_alive():
        try:
            w.join(1)
        except KeyboardInterrupt:
            print('X Pixel Map: still finishing the current step; the server stops right after it.')
