# X-T1 green-LED SD card: why it may have stayed dark (offline, 2026-11-04)
CERTAIN = read in code or data, with the location given. GUESSED = inference. Nothing touched the camera or USB.
Stream addresses are stream1 runtime addresses (base 0x780400). Loader offsets are into the stage-1 loader (bit-inverted FWUP0005.DAT[262748:+0x40000], sha256 752b6c19..., runs at address 0).

## Hardware result being explained
Card with green_led_xt1, battery out/in, AE-L + Fn1 (front button) held at power-on: fully dark (no LCD, EVF or LED). After a battery pull it boots normally.

## 1. Lamp pins
- **Rear green: GPIO 15.2, active low (CERTAIN).**
  - LED table 0xa736f4 (17 x 56 B). Entry 1 at 0xa7372c = `0, 1 (direct GPIO), 0x000f0002, 0 (on level)`.
  - Driver FUN_0087b0c4 writes `1<<(pin+16) | on<<pin` at 0xfff89000 + 64*port.
  - The fujimod startup_lamp entry (xt1-552) agrees: 0xfff893c0, pin 2.
- **Rear red: GPIO 15.0, active low.** The pin is CERTAIN (descriptor 0xa73748); the colour is GUESSED from the X-E2 code.
- **Front lamp: GPIO 1.7, active high, PWM-dimmed (routine 0x86ec10).** The register writes are CERTAIN; that it is the AF-assist/self-timer lamp is GUESSED.
- There is no separate card-access LED.
- **green_led_xt1/ffboot.bin's 10 writes match the stock loader's port-15 record (CERTAIN).**
  - The record is at loader 0x1c94; the loader's loop is at 0x2658-0x26c0. The card writes the same registers in the same order with the same polarity.
  - The card only skips the stock +0x1c = 0xff write (pull/drive setup; GUESSED harmless).
  - So a wrong pin is not the cause.
- **Not done by the card:**
  - It doesn't set power hold (0xfff88084 bit 0). The stock loader sets it itself at 0x2724-0x2730.
  - It skips the loader's pre-table writes: 0xfffb2004-14 and 0xfff88024-30 (0x08000000, 0x1810, 0, 0).
- An LED supply rail switched through the PMIC would be invisible to this analysis (GUESSED).

## 2. X70 comparison
- **Same mechanism (CERTAIN):** builder, table-loop code, ffsys edit and boot-ROM hash.
- **Different lamp port (CERTAIN):** the X70 uses port 2; the X-T1 uses port 15.
- **Correction to helper/xvalidate row D:** the X-T1 loader port table is not identical to the X-E2 or X70 tables. Only the LED descriptor matches the X-E2's.
- Nothing in the firmware explains why the card works on the X70 and not the X-T1.

## 3. Why it stayed dark (all GUESSED, most likely first)
The firmware has no AE-L+Fn1 behaviour (trace_modes, CERTAIN), so the dark state means the ROM most likely left its NAND path.
1. **The ROM took the card path but never ran ffboot.bin.** It may have rejected the card, the format or the header. With no power hold, the camera switches off.
2. **ffboot ran but power was not held.**
3. **Power was held but the lamp was unpowered or invisible.**
4. **The buttons alone select another ROM boot device.**

## 4. New cards
Both cards share ffsys.bin (1a9c6f59...). Each ffboot.bin:
- first sets power hold (a read-modify-write of 0xfff88084 bit 0, the same write as the stock loader);
- then loops forever: green 1 unit, off 1 unit, red 1 unit, then off plus a short front-lamp flash, then off 1 unit. One unit is 0.1-3 s depending on the clock (GUESSED).

Exit with a battery pull.

**The cards:**
- **led_blink_hold_xt1** (ffboot sha256 13b089f9...fe31): the stock port-15 record including +0x1c, plus port 1 pin 7 only. This is the minimal card.
- **led_blink_fullgpio_xt1** (ffboot sha256 1bd2d6bd...9075): the verbatim stock loader GPIO table for ports 0-17. Its 116 writes are identical to the stock loop's (checked in a second emulator); the loop's 4 non-GPIO writes are left out.

**Audit** (audit_blink.py; output in audit_hold.txt and audit_fullgpio.txt):
- Writes go only to 0xfff88084 (value 1) and to GPIO 0xfff89000-0xfff8945c. The hold card touches only ports 15 and 1.
- Reads go only to 0xfff88084 and 0xfff89058.
- Neither card touches the NAND, flash or SD controllers.

**Interpreting the test:**
- **Any light:** the ROM ran our code.
- **Still dark:**
  1. Run control (b), no card with the buttons held. A normal boot there means the card was read but rejected.
  2. Then retry on a card formatted in the camera.
- A card that makes a USB device enumerate is not feasible, because it would need a USB stack at ROM time.

Sources: led_blink.S, led_blink_fullgpio.S, build_blink.sh, build_fullgpio.sh, audit_blink.py.
