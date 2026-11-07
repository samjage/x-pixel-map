"""READ-ONLY. Find pixels that stand out from their same-colour neighbours (+-6 px, X-Trans period) in the same place
across many ordinary RAF frames. Needs numpy (run with the scratchpad venv). Never touches the camera.
Usage: hotscan.py <file_with_paths> <max_frames> [min_fraction=0.6] [threshold=250]
Prints candidates as: raf_x raf_y hits/frames mean_excess
"""
import sys, struct
import numpy as np
W, H = 4992, 3296


def load(path):
    d = open(path, 'rb').read()
    co, cl = struct.unpack('>II', d[0x64:0x6c])
    n = W * H * 2
    for off in (co, co + cl - n):
        a = np.frombuffer(d[off:off + n], dtype='<u2')
        if 100 < a.max() < 16384:
            return a.reshape(H, W).astype(np.int32)
    return None


def excess(a):
    # value minus the highest of the four same-colour neighbours at +-6 px
    e = np.full(a.shape, -9999, dtype=np.int32)
    m = np.maximum.reduce([a[6:-6, 0:-12], a[6:-6, 12:], a[0:-12, 6:-6], a[12:, 6:-6]])
    e[6:-6, 6:-6] = a[6:-6, 6:-6] - m
    return e


def spread(a):
    # value minus the median of the four same-colour neighbours at +-6 px
    e = np.full(a.shape, -9999, dtype=np.int32)
    n = np.stack([a[6:-6, 0:-12], a[6:-6, 12:], a[0:-12, 6:-6], a[12:, 6:-6]]).astype(np.float32)
    e[6:-6, 6:-6] = a[6:-6, 6:-6] - np.median(n, axis=0)
    return e


if __name__ == '__main__':
    paths = [l.rstrip('\n').split('\t')[-1] for l in open(sys.argv[1]) if l.strip()]
    nmax = int(sys.argv[2]); frac = float(sys.argv[3]) if len(sys.argv) > 3 else 0.6
    thr = int(sys.argv[4]) if len(sys.argv) > 4 else 250
    if len(paths) > nmax:
        paths = [paths[int(i * len(paths) / nmax)] for i in range(nmax)]
    hits = np.zeros((H, W), dtype=np.uint16); tot = np.zeros((H, W), dtype=np.int64); n = 0
    for p in paths:
        a = load(p)
        if a is None: continue
        e = excess(a); h = e > thr
        hits += h; tot += np.where(h, e, 0); n += 1
    print('frames used', n)
    ys, xs = np.nonzero(hits >= max(3, int(frac * n)))
    for y, x in sorted(zip(ys, xs), key=lambda t: -hits[t]):
        print(x, y, f'{hits[y, x]}/{n}', int(tot[y, x] / hits[y, x]))
