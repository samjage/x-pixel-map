"""READ-ONLY. Before/after check for a pixel-map save: did the target hot pixels go away, and did nothing new
appear around them? Reuses hotscan.load/excess (same-colour neighbours at +-6 px). Needs numpy (scratchpad venv).
Usage: acceptance.py --before A.RAF [B.RAF ...] --after C.RAF [D.RAF ...] --pixel 2497,1861 [--pixel x,y ...]
Shoot the same conditions before and after (lens cap on, RAW, LONG EXPOSURE NR off): one set at a short shutter,
one at >= 1 s (list2/list3 territory). Pass needs every target pixel fixed and no new defect within 3 px.
"""
import argparse, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hotscan as H

THR = 250          # DN above the brightest same-colour neighbour = hot (as in hotscan)
FIXED = 0.10       # target counts as fixed when hot in at most this fraction of the after frames
NEW = 0.50         # a neighbour counts as a new defect when hot/dark in this fraction after and <= FIXED before
R = 3              # neighbourhood radius checked for side effects (bad aux = smear or dark spot next to the pixel)


def stack(paths):
    out = []
    for p in paths:
        a = H.load(p)
        if a is None: print('  skipped (not an X-T1 RAF?)', p); continue
        out.append(H.excess(a))
    if not out: raise SystemExit('no usable frames')
    return np.array(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--before', nargs='+', required=True)
    ap.add_argument('--after', nargs='+', required=True)
    ap.add_argument('--pixel', action='append', required=True, help='RAF x,y')
    a = ap.parse_args()
    pix = [tuple(int(t) for t in p.split(',')) for p in a.pixel]
    eb, ea = stack(a.before), stack(a.after)
    print(f'frames: before {len(eb)}, after {len(ea)}')
    ok = True
    for x, y in pix:
        hb, ha = (eb[:, y, x] > THR).mean(), (ea[:, y, x] > THR).mean()
        fixed = ha <= FIXED
        ok &= fixed
        print(f'({x},{y}): hot before {hb:.0%} (median excess {int(np.median(eb[:, y, x]))}), '
              f'after {ha:.0%} (median {int(np.median(ea[:, y, x]))}) -> {"FIXED" if fixed else "NOT FIXED"}')
        for dy in range(-R, R + 1):
            for dx in range(-R, R + 1):
                if (dx, dy) == (0, 0): continue
                px, py = x + dx, y + dy
                for kind, b, n in (('hot', eb[:, py, px] > THR, ea[:, py, px] > THR),
                                   ('dark', eb[:, py, px] < -THR, ea[:, py, px] < -THR)):
                    if n.mean() >= NEW and b.mean() <= FIXED:
                        ok = False
                        print(f'   NEW {kind} neighbour ({px},{py}): before {b.mean():.0%}, after {n.mean():.0%}')
    print('RESULT:', 'PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
