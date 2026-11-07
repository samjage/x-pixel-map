# X-T1 boot modes and recovery entry paths (offline, 2026-11-04)
C = read in code/data. F = stated in fujimod/fffw sources, never tested on an X-T1. G = guessed.
Script: blgraph.py (backward call graph for stream2). Nothing touched the camera.

## Entry table
| Trigger at power-on | Result | Preconditions | Config broken? | NAND/FTL broken? | Certainty |
|---|---|---|---|---|---|
| Battery pull, hold AE-L + Fn1, FAT32 SD with ffsys.bin + ffboot.bin | Mask ROM boots a ~30 MB RAM image from SD; stock firmware runs from RAM | Card built by tools/fujimod/crates/recovery (recipe xt1-552) | Same as stock boot | Skips NAND code, but still runs NAND init, bad-block scan, FTL mount: broken FTL still hangs unless the image is patched | F (all X-T1 SD stages "not-run") |
| Same card, release at green LED, then hold DISP/BACK | Stock UPDATE mode from SD, flashes FWUP0005.DAT | cfg 0x22e bit 1 (factory default 0x0f) | Yes if fallback config loads; no if config all zero | Rewrites flash 0..0x28a0020 (stage-1, streams); never touches the FTL (block 0x37a+) | F + C |
| Green-LED card (ffsys.bin + tiny ffboot.bin) | LED green: proves ROM took the SD path | None | Yes | Yes (no NAND use) | F |
| Hold DISP/BACK (normal boot) | UPDATE mode (only caller of request 0xc) | cfg 0x22e bit 1; FWUP + cfg[0x1c0..0x1c3] + .DAT on card | Default fallback yes; all-zero no | No | C |
| USB + Shutter (S2) + Up | JIG / ff80 | Boot reaches initcont | Yes | No | C |
| USB with cfg u16 0x102 == 0xff80 | JIG | Factory default config and patch table both hold 0xff80 | Yes, automatic when defaults load | No | C (runtime unverified) |
| KEY_JIGBOOT (0x33f) | JIG | Hardware jig line, not in key-scanner table (test pad?) | Yes | No | C / G |
| USB alone | DPS = PC/PTP (or REC if setting 0xaf != 0); PID from cfg 0x102 (0x02bf) | None | Yes | No | C |
| Lens barrier / POWER | REC / PLAY / POWERON; SETUP if clock not set (G) | None | Yes | No | C / G |
| Battery/card door | Power off / wake | None | n/a | n/a | C |

## Details
1. Key combos: no other hidden or factory modes in FUN_0082b878 / FUN_01008178 / FUN_010083dc (stream2 mode 0x11 is for a power ring the X-T1 lacks, G). All key checks run after FTL mount and config load (C).
2. SD files (stream2 FUN_010db7d4; names C, roles G): FWUP0005.DAT body firmware; XFUPxxxx.DAT lens; XFTCxxxx.DAT teleconverter; FAUP0001.DAT accessory. No automatic scan at boot; only from UPDATE-mode state tables (0x13f0060 / 0x13f0530).
3. Stage-1 loader (flash 0x40000, C): NAND init (0x205c), bad-block scan (0x2080), section table (0x1dec), footer check at NAND 0x28a0000 (u16 0xaaaa), kernel load/decompress (0x1d30). Any failure calls 0x1e10 (clears power-hold bit 0 of 0xfff88084) = camera switches off. No key/SD/USB fallback.
4. Mask ROM: SD path that skips NAND code exists (F). ffsys.bin = 512-byte NAND boot header from flash 0x0 (00ffaa55 / 01234567 / 89abcdef) plus the 0x800 page; fujimod sets bit 0 at +0x10 (0x300 -> 0x301) to hold power while the ROM loads ffboot.bin; word +0x18 = 2 (unknown). Boot-source latch at 0xfff98000 (fujimod emulator); AE-L and Fn1 are plain GPIO lines (G that the ROM samples them). X-T1 recipe keeps NAND init, bad-block scan, FTL/config access; a no-NAND option exists only for EXR-I. No ROM dump in this repo.
5. USB: only a PTP interface descriptor; PID from cfg 0x102. No mass storage, no USB boot loader (ROM boot devices: RAM, MMC0_FAT, MMC1_RAW, EEP0-3, DEV56).

## Recovery plan
- Config broken, NAND fine: defaults load (PID 0xff80) -> USB alone or Shutter+Up gives ff80 -> restore backup. DISP/BACK update also works.
- Stage-1 / firmware / footer broken (camera powers straight off): SD boot (AE-L + Fn1) then DISP/BACK -> reflash official DAT from the card. Test the green-LED card first; no X-T1 SD boot has been confirmed.
- FTL broken (camera hangs powered on): neither ff80 nor stock SD boot helps; would need a custom ffboot.bin that patches out the FTL-mount loop and config load (feasible, G). A firmware update never rewrites the FTL area.
- NAND controller / bad-block scan failure: stock X-T1 SD recipe also powers off; patching calls at 0x1f30 / 0x1f5c (as the EXR-I option does) is untested (G).
- Before any risky write: build the X-T1 green-LED and recovery SD cards offline with fujimod-recovery (cargo, crates already present, no download) and test the green-LED card.
