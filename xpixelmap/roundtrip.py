"""Emulated round trip for `verify`: the same Session code the real camera would get, driven against the
emulated camera (needs unicorn: runs inside the xt1-emu:burner container)."""
from .image import sha256
from .journal import Journal
from .session import Session, run_write, run_restore


def emulated_roundtrip(base, img, journal_root, log=print):
    """Return (ok, lines)."""
    from .emulated import EmulatedCamera, EmulatedTransport, per_shot_counts, MODELLED
    lines, ok = [], True

    def rec(cond, text):
        nonlocal ok
        ok &= bool(cond)
        lines.append(f'  [{"PASS" if cond else "FAIL"}] {text}')

    quiet = lambda *a: None
    cam = EmulatedCamera(base)
    t = EmulatedTransport(cam)
    rec(cam.ram() == base, 'emulated boot from base flash reproduces the base image')
    s = run_write(t, Journal(journal_root, 'emu-write'), base, img, confirm=lambda p: True, log=quiet)
    rec(s.saved, 'write (full image) + read-back sha256 + save reported success')
    rec(cam.flash_image() == img, 'flash after save == intended image (0x80..0xa0 as the save command leaves it)')
    cam.reboot()
    s2 = Session(t, Journal(journal_root, 'emu-after-reboot'), img, log=quiet)
    s2.identify(); data, sha, _ = s2.backup()
    rec(data == img, f'after reboot, full read == intended image (sha256 {sha[:12]})')
    rec(all(img[a:b] == base[a:b] for a, b in MODELLED), 'image does not touch the modelled (non-emulated) lens region')
    before, after = per_shot_counts(base), per_shot_counts(cam.ram())
    for k in before:
        lines.append(f'  per-shot setup {k}: before {[(m, hex(o), c) for m, o, c in before[k]]}')
        lines.append(f'  per-shot setup {k}: after  {[(m, hex(o), c) for m, o, c in after[k]]}')
    rec(all(after[k] for k in after), 'per-shot correction setup runs on the recalled image')
    run_restore(t, Journal(journal_root, 'emu-restore'), base, confirm=lambda p: True, log=quiet)
    cam.reboot()
    s3 = Session(t, Journal(journal_root, 'emu-after-restore'), base, log=quiet)
    s3.identify(); data, sha, _ = s3.backup()
    rec(data == base, f'restore drill: after restore + reboot, camera == base (sha256 {sha[:12]})')
    return ok, lines
