"""RealTransport: thin wrapper over tools/fffw/ff80 (ffjlib jig protocol over USB 04cb:ff80).

NEVER executed by the test suite. Construction requires real=True (the CLI passes it only with --real) and is
refused when XPIXELMAP_NO_REAL is set (the test runner sets it). USB libraries are imported only inside __init__.

Wire operations used (and nothing else):
  info         0x150000 get_device_info tags 1,2,4,8,0x10,0x20,0x40
  config_size  0x1a0002 sub 1 (mb=1)
  read         0x1a0002 sub 2 (mb=1, page 0)
  write        0x1a0002 sub 3 (mb=1, page 0)      -- refused for 0x80..0x83 and outside 0..0x41ffff
  save         app80: write 0x81.. = ff 00*31, write 0x80 = 0x22 (ffjlib.app80_request), wait, read 0x80 x33
"""
import os
import sys
import time

from .allowlist import check_write, check_read
from .constants import PROJECT, SAVE_CMD, SAVE_PARAM
from .transport import Transport, TransportError, TransportTimeout

VENDOR_ID, PRODUCT_ID = 0x04cb, 0xff80


class RealHardwareNotAllowed(Exception):
    pass


class RealTransport(Transport):
    def __init__(self, real=False, save_wait=10.0, response_tries=20):
        if real is not True:
            raise RealHardwareNotAllowed('RealTransport needs real=True (CLI flag --real)')
        if os.environ.get('XPIXELMAP_NO_REAL'):
            raise RealHardwareNotAllowed('XPIXELMAP_NO_REAL is set (test environment): real hardware refused')
        sys.path.insert(0, str(PROJECT / 'tools' / 'fffw' / 'ff80'))
        import usb1                      # noqa: E402  (only here, never at module import)
        import fftlib, ffjlib            # noqa: E402
        self._usb1, self._ffjlib = usb1, ffjlib
        self._save_wait, self._tries = save_wait, response_tries
        self._ctx = usb1.USBContext()
        self._h = self._ctx.openByVendorIDAndProductID(VENDOR_ID, PRODUCT_ID, skip_on_error=True)
        if self._h is None:
            self._ctx.close()
            raise TransportError(f'device {VENDOR_ID:04x}:{PRODUCT_ID:04x} not found (service mode?)')
        self._h.claimInterface(0)
        self._ftl = fftlib.ftl(self._h)
        self._jig = ffjlib.jig(self._ftl)
        self._ftl.open_session()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        try:
            self._ftl.close_session()
        finally:
            try: self._h.releaseInterface(0)
            finally: self._ctx.close()

    def _guard(self, fn, *a):
        u = self._usb1
        try:
            return fn(*a)
        except u.USBErrorTimeout as e:
            raise TransportTimeout(f'USB timeout: {e}')
        except (u.USBError, self._ffjlib.jig_exception, AssertionError) as e:
            raise TransportError(f'{type(e).__name__}: {e}')

    def _info(self):
        tags = self._jig.info_tags()
        return {tags.get(t, hex(t)): self._guard(self._jig.get_device_info, t) for t in (1, 2, 4, 8, 0x10, 0x20, 0x40)}

    def _config_size(self):
        return self._guard(self._jig.get_config_data_size, 1)

    def _read(self, off, n):
        check_read(off, n)
        return self._guard(self._jig.read_config_data, 0, off, n, 1)

    def _write(self, off, data):
        check_write(off, data)            # second, independent check right before the wire
        self._guard(self._jig.write_config_data, 0, off, bytearray(data), 1)

    def _save(self):
        # the only path that writes 0x81..0xa0 and 0x80: exactly ffjlib.app80_request(0x22, [0xff])
        self._guard(self._jig.app80_request, SAVE_CMD, bytearray([SAVE_PARAM]))
        time.sleep(self._save_wait)       # reference tool waits 5 s; flash write measured ~1 s (guess)
        self._guard(self._ftl.nop)
        r = b''
        for _ in range(self._tries):
            r = bytes(self._guard(self._jig.app80_response))
            if len(r) >= 2 and r[0] == SAVE_CMD and r[1] != SAVE_PARAM:
                return r                  # status byte written by the camera (0 = OK)
            time.sleep(1.0)
        raise TransportTimeout(f'no app80 save status after waiting (last response {r[:4].hex()})')
