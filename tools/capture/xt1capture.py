"""Guided defect-detection capture for the X-T1 (normal shooting mode, USB tethered / PC SHOOT). Never uses service
mode. The PC fires the shots and downloads the RAFs; you set the shutter dial / shutter type / cap when prompted.
Every frame's EXIF (exposure, ISO, shutter type) is checked against the step; mismatches are kept but flagged.

  python3 tools/capture/xt1capture.py capture --plan detect --label before   # guided session -> captures/<stamp>_<label>/
  python3 tools/capture/xt1capture.py capture --plan detect --simulate DIR   # no camera: copies RAFs from DIR
  python3 tools/capture/xt1capture.py analyze captures/<session>             # hot (dark sets) + dead (flat sets)
  python3 tools/capture/xt1capture.py compare <before> <after> --pixel 2497,1861 [--pixel x,y]

gphoto2 use is limited to: --auto-detect, --get-config, --set-config iso/capturetarget, --capture-image-and-download.
Never --wait-event (it upset this camera before).
"""
import argparse, datetime, glob, hashlib, io, json, os, shutil, struct, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
sys.path.insert(0, str(PROJECT / 'helper'))
CAPTURES = PROJECT / 'captures'
SAFE = Path(os.environ['XPIXELMAP_SAFE_COPY']) / 'captures' if os.environ.get('XPIXELMAP_SAFE_COPY') else None   # optional second copy

# (name, kind, shutter type, exposure seconds, ISO, frames). ES = electronic (no shutter wear), MS = mechanical.
# Electronic shutter covers <= 1 s on the X-T1; longer exposures fall back to the mechanical shutter.
PLANS = {
    'detect': [
        ('dark_short_iso200', 'dark', 'ES', 1 / 200, 200, 5),
        ('dark_short_iso800', 'dark', 'ES', 1 / 200, 800, 5),
        ('dark_short_iso3200', 'dark', 'ES', 1 / 200, 3200, 3),
        ('dark_1s_iso200', 'dark', 'ES', 1.0, 200, 3),
        ('dark_1s_iso800', 'dark', 'ES', 1.0, 800, 3),
        ('dark_4s_iso200', 'dark', 'MS', 4.0, 200, 2),
        ('flat_iso200', 'flat', 'ES', None, 200, 5),
        ('flat_iso800', 'flat', 'ES', None, 800, 5),
    ],
    'iso1600': [('dark_short_iso1600', 'dark', 'ES', 1 / 200, 1600, 3)],
    # the camera as it is, on the shutter the owner shoots with; the last set is the electronic control
    'mech': [
        ('dark_1s_iso200', 'dark', 'MS', 1.0, 200, 6),
        ('dark_quarter_iso200', 'dark', 'MS', 1 / 4, 200, 3),
        ('dark_short_iso200', 'dark', 'MS', 1 / 200, 200, 3),
        ('dark_4s_iso200', 'dark', 'MS', 4.0, 200, 3),
        ('dark_1s_es_iso200', 'dark', 'ES', 1.0, 200, 3),
        ('dark_1s_iso800', 'dark', 'MS', 1.0, 800, 6),
        ('dark_quarter_iso800', 'dark', 'MS', 1 / 4, 800, 3),
        ('dark_short_iso800', 'dark', 'MS', 1 / 200, 800, 3),
        ('dark_4s_iso800', 'dark', 'MS', 4.0, 800, 3),
        ('dark_1s_iso1600', 'dark', 'MS', 1.0, 1600, 6),
        ('dark_quarter_iso1600', 'dark', 'MS', 1 / 4, 1600, 3),
        ('dark_short_iso1600', 'dark', 'MS', 1 / 200, 1600, 3),
        ('dark_4s_iso1600', 'dark', 'MS', 4.0, 1600, 3),
    ],
    # the ISO range actually shot (200-800, 1600 at most): short lists = Tv < 1 s and ISO <= 1600, long lists = Tv >= 1 s
    'sweep': [
        ('dark_short_iso200', 'dark', 'ES', 1 / 200, 200, 3),
        ('dark_1s_iso200', 'dark', 'ES', 1.0, 200, 3),
        ('dark_4s_iso200', 'dark', 'MS', 4.0, 200, 3),
        ('dark_short_iso400', 'dark', 'ES', 1 / 200, 400, 3),
        ('dark_1s_iso1600', 'dark', 'ES', 1.0, 1600, 3),
    ],
    # where do the ISO 200 "+100" pixels switch on? 1/4 s and 1/2 s use the short lists, 2 s the long ones
    'probe200': [
        ('dark_quarter_iso200', 'dark', 'ES', 1 / 4, 200, 3),
        ('dark_half_iso200', 'dark', 'ES', 1 / 2, 200, 3),
        ('dark_2s_iso200', 'dark', 'ES', 2.0, 200, 3),
    ],
    'verify': [
        ('dark_short_iso800', 'dark', 'ES', 1 / 200, 800, 5),
        ('dark_1s_iso800', 'dark', 'ES', 1.0, 800, 3),
        ('flat_iso800', 'flat', 'ES', None, 800, 5),
    ],
    # white frames catch what dark frames cannot: light-sensitive (too bright under light) and dead (too dark) pixels;
    # X-T1 2026: (2496,1859) is invisible in dark frames but up to +7900 in photos
    'white': [
        ('flat_iso200', 'flat', 'ES', None, 200, 5),
        ('flat_iso800', 'flat', 'ES', None, 800, 5),
    ],
}
FLAT_HOW = ('PHONE SCREEN all white at full brightness, lens almost touching it, MANUAL focus at INFINITY so it is a '
            'smooth blur, aperture f/5.6 set by hand (aperture ring on XF lenses, command dial on ringless XC lenses; not wide open: AF pixels react to steep light angles), shutter dial on A (the camera meters the white screen to mid-grey itself)')
BLACK, WHITE = 1024, 16383   # X-T1 14-bit raw black and saturation levels
WATCH = [(2317, 1649), (1218, 2629), (4784, 935)]   # pixels the current candidate is meant to fix (focus pixel, two Fuji-style left partners); analyze prints FIXED / STILL HOT
SHUTTER_NAMES = {0: 'MS', 1: 'ES', 2: 'ES', 3: 'EFC'}   # Fuji MakerNote 0x1050 ShutterType


def gp(*args, check=True):
    if '--wait-event' in args: raise SystemExit('refusing --wait-event')
    r = subprocess.run(['gphoto2', *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode: raise RuntimeError(f'gphoto2 {" ".join(args)}: {r.stderr.strip() or r.stdout.strip()}')
    return r


def set_cfg(name, value, tries=6):
    """Set a camera setting from the PC; the camera answers 'busy' for a while after a long frame, so retry, then give up loudly."""
    import time
    for _ in range(tries):
        r = gp('--set-config', f'{name}={value}', check=False)
        if r.returncode == 0: return
        time.sleep(4)
    raise SystemExit(f'the camera would not accept {name}={value}: {(r.stderr or r.stdout).strip()[:200]}')


def speed_choices():
    """{label: seconds} of the shutter speeds the camera offers (read-only gphoto2 --get-config)."""
    out = {}
    for l in gp('--get-config', 'shutterspeed', check=False).stdout.splitlines():
        if not l.startswith('Choice:'): continue
        lab = l.split(None, 2)[2].strip()
        try:
            if lab.endswith('s') and not lab.endswith('ms'): out[lab] = float(lab[:-1])
            elif '/' in lab: n, d = lab.split('/'); out[lab] = float(n) / float(d)
        except ValueError:
            pass
    return out


def auto_exposure(out, name, tries=6, set_speed=None):
    """White frames with the computer setting the speed: the camera does not meter, so find the shutter speed that puts the
    centre of the frame at mid-level (30-65 %, nothing clipped) with trial frames. Returns (label, seconds)."""
    import numpy as np, hotscan as H
    ch = speed_choices()
    t = 1 / 250
    for i in range(tries):
        lab = min(ch, key=lambda k: abs(np.log(ch[k] / t)))
        (set_speed(ch[lab]) if set_speed else set_cfg('shutterspeed', lab))
        base = out / f'trial_{name}_{i + 1}'
        gp('--capture-image-and-download', '--filename', str(base) + '.%C')
        raf = base.with_suffix('.RAF')
        for f in out.glob(base.name + '.*'):
            if f.suffix.lower() == '.raf' and f != raf: f.rename(raf)
        a = H.load(raf); h, w = a.shape
        level = (float(np.median(a[h // 4:3 * h // 4, w // 4:3 * w // 4])) - BLACK) / (WHITE - BLACK)
        clipped = float(np.mean(a >= WHITE - 64))
        et, eiso, est = exif(raf)
        print(f'  exposure search {name}: {lab} -> level {level:.0%}, clipped {clipped:.2%} (ISO {eiso}, {est})')
        if 0.30 <= level <= 0.65 and clipped < 0.001: return lab, ch[lab]
        t = ch[lab] * (0.45 / max(level, 0.02) if level < 0.99 else 0.2)
    raise SystemExit(f'{name}: could not find a good exposure in {tries} tries; check the screen is white, bright and covers the lens')


def exif(path):
    """Exposure (s), ISO and shutter type from the RAF's embedded JPEG EXIF + Fuji MakerNote."""
    from PIL import Image
    h = open(path, 'rb').read(0x200000)
    jo, jl = struct.unpack('>II', h[0x54:0x5c])
    ex = Image.open(io.BytesIO(h[jo:jo + jl])).getexif().get_ifd(0x8769)
    t, iso = ex.get(0x829a), ex.get(0x8827)
    st = None
    mn = ex.get(0x927c)
    if isinstance(mn, (bytes, bytearray)) and mn[:8] == b'FUJIFILM':
        o = struct.unpack_from('<I', mn, 8)[0]
        for i in range(struct.unpack_from('<H', mn, o)[0]):
            tag, typ, cnt, val = struct.unpack_from('<HHII', mn, o + 2 + 12 * i)
            if tag == 0x1050: st = SHUTTER_NAMES.get(val & 0xffff, f'type{val & 0xffff}')
    return (float(t) if t else None), iso, st


def fnumber(path):
    """Aperture from the RAF's embedded EXIF (None without a lens / unknown)."""
    from PIL import Image
    h = open(path, 'rb').read(0x200000)
    jo, jl = struct.unpack('>II', h[0x54:0x5c])
    f = Image.open(io.BytesIO(h[jo:jo + jl])).getexif().get_ifd(0x8769).get(0x829d)
    try:
        f = float(f)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f if f == f and f > 0 else None   # body cap / no lens records 0/0 (nan)


def sha(path):
    return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def prompt(msg, yes=False):
    if yes:
        print(f'\n>>> {msg}')
        return
    input(f'\n>>> {msg}\n    press Enter when ready ')


def fmt_t(t):
    return 'A (auto)' if t is None else (f'1/{round(1 / t)} s' if t < 1 else f'{t:g} s')


def controllable():
    """Which of the settings we use the camera lets the PC change (read-only gphoto2 --list-config)."""
    names = {l.rsplit('/', 1)[-1].strip() for l in gp('--list-config', check=False).stdout.splitlines()}
    return {n for n in ('iso', 'shutterspeed', 'shutterspeed2', 'f-number', 'aperture', 'exposureprogram', 'capturetarget') if n in names}


def pc_priority():
    """Hand control from the dials to the PC: Fuji property 0xD207 (gphoto2 'prioritymode'), the same one Fuji's
    XSDK_SetPriorityMode sets (XGFXAPI.dll CCameraParamPriority). Whether the X-T1 then takes ISO / shutter from the PC
    is untested; the EXIF check after each frame shows it. Returns the label set, or None if the camera has no such setting."""
    r = gp('--get-config', 'prioritymode', check=False)
    if r.returncode: return None
    choices = [l.split(None, 2)[2].strip() for l in r.stdout.splitlines() if l.startswith('Choice:')]
    current = next((l.split(':', 1)[1].strip() for l in r.stdout.splitlines() if l.startswith('Current:')), '?')
    pick = next((c for c in choices if c.lower() in ('usb', 'pc', 'pc priority', 'usb priority')),
                next((c for c in choices if c.lower() != 'camera'), None))
    print(f'  priority mode: {current} (choices {choices})' + (f' -> {pick}' if pick else ''))
    if pick and pick != current: set_cfg('prioritymode', pick)
    return pick


def warm_up(seconds):
    """Keep the camera awake with live view for a while so the sensor sits at its working temperature; touches the
    camera every 30 s (a read-only config query) so it does not sleep."""
    import time
    if seconds <= 0: return
    end = time.time() + seconds
    while (left := end - time.time()) > 0:
        print(f'\r  warming up: {int(left) // 60}:{int(left) % 60:02d} left (leave live view on) ', end='', flush=True)
        gp('--get-config', 'iso', check=False)
        time.sleep(min(30, max(left, 0)))
    print('\r  warm-up done.' + ' ' * 40)


def test_tether(args):
    """One frame over USB: proves PC SHOOT works and shows which settings the PC can change. Saves into captures/."""
    print(gp('--auto-detect').stdout)
    ctl = controllable(); print('camera lets the PC set:', ', '.join(sorted(ctl)) or 'nothing we use')
    out = CAPTURES / f'{datetime.datetime.now():%Y-%m-%d_%H%M%S}_tethertest'; out.mkdir(parents=True)
    r = gp('--capture-image-and-download', '--filename', str(out / 'test.%C'), check=False)
    files = sorted(out.iterdir()); print('received:', [f.name for f in files])
    if not files:
        print(r.stderr.strip() or r.stdout.strip())
        if 'auto-focus' in (r.stderr + r.stdout):
            print('the camera tried to autofocus and failed: set the front focus switch to M (manual) and try again')
    for f in files:
        if f.suffix.upper() == '.RAF': print('  exposure %s s, ISO %s, shutter %s' % exif(f))
    return 0 if files else 1


def capture(args):
    plan = [s for s in PLANS[args.plan] if not args.sets or s[0] in args.sets.split(',')]
    if not plan: raise SystemExit(f'no set of plan {args.plan} matches --sets {args.sets}')
    if not args.simulate:
        found = gp('--auto-detect').stdout
        if len([l for l in found.splitlines() if l.strip()]) < 3:
            raise SystemExit('no camera found on USB: switch it on in normal mode (USB MODE = PC SHOOT AUTO), plug it in, and run this again')
    stamp = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
    out = CAPTURES / f'{stamp}_{args.label}'
    out.mkdir(parents=True, exist_ok=False)
    man = open(out / 'manifest.jsonl', 'a')
    sim = sorted(Path(args.simulate).glob('*.RAF')) if args.simulate else None
    if not sim:
        iso_note = ('ISO dial on A (the PC sets ISO for every set)' if args.pc_priority
                    else 'ISO dial on the ISO for this run (a fixed dial position overrides the PC on this body)')
        prompt('Camera in NORMAL mode (not service mode), USB MODE = PC SHOOT, RAW (RAF), AUTO POWER OFF = OFF, '
               'LONG EXPOSURE NR = OFF, focus switch M, SHUTTER TYPE = MECHANICAL. ' + iso_note, args.yes)
        if args.pc_priority: pc_priority()
        ctl = controllable()
        print('camera lets the PC set:', ', '.join(sorted(ctl)) or 'nothing we use')
        iso_ok, ss_ok = 'iso' in ctl, 'shutterspeed' in ctl
        warm_up(args.warmup)
    n = 0
    for name, kind, stype, t, iso, frames in plan:
        if not sim:
            what = ('lens stays ON, LENS CAP on, dark cloth over the lens, dim room' + ('' if t and t >= 1 else '')
                    if kind == 'dark' else 'lens on, ' + FLAT_HOW)
            dial = '' if (ss_ok and t) else f'; shutter dial {fmt_t(t)}'
            iso_how = f'ISO DIAL on A (PC priority: the PC sets ISO {iso})' if args.pc_priority else f'ISO DIAL on {iso} (or use --pc-priority with the dial on A)'
            prompt(f'{name}: {what}; SHUTTER TYPE = {"ELECTRONIC" if stype == "ES" else "MECHANICAL"}{dial}'
                   + f'; {iso_how}' + ' - confirm when ready', args.yes)
            if args.pc_priority and iso_ok: set_cfg('iso', str(iso))
            if ss_ok and t: set_cfg('shutterspeed', "1/%d" % round(1 / t) if t < 1 else "%gs" % t)
            if ss_ok and kind == 'flat': t = auto_exposure(out, name)[1]
        for k in range(min(frames, args.frames or frames)):
            dst = out / f'{name}_{k + 1:02d}.RAF'
            if sim:
                shutil.copy2(sim[n % len(sim)], dst)
            else:
                gp('--capture-image-and-download', '--filename', str(out / f'{name}_{k + 1:02d}.%C'))
                for f in out.glob(f'{name}_{k + 1:02d}.*'):   # the camera names it .raf (lowercase)
                    if f.suffix.lower() == '.raf' and f != dst: f.rename(dst)
                if not dst.exists(): raise SystemExit(f'no RAF came back for {dst.name}; is the camera set to RAW?')
            n += 1
            et, eiso, est = exif(dst)
            fn = fnumber(dst)
            bad = [] if sim else [w for w, ok in (
                ('exposure', t is None or (et and abs(et - t) / t < 0.05)),
                ('ISO', eiso == iso or kind == 'flat'), ('shutter type', est in (None, stype)),
                ('aperture wider than f/4 (set f/5.6 on the ring or command dial)', kind != 'flat' or not fn or fn >= 4)) if not ok]
            rec = dict(file=dst.name, set=name, kind=kind, want=dict(shutter=stype, t=t, iso=iso),
                       got=dict(t=et, iso=eiso, shutter=est, f=fn), mismatch=bad, sha256=sha(dst))
            man.write(json.dumps(rec) + '\n'); man.flush()
            print(f'  {dst.name}: t={et} ISO={eiso} shutter={est}' + (f'  MISMATCH {bad}' if bad else ''))
            if bad and not sim:
                man.close()
                raise SystemExit(f'stopping: the camera did not use the requested settings ({bad}). Fix that and run again.')
        if args.show:   # after each set: picture with circles where hot/dead pixels were found
            analyze(argparse.Namespace(session=str(out), only=name, show=True, backdrop=args.backdrop))
    man.close()
    if SAFE and SAFE.parent.exists() and not args.no_safe_copy:
        shutil.copytree(out, SAFE / out.name)
        print('copied to', SAFE / out.name)
    print('session:', out)
    return 0


def frames_by_set(session):
    sets = {}
    for l in open(Path(session) / 'manifest.jsonl'):
        r = json.loads(l)
        sets.setdefault((r['set'], r['kind']), []).append(Path(session) / r['file'])
    return sets


def photo(raf):
    """The RAF's own colour preview and a raw(x, y) -> preview(x, y) mapping, from the RAF's crop records
    (0x110 crop top-left, 0x111 cropped size) and the preview's EXIF orientation. None if unavailable."""
    from PIL import Image
    d = open(raf, 'rb').read(0x400000)
    jo, jl, ho, _ = struct.unpack('>IIII', d[0x54:0x64])
    rec, o = {}, ho + 4
    for _ in range(struct.unpack('>I', d[ho:ho + 4])[0]):
        t, s = struct.unpack('>HH', d[o:o + 4]); rec[t] = d[o + 4:o + 4 + s]; o += 4 + s
    if 0x110 not in rec or 0x111 not in rec: return None
    top, left = struct.unpack('>HH', rec[0x110]); ch, cw = struct.unpack('>HH', rec[0x111])
    im = Image.open(io.BytesIO(d[jo:jo + jl])); im.load()
    orient = im.getexif().get(0x112, 1)   # the preview is stored in sensor orientation; this only says how to show it
    pw, ph = im.size
    to_view = lambda x, y: ((x - left) * pw / cw, (y - top) * ph / ch)
    return im.convert('RGB'), to_view, orient


def upright(img, orient):
    """Rotate a picture drawn in sensor orientation for display (EXIF orientation 3/6/8)."""
    from PIL import Image
    return {3: lambda i: i.transpose(Image.ROTATE_180), 6: lambda i: i.transpose(Image.ROTATE_270),
            8: lambda i: i.transpose(Image.ROTATE_90)}.get(orient, lambda i: i)(img)


def render(img, found, title, out, zoom=12, half=6, raf=None):
    """The colour photo (the RAF's own preview) with a dot where each pixel was found, plus a strip of zoomed raw
    crops so the single pixel is visible. found = [(raf_x, raf_y, is_new)] in raw sensor coordinates; the preview
    mapping comes from the RAF's crop records. Falls back to a grey view of the raw data without a preview."""
    import numpy as np
    from PIL import Image, ImageDraw
    p = photo(raf) if raf else None
    orient = 1
    if p:
        ov, to_view, orient = p
    else:
        s = 4
        h, w = img.shape
        small = img[:h // s * s, :w // s * s].reshape(h // s, s, w // s, s).max(axis=(1, 3)).astype(np.float64)
        lo, hi = np.percentile(small, 1), np.percentile(small, 99.5)
        view = np.clip((small - lo) / max(hi - lo, 1), 0, 1) ** 0.5 * 255
        ov = Image.fromarray(view.astype(np.uint8)).convert('RGB')
        to_view = lambda x, y: (x / s, y / s)
    dr = ImageDraw.Draw(ov)
    for x, y, new in found:   # a dot on each found pixel: red = new, grey = already in Fuji's lists
        c = (255, 40, 40) if new else (150, 150, 150)
        u, v = to_view(x, y)
        dr.ellipse([u - 6, v - 6, u + 6, v + 6], fill=c, outline=(255, 255, 255))
    ov = upright(ov, orient)
    tiles = []
    for x, y, new in found[:24]:
        crop = img[max(0, y - half):y + half + 1, max(0, x - half):x + half + 1].astype(np.float64)
        crop = np.clip((crop - crop.min()) / max(np.ptp(crop), 1), 0, 1) * 255
        t = Image.fromarray(crop.astype(np.uint8)).resize((crop.shape[1] * zoom, crop.shape[0] * zoom), Image.NEAREST).convert('RGB')
        td = ImageDraw.Draw(t)
        cx, cy = (x - max(0, x - half)) * zoom + zoom // 2, (y - max(0, y - half)) * zoom + zoom // 2
        td.ellipse([cx - 3, cy + zoom, cx + 3, cy + zoom + 6], fill=(255, 40, 40) if new else (150, 150, 150))
        td.text((3, 3), f'{x},{y}{" NEW" if new else ""}', fill=(255, 255, 0))
        tiles.append(t)
    tw = (2 * half + 1) * zoom
    strip_h = tw * ((len(tiles) + 5) // 6) if tiles else 0
    sheet = Image.new('RGB', (max(ov.width, 6 * tw), ov.height + 24 + strip_h), (20, 20, 20))
    ImageDraw.Draw(sheet).text((6, 4), title, fill=(255, 255, 255))
    sheet.paste(ov, (0, 24))
    for i, t in enumerate(tiles): sheet.paste(t, ((i % 6) * tw, ov.height + 24 + (i // 6) * tw))
    sheet.save(out)
    return out


def flat_ratio(a):
    """Black-subtracted value / mean of the four same-colour neighbours at +-6 px (1.0 = normal)."""
    import numpy as np
    f = np.maximum(a.astype(np.float64) - BLACK, 0); r = np.ones_like(f)
    nb = (f[6:-6, :-12] + f[6:-6, 12:] + f[:-12, 6:-6] + f[12:, 6:-6]) / 4
    r[6:-6, 6:-6] = f[6:-6, 6:-6] / np.maximum(nb, 1)
    return r


def flat_quality(a, r):
    """(signal level 0..1, clipped fraction, texture) of a white frame; texture = spread of the neighbour ratio in the
    central region (a smooth blur gives ~0.02-0.04, visible detail much more)."""
    import numpy as np
    h, w = a.shape
    c = a[h // 4:3 * h // 4, w // 4:3 * w // 4]
    level = (float(np.median(c)) - BLACK) / (WHITE - BLACK)
    clipped = float(np.mean(a >= WHITE - 64))
    rc = r[h // 4:3 * h // 4, w // 4:3 * w // 4]
    texture = float(np.median(np.abs(rc - np.median(rc))))
    return level, clipped, texture


def library(args):
    """Find defects from ordinary photos: a pixel brighter than its same-colour neighbours in the same place in most
    of many different scenes is not scene detail. Catches light-sensitive defects that dark frames miss."""
    import numpy as np
    import hotscan as H
    import edit_lists as E
    paths = sorted(p for g in args.photos for p in (glob.glob(os.path.join(g, '*.RAF')) if os.path.isdir(g) else [g]))
    paths = [p for p in paths if b'X-T1' in open(p, 'rb').read(64)][-args.recent:]
    if len(paths) < 15: print(f'WARNING only {len(paths)} X-T1 photos; 30+ varied recent photos give reliable results')
    hits, n = None, 0
    for p in paths:
        a = H.load(p)
        if a is None: continue
        h = H.excess(a) > H_THR
        hits = h.astype(np.int32) if hits is None else hits + h
        n += 1
    d = open(E.BACKUP, 'rb').read()
    listed = {nm: E.listed_xy(*E.read_list(d, nm)) for nm in E.LISTS}
    ys, xs = np.nonzero(hits >= max(3, int(args.min_frac * n)))
    rows = []
    for y, x in zip(ys, xs):
        lx = x - 4; where = [nm for nm in listed if (lx, y) in listed[nm]]
        rows.append((int(x), int(y), int(hits[y, x]), where, E.is_af_pixel(lx, y)))
    rows.sort(key=lambda r: -r[2])
    print(f'{n} photos scanned; pixels hot in >= {args.min_frac:.0%} of them:')
    for x, y, k, where, af in rows[:40]:
        print(f'  ({x},{y}) {k}/{n}  ' + ('listed in ' + '+'.join(where) if where else 'NEW') + (' (AF pixel - check its neighbours first)' if af else ''))
    out = Path(args.out or CAPTURES) / f'library_{datetime.datetime.now():%Y-%m-%d_%H%M}.png'
    out.parent.mkdir(parents=True, exist_ok=True)
    render(H.load(paths[-1]), [(x, y, not w) for x, y, k, w, af in rows[:MAX_FOUND]],
           f'{n} photos: {sum(1 for r in rows if not r[3])} new defects', out, raf=paths[-1])
    print(out)
    if args.show: show(out)
    return 0


def show(path):
    if os.environ.get('XT1CAPTURE_NO_VIEWER'): return
    subprocess.Popen(['xdg-open', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def focus_own_share(imgs, n_sites=6000):
    """How much of each focus (phase-detect) site's OWN reading is left in the RAF: the phase stage builds every
    focus site as share*own + (1-share)*neighbours, factory share 16/64 = 0.25 (cfg own byte 0x11426+class).
    Frame-to-frame deviations of a sample of focus sites are regressed on their neighbours' deviations; the part
    the neighbours cannot explain is share x own noise, compared with ordinary same-colour pixels 6 rows away.
    Needs 3+ dark frames of one setting. ~0.25 = factory blend, ~0 = own byte at 0 (v10). Returns (share, n) or None."""
    import numpy as np
    if len(imgs) < 3: return None
    if max(float(np.median(a[1004:2291:7, 1489:3437:7])) for a in imgs) > BLACK + 200: return None   # lit frames: scene changes, not noise
    dev = np.stack(imgs).astype(np.float64); dev -= dev.mean(0)
    ys, xs = np.meshgrid(np.arange(1004, 2291), np.arange(1489, 3437), indexing='ij')
    af = ((xs - 4) % 3 == 0) & ((ys % 12 == 1) | (ys % 12 == 5))
    ay, ax = ys[af], xs[af]
    pick = np.random.default_rng(1).choice(len(ay), min(n_sites, len(ay)), replace=False)
    ay, ax = ay[pick], ax[pick]
    taps = [(dy, dx) for dy in range(-3, 4) for dx in range(-3, 4) if (dy, dx) != (0, 0)]
    taps += [(0, -6), (0, 6), (-6, 0), (6, 0), (-6, -6), (-6, 6), (6, -6), (6, 6), (0, -9), (0, 9)]
    A = np.stack([dev[:, ay + dy, ax + dx].ravel() for dy, dx in taps], 1)
    b = dev[:, ay, ax].ravel()
    w, *_ = np.linalg.lstsq(A, b, rcond=None)
    ordinary = dev[:, ay + 6, ax].ravel()
    return float((b - A @ w).std() / ordinary.std()), len(ay)


def analyze(args):
    import numpy as np
    import hotscan as H
    import edit_lists as E
    d = open(E.BACKUP, 'rb').read()
    listed = {n: E.listed_xy(*E.read_list(d, n)) for n in E.LISTS}
    rows = []
    for (name, kind), paths in frames_by_set(args.session).items():
        if getattr(args, 'only', None) and name != args.only: continue
        imgs = [a for a in (H.load(p) for p in paths) if a is not None]
        if not imgs: continue
        mn = None
        if kind == 'dark':
            # a pixel counts if it is hot in EVERY frame of the set, by more than DARK_SIGMA x the noise of this
            # set (robust sigma of the frame-to-frame minimum excess) and at least DARK_MIN raw units
            mn = np.min([H.excess(a) for a in imgs], axis=0).astype(float)
            core = mn[200:3000, 200:4700]
            sigma = 1.4826 * float(np.median(np.abs(core - np.median(core))))
            thr = max(DARK_MIN, DARK_SIGMA * sigma)
            print(f'  {name}: noise sigma {sigma:.1f}, hot = more than {thr:.0f} above the neighbours in all {len(imgs)} frames')
            for wx, wy in WATCH:   # the pixels a candidate is meant to fix: verdict per set
                ex = [int(H.excess(a)[wy, wx]) for a in imgs]
                print(f'  {name}: FIX CHECK ({wx},{wy}) excess per frame {ex} -> '
                      + ('FIXED (below the hot threshold in every frame)' if max(ex) <= thr else f'STILL HOT (threshold {thr:.0f})'))
            own = focus_own_share(imgs)
            if own: print(f'  {name}: focus-site own share {own[0]:.3f} ({own[1]} sites; factory blend ~0.25, own byte 0 ~0)')
            hits = (mn > thr).astype(int) * len(imgs)
        else:   # white field: dead (too dark) or light-sensitive (too bright) vs the four same-colour neighbours
            hits = 0
            for a, p in zip(imgs, paths):
                r = flat_ratio(a)
                level, clipped, texture = flat_quality(a, r)
                warn = [w for w, bad in (('too dark - longer exposure', level < 0.25), ('clipped - shorter exposure', clipped > 0.001),
                                         ('textured - defocus more / use the phone screen', texture > 0.06)) if bad]
                print(f'  {Path(p).name}: level {level:.0%}, clipped {clipped:.2%}, texture {texture:.3f}' + (f'  WARNING {warn}' if warn else ''))
                hits = hits + ((r < DEAD_RATIO) | (r > BRIGHT_RATIO))
            hits[:, :16] = 0; hits[:, 4940 - 12:] = 0; hits[:12] = 0; hits[-12:] = 0   # raw margin: x 4..4939 is lit, the rest is not image
        ys, xs = np.nonzero(hits >= max(2, int(0.6 * len(imgs))))
        found = []
        for y, x in zip(ys, xs):
            lx = x - 4
            where = [n for n in listed if (lx, y) in listed[n]]
            rows.append((name, int(x), int(y), f'{int(hits[y, x])}/{len(imgs)}', 'listed:' + '+'.join(where) if where else
                         ('NEW AF pixel' if E.is_af_pixel(lx, y) else 'NEW'), int(mn[y, x]) if mn is not None else ''))
            found.append((int(x), int(y), not where))
        found.sort(key=lambda f: not f[2])
        new = sum(f[2] for f in found)
        if len(found) > MAX_FOUND:   # a real sensor has a handful; thousands means scene detail or light leaks
            print(f'  WARNING {name}: {len(found)} spots - these do not look like {kind} frames '
                  f'({"cap on? light leak?" if kind == "dark" else "evenly lit? defocused?"}); showing the first {MAX_FOUND}')
            found = found[:MAX_FOUND]
            new = sum(f[2] for f in found)
        png = render(imgs[0], found, f'{name}: {new} new, {len(found) - new} already listed ({len(imgs)} frames)',
                     Path(args.session) / f'{name}_found.png', raf=getattr(args, 'backdrop', None) or paths[0])
        print(f'  {name}: {new} new, {len(found) - new} listed -> {png}')
        if getattr(args, 'show', False): show(png)
    rows.sort(key=lambda r: (r[4] != 'NEW' and r[4] != 'NEW AF pixel', r[0], r[1], r[2]))
    out = Path(args.session) / 'candidates.csv'
    with open(out, 'w') as f:
        f.write('set,raf_x,raf_y,hits,status,min_excess\n')
        for r in rows: f.write(','.join(map(str, r)) + '\n')
    for r in rows[:40]: print(*r)
    print(f'{len(rows)} rows -> {out}')
    return 0


def mark(args):
    """Dots on any of your photos where the given pixels are (e.g. to see where a hot pixel lands in a shot)."""
    import hotscan as H
    pix = [tuple(int(t) for t in p.split(',')) for p in args.pixel]
    for raf in args.raf:
        out = Path(args.out or Path(raf).parent) / (Path(raf).stem + '_marked.png')
        if out.exists(): raise SystemExit(f'{out} exists, refusing to overwrite')
        render(H.load(raf), [(x, y, True) for x, y in pix], f'{Path(raf).name}: {len(pix)} marked', out, raf=raf)
        print(out)
        if args.show: show(out)
    return 0


def compare(args):
    sb, sa = frames_by_set(args.before), frames_by_set(args.after)
    common = sorted(set(k for k in sb if k[1] == 'dark') & set(sa))
    if not common: raise SystemExit('no matching dark sets between the sessions')
    rc = 0
    for key in common:
        print(f'== {key[0]}')
        r = subprocess.run([sys.executable, '-I', str(PROJECT / 'helper' / 'acceptance.py'),
                            '--before', *map(str, sb[key]), '--after', *map(str, sa[key]),
                            *[a for p in args.pixel for a in ('--pixel', p)]])
        rc |= r.returncode
    print('OVERALL:', 'PASS' if rc == 0 else 'FAIL')
    return rc


H_THR, DEAD_RATIO, BRIGHT_RATIO, MAX_FOUND = 250, 0.6, 1.3, 300   # H_THR: library() only
DARK_SIGMA, DARK_MIN = 8, 50   # dark frames: hot = > max(DARK_MIN, DARK_SIGMA x noise) in every frame of a set

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('capture'); c.add_argument('--plan', choices=PLANS, default='detect')
    c.add_argument('--label', default='session'); c.add_argument('--simulate', help='folder of RAFs to use instead of the camera')
    c.add_argument('--no-safe-copy', action='store_true')
    c.add_argument('--sets', help='comma-separated set names of the plan to run (default: all)')
    c.add_argument('--frames', type=int, help='take at most this many frames per set (3 is enough to confirm a spot)')
    c.add_argument('--warmup', type=int, default=180, help='seconds of live-view warm-up before shooting (0 = skip)')
    c.add_argument('--yes', action='store_true', help='print the per-set instructions but do not wait for Enter')
    c.add_argument('--pc-priority', action='store_true', help='set Fuji priority mode to PC first and let the PC set ISO (untested on the X-T1; EXIF check stops on mismatch)')
    sub.add_parser('test', help='fire one frame over USB to prove tethering works')
    c.add_argument('--no-show', dest='show', action='store_false', help='do not open the found-pixels picture after each set')
    bd = 'a normal photo (RAF) to draw the dots on; detection frames themselves are black or white'
    c.add_argument('--backdrop', help=bd)
    a = sub.add_parser('analyze'); a.add_argument('session'); a.add_argument('--show', action='store_true')
    a.add_argument('--backdrop', help=bd)
    m = sub.add_parser('compare'); m.add_argument('before'); m.add_argument('after')
    m.add_argument('--pixel', action='append', required=True)
    lb = sub.add_parser('library', help='find defects from your ordinary photos (no special shots)')
    lb.add_argument('photos', nargs='+', help='folders or RAF files'); lb.add_argument('--recent', type=int, default=60)
    lb.add_argument('--min-frac', type=float, default=0.5); lb.add_argument('--out'); lb.add_argument('--show', action='store_true')
    k = sub.add_parser('mark'); k.add_argument('raf', nargs='+'); k.add_argument('--pixel', action='append', required=True)
    k.add_argument('--out', help='folder for the marked pictures (default: next to each RAF)'); k.add_argument('--show', action='store_true')
    args = ap.parse_args()
    sys.exit({'capture': capture, 'analyze': analyze, 'compare': compare, 'mark': mark, 'library': library, 'test': test_tether}[args.cmd](args))
