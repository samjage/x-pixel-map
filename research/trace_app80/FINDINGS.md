# app80 (jig/service) command set (offline helper trace, 2026-11-03; CERTAIN = read in decompile, GUESSED = inferred)
Full 150-command table: command_table.txt (this folder). Ghidra ran on a copy.

## Dispatcher - CERTAIN
- PC writes params to cfg 0x81.., then cfg 0x80 = command byte. Write callback stream2 FUN_010e92f8 validates via FUN_010dcec0 (table @013c60b8), FUN_011e9930 posts msg 0x1707 -> FUN_011e5a28 -> FUN_011e5028(2) (FSM) -> FUN_011e67c0 -> FUN_010dce2c(cmd) = dispatcher.
- Table @013c60b8, 6 words per record: [cmd, flag, handler, descptr, modemask, newmode]. Match needs cmd AND (mode global DAT_0146a7f0 & modemask); newmode != 0 sets the mode; then handler(descptr). descptr word0 = cfg offset of the params (0x81 typical).
- Status via FUN_010dccd0 (0 = OK, 0x80000000 = error), read back by app80_response (cfg 0x80, 33 bytes). Example: 0x22 = cfgdata save (FUN_010df4fc).

## Jig shots 0xa4/0xa5/0xa7/0xa9 - mechanism CERTAIN, parameters GUESSED
- All map to handler 010e558c -> FUN_010e5538 -> FUN_010e54c4, which triggers one shot (op 0x88/0xa0 on image-pipeline port 0x82 via FUN_010e53f0 / FUN_010d53c4, waits FUN_010dcbec).
- During the shot flag _DAT_00be3b64 is set (FUN_011de140); per-shot setup FUN_0116a1e4 calls FUN_011651c4 -> 1 (a5) / 2 (a7) / 3 (a9). `if (ret == 3) return 1;` => 0xa9 = defect correction SKIPPED (raw frame); 0xa5/0xa7 = corrected (pair chosen by the Tv/ISO logic). a5 vs a7 difference not found.
- Exposure/ISO/gain come from setup command 0xa0 (FUN_010e4b98): struct @0x1756978 (const 0x708 etc.), 0x7b4-byte block from 013d2c74, cfg 0xc1/0xc2 (gain/ISO class via tables 013c7c64 / 013c7b18), cfg 0x5a0f..0x5a12, 0x14b..0x14e.
- Image RAM buffer address not pinned. GUESSED: PC reads it via debug RAM read (op 0x200001, needs debug byte 0xf7) or 0x1a0001; no dedicated app80 read found.
- Mode flow: 0xa0 -> mode 8; 0xa3 toggles 8 <-> 0x10 (FUN_010dcb08); a5/a7/a9 need mode 0x10.

## In-camera defect detection - CERTAIN: none
- Counts 0x28e0..0x2903 are only READ (FUN_00875334 loader, FUN_0116a1e4 per-shot setup). No count writer, no 34-bit packer, no sort routine in either stream; list bases appear only via the read accessor FUN_00875710.
- => The service PC shoots corrected (a5/a7) and uncorrected (a9) frames, detects defects itself, writes the finished lists + counts into the RAM config image with ordinary cfgdata writes (op 0x1a0002), then commits with cfgdata save (0x22/0xff -> FUN_011e9d68 -> FUN_008758bc).

## Readback / other
- 0xa6 (FUN_010e51a0) waits for a 0x10-byte result (event 0x105) and returns it (GUESSED: result/status read).
- 0x92 (FUN_010e42ec) forwards framed packets to a sub-processor (CRC16 protocol, cmds 0x97-0x9c).
- Groups 0x00-0x36 (cfg getters/setters, modemask 0x5f), 0x40-0x74 (modemask 0x18), 0x80-0x95 (sub-device/pipeline), 0xc0-0xcc, 0xd0-0xde, 0xe0-0xeb (small getters): listed, not decompiled.
- False positives ruled out: FUN_01175150 (crash/telemetry buffer, markers "UMTS"/"UMAL"), FUN_010a1b18 (generic FSM step).
