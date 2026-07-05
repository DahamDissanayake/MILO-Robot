# Firmware Deep Dive — Sesame Robot

**Date:** 2026-06-29  
**Author:** Daham Dissanayake  
**Scope:** Full analysis of `firmware/` — architecture, control flow, servo kinematics, display system, and networking stack.

---

## Overview

Sesame is a **4-legged walking robot** built on an ESP32 microcontroller. It has no camera, no sensors, and no onboard compute beyond the ESP32 itself. All intelligence lives in commands sent over WiFi from an external device (browser, Python script, voice assistant, etc.). The firmware handles three things: servo motion, OLED face display, and a WiFi HTTP server.

---

## File Map

| File | Role |
|---|---|
| `sesame-firmware-main.ino` | Entry point — `setup()`, `loop()`, WiFi, HTTP routes, face/idle engine |
| `movement-sequences.h` | All servo poses and locomotion gaits (inline functions) |
| `face-bitmaps.h` | 128×64 PROGMEM bitmaps + X-macro face registration |
| `captive-portal.h` | Embedded HTML/CSS/JS web controller (single-page app) |
| `debugging-firmware/sesame-motor-tester.ino` | Standalone servo calibration sketch |

---

## Hardware

### MCU / Board Support

The firmware targets three hardware generations via compile-time pin configuration:

| Board | MCU | Servo Pins | I2C SDA/SCL |
|---|---|---|---|
| Lolin S2 Mini | ESP32-S2 | 1, 2, 4, 6, 8, 10, 13, 14 | 33 / 35 |
| Distro Board V1 | ESP32-WROOM32 | 15, 2, 23, 19, 4, 16, 17, 18 | 21 / 22 |
| Distro Board V2 | ESP32-S3 | 4, 5, 6, 7, 15, 16, 17, 18 | 8 / 9 |
| Distro Board V3 | ESP32-S3 | 4, 5, 6, 7, 10, 11, 12, 13 | 8 / 9 |

Switching boards = uncommenting the right `servoPins[]` and `I2C_SDA`/`I2C_SCL` defines.

### Servo Layout — 8 motors, 4 legs

```
       FRONT
  L1 ---|--- R1   (upper leg, hip rotation)
  L2 ---|--- R2   (lower leg, knee)
       BODY
  L3 ---|--- R3   (upper rear leg)
  L4 ---|--- R4   (lower rear leg)
       BACK
```

Servo indices are named `R1–R4` (right) and `L1–L4` (left) via the `ServoName` enum in `movement-sequences.h:5`.

### Display

SSD1306 128×64 OLED via hardware I2C at 400kHz. All face art is stored in Flash (`PROGMEM`) as 1024-byte raw bitmap arrays.

---

## Firmware Architecture

### Boot Sequence (`setup()`)

```
1. Serial init (115200 baud)
2. I2C init (hardware pins)
3. OLED init — show "Setting up WiFi..."
4. WiFi config:
     - If ENABLE_NETWORK_MODE=true → try STA connection (10s timeout)
     - Always → start SoftAP ("Sesame-Controller", "12345678")
5. Build WiFi info scroll text
6. mDNS start → "sesame-robot.local"
7. DNS server start (port 53, wildcard "*" → AP IP) — captive portal
8. HTTP routes registered
9. PWM timers allocated (0–3), 8 servos attached at 50Hz (732–2929µs)
10. setFace("rest") — show face on OLED, no motors move
```

### Main Loop (`loop()`)

The loop is **single-core, non-preemptive**. Everything runs cooperatively:

```
loop() {
  dnsServer.processNextRequest()     // captive portal DNS
  server.handleClient()              // HTTP requests
  updateAnimatedFace()               // advance face frame if needed
  updateIdleBlink()                  // random blink scheduler
  updateWifiInfoScroll()             // scroll WiFi info on OLED (first 30s)

  if (currentCommand != "") {        // dispatch movement
    run[Command]Pose()
  }

  if (Serial.available()) {          // debug CLI
    parse and dispatch
  }
}
```

`currentCommand` is a global `String` that acts as the state machine — set by HTTP handlers, cleared by pose functions when they finish.

---

## Servo Control

### `setServoAngle(channel, angle)` — `sesame-firmware-main.ino:771`

```cpp
void setServoAngle(uint8_t channel, int angle) {
  int adjustedAngle = constrain(angle + servoSubtrim[channel], 0, 180);
  servos[channel].write(adjustedAngle);
  delayWithFace(motorCurrentDelay);   // default 20ms stagger
}
```

- Applies per-servo **subtrim** offset (default 0, range ±90°) — useful for physical calibration without disassembly.
- **`motorCurrentDelay`** (default 20ms) staggers each servo write to prevent simultaneous inductive surge causing a brownout. Tunable at runtime via `/setSettings`.
- `delayWithFace()` keeps the HTTP server and OLED alive during the delay.

### PWM Mapping

Pulse range: **732µs–2929µs** at 50Hz. This covers 0–180° for standard hobby servos. For 270° servos, the README notes 833–2167µs works.

**Known issue:** ESP32Servo v3.0.9 is pinned — newer releases have a bug where writing to one servo leaks PWM to other channels.

---

## Locomotion Gaits

All gaits are defined as inline functions in `movement-sequences.h`.

### Walk Forward (`runWalkPose`)

Uses a **diagonal trot gait** — front-left / rear-right move together, then front-right / rear-left:

```
Step 1: Lift R3+L3 (rear knees)
        Swing R2+L1 (front hip)
Step 2: Push R4+L4 → swing L2+R1 (other diagonal)
...repeat walkCycles times
```

`pressingCheck(cmd, ms)` is called between every sub-step — if the user releases the button mid-walk the robot aborts and runs `runStandPose(1)`. This is the core **non-blocking interruptibility pattern** used throughout.

### Backward / Turn Left / Turn Right

Backward is the walk gait with reversed hip swing angles. Turns work by driving one diagonal forward while keeping the other stationary (differential drive logic adapted for legs).

### One-Shot Poses

All other commands (`wave`, `dance`, `swim`, `pushup`, `bow`, etc.) are one-shot animations:
1. Call `setFaceWithMode(name, FACE_ANIM_ONCE)` 
2. Sequence servo angles with `delayWithFace()` pauses
3. End with `runStandPose(1)` → enters idle

---

## Display System

### Face Animation Engine

Three playback modes (`FaceAnimMode`):

| Mode | Behavior |
|---|---|
| `FACE_ANIM_LOOP` | Loops frames 0→N→0→N... |
| `FACE_ANIM_ONCE` | Plays to last frame, stops |
| `FACE_ANIM_BOOMERANG` | Plays 0→N→0 (ping-pong) |

`updateAnimatedFace()` is called every loop iteration. It checks elapsed time vs. the face's FPS entry and advances `currentFaceFrameIndex` accordingly. Renders via `display.drawBitmap()`.

### Face Library — 37 faces registered

Faces are registered with a **single X-macro** in `face-bitmaps.h:13`:

```cpp
#define FACE_LIST \
  X(walk) X(rest) X(swim) X(dance) X(wave) X(point) X(stand) \
  X(cute) X(pushup) X(freaky) X(bow) X(worm) X(shake) X(shrug) \
  X(dead) X(crab) X(defualt) X(idle) X(idle_blink) \
  X(happy) X(talk_happy) X(sad) X(talk_sad) X(angry) X(talk_angry) \
  X(surprised) X(talk_surprised) X(sleepy) X(talk_sleepy) \
  X(love) X(talk_love) X(excited) X(talk_excited) \
  X(confused) X(talk_confused) X(thinking) X(talk_thinking)
```

Adding a new face = add `X(myface)` to `FACE_LIST` + paste the `image2cpp` output into `face-bitmaps.h`. The macro auto-generates frame arrays and lookup table entries.

**Note:** `defualt` is a typo in the codebase (should be `default`) — the string `"default"` is still used at runtime via special-case lookup.

### Idle System

- **Trigger:** No commands received (idle mode is entered after `runStandPose`)
- **Behavior:** `idle` face plays in `FACE_ANIM_BOOMERANG` (breathing effect)
- **Blinking:** Random interval 3–7 seconds; `idle_blink` plays `ONCE`; 30% chance of double-blink (120–220ms gap between blinks)
- **Exit:** Any command clears `idleActive` via `exitIdle()`

### WiFi Info Overlay

For the first 30 seconds after boot (or until first input), a scrolling text bar composites over the face bitmap at row 0. Drawn as: face bitmap → black rect over top 10px → white scrolling text.

---

## Networking Stack

### Dual-Mode WiFi

```
Default:   AP only  (WIFI_AP)
Optional:  AP + STA (WIFI_AP_STA) — connects to home network simultaneously
```

Access Point: SSID `Sesame-Controller`, pass `12345678`, IP `192.168.4.1`  
Station: configured via `NETWORK_SSID` / `NETWORK_PASS` / `ENABLE_NETWORK_MODE`

### Captive Portal

DNS server on port 53 with wildcard `"*"` → AP IP. Any domain request from a connected device resolves to the ESP32, which serves the controller web app. This auto-opens on iOS/Android/Windows.

### HTTP API Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Web controller (from `captive-portal.h`) |
| `/cmd?go=[dir]` | GET | Legacy movement (forward/backward/left/right/stop) |
| `/cmd?pose=[name]` | GET | Legacy pose trigger |
| `/cmd?motor=[n]&value=[deg]` | GET | Individual motor angle |
| `/getSettings` | GET | Returns JSON of tunable params |
| `/setSettings?...` | GET | Set frameDelay, walkCycles, motorCurrentDelay, faceFps |
| `/api/status` | GET | JSON: currentCommand, currentFace, IPs |
| `/api/command` | POST | JSON command (see below) |

### `/api/command` JSON Format

```json
// Movement + face
{ "command": "dance", "face": "dance" }

// Face-only (no "command" key = face-only path)
{ "face": "happy" }

// Stop
{ "command": "stop" }
```

The endpoint detects face-only mode by checking `body.indexOf("\"command\":") == -1`. Manual JSON parsing (no library) — works but is brittle to whitespace variations.

### mDNS

`sesame-robot.local` is broadcast on the local network via `ESPmDNS`. Requires Bonjour on Windows, avahi on Linux. Falls back gracefully if mDNS fails — robot still reachable by IP.

---

## Serial Debug CLI

Available via Arduino Serial Monitor at 115200 baud. Commands:

| Command | Action |
|---|---|
| `run walk` / `rn wf` | Run walk forward once |
| `rn wb/tl/tr` | Walk back / turn left / turn right |
| `rn wv/dn/sw/pt/pu/bw/ct/fk/wm/sk/sg/dd/cb` | Pose shortcuts |
| `0 90` (motor angle) | Set servo 0 to 90° |
| `all 90` | Set all servos to 90° |
| `subtrim` / `st` | Print subtrim values |
| `subtrim [motor] [offset]` | Set per-servo trim (±90°) |
| `subtrim save` | Print copy-paste code for hardcoding trims |
| `subtrim reset` | Zero all trims |

---

## Known Limitations & Gotchas

1. **Single-core blocking loop** — long animations block HTTP serving. The `pressingCheck` + `delayWithFace` pattern mitigates this, but a blocking `delay()` anywhere kills responsiveness.

2. **Hardware timer limit** — only 4 PWM timers on ESP32. Adding ESCs or more PWM devices can exhaust timers and kill the captive portal.

3. **Manual JSON parsing** — `handleApiCommand()` uses `String.indexOf()` to parse JSON. Fragile to whitespace. A library like ArduinoJson would be more robust.

4. **No auth** — the HTTP server accepts commands from any device on the network. Fine for a toy, not for a production deployment.

5. **Typo in face name** — `defualt` (not `default`) in `FACE_LIST`. The string `"default"` is matched via a hardcoded special case in `faceEntries[]`. Don't rename without updating both sides.

6. **ESP32Servo pinned to v3.0.9** — newer versions have a multi-servo PWM leak bug. Don't upgrade without testing.

---

## Extension Points

- **Add a face:** `X(myface)` in `FACE_LIST` + paste bitmap + add FPS entry in `faceFpsEntries[]`
- **Add a pose:** Write `inline void runMyPose()` in `movement-sequences.h`, add prototype + dispatch in `loop()` and Serial CLI
- **Connect to AI:** Enable network mode, call `/api/command` POST from a Python script — conversational faces (`talk_happy`, etc.) are already built for lip-sync
- **Calibrate servos:** Use Serial CLI `subtrim [motor] [offset]`, then `subtrim save` to bake values into code
