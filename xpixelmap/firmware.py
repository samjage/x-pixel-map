"""The owner's own copy of Fujifilm's X-T1 5.52 firmware, used only by the emulated rehearsal before a save. It is never shipped:
the owner downloads FWUP0005.DAT from Fujifilm and chooses it once, then it is unpacked into the data folder.
The stream format is fffw's fflz (ffcompress/fflz, MIT): a byte under 0x80 is that many literals, otherwise a 2-byte match."""
import hashlib
import struct

from .constants import DATA_DIR

FW_DIR = DATA_DIR / 'firmware'
FW_SHA256 = '4cf56ba6abb4298a844422c016289228bb0acce0a2ae2c6755c05fffb38e92db'    # FWUP0005.DAT, X-T1 5.52
FW_URL = 'https://fujifilm-x.com/global/support/download/firmware/cameras/x-t1/'
FLASH_OFFSET = 604                                                                # the file is the flash image, inverted
STREAMS = (('stream1_decoded.bin', 1441792, 4115048), ('stream2_decoded.bin', 3670016, 6505664))
FILES = ('flash.bin',) + tuple(s[0] for s in STREAMS)


class FirmwareError(Exception):
    pass


def ready():
    return all((FW_DIR / f).is_file() for f in FILES)


def decompress(src):
    unpacked, packed, _, page, method = struct.unpack_from('<5I', src, 0)
    if page != 0x4000 or method != 4 or not 20 <= packed <= len(src):
        raise FirmwareError('unexpected stream header')
    body, out, pos = src[20:packed], bytearray(), 0
    while pos < len(body):
        c = body[pos]; pos += 1
        if c < 0x80:
            out += body[pos:pos + c]; pos += c
        else:
            s = body[pos]; pos += 1
            dist, n = ((c & 0x7f) << 4) | (s >> 4), s & 15
            for _ in range(n): out.append(out[-dist])
    if len(out) != unpacked:
        raise FirmwareError('stream did not unpack to its stated size')
    return bytes(out)


def install(data):
    """Check the file is the exact firmware, then unpack it. Anything else is refused and nothing is kept."""
    if hashlib.sha256(data).hexdigest() != FW_SHA256:
        raise FirmwareError('This is not the X-T1 firmware 5.52 file (FWUP0005.DAT). Nothing was kept.')
    flash = bytes(data[FLASH_OFFSET:]).translate(bytes(255 - i for i in range(256)))
    FW_DIR.mkdir(parents=True, exist_ok=True)
    for name, off, size in STREAMS:
        (FW_DIR / name).write_bytes(decompress(flash[off:off + size + 64]))
    (FW_DIR / 'flash.bin').write_bytes(flash)
