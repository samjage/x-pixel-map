"""Run the whole suite (inside the burner container):
  docker run --rm --network none -v "$PWD":/proj:ro -w /proj xt1-emu:burner \
      python -B xpixelmap/tests/run_all.py
Fails if RealTransport was instantiated or a USB library got imported."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402  (tripwire first)

if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent), pattern='test_*.py',
                                                top_level_dir=str(Path(__file__).resolve().parent))
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    tripped = len(common.REAL_INSTANTIATIONS)
    usb = [m for m in ('usb1', 'usb', 'fftlib', 'ffjlib') if m in sys.modules]
    print(f'\nRealTransport instantiations: {tripped}; USB modules imported: {usb or "none"}; '
          f'unicorn available: {common.HAVE_UNICORN}')
    ok = res.wasSuccessful() and tripped == 0 and not usb
    print('SUITE', 'OK' if ok else 'FAILED')
    sys.exit(0 if ok else 1)
