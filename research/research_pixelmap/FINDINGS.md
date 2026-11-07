# Public info on Fuji pixel mapping (web research, 2026-11-03)
Labels: [OFFICIAL] service manual/user manual/patent, [FORUM] anecdote, [INFER] inference.

## Service procedure (X-Pro1 service manual, rev. 13 Apr 2012 - closest public analogue; nothing public for X-T1/X-E2/X100S)
- [OFFICIAL] PC software "X-Pro1_W Adjustment Software" run by HyperFFW, needs a "DSC jig driver". https://www.manualslib.com/manual/2198832/Fujifilm-X-Pro1.html?page=65 (also ?page=67, ?page=70)
- [OFFICIAL] Jig mode = power on holding Shutter + Up (LED green), USB, battery jig at 7.20 V. Items include [F4] "CMOS Data" = CMOS Defect Correction, [F5] Camera, [F8] Firmware Download, [L]/[H] Level at normal/high temperature, [I] Main PWB Initialize, [F12] End Setting (writes repair-site ID). Order after a board swap: I > F9 > L > F4 > F5 > H > E > F1 > F12. ?page=72, ?page=73
- [OFFICIAL] CMOS Defect Correction (p.3-24/3-25, ?page=84, ?page=85): darkroom or black box, no fluorescent light, no vibration; Level adjustment first. Equipment: PTB450F pattern box at 160+-5 cd/m2, 6100+-50 K (10 min warm-up), LB140/LB145 filters, XF35mm F1.4 jig lens, tripod/X-Y stage, Minolta LS-110 and Color Meter IIIF. The PC drives the capture; ends with "CMOS Defect Correction completed". Fails more often as camera temperature rises.
- NOT public: ISO, exposure time, frame count, thresholds.
- [OFFICIAL, snippet] X-M1 jig list has "CMOS Defect Correction" and "CMOS Dust Inspection", "Common with the X-Pro1". https://manualmachine.com/fujifilm/xm1/24356556-service-manual
- [OFFICIAL, snippet] Some repairs erase "all the adjusted data in our factory", so the full sequence must be redone. https://www.scribd.com/document/559077643/X-Pro1tg
- [OFFICIAL, older] S3 Pro "CCD Post-shipment Defect Correction adjustment Manual": correction data for white scratches appearing after shipment is written alongside the factory data, not replacing it. https://manualmachine.com/fujifilm/finepixs3pro/9136066-correction-adjustment-manual
- [OFFICIAL patent] US8023012 (Fujifilm, 2004): two defect tables, a usual one and a long-exposure one applied only above an exposure-time threshold. https://patents.google.com/patent/US8023012B2/en
- [Community] fffw wiki: app80 service functions can compensate "dead pixels and even sensor scratches"; settings go to RAM cfgdata and are committed with `cfgdata save`. https://github.com/tiredboffin/fffw/wiki/Route-0xff80
- [INFER] X-T1 tooling likely the same "<model>_W"/HyperFFW family; defect step likely combines dark capture (hot/white) and a flat 160 cd/m2 capture (dark defects).

## In-camera PIXEL MAPPING (X-T2 and later; X-T1 has none)
- [OFFICIAL] For "bright spots", takes a few seconds, "Results are not guaranteed", needs full battery, unavailable when camera is hot. https://www.manualslib.com/manual/1187745/Fujifilm-X-T2.html?page=148 , https://fujifilm-dsc.com/en-int/manual/x100f/menu_shooting/pixel_mapping/index.html
- [FORUM] "just dims them" https://www.fuji-x-forum.com/topic/4229-x-t2-sensor-test-hot-pixels ; service offered "more advanced" mapping ~$400 (GFX100S) https://www.fuji-x-forum.com/topic/16563-hot-pixels

## Community / forums
- [FORUM] X-E2 remapped three times at Fuji NJ, hot pixels returned; X-T1 owner told remapping is "only Fuji does". https://www.dpreview.com/forums/thread/3716614 , https://www.dpreview.com/forums/post/53556746 (snippets)
- Nothing public on Fuji defect-table layout (fffw issues/wiki, fujihack). No "kouhatsu" hits.

## Defects over time
- [ACADEMIC] Hot pixels accumulate at a roughly constant rate (cosmic rays), random positions; rate ~ pixel size^-3, ISO^0.5. https://www.sfu.ca/~gchapman/papers/dft15.pdf
- [OFFICIAL] Sony bodies run Auto Pixel Mapping at power-off. https://helpguide.sony.net/ilc/2510/v1/en/contents/201h_auto_pixel_mapping.html
- [INFER] New hot pixels after a service mapping are expected; a re-map must add to the stored table.

Next lead: ManualsLib X-Pro1 manual pages 86-100 ([F5] camera / black-level steps).
