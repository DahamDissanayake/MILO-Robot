# The Milo Web Dashboard

A browser control panel served directly off the robot — no brain, no phone
app, no extra install. Point any device on the same LAN at Milo and you get
a live cockpit: camera, communication (listen and talk), movement, poses,
servo trims, sensors, memory graph, and the bridge's own log, all in one
page.

## 1. What it is

The dashboard is a single-page app served by `milo-bridge` itself (the same
process that runs the gait engine and drivers) over a WebSocket-plus-REST API
on `bridge/milo_bridge/webapp/`. It needs no build step — it's hand-written
ES modules loaded straight from `static/`. Every route is gated behind a
login page (see §7's smoke checklist), so you do need to sign in once per
browser before you can see anything at all — but once you're in, **observation
is still always free**: no panel that only watches (camera, the Communication
panel's listening side, sensors, the status bar's telemetry, the Bridge Log,
the memory graph) requires anything beyond being logged in. Taking the
robot's actuators away from the brain (driving it, posing it, talking through
it) requires explicitly clicking **Take Control**, and one physical **STOP**
button is always live, for anyone, in any tab, whether or not they hold
control.

The page is a fixed "cockpit" console, not a rearrangeable grid: a status
bar runs across the top (brand, connection state, current control owner,
and the page-level action buttons), a two-column cockpit below it puts
Camera → Move → Communication in the center column and Sensors in a side
column, and a full-width Memory Graph section sits below the cockpit.
Less-frequently-used panels — Poses & Emotes, Servo Test, and Bridge Log —
live in a Tools drawer opened from the status bar's **Tools** button. This
layout is identical for every device — there's no per-browser saved
arrangement to diverge between a laptop and a phone — and a real mobile
breakpoint (at 900px and below) reflows the cockpit to a single column and
collapses the status bar's secondary stats behind a **⋯** toggle. It works
in light or dark mode and follows your OS theme by default.

_screenshot to be added after first run_

## 2. Reaching it: `milo.local`

Milo advertises itself over mDNS as `milo.local`, so day to day you just
open `http://milo.local` from any device on the same Wi-Fi. To set this up
once on the robot:

```bash
sudo raspi-config nonint do_hostname milo
```

Confirm mDNS is actually running (Raspberry Pi OS ships it, but verify after
a fresh flash):

```bash
systemctl is-active avahi-daemon   # should print "active"
```

Then reboot so the new hostname takes effect everywhere (mDNS advertisement,
`/etc/hostname`, the shell prompt):

```bash
sudo reboot
```

After the reboot, `http://milo.local` should load the dashboard directly —
the bridge's systemd unit runs with `CAP_NET_BIND_SERVICE` (see §4 below and
`bridge/systemd/milo-bridge.service`) so an unprivileged process can bind
port 80 without being root. If port 80 is ever unavailable — another
service already bound it, or the capability grant didn't take — the server
falls back automatically to port 8080 and logs the fallback, so
`http://milo.local:8080` always works as a second-line address. If
`milo.local` doesn't resolve at all (some routers or client OSes are picky
about mDNS across VLANs or guest networks), fall back to the robot's raw
LAN IP, e.g. `http://192.168.1.42:8080`.

### Logging in

`http://milo.local` lands on a login page. The default seeded credentials
(set on first run in `~/.milo/config.json`, per the `BridgeConfig` fields
`web_username`/`web_password_hash`) are:

- **Username:** `dama`
- **Password:** `MILO@gate`

The password is stored as a salted `scrypt` hash — never in plaintext.
Sessions are per-browser and end when you close the browser (no "stay
logged in" cookie). To change the password later, compute a new hash and
paste it into `~/.milo/config.json`, then restart the service:

```bash
python -c "from milo_bridge.webapp.auth import hash_password; print(hash_password('new-password'))"
```

Edit `~/.milo/config.json`'s `web_password_hash` field to the printed
value (and `web_username` if you want a different username too), then
`sudo systemctl restart milo-bridge`.

## 3. Feature tour

The dashboard is built from a status bar plus seven panels: three in the
fixed cockpit's center column, one in its side column, one full-width
section below the cockpit, and three tucked into a Tools drawer. Every
panel that can move hardware or make noise is marked **needs control**
below; the rest are pure observation and work in every tab, all the time.

- **Status bar** — merges the old header and Status card into one strip
  across the top: brand, a connection dot, who currently owns control
  (`none` / `brain` / `web`), and the page-level actions (Take Control,
  STOP, Tools, Logout, theme toggle). A secondary stat group — Link, Gait
  backend, CPU %, SoC temperature, RAM %, and web-server uptime — sits
  alongside those on desktop, refreshed off the same telemetry broadcast
  every connected tab already receives; below the ~900px mobile breakpoint
  it's hidden behind a **⋯** toggle button so the bar stays one line.
- **Camera** (observe-only, cockpit center) — a live MJPEG feed at
  `/stream/camera`, one hub subscription per browser tab, so opening the
  dashboard on three devices doesn't triple the load on the camera driver
  — they all share the single upstream reader. A **Snapshot** button grabs
  the current frame into a downloadable JPEG client-side, no server round
  trip needed.
- **Move** (needs control, cockpit center) — an on-screen joystick, or
  WASD / arrow keys with Q/E to turn, driving the gait engine's velocity
  command. A speed slider scales every axis together. Commands are sent at
  a steady 100 ms cadence while a direction is held and immediately zeroed
  on release, and there is always a local **STOP** button on the panel in
  addition to the global one in the status bar.
- **Communication** (cockpit center) — replaces the old separate Ears and
  Voice cards. A headphones toggle turns live listening on or off and
  needs no control at all — anyone can listen in — and drives a vertical
  VU meter that fills green and switches to red above roughly half scale.
  Push-to-talk (**Hold to Talk**) and the type-and-**Say** text bar (both
  need control) are gated independently of listening: both stay visibly
  locked until this tab holds control, and if control is lost mid-hold (a
  heartbeat timeout, another tab taking control, or the connection
  dropping) an in-flight push-to-talk session is torn down immediately
  rather than just being blocked from starting again. TTS goes through
  `espeak-ng` on the Pi — it must be installed with
  `sudo apt install espeak-ng`, or `/api/speak` reports `tts-unavailable`
  and the panel shows the error inline.
- **Sensors** (observe-only, cockpit side column) — six live tiles: Pitch
  / Roll, Gyro, SoC Temp, CPU, and RAM, plus a row of hardware-presence
  dots (camera / audio / IMU / display) fetched once from `/api/status`,
  so you can tell at a glance what the robot thinks is actually attached.
  A **Details** toggle reveals two rolling-history sparkline canvases —
  Attitude (pitch/roll) and System (CPU / RAM / Temp) — built from the
  same telemetry stream.
- **Memory Graph** (observe-only, full-width below the cockpit) — a
  force-directed canvas view of Milo's on-robot knowledge graph. It shows
  the entire graph as soon as it mounts, not only after you search, and
  polls every 5 seconds to pick up graph changes from other sources
  (there's no live WebSocket push for graph mutations); newly-arrived
  nodes grow into view over about 400 ms instead of popping in. Typing a
  search term and hitting **Search** (or Enter) highlights matching nodes
  in place and dims the rest, rather than replacing the visible graph —
  **Clear** resets the highlight — and clicking any node shows its full
  type and properties below the canvas.
- **Tools drawer** — opened with the status bar's **Tools** button, holds
  the panels used less often. On desktop it slides in from the right and
  can be closed either by clicking the backdrop or the drawer's own
  **✕ Close** button; on mobile the drawer becomes a full-screen overlay
  that covers both the backdrop and the status bar underneath it, so the
  in-drawer **✕ Close** button is the only way to close it there. It
  contains:
  - **Poses & Emotes** (needs control) — buttons for every scripted pose
    in `milo_bridge.poses` and every face bitmap under `assets/faces/`,
    fetched from `/api/poses` and `/api/faces` so the panel never needs
    updating when poses or faces are added — it just reflects what's on
    the robot.
  - **Servo Test** (needs control) — one slider per servo channel
    (R1–R4, L1–L4), each sending a live `deg` update as you drag, plus a
    **Center All (90°)** button for quickly returning every joint to
    neutral during assembly or calibration work.
  - **Bridge Log** (observe-only) — a live tail of the bridge's own log
    output. It loads the last 100 lines from `/api/logs` on mount, then
    appends new lines in real time as the bridge's `RingBufferLogHandler`
    broadcasts them over the WebSocket — useful for watching what the
    robot is actually doing without SSHing in.

The layout itself is fixed — nothing can be dragged, resized, or hidden —
and the Tools drawer is what replaces the old per-card hide/show and its
`localStorage`-persisted per-browser state.

## 4. Control & safety

Motion, poses, servos, and voice output all funnel through one gate:
`milo_bridge.webapp.control.ControlBroker`. The rules are deliberately
simple:

- **Observation is never brokered.** Camera, the Communication panel's
  listening side, Sensors, the status bar's telemetry, the Bridge Log, and
  the Memory Graph work in every tab regardless of who — if anyone — holds
  control.
- **The brain has motion rights by default.** Whenever no web client holds
  the control slot, `broker.allow_brain_motion()` is true and the brain's
  own gait/pose commands reach the hardware as normal (see
  `bridge/milo_bridge/net/session.py`).
- **Take Control is exclusive and web-only.** Clicking **Take Control**
  sends `{"t":"control","take":true}` over the WebSocket; the broker grants
  it to that client's `client_id` only if no other web client currently
  holds it. While a web client holds control, any brain motion command that
  arrives is intentionally dropped — the session logs
  `"dropping brain motion cmd while web client controls"` and does nothing
  else, so the two control paths can never fight over the servos.
  Releasing control (or the tab disconnecting) hands motion rights straight
  back to the brain.
- **Control expires on silence.** Every connected tab sends a `{"t":"hb"}`
  heartbeat every 5 seconds; the broker checks for staleness every second
  and releases control automatically if 10 seconds pass with no heartbeat
  from the controlling client (a closed laptop lid, a dropped Wi-Fi
  connection, a crashed tab) — the robot is never stuck waiting on a client
  that's gone.
- **Gait commands go stale fast.** Independently of the heartbeat, the
  motion watchdog zeroes the gait velocity if a non-zero command hasn't been
  refreshed in 0.5 seconds. The Move panel already re-sends every 100 ms
  while a direction is held, so this only fires if the browser tab itself
  stalls or the connection drops mid-motion — it's the last line of defense
  against a robot left walking into a wall.
- **STOP is exempt from all of the above.** The status bar's STOP button
  (and the Move panel's own STOP button) sends `{"t":"stop"}`, which is handled
  outside the control gate entirely: it zeroes gait velocity and aborts any
  running pose unconditionally, for any tab, controlling or not. Safety
  never depends on holding the control slot.

## 5. Writing a new panel

Panels are the unit of extension: a new dashboard feature is one static JS
file plus one entry in the registry — nothing else needs to change, and
`bridge/tests/webapp/test_static_integrity.py` fails the build if a
registered panel's file goes missing, so this contract can't silently rot.

Every panel is a plain object with an `id`, a `title`, and a
`mount(el, { bus })` function that renders into `el` and returns an
optional cleanup function. Here's a complete, working example — a panel
that shows the robot's uptime and a button that pings STOP for fun:

```js
// bridge/milo_bridge/webapp/static/js/panels/hello.js
export default {
  id: "hello", title: "Hello Milo",
  // needsControl: true   // uncomment if the WHOLE panel should lock until
                           // this tab holds control — layout.js handles the
                           // dimming/overlay for you (see move.js, poses.js)

  mount(el, { bus }) {
    el.innerHTML = `
      <div class="muted" id="hello-uptime">uptime: —</div>
      <button class="btn" id="hello-ping">Say hi in the log</button>`;

    const off = bus.on("telemetry", (m) => {
      el.querySelector("#hello-uptime").textContent = `uptime: ${m.uptime_s}s`;
    });

    const ping = el.querySelector("#hello-ping");
    const onClick = () => bus.send({ t: "hb" }); // any existing message type works here
    ping.addEventListener("click", onClick);

    // mount() may return a cleanup function. The fixed cockpit mounts every
    // panel once at startup and never unmounts it, so layout.js doesn't
    // call this itself — but returning one is still good practice, and
    // bus.on()'s own unsubscribe function makes it nearly free.
    return () => {
      off();
      ping.removeEventListener("click", onClick);
    };
  },
};
```

Register it in `bridge/milo_bridge/webapp/static/js/registry.js` by adding
it to whichever zone array it belongs in — `cockpitCenter` (the main
column), `cockpitSide` (the narrower side column), `graph` (the full-width
section below the cockpit), or `tools` (the drawer):

```js
import hello from "./panels/hello.js";
// ...
export const registry = {
  cockpitCenter: [camera, move, comm, hello],
  cockpitSide: [sensors],
  graph: [graph],
  tools: [poses, servos, log],
};
```

A panel opts into locking its **entire** body until this tab holds control
by setting `needsControl: true`, the way `move.js` and `poses.js` do —
`layout.js` dims the panel and disables pointer events on it automatically
whenever `bus.controlled` is false. If only *part* of a panel needs that
treatment — like the Communication panel, where listening is free but
push-to-talk and Say require control — leave `needsControl` unset and gate
those specific controls yourself inside `mount()`, the way `comm.js` does:
subscribe to the `"control"` and `"_close"` bus topics, toggle a
`locked-control` class and `disabled` state on just the affected elements,
and tear down any in-flight session (like a push-to-talk audio stream) the
moment control is lost.

That's the whole frontend contract: `bus.on(topic, fn)` subscribes to any
inbound WebSocket message type (`telemetry`, `control`, `log`, or any custom
`t` your server route pushes), `bus.onBinary(fn)` subscribes to binary audio
frames, and `bus.send(obj)` / `bus.sendBytes(u8)` send JSON or binary frames
back. `bus.controlled` and the `"control"` topic tell you whether this tab
currently holds the control slot.

If the panel needs a new HTTP endpoint (not just WebSocket messages), add a
module under `bridge/milo_bridge/webapp/api/` following the existing ones
(`status.py`, `graph.py`, `logs.py`, `speak.py`, `media.py`,
`motion_meta.py`) — each exposes a `register(app: web.Application) -> None`
that adds its routes — and wire it into
`bridge/milo_bridge/webapp/api/__init__.py:register_routes()` with one
import and one call, the same one-line-to-add pattern as the panel registry.

## 6. Audio rates

The Communication panel hardcodes a single `SAMPLE_RATE` constant that must
match the robot's actual capture/playback rate — it's shared by both audio
paths now that the old Ears and Voice cards are one panel:

- `bridge/milo_bridge/webapp/static/js/panels/comm.js` — `SAMPLE_RATE = 16000`,
  used both to build the `AudioContext`/`AudioBuffer`s that play back
  microphone audio streamed down from the robot (must match whatever rate
  `AudioIO.capture_frames()` actually captures at on the Pi) and for the
  push-to-talk `AudioContext` that captures your browser's microphone
  before streaming it up to the robot's speaker (must match whatever rate
  `AudioIO.play_pcm()` expects on playback).

It's currently `16000` to match the INMP441 mic / MAX98357A amp
configuration used on the reference hardware. If your build's audio HAT or
driver uses a different rate, change the constant to match — a mismatch
doesn't error, it just plays back pitched up or down, since the PCM frames
carry no sample-rate header of their own over the wire. Capture and
playback are independent paths in principle and could use different rates,
but sharing one constant keeps the numbers easy to reason about and
matches the current hardware, which uses the same rate for both.

## 7. Development off-Pi

The entire dashboard is testable without a robot. `bridge/tools/webdev.py`
starts the real `aiohttp` app wired to the same fake drivers the test suite
uses (`bridge/tests/webapp/fakes.py`), so you get the actual frontend, the
actual WebSocket protocol, and actual API responses — just backed by fake
hardware instead of real GPIO/I2C/camera devices:

```bash
python bridge/tools/webdev.py
```

Then open `http://localhost:8080`. Everything works except real media: the
Camera panel streams a repeating placeholder frame instead of a live feed,
the Communication panel moves real PCM bytes back and forth for both
listening and push-to-talk (so the plumbing is fully exercised) but
there's no real microphone or speaker on the other end, and text-to-speech
will report `tts-unavailable` unless `espeak-ng` happens to be installed
on your dev machine too. Motion panels (Move, Poses, Servo Test) work
fully — they just print into `FakeGait`/`FakeServos`/`FakeRunner` instead
of moving real hardware, which is exactly what makes this useful for
frontend iteration.

### Manual smoke checklist

Run through this in a browser (both themes, and at both a desktop and a
mobile viewport width) after any change that touches the webapp, before
considering it done. To seed graph nodes for the Memory Graph check below,
run:

```bash
curl -X POST http://localhost:8080/api/graph \
  -H "Content-Type: application/json" \
  -d '{"op":"upsert_node","type":"person","props":{"name":"Ada"}}'
```

- [ ] Page loads at `http://localhost:8080`; toggle the theme button and
      confirm both light and dark look correct.
- [ ] Click **Take Control** — the Move / Communication (push-to-talk +
      Say) / Poses / Servo Test controls unlock. Open a second tab and try
      **Take Control** there too — it must be denied.
- [ ] With the *first* tab controlling, click **STOP** from the *second*,
      non-controlling tab — it must still work; STOP is never gated by
      control.
- [ ] The Camera panel streams frames continuously and the Bridge Log
      panel (in the Tools drawer) shows new lines arriving live.
- [ ] In the Communication panel, toggle **Listen** without holding
      control — it works, and the vertical VU meter reacts. Confirm
      push-to-talk and Say stay visibly locked until Take Control is held.
- [ ] Seed a couple of graph nodes (see the `curl` example above) and
      confirm they appear in the Memory Graph section automatically,
      without needing to search first; confirm searching highlights
      matches rather than hiding non-matches.
- [ ] Click **Tools** in the status bar — the drawer opens with Poses &
      Emotes, Servo Test, and Bridge Log. Confirm it closes both ways:
      clicking the backdrop, and clicking the drawer's own **✕ Close**
      button.
- [ ] At a narrow (≤900px) viewport: the status bar's secondary stats
      collapse behind a **⋯** toggle, the cockpit becomes a single column
      in priority order (camera, move, communication, sensors), and the
      Tools drawer becomes a full-width overlay — confirm the **✕ Close**
      button closes it here too, since the full-screen drawer covers the
      backdrop and the status bar's Tools button at this width.
- [ ] Logged-out and login-error flows (`/login`) are unchanged from
      before this redesign — confirm they still work.
