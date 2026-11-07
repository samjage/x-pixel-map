"""OFFLINE ONLY. Add pixels to the correction lists of a cfgdata image and write the result to a NEW file.
Never opens the camera and never overwrites its input (refuses if output == input or the backup).

Usage: python3 -I edit_lists.py [--partners=list2|both] <in.bin> <out.bin> <raf_x,raf_y> [<raf_x,raf_y> ...]
Pixels are given in RAF coordinates (list x = raf_x - 4). Each pixel is added to list0 (corrected before the RAF
is written) and list2 (its superset), A part and/or B part as the x range requires. --partners also adds each
pixel's left neighbour (x-1, y) to list2 (or both lists), like the 9/23/2025 service repair.

Layout facts used (see NOTES.md): 34-bit LSB-first records; A part first, padded to a 64-bit multiple, then the B
part packed directly after; counts are 24-bit LE at the config offsets in LISTS; B x is relative to 3660.
Order and aux follow the factory rules in NOTES.md 'Fuji order and pair rule' (helper/fuji_rebuild_test.py
rebuilds the 9/23 repair byte for byte). Pixels within 2 px of an existing entry, and neighbour patterns the factory
data does not cover, are refused. The hardware's tolerance for order is UNVERIFIED.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from record_model import predict_cls, predict_flag, build_aux_table, predict_aux, pack, REG

BACKUP = os.environ.get('XPIXELMAP_FACTORY_BACKUP') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'camera', 'cfgdata_dump_BACKUP_original.bin')
BSTART = 3660     # B part x is relative to this (u16 at config 0x2906)
OVERLAP = 3662    # list x >= this is also stored in the B part
M34 = (1 << 34) - 1
# name -> (list base offset, config offset of A count, config offset of B count)
LISTS = {'list0': (0x76800, 0x28e0, 0x28e3), 'list2': (0x11c840, 0x28e6, 0x28e9),
         'list1': (0x3b4940, 0x28f2, 0x28f5), 'list3': (0x3b8ba8, 0x28f8, 0x28fb)}
MAIN, AFL = ('list0', 'list2'), ('list1', 'list3')
# Phase-detect (AF) pixels: every list1/list3 entry lies on this lattice inside AF_BOX, and list0 never holds one
# (0 of 594 entries in the box; 33 expected by chance). Defects there go into list1 (short) + list3 (long, superset).
AF_BOX = (1485, 3432, 1001, 2293)


def is_af_pixel(x, y):
    return AF_BOX[0] <= x <= AF_BOX[1] and AF_BOX[2] <= y <= AF_BOX[3] and x % 3 == 0 and y % 12 in (1, 5)


def route(x, y):
    """Lists a list-frame pixel goes into. Refuses lattice pixels just outside the box (AF extent not known there)."""
    if is_af_pixel(x, y): return AFL
    if x % 3 == 0 and y % 12 in (1, 5) and REG[0] <= x <= REG[1] and REG[2] <= y <= REG[3]:
        raise SystemExit(f'({x + 4},{y}) is on the AF-pixel lattice near the AF area but outside the known AF box; refusing')
    return MAIN


def asize(n): return ((n * 34 + 63) & ~63) >> 3
def bsize(n): return (n * 34 + 7) >> 3
def u24(d, o): return int.from_bytes(d[o:o + 3], 'little')


def unpack(buf, n):
    v = int.from_bytes(buf, 'little')
    return [(v >> (34 * i)) & M34 for i in range(n)]


def packrecs(recs):
    v = 0
    for i, r in enumerate(recs): v |= r << (34 * i)
    return v


def read_list(d, name):
    base, ca, cb = LISTS[name]
    A, B = u24(d, ca), u24(d, cb)
    a = unpack(d[base:base + asize(A)], A)
    b = unpack(d[base + asize(A):base + asize(A) + bsize(B)], B)
    return a, b


def span(d, name):
    base, ca, cb = LISTS[name]
    A, B = u24(d, ca), u24(d, cb)
    return base, asize(A) + bsize(B)


def write_list(d, name, a, b):
    """Return a new image with list `name` rewritten from record lists a, b."""
    base, ca, cb = LISTS[name]
    old = span(d, name)[1]
    new = packrecs(a).to_bytes(asize(len(a)), 'little') + (packrecs(b).to_bytes(bsize(len(b)), 'little') if b else b'')
    out = bytearray(d)
    if len(new) > old:   # growing: the grown region must be erased (0xff) filler, nothing else
        grow = d[base + old:base + len(new)]
        if any(x != 0xff for x in grow): raise SystemExit('list would overwrite non-filler bytes')
    out[base:base + len(new)] = new
    if len(new) < old: out[base + len(new):base + old] = b'\xff' * (old - len(new))
    out[ca:ca + 3] = len(a).to_bytes(3, 'little')
    out[cb:cb + 3] = len(b).to_bytes(3, 'little')
    return bytes(out)


def xof(r): return r & 0x1fff
def yof(r): return (r >> 17) & 0x1fff


# Factory ordering (fitted on all four list parts, see NOTES.md 'Fuji order and pair rule'): records sort by
# (group key, own key). own key = (x, y, x), except pixels at (x%6, y%12) in AF_SHIFT inside SORT_REG sort as x-1
# (ties then go by real x). group = cluster joined by 8-neighbours and coded same-colour neighbours; group key =
# smallest own key in it, so a pair (x-1,y),(x,y) is stored together at the position of (x-1,y).
AF_SHIFT = {(1, 0), (1, 1), (1, 2), (1, 4), (1, 5), (1, 6), (4, 0), (4, 1), (4, 2), (4, 4), (4, 5), (4, 6)}
SORT_REG = (1482, 3454, 990, 2310)   # fitted: factory order then has 0 / 0 / 4 / 0 inversions (list0 A, B, list2 A, B)
N8 = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (dx, dy) != (0, 0)]
W5 = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if (dx, dy) != (0, 0)]


def own_key(x, y):
    s = 1 if SORT_REG[0] <= x <= SORT_REG[1] and SORT_REG[2] <= y <= SORT_REG[3] and (x % 6, y % 12) in AF_SHIFT else 0
    return (x - s, y, x)


def nb_code(table, x, y, o):
    """aux code a listed neighbour at offset o gives pixel (x, y): None if never observed in the factory data."""
    return table.nb.get((predict_cls(x, y), predict_flag(x, y)), {}).get(o)


def group_key(x, y, listed, table):
    seen = {(x, y)}; st = [(x, y)]
    while st:
        qx, qy = st.pop()
        for o in W5:
            n = (qx + o[0], qy + o[1])
            if n in listed and n not in seen and (o in N8 or nb_code(table, qx, qy, o)):
                seen.add(n); st.append(n)
    return (min(own_key(*q) for q in seen), own_key(x, y))


def insert_sorted(recs, rec, rel, listed, table):
    """Insert rec after the last record whose factory sort key is <= its key (rel = BSTART for B parts)."""
    full = set(listed) | {(xof(r) + rel, yof(r)) for r in recs} | {(xof(rec) + rel, yof(rec))}
    k = group_key(xof(rec) + rel, yof(rec), full, table); pos = 0
    for i, r in enumerate(recs):
        if group_key(xof(r) + rel, yof(r), full, table) <= k: pos = i + 1
    return recs[:pos] + [rec] + recs[pos:]


def fuji_aux(x, y, table, listed):
    """aux for list-frame pixel (x, y) given the final set of listed pixels in its list. Outside the AF region aux
    names the one listed same-colour neighbour the correction must skip (codes 1-4 per colour, table.nb, learned
    from every factory record with a single listed neighbour within 2 px; no exceptions). Refuses what the factory
    data does not cover."""
    base = predict_aux(x, y, table)
    codes = []
    for o in W5:
        if (x + o[0], y + o[1]) not in listed: continue
        c = nb_code(table, x, y, o)
        if c is None: raise SystemExit(f'({x + 4},{y}) listed neighbour at {o} never seen in factory data; refusing')
        if c: codes.append(c)
    if not codes: return base
    if len(codes) > 1: raise SystemExit(f'({x + 4},{y}) has {len(codes)} coded neighbours; not modelled, refusing')
    if base: raise SystemExit(f'({x + 4},{y}) coded neighbour inside the AF region (AF code {base}); not modelled, refusing')
    return codes[0]


def make_record(x, y, table, listed=frozenset()):
    return pack(x, y, predict_cls(x, y), fuji_aux(x, y, table, listed), predict_flag(x, y))


def expected_records(x, y, table, listed=frozenset()):
    """(part, raw34) pairs the editor writes for list-frame pixel (x, y); listed = final listed set of its list."""
    rec = make_record(x, y, table, listed); out = []
    if x <= 3673: out.append(('A', rec))
    if x >= OVERLAP: out.append(('B', (rec & ~0x1fff) | (x - BSTART)))
    return out


def listed_xy(a, b):
    return {(xof(r), yof(r)) for r in a} | {(xof(r) + BSTART, yof(r)) for r in b}


class AuxTables(dict):
    """AF-region aux table (the dict) plus .nb: (cls, flag) -> {neighbour offset: aux}."""


def neighbour_table(d):
    """From list0+list2 outside the AF region: records with exactly one listed neighbour within 2 px.
    Keeps only (cls, flag, offset) keys whose aux never varies."""
    import collections
    c = collections.defaultdict(collections.Counter)
    for name in MAIN:
        a, b = read_list(d, name)
        rec = {(xof(r), yof(r)): r for r in a}
        for r in b: rec.setdefault((xof(r) + BSTART, yof(r)), r)
        for (x, y), r in rec.items():
            if REG[0] <= x <= REG[1] and REG[2] <= y <= REG[3]: continue
            nb = [o for o in W5 if (x + o[0], y + o[1]) in rec]
            if len(nb) == 1: c[((r >> 13) & 15, r >> 33, nb[0])][(r >> 30) & 7] += 1
    out = {}
    for (cls, flag, o), v in c.items():
        if len(v) == 1: out.setdefault((cls, flag), {})[o] = next(iter(v))
    return out


def aux_table(d):
    a2, _ = read_list(d, 'list2')
    t = AuxTables(build_aux_table([(xof(r), yof(r), (r >> 30) & 7) for r in a2]))
    t.nb = neighbour_table(d)
    return t


def fuji_partners(pixels_raf):
    """Left neighbours (x-1, y): the 9/23/2025 service repair added one for 29 of 30 pixels in list2, 11 in list0."""
    return [(rx - 1, ry) for rx, ry in pixels_raf]


def wanted(pixels_raf, extra, name):
    """List-frame pixels requested for list `name`: each of pixels_raf goes into the lists route() gives it;
    extra[name] goes into that list only (main lists only)."""
    out = []
    for rx, ry in pixels_raf:
        if name in route(rx - 4, ry) and (rx - 4, ry) not in out: out.append((rx - 4, ry))
    for rx, ry in (extra or {}).get(name, ()):
        if name in AFL or is_af_pixel(rx - 4, ry): raise SystemExit(f'extra pixel ({rx},{ry}) for {name}: AF pixels take no partners')
        if (rx - 4, ry) not in out: out.append((rx - 4, ry))
    return out


def af_record(x, y):
    """list1/list3 record: all 254 factory AF entries but 2 have cls, aux and flag 0."""
    return pack(x, y, 0, 0, 0)


def add_pixels(d, pixels_raf, extra=None):
    """Return (new image, list of (name, part, record)) for the additions.
    Ordinary pixels go into list0 + list2, AF-lattice pixels into list1 + list3 (route());
    extra = {'list0': [...], 'list2': [...]} adds pixels to one main list only."""
    table = aux_table(d)
    out, log = d, []
    for name in MAIN + AFL:
        a, b = read_list(out, name)
        have = listed_xy(a, b)
        want = []
        for x, y in wanted(pixels_raf, extra, name):
            if not (4 <= x <= 4931 and 4 <= y <= 3291): raise SystemExit(f'({x + 4},{y}) outside list range')
            if (x, y) in have: print(f'  {name}: ({x + 4},{y}) already listed, skipped'); continue
            want.append((x, y))
        # a new pixel within 2 px of an existing entry of the same list could change that entry's aux or group;
        # existing records are never rewritten, so refuse. New pixels near each other (pairs) are fine, and so is
        # proximity across main and AF lists (factory: (3264,1717) in list0/list2 next to (3265,1717) in list3).
        for x, y in want:
            if any((x + dx, y + dy) in have for dx, dy in W5):
                raise SystemExit(f'{name}: ({x + 4},{y}) is within 2 px of an existing listed defect; refusing')
        if name in AFL:
            # factory AF lists are concatenated runs, each sorted by (y, x): append the additions as one new run
            new = sorted(want, key=lambda p: (p[1], p[0]))
            a = a + [af_record(x, y) for x, y in new]
            log += [(name, 'A', af_record(x, y)) for x, y in new]
            out = write_list(out, name, a, b)
            continue
        final = have | set(want)
        for x, y in want:
            for part, rec in expected_records(x, y, table, final):
                if part == 'A': a = insert_sorted(a, rec, 0, final, table)
                else: b = insert_sorted(b, rec, BSTART, final, table)
                log.append((name, part, rec))
        out = write_list(out, name, a, b)
    return out, log


def check(orig, new, pixels_raf, extra=None):
    """Structural checks. Returns list of failure strings (empty = all passed)."""
    bad = []
    table = aux_table(orig)
    for name in LISTS:
        want = set(wanted(pixels_raf, extra, name))
        oa, ob = read_list(orig, name); na, nb = read_list(new, name)
        strip_a = [r for r in na if (xof(r), yof(r)) not in want or r in oa]
        strip_b = [r for r in nb if (xof(r) + BSTART, yof(r)) not in want or r in ob]
        if strip_a != oa: bad.append(f'{name}: A minus additions != original')
        if strip_b != ob: bad.append(f'{name}: B minus additions != original')
        # each requested pixel present exactly once per required part, with the exact expected bits
        olisted = listed_xy(oa, ob); nlisted = listed_xy(na, nb)
        if name in AFL:
            # additions = one new run after all originals, sorted by (y, x), factory AF record bits, no B part
            add = sorted((p for p in want if p not in olisted), key=lambda p: (p[1], p[0]))
            if na != oa + [af_record(x, y) for x, y in add] or nb != ob:
                bad.append(f'{name}: not the originals followed by the AF additions {[(x + 4, y) for x, y in add]} in (y, x) order')
            if any(not is_af_pixel(x, y) for x, y in nlisted - olisted): bad.append(f'{name}: non-AF pixel added')
        for x, y in want:
            if name in AFL: break
            if (x, y) in olisted: continue
            for part, rec in expected_records(x, y, table, nlisted):
                recs, rel = (na, 0) if part == 'A' else (nb, BSTART)
                hits = [r for r in recs if (xof(r) + rel, yof(r)) == (x, y)]
                if hits != [rec]: bad.append(f'{name} {part}: ({x + 4},{y}) expected {rec:09x}, found {[f"{h:09x}" for h in hits]}'); continue
                # factory order: everything before it sorts <= it, nothing after it sorts <= it
                i = recs.index(rec); k = group_key(x, y, nlisted, table)
                keys = [group_key(xof(r) + rel, yof(r), nlisted, table) for r in recs]
                if (i and keys[i - 1] > k) or any(q <= k for q in keys[i + 1:]):
                    bad.append(f'{name} {part}: ({x + 4},{y}) not at its factory sort position')
        # overlap columns must be paired with identical fields in A and B
        pa = {(xof(r), yof(r)): r >> 13 for r in na if xof(r) >= OVERLAP}
        pb = {(xof(r) + BSTART, yof(r)): r >> 13 for r in nb if xof(r) + BSTART <= 3673}
        if pa != pb: bad.append(f'{name}: overlap columns not paired between A and B')
        back = lambda rs: max([xof(rs[i]) - xof(rs[i + 1]) for i in range(len(rs) - 1)] + [0])
        for part, o, n in (('A', oa, na), ('B', ob, nb)):
            if name in MAIN and back(n) > back(o): bad.append(f'{name}: {part} max backward x step grew {back(o)}->{back(n)}')
        if name in MAIN and any(is_af_pixel(x, y) for x, y in nlisted - olisted): bad.append(f'{name}: AF-lattice pixel added to a main list')
        # A padding bits and B trailing bits must be zero
        base, ca, cb = LISTS[name]
        A, B = len(na), len(nb)
        va = int.from_bytes(new[base:base + asize(A)], 'little')
        vb = int.from_bytes(new[base + asize(A):base + asize(A) + bsize(B)], 'little')
        if va >> (34 * A) or vb >> (34 * B): bad.append(f'{name}: nonzero padding bits')
    # every list (all six, from the counts in the new image) must stay inside its own gap: the boot loader trusts
    # the counts and its bounds check allows reads past the 0x420000 image (emu/drills D5)
    gaps = [(0x76800, 0x28e0, 0x11c840), (0x11c840, 0x28e6, 0x2bb8e0), (0x2bb8e0, 0x28ec, 0x3b4940),
            (0x3b4940, 0x28f2, 0x3b8ba8), (0x3b8ba8, 0x28f8, 0x3c31b0), (0x3c31b0, 0x28fe, 0x400000)]
    for base, ca, limit in gaps:
        n = asize(u24(new, ca)) + bsize(u24(new, ca + 3))
        if base + n > limit: bad.append(f'list at {base:#x} spans {n:#x} bytes, past its limit {limit:#x}')
    # everything outside the two list spans and count fields must be byte-identical
    keep = bytearray(b'\x01' * len(orig))
    for name in LISTS:
        base, ca, cb = LISTS[name]
        n = max(span(orig, name)[1], span(new, name)[1])
        for i in range(base, base + n): keep[i] = 0
        for i in range(ca, ca + 6): keep[i] = 0
    diff = [i for i in range(len(orig)) if keep[i] and orig[i] != new[i]]
    if diff: bad.append(f'{len(diff)} bytes changed outside list regions, first {hex(diff[0])}')
    if len(orig) != len(new): bad.append('length changed')
    return bad


if __name__ == '__main__':
    args = sys.argv[1:]
    partners = None   # --partners=list2 | both: also add each pixel's left neighbour like the 9/23 repair
    for s in [s for s in args if s.startswith('--partners=')]:
        partners = s.split('=', 1)[1]; args.remove(s)
        if partners not in ('list2', 'both'): raise SystemExit('--partners must be list2 or both')
    src, dst = args[0], args[1]
    pix = [tuple(int(t) for t in s.split(',')) for s in args[2:]]
    extra = None
    if partners: extra = {n: fuji_partners(pix) for n in (('list2',) if partners == 'list2' else ('list0', 'list2'))}
    if os.path.realpath(dst) in (os.path.realpath(src), os.path.realpath(BACKUP)): raise SystemExit('refusing to overwrite input/backup')
    if os.path.exists(dst): raise SystemExit('output exists, refusing to overwrite')
    d = open(src, 'rb').read()
    new, log = add_pixels(d, pix, extra)
    for name, part, rec in log:
        print(f'  added to {name} {part}: raw34={rec:09x} x={xof(rec)} cls={(rec>>13)&15} y={yof(rec)} aux={(rec>>30)&7} flag={rec>>33}')
    bad = check(d, new, pix, extra)
    print('CHECKS:', 'ALL PASSED' if not bad else bad)
    if not bad:
        open(dst, 'wb').write(new)
        print('wrote', dst, len(new), 'bytes;', sum(1 for i in range(len(d)) if d[i] != new[i]), 'bytes differ from input')
