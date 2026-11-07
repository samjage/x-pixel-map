"""History keeps a small copy of each finished session so the before/after viewer still works after the RAW frames are gone.
Dark sets: the per-pixel excess map (the same one the sorting step used), clipped to int16 and compressed.
White sets: the mean frame at one third size.  A real session is about 1.3 GB of RAW; this is a few MB per set."""
import os
from pathlib import Path

LO, HI = -400, 4000      # excess values outside this are clipped; a hot pixel that bright is already as bad as it gets


def _dir(sess_dir): return Path(sess_dir) / 'archive'


def save(sess_dir, sets):
    """Write archive/<set>.npz for every set whose frames are still here. Returns the names saved."""
    import numpy as np, hotscan as H
    out, saved = _dir(sess_dir), []
    out.mkdir(exist_ok=True)
    for name, info in sets.items():
        files = [Path(f) for f in info.get('files', []) if Path(f).is_file()]
        if not files: continue
        if info.get('kind') == 'flat':
            a = np.mean([H.load(f).astype(np.float32) for f in files], axis=0)
            np.savez_compressed(out / f'{name}.npz', flat=a[::3, ::3].astype(np.float16))
        else:
            npy = files[0].with_suffix('.minexcess.npy')
            if not npy.is_file(): continue
            np.savez_compressed(out / f'{name}.npz', excess=np.clip(np.load(npy), LO, HI).astype(np.int16))
        saved.append(name)
    return saved


def load_mean(sess_dir, name, flat, black):
    """A stand-in for the mean frame, from the archive: (array shaped like the sensor, as float32) or None."""
    import numpy as np
    f = _dir(sess_dir) / f'{name}.npz'
    if not f.is_file(): return None
    z = np.load(f)
    if flat and 'flat' in z: return np.repeat(np.repeat(z['flat'].astype(np.float32), 3, axis=0), 3, axis=1)
    if 'excess' in z: return z['excess'].astype(np.float32) + black
    return None


def clear_frames(sess_dir, sets, saved):
    """Delete the RAW frames (and their excess maps) of every set that was archived. Nothing is deleted unless its archive file exists."""
    freed = 0
    for name in saved:
        if not (_dir(sess_dir) / f'{name}.npz').is_file(): continue
        for f in sets.get(name, {}).get('files', []):
            f = Path(f)
            for g in (f, f.with_suffix('.minexcess.npy'), f.with_suffix('.peakexcess.npy')):
                try: freed += g.stat().st_size; g.unlink()
                except FileNotFoundError: pass
    fr = Path(sess_dir) / 'frames'                    # trial shots and anything else left over in the frames folder
    if fr.is_dir() and saved and all((_dir(sess_dir) / f'{n}.npz').is_file() for n in saved):
        for g in list(fr.iterdir()):
            if g.suffix in ('.RAF', '.npy'):
                try: freed += g.stat().st_size; g.unlink()
                except FileNotFoundError: pass
    return freed


def keep_frames(): return bool(os.environ.get('XPIXELMAP_KEEP_FRAMES'))
