# EXR II boot ROM SD path vs the X-T1 (2026-11-04)
ROM: boot-rom.bin from FujiMod's X70 emulator bundle (fujimod.com/runtime/x70-110/), 16,384 B, sha256 4ecedfbf... = the hash FujiMod lists for the X-T1 5.52 too. Kept only in the scratchpad (not in the project, not shared). Disassembled at address 0 (ARM).

## Boot flow (CERTAIN, ROM offsets)
- 0x260 mode select: r1 = latch[0xfff98000]&7 | GPIO0 data bits 10,11 (0x14 & 0xc00) >> 7. Both button bits low (AE-L + Fn1 pressed) -> mode 1 = SD (0x82c). All latch bits set -> power hold cleared.
- 0x82c SD boot: mount (0x1ba0, Petit-FatFs-style: MBR first partition, FAT12/16/32, 8.3 names, LFN ignored), 3 tries; open ffsys.bin, read 1024 B to 0xfff00004; 0x304 applies the header (power hold = +0x10 bit 0, clocks/DRAM from +0x14..+0x80); open ffboot.bin, read in 32 KB chunks to 0x40000000; retries = header +0x10 bits 8-10. Any failure: 0x858 clears power hold (0xfff88084) -> camera goes dark.
- 0x2298: latch[0xfff98000+8] = 1 (SD), jump to address 0 (loaded image).
- 0x1308 SD init (card 0 = SDHCI at 0xfff50000): pin mux port 12 (+0x30c = 0xf000a000), clock enables, then 0x155c: software reset, wait for card-state-stable, then REQUIRES present-state bit 16 (card inserted, from the controller's card-detect input). No card detected -> init fails.

## Why the X-T1 goes dark: leading hypothesis, UNPROVEN (downgraded 2026-11-04 after the owner's challenge)
- Every other EXR II body in FujiMod's catalog (X70, X-E2, X-E2S, X-T10, X100S, X100T, X20, X30, X-A1, X-A2, X-M1, XQ) wires the SD card-detect switch to GPIO 12.7 (the SD pin group the ROM muxes).
- The X-T1 wires it to GPIO 15.1: FujiMod control-ids (source 0xF0001) AND the X-T1's own key table (stream1 0xAF5428 = 0x000F0001; no 0x000C0007 entry). CERTAIN for the firmware table.
- The ROM checks only the SD controller's card-inserted bit, so on the X-T1 it most likely never sees a card -> dark with or without a card. Matches every hardware result: green_led card, re-laid-out card, blink card (sets power hold itself), and no card at all (control B) all go dark the same way.
- Consequence IF the hypothesis holds: no card-side fix exists. Not confirmed on the board; a dark lamp has several possible causes. Treat SD boot as unproven, not as a safety net and not as ruled out. Better probe: observe the chip (e.g. a meter or logic probe on the card-detect pin), not the lamp. Remaining recovery paths: USB service mode (Shutter+Up), automatic service mode after a config failure, DISP/BACK firmware update from the card (stock firmware, GPIO card detect). A flash-layer failure during a save remains unrecoverable at home.
