# X Pixel Map tool (`xpixelmap`): Fujifilm X defect-list repair (X-T1 fw 5.52)

Python 3 standard library only (the emulated camera additionally needs `unicorn`, which exists only in the
`xt1-emu:burner` container). Nothing in this tool has ever been run against the real camera.

## Safety rules built into the tool

- **Five camera operations, nothing else.** `Transport` exposes exactly `info()`, `config_size()`,
  `read(off, n<=0x100)`, `write(off, bytes<=0x100)`, `save()`. Subclasses cannot add public methods or override
  these (enforced when the class is created). No syslog, no RAM read/write, no firmware upload, no debug-byte
  changes, no other app80 commands. A test parses `real.py` and fails if it calls any other ffjlib function.
- **Write allowlist.** Every write is checked before it is sent (and again inside `RealTransport`): 1..0x100
  bytes, inside 0..0x41ffff, never touching 0x80..0x83. Reads: 1..0x100 bytes inside the image.
- **Save = the real app80 save only:** params `ff 00*31` at 0x81, then 33 bytes `22 ff 00..` at 0x80
  (exactly `ffjlib.app80_request(0x22, [0xff])`), then wait and read the 33-byte response at 0x80. Success only for
  `22 00`. This is the only code path that writes 0x80..0xa0.
- **Session order is enforced** (one session = one power-on in service mode): identify (product must be `X-T1`,
  size 0x420000/0x100) -> full backup read (must equal the base image, normally the known backup) -> offline pre-save
  checks -> write the FULL image in 0x100-byte chunks (16,897 writes; 0x80..0x83 skipped, and only if the camera's
  bytes already equal the image's) -> full read-back, sha256 must equal the image -> save only after a typed
  confirmation phrase `SAVE <sha12> TO CAMERA` at an interactive terminal. Any error taints the session: no
  further write or save; power-cycle and start again.
- **Pre-save checks** (`image.presave_checks`): size; 0x202 = 55 AA; 0x244 = 0; 0xf7 = 0; 0x80..0x83 equal the
  base; 0x80..0xa0 = `22 ff 00..` (what the save leaves there, so flash == image); ALL SIX lists, using the counts
  in the image, inside their own gap (0x76800<0x11c840<0x2bb8e0<0x3b4940<0x3b8ba8<0x3c31b0<0x400000; the boot
  loader itself would read up to 0x520000); zero padding bits; every record passes the factory invariants (x, y
  ranges, bits 12/16/29 clear: catches off-by-one counts); filler after a shrunk list is 0xff; lens block
  0x3e0000..0x3fffff unchanged; nothing outside list spans and count fields differs from the base.
- **Never overwrites:** backups and builds are opened with `xb`; `build` refuses the backup and existing files.
- **RealTransport** is only constructed with `--real` (`RealTransport(real=True)`), refuses when
  `XPIXELMAP_NO_REAL` is set, and imports `usb1`/ffjlib only inside its constructor.
- **Journal:** each session gets `data/sessions/<UTC>-<cmd>/journal.jsonl` (append-only, fsync before each step is
  sent) and `state.json`. `status` prints the events and what is true now (nothing sent / RAM dirty, not saved /
  RAM verified, not saved / SAVE OUTCOME UNKNOWN / saved), with the next step.

## Usage

```
python3 -m xpixelmap build --pixel 2497,1861 [--out new.bin]      # new file via helper/edit_lists.py + checks
python3 -m xpixelmap verify new.bin [--pixel 2497,1861]            # offline checks + emulated round trip (docker)
python3 -m xpixelmap backup --real                                 # full read, new file, compare with known backup
python3 -m xpixelmap write  --real new.bin                         # write + read-back verify, NO save
python3 -m xpixelmap save   --real new.bin                         # write + verify + typed phrase + save
#   then: wait, battery out/in, power on, service mode again:
python3 -m xpixelmap backup --real --expect new.bin                # flash really holds the image?
python3 -m xpixelmap restore --real [--allow-trigger-diff]         # write + verify + save the known backup
python3 -m xpixelmap status [session_dir]
```
Every camera command needs `--emu` or `--real`. `--emu` (container only) runs the same commands against the
emulated camera; `--emu-flash-state FILE` keeps its flash between commands, `--yes-emulated` auto-confirms.
`--allow-trigger-diff` (restore only) allows camera bytes 0x80..0x83 to differ from the backup (e.g. after a
fallback to factory defaults they read 00 00 00 00); the read-back then ignores those 4 bytes because the app80
save command itself rewrites them to `22 ff 00 00`. Output goes to `xpixelmap/data/` unless `--data-dir` is given.

Tests (inside the container, project mounted read-only):
```
docker run --rm --network none -v "$PWD":/proj:ro -w /proj xt1-emu:burner \
    python -B xpixelmap/tests/run_all.py
```

## What is tested (emulated camera = the camera's own firmware code in unicorn)

Write/read handlers FUN_010e92f8/FUN_010e93a4, the app80 trigger path (message 0x1707), the 0x22 handler
FUN_010df4fc -> FUN_008758bc -> FUN_00880c90 -> status writer FUN_010dccd0, boot FUN_00874c24 + list loader
FUN_00875334 (including the factory-default fallback via raw NAND blocks 7..9 from out/xt1_flash.bin), per-shot
setup FUN_0116a1e4. Tests: allowlist and class-level command restriction; happy path one pixel ->
write -> verify -> save -> reboot -> backup == intended (per-shot counts 3072 / 17504) -> restore -> backup;
ordering refusals; chunk layout; USB error / timeout / Ctrl-C mid-write; errors during read-back; corrupted
transfers (write and read); corrupted backup read; power loss during save after 0,1,2,5,16,32,33 blocks, then
restore; power loss during the restore's own save (D1r) then a second restore; flash write error (the 0x22
handler passes it on: response `22 01`, journal `save_error`, then restore); corrupted images (marker, count fields incl. overflowing counts, stray byte); wrong
camera (serial) and wrong model; restore from factory defaults; CLI end to end. RealTransport is never
instantiated (tripwire on `__new__`, checked by the runner) and no USB module is imported.

## NOT tested / not covered (real hardware only)

- RealTransport has never run. The USB layer, ffjlib reply formats, error codes, timing of the save response and
  whether the camera keeps answering reads while it saves are untested. `save_wait` = 10 s is a guess.
- Stubbed in emulation: raw FTL block I/O (each 128 KB block is written whole or not at all; real torn pages,
  bad blocks, ECC and the FTL mount are not modelled), RTOS message posting, the app80 dispatcher (the 0x22
  handler is called directly when the trigger posts 0x1707), the USB transfer itself.
- Modelled: the lens block 0x3e0000..0x3f7dff is copied from flash at boot (its loader is not in the emulated path).
- An FTL-level failure (camera no longer reaches service mode) has no on-camera recovery; restore cannot help.
- In emulation the 0x22 handler reports a stubbed FTL error as status 1, but the real FTL/NAND error paths are not
  emulated; only `backup --expect` after a reboot proves the flash content.
- Whether the PFP hardware accepts the edited list (ordering tolerance, aux/flag guesses) is unknown until a real
  save + dark frame.
