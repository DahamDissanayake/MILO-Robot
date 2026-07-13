# The Milo Web Dashboard

A browser control panel served directly off the robot — no brain, no phone
app, no extra install. Point any device on the same LAN at Milo and you get
a live cockpit: camera, ears, voice, movement, poses, servo trims, sensors,
memory graph, and the bridge's own log, all in one page.

## 1. What it is

The dashboard is a single-page app served by `milo-bridge` itself (the same
process that runs the gait engine and drivers) over a WebSocket-plus-REST API
on `bridge/milo_bridge/webapp/`. It needs no build step — it's hand-written
ES modules loaded straight from `static/`, and no accounts or brain pairing
are required to look at it: **observation is always free**. Taking the
robot's actuators away from the brain (driving it, posing it, talking through
it) requires explicitly clicking **Take Control**, and one physical **STOP**
button is always live, for anyone, in any tab, whether or not they hold
control. The page is a responsive drag-and-resize card grid — arrange it
once, on one device, and it stays arranged; open the same URL from a second
phone and get a different, independent layout. It works in light or dark
mode and follows your OS theme by default.

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

## 3. Feature tour

The dashboard ships ten cards. Every card that can move hardware or make
noise is marked **needs control** below; the rest are pure observation and
work in every tab, all the time.

- **Status** — a plain table of the telemetry the bridge already tracks for
  itself: brain link state, who currently owns control (`none` / `brain` /
  `web`), which gait backend is active (`cpg` or the trained ONNX policy),
  CPU %, SoC temperature, RAM %, and how long the web server has been up.
  It refreshes every two seconds off the same telemetry broadcast every
  connected tab receives, so it costs nothing extra to have open.
- **Camera** — a live MJPEG feed at `/stream/camera`, one hub subscription
  per browser tab, so opening the dashboard on three devices doesn't triple
  the load on the camera driver — they all share the single upstream reader.
  A **Snapshot** button grabs the current frame into a downloadable JPEG
  client-side, no server round trip needed.
- **Move** (needs control) — an on-screen joystick, or WASD / arrow keys
  with Q/E to turn, driving the gait engine's velocity command. A speed
  slider scales every axis together. Commands are sent at a steady 100 ms
  cadence while a direction is held and immediately zeroed on release, and
  there is always a local **STOP** button on the card in addition to the
  global one in the header.
- **Ears (Listen)** (observe-only, no control needed) — subscribes to the
  robot's live microphone audio over the WebSocket's binary channel and
  plays it back through your browser's speakers, with a small VU meter per
  channel. Anyone can listen in without taking control — it's a monitor,
  not an actuator.
- **Voice (Speak)** (needs control) — two ways to make Milo talk: hold the
  **Hold to Talk** button for a push-to-talk intercom (your microphone is
  captured, resampled, and streamed to the robot's speaker while the button
  is held), or type text and hit **Say** to have the robot speak it via
  text-to-speech. TTS goes through `espeak-ng` on the Pi — it must be
  installed with `sudo apt install espeak-ng`, or `/api/speak` reports
  `tts-unavailable` and the card shows the error inline.
- **Poses & Emotes** (needs control) — buttons for every scripted pose in
  `milo_bridge.poses` and every face bitmap under `assets/faces/`, fetched
  from `/api/poses` and `/api/faces` so the card never needs updating when
  poses or faces are added — it just reflects what's on the robot.
- **Servo Test** (needs control) — one slider per servo channel (R1–R4,
  L1–L4), each sending a live `deg` update as you drag, plus a **Center All
  (90°)** button for quickly returning every joint to neutral during
  assembly or calibration work.
- **Sensors** (observe-only) — a small IMU chart (pitch/roll history as a
  scrolling sparkline) driven by the same telemetry stream as the Status
  card, plus a row of hardware-presence dots (camera / audio / IMU /
  display) fetched once from `/api/status` so you can tell at a glance what
  the robot thinks is actually attached.
- **Memory Graph** (observe-only) — a force-directed canvas view of Milo's
  on-robot knowledge graph. Type a search term and hit **Search** (or
  Enter) to query `/api/graph/search`; matching nodes and their edges are
  laid out with a simple spring simulation, and clicking a node shows its
  full type and properties below the canvas.
- **Bridge Log** (observe-only) — a live tail of the bridge's own log
  output. It loads the last 100 lines from `/api/logs` on mount, then
  appends new lines in real time as the bridge's `RingBufferLogHandler`
  broadcasts them over the WebSocket — useful for watching what the robot
  is actually doing without SSHing in.

Cards can be dragged by their header to reorder, resized from their
bottom-right corner, hidden with the header's ✕ and brought back with
**+ Card**, and the whole layout can be wiped back to defaults with the ⟲
button in the header. Layout, per-card size, and hidden state all persist
per-browser in `localStorage`, independently of every other tab or device.

## 4. Control & safety

Motion, poses, servos, and voice output all funnel through one gate:
`milo_bridge.webapp.control.ControlBroker`. The rules are deliberately
simple:

- **Observation is never brokered.** Camera, ears, sensors, status, logs,
  and the memory graph work in every tab regardless of who — if anyone —
  holds control.
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
  refreshed in 0.5 seconds. The Move card already re-sends every 100 ms
  while a direction is held, so this only fires if the browser tab itself
  stalls or the connection drops mid-motion — it's the last line of defense
  against a robot left walking into a wall.
- **STOP is exempt from all of the above.** The header's STOP button (and
  the Move card's own STOP button) sends `{"t":"stop"}`, which is handled
  outside the control gate entirely: it zeroes gait velocity and aborts any
  running pose unconditionally, for any tab, controlling or not. Safety
  never depends on holding the control slot.

## 5. Writing a new card

Cards are the unit of extension: a new dashboard feature is one static JS
file plus one line in the registry — nothing else needs to change, and
`bridge/tests/webapp/test_static_integrity.py` fails the build if a
registered card's file goes missing, so this contract can't silently rot.

Every card is a plain object with an `id`, a `title`, a grid size (`w`/`h`
in 12-column grid units), and a `mount(el, { bus })` function that renders
into `el` and returns an optional cleanup function. Here's a complete,
working example — a card that shows the robot's uptime and a button that
pings STOP for fun:

```js
// bridge/milo_bridge/webapp/static/js/cards/hello.js
export default {
  id: "hello", title: "Hello Milo", w: 3, h: 2,
  // needsControl: true   // uncomment if this card should lock while nobody has control

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

    // mount() may return a cleanup function; grid.js calls it if the card
    // is ever unmounted (hidden via the header ✕, or on a full re-render).
    return () => {
      off();
      ping.removeEventListener("click", onClick);
    };
  },
};
```

Register it with one line in `bridge/milo_bridge/webapp/static/js/registry.js`:

```js
import hello from "./cards/hello.js";
// ...
export const cards = [status, camera, move, ears, voice, poses, servos, sensors, graph, log, hello];
```

That's the whole frontend contract: `bus.on(topic, fn)` subscribes to any
inbound WebSocket message type (`telemetry`, `control`, `log`, or any custom
`t` your server route pushes), `bus.onBinary(fn)` subscribes to binary audio
frames, and `bus.send(obj)` / `bus.sendBytes(u8)` send JSON or binary frames
back. `bus.controlled` and the `"control"` topic tell you whether this tab
currently holds the control slot, which is how `grid.js` decides whether to
visually lock a card marked `needsControl: true`.

If the card needs a new HTTP endpoint (not just WebSocket messages), add a
module under `bridge/milo_bridge/webapp/api/` following the existing ones
(`status.py`, `graph.py`, `logs.py`, `speak.py`, `media.py`,
`motion_meta.py`) — each exposes a `register(app: web.Application) -> None`
that adds its routes — and wire it into
`bridge/milo_bridge/webapp/api/__init__.py:register_routes()` with one
import and one call, the same one-line-to-add pattern as the card registry.

## 6. Audio rates

The dashboard's two audio cards each hardcode a `SAMPLE_RATE` constant that
must match the robot's actual capture/playback rate:

- `bridge/milo_bridge/webapp/static/js/cards/ears.js` — `SAMPLE_RATE = 16000`,
  used to build the `AudioContext` and each `AudioBuffer` that plays back
  microphone audio streamed down from the robot. It must match whatever
  rate `AudioIO.capture_frames()` actually captures at on the Pi.
- `bridge/milo_bridge/webapp/static/js/cards/voice.js` — `SAMPLE_RATE = 16000`,
  used for the intercom `AudioContext` that captures your browser's
  microphone before streaming it up to the robot's speaker. It must match
  whatever rate `AudioIO.play_pcm()` expects on playback.

Both are currently `16000` to match the INMP441 mic / MAX98357A amp
configuration used on the reference hardware. If your build's audio HAT or
driver uses a different rate, change both constants to match — a mismatch
doesn't error, it just plays back pitched up or down, since the PCM frames
carry no sample-rate header of their own over the wire. There's no reason
the two constants need to be equal to each other in principle (capture and
playback are independent paths), but keeping them equal makes the numbers
easy to reason about and matches the current hardware, which uses the same
rate for both.

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
camera card streams a repeating placeholder frame instead of a live feed,
the ears/voice cards move real PCM bytes back and forth (so the plumbing is
fully exercised) but there's no real microphone or speaker on the other
end, and text-to-speech will report `tts-unavailable` unless `espeak-ng`
happens to be installed on your dev machine too. Motion cards (Move, Poses,
Servo Test) work fully — they just print into `FakeGait`/`FakeServos`/
`FakeRunner` instead of moving real hardware, which is exactly what makes
this useful for frontend iteration.

### Manual smoke checklist

Run through this in a browser (both themes) after any change that touches
the webapp, before considering it done:

- [ ] Page loads at `http://localhost:8080`; toggle the theme button and
      confirm both light and dark look correct; reload and confirm the
      layout (card order, sizes, any hidden cards) survives the reload.
- [ ] Click **Take Control** — the Move / Voice / Poses / Servo Test cards
      unlock (their `needsControl` lock overlay disappears). Open a second
      tab and try **Take Control** there too — it must be denied (the
      button stays "Take Control", not "Release Control", and an
      `{"t":"err","for":"control","error":"held-by-other"}` comes back).
- [ ] With the *first* tab controlling, click **STOP** from the *second*,
      non-controlling tab — it must still work (gait zeroed, any running
      pose aborted); STOP is never gated by control.
- [ ] The Camera card streams frames continuously (placeholder frames
      off-Pi, but the `<img>` must keep updating, not show the
      "camera offline" broken-image state) and the Bridge Log card shows
      new lines arriving live as the server logs activity.
- [ ] Seed a couple of graph nodes so the Memory Graph card has something
      to find — e.g. from a Python shell or `curl`:
      ```bash
      curl -X POST http://localhost:8080/api/graph \
        -H "Content-Type: application/json" \
        -d '{"op":"upsert_node","type":"person","props":{"name":"Ada"}}'
      ```
      then search for `ada` in the Memory Graph card and confirm the seeded
      node appears with the right label.
