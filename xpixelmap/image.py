"""Offline checks on config images (stdlib only)."""
import hashlib
from .constants import (IMAGE_SIZE, MAGIC_OFF, MAGIC, LIST_RELOAD_FLAG, USB_DEBUG, TRIGGER, APP80_AREA,
                        APP80_SAVE_BYTES, LISTS, LIST_REGION_END, COUNT_FIELDS, LENS_REGION,
                        AF_WEIGHT_TABLES, AF_WEIGHT_CLASSES, RECC_GATE, RECC_GATE_ON)


class ImageError(Exception):
    pass


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def load_image(path):
    with open(path, 'rb') as f:
        d = f.read(IMAGE_SIZE + 1)
    if len(d) != IMAGE_SIZE:
        raise ImageError(f'{path}: {len(d)} bytes, expected {IMAGE_SIZE} (0x{IMAGE_SIZE:x})')
    return d


def u24(d, o):
    return int.from_bytes(d[o:o + 3], 'little')


def asize(n): return ((n * 34 + 63) & ~63) >> 3
def bsize(n): return (n * 34 + 7) >> 3


def list_layout(d):
    """[(name, base, A, B, span_bytes, limit)] where limit is the start of the next region."""
    bases = sorted(b for _, b, _, _ in LISTS) + [LIST_REGION_END]
    out = []
    for name, base, ca, cb in LISTS:
        A, B = u24(d, ca), u24(d, cb)
        out.append((name, base, A, B, asize(A) + bsize(B), bases[bases.index(base) + 1]))
    return out


def first_diff(a, b, start=0, end=None):
    end = len(a) if end is None else end
    if a[start:end] == b[start:end]:
        return None
    lo, hi = start, end           # binary search for the first differing byte
    while hi - lo > 64:
        mid = (lo + hi) // 2
        if a[lo:mid] != b[lo:mid]: hi = mid
        else: lo = mid
    for i in range(lo, hi):
        if a[i] != b[i]: return i
    return None


def allowed_ranges(img, base):
    """Byte ranges an edit may change: each list's span (max of old/new), the count fields, the AF weight bytes and the raw correction gate."""
    lb, li = {n: s for n, _, _, _, s, _ in list_layout(base)}, list_layout(img)
    r = [(b, min(IMAGE_SIZE, b + max(s, lb[n]))) for n, b, _, _, s, _ in li] + [COUNT_FIELDS]
    r += [(t + c, t + c + 1) for t in AF_WEIGHT_TABLES for c in AF_WEIGHT_CLASSES]
    r += [RECC_GATE]
    return sorted(r)


def records(buf, n):
    """n 34-bit LSB-first records from buf."""
    out = []
    for i in range(n):
        bit = 34 * i
        v = int.from_bytes(buf[bit >> 3:(bit >> 3) + 6], 'little')
        out.append((v >> (bit & 7)) & ((1 << 34) - 1))
    return out


def record_problems(d, name, b, A, B):
    """Factory invariants (NOTES.md 'Editor review'): A x 4..3673, B rel x 2..1271, y 4..3291, bits 12/16/29 clear."""
    for part, recs, xlo, xhi in (('A', records(d[b:b + asize(A)], A), 4, 3673),
                                 ('B', records(d[b + asize(A):b + asize(A) + bsize(B)], B), 2, 1271)):
        for i, r in enumerate(recs):
            x, y = r & 0x1fff, (r >> 17) & 0x1fff
            if not (xlo <= x <= xhi and 4 <= y <= 3291) or r & ((1 << 12) | (1 << 16) | (1 << 29)):
                return f'{name} {part}[{i}] = {r:09x} is not a valid record (x={x} y={y}); count field corrupt?'
    return None


def presave_checks(img, base):
    """Return a list of failure strings (empty = all passed). `base` = the image the camera currently holds."""
    bad = []
    if len(img) != IMAGE_SIZE or len(base) != IMAGE_SIZE:
        return [f'size {len(img)} / base {len(base)} != {IMAGE_SIZE}']
    if img[MAGIC_OFF:MAGIC_OFF + 2] != MAGIC:
        bad.append(f'magic at {MAGIC_OFF:#x} is {img[MAGIC_OFF:MAGIC_OFF + 2].hex()}, need 55aa')
    if img[LIST_RELOAD_FLAG] != 0:
        bad.append(f'byte {LIST_RELOAD_FLAG:#x} = {img[LIST_RELOAD_FLAG]:#x}, need 0')
    if img[USB_DEBUG] != 0:
        bad.append(f'byte {USB_DEBUG:#x} (USB debug) = {img[USB_DEBUG]:#x}, need 0')
    if img[TRIGGER[0]:TRIGGER[1]] != base[TRIGGER[0]:TRIGGER[1]]:
        bad.append(f'trigger bytes 0x80..0x83 differ from base ({img[0x80:0x84].hex()} vs {base[0x80:0x84].hex()})')
    if img[APP80_AREA[0]:APP80_AREA[1]] != APP80_SAVE_BYTES:
        bad.append('0x80..0xa0 is not what the save command leaves there (22 ff 00..): saved flash would differ')
    layout = list_layout(img)
    overflow = False
    for name, b, A, B, span, limit in layout:      # ALL six lists, counts taken from the image being written
        if b + span > limit:
            bad.append(f'{name}: counts A={A} B={B} give span {span:#x} at {b:#x}, past its limit {limit:#x}')
            overflow = True
    if img[LENS_REGION[0]:LENS_REGION[1]] != base[LENS_REGION[0]:LENS_REGION[1]]:
        bad.append(f'lens block {LENS_REGION[0]:#x}..{LENS_REGION[1] - 1:#x} differs from base')
    for c in AF_WEIGHT_CLASSES:
        w, w0 = [img[t + c] for t in AF_WEIGHT_TABLES], [base[t + c] for t in AF_WEIGHT_TABLES]
        # allowed per class: factory neighbours with the own byte lowered (blend-strength reading), or any 1..63
        # neighbours with the factory total 68 (sum-preserving reading); own never raised above factory 16
        # allowed per class: factory n1..n3 (10,25,9) with own <= 16 and n4 (cfg 0x113b6, the other own-share candidate, 8/32) <= 8,
        # or any 1..63 neighbours with the factory total 68 (sum-preserving reading); own never raised above factory 16
        own_only = list(w[1:4]) == [10, 25, 9] and 0 <= w[4] <= 8
        if not ((own_only and w[0] <= 16) or (sum(w) == 68 and w[0] <= 16 and all(1 <= x <= 63 for x in w[1:]))):
            bad.append(f'AF weights class {c}: {w} (base {w0}): need factory (10,25,9) + n4 <= 8 with own <= 16, or total 68 with neighbours 1..63')
    for o in range(*RECC_GATE):
        if img[o] not in (base[o], RECC_GATE_ON):
            bad.append(f'raw correction gate {o:#x}: {img[o]}, need {base[o]} or {RECC_GATE_ON}')
    if overflow:
        return bad                                  # spans are meaningless; do not evaluate further
    for name, b, A, B, span, limit in layout:
        va = int.from_bytes(img[b:b + asize(A)], 'little')
        vb = int.from_bytes(img[b + asize(A):b + span], 'little')
        if va >> (34 * A) or vb >> (34 * B):
            bad.append(f'{name}: nonzero padding bits')
        p = record_problems(img, name, b, A, B)
        if p:
            bad.append(p)
    # outside the list spans and count fields nothing may differ from the base
    pos = 0
    for lo, hi in allowed_ranges(img, base) + [(IMAGE_SIZE, IMAGE_SIZE)]:
        i = first_diff(img, base, pos, lo) if lo > pos else None
        if i is not None:
            bad.append(f'byte {i:#x} differs from base outside list spans/count fields')
            break
        pos = max(pos, hi)
    # inside a list's allowed range, bytes beyond its new span must be erased filler
    lb = {n: s for n, _, _, _, s, _ in list_layout(base)}
    for name, b, A, B, span, limit in list_layout(img):
        tail = img[b + span:b + max(span, lb[name])]
        if any(x != 0xff for x in tail):
            bad.append(f'{name}: bytes after the list are not 0xff filler')
    return bad


def summary(img, base):
    lines = []
    for (n, b, A, B, s, _), (_, _, A0, B0, s0, _) in zip(list_layout(img), list_layout(base)):
        tag = '' if (A, B) == (A0, B0) else '   <- changed'
        lines.append(f'  {n} @{b:#08x}: A {A0}->{A}  B {B0}->{B}  span {s0:#x}->{s:#x}{tag}')
    ndiff = sum(1 for lo, hi in allowed_ranges(img, base) for i in range(lo, hi) if img[i] != base[i])
    lines.append(f'  bytes differing from base: {ndiff}')
    return '\n'.join(lines)
