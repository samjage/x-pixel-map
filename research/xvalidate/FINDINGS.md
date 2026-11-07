# Cross-validation of X-T1 recovery paths against sibling cameras (offline, 2026-11-04)
C = checked in code/data, F = stated in a local source, G = guess. No downloads, no camera.

| Claim | X-T1 evidence | Sibling evidence | X-T1 grade |
|---|---|---|---|
| A. USB + Shutter + Up = ff80 | C: KEY_USB+UP+S2 in FUN_0082b878 / stream2 | X-Pro1 service manual (official); fujimod boot.service confirmed on X70 and X-T20 (catalog/bodies/{x70,xt20}/controls.json, catalog/progress/); fffw README same; fffw `ff80 bootrom` lists X-T1 supported (ff80_cmd/cmd.py:82). X-T1 camera-identity not-run in fujimod | High |
| B. Auto jig when cfg 0x102 == 0xff80 | C: compare in code; factory default config (flash 0xE0000): 0x102=0xff80, 0x13a=0xff80, 0x13e=0x02bf | C: X-A2 1.30 and X-T2 4.41 default configs same layout (0x102=0x13a=0xff80; 0x13e=0x02c6/0x02cd). F: fffw names 0x102 "Default PID", 0x13a "JIG PID ?", 0x13e "PTP PID ?" (ffjlib.py:119). Not tested on any model | Medium (runtime untested) |
| C. Boot-ROM SD boot, AE-L + Fn1 | C: X-T1 key table (stream1 0xAF5400): AE-LOCK (0x25) = GPIO0.10, FUNCTION2/Fn1 (0x224) = GPIO0.11. F: fujimod xt1 controls.json boot.sd = AE-L + Fn1 | All 10 cataloged EXR-II bodies use the two controls on GPIO0.10 + GPIO0.11 (X-E2/E2S dial push + Fn7, X100T dial push + Fn1, X30 dial push + View, X70 control + Movie, X-A1/A2/M1 sub-dial + Fn, X-T10 dial push + Delete). One ROM hash for EXR-II (X-E2/X-A2/X-T1/X-M1/X-T10). Hardware: only X70 passes sd-select / sd-boot. Every X-T1 SD stage not-run | Keys: medium-high. Works on X-T1: medium |
| D. Green-LED card | Bindings exist for xt1-552; LED descriptor hash-identical to X-E2 4.11. CORRECTED 2026-11-04 (helper/trace_led): the loader GPIO port-15 record differs from X-E2/X-T10 and X-A2, and X70 uses a different lamp port (2 vs 15); only the descriptor matches | fffw xe2-start.S / xe2-led0-main.c: power hold (1 -> 0xfff88084), blink LEDs via 0xfff893c0/c4 forever. fujimod green-led-arm.bin (disassembled): masks IRQs, <=24 register writes in 0xf0000000-0xfffff000, then WFE forever. No NAND access, no firmware calls; exit = battery pull. Hardware pass: X70 | No flash risk: high. Lights on X-T1: medium |
| E. SD reflash of official firmware | Recipe complete; updater accepts equal version, so card needs FWUP0005.DAT = 5.52. X-T1 sd-recovery / emulator-updater not-run | ffre: "partially tested only on XE2", abandoned. fujimod sd-recovery not-run on every model incl. X70; emulator-updater passes only on X70. Newer-body cards booted X-S10, X100V, X-Pro3 | Low-medium |
| F. DISP/BACK update + cfg 0x22e gate | C: request 0xc only if 0x22e bit 1; default 0x22e=0x0f, 0x1c0="0005" | fujimod: DISP/BACK on all EXR-II. C: X-A2 / X-T2 defaults 0x22e=0x0f, 0x1c0 "0006"/"0010" matching their FWUP names. Gate code traced on X-T1 only | Entry high, gate medium-high |

## Conclusions
1. X-T1 SD-boot keys sit on the same two GPIO pins (0.10, 0.11) every EXR-II body uses; confirmed in X-T1 firmware. No ROM dump to prove the ROM samples them.
2. EXR-II SD boot confirmed on hardware only on the X70; reflash-from-SD not run on any EXR-II camera.
3. Green-LED card touches no NAND and calls no firmware; holds power until battery pull: right first test.
4. KEY_JIGBOOT (0x33f) is a debug key "JIG" sent over ff80, not a cold-boot path; the "test pad" guess is unsupported.
5. X70: high-performance standby skipped SD boot until the battery was pulled - keep the battery pull in the X-T1 procedure.
6. No UART/JTAG/test-pad/USB-ROM-boot notes in any local source; nothing contradicts our findings (power-hold 0xfff88084 bit 0 and LED 0xfff893c0 match).
Sources: tools/fujimod/catalog/{bodies,progress,recovery}/, tools/fujimod/crates/recovery/, tools/fffw/{README.md,boot/,ff80/,ffun/ports/xe2/0411/bootrom.h}, out/{xt1_flash.bin,stream1_decoded.bin,xa2_130.bin,xt2_441.bin}.
