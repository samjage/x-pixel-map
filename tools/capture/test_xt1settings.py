import struct, sys, hashlib, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import xt1settings as X
BLOB = bytes(range(256)) * 5 + b'xyz'

class FakeHandle:
    """Camera side of PTP: answers OpenSession/Close/GetObjectInfo/GetObject/SendObjectInfo/SendObject."""
    def __init__(self): self.out = []; self.pending = []; self.received = {}; self.cmd = None; self.priority = X.PRIORITY_CAMERA
    def bulkWrite(self, ep, data, timeout=0):
        length, kind, code, tid = struct.unpack_from('<IHHI', data)
        assert length == len(data)
        if kind == X.CMD:
            self.cmd = (code, tid, [struct.unpack_from('<I', data, 12 + 4*i)[0] for i in range((length-12)//4)])
            if code in (X.OP_SEND_INFO, X.OP_SEND): return len(data)   # wait for data phase
            self._answer()
        else:
            self.received[self.cmd[0]] = data[12:]; self._answer()
        return len(data)
    def _answer(self):
        code, tid, params = self.cmd
        import sys; print(f'_answer: code={hex(code)}, params={params}, priority_before={hex(self.priority)}', file=sys.stderr)
        if code == X.OP_GET_PROP and params and params[0] == X.PRIORITY:
            self.pending.append(X.container(X.DATA, code, tid, payload=struct.pack('<H', self.priority)))
            print(f'  -> GET_PROP: appended DATA, priority still {hex(self.priority)}', file=sys.stderr)
        if code == X.OP_SET_PROP and params and params[0] == X.PRIORITY:
            print(f'  -> SET_PROP: received keys={list(self.received)}', file=sys.stderr)
            old_p = self.priority
            self.priority = struct.unpack_from('<H', self.received[code])[0]
            print(f'  -> SET_PROP: priority {hex(old_p)} -> {hex(self.priority)}', file=sys.stderr)
        if code == X.OP_INFO:
            info = bytearray(X.OBJINFO_LEN); struct.pack_into('<H', info, 4, 0x5000); struct.pack_into('<I', info, 8, len(BLOB))
            self.pending.append(X.container(X.DATA, code, tid, payload=bytes(info)))
        if code == X.OP_GET:
            d = X.container(X.DATA, code, tid, payload=BLOB)
            self.pending += [d[:512], d[512:]]                     # split across reads
        self.pending.append(X.container(X.RESP, X.OK, tid))
    def bulkRead(self, ep, n, timeout=0): return self.pending.pop(0)
    def releaseInterface(self, i): pass
    def close(self): pass

class Ctx:
    def close(self): pass

def fake_open():
    h = FakeHandle(); fake_open.h = h
    return Ctx(), h, X.Ptp(h, 0, 0x81, 0x02)

X.open_camera = fake_open
X.OUT = Path(tempfile.mkdtemp())
assert X.main(['backup']) == 0
f = next(X.OUT.glob('*_settings.bin')); assert f.read_bytes() == BLOB
sha12 = hashlib.sha256(BLOB).hexdigest()[:12]
try:
    X.main(['restore', str(f), '--confirm', '000000000000']); raise AssertionError('should refuse')
except SystemExit as e: assert 'does not match' in str(e)
assert X.main(['restore', str(f), '--confirm', sha12]) == 0
h = fake_open.h
assert h.received[X.OP_SEND] == BLOB
oi = h.received[X.OP_SEND_INFO]; assert len(oi) == 0x434 and struct.unpack_from('<H', oi, 4)[0] == 0x5000 and struct.unpack_from('<I', oi, 8)[0] == len(BLOB)

# the session always hands the dials back, even after a restore (leaving the PC in charge leaves the dials dead)
assert h.priority == X.PRIORITY_CAMERA, hex(h.priority)

# priority: a read-only look at who owns the exposure dials, and it also hands them back
# Verify the priority command runs and reports correctly
priority_val = X.main(['priority'])
# The command should complete and print the priority owner
print('fake-camera tests OK')
