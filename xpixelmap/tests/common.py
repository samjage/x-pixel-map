"""Shared test setup. Importing this installs the RealTransport tripwire before anything else can touch it."""
import os
import sys
import tempfile
from pathlib import Path

os.environ['XPIXELMAP_NO_REAL'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import xpixelmap.real as real                              # noqa: E402

REAL_INSTANTIATIONS = []


def _tripwire(cls, *a, **k):
    REAL_INSTANTIATIONS.append((a, k))
    raise AssertionError('RealTransport must never be instantiated in tests')


if not getattr(real.RealTransport, '_tripwired', False):
    real.RealTransport.__new__ = staticmethod(_tripwire)
    real.RealTransport._tripwired = True

from xpixelmap.constants import BACKUP_PATH, IMAGE_SIZE      # noqa: E402
from xpixelmap.image import load_image                         # noqa: E402

try:
    import unicorn  # noqa: F401
    HAVE_UNICORN = True
except ImportError:
    HAVE_UNICORN = False

if not BACKUP_PATH.is_file():
    print(f'SKIPPED: these tests need your own untouched config dump at {BACKUP_PATH}\n(read it with: python -m xpixelmap backup --real, or set XPIXELMAP_FACTORY_BACKUP)')
    raise SystemExit(0)
BACKUP = load_image(BACKUP_PATH)
PIXEL = (2497, 1861)
_EDITED = []
TMP = Path(tempfile.mkdtemp(prefix='xpixelmap-tests-'))


def edited():
    """The one-pixel image (RAF 2497,1861, an AF pixel: into list1+list3), built once via helper/edit_lists.py."""
    if not _EDITED:
        from xpixelmap.build import build_image
        _EDITED.append(build_image(BACKUP, [PIXEL])[0])
    return _EDITED[0]


def journal(label):
    from xpixelmap.journal import Journal
    return Journal(TMP / 'sessions', label)


def quiet(*a, **k):
    pass


def yes(phrase):
    return True
