"""Sort the pixels the shots found, build the candidate from the owner's selection, verify it offline.
Rules (from the 2026-11-04/06 sessions, see PROCEDURE.md):
  pair       hot in the short frames, its LEFT neighbour leaks in 1 s  -> Fuji-style pixel + left partner (list2), joint tags
  light/dead only odd under light (white flat)                         -> list0 + list2
  focus      on the phase-detect grid                                  -> no list entry: cfg 0x113b6 = 0 (all 28 classes) handles it
  ordinary   above HOT in every frame of two sets, or of one set up to ISO 1000 -> list0 + list2
  spike      above SPIKE in single frames, in two sets or more         -> list0 + list2 (the correction misses it now and then)
  faint-iso  anything else that showed up, usually twitchy pixels      -> leave, can be ticked by hand
  margin     outside the editable list range (x < 16 or x >= 4928)    -> cannot be listed
  listed     already in Fuji's lists but still hot                     -> report only
"""
import os, re, sys, time
from pathlib import Path

from engine import StopRequest
from camera import HOT, PROJECT, SPIKE, camera_image, sh, sha12

FOCUS_TABLE = 0x113b6          # cfg byte per class (28): 0 removes a hot phase-detect pixel (proven 2026-11-05, v12)
RECC_GATE, RECC_FACTORY, RECC_ON = (0x2800, 0x2805), bytes([0, 0, 0, 20, 49]), 127   # raw correction: 127 = on at every shutter speed
# dark sets of a scan and of the check: these settings caught 28 of the 32 pixels the full ladder found (ISO 200-1600, 2026-11-06)
SCAN = (('dark_4th_iso200', 200, 1 / 4, '1/4 s'), ('dark_1s_iso250', 250, 1.0, '1 s'), ('dark_4th_iso320', 320, 1 / 4, '1/4 s'),
        ('dark_4th_iso400', 400, 1 / 4, '1/4 s'), ('dark_short_iso800', 800, 1 / 200, '1/200 s'), ('dark_4s_iso1250', 1250, 4.0, '4 s'))
SCAN_FRAMES = 5
DARK = tuple(n for n, *_ in SCAN)
ONE_SET_ISO = 1000             # above this one set is not enough: warm, high-ISO long frames show twitchy pixels in every frame
LABELS = {'ordinary': 'Hot pixel', 'pair': 'Coupled pair', 'light': 'Too bright in light', 'dead': 'Too dark in light',
          'focus': 'Hot pixel (focus sensor)', 'focus-faint': 'Faint focus-sensor pixel', 'bright-iso': 'Bright at one ISO only', 'spike': 'Hot now and then', 'faint-iso': 'Seen at one setting only', 'margin': 'Frame edge', 'listed': 'Already listed'}
FIXABLE = ('ordinary', 'spike', 'pair', 'light', 'dead', 'bright-iso', 'faint-iso')     # faint-iso can be ticked by hand, never pre-ticked
PRETICKED = ('ordinary', 'spike', 'pair', 'light', 'dead', 'bright-iso')


def _maps(sess):
    import numpy as np
    sets = sess.ctx.get('sets', {})
    mx = {n: np.load(Path(i['files'][0]).with_suffix('.minexcess.npy')) for n, i in sets.items() if i['kind'] == 'dark'}
    return sets, mx


def _peaks(sets, mx):
    """Highest reading of each dark set; sessions from before 2026-11-06 only kept the lowest."""
    import numpy as np
    out = {}
    for n, m in mx.items():
        f = Path(sets[n]['files'][0]).with_suffix('.peakexcess.npy')
        out[n] = np.load(f) if f.is_file() else m
    return out


def classify(sess, p):
    import numpy as np, hotscan as H, edit_lists as E, xt1capture as C
    sets, mx = _maps(sess); pk = _peaks(sets, mx)
    base, base_sha = camera_image(sess); d = Path(base).read_bytes()
    listed = set()
    for n in ('list0', 'list2'): listed |= E.listed_xy(*E.read_list(d, n))
    p('Sorting what the shots found', 0.2)
    cand = {}
    for n, i in sets.items():
        for x, y, e in i.get('hot', []): cand.setdefault((x, y), set()).add(n)
    ratio = None
    if 'flat_iso200' in sets:
        p('Checking the white frames for dead or light-sensitive pixels', 0.4)
        ratio = np.median([C.flat_ratio(H.load(f)) for f in sets['flat_iso200']['files']], axis=0)
        bad = (ratio < 0.6) | (ratio > 1.3); bad[:, :16] = 0; bad[:, 4928:] = 0; bad[:12] = 0; bad[-12:] = 0
        for y, x in zip(*np.nonzero(bad)): cand.setdefault((int(x), int(y)), set()).add('flat_iso200')
    focus_fix = all(d[FOCUS_TABLE + c] == 0 for c in range(28))
    found = []
    for (x, y), where in sorted(cand.items()):
        ex = {n: int(m[y, x]) for n, m in mx.items()}
        peak = {n: int(m[y, x]) for n, m in pk.items()}
        r = float(ratio[y, x]) if ratio is not None else None
        steady = [n for n in DARK if ex.get(n, 0) > HOT]
        repeats = len(steady) >= 2 or any(sets[n]['iso'] <= ONE_SET_ISO for n in steady)
        spikes = sum(peak.get(n, 0) > SPIKE for n in DARK) >= 2
        if not (16 <= x < 4928): cls, why = 'margin', 'at the very edge of the frame, outside what the lists can hold'
        elif E.is_af_pixel(x - 4, y) and not (repeats or spikes):
            cls, why = 'focus-faint', 'on a focus-sensor spot, faint and at one ISO only: no list can fix this one, and the focus table already applies'
        elif E.is_af_pixel(x - 4, y):
            cls = 'focus'
            why = ('sits on a focus-sensor spot; already fixed in your camera' if focus_fix else
                   'sits on a focus-sensor spot; fixed automatically, nothing to tick')
        elif (x - 4, y) in listed: cls, why = 'listed', 'already in Fuji\'s lists but still above the threshold'
        elif r is not None and r > 1.3: cls, why = 'light', f'{r:.2f}x its neighbours under light'
        elif r is not None and r < 0.6: cls, why = 'dead', f'{r:.2f}x its neighbours under light'
        elif ex.get('dark_short_iso800', 0) > HOT and \
                max([int(mx[n][y, x - 1]) for n in DARK if n in mx and n != 'dark_short_iso800'] or [0]) > 25:
            cls, why = 'pair', 'hot in short frames; its left neighbour leaks in long frames (Fuji pairs these)'
        else:
            if repeats: cls, why = 'ordinary', f'above {HOT} in every frame: a real hot pixel'
            elif spikes: cls, why = 'spike', f'jumps above {SPIKE} in single frames at more than one setting: the camera misses it now and then'
            else: cls, why = 'faint-iso', 'showed up at one setting only, probably a twitchy pixel, so not ticked. You can still tick it to list it'
        found.append({'x': x, 'y': y, 'cls': cls, 'label': LABELS[cls], 'why': why, 'ex': peak, 'ratio': r,
                      'selected': cls in PRETICKED, 'tile_set': sorted(where)[0]})
    found.sort(key=lambda f: (f['cls'] not in FIXABLE, -max(f['ex'].values() or [0])))
    sess.ctx['found'] = found; sess.ctx['focus_fix_in_camera'] = focus_fix; sess.ctx['base'] = {'path': base, 'sha12': base_sha}
    counts = {}
    for f in found: counts[f['label']] = counts.get(f['label'], 0) + 1
    p(f'Found {len(found)} pixels: ' + ', '.join(f'{v} {k.lower()}' for k, v in counts.items()), 1.0)
    return {'found': len(found), 'counts': counts}


def _drop(E, d, pts):
    for name in ('list0', 'list2'):
        a, b = E.read_list(d, name); rm = {(x - 4, y) for x, y in pts}
        d = E.write_list(d, name, [r for r in a if (E.xof(r), E.yof(r)) not in rm],
                         [r for r in b if (E.xof(r) + E.BSTART, E.yof(r)) not in rm])
    return d


def build(sess, p):
    import edit_lists as E
    from xpixelmap import firmware
    if not sess.simulate and not firmware.ready():
        raise StopRequest('the safety rehearsal needs your firmware file first: go to Home and add it there. Nothing was sent.')
    base, base_sha = camera_image(sess)
    d = Path(base).read_bytes(); orig = d
    chosen = {tuple(map(int, s.split(','))) for s in sess.ctx.get('selected', [])}
    found = {(f['x'], f['y']): f for f in sess.ctx.get('found', [])}
    done, skipped = [], []
    for i, (x, y) in enumerate(sorted(chosen)):
        f = found.get((x, y)); p(f'Adding ({x},{y}) to the lists', 0.1 + 0.5 * i / max(len(chosen), 1))
        if not f or f['cls'] not in FIXABLE: skipped.append(f'({x},{y}) {f["label"] if f else "?"}: not a list fix'); continue
        try:
            if f['cls'] == 'pair':
                d = _drop(E, d, [(x, y)]); d, _ = E.add_pixels(d, [(x, y)], {'list2': [(x - 1, y)]})
                done.append(f'({x},{y}) + left partner ({x - 1},{y})')
            else:
                d, _ = E.add_pixels(d, [(x, y)]); done.append(f'({x},{y})')
        except SystemExit as e:
            skipped.append(f'({x},{y}): editor refused ({e})')
    fixed_table = False
    if not all(d[FOCUS_TABLE + c] == 0 for c in range(28)):
        b = bytearray(d)
        for c in range(28): b[FOCUS_TABLE + c] = 0
        d = bytes(b); fixed_table = True; done.append('focus-pixel fix: cfg 0x113b6 = 0 for all 28 classes')
    gate = d[slice(*RECC_GATE)]
    if gate == RECC_FACTORY:
        b = bytearray(d); b[slice(*RECC_GATE)] = bytes([RECC_ON]) * len(gate)
        d = bytes(b); done.append('raw correction on at every shutter speed: cfg 0x2800..0x2804 = 127')
    elif set(gate) != {RECC_ON}:
        skipped.append(f'raw correction left as it is: unexpected values {list(gate)}')
    if d == orig:
        sess.ctx.pop('candidate', None); p('Nothing to change: the camera already holds everything this scan would list', 1.0)
        return {'changes': 0, 'skipped': len(skipped)}
    out = PROJECT / 'out' / 'ui'; out.mkdir(parents=True, exist_ok=True)
    path = out / f'{time.strftime("%Y%m%dT%H%M%S")}_candidate.bin'
    with open(path, 'xb') as fh: fh.write(d)
    s12 = sha12(path)
    p('Checking the new pixel map offline' + ('' if sess.simulate else ' and in the emulated camera (about 2 minutes)'), 0.7)
    args = [sys.executable, '-m', 'xpixelmap', 'verify', '--base', base, str(path)] + (['--no-emu'] if sess.simulate else [])
    rc, log = sh(args, 1800)
    ok = 'offline pre-save checks: ALL PASSED' in log and (sess.simulate or 'emulated round trip: PASS' in log)
    if rc or not ok: raise StopRequest('the new pixel map failed its checks, nothing was sent: ' + log.strip()[-400:])
    nbytes = sum(a != b for a, b in zip(orig, d))
    sess.ctx['candidate'] = {'path': str(path), 'sha12': s12, 'base_sha12': base_sha, 'changes': done, 'skipped': skipped,
                             'bytes': nbytes, 'focus_table': fixed_table}
    p(f'New pixel map {s12} ready: {len(done)} changes, {nbytes} bytes differ, all checks passed', 1.0)
    return {'sha12': s12, 'changes': len(done), 'skipped': len(skipped)}


def report(sess, p):
    """Before / after for every pixel the session touched, plus the focus-site own share and both heat maps."""
    import xt1capture as C, hotscan as H
    sets, mx = _maps(sess); pk = _peaks(sets, mx)
    p('Comparing before and after', 0.3)
    rows = []
    pix = [(f['x'], f['y'], f['label']) for f in sess.ctx.get('found', []) if f['selected'] or f['cls'] == 'focus']
    for x, y, lab in pix:
        row = {'x': x, 'y': y, 'label': lab}
        for n in DARK:
            b, a = pk.get(n), pk.get('after_' + n)
            if b is not None and a is not None:
                row[n] = [int(b[y, x]), int(a[y, x]), int(a[y, x]) <= HOT]
        rows.append(row)
    own = {}
    for n in DARK:
        for tag in (n, 'after_' + n):
            if tag in sets:
                r = C.focus_own_share([H.load(f) for f in sets[tag]['files']])
                own[tag] = round(r[0], 3) if r else None
    if sess.simulate:   # the replayed 'after' frames come from the real v12 session: only its fixes can show in them
        import glob as G, edit_lists as E
        v12 = sorted(G.glob(str(PROJECT / 'xpixelmap' / 'data' / 'backups' / '*backup_37d02ff25784*.bin')))
        if v12:
            d12 = Path(v12[-1]).read_bytes(); in12 = set()
            for n in ('list0', 'list2'): in12 |= E.listed_xy(*E.read_list(d12, n))
            for r in rows:
                if r['label'] != LABELS['focus'] and (r['x'] - 4, r['y']) not in in12: r['sim_untestable'] = True
    rows_t = [r for r in rows if not r.get('sim_untestable')]
    fixed = sum(all(v[2] for k, v in r.items() if k.startswith('dark_')) for r in rows_t)
    sess.ctx['report'] = {'rows': rows, 'own': own, 'fixed': fixed, 'total': len(rows_t), 'untestable': len(rows) - len(rows_t)}
    p(f'{fixed} of {len(rows)} pixels now read at normal level', 1.0)
    _compress_and_clear(sess, p)
    return {'fixed': fixed, 'total': len(rows)}


def _compress_and_clear(sess, p):
    """History keeps a small copy; the RAW frames (about 1.3 GB a run) go. Real sessions only, and only if every set was archived."""
    import archive as A
    if sess.simulate or A.keep_frames(): return
    try:
        p('Keeping a compressed copy for history', 0.95)
        sets = sess.ctx.get('sets', {})
        saved = A.save(sess.dir, sets)
        have = [n for n, i in sets.items() if any(Path(f).is_file() for f in i.get('files', []))]
        if sorted(saved) != sorted(have): sess.log('history copy incomplete; the photos were kept', 'warn'); return
        freed = A.clear_frames(sess.dir, sets, saved)
        sess.log(f'photos compressed for history; {freed / 1e6:.0f} MB of RAW files removed', 'info')
    except Exception as e:
        sess.log(f'could not compress the photos ({e}); they were kept', 'warn')
