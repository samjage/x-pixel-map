# NAND save / load trace (offline, helper report; [C]=read in decompile, [D]=deduced, [G]=guess)
Dumps in this folder: dump_*.txt, callers1.txt, run.sh (headless against a COPY of the Ghidra project).

## Layout
- NAND: 2048 B pages, 64 pages per 0x20000 block, 1024 blocks. [C]
- Config image = FTL logical blocks 0x29..0x49 (33 blocks); FTL region = physical 0x37a.., 134 blocks, 114 logical, ~20 spare. Mapping is RAM-only, rebuilt at boot from spare tags. [C/D]
- FUN_0088076c ("block+7") is NOT a second slot: raw read of absolute NAND blocks 7..9 = the factory-default config shipped in the firmware (out/xt1_flash.bin @0xE0000). It has magic 55AA at +0x202 but all list sizes (0x28e0..0x2903) are 0. Only the firmware-update path writes it. [C/D]

## Save (FUN_00880c90 -> FUN_0087f94c -> FUN_008fef0c -> FUN_00901a80)
- One synchronous FTL write, verify=1. Per logical block ascending: take a free physical block, program 64 pages, read every page back and compare, then update the map and erase the old block. Program fail or verify mismatch: mark new block bad, retry on another free block. No free block: error -0x28, else -0xff. [C]
- ECC is hardware (BCH in the NAND controller); spare tag = logical block number + parity, stored twice. Exact BCH strength is [G].
- FUN_011e9d68 (the service-mode save handler) ignores the save result. [C]

## Power-fail
- Each 128 KB block is crash-safe (old copy erased only after new verified; mount erases blocks whose first/last page tags disagree). [C/D]
- The 33-block save is NOT atomic: no two slots, no commit marker, no checksum. A power cut gives a mixed old/new image, and block 0 (header + list sizes) is written first while the lists are in later blocks. [C/D]
- A mixed image still loads (see below). Re-running a full save rewrites all 33 blocks.
- Pulling the battery after the save command has returned OK is safe; expected save time ~1 s ([G]; the "5 s" is only a guess).
- If FTL mount fails at boot the camera hangs in an infinite loop. [C]

## Load (FUN_00874c24)
- Reads logical [0,0x76800) and the last 0xfd00 bytes; return codes ignored. Only validation: u16 at image+0x202 == 0xAA55. No version/size/CRC. [C]
- Bad magic: zero the image, reload raw factory copy (blocks 7..), if still bad FUN_00874b64 zeroes it and applies a 125-entry patch table (no magic in it, lists empty). Nothing reloads lists from CORRDATA (partition 6) ([G], static xrefs only). [C/G]
- Always forced after load: [0x204]=02, [0x205]=0x39, [0xd8]=0x80, [0x4069..0x406e]=02 70 02 70 02 70. [C]
- Lists loaded separately by FUN_00875334 from the six offsets; sizes from the 3-byte counts at 0x28e0..0x2903. If base[0x244]==1 it also overwrites [0x3000,0x42000) from raw block 7 (byte is 0 in our dump). [C]
- On a failed load the lists are empty in RAM; the NEXT save would overwrite the NAND lists with zeros. The factory copy cannot restore lists.

## Implications for the write test
- Biggest risk is an image whose u16 at 0x202 is not 0xAA55 (everything incl. lists discarded at next boot), not NAND tearing.
- No second on-camera copy of the lists. Keep camera/cfgdata_dump_BACKUP_original.bin.
- Our edit tool changes no bytes outside the list regions and counts, so 0x202 and 0x244 stay as in the backup.
