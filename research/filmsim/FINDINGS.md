# X-T1 film simulations (offline helper trace, 2026-11-04; CERTAIN = read in code/data, GUESSED = inferred)
Scripts and dumps in this folder: fsimtables.py, tonecurves.py, modetbl.py, availtbl.py (+ *_out.txt), run.sh.

## Verdict
- Remix an existing simulation through the CONFIG image only: partly possible (inferred, untested) - its selector bytes can point at other presets the firmware already has (e.g. ASTIA's columns at Classic Chrome's presets). No new curves or colour data.
- New simulation / spare slot / new curves or colour presets / new menu entry: needs a FIRMWARE patch.

## 1. Ids (CERTAIN)
MODE_FSIM: STD/PROVIA=0, F1b/ASTIA=1, F1c=2, F2/Velvia=3, B_AND_W=4, SEPIA=5, CHROME=6, MONO_N=7, MONO_R=8, MONO_Y=9, MONO_G=10, NEGA_COL_1/PRO Neg Std=11, NEGA_COL_2/PRO Neg Hi=12, CLASSIC_CHROME=13 (14 values; same as X-E2 header).
- Setting 0x191 = film simulation; 0x193-0x195 = film-simulation bracket slots. Conversion table stream1 0x9fd264 (lookup by setting id at 0xae75bc; FUN_007cd39c): stored value, Wi-Fi id, MODE_FSIM, PTP 0xD001 value.
- Names: 0x7ab884 (MODE_FSIM_* strings), 0x789d4c (UI_FILM_SIM_*).
- Stored as a 4-bit field in RAM settings struct 0xc27d64 (+bank*0x424 + (slot+8)*0x40), defaults at 0xa7c744; current value global eModeFsim 0xc31530. Whether that struct is saved into cfgdata: UNKNOWN.
- Hidden: F1c, CHROME, B_AND_W (no Wi-Fi/PTP id, off in every mode in availability table stream1 0xa296a0; rows = value + 8 per-mode flags 2 default / 1 allowed / 0 off; FUN_007cd550).

## 2. Pipeline (CERTAIN unless marked)
- eModeFsim -> capture request -> stream2 shot struct +0x50c (FUN_011dae8c). Maker-note FilmMode tag built in FUN_011c5f7c.
- FUN_01099424 maps simulation -> 10 "classes" (IP state +0x108): 0 STD+F1c, 1 CHROME, 2 all monochrome, 3 SEPIA, 4 Velvia, 5 ASTIA, 6 PRO Neg Std, 7 PRO Neg Hi, 8 Classic Chrome, 9 never produced. Mono filter colour -> +0x114 (FUN_0109953c).
- Per-class parameter readers in the IP library (~stream2 0x1089000-0x1092000): FUN_010902c8 tone curves, FUN_0108b4d0/b538 matrix preset, FUN_0108b634 saturation, FUN_0108c1b0 hue preset, plus FUN_0108d434, d9f4, fce8, 01091188, 01091ad0, 01091e00. Each reads a CONFIG byte at an offset from a u16 table (stream2 0x1465b48-0x14691a8, 10 class columns x 2 rows): a direct value or a firmware preset number.
- Tone curves: config tone preset (Velvia=2, ASTIA=3, Std=4, Hi=5, Classic Chrome=59) -> second config map (offset table stream2 0x1468278, config bytes 0xcb77-0xccde) -> curve id. Curves: firmware table stream1 0xaf45e8, 42 records x 0x48 bytes (9 spacing bytes + 28 u16 knots 0..16383), count at 0xaf45e0. Not in cfgdata; never written by code.
- Colour/hue/matrix presets: firmware tables stream2 0x1465bcc (10 x u16 per preset), 0x1465c6c, 0x1465d10 (4 x u16 per preset).
- No 3D LUT indexed by film simulation. TCUBE/TCPR (driver FUN_0117bc40) appears tied to advanced filters (recbfAdvFTcubePRProcess) - GUESS.

## 3. Where things live
- CONFIG (CERTAIN, inside the boot-loaded range < 0x76800): per-simulation selector bytes ~0xa9cc-0xcf21, e.g. matrix preset 0xb0c7-0xb0d0 and 0xcec2-0xcec5; hue preset 0xb3fd-0xb406 and 0xceca-0xcecd; tone preset 0xccdf-0xccfc and 0xcef4-0xceff; tone-preset->curve map 0xcb77-0xccde.
- FIRMWARE (constant): curves and preset coefficients.
- CHROME uses exactly STD's selectors; B_AND_W = plain monochrome class.

## 4. Slots
- Stored field has 16 values, 14 used (14, 15 free). IP library class 9 is a spare slot that already has its own config bytes (~0xcec3-0xcf21, distinct matrix presets 12/14); nothing maps to it without patching FUN_01099424. A menu entry also needs rows in 0x9fd264 and 0xa296a0 plus menu/name tables (firmware).

## 5. X-T2
- out/xt2_441.bin is X-T2 firmware payload (not a config). Its streams list MODE_FSIM STD..CLASSIC_CHROME, then ACROS_N/R/Y/G = 0xe-0x11, MODE_FSIM_NUM = 0x12; no Eterna (X-T4 header has Eterna = 0x12). No X-T2 config image found.

Note: the helper's scratch Ghidra copy overwrote an older scratch copy in the scratchpad; no project file changed.
