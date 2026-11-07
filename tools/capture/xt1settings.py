"""X-T1 menu settings backup / restore and clock set over ordinary USB PTP (camera in NORMAL mode, USB = PC SHOOT).

Why: leaving ff80 service mode resets date/time, language and all menu settings to defaults (NOTES.md). This saves
them before a service session and puts them back afterwards, the way FUJIFILM X Acquire does.

Wire sequence copied from Fuji's own SDK (XGFXAPI.dll, CCameraCommandBackupSettings; see
helper/trace_afpix/FINDINGS.txt):
  backup  = GetObjectInfo(0x1008, handle 0) + GetObject(0x1009, handle 0)          -> the settings object
  restore = SendObjectInfo(0x100C, 0, 0) with a 0x434-byte ObjectInfo, ObjectFormat 0x5000, size = object size
            + SendObject(0x100D) with the object
  clock   = gphoto2 --set-config datetime=now (standard PTP DateTime 0x5011)

Usage (system python3, needs python-libusb1):
  python3 tools/capture/xt1settings.py backup                       # read-only; writes camera/settings/<stamp>_settings.bin
  python3 tools/capture/xt1settings.py restore FILE --confirm SHA12  # writes the settings back (asks nothing else)
  python3 tools/capture/xt1settings.py clock                        # camera clock = this PC's local time
"""
import argparse, datetime, hashlib, struct, subprocess, sys, time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
OUT = PROJECT / 'camera' / 'settings'
FUJI_VID, SERVICE_PID = 0x04cb, 0xff80
OP_OPEN, OP_CLOSE, OP_INFO, OP_GET, OP_SEND_INFO, OP_SEND = 0x1002, 0x1003, 0x1008, 0x1009, 0x100C, 0x100D
OK, BUSY = 0x2001, 0x2019
OP_GET_PROP, OP_SET_PROP, PRIORITY, PRIORITY_CAMERA, PRIORITY_PC = 0x1015, 0x1016, 0xD207, 1, 2   # Fuji priority mode: 1 camera dials, 2 PC
CMD, DATA, RESP = 1, 2, 3
SETTINGS_FORMAT, OBJINFO_LEN = 0x5000, 0x434


def container(kind, code, tid, params=(), payload=b''):
    body = b''.join(struct.pack('<I', p) for p in params) + payload
    return struct.pack('<IHHI', 12 + len(body), kind, code, tid) + body


class Ptp:
    """Minimal PTP initiator over a libusb1 handle: command, optional data phase, response."""

    def __init__(self, handle, iface, ep_in, ep_out, timeout_ms=10000):
        self.h, self.iface, self.ep_in, self.ep_out, self.ms = handle, iface, ep_in, ep_out, timeout_ms
        self.tid = 0

    def _read_container(self):
        buf = bytes(self.h.bulkRead(self.ep_in, 1 << 20, timeout=self.ms))
        length, kind, code, tid = struct.unpack_from('<IHHI', buf)
        while len(buf) < length:
            buf += bytes(self.h.bulkRead(self.ep_in, 1 << 20, timeout=self.ms))
        return kind, code, buf[12:length]

    def run(self, code, params=(), send=None):
        """Returns (response code, response params, data received or b'')."""
        self.tid += 1
        self.h.bulkWrite(self.ep_out, container(CMD, code, self.tid, params), timeout=self.ms)
        if send is not None:
            self.h.bulkWrite(self.ep_out, container(DATA, code, self.tid, payload=send), timeout=self.ms)
        data = b''
        while True:
            kind, rcode, body = self._read_container()
            if kind == DATA:
                data = body
                continue
            if kind == RESP:
                return rcode, [struct.unpack_from('<I', body, 4 * i)[0] for i in range(len(body) // 4)], data


def open_camera():
    import usb1
    ctx = usb1.USBContext()
    for dev in ctx.getDeviceList(skip_on_error=True):
        if dev.getVendorID() != FUJI_VID: continue
        if dev.getProductID() == SERVICE_PID:
            raise SystemExit('the camera is in ff80 SERVICE mode: settings need NORMAL mode (USB = PC SHOOT)')
        for setting in (s for cfg in dev.iterConfigurations() for i in cfg.iterInterfaces() for s in i.iterSettings()):
            if setting.getClass() != 6: continue                  # still image (PTP)
            eps = [e.getAddress() for e in setting.iterEndpoints() if e.getAttributes() & 3 == 2]
            ep_in, ep_out = next(a for a in eps if a & 0x80), next(a for a in eps if not a & 0x80)
            h = dev.open()
            if h.kernelDriverActive(setting.getNumber()): h.detachKernelDriver(setting.getNumber())
            h.claimInterface(setting.getNumber())
            return ctx, h, Ptp(h, setting.getNumber(), ep_in, ep_out)
    raise SystemExit('no FUJIFILM camera in PTP mode on USB: normal mode, USB = PC SHOOT, cable in, camera on')


def session(fn):
    ctx, h, ptp = open_camera()
    try:
        rc, _, _ = ptp.run(OP_OPEN, (1,))
        if rc not in (OK, 0x201e):                                   # 0x201e = session already open
            raise SystemExit(f'OpenSession refused: {rc:#06x}')
        try:
            return fn(ptp)
        finally:
            # give the dials back, like a power cycle: leaving the PC holding priority makes the ISO and shutter
            # dials look dead until the next power-off (observed 2026-11-06)
            try:
                ptp.run(OP_SET_PROP, (PRIORITY,), send=struct.pack('<H', PRIORITY_CAMERA))
            except Exception:
                pass
    finally:
        try:
            ptp.run(OP_CLOSE)
        finally:
            h.releaseInterface(ptp.iface); h.close(); ctx.close()


def settings_info(ptp):
    rc, _, info = ptp.run(OP_INFO, (0,))
    if rc == BUSY:      # the camera has priority (0xD207 = 1): hand it to the PC like X Acquire does, then ask again
        ptp.run(OP_SET_PROP, (PRIORITY,), send=struct.pack('<H', PRIORITY_PC))
        rc, _, info = ptp.run(OP_INFO, (0,))
    if rc != OK or len(info) < 12:
        raise SystemExit(f'the camera has no settings object (GetObjectInfo 0 -> {rc:#06x})')
    fmt, size = struct.unpack_from('<H', info, 4)[0], struct.unpack_from('<I', info, 8)[0]
    if fmt != SETTINGS_FORMAT:
        raise SystemExit(f'object 0 has format {fmt:#06x}, not the settings format {SETTINGS_FORMAT:#06x}; stopping')
    return size, info


def backup(args):
    def go(ptp):
        size, info = settings_info(ptp)
        rc, _, blob = ptp.run(OP_GET, (0,))
        if rc != OK or len(blob) != size:
            raise SystemExit(f'GetObject 0 -> {rc:#06x}, {len(blob)} of {size} bytes; nothing saved')
        OUT.mkdir(parents=True, exist_ok=True)
        stem = OUT / f'{datetime.datetime.now():%Y%m%dT%H%M%S}_settings'
        stem.with_suffix('.bin').write_bytes(blob); stem.with_suffix('.objinfo').write_bytes(info)
        stem.with_suffix('.bin').chmod(0o444)
        print(f'saved {len(blob)} bytes -> {stem.with_suffix(".bin")}  sha12 {hashlib.sha256(blob).hexdigest()[:12]}')
        return 0
    return session(go)


def restore(args):
    blob = Path(args.file).read_bytes()
    sha12 = hashlib.sha256(blob).hexdigest()[:12]
    if args.confirm.strip().lower() != sha12:
        raise SystemExit(f'--confirm {args.confirm} does not match {args.file} ({sha12}); nothing sent')

    def go(ptp):
        size, _ = settings_info(ptp)
        if size != len(blob):
            raise SystemExit(f'the camera expects {size} bytes, the file has {len(blob)}; nothing sent')
        objinfo = bytearray(OBJINFO_LEN)
        struct.pack_into('<H', objinfo, 4, SETTINGS_FORMAT)
        struct.pack_into('<I', objinfo, 8, len(blob))
        rc, _, _ = ptp.run(OP_SEND_INFO, (0, 0), send=bytes(objinfo))
        if rc != OK: raise SystemExit(f'SendObjectInfo refused: {rc:#06x}; settings unchanged')
        rc, _, _ = ptp.run(OP_SEND, send=blob)
        if rc != OK: raise SystemExit(f'SendObject refused: {rc:#06x}')
        print(f'restored {len(blob)} bytes of settings ({sha12})')
        return 0
    return session(go)


def set_exposure(iso, seconds=None):
    """Set ISO and shutter speed from the PC with the ISO and shutter dials on A: take priority (0xD207 = 2), switch to manual
    (0x500E = 1), then write 0x500D and 0x500F. gphoto2 cannot do this: its 'prioritymode' labels are inverted, so its USB
    choice leaves the camera in charge. Shutter speeds are an enumeration in microseconds; the nearest one is used."""
    ctx, h, ptp = open_camera()
    try:
        for ep in (ptp.ep_in, ptp.ep_out):
            try: h.clearHalt(ep)
            except Exception: pass
        ptp.run(OP_OPEN, (1,))
        steps = [(PRIORITY, struct.pack('<H', PRIORITY_PC)), (0x500E, struct.pack('<H', 1))]
        for prop, value in steps:
            ptp.run(OP_SET_PROP, (prop,), send=value)
        if seconds is not None:
            for _ in range(10):                                      # the list is empty for a moment after the switch to manual
                rc, _, desc = ptp.run(0x1014, (0x500D,))                 # GetDevicePropDesc: code, type, rw, default, current, form, count, values
                n = struct.unpack_from('<H', desc, 14)[0]
                if n: break
                time.sleep(0.3)
            if not n: raise RuntimeError('the camera offered no shutter speeds')
            speeds = struct.unpack_from(f'<{n}I', desc, 16)
            usec = min(speeds, key=lambda v: abs(v - seconds * 1e6))
            rc = ptp.run(OP_SET_PROP, (0x500D,), send=struct.pack('<I', usec))[0]
            if rc != OK: raise RuntimeError(f'shutter speed refused: {rc:#06x}')
        rc = ptp.run(OP_SET_PROP, (0x500F,), send=struct.pack('<I', iso))[0]
        if rc != OK: raise RuntimeError(f'ISO refused: {rc:#06x}')
    finally:
        try: ptp.run(OP_CLOSE)
        finally: h.releaseInterface(ptp.iface); h.close(); ctx.close()


def priority(args):
    """Read-only: who owns the exposure dials right now."""
    def go(ptp):
        rc, _, data = ptp.run(OP_GET_PROP, (PRIORITY,))
        if rc == BUSY:                       # camera holds priority; borrow it for one read (handed back on close)
            ptp.run(OP_SET_PROP, (PRIORITY,), send=struct.pack('<H', PRIORITY_PC))
            rc, _, data = ptp.run(OP_GET_PROP, (PRIORITY,))
        if rc != OK or len(data) < 2:
            raise SystemExit(f'GetDevicePropValue 0xD207 -> {rc:#06x}, {len(data)} bytes')
        v = struct.unpack_from('<H', data)[0]
        owner = {PRIORITY_CAMERA: 'camera dials', PRIORITY_PC: 'PC'}.get(v, 'unknown')
        print(f'priority 0xD207 = {v} ({owner})')
        return 0
    return session(go)


def clock(args):
    r = subprocess.run(['gphoto2', '--set-config', 'datetime=now'], capture_output=True, text=True, timeout=60)
    print((r.stdout + r.stderr).strip() or 'clock set to', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    return r.returncode


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('backup', help='read the menu settings (read-only) into camera/settings/')
    r = sub.add_parser('restore', help='write saved menu settings back'); r.add_argument('file')
    r.add_argument('--confirm', required=True, metavar='SHA12', help='first 12 hex of the file sha256')
    sub.add_parser('clock', help='set the camera clock to this PC time')
    sub.add_parser('priority', help='read-only: who owns the exposure dials (camera or PC)')
    a = p.parse_args(argv)
    return {'backup': backup, 'restore': restore, 'clock': clock, 'priority': priority}[a.cmd](a)


if __name__ == '__main__':
    sys.exit(main())
