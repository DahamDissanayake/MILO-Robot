# Sesame firmware reference files

These files are copied **verbatim** from the original
[Sesame Robot Project](https://github.com/dorianborian/sesame-robot) by
[Dorian Todd](https://www.doriantodd.com/), under the Apache 2.0 license.
They are the source of truth Milo ports from — they are **not** compiled or run here.

| File | Used by |
|---|---|
| `movement-sequences.h` | `bridge/milo_bridge/poses.py` — servo angles for rest/stand/wave/walk/… transfer directly because the body and horn geometry are unchanged |
| `face-bitmaps.h` | `bridge/tools/convert_faces.py` — 128×64 PROGMEM face bitmaps converted to PNG assets in `bridge/assets/faces/` |

Servo channel/naming convention inherited from these files: R1=0, R2=1, L1=2, L2=3,
R4=4, R3=5, L3=6, L4=7.
