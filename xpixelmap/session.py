"""One camera session = one power-on in service mode, one Transport. Enforces the order
identify -> backup (camera must equal the base image) -> write full image -> full read-back (sha256) -> save,
and refuses anything out of order. Any transport failure taints the session: no further write or save."""
from pathlib import Path

from .constants import IMAGE_SIZE, CHUNK, TRIGGER, EXPECTED_PRODUCT, SAVE_PHRASE, SAVE_CMD
from .image import sha256, presave_checks, first_diff
from .transport import TransportError
from .allowlist import ForbiddenOperation


class SessionError(Exception):
    pass


class Refused(SessionError):
    """Refused before anything was written."""


class Aborted(SessionError):
    """Stopped mid-way; nothing was saved."""


class SaveOutcomeUnknown(SessionError):
    """Save command sent, no valid response."""


PROGRESS_EVERY = 0x40000


class Session:
    def __init__(self, transport, journal, base, restore_mode=False, log=print):
        if len(base) != IMAGE_SIZE:
            raise Refused('base image has the wrong size')
        self.t, self.j, self.base, self.log = transport, journal, bytes(base), log
        self.base_sha = sha256(self.base)
        self.restore_mode = restore_mode
        self.identified = False
        self.camera_image = None
        self.camera_matches_base = None
        self.written_sha = self.verified_sha = None
        self.tainted = False
        self.saved = False
        self.trigger_diff = False
        journal.event('session_start', base_sha=self.base_sha, restore_mode=restore_mode)

    # ---- helpers
    def _refuse(self, why):
        self.j.event('refused', reason=why)
        raise Refused(why)

    def _fail(self, event, err, **f):
        self.tainted = True
        self.j.event(event, error=f'{type(err).__name__}: {err}', **f)

    # ---- steps
    def identify(self):
        try:
            info = self.t.info()
            size, chunk = self.t.config_size()
        except (TransportError, ForbiddenOperation) as e:
            self._fail('identify_failed', e)
            raise Aborted(f'identify failed: {e}')
        product = str(info.get('Product Name', '')).strip('\x00 ')
        self.j.event('identified', info=info, config_size=size, chunk=chunk)
        if product != EXPECTED_PRODUCT:
            self._refuse(f'camera reports product {product!r}, expected {EXPECTED_PRODUCT!r}')
        if (size, chunk) != (IMAGE_SIZE, CHUNK):
            self._refuse(f'config size {size:#x}/{chunk:#x}, expected {IMAGE_SIZE:#x}/{CHUNK:#x}')
        self.identified = True
        return info

    def read_full(self, what):
        data = bytearray()
        try:
            for off in range(0, IMAGE_SIZE, CHUNK):
                data += self.t.read(off, CHUNK)
        except (TransportError, ForbiddenOperation, KeyboardInterrupt) as e:
            self._fail(f'{what}_failed', e, offset=len(data))
            if isinstance(e, KeyboardInterrupt): raise
            raise Aborted(f'{what} read failed at {len(data):#x}: {e}')
        return bytes(data)

    def backup(self, out_dir=None):
        if not self.identified:
            self._refuse('identify first')
        self.j.event('backup_begin')
        data = self.read_full('backup')
        sha = sha256(data)
        path = None
        if out_dir is not None:
            out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f'cfgdata_{self.j.dir.name}_{sha[:12]}.bin'
            with open(path, 'xb') as f:            # never overwrite
                f.write(data)
            path = str(path)
        self.camera_image = data
        self.camera_matches_base = data == self.base
        self.j.event('backup', sha256=sha, matches_base=self.camera_matches_base, file=path)
        self.log(f'camera image sha256 {sha} ({"==" if self.camera_matches_base else "!="} base {self.base_sha[:12]})')
        return data, sha, path

    def write_image(self, img, allow_trigger_diff=False):
        img = bytes(img); sha = sha256(img)
        if self.tainted: self._refuse('session had a failure: power-cycle and start a new session')
        if self.saved: self._refuse('already saved in this session: power-cycle first')
        if not self.identified or self.camera_image is None: self._refuse('identify + backup first')
        if self.written_sha is not None: self._refuse('one write per session')
        if self.restore_mode:
            if img != self.base: self._refuse('restore writes the base (backup) image only')
        elif not self.camera_matches_base:
            self._refuse('camera image != base image (wrong camera, or camera not in the expected state)')
        bad = presave_checks(img, self.base)
        if bad: self._refuse('pre-save checks failed: ' + '; '.join(bad))
        try:
            cam_trig = self.t.read(TRIGGER[0], TRIGGER[1] - TRIGGER[0])
        except (TransportError, ForbiddenOperation) as e:
            self._fail('write_failed', e, offset=0)
            raise Aborted(f'trigger read failed: {e}')
        if cam_trig != img[TRIGGER[0]:TRIGGER[1]]:
            if not (allow_trigger_diff and self.restore_mode):
                self._refuse(f'camera bytes 0x80..0x83 = {cam_trig.hex()} != image {img[0x80:0x84].hex()}; they are '
                             f'never written (restore may pass --allow-trigger-diff: the save command rewrites them)')
            self.trigger_diff = True
        self.j.event('write_begin', image_sha=sha, trigger_diff=self.trigger_diff)
        off = 0
        try:
            for off in range(0, IMAGE_SIZE, CHUNK):
                if off <= TRIGGER[0] < off + CHUNK:   # split around 0x80..0x83 (verified identical or restore)
                    if TRIGGER[0] > off: self.t.write(off, img[off:TRIGGER[0]])
                    if off + CHUNK > TRIGGER[1]: self.t.write(TRIGGER[1], img[TRIGGER[1]:off + CHUNK])
                else:
                    self.t.write(off, img[off:off + CHUNK])
                if (off + CHUNK) % PROGRESS_EVERY == 0:
                    self.j.event('write_progress', next_offset=off + CHUNK)
        except (TransportError, ForbiddenOperation, KeyboardInterrupt) as e:
            self._fail('write_failed', e, offset=off)
            if isinstance(e, KeyboardInterrupt): raise
            raise Aborted(f'write failed at {off:#x}: {e}. NOTHING SAVED; power-cycle the camera.')
        self.written_sha = sha
        self.j.event('write_done', image_sha=sha)

    def verify_ram(self, img):
        img = bytes(img); sha = sha256(img)
        if self.tainted or self.written_sha != sha:
            self._refuse('verify needs a completed write of this image in this session')
        self.j.event('readback_begin', image_sha=sha)
        data = self.read_full('readback')
        if self.trigger_diff:
            ok = (data[:TRIGGER[0]] == img[:TRIGGER[0]] and data[TRIGGER[1]:] == img[TRIGGER[1]:])
        else:
            ok = sha256(data) == sha
        if not ok:
            i = first_diff(data, img)
            self.tainted = True
            self.j.event('readback_mismatch', first_diff=i, got_sha=sha256(data))
            raise Aborted(f'read-back differs from the image (first at {i:#x}). NOT SAVED; power-cycle the camera.')
        self.verified_sha = sha
        self.j.event('readback_ok', image_sha=sha, readback_sha=sha256(data))

    def save(self, img, confirm, image_path=None):
        img = bytes(img); sha = sha256(img)
        if self.tainted: self._refuse('session had a failure; no save')
        if self.saved: self._refuse('already saved')
        if not (self.written_sha == self.verified_sha == sha):
            self._refuse('save only after write + read-back verify of this exact image in this session')
        if image_path is not None:
            with open(image_path, 'rb') as f:
                if sha256(f.read()) != sha:
                    self._refuse(f'{image_path} changed on disk since it was written')
        phrase = SAVE_PHRASE.format(sha12=sha[:12])
        self.j.event('save_prompt', phrase=phrase)
        if not confirm(phrase):
            self.j.event('save_declined')
            self.log('save declined: camera RAM holds the image, flash unchanged; power-cycle to discard.')
            return False
        self.j.event('save_begin', image_sha=sha)
        try:
            r = self.t.save()
        except (TransportError, ForbiddenOperation, KeyboardInterrupt) as e:
            self._fail('save_interrupted', e)
            if isinstance(e, KeyboardInterrupt): raise
            raise SaveOutcomeUnknown(f'save sent, no valid response: {e}')
        self.j.event('save_response', response=r[:4].hex())
        if r[0] == SAVE_CMD and r[1] == 0:
            self.saved = True
            self.j.event('save_ok', image_sha=sha)
            return True
        self.tainted = True
        self.j.event('save_error', response=r[:4].hex())
        raise SessionError(f'camera reported save error: response {r[:4].hex()}')


def run_write(transport, journal, base, img, confirm=None, backup_dir=None, image_path=None, log=print):
    """identify -> backup -> write -> verify [-> save if confirm is given]. Returns the Session."""
    s = Session(transport, journal, base, log=log)
    s.identify(); s.backup(backup_dir); s.write_image(img); s.verify_ram(img)
    log('write + read-back OK (sha256 matches).')
    if confirm is not None:
        s.save(img, confirm, image_path=image_path)
    return s


def run_restore(transport, journal, backup_img, confirm, backup_dir=None, allow_trigger_diff=False, log=print):
    """Write the backup image back in full, verify, save. Camera may hold anything (that is why we restore)."""
    s = Session(transport, journal, backup_img, restore_mode=True, log=log)
    s.identify(); s.backup(backup_dir)
    s.write_image(backup_img, allow_trigger_diff=allow_trigger_diff)
    s.verify_ram(backup_img)
    s.save(backup_img, confirm)
    return s
