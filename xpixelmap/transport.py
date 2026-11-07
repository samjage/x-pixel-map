"""Transport interface: exactly five camera operations, nothing else.

info()          -> dict of device strings
config_size()   -> (image size, max chunk)
read(off, n)    -> bytes, n <= 0x100
write(off, b)   -> None, len(b) <= 0x100, never 0x80..0x83, never outside 0..0x41ffff
save()          -> 33-byte app80 response (cfgdata save = app80 cmd 0x22 param 0xff)

Subclasses implement _info/_config_size/_read/_write/_save. They may not add public attributes or override the
five public methods (enforced at class creation), so no other command can be reached through a Transport.
"""
from .allowlist import check_read, check_write

ALLOWED_OPS = ('info', 'config_size', 'read', 'write', 'save')


class TransportError(Exception):
    """Camera did not complete an operation (USB error, error status, short reply)."""


class TransportTimeout(TransportError):
    """No reply in time (cable pulled, camera hung, power lost)."""


class Transport:
    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        for name in vars(cls):
            if name.startswith('_'):
                continue
            if name in ALLOWED_OPS:
                raise TypeError(f'{cls.__name__} may not override public operation {name!r}')
            raise TypeError(f'{cls.__name__} may not expose public attribute {name!r} (allowlist: {ALLOWED_OPS})')

    def info(self):
        return dict(self._info())

    def config_size(self):
        size, chunk = self._config_size()
        return int(size), int(chunk)

    def read(self, off, n):
        check_read(off, n)
        data = bytes(self._read(off, n))
        if len(data) != n:
            raise TransportError(f'short read at {off:#x}: {len(data)} of {n} bytes')
        return data

    def write(self, off, data):
        data = bytes(data)
        check_write(off, data)
        self._write(off, data)

    def save(self):
        r = bytes(self._save())
        if len(r) < 2:
            raise TransportError(f'short app80 response {r.hex()}')
        return r

    # --- implemented by subclasses
    def _info(self): raise NotImplementedError
    def _config_size(self): raise NotImplementedError
    def _read(self, off, n): raise NotImplementedError
    def _write(self, off, data): raise NotImplementedError
    def _save(self): raise NotImplementedError
