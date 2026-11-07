"""READ-ONLY. Per-frame report of a capture folder: shutter, ISO, exposure, the watched pixels and how many pixels stand out.
Value = pixel minus the median of its four same-colour neighbours at +-6 px, per frame (not the lowest of the frames).
Usage: python tools/capture/frame_report.py <capture folder> [x,y ...]"""
import glob, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'helper'), str(ROOT / 'tools' / 'capture')]
import numpy as np, hotscan as H, xt1capture as C

WATCH = [(3208, 1789), (3208, 1790), (2242, 1470), (878, 1213), (2312, 2060), (4213, 1774), (2317, 1649), (1099, 2905)]


def dev(a):
    n = np.stack([a[6:-6, :-12], a[6:-6, 12:], a[:-12, 6:-6], a[12:, 6:-6]])
    return a[6:-6, 6:-6] - np.median(n, axis=0)


def main():
    folder = sys.argv[1]
    watch = [tuple(int(v) for v in p.split(',')) for p in sys.argv[2:]] or WATCH
    print('watched pixels:', watch)
    sets = {}
    for f in sorted(glob.glob(os.path.join(folder, '*.RAF'))):
        sets.setdefault(re.sub(r'_\d+\.RAF$', '', os.path.basename(f)), []).append(f)
    for name, files in sets.items():
        t, iso, st = C.exif(files[0])
        print(f'\n{name}: {st}, ISO {iso}, {t:g} s, {len(files)} frames')
        hits = None
        for f in files:
            a = H.load(f).astype(np.float32); e = dev(a)
            noise = 1.4826 * float(np.median(np.abs(e[500:2500, 500:4000])))
            hot = e > max(40.0, 8 * noise)
            hits = hot.astype(np.uint16) if hits is None else hits + hot
            vals = [int(a[y, x] - np.median([a[y, x - 6], a[y, x + 6], a[y - 6, x], a[y + 6, x]])) for x, y in watch]
            print(f'  {os.path.basename(f)[-6:-4]} noise {noise:5.1f} not-multiple-of-4 {float((a % 4 != 0).mean()) * 100:5.1f}%  {vals}')
        need = max(2, (len(files) * 2 + 2) // 3)
        ys, xs = np.nonzero(hits >= need)
        print(f'  hot in at least {need} of {len(files)} frames: {len(xs)}', [(int(x) + 6, int(y) + 6) for x, y in zip(xs[:12], ys[:12])])


if __name__ == '__main__':
    main()
