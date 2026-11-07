# Boot flow and recovery trace (offline, helper report; CERTAIN = read in decompile/disassembly, GUESSED = inferred)
Dumps and scripts in this folder: dump_*.txt, run.sh, *.java.

## Boot order (CERTAIN)
mask ROM (not in the DAT) -> NAND stage-1 at flash 0x40000 (entry 0x1800) -> stream1 (entry FUN_0086f3b4) -> init task FUN_0086f9a8:
FUN_0087fffc (FTL mount) -> FUN_00883280 -> FUN_00874c24 (config load) -> FUN_0084e3c8 -> FUN_0086f560 (power-on cause) -> FUN_0086eef8 (~25 module inits) -> ... "initcont" task FUN_009246d8.

## (1) Service ("jig", ff80) mode decision
- CERTAIN: the key check comes AFTER both FTL mount and config load (initcont -> FSM FUN_0092411c -> FUN_00924740 -> FUN_009232b4 -> FUN_00831078 -> FUN_0082b028 key bits -> FUN_0082b878 mode choice; same logic in stream2 FUN_01008178 / FUN_010083dc).
- Key ids (tools/fujimod catalog xt1/5.52): UP=2, POWER=6, S2=8, DISP_BACK=9, USB=0x10, JIGBOOT=0x33f.
- JIG mode is requested when: KEY_USB and config u16@0x102 == 0xff80, OR KEY_USB + UP + S2 (Shutter+Up), OR KEY_JIGBOOT.
- Config u16@0x102 is the USB PID. Our dump has 0x02bf. The factory default config (NAND 0xE0000) and the last-resort patch table (FUN_00874b64) both have 0xff80, so a config that falls back to defaults should boot straight into ff80 when USB is plugged in (CERTAIN from data, runtime effect NOT verified).
- Shutter+Up does not depend on config contents, only on the load having run. The cfgdata handlers work on the RAM image without a validity check. GUESSED: KEY_USB reads 1 at power-on with the cable plugged in.

## (2) SD-card firmware update
- CERTAIN: stream2 FUN_010db7d4 builds "A:\FWUP" + cfg[0x1c0..0x1c3] + ".DAT" (config holds "0005"). Also XFUPDATE.DAT, XFTCDATE.DAT, FAUPxxxx.DAT.
- Holding DISP_BACK at power-on requests UPDATE mode, only if cfg[0x22e] bit 1 is set (it is, 0x0f). Menu path GUESSED.
- Writer is the flashloader task FUN_00921964 (request 0x4c00+n, 0 = MAIN). It runs after FTL mount and config load, so a failed FTL mount means it is never reached.
- With the factory-default fallback it still works (default block has "0005" at 0x1c0 and 0x0f at 0x22e). Only if the default block at NAND 0xE0000 is also bad do 0x1c0 and 0x22e become zero: filename malformed, key combo disabled.

## (3) Failure behaviour (CERTAIN)
- FTL mount failure or FUN_00883280 nonzero: infinite loop in the init task. No UI, no USB, no retry, no reset. Nothing in the firmware recovers it.
- Bad config magic: not fatal. Image zeroed and reloaded from the raw NAND factory copy, else the patch table; boot continues.
- Module init errors (FUN_0086eef8 / FUN_0086eed8) spin forever. FUN_0086f560 returning 0 powers off via the PMIC, then spins.
- No watchdog or reset code found. Whether a hardware watchdog is armed is unknown (GUESSED: none). A battery pull is the only exit from a spin.

## (4) Separate boot stages
- The DAT does contain early boot code: flash block 0 (repeated 55 AA FF 00 / 01234567 / 89ABCDEF headers, role GUESSED), 0x40000 (+ copy at 0x50000) vector table and stage-1 loader, 0x80000/0xa0000 a 56 KB "syslog Ver 2.3" image (role GUESSED), stream1 at 0x160000, stream2 at 0x380000, factory default config at 0xE0000 (blocks 7-9).
- Stage-1 reads a descriptor at NAND 0x28a0000 and checks u16 0xaaaa; on failure calls fn 0x1e10 and spins. No key, SD or USB access seen in it.
- NOT in the DAT: the mask boot ROM (16 KB at 0x80000000, tag "JASM" at +0x3ff0). fffw `ff80 bootrom` lists X-T1 as supported; the X-E2 bootrom.h lists boot devices RAM, MMC0_FAT, MMC1_RAW, EEP.
- Config damage lives in the FTL region (physical blocks 0x37a+), disjoint from stage-1 and main (raw blocks <= 0x145). So config damage cannot break the loader; only an FTL-mount failure causes a hang.

## Cannot be known offline
- Mask ROM fallback order (needs a ROM dump via `ff80 ram read`, which the owner declined).
- Whether a hardware watchdog is armed.
- Which FTL states make the mount return nonzero.
- Whether a firmware update rewrites stage-1 or blocks 7-9 and whether it preserves the config partition.
