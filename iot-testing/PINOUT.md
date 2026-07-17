# Milo Wiring Reference

Source of truth: `docs/ARCHITECTURE.md` §5. This is a condensed, on-Pi-readable
copy of the same facts, for reference during hardware testing.

## Safety rule (read this first)

Servos draw from Buck 2 (5A rail) via the PCA9685's **V+** terminal ONLY —
never from the Pi's own 5V. PCA9685 logic **VCC** comes from the Pi's 3.3V, a
DIFFERENT pin from V+. Set both bucks to 5.1V with a multimeter before
connecting any load.

## Pi Zero 2W 40-pin header

```
                 3V3  [ 1] [ 2]  5V   <-- Buck 1 (logic rail)
   I2C SDA -->  GPIO2 [ 3] [ 4]  5V
   I2C SCL -->  GPIO3 [ 5] [ 6]  GND  <-- common ground
                      [ 7] [ 8]
                 GND  [ 9] [10]
                      [11] [12]  GPIO18 --> I2S BCLK
                      [13] [14]  GND
                      [15] [16]
                 3V3 [17] [18]        <-- 3V3 -> mic VDD, PCA9685 VCC, Mic B L/R pin
                      [19] [20]  GND
                      [21] [22]
                      [23] [24]
                 GND [25] [26]
                      [27] [28]
                      [29] [30]  GND
                      [31] [32]
                      [33] [34]  GND
   I2S LRCLK <- GPIO19[35] [36]
                      [37] [38]  GPIO20 --> I2S DATA IN (from mics)
                 GND [39] [40]  GPIO21 --> I2S DATA OUT (to amp)

   CSI connector (board edge): IMX219 camera via 15->22-pin Zero ribbon
```

| GPIO | Pin | Function | Connects to |
|---|---|---|---|
| GPIO 2 | 3 | I2C1 SDA | PCA9685 SDA + SSD1306 SDA + MPU6050 SDA |
| GPIO 3 | 5 | I2C1 SCL | PCA9685 SCL + SSD1306 SCL + MPU6050 SCL |
| GPIO 18 | 12 | I2S BCLK | INMP441 x2 SCK and MAX98357A BCLK |
| GPIO 19 | 35 | I2S LRCLK | INMP441 x2 WS and MAX98357A LRC |
| GPIO 20 | 38 | I2S DATA IN | INMP441 x2 SD (one shared line) |
| GPIO 21 | 40 | I2S DATA OUT | MAX98357A DIN |
| 5V | 2/4 | Power in | Buck 1 output |
| 3V3 | 1/17 | Logic ref | mic VDD, PCA9685 VCC, Mic B channel-select |
| GND | 6,9,14,... | Ground | common ground |
| CSI | -- | Camera | IMX219, 15->22-pin ribbon |

## I2C bus (3 devices, one bus)

- PCA9685 @ `0x40` — servo driver (VCC=3V3, V+=Buck 2, never the Pi's 5V)
- SSD1306 @ `0x3C` — OLED face
- MPU6050 @ `0x68` — IMU (mount RIGID near body center — screws/standoffs, never foam)

Bring-up check: `i2cdetect -y 1` must show `0x3c`, `0x40`, `0x68`.

## I2S bus (2 mics in, 1 amp out, shared clocks)

- GPIO18 BCLK → Mic A SCK, Mic B SCK, MAX98357A BCLK
- GPIO19 LRCLK → Mic A WS, Mic B WS, MAX98357A LRC
- GPIO20 ← Mic A SD, Mic B SD (shared data-in line)
- GPIO21 → MAX98357A DIN

Mic A: L/R pin → GND (LEFT channel), mounted LEFT side of head.
Mic B: L/R pin → 3V3 (RIGHT channel), mounted RIGHT side of head.
Target 10–15 cm mic separation.

## Servo channel map (PCA9685) — front legs match Sesame firmware naming, rear legs rewired

| Channel | Servo | Position |
|---|---|---|
| 0 | R1 | front-right hip |
| 1 | R2 | front-right knee |
| 2 | L1 | front-left hip |
| 3 | L2 | front-left knee |
| 8 | R4 | rear-right knee |
| 9 | R3 | rear-right hip |
| 10 | L3 | rear-left hip |
| 11 | L4 | rear-left knee |
