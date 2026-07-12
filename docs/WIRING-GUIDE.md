# MILO — Complete Wiring Guide: Every Wire, Both Ends

**Date:** 2026-07-08
**Author:** Daham Dissanayake
**Companion docs:** [`ARCHITECTURE.md`](ARCHITECTURE.md) section 5 (source diagrams) · [`BUILD-PLAN.md`](BUILD-PLAN.md) Phases 4 and 7 · [`SOFTWARE-SETUP.md`](SOFTWARE-SETUP.md)

This document lists every single wire in the robot: what it is, where end A connects, where end B connects, what gauge to use, and a suggested color. Work through it section by section with the battery disconnected. Check off each wire as you make it.

**Wire and color conventions used throughout:**

| Signal type | Gauge | Suggested color |
|---|---|---|
| Battery and 5 V power | 22 AWG silicone | Red (+), Black (-) |
| 3.3 V power | 30 AWG | Orange |
| Ground (signal side) | 30 AWG | Black |
| I2C data (SDA) | 30 AWG | Blue |
| I2C clock (SCL) | 30 AWG | Yellow |
| I2S lines | 30 AWG | Green / White / Purple / Grey |

Thicker signal wire will not fit the frame — keep all signal runs 30 AWG. Every soldered splice gets heat-shrink. Twisting SDA with SCL, and keeping I2S runs short (<10 cm where possible), keeps the buses clean.

---

## 1. Pi Zero 2W Header — Master Pin Reference

Only these pins are used. Physical pin numbers count left-right, top-down with the header at the top:

| Physical pin | Name | Used for |
|---|---|---|
| 1 or 17 | 3V3 | Logic power to all breakouts |
| 2 (and 4) | 5V | Power IN from Buck 1 |
| 3 | GPIO 2 (SDA) | I2C data bus |
| 5 | GPIO 3 (SCL) | I2C clock bus |
| 6, 9, 14, 20, 25, 30, 34, 39 | GND | Common ground (any of them) |
| 12 | GPIO 18 | I2S bit clock (BCLK/SCK) |
| 35 | GPIO 19 | I2S word select (LRCLK/WS/LRC) |
| 38 | GPIO 20 | I2S data IN (from both mics) |
| 40 | GPIO 21 | I2S data OUT (to the amp) |
| CSI connector | — | Camera ribbon |

---

## 2. Power System Wiring (build and verify FIRST)

### 2.1 Battery pack: 2x 18650 + 2S BMS

A 2S BMS has three battery-side pads (B-, BM, B+) and two output pads (P-, P+).

| # | From (end A) | To (end B) | Wire |
|---|---|---|---|
| P1 | Cell 1 negative terminal | BMS **B-** pad | 22 AWG black |
| P2 | Cell 1 positive terminal | Cell 2 negative terminal (series link), tap this junction to BMS **BM** pad | 22 AWG red (link), 22 AWG black (BM tap) |
| P3 | Cell 2 positive terminal | BMS **B+** pad | 22 AWG red |

Result: BMS P+ / P- now carry the protected 7.4 V (nominal) pack output. Verify with the multimeter: P+ to P- reads 6.0–8.4 V depending on charge.

### 2.2 Charger module (battery side, BEFORE the switch)

| # | From | To | Wire |
|---|---|---|---|
| P4 | Charger module OUT+ (or B+) | BMS **P+** | 22 AWG red |
| P5 | Charger module OUT- (or B-) | BMS **P-** | 22 AWG black |

Wired here so the pack charges even with the robot switched off. The charger's input jack faces outward through the shell.

### 2.3 Main switch and buck inputs

| # | From | To | Wire |
|---|---|---|---|
| P6 | BMS **P+** | KCD1 switch terminal 1 | 22 AWG red |
| P7 | KCD1 switch terminal 2 | Buck 1 **IN+** | 22 AWG red |
| P8 | KCD1 switch terminal 2 (same splice as P7) | Buck 2 **IN+** | 22 AWG red |
| P9 | BMS **P-** | Buck 1 **IN-** | 22 AWG black |
| P10 | BMS **P-** (same splice as P9) | Buck 2 **IN-** | 22 AWG black |

Both bucks hang in parallel off the switched battery. Splice P7/P8 and P9/P10 as Y-joints, heat-shrunk.

**STOP here.** Power on with NOTHING on the buck outputs and set each buck's trim pot to **5.10 V** measured at its OUT terminals. Mark the pots with nail polish.

### 2.4 Logic rail (Buck 1, 5 V / 2 A)

| # | From | To | Wire |
|---|---|---|---|
| P11 | Buck 1 **OUT+** | Pi **pin 2 (5V)** | 22 AWG red |
| P12 | Buck 1 **OUT-** | Pi **pin 6 (GND)** | 22 AWG black |
| P13 | Buck 1 **OUT+** (splice off P11) | MAX98357A **VIN** | 22 AWG red |
| P14 | Buck 1 **OUT-** (splice off P12) | MAX98357A **GND** | 22 AWG black |

The Pi then feeds 3.3 V to all small breakouts from its own regulator (section 3). The amp takes 5 V directly from Buck 1 for full 3 W output.

### 2.5 Servo rail (Buck 2, 5 V / 5 A)

| # | From | To | Wire |
|---|---|---|---|
| P15 | Buck 2 **OUT+** | PCA9685 **V+ screw terminal** | 22 AWG red |
| P16 | Buck 2 **OUT-** | PCA9685 **GND screw terminal** | 22 AWG black |

This is the ONLY source of servo power. Nothing else ever connects to V+.

### 2.6 Common ground

All grounds must be one electrical net: battery P-, Buck 1 OUT-, Buck 2 OUT-, Pi GND, and every breakout GND. The buck input wiring (P9/P10) and output wiring (P12, P14, P16) already achieve this if the breakout grounds in sections 3–4 all return to Pi GND pins. Verify with the multimeter in continuity mode: any GND to any other GND must beep.

---

## 3. I2C Bus — PCA9685, OLED, MPU6050 (three devices, one bus)

The bus is two shared lines (SDA, SCL) daisy-chained or star-spliced to all three boards, plus power and ground per board.

### 3.1 PCA9685 servo driver (address 0x40)

| # | From (Pi) | To (PCA9685) | Wire |
|---|---|---|---|
| I1 | Pin 1 (3V3) | **VCC** | 30 AWG orange |
| I2 | Pin 6/9 (GND) | **GND** (header pin, not the screw terminal) | 30 AWG black |
| I3 | Pin 3 (GPIO 2, SDA) | **SDA** | 30 AWG blue |
| I4 | Pin 5 (GPIO 3, SCL) | **SCL** | 30 AWG yellow |

Leave the PCA9685 **OE** pin unconnected. Remember: VCC (I1, logic 3.3 V) and V+ (P15, servo 5 V) are different pins — mixing them up feeds 5 V servo power into the Pi's 3.3 V rail.

### 3.2 SSD1306 OLED face (address 0x3C)

| # | From | To (OLED) | Wire |
|---|---|---|---|
| I5 | Pi 3V3 (splice off I1, or pin 17) | **VCC** | 30 AWG orange |
| I6 | Pi GND (any GND pin) | **GND** | 30 AWG black |
| I7 | SDA line (splice off I3) | **SDA** | 30 AWG blue |
| I8 | SCL line (splice off I4) | **SCL** | 30 AWG yellow |

The OLED sits in the top-cover slot, so give these four wires enough slack to open the cover. Route per the Sesame guide.

### 3.3 MPU6050 IMU on GY-521 breakout (address 0x68)

| # | From | To (GY-521) | Wire |
|---|---|---|---|
| I9 | Pi 3V3 (splice) | **VCC** | 30 AWG orange |
| I10 | Pi GND | **GND** | 30 AWG black |
| I11 | SDA line (splice) | **SDA** | 30 AWG blue |
| I12 | SCL line (splice) | **SCL** | 30 AWG yellow |

Leave **XDA, XCL, INT** unconnected. Leave **AD0** unconnected or tie to GND — either way the address is 0x68 (tying it to 3V3 would change it to 0x69 and break the software). Mount the board rigidly (screws/standoffs) near the body center, component side up, axes square to the body.

**Verification:** `i2cdetect -y 1` shows `0x3c`, `0x40`, `0x68`. A missing device means a bad splice on that board's four wires.

---

## 4. I2S Audio Bus — Two Mics In, One Amp Out (shared clocks)

Three boards share BCLK and LRCLK. The two mics additionally share one data-in line; the amp has its own data-out line.

### 4.1 Microphone A — LEFT side of the head

| # | From | To (INMP441 A) | Wire |
|---|---|---|---|
| S1 | Pi pin 1/17 (3V3) | **VDD** | 30 AWG orange |
| S2 | Pi GND | **GND** | 30 AWG black |
| S3 | Pi pin 12 (GPIO 18) | **SCK** | 30 AWG green |
| S4 | Pi pin 35 (GPIO 19) | **WS** | 30 AWG white |
| S5 | Pi pin 38 (GPIO 20) | **SD** | 30 AWG purple |
| S6 | Mic A **L/R** pin | **GND** (short jumper on the breakout itself) | 30 AWG black |

S6 is what makes this the LEFT channel.

### 4.2 Microphone B — RIGHT side of the head

| # | From | To (INMP441 B) | Wire |
|---|---|---|---|
| S7 | 3V3 (splice off S1) | **VDD** | 30 AWG orange |
| S8 | Pi GND | **GND** | 30 AWG black |
| S9 | GPIO 18 line (splice off S3) | **SCK** | 30 AWG green |
| S10 | GPIO 19 line (splice off S4) | **WS** | 30 AWG white |
| S11 | GPIO 20 line (splice off S5 — SHARED data line with Mic A) | **SD** | 30 AWG purple |
| S12 | Mic B **L/R** pin | **3V3** (jumper to the board's own VDD) | 30 AWG orange |

S12 is what makes this the RIGHT channel. If both mics have L/R strapped the same way, you get one channel twice and sound direction breaks — this is the most common audio wiring mistake.

Mount the mics 10–15 cm apart (left and right of the head), sound ports unobstructed. Hot glue or foam tape is fine for mics.

### 4.3 MAX98357A amplifier + speaker

| # | From | To (MAX98357A) | Wire |
|---|---|---|---|
| S13 | Buck 1 OUT+ (already listed as P13) | **VIN** | 22 AWG red |
| S14 | Buck 1 OUT- / common ground (P14) | **GND** | 22 AWG black |
| S15 | GPIO 18 line (splice off S3/S9) | **BCLK** | 30 AWG green |
| S16 | GPIO 19 line (splice off S4/S10) | **LRC** | 30 AWG white |
| S17 | Pi pin 40 (GPIO 21) | **DIN** | 30 AWG grey |
| S18 | Amp **+** speaker terminal | Speaker **+** | 22 AWG red |
| S19 | Amp **-** speaker terminal | Speaker **-** | 22 AWG black |

Leave **GAIN** unconnected (default 9 dB) and **SD** unconnected (default enabled, (L+R)/2 mono mix — correct for Milo's single speaker). Note the amp's data line is S17 (GPIO 21, OUT from Pi) and never touches GPIO 20 (the mic data line) — they are separate one-way streets sharing the same clocks.

**Verification:** `arecord -D plughw:0 -c2 -r16000 -f S16_LE -d 3 test.wav && aplay test.wav` — both channels present, playback audible.

---

## 5. Servos — 8 Plugs Into the PCA9685

No soldering here: each MG90S lead is a 3-pin female plug that pushes straight onto the PCA9685 channel headers. Each channel header has three pins stacked: GND (bottom row), V+ (middle row), signal (top row).

**Finding channel 0 on the 16-channel board:** the channel numbers are printed on the silkscreen along the header rows, in four blocks of four (`0 1 2 3 | 4 5 6 7 | 8 9 10 11 | 12 13 14 15`). Hold the board with the servo headers along the bottom edge facing you and the I2C pins (VCC/GND/SDA/SCL) on the left — channel 0 is then the leftmost 3-pin column, channel 15 the rightmost. Milo uses only channels 0–7 (the eight leftmost columns); 8–15 stay empty. To confirm electrically, plug one servo into the presumed channel 0 and run `python bridge/tools/servo_sweep.py` — it drives channels in order and announces each, so whichever announcement moves the servo is that column's true number.

Plug orientation per servo lead: **brown or black = GND, red = V+ (center), orange or yellow = signal.** Red is always the middle pin, so a plug rotated 180 degrees puts GND on signal — the servo just won't move, but fix it before blaming software.

| Channel | Servo label | Leg position |
|---|---|---|
| 0 | R1 | front-right hip |
| 1 | R2 | front-right knee |
| 2 | L1 | front-left hip |
| 3 | L2 | front-left knee |
| 4 | R4 | rear-right knee |
| 5 | R3 | rear-right hip |
| 6 | L3 | rear-left hip |
| 7 | L4 | rear-left knee |

This map is baked into the software. If the wrong leg moves during the sweep test, move the plug, never edit the code.

**Verification:** `python bridge/tools/servo_sweep.py` — each channel in order moves exactly the leg named in the table.

---

## 6. Camera — CSI Ribbon

Not a wire but a ribbon; orientation matters at both ends.

| End | Connector | Orientation |
|---|---|---|
| A | Pi Zero 2W CSI port (board edge, next to the power connector) | The Zero's CSI socket takes the **22-pin (narrow)** end; contacts face the board, blue stiffener faces away from the board |
| B | Camera module connector | The **15-pin (wide)** end; blue stiffener faces away from the lens side (toward the back of the camera board) |

Open each socket's latch by pulling it out gently, seat the ribbon fully and squarely, close the latch. A camera "not detected" is almost always a ribbon inserted backwards or not fully seated — reseat both ends before anything else. The camera mounts facing forward in the head.

**Verification:** `rpicam-hello --list-cameras` lists imx219.

---

## 7. Full Wire Count Summary

| Section | Wires/connections | Check |
|---|---|---|
| Battery + BMS | P1–P3 (4 solder joints) | [ ] |
| Charger | P4–P5 | [ ] |
| Switch + buck inputs | P6–P10 | [ ] |
| Logic rail | P11–P14 | [ ] |
| Servo rail | P15–P16 | [ ] |
| PCA9685 I2C | I1–I4 | [ ] |
| OLED | I5–I8 | [ ] |
| MPU6050 | I9–I12 | [ ] |
| Mic A | S1–S6 | [ ] |
| Mic B | S7–S12 | [ ] |
| Amp + speaker | S13–S19 | [ ] |
| Servo plugs | 8 plugs, channels 0–7 | [ ] |
| Camera ribbon | 2 ends | [ ] |

Roughly 45 electrical connections total. Suggested build order: section 2 (power, verified at 5.1 V) -> section 3 (I2C, verify with i2cdetect) -> section 4 (I2S, verify with arecord/aplay) -> section 5 (servos, verify with sweep) -> section 6 (camera). Verifying each bus before starting the next means a fault is always in the last ten wires you touched.

---

## 8. Final Electrical Checks Before First Full Power-On

1. Continuity: every GND point beeps to every other GND point.
2. No continuity between 5 V and GND, between 3V3 and GND, or between 5 V and 3V3 (a beep = a short; find it before powering).
3. Both buck outputs still read 5.10 V under no load.
4. PCA9685 V+ receives Buck 2 only; nothing else on that terminal.
5. Pi 5 V pin receives Buck 1 only; no servo lead red wire goes anywhere near the Pi.
6. Every splice heat-shrunk, wires bundled with zip ties clear of leg travel.
