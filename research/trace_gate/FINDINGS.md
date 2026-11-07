# cfgdata write (op 0x1a0002 sub 3) — trace results

Method: copied ghidra/xt1research.gpr+.rep to a scratch dir, ran headless
Decomp/Callers/Probe/FindArg scripts against the copy (never touched the
locked live project). Scripts: helper/trace_gate/{Probe,FindArg}.java (new).
Full decompiles: helper/trace_gate/decomp_stream{1,2}_gate.txt.

## (a) Handler function — CERTAIN
The three literal hits for 0x1a0001/0x1a0002 in stream1 at 0xa91974/0xa5ee30/
0xaf5448 (NOTES.md) are **false positives**: byte-dumped, they are plain
int16 coefficient/kernel tables (0xa91974) and small signed-delta tables
(0xa5ee30, 0xaf5448); the "opcode" is just two adjacent unrelated int16s.
No opcode→handler table was found this way, and brute-force scans of both
streams for the 4-byte patterns elsewhere hit only unrelated data (a jig
sprite/format table around 0x1380000-0x13c0000) or coincidental bytes
inside unrelated code (e.g. a sprintf-style formatter at stream2 0xf037dc),
not a dispatch table.

What **is** certain (found via decompile, stream2 = base 0xeff120):
- stream2 **0x013c7fc8**: a 0x4c-byte template holding 4 function pointers:
  `010e92f8`(write) `010e93a4`(read) `010e9588`(commit) `010e95b4`(block-by-id),
  rest zero. Immediately followed at 0x013c8014 by a 0x10-byte param
  template `00000000 00000000 00420000 43dcc800` (size=0x420000).
- **FUN_010e9248** (pipeline init) copies that block into a RAM struct and
  patches slot0=image-base-ptr (`func_0x008756cc`), slot3=ptr from
  `func_0x008801cc`. Matches the prompt's description exactly.
- Call chain: **FUN_011e7c38**(`DAT_011e7c60`) → FUN_010e9248; called from
  **FUN_011e64e0**, which also calls `FUN_010a34c0(FUN_011e8128, 3,
  DAT_011e653c, +0x4c, …)`, an RTOS "register" primitive (posts kernel msg
  0x3403), reached only via the module-init pointer table at 0x013f0db4..cf8.
- **Gap (honest)**: the indirect call turning incoming f_code (1/2/3) into
  `vtable->write/read/...()` goes through a runtime device-handle, so Ghidra
  shows **zero** code xrefs to 0x010e92f8/93a4/9588/95b4 besides the
  vtable's own data words (checked with Callers.java). That generic
  dispatcher was not located; everything inside the 4 callbacks themselves,
  below, is a direct, certain decompile.

## (b) Gating — write handler itself does NOT check the 0xf7 debug mask
FUN_010e92f8 (full decompile in dump) does: bounds-check
`(len-1)+offset <= 0x41ffff` → else return **0x80000000**; then
unconditionally writes every byte via `FUN_008756a8(addr,byte)` (plain
`*(_DAT_00c285bc+off)=byte`, no checks at all). **No reference to offset
0xf7, or any lock/mode global, anywhere in this function.** Read
(FUN_010e93a4) and commit (FUN_010e9588) are likewise free of such checks.
FUN_008756a8/FUN_00875310 (the byte accessors) are one-line pokes/peeks,
also unconditional (CERTAIN).
Byte 0xf7 itself: searched all call sites of FUN_00875310(0xf7) in both
streams (10 in stream1, 3 in stream2, via helper/trace_gate/FindArg.java).
Every hit is in USB-cable-connect/enumeration logic (FUN_00845f44/
FUN_00845ca8/FUN_00921364 in stream1, FUN_011e79c0/FUN_011e31a4/FUN_011e7b40
in stream2), choosing which USB class to present and whether to arm a
0x100000-byte debug/postmortem trace buffer at 0x437bc800 — 0xf7 is a
tri-state (0/1/2) debug-log level checked **at cable plug-in**, not per
command. So "debug must be enabled" (fffw's comment) most plausibly means
0xf7 must already be set for the ff80/jig interface to enumerate at all —
a session precondition, not a per-write check (GUESSED interpretation;
the enumeration-time check itself is CERTAIN).
Error-code shape when the *bounds* check fails: firmware returns raw
0x80000000; how that maps to the wire (errcode1,errcode2) pair that
ffjlib._mem_op raises on is not traced (GUESSED: probably errcode1≠0,
generic failure).

## (c) param_1 in FUN_010e92f8 — mostly certain
param_1 = the "mb" flag from write_config_data (ffjlib.py: `mb=1` default).
It does **not** gate the byte-copy loop (always runs). Its only effect:
when param_1≠0 **and** offset==0x80 exactly, after the copy it re-reads
byte 0x80, calls FUN_010dcec0() (table lookup over DAT_013c60b8, stride-6
records — purpose unclear, called here with no visible argument, GUESSED
to be some "apply/ack" check), then `FUN_011e9930(0xff, byte@0x80)` which
posts kernel message **0x1707** — the SAME message id NOTES.md identified
in the config-image save/load FSM (FUN_011e5a28/FUN_011e5028, msgs
0x1706/0x1707/0x1708/0x1709/0x13fa). So writing offset 0x80 is a distinct
"app80" trigger byte (matches ffjlib.app80_request: write params at 0x81,
then trigger at 0x80) that pokes the cfgdata FSM — unrelated to ordinary
data writes elsewhere in the image (CERTAIN mechanism, GUESSED purpose).

## (d) Validation / side effects — CERTAIN for the general case
Bounds: yes, `offset+(len-1) < 0x420000`, else 0x80000000, no partial write;
no length cap beyond that (host already limits chunks to ≤0x100 bytes). No
dirty flag, no checksum, nothing else touched for a normal offset (≠0x80):
only the RAM image bytes change. No automatic flash save — that only
happens via the explicit `cfgdata save` command (stream2 FUN_011e9d68 →
stream1 `func_0x008758bc` → `FUN_00880c90`, NOTES.md phase-0 finding,
reconfirmed here). The only exception is the offset==0x80 special case in
(c), unrelated to generic writes (the planned hot-pixel-list write at
0x76800+ is unaffected by it).
