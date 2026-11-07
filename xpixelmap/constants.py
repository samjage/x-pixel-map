"""Fixed facts about the X-T1 (fw 5.52) config image. See NOTES.md / helper/trace_* FINDINGS for sources."""
import hashlib, os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
# your own first read of the camera's config (python -m xpixelmap backup --real), kept as the untouched baseline
BACKUP_PATH = Path(os.environ.get('XPIXELMAP_FACTORY_BACKUP') or PROJECT / 'camera' / 'cfgdata_dump_BACKUP_original.bin')
BACKUP_SHA256 = hashlib.sha256(BACKUP_PATH.read_bytes()).hexdigest() if BACKUP_PATH.is_file() else None
DATA_DIR = PROJECT / 'xpixelmap' / 'data'          # default place for new backups / builds / session journals

IMAGE_SIZE = 0x420000          # config image (cfgdata) size; write handler accepts offset+len-1 <= 0x41ffff
CHUNK = 0x100                  # max bytes per cfgdata read/write (get_config_data_size reports 0x100)
TRIGGER = (0x80, 0x84)         # [start, end): app80 command trigger bytes. Never written by write().
APP80_AREA = (0x80, 0xa1)      # what `cfgdata save` itself writes: 0x81.. = ff 00*31, then 0x80 = 0x22
APP80_SAVE_BYTES = bytes([0x22, 0xff]) + bytes(31)
SAVE_CMD, SAVE_PARAM = 0x22, 0xff

MAGIC_OFF, MAGIC = 0x202, b'\x55\xaa'
LIST_RELOAD_FLAG = 0x244       # must be 0 (1 = loader overwrites [0x3000,0x42000) from the raw factory copy)
USB_DEBUG = 0xf7               # must stay 0

# name, list base, offset of 24-bit A count, offset of 24-bit B count (FUN_00875334 / FUN_0116a1e4)
LISTS = [
    ('list0', 0x76800, 0x28e0, 0x28e3),
    ('list2', 0x11c840, 0x28e6, 0x28e9),
    ('list4', 0x2bb8e0, 0x28ec, 0x28ef),
    ('list1', 0x3b4940, 0x28f2, 0x28f5),
    ('list3', 0x3b8ba8, 0x28f8, 0x28fb),
    ('list5', 0x3c31b0, 0x28fe, 0x2901),
]
# Each list must stay inside its own gap [base, next base); the last one ends at 0x400000. The boot loader's own
# bound is 0x520000 (FUN_00880918), so a bad count would overwrite RAM past the image at boot (emu/drills D5).
LIST_REGION_END = 0x400000
LENS_REGION = (0x3e0000, 0x400000)   # lens-profile block: must never change (not loaded by the emulated boot)
COUNT_FIELDS = (0x28e0, 0x2904)
# AF-stage (recbfCorrectPhase) weights, one byte per class (28 classes, stride 0x1c between the tables):
# own pixel, then the four neighbour weights (6-bit register fields). Only classes AF_WEIGHT_CLASSES (all 28 during the class-mapping test; was ISO <= 2000 long
# exposures, class = ISO group*7 + exposure group) may differ from the base, and the total must stay as in the base.
AF_WEIGHT_TABLES = (0x11426, 0x1140a, 0x113ee, 0x113d2, 0x113b6)   # own, n1, n2, n3, n4
AF_WEIGHT_CLASSES = tuple(range(28))

# Raw correction gate (FUN_0116cd10), one byte per ISO index: on when Tv <= a * 10, 127 = on at every shutter speed.
RECC_GATE = (0x2800, 0x2805)   # the ISO indexes not already on: below 200 up to ISO 1000
RECC_GATE_ON = 127

EXPECTED_PRODUCT = 'X-T1'
SAVE_PHRASE = 'SAVE {sha12} TO CAMERA'
