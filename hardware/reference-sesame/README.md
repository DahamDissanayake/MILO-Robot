# Sesame firmware reference files

These files are copied **verbatim** from the original
[Sesame Robot Project](https://github.com/dorianborian/sesame-robot) by
[Dorian Todd](https://www.doriantodd.com/), under the Apache 2.0 license.
They are the source of truth Milo ports from — they are **not** compiled or run here.

| File | Used by |
|---|---|
| `movement-sequences.h` | `bridge/milo_bridge/poses.py` — servo angles for rest/stand/wave/walk/… transfer directly because the body and horn geometry are unchanged |
| `face-bitmaps.h` | `bridge/tools/convert_faces.py` — 128×64 PROGMEM face bitmaps converted to PNG assets in `bridge/assets/faces/` |

Servo naming convention inherited from these files: R1, R2, L1, L2, R4, R3,
L3, L4. Milo's PCA9685 channel assignment diverges from the original
firmware's contiguous 0-7: front legs stay on R1=0, R2=1, L1=2, L2=3; rear
legs are rewired to R4=8, R3=9, L3=10, L4=11.
