# Movement, Face, Speech & IMU MCP Engine

**Date:** 2026-07-17
**Status:** Approved for planning

## Problem

The gait/pose engine (`bridge/milo_bridge/gait/`, `bridge/milo_bridge/poses.py`) already implements nearly the
full Sesame firmware movement repertoire — walk, walk_backward, turn_left, turn_right, crab, look_up, look_down,
wave, dance, bow, pushup, swim, cute, freaky, worm, shake, shrug, dead, wake_up, rest, stand — at a 50 Hz
IMU-aware control loop (`GaitEngine`), with balance correction (`gait/balance.py`) and an ONNX RL policy fallback
to a CPG trot. But the brain's LLM (`brain/milo_brain/llm/agent.py`) can only trigger **6** hardcoded poses
(`none/wave/dance/bow/point/pushup`) through a single JSON field (`move`) in its structured chat response, has
no access to walking/turning/looking, and never sees IMU state (roll/pitch/gyro) — so it cannot reason about
balance or react to it. Face selection is likewise a fixed JSON field, and the bridge's web dashboard has a
"Say" feature (bridge-local `espeak-ng` synthesis, `webapp/api/speak.py`) for on-demand speech that the brain
has no equivalent of — it can only speak as a direct reply to an utterance. There is also no way to verify,
offline, how each movement actually behaves on real hardware (peak tilt, settle time, stability), so movement
additions and gait/servo tuning changes are unverified until someone watches the robot do it in person.

## Goals

- Give the brain's LLM real, multi-step tool-calling control over the robot's entire movement repertoire, face
  display, on-demand speech, and live IMU telemetry, via a standard MCP server — not a set of hardcoded enums.
- Run that MCP server on the bridge (robot side), so both the brain's own agent loop and a human's MCP client
  (Claude Desktop/Code) can drive or inspect the robot for manual testing/debugging.
- Reuse the existing pairing trust store (`common/milo_common/auth.py` `PairedStore`) for MCP access control —
  no new auth system.
- Preserve the existing `ControlBroker` motion-arbitration guarantee (web client always wins when it holds
  control; STOP and telemetry reads are never gated) for every MCP-originated command, closing the gap where
  today's brain-driven face changes are ungated while the web path's aren't.
- Produce a repeatable, offline per-movement IMU characterization report (peak tilt, settle time, pass/fail
  against a safety ceiling) usable after any gait/servo tuning change.

## Non-goals

- Camera/audio perception upgrades (scene understanding, sound-event detection) — separate sub-project.
- The Linux CLI/TUI brain redesign — separate sub-project.
- Backward compatibility with brains/bridges that don't understand the new protocol — this is a coordinated
  upgrade of both sides (see Migration below), not a fleet-compatible rollout.
- Full inverse-kinematics or any new balance algorithm — the existing `GaitEngine`/`balance.py` behavior is
  exposed as-is, not redesigned.
- Replacing the brain's conversational Piper TTS pipeline (`T_TTS` streaming) — the new `speak` tool is an
  additional, separate on-demand voice channel (mirroring the dashboard's "Say" button), not a replacement.

## Design

### 1. Architecture

Two independent channels exist between bridge and brain after this change:

- The existing authenticated WebSocket (robot dials out to the brain, per `handshake.py`) — still carries
  video/audio/TTS/graph frames, **unchanged**.
- A new MCP server on the bridge, reachable over HTTP — carries movement, face, and speech commands plus
  IMU/status telemetry. The brain (and, for manual testing, a human's MCP client) dials **in** to it.

```
bridge (Raspberry Pi)                         brain (server)
+-----------------------------+               +-------------------------------+
| GaitEngine / PoseRunner /   |               | CognitionAgent (tool-calling   |
| Mpu6050 / ControlBroker     |               | loop against Ollama)           |
|        ^                    |               |        |                      |
|        | (in-process calls) |               |        | MCP client           |
| +------+------------------+ |   HTTP/SSE    |        v                      |
| | Milo Movement MCP server| |<--------------+  MCP tool calls                |
| | (mcp SDK, Streamable    | |  milo.local:  |                                |
| |  HTTP, milo.local:8765) | |    8765       | also called directly (no LLM)  |
| +--------------------------+ |               | by the audio-direction        |
| existing WS session (video/  |               | "turn toward speaker" reflex  |
| audio/TTS/graph) ----------->| brain, unchanged                              |
+-----------------------------+               +-------------------------------+
```

`move` and `face` are removed entirely from `T_CMD` — both become MCP tool calls. The existing conversational
voice path (Piper synthesis on the brain, streamed as `T_TTS` frames over the socket) is untouched; the new
`speak` MCP tool is a separate, bridge-local (`espeak-ng`) on-demand voice channel, not a replacement for it.

### 2. MCP server (bridge-side, new `bridge/milo_bridge/mcp/` package)

Built with the official `mcp` Python SDK (`mcp.server.fastmcp.FastMCP`), served over Streamable HTTP on its own
port (default `8765`), started alongside the existing aiohttp dashboard in `main.py` (same graceful-degradation
pattern as every other optional subsystem there).

Tools, each a thin wrapper around objects `main.py` already constructs (`gait`, `runner`, `imu`, `broker`,
`servos`, `display`, `audio`):

| Tool | Wraps | Gated by broker? |
|---|---|---|
| `walk(vx, vy, yaw_rate)` | `gait.set_velocity_command`, clamped to the same `VX_LIM`/`VY_LIM`/`YAW_LIM` as `webapp/motion.py` | yes |
| `run_pose(name, cycles=None)` | `runner.run(name)`, `name` in `poses.POSES` | yes |
| `turn(direction)` | continuous `turn_left`/`turn_right` pose, matches `MotionService.turn` | yes |
| `set_mode(name)` | `gait.set_mode`, `name` in `raw/balanced/angled` | yes |
| `reset()` / `standby()` | `gait.reset()` / `gait.standby()` | yes |
| `relax()` / `hold()` | `servos.relax()` / `servos.hold()` | yes |
| `set_face(name)` | `display.set_face(name, mode)` — see §2a | yes |
| `speak(text)` | bridge-local `espeak-ng` synth + `audio.play_pcm`, same path as `webapp/api/speak.py`, capped at 500 chars | yes |
| `stop()` | zero velocity + `runner.abort()` | **never** |
| `get_imu_state()` | `imu.update()` snapshot (roll/pitch/yaw/gyro/accel) | **never** |
| `get_status()` | mode, gait backend, broker owner, hardware_status, moving flag, `current_face` | **never** |

Gated tools check `broker.allow_brain_motion()` before acting, matching `RobotSession._handle_cmd`'s existing
behavior, and return a structured result (e.g. `{"ok": False, "error": "web-control-active"}`) instead of
raising — the LLM sees the rejection in the tool result and can react (e.g. mention it), rather than the
command silently vanishing as it does today. This also *closes* an existing gap: today the web dashboard's
face (`MotionService.face`) and Say (`post_speak`) are gated on `is_web_controller`, but the brain's face-set
(`RobotSession._handle_cmd`) is not gated at all — after this change both actors go through the identical
broker check.

**Single in-flight movement guard.** Today only one non-web actor (the brain, via its one WS session) ever
issues motion commands, so nothing arbitrates *between* non-web actors. Once MCP allows a human MCP client to
connect *alongside* the brain's own tool-calling loop, both are "non-web," and `ControlBroker.allow_brain_motion()`
permits both simultaneously — two callers could issue conflicting `run_pose`/`turn` calls with nothing stopping
them from racing. The MCP server tracks a single in-flight movement task (mirroring `MotionService.pose()`'s
existing "pose-running" guard) so any two overlapping motion-tool calls — regardless of which MCP client sent
them — serialize instead of racing; a call arriving while another is in flight gets `{"ok": False, "error":
"movement-in-progress"}`. `set_face` and `speak` are not movement and are not subject to this same-task guard —
they can run alongside an in-flight `run_pose`/`walk`.

### 2a. Face selection and the talk/revert reflex

`display.py`'s emotion faces (`happy/sad/angry/surprised/sleepy/love/excited/confused/thinking/idle` — the same
set `agent.py`'s `VALID_FACES` already validates against) each have a `talk_<name>` asset variant used only
while TTS audio is actually playing. `set_face(name)` accepts any name `FaceDisplay` has art for; the LLM's
tool description only advertises the plain emotion names, but the tool itself doesn't special-case them — the
talk/revert reflex (below) calls the identical tool with `talk_<name>`, so there is exactly one code path to
the display, not two. Mode (`AnimMode.ONCE` vs the animated variants) is chosen inside the tool based on the
name, not exposed as a parameter.

The switch to `talk_<name>` while speaking and back to `<name>` afterward is a **timing reflex**, not a per-turn
LLM decision — mirrored on the brain-side TTS-playback code exactly like the existing audio-direction "turn
toward speaker" reflex (§4): it calls `get_status()` to learn `current_face` (whatever the LLM last set this
turn, or the previous value if it didn't call `set_face` at all), calls `set_face("talk_" + current_face)` when
TTS playback starts, and `set_face(current_face)` when it ends.

**Auth.** Reuses `PairedStore` rather than a new mechanism. Every MCP request carries `Authorization: Bearer
<token>` and `X-Milo-Peer: <peer_id>`; the server checks `PairedStore.token_for(peer_id) == token`. Since there's
no live challenge/response socket handshake for a bare HTTP client, a new CLI command,
`python -m milo_bridge.cli mcp-pair --name <peer-name>`, mints a token, adds it to the same `paired.json` store,
and prints it once for the operator to paste into the brain's config or a Claude Desktop/Code MCP server config.

### 3. Protocol addition (`common/milo_common/protocol.py`, `handshake.py`)

`T_HELLO` gains one field, `mcp_url`, sent by the robot during the existing handshake — the brain learns the
bridge's MCP endpoint from the connection it's already making, without a second discovery mechanism.
`PROTOCOL_VERSION` bumps.

### 4. Brain-side tool-calling loop (`brain/milo_brain/llm/agent.py`, `session.py`)

`CognitionAgent.on_utterance` replaces its single JSON-schema chat call with a bounded loop:

1. Build context as today (speaker, graph neighbors, recent events), plus a one-line IMU/status summary.
2. Call Ollama's `/api/chat` with `tools=<movement tool schemas>`, fetched from the bridge's MCP server once at
   session start (so the tool list always matches what that specific robot exposes).
3. If the response includes `tool_calls`, execute each against the bridge's MCP client, append the results as
   tool messages, and loop — capped at **4 rounds**, so a local 3B model can never wedge a conversation turn.
4. Once the model returns a final message with no tool calls, parse it against a **shrunk** schema:
   `{reply, facts}` — `move` and `face` are both gone; whatever happened during the tool-calling rounds *is*
   the movement and expression.
5. `sanitize()` drops the `move` and `face` fields/whitelists entirely (the `VALID_FACES` constant moves to
   describing the `set_face` tool's guidance instead of validating a JSON field). `SYSTEM_PROMPT` drops the
   `move`/`face` lines from its JSON schema description and gains brief guidance on when to use movement tools,
   when to change expression via `set_face`, when to use `speak` for something unprompted, and to check IMU
   state before/after moving.

Discrete poses (`run_pose`) are fire-and-forget: the tool returns as soon as the command is issued, matching
today's "reply, then move" behavior — the loop never blocks on a multi-second animation. `speak` is likewise
fire-and-forget on playback (synthesis is awaited so failures surface, playback isn't).

**Reflex migrations.** Two existing behaviors move from the socket protocol to direct MCP tool calls — neither
is an LLM decision, so both bypass the model entirely but still go through the same broker gating/serialization
as LLM-initiated calls:

- The audio-direction "turn toward speaker" logic in `RobotCognitionSession._handle_segment` currently sends
  `T_CMD move={"turn": bearing}`; it becomes a direct `turn(direction)` MCP call.
- The talk/revert face-flip around TTS playback (§2a) currently sends `T_CMD face=...`; it becomes direct
  `set_face(...)` MCP calls bracketing playback.

### 5. Offline IMU characterization harness (bridge CLI)

New subcommand: `python -m milo_bridge.cli characterize [--pose NAME | --all] [--out DIR] [--yes]`.

For each target (every `poses.POSES` entry, plus a few representative velocity walk/turn samples via
`GaitEngine`):

1. Build real hardware (`ServoDriver.from_hardware`, `Mpu6050.from_hardware`), exactly like the CLI's existing
   `_hardware()` helper, and calibrate gyro bias first — the robot must be still and level to start.
2. Run the movement to completion, sampling IMU at 50 Hz throughout plus a short settle window afterward.
3. Record: peak `|roll|`/`|pitch|`, residual tilt at end, gyro peak magnitude (jerkiness proxy), and settle time
   (first point after completion where tilt stays under 3° for 0.5s).
4. Return to `standby()` between movements so every test starts from the same known baseline.
5. Flag anything exceeding a hard safety ceiling (default 45° peak tilt) as unsafe, skip remaining cycles for
   *that* pose, log it, and continue — one bad pose never aborts the whole run.

Output: `~/.milo/characterization/<timestamp>/report.md` (table: pose, peak roll/pitch, settle time, pass/fail)
plus `data.json` (raw samples), so a rerun after a servo recalibration or gait tuning change can be diffed
against a previous run.

## Testing

All off-hardware except where noted, following the existing pattern (injected fakes/fake clocks, no real I2C):

- New `bridge/tests/test_mcp_server.py`: each tool against `FakeServos`/`FakeImu`/`FakeRunner` (already exist in
  `test_gait.py`) plus a fake `FaceDisplay`/`AudioIO` — gating (denied under web control, allowed otherwise),
  `stop`/`get_imu_state`/`get_status` never gated, clamping, correct pose dispatch, `set_face` accepting both
  plain and `talk_`-prefixed names, `speak`'s 500-char cap and synthesis-failure handling, and the single
  in-flight movement guard rejecting a second overlapping motion call while leaving `set_face`/`speak` unaffected.
- `common/tests/test_protocol.py` / handshake tests: extend for the new `mcp_url` `T_HELLO` field.
- New brain-side tests for `CognitionAgent`'s tool-calling loop: fake MCP client + scripted Ollama tool-call
  sequences — verify the 4-round cap, the shrunk `{reply, facts}` schema parsing, that the audio-direction
  reflex calls `turn` directly without going through the LLM, and that the TTS talk/revert reflex calls
  `set_face` with the correct `talk_<name>`/`<name>` pair around playback.
- New `bridge/tests/test_characterize.py`: the peak-tilt/settle-time analysis math against synthetic IMU sample
  arrays — no hardware needed for this part.

**Cannot be verified off-hardware:** whether MCP tool calls actually move, re-face, or speak through the real
robot correctly, real end-to-end pairing of a Claude Desktop/Code MCP client against the bridge, the actual
characterization report numbers for each pose, and whether Ollama's `llama3.2:3b` reliably produces usable
`tool_calls` in practice — these need a pass on real hardware after implementation, including likely
prompt/timeout tuning.
