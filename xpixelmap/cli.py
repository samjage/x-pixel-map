"""X Pixel Map tool (xpixelmap) command line. See README.md. Every camera command needs --emu or --real explicitly."""
import argparse
import os
import sys
from pathlib import Path

from .constants import PROJECT, BACKUP_PATH, BACKUP_SHA256, DATA_DIR, IMAGE_SIZE, SAVE_PHRASE
from . import firmware
from .image import load_image, presave_checks, sha256, summary, ImageError
from .journal import Journal, read_events, interpret
from .session import Session, SessionError, run_write, run_restore
from .build import build_file, parse_pixels, BuildError
from .allowlist import ForbiddenOperation
from .transport import TransportError



class ToolError(Exception):
    pass


def interactive_confirm(phrase):
    if not sys.stdin.isatty():
        print('REFUSED: the save confirmation must be typed at an interactive terminal.')
        return False
    print('\nThe camera RAM now holds the verified image. Saving writes it to the camera flash.')
    print('Do not touch the camera or the cable until the result is printed.')
    print(f'To SAVE, type exactly:  {phrase}')
    try:
        # discard anything typed or pasted before this prompt (it would otherwise prefix the answer and decline it)
        import termios
        termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (ImportError, OSError):
        pass
    try:
        return input('> ').strip() == phrase
    except EOFError:
        return False


def make_transport(args):
    """Returns (transport, emulated_camera_or_None). RealTransport is only reached with --real."""
    if getattr(args, 'real', False) and getattr(args, 'emu', False):
        raise ToolError('choose one of --emu / --real')
    if getattr(args, 'real', False):
        from .real import RealTransport
        return RealTransport(real=True), None
    if getattr(args, 'emu', False):
        try:
            from .emulated import EmulatedCamera, EmulatedTransport
        except ImportError as e:
            raise ToolError(f'--emu needs unicorn and your firmware file: {e}')
        nand = Path(args.emu_flash_state) if args.emu_flash_state else None
        src = nand if nand and nand.exists() else Path(args.emu_flash)
        cam = EmulatedCamera(load_image(src))
        return EmulatedTransport(cam), cam
    raise ToolError('no transport: pass --emu (emulated camera) or --real (the actual camera over USB)')


def _persist_emu(args, cam):
    if cam is not None and args.emu_flash_state:
        p = Path(args.emu_flash_state); tmp = p.with_suffix('.tmp')
        tmp.write_bytes(cam.flash_image()); os.replace(tmp, p)


def _sessions(args):
    return Path(args.data_dir) / 'sessions'


def _confirm_for(args):
    if getattr(args, 'emu', False) and getattr(args, 'yes_emulated', False):
        return lambda phrase: True
    given = getattr(args, 'confirm', None)
    if given:
        # confirmation given up front: the first 12 hex digits of the image sha256 must match, so a wrong file still refuses
        def confirm(phrase):
            ok = phrase == SAVE_PHRASE.format(sha12=given.strip().lower())
            print(f'--confirm {given}: ' + ('matches this image, saving.' if ok else f'does NOT match ({phrase}); not saving.'))
            return ok
        return confirm
    return interactive_confirm


def cmd_backup(args):
    base = load_image(args.base) if Path(args.base).is_file() else bytes(IMAGE_SIZE)    # the very first read has nothing to compare with
    t, cam = make_transport(args)
    j = Journal(_sessions(args), 'backup')
    try:
        s = Session(t, j, base)
        s.identify()
        data, sha, path = s.backup(Path(args.data_dir) / 'backups')
    finally:
        _close(t)
    print(f'stored {path}\n  sha256 {sha}')
    if BACKUP_SHA256:
        print(f'  known backup ({BACKUP_SHA256[:12]}): {"MATCH" if sha == BACKUP_SHA256 else "differs"}')
    else:
        print('  known backup: none on this install (no factory dump), nothing to compare against')
    rc = 0
    if args.expect:
        exp = load_image(args.expect)
        ok = data == exp
        print(f'  expected image {args.expect}: {"MATCH" if ok else "DIFFERS"}')
        if not ok:
            from .image import first_diff
            print(f'    first difference at {first_diff(data, exp):#x}')
            rc = 1
    elif BACKUP_SHA256 and sha != BACKUP_SHA256:
        print('  camera image differs from the known backup: STOP and compare before any write.')
        rc = 1
    _persist_emu(args, cam)
    return rc


def _close(t):
    if hasattr(t, '__exit__'):
        t.__exit__(None, None, None)


def cmd_build(args):
    pixels = parse_pixels(args.pixel)
    partners = parse_pixels(args.partner) if args.partner else []
    tag = "_".join(p.replace(",", "-") for p in args.pixel) + "".join(f'_p{p.replace(",", "-")}' for p in args.partner)
    out = args.out or str(Path(args.data_dir) / 'builds' / f'cfgdata_{tag}.bin')
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    _, lines = build_file(args.base, out, pixels, partners)
    print('\n'.join(lines))
    print('next: python3 -m xpixelmap verify', out)
    return 0


def cmd_verify(args):
    img, base = load_image(args.image), load_image(args.base)
    print(f'image {args.image}\n  sha256 {sha256(img)}\nbase  {args.base}\n  sha256 {sha256(base)}')
    print(summary(img, base))
    bad = presave_checks(img, base)
    if args.pixel:
        from .build import _edit_lists, partner_extra
        el, pixels = _edit_lists(), parse_pixels(args.pixel)
        extra = partner_extra(el, pixels, parse_pixels(args.partner) if args.partner else [])
        bad += el.check(base, img, pixels, extra)
    print('offline pre-save checks:', 'ALL PASSED' if not bad else 'FAILED')
    for b in bad: print('  -', b)
    if bad:
        return 1
    if args.no_emu:
        print('emulated round trip: SKIPPED (--no-emu)')
        return 0
    if not firmware.ready():
        raise ToolError(f'the emulated rehearsal needs your firmware file first: python -m xpixelmap firmware FWUP0005.DAT ({firmware.FW_URL})')
    from .roundtrip import emulated_roundtrip
    root = Path(args.data_dir) / 'sessions'
    ok, lines = emulated_roundtrip(base, img, root)
    print('emulated round trip (write -> read-back -> save -> reboot -> recall -> restore drill):')
    print('\n'.join(lines))
    print('emulated round trip:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


def cmd_firmware(args):
    firmware.install(Path(args.file).read_bytes())
    print('firmware ready:', firmware.FW_DIR)
    return 0


def cmd_write(args, save):
    img, base = load_image(args.image), load_image(args.base)
    bad = presave_checks(img, base)
    if bad:
        raise ToolError('pre-save checks failed: ' + '; '.join(bad))
    t, cam = make_transport(args)
    j = Journal(_sessions(args), 'save' if save else 'write')
    print('session journal:', j.dir)
    try:
        run_write(t, j, base, img, confirm=_confirm_for(args) if save else None,
                  backup_dir=Path(args.data_dir) / 'backups', image_path=args.image)
    finally:
        _close(t)
        print(interpret(read_events(j.dir))[1])
        _persist_emu(args, cam)
    return 0


def cmd_restore(args):
    img = load_image(args.backup)
    if sha256(img) != args.backup_sha:
        raise ToolError(f'{args.backup} sha256 {sha256(img)} != expected {args.backup_sha}')
    t, cam = make_transport(args)
    j = Journal(_sessions(args), 'restore')
    print('session journal:', j.dir)
    try:
        run_restore(t, j, img, _confirm_for(args), backup_dir=Path(args.data_dir) / 'backups',
                    allow_trigger_diff=args.allow_trigger_diff)
    finally:
        _close(t)
        print(interpret(read_events(j.dir))[1])
        _persist_emu(args, cam)
    return 0


def cmd_status(args):
    if args.session:
        d = Path(args.session)
    else:
        ds = sorted(p for p in _sessions(args).glob('*') if (p / 'journal.jsonl').exists())
        if not ds:
            print('no sessions recorded'); return 0
        d = ds[-1]
    ev = read_events(d)
    print('session', d)
    for e in ev:
        extra = {k: v for k, v in e.items() if k not in ('seq', 'time', 'event', 'info')}
        print(f"  {e.get('seq', '?'):>3} {e.get('time', '')[:19]} {e.get('event')} {extra if extra else ''}")
    key, text = interpret(ev)
    print(f'\nSTATE: {key}\n{text}')
    return 0


def parser():
    p = argparse.ArgumentParser(prog='xpixelmap', description='X Pixel Map tool: Fujifilm X defect-list repair (X-T1 fw 5.52) - see README.md')
    p.add_argument('--data-dir', default=str(DATA_DIR), help='where backups, builds and session journals go')
    sub = p.add_subparsers(dest='cmd', required=True)

    def transport_opts(sp):
        sp.add_argument('--emu', action='store_true', help='use the emulated camera (inside the container)')
        sp.add_argument('--real', action='store_true', help='use the REAL camera over USB (ff80 service mode)')
        sp.add_argument('--emu-flash', default=str(BACKUP_PATH), help='initial emulated flash image')
        sp.add_argument('--emu-flash-state', help='file that keeps the emulated flash between runs')
        sp.add_argument('--yes-emulated', action='store_true', help='auto-confirm saves (emulated camera only)')

    sp = sub.add_parser('backup', help='full read of the camera image, stored as a new file, compared')
    transport_opts(sp)
    sp.add_argument('--base', default=str(BACKUP_PATH))
    sp.add_argument('--expect', help='image the camera should hold (e.g. after a save + reboot)')
    sp = sub.add_parser('firmware', help='load your X-T1 5.52 firmware file for the emulated rehearsal, once')
    sp.add_argument('file')
    sp = sub.add_parser('build', help='add pixels (RAF coords) to list0+list2 of a copy, new file only')
    sp.add_argument('--base', default=str(BACKUP_PATH))
    sp.add_argument('--out')
    sp.add_argument('--pixel', action='append', default=[], help='x,y in RAF coordinates (repeatable)')
    sp.add_argument('--partner', action='append', default=[],
                    help='x,y of a --pixel whose left neighbour also goes into list2, like the 9/23 repair (repeatable)')
    sp = sub.add_parser('verify', help='offline pre-save checks + emulated round trip')
    sp.add_argument('image')
    sp.add_argument('--base', default=str(BACKUP_PATH))
    sp.add_argument('--pixel', action='append', default=[], help='also run edit_lists.check for these pixels')
    sp.add_argument('--partner', action='append', default=[], help='the --partner values the image was built with')
    sp.add_argument('--no-emu', action='store_true')
    sp.add_argument('--emu-only', action='store_true', help=argparse.SUPPRESS)
    for name, h in (('write', 'write full image + read-back verify; NO save'),
                    ('save', 'write + read-back verify + save (typed confirmation, or --confirm SHA12)')):
        sp = sub.add_parser(name, help=h)
        transport_opts(sp)
        sp.add_argument('image')
        sp.add_argument('--base', default=str(BACKUP_PATH), help='image the camera must currently hold')
        if name == 'save':
            sp.add_argument('--confirm', metavar='SHA12', help='save without the typed prompt: first 12 hex of the image sha256')
    sp = sub.add_parser('restore', help='write + verify + save the backup image')
    transport_opts(sp)
    sp.add_argument('backup', nargs='?', default=str(BACKUP_PATH))
    sp.add_argument('--backup-sha', default=BACKUP_SHA256)
    sp.add_argument('--allow-trigger-diff', action='store_true',
                    help='camera 0x80..0x83 may differ (e.g. factory defaults); the save command rewrites them')
    sp.add_argument('--confirm', metavar='SHA12', help='save without the typed prompt: first 12 hex of the backup sha256')
    sp = sub.add_parser('status', help='show what the last (or given) session did and what is true now')
    sp.add_argument('session', nargs='?')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.cmd == 'firmware': return cmd_firmware(args)
        if args.cmd == 'backup': return cmd_backup(args)
        if args.cmd == 'build': return cmd_build(args)
        if args.cmd == 'verify': return cmd_verify(args)
        if args.cmd == 'write': return cmd_write(args, save=False)
        if args.cmd == 'save': return cmd_write(args, save=True)
        if args.cmd == 'restore': return cmd_restore(args)
        if args.cmd == 'status': return cmd_status(args)
    except (ToolError, ImageError, BuildError, SessionError, TransportError, ForbiddenOperation, firmware.FirmwareError) as e:
        print(f'ERROR ({type(e).__name__}): {e}', file=sys.stderr)
        return 2
    except Exception as e:
        if type(e).__name__ == 'RealHardwareNotAllowed':
            print(f'ERROR: {e}', file=sys.stderr); return 2
        raise
    return 2
