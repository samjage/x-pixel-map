"""Camera-side tasks for the session UI. Every camera action goes through the tools that were tested on the X-T1:
tools/capture/xt1settings.py (menu settings, clock), gphoto2 (shots), python -m xpixelmap (save / read-back).
simulate=True never touches the camera or the real backups: shots are copied from saved sessions, saves are faked.
"""
import datetime, glob, hashlib, os, re, shutil, subprocess, sys, time
from pathlib import Path

from engine import StopRequest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'helper')); sys.path.insert(0, str(PROJECT / 'tools' / 'capture'))
BACKUPS = PROJECT / 'xpixelmap' / 'data' / 'backups'
SIM_DIR = BACKUPS / 'sim'          # optional demo fixtures: start.bin (focus pixel still hot) and after.bin (fixed), not included
SIM_START, SIM_AFTER = SIM_DIR / 'start.bin', SIM_DIR / 'after.bin'
SAFE_BACKUPS = Path(os.environ['XPIXELMAP_SAFE_COPY']) if os.environ.get('XPIXELMAP_SAFE_COPY') else None   # optional second copy (another disk)
SYS_PY = '/usr/bin/python3'          # has python-libusb1 (settings tool) and runs xpixelmap like the owner does
BLACK, WHITE = 1024, 16383


def camera_mode():
    """'service' (04cb:ff80), 'normal' (any other FUJIFILM id), or 'none'. lsusb only, never talks to the camera."""
    try: out = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=5).stdout
    except Exception: return 'none'
    ids = re.findall(r'ID 04cb:([0-9a-f]{4})', out)
    return 'service' if 'ff80' in ids else ('normal' if ids else 'none')


WRITE_TIMEOUT = ('The camera may be half way through a write. Do not start anything else: switch the camera off, take the battery out, '
                 'put it back, enter service mode again and read the pixel map back first.')


def sh(args, timeout=900, writes=False):
    try:
        r = subprocess.run(args, cwd=PROJECT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        what = 'the camera probe' if len(args) > 1 and args[1] == '-c' else ' '.join(str(a) for a in args[1:3])[:60]
        raise StopRequest(f'{what} did not finish within {timeout} s.' + (' ' + WRITE_TIMEOUT if writes else ''))
    return r.returncode, (r.stdout + r.stderr)


def sha12(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def has_image():
    return bool(glob.glob(str(BACKUPS / '*backup_*.bin')))


def camera_image(sess=None):
    """The image the camera holds: the newest read-back backup (every flash ends with one)."""
    root = (sess.dir / 'sim_backups') if sess is not None and sess.simulate and (sess.dir / 'sim_backups').exists() else BACKUPS
    files = sorted(glob.glob(str(root / '*backup_*.bin')), key=os.path.getmtime)
    if not files: raise StopRequest('no camera backup found: read the camera once in service mode first')
    return files[-1], Path(files[-1]).name.rsplit('_', 1)[-1][:12]


REGISTRY = PROJECT / 'xpixelmap' / 'data' / 'images.json'     # sha12 -> name ("v12", "Factory original", ...)
PIDS = {'02bf': 'X-T1'}                                          # USB product id in normal (PTP) mode -> model; service mode is ff80


def image_names():
    import json
    try: return json.loads(REGISTRY.read_text())
    except Exception: return {}


def name_image(sha, name):
    import json
    reg = image_names(); reg.setdefault(sha, name); REGISTRY.write_text(json.dumps(reg, indent=1) + '\n')


def display_name(sha, names, when, first=None):
    """What a person sees for an image: the first read of the camera is 'Original', anything else 'Edited <date>'.
    Research-style registry names (v12, v10c, ...) mean nothing to users and are not shown."""
    from xpixelmap.constants import BACKUP_SHA256
    if sha == first or (BACKUP_SHA256 and BACKUP_SHA256.startswith(sha)): return 'Original'   # first image ever read = before any change
    n = names.get(sha)
    if n and not re.fullmatch(r'v\d+[a-z]?', n): return n
    return 'Edited ' + when.strftime('%b %-d, %H:%M') if when else 'Edited'


def holds(sess=None):
    """What the camera holds and the rollback behind it: {'name','sha12','date'} each, from the locked read-back backups."""
    root = (sess.dir / 'sim_backups') if sess is not None and sess.simulate and (sess.dir / 'sim_backups').exists() else BACKUPS
    files = sorted(glob.glob(str(root / '*backup_*.bin')), key=os.path.getmtime)
    first = Path(files[0]).name.rsplit('_', 1)[-1][:12] if files else None
    names, seen, out = dict(image_names(), **((sess.ctx.get('names') or {}) if sess is not None else {})), [], []
    for f in reversed(files):
        sha = Path(f).name.rsplit('_', 1)[-1][:12]
        if sha in seen: continue
        seen.append(sha)
        m = re.search(r'(\d{8}T\d{6})Z', Path(f).name)
        when = datetime.datetime.strptime(m.group(1), '%Y%m%dT%H%M%S').replace(tzinfo=datetime.timezone.utc).astimezone() if m else None
        out.append({'sha12': sha, 'name': display_name(sha, names, when, first), 'date': when.strftime('%b %-d, %Y %H:%M') if when else '?'})
        if len(out) == 2: break
    return {'now': out[0] if out else None, 'rollback': out[1] if len(out) > 1 else None}


_DEVICE = {}


def device(sess=None, mode='none'):
    """Connected model + firmware. Normal mode: one read-only PTP GetDeviceInfo (cached per USB id); service mode:
    the model last seen in normal mode. Never runs while a session step is talking to the camera."""
    if sess is not None and sess.simulate: return {'model': 'X-T1', 'firmware': '5.52', 'mode': mode}
    if mode == 'none': return {'model': None, 'firmware': None, 'mode': mode}
    try: out = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=5).stdout
    except Exception: out = ''
    pid = next(iter(re.findall(r'ID 04cb:([0-9a-f]{4})', out)), None)
    if mode == 'normal' and pid not in _DEVICE and not (sess and sess.worker and sess.worker.is_alive()):
        rc, txt = sh([SYS_PY, '-c', 'import sys; sys.path.insert(0, "tools/capture"); import xt1settings as X, struct\n'
                      'def go(p):\n'
                      '    rc, _, d = p.run(0x1001)\n'
                      '    o = 8; n = d[o]; o += 1 + 2 * n + 2\n'
                      '    for _ in range(5): k = struct.unpack_from("<I", d, o)[0]; o += 4 + 2 * k\n'
                      '    v = []\n'
                      '    for _ in range(3): n = d[o]; v.append(d[o + 1:o + 1 + 2 * n].decode("utf-16-le").rstrip("\\x00")); o += 1 + 2 * n\n'
                      '    print("|".join(v)); return 0\n'
                      'X.session(go)'], 30)
        parts = txt.strip().splitlines()[-1].split('|') if rc == 0 and '|' in txt else []
        _DEVICE[pid] = {'model': parts[1] if len(parts) > 1 else PIDS.get(pid, f'FUJIFILM 04cb:{pid}'),
                        'firmware': parts[2] if len(parts) > 2 else None}
    last = _DEVICE.get(pid) or (next(iter(_DEVICE.values())) if _DEVICE else {'model': PIDS.get(pid), 'firmware': None})
    return dict(last, mode=mode)


# ---- menu settings + clock -------------------------------------------------------------------------------
def settings_backup(sess, p):
    if sess.simulate:
        p('Saving your menu settings (simulated)', 0.5); time.sleep(0.3)
        sess.ctx['settings'] = {'file': 'camera/settings/(simulated).bin', 'sha12': '000000000000'}; return {'sha12': 'simulated'}
    p('Reading your menu settings from the camera (read-only)', 0.3)
    rc, out = sh([SYS_PY, 'tools/capture/xt1settings.py', 'backup'], 120)
    m = re.search(r'-> (\S+)\s+sha12 ([0-9a-f]{12})', out)
    if rc or not m: raise StopRequest('could not read the settings: ' + out.strip()[-300:])
    sess.ctx['settings'] = {'file': m.group(1), 'sha12': m.group(2)}
    p(f'Settings saved ({m.group(2)})', 1.0)
    return {'file': m.group(1), 'sha12': m.group(2)}


def settings_restore(sess, p):
    s = sess.ctx.get('settings')
    if not s: raise StopRequest('no settings backup in this session')
    if sess.simulate:
        p('Restoring menu settings and clock (simulated)', 0.5); time.sleep(0.3); return {'restored': 'simulated'}
    p('Writing your saved menu settings back', 0.3)
    rc, out = sh([SYS_PY, 'tools/capture/xt1settings.py', 'restore', s['file'], '--confirm', s['sha12']], 120)
    if rc: raise StopRequest('settings restore failed: ' + out.strip()[-300:])
    p('Setting the camera clock from this computer', 0.7)
    rc, out2 = sh([SYS_PY, 'tools/capture/xt1settings.py', 'clock'], 60)
    if rc: raise StopRequest('clock not set: ' + out2.strip()[-300:])
    p('Settings and clock restored', 1.0)
    return {'restored': s['sha12'], 'clock': out2.strip()[-40:]}


# ---- shots -----------------------------------------------------------------------------------------------
HOT, SPIKE = 100, 200        # above its neighbours: in every frame of a set, or in any one frame
REASONS = {
    'white': 'This white frame came out at {level:.0%} brightness: keep the screen on and in front of the lens, then try again.',
    'none': 'No picture came back. Is the focus switch on M? With the body cap on, autofocus cannot lock and the camera will not shoot.',
    'exposure': 'The frame came out at {got} instead of {want}: put the shutter dial on A so the computer can set the speed.',
    'speed': 'The camera would not take the settings: put the shutter dial and ISO dial on A so the computer can set them.',
    'iso': 'The frame came out at ISO {got} instead of {want}: put the ISO dial on A so the computer can set it.',
    'shutter': 'That frame used the electronic shutter, which records less detail and shows pixels that photos do not: set Shutter type to Mechanical, or Mechanical + Electronic.',
}


def fmt_t(t):
    return f'1/{round(1 / t)} s' if t < 1 else f'{t:g} s'


def _link_or_copy(src, dst):
    """Simulation reuses the saved frames: a hard link costs no disk space (a copy was ~1 GB per demo run)."""
    try: os.link(src, dst)
    except OSError: shutil.copy2(src, dst)


def shoot_set(sess, p, name, kind, iso, t, frames, sim_src=None):
    import numpy as np, hotscan as H, xt1capture as C, xt1settings as X
    out = sess.dir / 'frames'; out.mkdir(exist_ok=True)
    files = []
    def expose(seconds):
        try: X.set_exposure(iso, seconds)
        except RuntimeError: raise StopRequest(REASONS['speed'])
    if kind == 'flat' and not sess.simulate:
        p('Finding the right exposure for the white screen (trial frames)', 0.05)
        try: t = C.auto_exposure(out, name, set_speed=expose)[1]
        except SystemExit as e: raise StopRequest(str(e))
    for k in range(frames):
        dst = out / f'{name}_{k + 1:02d}.RAF'
        p(f'{name.replace("_", " ")}: frame {k + 1} of {frames} ({fmt_t(t)}, ISO {iso})', k / frames, countdown=t + 2.5)
        if sess.simulate:
            folder, src_name = sim_src            # (saved capture folder, set name inside it)
            src = sorted(glob.glob(str(PROJECT / 'captures' / folder / f'{src_name}_0*.RAF')))
            if not src: raise StopRequest(f'simulation: no saved frames for {name} in {sim_src}')
            time.sleep(0.15); _link_or_copy(src[k % len(src)], dst)
        else:
            for old in out.glob(f'{name}_{k + 1:02d}.*'): old.unlink()      # a frame left by an earlier attempt must never pass for this one
            expose(t)                                    # gphoto2 hands priority back when it closes, so set it for every frame
            C.gp('--capture-image-and-download', '--filename', str(out / f'{name}_{k + 1:02d}.%C'), check=False)
            for f in out.glob(f'{name}_{k + 1:02d}.*'):
                if f.suffix.lower() == '.raf' and f != dst: f.rename(dst)
            if not dst.exists(): raise StopRequest(REASONS['none'])
            et, eiso, est = C.exif(dst)
            if kind == 'dark' and et and abs(et - t) / t > 0.05:
                raise StopRequest(REASONS['exposure'].format(got=fmt_t(et), want=fmt_t(t)))
            if eiso != iso: raise StopRequest(REASONS['iso'].format(got=eiso, want=iso))
            if kind == 'dark' and est == 'ES': raise StopRequest(REASONS['shutter'])
            if kind == 'flat':
                a = H.load(dst); h, w = a.shape
                level = (float(np.median(a[h // 4:3 * h // 4, w // 4:3 * w // 4])) - C.BLACK) / (C.WHITE - C.BLACK)
                if not 0.25 <= level <= 0.75: raise StopRequest(REASONS['white'].format(level=level))
        files.append(str(dst))
    info = {'kind': kind, 'iso': iso, 't': t, 'files': files}
    if kind == 'dark':
        info.update(dark_summary(files))
        png = heat_png(files, sess.dir / f'heat_{name}.png', info['hot'], f'{name}: {len(info["hot"])} pixels above {info["thr"]:.0f}')
        p(f'{name}: {len(info["hot"])} hot pixels found', 1.0, image=png)
    else:
        p(f'{name}: {frames} white frames', 1.0)
    sess.ctx.setdefault('sets', {})[name] = info
    return {'frames': len(files), 'hot': len(info.get('hot', []))}


def dark_summary(files):
    """Judged frame by frame: a pixel can read 0 in four frames and +480 in the fifth, so the lowest frame alone hides it."""
    import numpy as np, hotscan as H
    es = [H.spread(H.load(f)) for f in files]
    mn, pk = np.min(es, axis=0), np.max(es, axis=0)
    m = (mn > HOT) | (pk > SPIKE); m[:, :16] = 0; m[:, 4940 - 12:] = 0; m[:12] = 0; m[-12:] = 0
    ys, xs = np.nonzero(m)
    hot = sorted(((int(x), int(y), int(pk[y, x])) for x, y in zip(xs, ys)), key=lambda h: -h[2])[:400]
    np.save(Path(files[0]).with_suffix('.minexcess.npy'), mn.astype(np.int32))
    np.save(Path(files[0]).with_suffix('.peakexcess.npy'), pk.astype(np.int16))
    return {'thr': HOT, 'hot': hot}


def heat_png(files, out, hot, title):
    """Whole frame (mean of the set, brightest pixel of each 3x3 block, shadows lifted x4) with a ring on every hot pixel."""
    import numpy as np, hotscan as H
    from PIL import Image, ImageDraw
    mean = np.mean([H.load(f).astype(float) for f in files], axis=0)
    v = np.clip(mean - BLACK, 0, None) / (WHITE - BLACK)
    h, w = v.shape; v = v[:h // 3 * 3, :w // 3 * 3].reshape(h // 3, 3, w // 3, 3).max((1, 3))
    s = np.clip(v * 4, 0, 1); s = np.where(s <= 0.0031308, 12.92 * s, 1.055 * s ** (1 / 2.4) - 0.055)
    W = 1200; im = Image.fromarray((s * 255).astype(np.uint8)).convert('RGB').resize((W, int(W * v.shape[0] / v.shape[1])))
    d = ImageDraw.Draw(im); sc = W / w
    for x, y, e in hot: d.ellipse([x * sc - 11, y * sc - 11, x * sc + 11, y * sc + 11], outline=(255, 64, 64), width=2)
    im.save(out); return str(out)


# ---- flash: save, read-back --------------------------------------------------------------------------------
def save_candidate(sess, p):
    base, _ = camera_image(sess); cand = sess.ctx['candidate']
    if sess.simulate:
        for k in range(3): p('Writing the pixel map to the camera (simulated)', k / 3); time.sleep(0.15)
        p('Camera reports: save done (simulated)', 1.0); return {'saved': cand['sha12']}
    if camera_mode() != 'service': raise StopRequest('the camera is not in service mode on USB')
    p('Writing 4.1 MB to the camera, reading it back, then saving. Do not touch the camera or the cable.', 0.1)
    rc, out = sh([SYS_PY, '-m', 'xpixelmap', 'save', '--real', '--base', base, '--confirm', cand['sha12'], cand['path']], 1200, writes=True)
    if rc or 'Save reported success' not in out: raise StopRequest('save did not complete: ' + out.strip()[-400:])
    p('Camera reports: save done. Next: power cycle and read it back.', 1.0)
    return {'saved': cand['sha12']}


def first_backup(sess, p):
    """A new install has no image yet, and scanning and building both start from one: read the camera once."""
    if camera_mode() != 'service': raise StopRequest('the camera is not in service mode on USB')
    p('Reading the whole pixel map from the camera (about a minute)', 0.2)
    rc, out = sh([SYS_PY, '-m', 'xpixelmap', 'backup', '--real'], 1200)
    if rc: raise StopRequest('could not read the camera: ' + out.strip()[-300:])
    new = camera_image(sess)[0]; os.chmod(new, 0o444); sess.ctx['first_read'] = True
    p('Your camera\'s pixel map is backed up and locked.', 1.0)
    return {'backup': new}


def backups_list(sess=None):
    """Every locked image, newest first, one entry per image: name, save date, sha12/sha256, file."""
    root = (sess.dir / 'sim_backups') if sess is not None and sess.simulate and (sess.dir / 'sim_backups').exists() else BACKUPS
    files = sorted(glob.glob(str(root / '*backup_*.bin')), key=os.path.getmtime)
    first = Path(files[0]).name.rsplit('_', 1)[-1][:12] if files else None
    names = dict(image_names(), **((sess.ctx.get('names') or {}) if sess is not None else {}))
    out, seen = [], set()
    for f in reversed(files):
        s12 = Path(f).name.rsplit('_', 1)[-1][:12]
        if s12 in seen: continue
        seen.add(s12)
        m = re.search(r'(\d{8}T\d{6})Z', Path(f).name)
        when = datetime.datetime.strptime(m.group(1), '%Y%m%dT%H%M%S').replace(tzinfo=datetime.timezone.utc).astimezone() if m else None
        out.append({'path': f, 'sha12': s12, 'sha256': hashlib.sha256(Path(f).read_bytes()).hexdigest(),
                    'name': display_name(s12, names, when, first), 'date': when.strftime('%b %-d, %Y %H:%M') if when else '?'})
    return out


def restore_backup(sess, p):
    c = sess.ctx['choice']
    if sess.simulate:
        for k in range(3): p(f'Writing {c["name"]} back to the camera (simulated)', k / 3); time.sleep(0.15)
        return {'restored': c['sha12']}
    if camera_mode() != 'service': raise StopRequest('the camera is not in service mode on USB')
    p(f'Writing {c["name"]} back, reading it back, then saving. Do not touch the camera or the cable.', 0.1)
    rc, out = sh([SYS_PY, '-m', 'xpixelmap', 'restore', '--real', c['path'], '--backup-sha', c['sha256'], '--confirm', c['sha12']], 1200, writes=True)
    if rc or 'Save reported success' not in out: raise StopRequest('restore did not complete: ' + out.strip()[-400:])
    p('Camera reports: save done. Next: power cycle and read it back.', 1.0)
    return {'restored': c['sha12']}


def readback_choice(sess, p):
    c = sess.ctx['choice']
    return _readback(sess, p, c['path'], c['sha12'], name=None)


def readback(sess, p):
    cand = sess.ctx['candidate']
    return _readback(sess, p, cand['path'], cand['sha12'], name='Pixel map ' + time.strftime('%b %-d %H:%M'))


def read_back_matches(out):
    """True only for the tool's own verdict line 'expected image <path>: MATCH' (not the 'known backup ... MATCH' line)."""
    return bool(re.search(r'expected image .*: MATCH\s*$', out, re.M))


def _readback(sess, p, path, s12, name):
    cand = {'path': path, 'sha12': s12}
    if sess.simulate:
        p('Reading the whole pixel map back (simulated)', 0.5); time.sleep(0.3)
        d = sess.dir / 'sim_backups'; d.mkdir(exist_ok=True)
        dst = d / f'cfgdata_{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}-backup_{cand["sha12"]}.bin'; shutil.copy(cand['path'], dst)   # fresh mtime = newest
        if name: sess.ctx.setdefault('names', {})[cand['sha12']] = 'Pixel map (simulated)'
        p('The camera now holds the new pixel map (simulated)', 1.0); return {'match': True, 'backup': str(dst)}
    if camera_mode() != 'service': raise StopRequest('the camera is not in service mode on USB')
    p('Reading the whole pixel map back from the camera (about a minute)', 0.2)
    rc, out = sh([SYS_PY, '-m', 'xpixelmap', 'backup', '--real', '--base', camera_image(sess)[0], '--expect', cand['path']], 1200)
    if rc or not read_back_matches(out):
        raise StopRequest('the camera does NOT hold the new pixel map. Stop here; the old backup is safe. ' + out.strip()[-300:])
    new = sorted(glob.glob(str(BACKUPS / f'*backup_{cand["sha12"]}*.bin')), key=os.path.getmtime)[-1]
    os.chmod(new, 0o444)
    copied = 'no second copy configured (set XPIXELMAP_SAFE_COPY)'
    if SAFE_BACKUPS:
        safe = SAFE_BACKUPS / datetime.date.today().isoformat()
        try: safe.mkdir(parents=True, exist_ok=True); shutil.copy2(new, safe / Path(new).name); copied = str(safe)
        except Exception as e: copied = f'not copied ({e})'
    if name: name_image(cand['sha12'], name)
    p('The camera holds the new pixel map, byte for byte. Backup saved.', 1.0)
    return {'match': True, 'backup': new, 'copy': copied}
