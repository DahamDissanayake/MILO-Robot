# Milo Web Dashboard: Console Redesign

Status: approved
Author: Claude (with Daham Dissanayake)
Date: 2026-07-13

## 1. Summary

Replace the current free-form, draggable/resizable card-grid dashboard
(`bridge/milo_bridge/webapp/`) with a fixed, purpose-built "cockpit" console:
a top status bar, a center console (camera → move controls → a new unified
communication panel) flanked by a sensors panel, a full-width Obsidian-style
memory graph, and a collapsible drawer for secondary tools (poses, servo
test, bridge log). The redesign is responsive-first: a real desktop layout
and a real mobile layout (CSS `@media` breakpoints), not the current bare
column-count JS fallback.

Stack stays as-is: hand-written ES modules served as static files by
`aiohttp`, zero build step, zero `npm`/`package.json`. This is the
lightest, fastest option for an app served directly off a Raspberry Pi with
no install step for the client.

## 2. Motivation

- The current Sensors card shows almost nothing useful (an IMU sparkline
  and four presence dots) despite more telemetry already existing on the
  wire.
- Ears and Voice are two separate cards for what is conceptually one
  "talk and listen to Milo" feature.
- The draggable/resizable per-browser grid is flexible but doesn't read as
  a coherent "console" — camera and robot control are peers with a bridge
  log card in the current layout.
- There's no real mobile layout — just a 12-column-to-2-column reflow with
  no touch-specific sizing.
- The Memory Graph is invisible until you search; it should read like an
  always-growing map of what Milo knows, à la Obsidian's graph view.

## 3. Non-goals

- No change to the control/safety model (`ControlBroker`, heartbeat
  expiry, STOP exemption, brain-vs-web motion arbitration). This is a
  visual/structural redesign of the frontend, not a safety-model change.
- No new sensor hardware. The redesigned Sensors panel surfaces exactly
  the telemetry that already exists (IMU, SoC temp, CPU%, RAM%, hardware
  presence) — no battery, distance, or other new sensor types.
- No framework/build-tooling migration. Vanilla ES modules stay.
- No change to authentication, login page structure, or session handling.

## 4. Architecture

Kept as-is:
- `aiohttp` backend serving static files + REST + one WebSocket (`/ws`).
- `bus.js` — the WebSocket client (JSON topics + binary audio framing,
  auto-reconnect with backoff). Reused unchanged.
- The panel contract: a plain object with `id`, `title`, and a
  `mount(el, { bus }) -> cleanup?` function. Panels remain independently
  written/testable units, same idea as today's cards.

Replaced:
- `grid.js` (masonry drag/resize/persist-to-`localStorage` engine) is
  removed. In its place, a new `layout.js` mounts each panel into a named
  DOM slot defined by the fixed page structure (see §5) — no positioning
  math, no drag handles, no per-browser persisted arrangement.
- `registry.js`'s flat card list becomes a zone-grouped panel registry
  (e.g. `{ statusbar: [...], cockpit: { center: [...], side: [...] },
  graph: [...], tools: [...] }`).

## 5. Page structure

### Desktop

Top to bottom:

1. **Status bar** (`#statusbar`, sticky, full width) — brand, connection
   dot, link state, owner, gait backend, CPU%, SoC temp, RAM%, uptime
   (compact stat readout), Take Control, STOP, Logout, theme toggle, and a
   new **Tools** toggle button. This merges today's header bar and Status
   card into one persistent strip.
2. **Cockpit** (`#cockpit`, 2-column CSS grid) —
   - **Center column** (wide): Camera feed, Move controls directly
     beneath it, Communication panel beneath that.
   - **Side column** (narrower): Sensors panel.
3. **Memory Graph** (`#memory-graph`, full width) — search bar plus the
   force-directed graph canvas.
4. **Tools drawer** — off-canvas panel (slide-in from the side), opened
   by the status bar's Tools button, containing Poses & Emotes, Servo
   Test, and Bridge Log. Closed by default.

### Mobile (`@media (max-width: 900px)` or similar — exact breakpoint
decided during implementation against real device widths)

Single column, stacked in priority order:

1. Condensed status bar — brand, connection dot, Take Control/STOP always
   visible; CPU/temp/RAM/uptime collapse behind a tap-to-expand affordance
   to save vertical space.
2. Camera
3. Move controls
4. Communication panel
5. Sensors (compact tile grid, e.g. 2 columns)
6. Memory Graph section
7. Tools — opens as a full-screen overlay (not a side drawer) on narrow
   viewports.

Touch targets (joystick zone, mic button, sliders, buttons) sized for
fingers (≥44px) on mobile; this is new — today's controls use uniform
sizing regardless of viewport.

### Explicit capability removal

The current per-browser draggable/resizable/hideable card layout
(position, size, and hidden-state persisted in `localStorage` per
`docs/WEB-DASHBOARD.md` §3) is removed. This is intentional: a fixed
console layout is the whole point of this redesign, traded for a
consistent, purpose-built interface over per-user rearrangement. The
header's **+ Card** and **⟲ Reset layout** buttons are removed along with
it, replaced by the Tools drawer toggle.

## 6. Panels

### Status bar

Merges today's `<header id="topbar">` and the Status card. Data comes
from the existing `telemetry` and `control` WS topics — no backend
change. Buttons (`Take Control`, `STOP`, `Logout`, theme toggle) keep
their current click handlers/semantics from `main.js`.

### Camera

Unchanged behavior: `<img>` consuming the MJPEG stream at
`/stream/camera`, client-side canvas Snapshot button. Restyled larger and
centered as the visual anchor of the console. No backend change.

### Move (controls)

Unchanged behavior: on-screen joystick, WASD/arrow + Q/E keyboard, speed
slider, local STOP, sending `{t:"gait",vx,vy,yaw}` at 100ms cadence.
Restyled to sit directly beneath the camera. No backend change.

### Communication panel (new — replaces Ears + Voice)

One panel, four elements, split control-gating (matches today's
per-card gating, just visually unified):

- **Headphones icon** — toggles listening on/off. When on, subscribes to
  the WS binary audio channel and plays it back via Web Audio (today's
  Ears behavior). No control required — anyone can listen, any time.
- **Vertical VU meter** — shows the robot's incoming mic level, computed
  from the same PCM stream the headphones toggle subscribes to. Green up
  to a "loud but clean" amplitude threshold, red near clipping. Runs
  whenever the headphones toggle is on.
- **Push-to-talk mic button** — hold to capture, resample, and stream
  the user's mic to the robot's speaker (today's Voice hold-to-talk).
  Dimmed/locked (with the same lock-overlay treatment as today's
  `needsControl` cards) unless `bus.controlled` is true.
- **Text input + Say button** — `POST /api/speak` for TTS, same as
  today's Voice text box. Same control-gating as push-to-talk.

No backend change — reuses the existing WS binary audio protocol and
`/api/speak`.

### Sensors panel

Tiles, one per live signal that actually exists on the robot:

- IMU attitude — pitch/roll as a small tilt indicator plus numeric
  readout.
- Gyro — deg/s (magnitude or per-axis, decided during implementation).
- SoC temperature.
- CPU%.
- RAM%.
- Hardware presence — camera / audio / imu / display dots (from
  `/api/status`'s `hardware` object, fetched once).

A **Details** control (button or expand affordance) on the panel opens a
fuller view with rolling history sparklines for each numeric signal —
extending today's pitch/roll-only sparkline (`cards/sensors.js`) to also
cover temp/CPU/RAM. History is kept client-side in a rolling buffer fed
by the existing `telemetry` WS broadcast; no backend change.

### Memory Graph

Search bar is kept. On mount, the panel now fetches and renders the
**entire** graph by default (force-directed layout, styled to read as
"alive" — nodes/edges with an Obsidian-like glow/spacing aesthetic to be
detailed at implementation time by the frontend-design skill), instead of
staying empty until a search is run. New nodes pushed via
`POST /api/graph` animate into the existing layout rather than requiring
a fresh search. Typing in the search bar highlights/focuses matching
nodes within the already-visible graph rather than being the only way to
populate it.

**Backend change required:**
- `bridge/milo_bridge/graph/store.py`: add `GraphStore.all(limit=200)`
  returning `{nodes, edges}` for the whole graph (capped; ordering choice
  — e.g. most-recently-touched-first — decided at implementation time).
- `bridge/milo_bridge/webapp/api/graph.py`: `get_search` calls
  `deps.graph_store.all(limit)` when `q` is empty, instead of
  short-circuiting to `{"nodes": [], "edges": []}`. The route stays
  `GET /api/graph/search` — no new endpoint, just a change to its
  empty-query behavior.

### Tools drawer

Poses & Emotes, Servo Test, and Bridge Log carry over with unchanged
internal logic (`cards/poses.js`, `cards/servos.js`, `cards/log.js` →
relocated into the drawer's panel list), just removed from the main
draggable grid and placed in a collapsible drawer opened from the status
bar. No backend change.

## 7. Data flow & control model

Unchanged. Single WebSocket (`bus.js`) carries `telemetry`, `control`,
`log`, `gait`, `pose`, `face`, `servo`, `servo_batch`, `audio`,
heartbeat, and binary audio frames. REST endpoints
(`/api/login`, `/api/logout`, `/api/status`, `/stream/camera`,
`/api/speak`, `/api/graph`, `/api/graph/search`, `/api/poses`,
`/api/faces`, `/api/logs`) are unchanged except the `/api/graph/search`
empty-query behavior described in §6. `ControlBroker` semantics (Take
Control exclusivity, brain-vs-web arbitration, heartbeat-based expiry,
STOP's unconditional exemption) are untouched — see
`docs/WEB-DASHBOARD.md` §4 for the existing, still-accurate description.

## 8. Error handling

- WS reconnect/backoff — unchanged, already handled in `bus.js`.
- Camera stream failure — show a proper "camera offline" placeholder
  state (styled, not a browser broken-image icon); the `<img>` element
  must keep attempting to update rather than freezing on the broken
  state, matching today's requirement in the manual smoke checklist.
- TTS unavailable (`tts-unavailable` from `/api/speak`) — surfaced
  inline in the communication panel's Say control, same as today's Voice
  card.
- Memory graph full-fetch failure — inline error text in the graph
  section; does not block or break the rest of the page.

## 9. Testing

- Update (or replace) `bridge/tests/webapp/test_static_integrity.py` to
  check the new zone-grouped panel registry instead of the old flat card
  registry, keeping the same guarantee: a registered panel's file can't
  silently go missing.
- Add a backend test for `GraphStore.all()` and the updated
  empty-query `get_search` behavior (alongside existing graph tests).
- Rewrite the manual smoke checklist in `docs/WEB-DASHBOARD.md` for the
  new panels/layout: status bar contents, cockpit zones, communication
  panel split-gating (headphones work without control, mic/Say don't),
  sensors panel Details view, memory graph loading fully on open plus
  search-highlight behavior, tools drawer open/close on both desktop and
  a mobile viewport width. Drop the now-removed drag/resize/reset-layout
  checks.
- Control/safety-model tests (`ControlBroker` exclusivity, STOP
  exemption, heartbeat expiry) are untouched by this work and don't need
  new coverage here.
- Visual/aesthetic polish (exact colors, spacing, the Obsidian-style
  graph look) is implementation-time work for the frontend-design skill,
  not something to spec numerically here.

## 10. Rollout

Single-branch rewrite of `bridge/milo_bridge/webapp/static/` plus the two
small backend changes in §6. No feature flag or gradual rollout — the
old grid-based UI is fully replaced. `bridge/tools/webdev.py` (fake
drivers, off-Pi dev server) continues to be the way to iterate on and
verify this without real hardware.
