"""Build an edited image (new file only) with helper/edit_lists.py and its strict checks, plus presave_checks."""
import importlib.util
import io
import contextlib
import os

from .constants import PROJECT, BACKUP_PATH
from .image import load_image, presave_checks, sha256, summary


class BuildError(Exception):
    pass


def _edit_lists():
    spec = importlib.util.spec_from_file_location('xt1_edit_lists', PROJECT / 'helper' / 'edit_lists.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_pixels(items):
    out = []
    for s in items:
        try:
            x, y = (int(t) for t in s.split(','))
        except ValueError:
            raise BuildError(f'bad pixel {s!r}, expected RAF coordinates x,y')
        out.append((x, y))
    if not out:
        raise BuildError('no pixels given')
    return out


def partner_extra(el, pixels, partners):
    """list2 additions for --partner: the left neighbour of each named pixel, like the 9/23/2025 service repair."""
    if not partners:
        return None
    stray = [p for p in partners if p not in pixels]
    if stray:
        raise BuildError(f'--partner {stray} is not one of the --pixel values')
    return {'list2': el.fuji_partners(partners)}


def build_image(base, pixels, partners=()):
    """Return (new image bytes, log lines). Raises BuildError on any refusal or failed check."""
    el = _edit_lists()
    extra = partner_extra(el, pixels, partners)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            new, log = el.add_pixels(base, pixels, extra)
    except SystemExit as e:
        raise BuildError(f'edit_lists refused: {e}')
    bad = el.check(base, new, pixels, extra)
    bad += presave_checks(new, base)
    if bad:
        raise BuildError('checks failed: ' + '; '.join(bad))
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    for name, part, rec in log:
        lines.append(f'  added {name} {part}: raw34={rec:09x} x={rec & 0x1fff} cls={(rec >> 13) & 15} '
                     f'y={(rec >> 17) & 0x1fff} aux={(rec >> 30) & 7} flag={rec >> 33}')
    return new, lines


def build_file(base_path, out_path, pixels, partners=()):
    real_out = os.path.realpath(out_path)
    if real_out in (os.path.realpath(base_path), os.path.realpath(BACKUP_PATH)):
        raise BuildError('refusing to overwrite the base/backup')
    if os.path.exists(out_path):
        raise BuildError(f'{out_path} exists; refusing to overwrite')
    base = load_image(base_path)
    new, lines = build_image(base, pixels, partners)
    with open(out_path, 'xb') as f:
        f.write(new)
    lines.append(summary(new, base))
    lines.append(f'wrote {out_path} sha256 {sha256(new)}')
    return new, lines
