# Hot pixel scan of the "Jan/Feb 2026" RAF set

Method: helper/hotscan.py logic (excess over the brightest of four same-colour neighbours at +-6 px, threshold 250 DN), reused via scan.py. The path list was parsed as "date size path" (paths are relative to a backup disk; all 115 exist and are X-T1 RAFs). Read-only. Scanned all 115 frames, plus a 24-frame evenly spread subsample (the NOTES.md style).

IMPORTANT caveat on the set: all 115 frames are from ONE day, 2026-02-27 (file date and EXIF date), DSCF0350..DSCF0434-ish. Not spread over Jan/Feb. Exposure: ISO 200 (109 frames) or ISO 400 (6), shutter 1/4000 to 1/250 s. No long exposures and no ISO >= 1250, so only list0 (short, low ISO) territory is tested.

## Result: pixels hot in >= 40% of frames (RAF coordinates)
| pixel (RAF x,y) | all 115 frames | 24-frame sample | mean excess | in list0 | in list2 |
|---|---|---|---|---|---|
| (2496,1859) | 92/115 = 80% | 18/24 | ~720 DN | no | no |
| (4346,2269) | 57/115 = 50% | 12/24 | ~360 DN | no | no |

Nothing else reaches 40%. (List x = RAF x - 4; checked via the raf_x/raf_y columns of decoded_list0/2.csv; no listed pixel within 2 px of any of these either.)

## Known candidates
- (2496,1859): hot, 80%, not listed. Persistent (matches 2025-11 and 2026-08 scans).
- (4346,2269): hot, 50%, not listed. Persistent but intermittent.
- (2497,1861): NOT hot here: 2/115 (excess 265, 289), median -57. Not listed. Neighbours also clean. So it is absent in Feb 2026 and only appears later (Oct 2026 / dark frames), supporting that (2496,1859) and (2497,1861) are separate pixels, not one defect with a shifted detector.
- (2317,1649): 2/115 (excess 296, 726), median -70. Not listed. Sporadic, not a stable hot pixel in this set.
- (470,2352): 4/115 (excess 301-801), median -112. Not listed. Sporadic; its 3x3 neighbours also fire a few times, so these look like scene content or noise spikes, not a defect.

Per-frame excess for these and their 3x3 surroundings: detail.csv (see below).

## Files
scan.py (scan), detail.py (known candidates), summ.py (summary and results.csv), results.csv, detail.csv, frames.csv are all in this folder. Scratch outputs: .../scratchpad/janfeb/.
