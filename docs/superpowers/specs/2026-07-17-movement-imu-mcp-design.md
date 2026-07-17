# Movement & IMU MCP Engine

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
balance or react to it. There is also no way to verify, offline, how each movement actually behaves on real
hardware (peak tilt, settle time, stability), so movement additions and gait/servo tuning changes are unverified
until someone watches the robot do it in person.

## Goals

- Give the brain's LLM real, multi-step tool-calling control over the robot's entire movement repertoire and
  live IMU telemetry, via a standard MCP server — not a bigger hardcoded enum.
- Run that MCP server on the bridge (robot side), so both the brain's own agent loop and a human's MCP client
  (Claude Desktop/Code) can drive or inspect movement for manual testing/debugging.
- Reuse the existing pairing trust store (`common/milo_common/auth.py` `PairedStore`) for MCP access control —
  no new auth system.
- Preserve the existing `ControlBroker` motion-arbitration guarantee (web client always wins when it holds
  control; STOP and telemetry reads are never gated) for every MCP-originated motion command.
- Produce a repeatable, offline per-movement IMU characterization report (peak tilt, settle time, pass/fail
  against a safety ceiling) usable after any gait/servo tuning change.

## Non-goals

- Face/display control — stays exactly as it is today (`T_CMD` `face` field, unchanged).
- Camera/audio perception upgrades (scene understanding, sound-event detection) — separate sub-project.
- The Linux CLI/TUI brain redesign — separate sub-project.
- Backward compatibility with brains/bridges that don't understand the new protocol — this is a coordinated
  upgrade of both sides (see Migration below), not a fleet-compatible rollout.
- Full inverse-kinematics or any new balance algorithm — the existing `GaitEngine`/`balance.py` behavior is
  exposed as-is, not redesigned.

## Design

### 1. Architecture

Two independent channels exist between bridge and brain after this change:

- The existing authenticated WebSocket (robot dials out to the brain, per `handshake.py`) — still carries
  video/audio/TTS/graph/face frames, **unchanged**.
- A new MCP server on the bridge, reachable over HTTP — carries only movement commands and IMU/status
  telemetry. The brain (and, for manual testing, a human's MCP client) dials **in** to it.

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
| audio/TTS/graph/face) ------>| brain, unchanged                              |
+-----------------------------+               +-------------------------------+
```

`move` is removed entirely from `T_CMD`. `face` stays on the socket protocol.

### 2. MCP server (bridge-side, new `bridge/milo_bridge/mcp/` package)

Built with the official `mcp` Python SDK (`mcp.server.fastmcp.FastMCP`), served over Streamable HTTP on its own
port (default `8765`), started alongside the existing aiohttp dashboard in `main.py` (same graceful-degradation
pattern as every other optional subsystem there).

Tools, each a thin wrapper around objects `main.py` already constructs (`gait`, `runner`, `imu`, `broker`,
`servos`):

| Tool | Wraps | Gated by broker? |
|---|---|---|
| `walk(vx, vy, yaw_rate)` | `gait.set_velocity_command`, clamped to the same `VX_LIM`/`VY_LIM`/`YAW_LIM` as `webapp/motion.py` | yes |
| `run_pose(name, cycles=None)` | `runner.run(name)`, `name` in `poses.POSES` | yes |
| `turn(direction)` | continuous `turn_left`/`turn_right` pose, matches `MotionService.turn` | yes |
| `set_mode(name)` | `gait.set_mode`, `name` in `raw/balanced/angled` | yes |
| `reset()` / `standby()` | `gait.reset()` / `gait.standby()` | yes |
| `relax()` / `hold()` | `servos.relax()` / `servos.hold()` | yes |
| `stop()` | zero velocity + `runner.abort()` | **never** |
| `get_imu_state()` | `imu.update()` snapshot (roll/pitch/yaw/gyro/accel) | **never** |
| `get_status()` | mode, gait backend, broker owner, hardware_status, moving flag | **never** |

Gated tools check `broker.allow_brain_motion()` before acting, matching `RobotSession._handle_cmd`'s existing
behavior, and return a structured result (e.g. `{"ok": False, "error": "web-control-active"}`) instead of
raising — the LLM sees the rejection in the tool result and can react (e.g. mention it), rather than the
command silently vanishing as it does today.

**Single in-flight movement guard.** Today only one non-web actor (the brain, via its one WS session) ever
issues motion commands, so nothing arbitrates *between* non-web actors. Once MCP allows a human MCP client to
connect *alongside* the brain's own tool-calling loop, both are "non-web," and `ControlBroker.allow_brain_motion()`
permits both simultaneously — two callers could issue conflicting `run_pose`/`turn` calls with nothing stopping
them from racing. The MCP server tracks a single in-flight movement task (mirroring `MotionService.pose()`'s
existing "pose-running" guard) so any two overlapping motion-tool calls — regardless of which MCP client sent
them — serialize instead of racing; a call arriving while another is in flight gets `{"ok": False, "error":
"movement-in-progress"}`.

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
   `{reply, face, facts}` — `move` is gone; whatever happened during the tool-calling rounds *is* the movement.
5. `sanitize()` drops the `move` field/whitelist entirely; `VALID_FACES` handling is unchanged. `SYSTEM_PROMPT`
   drops the `move` line from its JSON schema description and gains brief guidance on when to use movement tools
   and check IMU state before/after moving.

Discrete poses (`run_pose`) are fire-and-forget: the tool returns as soon as the command is issued, matching
today's "reply, then move" behavior — the loop never blocks on a multi-second animation.

**Reflex migration.** The audio-direction "turn toward speaker" logic in `RobotCognitionSession._handle_segment`
currently sends `T_CMD move={"turn": bearing}`. It moves to a **direct MCP tool call** (`turn(direction)`)
through the same MCP client the LLM loop uses — this is a reflex, not an LLM decision, so it bypasses the model
entirely but still goes through the one movement API and the same broker gating/serialization.

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
  `test_gait.py`) — gating (denied under web control, allowed otherwise), `stop`/`get_imu_state`/`get_status`
  never gated, clamping, correct pose dispatch, and the single in-flight movement guard rejecting a second
  overlapping call.
- `common/tests/test_protocol.py` / handshake tests: extend for the new `mcp_url` `T_HELLO` field.
- New brain-side tests for `CognitionAgent`'s tool-calling loop: fake MCP client + scripted Ollama tool-call
  sequences — verify the 4-round cap, the shrunk `{reply, face, facts}` schema parsing, and that the
  audio-direction reflex calls the MCP client directly without going through the LLM.
- New `bridge/tests/test_characterize.py`: the peak-tilt/settle-time analysis math against synthetic IMU sample
  arrays — no hardware needed for this part.

**Cannot be verified off-hardware:** whether MCP tool calls actually move the real robot correctly, real
end-to-end pairing of a Claude Desktop/Code MCP client against the bridge, the actual characterization report
numbers for each pose, and whether Ollama's `llama3.2:3b` reliably produces usable `tool_calls` in practice —
these need a pass on real hardware after implementation, including likely prompt/timeout tuning.
