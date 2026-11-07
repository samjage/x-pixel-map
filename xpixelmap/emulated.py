"""Emulated camera: the X-T1's own firmware code in unicorn (runs only inside the xt1-emu:burner container).

Real firmware code used:
  write:  stream2 FUN_010e92f8 (service cfgdata write, mb=1)      read: stream2 FUN_010e93a4 (mb=1)
  save:   app80 params write (0x81) + trigger write (0x80) through FUN_010e92f8, then the 0x22 command handler
          FUN_010df4fc -> FUN_008758bc -> FUN_00880c90 (whole image to flash) -> FUN_010dccd0 (status to cfg 0x81)
  boot:   FUN_00874c24 (config load, magic check, defaults) + FUN_00875334 (list loader)
Stubbed: raw FTL block read/write (a dict of 128 KB logical blocks is the flash), RTOS message post FUN_011e92f0,
notify FUN_00848464 / FUN_010a380c, log/semaphore calls (as in emu/roundtrip_emu.py).
Modelled, not emulated: [0x3e0000, 0x3f7e00) (lens block) is copied from flash at boot because its loader is not
part of the emulated boot path; the USB layer itself; the app80 dispatcher (the 0x22 handler is called directly when
the trigger write posts message 0x1707).
"""
import struct
import sys
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UcError
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, \
    UC_ARM_REG_LR, UC_ARM_REG_PC

from .constants import PROJECT, IMAGE_SIZE, CHUNK, SAVE_CMD, SAVE_PARAM
from .transport import Transport, TransportError, TransportTimeout

from . import pfp_setup_emu as PS   # loads the decoded firmware streams
from .firmware import FW_DIR

IMG = 0x41edd800
STACK, STOP, BUF = 0x03000000, 0x02fff000, 0x02900000
BLK, NBLK = 0x20000, 33
FN_WRITE, FN_READ, FN_BOOT, FN_LISTS = 0x010e92f8, 0x010e93a4, 0x00874c24, 0x00875334
FN_SAVE_HANDLER, SAVE_DESC = 0x010df4fc, 0x013c7028
FTL_WRITE, FTL_READ, MSG_POST = 0x0087f94c, 0x0087f9f0, 0x011e92f0
RET0 = (0x00908b84, 0x00908c1c, 0x00042190, 0x0004263c, 0x00042634, 0x00848464, 0x010a380c)
RAW_READ = 0x0087fa88          # raw NAND block read used by the factory-default fallback (FUN_0088076c)
MODELLED = [(0x3e0000, 0x3f7e00)]
LBN_BASE = 0x29
_RAW = []


def _rawflash():
    if not _RAW:
        _RAW.append((FW_DIR / 'flash.bin').read_bytes())
    return _RAW[0]


class PowerLost(Exception):
    pass


class Fault:
    """kind: 'fail' | 'timeout' | 'flip' | 'power_loss' | 'ftl_error' | 'interrupt'.
    op: transport op the fault applies to ('read', 'write', 'save', 'info', 'config_size').
    nth: fire on the nth call of that op (1-based) counted from when the fault was armed.
    blocks: for power_loss, flash blocks written before power is lost. byte: index to flip for 'flip'."""

    def __init__(self, kind, op=None, nth=1, blocks=0, byte=0):
        self.kind, self.op, self.nth, self.blocks, self.byte = kind, op, nth, blocks, byte
        self.seen, self.fired = 0, False


class EmulatedCamera:
    """The device: flash (dict of blocks), RAM (unicorn), fault injection, reboot(). Not a Transport."""

    def __init__(self, flash_image=None, nand=None, product='X-T1'):
        self.nand = dict(nand) if nand is not None else {}
        if flash_image is not None:
            img = bytes(flash_image) + b'\xff' * (NBLK * BLK - len(flash_image))
            for i in range(NBLK):
                self.nand[LBN_BASE + i] = img[i * BLK:(i + 1) * BLK]
        self.product = product
        self.faults = []
        self.calls = {}           # op -> count (all ops ever requested through the transport)
        self.save_count = 0       # how many times the firmware save path wrote flash
        self.messages = []
        self.dead = False
        self.reboot()

    # ---- device lifecycle
    def reboot(self):
        """Battery out/in + power on into service mode: fresh RAM, firmware boot loads config from flash."""
        self.dead = False
        self.raw_reads = []
        self._power_loss_blocks = None
        self._ftl_error = False
        self.mu = mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        mu.mem_map(0, 0x04000000)
        mu.mem_write(PS.S1B, PS.S1); mu.mem_write(PS.S2B, PS.S2)
        lo = IMG & ~0xfff; mu.mem_map(lo, 0x430000)
        mu.mem_write(IMG, b'\xff' * IMAGE_SIZE)
        mu.mem_write(0x00c285bc, struct.pack('<I', IMG))
        mu.mem_write(0x00af5f78, struct.pack('<I', 1))
        hooks = {FTL_WRITE: self._ftl_write, FTL_READ: self._ftl_read, MSG_POST: self._msg_post,
                 RAW_READ: self._raw_read}
        for a in RET0: hooks[a] = self._ret0
        for a, (name, ret) in PS.STUBS.items():
            hooks.setdefault(a, (lambda r: (lambda uc, *_: self._ret(uc, r)))(ret))
        for a, h in hooks.items():
            mu.hook_add(UC_HOOK_CODE, (lambda f: (lambda uc, addr, size, ud: f(uc)))(h), begin=a, end=a)
        self._call(FN_BOOT); self._call(FN_LISTS)
        flash = self.flash_image()
        for a, b in MODELLED:
            mu.mem_write(IMG + a, flash[a:b])

    def flash_image(self):
        return b''.join(self.nand.get(LBN_BASE + i, b'\xff' * BLK) for i in range(NBLK))[:IMAGE_SIZE]

    def ram(self, off=0, n=IMAGE_SIZE):
        return bytes(self.mu.mem_read(IMG + off, n))

    # ---- firmware stubs
    @staticmethod
    def _ret(uc, v):
        uc.reg_write(UC_ARM_REG_R0, v & 0xffffffff)
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

    def _ret0(self, uc):
        self._ret(uc, 0)

    def _msg_post(self, uc):
        self.messages.append(tuple(uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)))
        self._ret(uc, 0)

    def _ftl_write(self, uc):
        lbn, n, buf = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        if self._ftl_error:                      # one-shot write error: firmware save path ignores the result
            self._ftl_error = False
            return self._ret(uc, -0xff)
        k = n if self._power_loss_blocks is None else min(n, self._power_loss_blocks)
        for i in range(k):                       # each 128 KB block is crash-safe in the FTL (whole or nothing)
            self.nand[lbn + i] = bytes(uc.mem_read(buf + i * BLK, BLK))
        if self._power_loss_blocks is not None:
            self.dead = True
            uc.emu_stop()
            return
        self.save_count += 1
        self._ret(uc, 0)

    def _ftl_read(self, uc):
        lbn, n, buf = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        for i in range(n):
            uc.mem_write(buf + i * BLK, self.nand.get(lbn + i, b'\xff' * BLK))
        self._ret(uc, 0)

    def _raw_read(self, uc):
        """Raw NAND read (factory-default copy, absolute blocks 7..9), served from the firmware's flash image."""
        blk, n, buf = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
        for i in range(n):
            uc.mem_write(buf + i * BLK, _rawflash()[(blk + i) * BLK:(blk + i + 1) * BLK].ljust(BLK, b'\xff'))
        self.raw_reads.append((blk, n))
        self._ret(uc, 0)

    def _call(self, fn, *args):
        mu = self.mu
        mu.reg_write(UC_ARM_REG_SP, STACK); mu.reg_write(UC_ARM_REG_LR, STOP)
        for r, a in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
            mu.reg_write(r, a & 0xffffffff)
        try:
            mu.emu_start(fn, STOP, count=50_000_000)
        except UcError as e:
            raise TransportError(f'emulated firmware fault in {fn:#x}: {e} pc={mu.reg_read(UC_ARM_REG_PC):#x}')
        if self.dead:
            raise PowerLost('power lost')
        return mu.reg_read(UC_ARM_REG_R0)

    # ---- fault handling (called by the transport for every op)
    def _fault(self, op):
        self.calls[op] = self.calls.get(op, 0) + 1
        if self.dead:
            raise TransportTimeout(f'{op}: no response (camera has no power / hung)')
        for f in self.faults:
            if f.fired or (f.op is not None and f.op != op):
                continue
            f.seen += 1
            if f.seen != f.nth:
                continue
            f.fired = True
            if f.kind == 'fail': raise TransportError(f'injected USB error on {op} #{f.nth}')
            if f.kind == 'timeout': raise TransportTimeout(f'injected timeout on {op} #{f.nth}')
            if f.kind == 'interrupt': raise KeyboardInterrupt(f'injected interrupt on {op} #{f.nth}')
            if f.kind == 'power_loss': self._power_loss_blocks = f.blocks
            if f.kind == 'ftl_error': self._ftl_error = True
            if f.kind == 'flip': return f
        return None


class EmulatedTransport(Transport):
    def __init__(self, camera):
        self._cam = camera

    def _info(self):
        self._cam._fault('info')
        return {'Product Name': self._cam.product, 'FW Version?': '5.52 (emulated)', 'Framework': 'emulator'}

    def _config_size(self):
        self._cam._fault('config_size')
        return IMAGE_SIZE, CHUNK

    def _read(self, off, n):
        flip = self._cam._fault('read')
        cam = self._cam
        st = cam._call(FN_READ, 1, off, n, BUF)
        if st != 0:
            raise TransportError(f'read at {off:#x} returned status {st:#x}')
        data = bytearray(cam.mu.mem_read(BUF, n))
        if flip: data[flip.byte % n] ^= 0x01          # corrupted on the way to the host
        return bytes(data)

    def _write(self, off, data):
        flip = self._cam._fault('write')
        cam = self._cam
        data = bytearray(data)
        if flip: data[flip.byte % len(data)] ^= 0x01  # corrupted on the way to the camera
        cam.mu.mem_write(BUF, bytes(data))
        st = cam._call(FN_WRITE, 1, off, len(data), BUF)
        if st != 0:
            raise TransportError(f'write at {off:#x} returned status {st:#x}')

    def _save(self):
        """Same wire sequence as ffjlib.app80_request(0x22, [0xff]) + app80_response()."""
        self._cam._fault('save')
        cam = self._cam
        try:
            cam.mu.mem_write(BUF, bytes([SAVE_PARAM]) + bytes(31))
            if cam._call(FN_WRITE, 1, 0x81, 32, BUF) != 0:
                raise TransportError('app80 parameter write failed')
            cam.messages.clear()
            cam.mu.mem_write(BUF, bytes([SAVE_CMD, SAVE_PARAM]) + bytes(31))   # ffjlib writes all 33 bytes at 0x80
            if cam._call(FN_WRITE, 1, 0x80, 33, BUF) != 1:
                raise TransportError('app80 trigger rejected')
            if not any(m[1] == 0x1707 for m in cam.messages):
                raise TransportError('trigger did not post message 0x1707')
            cam._call(FN_SAVE_HANDLER, SAVE_DESC)          # the service task runs the 0x22 handler
        except PowerLost:
            raise TransportTimeout('save: no response (power lost during flash write)')
        st = cam._call(FN_READ, 1, 0x80, 33, BUF)
        if st != 0:
            raise TransportError('app80 response read failed')
        return bytes(cam.mu.mem_read(BUF, 33))


def per_shot_counts(ram):
    """Run the camera's per-shot correction setup (FUN_0116a1e4) on a RAM image; return list counts it uses."""
    out = {}
    for name, tv in (('1/180 s', 749), ('4 s', -200)):
        calls, log, err, r0 = PS.run(bytes(ram), tv, 700)
        if err:
            raise TransportError(f'per-shot setup failed: {err}')
        out[name] = [(c['mode'], c['list_addr'] - IMG, c['count']) for c in calls]
    return out
