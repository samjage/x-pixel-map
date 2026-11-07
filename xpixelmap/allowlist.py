"""Command allowlist. Every transport routes read/write through these checks before anything is sent."""
from .constants import IMAGE_SIZE, CHUNK, TRIGGER


class ForbiddenOperation(Exception):
    """A request the tool must never send to the camera."""


def _int(v, name):
    if not isinstance(v, int) or isinstance(v, bool):
        raise ForbiddenOperation(f'{name} must be an int, got {type(v).__name__}')
    return v


def check_read(off, n):
    _int(off, 'offset'); _int(n, 'length')
    if not 1 <= n <= CHUNK:
        raise ForbiddenOperation(f'read length {n} outside 1..{CHUNK:#x}')
    if off < 0 or off + n > IMAGE_SIZE:
        raise ForbiddenOperation(f'read [{off:#x},+{n:#x}) outside the config image 0..{IMAGE_SIZE - 1:#x}')


def check_write(off, data):
    _int(off, 'offset')
    if not isinstance(data, (bytes, bytearray)):
        raise ForbiddenOperation('write data must be bytes')
    n = len(data)
    if not 1 <= n <= CHUNK:
        raise ForbiddenOperation(f'write length {n} outside 1..{CHUNK:#x}')
    if off < 0 or off + n > IMAGE_SIZE:
        raise ForbiddenOperation(f'write [{off:#x},+{n:#x}) outside the config image 0..{IMAGE_SIZE - 1:#x}')
    if off < TRIGGER[1] and off + n > TRIGGER[0]:
        raise ForbiddenOperation(f'write [{off:#x},+{n:#x}) touches the app80 trigger bytes '
                                 f'{TRIGGER[0]:#x}..{TRIGGER[1] - 1:#x}')
