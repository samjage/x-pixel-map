"""OFFLINE emulation, on the owner's own unpacked firmware (firmware.py).
Runs the camera's own per-shot defect-correction setup (stream2 FUN_0116a1e4) on a config image and records what it
hands to the PFP driver (FUN_0117a84c): mode, list address, count, weight-table pointer, x offset.
Real firmware code runs for the list accessors (stream1 FUN_00875310 / 875710 / 875904 / 8758dc) and the ISO-class
lookup; OS / hardware calls are stubbed and logged.
Usage: python pfp_setup_emu.py <image.bin> [<image2.bin> ...]
"""
import sys, struct
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED, UcError
from unicorn.arm_const import *

from .firmware import FW_DIR

S1, S1B = (FW_DIR / 'stream1_decoded.bin').read_bytes(), 0x780400
S2, S2B = (FW_DIR / 'stream2_decoded.bin').read_bytes(), 0xeff120
IMG_BASE = 0x41edd800
STACK, STOP = 0x03000000, 0x02fff000
P1, P2 = 0x02800000, 0x02810000          # fake param_1 / param_2 structs
PFP_DRIVER, SETUP = 0x0117a84c, 0x0116a1e4

STUBS = {   # address: (name, return value)
    0x011651c4: ('jig-shot kind', 0),         # 0 = normal shot (3 would skip correction)
    0x009211e0: ('mutex/notify', 0),
    0x0116a1cc: ('sub-pass enable', 1),
    0x011cc2c4: ('post-step', 0),            # makes the function return right after the PFP passes
    0x00042190: ('log string', 0),
}


def page(a): return a & ~0xfff


def make(img):
    mu = Uc(UC_ARCH_ARM, UC_MODE_ARM)
    mu.mem_map(0, 0x04000000)                                  # code, data, bss, stacks, fake structs
    mu.mem_write(S1B, S1); mu.mem_write(S2B, S2)
    lo = page(IMG_BASE); mu.mem_map(lo, page(IMG_BASE + len(img) + 0xfff) - lo + 0x1000)
    mu.mem_write(IMG_BASE, img)
    mu.mem_write(0x00c285bc, struct.pack('<I', IMG_BASE))       # _DAT_00c285bc = config image base
    return mu


def run(img, tv, sv, flags=0):
    mu = make(img); calls = []; log = []
    p1 = bytearray(0x4000)
    struct.pack_into('<I', p1, 0x670, tv & 0xffffffff)      # Tv in 1/100 EV (0 = 1 s, larger = shorter)
    struct.pack_into('<H', p1, 0x76a, sv)                    # Sv
    struct.pack_into('<I', p1, 0x49c, flags)
    mu.mem_write(P1, bytes(p1)); mu.mem_write(P2, bytes(0x100))

    def hook(uc, addr, size, _):
        if addr == STOP: uc.emu_stop(); return
        if addr in STUBS:
            name, ret = STUBS[addr]; log.append(name)
            uc.reg_write(UC_ARM_REG_R0, ret); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR)); return
        if addr == PFP_DRIVER:
            p = uc.reg_read(UC_ARM_REG_R1)
            w = struct.unpack('<8I', uc.mem_read(p, 32))
            calls.append({'mode': w[0], 'f1': w[1], 'xoff': w[2], 'f3': w[3], 'list_addr': w[4], 'count': w[5],
                          'f6': w[6], 'table': w[7]})
            uc.reg_write(UC_ARM_REG_R0, 1); uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR)); return
    mu.hook_add(UC_HOOK_CODE, hook)
    unm = []
    mu.hook_add(UC_HOOK_MEM_UNMAPPED, lambda uc, t, a, s, v, d: unm.append(hex(a)) or False)
    mu.reg_write(UC_ARM_REG_SP, STACK); mu.reg_write(UC_ARM_REG_LR, STOP)
    for r, v in ((UC_ARM_REG_R0, P1), (UC_ARM_REG_R1, P2), (UC_ARM_REG_R2, 0), (UC_ARM_REG_R3, 0)):
        mu.reg_write(r, v)
    try:
        mu.emu_start(SETUP, STOP, count=2_000_000)
        err = None
    except UcError as e:
        err = f'{e} at pc={mu.reg_read(UC_ARM_REG_PC):#x} unmapped={unm[:3]}'
    return calls, log, err, mu.reg_read(UC_ARM_REG_R0)


def describe(c):
    off = c['list_addr'] - IMG_BASE
    return f"mode={c['mode']} list=image+{off:#x} count={c['count']} xoff={c['xoff']:#x} table={c['table']:#x}"


if __name__ == '__main__':
    # Tv: 1/100 EV, 0 = 1 s. 1/180 s ~ +749, 4 s = -200. Sv grid spans the ISO-class table.
    cases = [('1/180 s', 749), ('1/4 s', 200), ('1 s', 0), ('4 s', -200)]
    svs = list(range(300, 1500, 100))
    imgs = sys.argv[1:]
    results = {}
    for path in imgs:
        img = open(path, 'rb').read()
        print('=====', path)
        for tname, tv in cases:
            for sv in svs:
                calls, log, err, r0 = run(img, tv, sv)
                key = (tname, sv)
                results.setdefault(key, []).append([describe(c) for c in calls])
                print(f'{tname:7s} Sv={sv:5d} ret={r0} err={err} passes={len(calls)}')
                for c in calls: print('     ', describe(c))
    if len(imgs) == 2:
        print('===== differences between the two images')
        nd = 0
        for k, (a, b) in results.items():
            if a != b:
                nd += 1; print(k); print('  A:', a); print('  B:', b)
        print('cases differing:', nd, 'of', len(results))
