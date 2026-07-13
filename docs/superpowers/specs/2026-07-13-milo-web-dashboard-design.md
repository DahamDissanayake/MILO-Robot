# Milo Web Dashboard — LAN Web App at `http://milo.local`

**Date:** 2026-07-13
**Status:** Approved

## Purpose

Give any browser on the LAN a full window into — and controls for — the
robot: live camera, live microphone audio, speaking through Milo's speaker,
movement and pose/emote control, per-servo testing, sensor readouts, a
visual searchable view of the knowledge graph, and bridge logs. The
dashboard is served *by the robot itself* from the `milo-bridge` process and
reachable at a stable LAN name, `http://milo.local`.

This complements (not replaces) the SSH tools: IOT-Testing remains the
wiring-validation TUI; MILO-Dashboard remains the SSH system monitor. The
web dashboard is the day-to-day "live cockpit".

## Scope

In scope: an `aiohttp` web server embedded in the bridge process; mDNS
hostname setup so `milo.local` resolves; a `ControlBroker` for exclusive
motion control; a `MediaHub` fanout so camera/audio serve the brain and web
clients simultaneously; ten feature cards (below); a no-build vanilla-JS
card framework with drag-and-drop reorder, resize, persistence, and
light/dark monochrome theming; graph text search added to `GraphStore`;
pytest coverage with fake drivers; docs for setup and for writing new cards.

Out of scope: authentication (deliberately open on the LAN, per decision);
WAN/remote access; recording/storage of media; changing the brain protocol;
touching `training/` or `brain/`.

## Decisions (locked)

- **Embedded server** — the dashboard lives inside `milo_bridge` (approach
  A): hardware is process-exclusive, so only the bridge process can serve
  it. New runtime dep for the bridge: `aiohttp>=3.9`.
- **Observe always, control on request** — media/telemetry/graph are
  multi-client and always available. Motion (gait, poses, servos, emotes,
  speaker) requires holding the single control slot.
- **No-build vanilla JS** — ES modules + CSS custom properties, all assets
  vendored/local, no npm, no CDN, works offline.
- **Open on LAN** — no login. (Revisit only if the user asks.)

## `milo.local`

- Pi hostname set to `milo`; `avahi-daemon` (already required for zeroconf)
  publishes `milo.local` via mDNS. Setup is documented, not coded:
  `sudo raspi-config nonint do_hostname milo && sudo reboot`.
- Server listens on `0.0.0.0:80` by default so the URL needs no port.
  `bridge/systemd/milo-bridge.service` gains
  `AmbientCapabilities=CAP_NET_BIND_SERVICE` and
  `CapabilityBoundingSet=CAP_NET_BIND_SERVICE`. Port lives in
  `BridgeConfig` (`web_port`, default 80); if binding fails the server
  retries on 8080 and logs the fallback.

## Architecture

```
bridge/milo_bridge/webapp/
  __init__.py         — create_app(deps) -> aiohttp.web.Application
  deps.py             — WebDeps dataclass: runner, display, camera, audio,
                        gait, graph_api, graph_store, servos, imu, broker,
                        media_hub, log_buffer, config
  server.py           — start_web(deps) task: bind 80→8080, serve static+api
  control.py          — ControlBroker
  media_hub.py        — MediaHub (camera + audio fanout)
  logbuf.py           — RingBufferLogHandler (attach to root logger)
  api/
    __init__.py       — register_routes(app, deps): one line per module
    status.py         — GET /api/status
    motion.py         — WS-dispatched gait/pose/servo/emote handlers
    media.py          — GET /stream/camera (MJPEG), audio WS binary framing
    speak.py          — POST /api/speak (TTS), WS binary intercom in
    graph.py          — POST /api/graph (op passthrough), GET /api/graph/search
    logs.py           — GET /api/logs
  ws.py               — /ws endpoint: JSON dispatch + binary audio, heartbeat
  static/
    index.html
    css/theme.css     — custom properties, light/dark monochrome
    css/grid.css      — dashboard grid, drag/resize affordances
    js/main.js        — boot: registry → grid → websocket
    js/registry.js    — list of card modules (add a card = one line here)
    js/grid.js        — drag-drop reorder, resize, localStorage persistence
    js/bus.js         — WS client wrapper: reconnect w/ backoff, topic pubsub
    js/cards/*.js     — one module per card
```

**Startup**: `main.py` builds `WebDeps` from the objects it already
constructs, attaches the log ring buffer, wires `MediaHub` between drivers
and `SessionManager`, and `asyncio.create_task(start_web(deps))`. Web
server failure logs an error but never kills the robot service.

## ControlBroker

- State: `owner: "brain" | "web" | None`, plus the owning web client id.
- `acquire_web(client_id)` succeeds unless another web client holds it
  (brain never *holds* the slot; it has it implicitly when no web client
  does). `release_web(client_id)`; auto-release when the client's WS
  closes or misses heartbeats for 10 s.
- `allow_brain_motion() -> bool` — checked by `RobotSession.dispatch` for
  motion messages (pose/gait/servo); media and graph traffic unaffected.
  While a web client owns control, brain motion commands are dropped with a
  log line.
- Motion API handlers require the caller to be the owning web client;
  otherwise they return `{"error": "not-controlling"}`.
- **STOP is exempt**: `stop` (abort pose, zero gait velocity) is honored
  from anyone, always.
- Broadcasts owner changes to all WS clients (`{"t":"control","owner":...}`).

## MediaHub

- One reader task per driver generator (`camera.frames()`,
  `audio.capture_frames()`), started lazily on first subscriber, stopped
  when the last unsubscribes.
- Subscribers get an `asyncio.Queue(maxsize=2)`; on overflow the oldest
  frame is dropped (slow browser never stalls the brain).
- `streams.pump_video/pump_audio` switch from driver generators to hub
  subscriptions — behavior toward the brain socket unchanged (still feeds
  `on_level` for the sleep controller from the hub's audio reader).
- Fully testable with fake async generators.

## Transports

- **`GET /stream/camera`** — `multipart/x-mixed-replace` MJPEG from a hub
  subscription. Works in a plain `<img>` tag, multi-client.
- **`WS /ws`** — per-client socket. Text frames: JSON
  `{"t": <topic>, ...}` both directions. Binary frames: audio PCM —
  server→client mic audio (prefix byte `0x01`), client→server intercom
  audio for the speaker (prefix `0x02`). Client subscribes to audio with
  `{"t":"audio","on":true}`.
- **REST** — `GET /api/status`, `POST /api/graph`, `GET /api/graph/search?q=`,
  `GET /api/logs?n=200`, `POST /api/speak {"text": ...}`,
  `GET /api/poses`, `GET /api/faces`.

Telemetry (`{"t":"telemetry"}`, 2 s interval): IMU pitch/roll/gyro, CPU %,
SoC temp, RAM %, uptime, throttle flags, brain link state, control owner,
gait backend — assembled server-side, pushed to every WS client.

## Feature cards

| # | Card | Backend surface | Notes |
|---|------|-----------------|-------|
| 1 | Status | telemetry push | link state, owner, CPU/temp/RAM, uptime, gait backend, throttle flags |
| 2 | Camera | `/stream/camera` | live MJPEG + snapshot (canvas grab) |
| 3 | Ears (listen) | WS binary `0x01` | Web Audio playback + stereo VU meters |
| 4 | Voice (speak) | WS binary `0x02`, `POST /api/speak` | push-to-talk from browser mic (getUserMedia, downsampled to the robot rate); text→`espeak-ng` piped to `play_pcm`; card shows "TTS unavailable" if espeak-ng missing. Requires control. |
| 5 | Move | WS `{"t":"gait","vx","vy","yaw"}` | joystick + WASD/arrows, speed slider, STOP. 500 ms command timeout → auto-zero. Requires control. |
| 6 | Poses & Emotes | `GET /api/poses`, `GET /api/faces`, WS `{"t":"pose"}` / `{"t":"face"}` | buttons per `POSES` key and face PNG (thumbnails served from assets). Requires control. |
| 7 | Servo Test | WS `{"t":"servo","ch","deg"}` | 8 sliders (R1…L4 map), center-all-90°. Requires control. |
| 8 | Sensors | telemetry + `GET /api/status` | IMU sparklines (canvas), hardware presence (i2c/camera/mic/oled), refresh |
| 9 | Memory Graph | `POST /api/graph`, `GET /api/graph/search` | canvas force-directed layout (hand-rolled, ~150 lines: repulsion + springs), nodes colored by type, click → props panel, search highlights matches and neighbors |
| 10 | Log | `GET /api/logs` + WS `{"t":"log"}` push | ring buffer tail, level filter |

## Graph search (backend addition)

`GraphStore.search_text(q, limit=25)` — SQL `LIKE` over node type and the
JSON props column, returning nodes + their edges. Exposed as
`GET /api/graph/search?q=` and as a new `GraphApi` op (`search_text`) so
the brain gains it for free. `POST /api/graph` accepts the existing op
dicts and passes straight to `GraphApi.handle()` (read AND write ops —
the graph is the robot's memory and the dashboard may edit it).

## Frontend card framework

- Card module contract:
  `export default { id, title, defaultSize: {w, h}, needsControl?: bool,
  mount(el, ctx) -> unmount? }` where `ctx = { bus, api, controlled }`.
- `registry.js` imports and lists modules — **adding a card = one new file
  + one line**, documented in `docs/WEB-DASHBOARD.md`.
- `grid.js`: CSS grid (12 columns, row unit 80 px). Header drag to
  reorder (pointer events, drop indicator); corner handle to resize in
  grid units; per-card hide (✕) and an "Add card" menu listing hidden ones;
  "Reset layout". Layout persisted in `localStorage`
  (`milo.layout.v1`); unknown/removed card ids ignored on load.
- `bus.js`: single WS with exponential-backoff reconnect, topic
  subscribe/publish, binary handlers, heartbeat every 5 s.
- Cards render an explicit "unavailable" body when their backend reports
  the hardware missing; the grid never breaks.

## Theming

Monochrome black/white. `:root` custom properties; light = near-white
surface/black ink, dark = near-black surface/white ink; single functional
accent pair (ok-green, danger-red) used only for state. Default follows
`prefers-color-scheme`; header toggle overrides, persisted in
`localStorage`. System font stack; hairline borders; no shadows in dark
mode.

## Error handling

- Web server crash → logged, robot keeps running; server task restarts
  once after 5 s, then stays down until service restart.
- Missing camera/audio/espeak-ng/policy → status flags them; cards render
  unavailable states.
- WS client vanishes → hub unsubscribe + control auto-release.
- Gait command staleness (500 ms) → velocities zeroed server-side.
- All API handlers wrap errors into JSON `{"error": ...}`; never a 500
  with a traceback body.

## Testing

pytest, off-hardware, fake drivers (existing test fakes pattern):

- ControlBroker: acquire/deny/release/heartbeat-timeout/brain-drop/STOP-exempt.
- MediaHub: two subscribers get frames; slow subscriber drops oldest, never
  blocks; reader stops when last unsubscribes.
- WS dispatch: gait command routed to fake gait; servo bounds clamped;
  pose name validated; control enforcement (`not-controlling` errors).
- MJPEG handler: boundary framing over a fake camera.
- Graph: `search_text` matches type and props; `/api/graph` passthrough.
- Status/logs/speak endpoints incl. espeak-absent degradation.
- Static integrity: every file referenced by `index.html`/`registry.js`
  exists (guards the "add a card" workflow).

Frontend JS is deliberately thin; no JS test harness. Manual smoke:
`python -m milo_bridge.main` off-Pi + browser, documented checklist.

## Documentation

`docs/WEB-DASHBOARD.md`: `milo.local` hostname setup, port-80 systemd
capability lines, feature tour, the card framework contract, and a worked
"add your own card" example (server route + JS module + registry line).

## Build order

1. Webapp skeleton: `create_app`, static serving, `/api/status`, config
   `web_port`, `main.py` wiring, port fallback.
2. ControlBroker + `/ws` (JSON dispatch, heartbeat, owner broadcast) +
   session-manager motion gate.
3. MediaHub + `streams.py` refactor + `/stream/camera` + audio WS out.
4. Motion APIs: gait (with staleness timer), poses, faces, servos, STOP.
5. Speak: intercom in (binary `0x02` → `play_pcm`) + espeak-ng TTS.
6. Graph: `search_text` in store + API routes.
7. Logs: ring buffer handler + endpoint + WS push.
8. Frontend shell: theme, grid, bus, registry (Status + Log cards prove it).
9. Remaining cards: Camera, Ears, Voice, Move, Poses/Emotes, Servo,
   Sensors, Memory Graph.
10. `docs/WEB-DASHBOARD.md` + systemd unit update + README touch-ups.
