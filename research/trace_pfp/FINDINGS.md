# How the defect lists are used (offline helper trace, 2026-11-03; CERTAIN = read in decompile, GUESSED = inferred)
Dumps: d_*.txt, r_*.txt, c_*.txt, run.sh, DecompAt.java (headless on a Ghidra COPY).
Accessor index n of FUN_00875710(n): n0=0x76800 ("list0", A 3071/B 1038), n1=0x11c840 ("list2", 17503/5991), n2=0x2bb8e0 (empty), n3=0x3b4940 ("list1", 32), n4=0x3b8ba8 ("list3", 222), n5=0x3c31b0 (empty). Pairs: P0=(n0,n3), P1=(n1,n4), P2=(n2,n5).

## 1. Fresh or cached - CERTAIN: read fresh every shot
- Per-shot setup FUN_0116a1e4 calls FUN_00875710 (base _DAT_00c285bc + constant) and FUN_00875904 (count bytes) each call; also re-reads cfg 0xb4, 0xc9, 0x2906 and threshold bytes. Copies (DAT_0156da38, *DAT_010cb534) are rewritten each call.
- BUT a RAM-only edit cannot be tested by shooting: boot mode 7 (jig) skips REC start-up (stream1 FUN_009248ec, FUN_00924ec4, FUN_00924d10: FUN_00923f60(0x88), FUN_0092404c(0x88), FUN_00923ee8(0x88), FUN_00923954, FUN_00923b6c run only for mode 1). Leaving jig mode needs a power cycle; boot reloads lists from NAND (FUN_00875334). So an edit only takes effect after `cfgdata save`.

## 2. Which pair is used - logic CERTAIN, ISO boundary GUESSED
- Decision at 0x116a2f8. Tv = base+0x670 in 1/100 EV (0 = 1 s; FUN_00918f98, table 0xadba34). ISO class from Sv base+0x76a via FUN_00919438, table 0xadbc90, cfg u16 0x1902..0x1916 (= 400,500,...,1400 in our dump).
- Thresholds (signed bytes, per ISO class i): t0=0x25c0+i, t1=0x25cb+i, t2=0x25d6+i; second set 0x25e1/0x25ec/0x25f7 when base+0x4a8 != 0. 0x7f/0x80 are sentinels.
- Rule: if Tv > t0*10 -> no defect correction; else if Tv > t1*10 -> P0; else if Tv <= t2*10 -> P2; else P1.
- Our values: t0 = 127 for all classes; t1 = 0 for classes 0-4, 127 for 5-9; t2 = -128 everywhere (both sets).
  => shorter than 1 s at ISO classes 0-4 -> P0 (list0 + list1); 1 s or longer, or ISO classes 5-9 -> P1 (list2 + list3). P2 never used.
- ISO <-> class mapping GUESSED (helper: <=1600 -> P0, >=2000 -> P1).
- Correction jobs 7-10 (FUN_011bd0a4 -> FUN_0116a1e4) in still sequences; jobs 8/10 skip the sub pass. CKOU (kouhatsu) is job 0xd (FUN_011bd364), separate, does NOT use these lists. Jobs 5/6 = recbfCorrectPhase (FUN_0116d520). Movie/live view not traced.
- Main pass needs cfg[0xb4]&4 (mode 1), sub pass cfg[0xb4]&8 (mode 3); either becomes mode 4 if the matching cfg[0xc9] bit is set. Our dump: 0xb4=0x3d, 0xc9=0.

## 3. DMA - CERTAIN: hardware reads the image directly
- FUN_010cb46c -> stream1 FUN_0086d5a8 writes list address to PFP register 0xfff1a00c (& 0x3fffffff), count to 0xfff1a010 (max 0x3ffff). Address must be 8-byte aligned (driver errors otherwise) - this is why A is padded to 64 bits; B start must stay 8-aligned too.
- Cache: GUESSED low risk (0x41edd800 is the uncached alias; func_0x00042634(0,0) runs before PFP start).

## 4. Capture in jig mode - GUESSED: none found
- Ops 0x550001/0x760001 are USB loopback tests. Op 0x200001 sub 5 (send key / change mode) needs debug byte 0xf7; unknown whether it can reach REC.
- Hint of a factory adjustment-shot path: FUN_011651c4 returns 1/2/3 when flag _DAT_00be3b64 is set and cfg[0x80] (app80 command byte) is 0xa5/0xa7/0xa9; value 3 makes setup skip defect correction. Flag set by FUN_011de140 (jig-side FSM action), cleared by FUN_010e92e0. Not traced further.

## 5. What aux and flag do - table/registers CERTAIN, index mapping GUESSED (fits all factory records)
- stream1 FUN_0086d664 loads a 128-word weight table from stream2 0x0146bcf0 into PFP 0xfff1a030. Each word: four 6-bit neighbour weights (sum 32 or 0) + 4-bit neighbour-pattern id.
- Index = cls*16 + flag*8 + aux. All 27,857 factory records hit non-zero entries; other bit orders hit ~9,200 zero entries.
- cls/flag pick the neighbour layout (cls0 -> pattern 0, flag -> 1; cls1 -> 3, flag -> 2; cls2 -> 8; cls4-7 -> 4-7). aux picks weights: aux 0 uses all neighbours; aux 1-4 drop one neighbour; cls2 aux 5+ drops two. I.e. aux = which neighbour to leave out (a phase-detect or defective neighbour).
- For a new isolated hot pixel: aux = 0, flag from the position model.
