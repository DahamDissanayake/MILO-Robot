# Webapp-Supreme Control + Camera Recovery + Interactive Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The camera live view auto-recovers from a reader death; a human taking pilot control in the webapp fully suspends every connected brain (stops feeding it audio/video + ignores its speech) and resumes it instantly on release; the Memory Graph is draggable/pannable and settles into a compact graphify-style circular cluster.

**Architecture:** All in the robot's bridge. (1) `Fanout._run` (webapp/media_hub.py) retries its reader (fresh generator) after a natural death while subscribers remain, with a backoff. (2) `RobotSession` gets the `ControlBroker`; its outbound pumps and TTS playback gate on `broker.allow_brain_motion()` (True iff no web owner) — so a web pilot suspends the brain. (3) `graph.js` gains node-drag + canvas-pan and a retuned, compact force layout.

**Tech Stack:** Python 3.14 (asyncio, aiohttp), vanilla-JS canvas, pytest + pytest-asyncio.

## Global Constraints

- Suspend is realized purely by gating the brain's outbound stream + TTS on `ControlBroker.allow_brain_motion()` — no change to the handshake, pairing, active-brain selection, or the existing motion gating. Web owner present → brain suspended; absent → brain runs.
- The fan-out reader must not hot-loop on a hard-broken device: retries are spaced by a backoff, and stop entirely when no subscribers remain.
- No unit test uses real hardware / a real camera / a real websocket — fakes throughout (existing pattern).
- Bridge suite run from `bridge/` after each task (baseline 398). JS has no unit harness — the graph task is verified by driving the panel + not breaking existing behavior (manual, stated honestly).
- Commit messages: no AI co-author trailer.

---

### Task 1: Camera/mic fan-out auto-recovery

**Files:**
- Modify: `bridge/milo_bridge/webapp/media_hub.py`
- Test: the existing media-hub test file (locate with `ls bridge/tests | grep -i media`; if none, create `bridge/tests/test_media_hub.py`)

**Interfaces:**
- Produces: `Fanout(gen_factory, name, on_item=None, queue_size=..., restart_delay=1.0)`. `_run` retries a fresh `gen_factory()` after a natural reader death (uncaught driver exception or generator exhaustion) as long as subscribers remain, sleeping `restart_delay` between attempts; a cancellation (all subscribers gone) still stops it.

- [ ] **Step 1: Read the existing test + write the failing test**

Locate the media-hub test file and match its style. Add:

```python
def test_fanout_restarts_its_reader_after_a_natural_death():
    async def main():
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("camera hiccup")  # first reader dies mid-stream
                yield b""  # unreachable — makes this an async generator
            for i in range(3):
                yield bytes([i])

        fan = Fanout(factory, "test", restart_delay=0)
        q = fan.subscribe()
        try:
            got = await asyncio.wait_for(q.get(), timeout=2.0)  # survives the death, gets a frame
        finally:
            fan.unsubscribe(q)
        return got, calls["n"]

    got, n = asyncio.run(main())
    assert got == bytes([0])
    assert n >= 2  # the reader was restarted with a fresh generator
```

(Import `Fanout` from `milo_bridge.webapp.media_hub` and `asyncio` as the file needs.)

- [ ] **Step 2: Run the test to verify it fails**

Run (from `bridge/`): `../.venv/Scripts/python.exe -m pytest tests/<media-hub-test> -k restarts -v`
Expected: FAIL — today `_run` logs the exception and ends; the subscriber's `q.get()` never completes → `TimeoutError`. Also `Fanout()` doesn't accept `restart_delay` yet (TypeError first).

- [ ] **Step 3: Write the implementation**

In `bridge/milo_bridge/webapp/media_hub.py`, add `restart_delay` to `Fanout.__init__`:

```python
    def __init__(self, gen_factory: Callable[[], AsyncIterator[bytes]], name: str,
                 on_item: Callable[[bytes], None] | None = None, queue_size: int = QUEUE_SIZE,
                 restart_delay: float = 1.0):
        self._factory = gen_factory
        self._name = name
        self._on_item = on_item
        self._queue_size = queue_size
        self._restart_delay = restart_delay
        self._subs: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None
```

Replace `_run` so it retries a fresh generator while subscribers remain:

```python
    async def _run(self) -> None:
        # Keep a live reader for as long as anyone is subscribed. A natural
        # reader death -- an uncaught driver exception (e.g. a momentary
        # camera/I2C hiccup) or the generator ending -- is retried with a
        # fresh generator after a short backoff, so an already-connected
        # subscriber's stream self-heals instead of blanking forever. Only a
        # cancellation (all subscribers gone) stops the loop.
        while self._subs:
            try:
                async for item in self._factory():
                    if self._on_item is not None:
                        self._on_item(item)
                    for q in list(self._subs):
                        if q.full():
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        q.put_nowait(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s fanout reader died; retrying in %.1fs",
                              self._name, self._restart_delay)
            if self._subs:
                await asyncio.sleep(self._restart_delay)
```

`subscribe`/`unsubscribe`/`_on_task_done`/`_start_task` are unchanged (unsubscribe still cancels when `_subs` empties, ending the loop; the cancellation-race restart in `_on_task_done` still applies).

- [ ] **Step 4: Run the test to verify it passes**

Run: `../.venv/Scripts/python.exe -m pytest tests/<media-hub-test> -v`
Expected: all pass (existing fan-out tests included — a healthy generator streams then, when all subscribers leave, the task is cancelled and stops; the retry loop only spins while subscribed).

- [ ] **Step 5: Run the full bridge suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `bridge/`)
Expected: all pass (baseline 398).

```bash
git add bridge/milo_bridge/webapp/media_hub.py bridge/tests/
git commit -m "fix(bridge): auto-recover the camera/mic fanout reader after a natural death"
```

---

### Task 2: Suspend the brain while the webapp holds control

**Files:**
- Modify: `bridge/milo_bridge/net/streams.py`
- Modify: `bridge/milo_bridge/net/session.py`
- Modify: `bridge/milo_bridge/net/server.py`
- Test: the existing streams/session tests (locate with `ls bridge/tests | grep -iE "stream|session"`)

**Interfaces:**
- Produces: `streams.pump_video(sock, fanout, should_stream=None)` and `pump_audio(sock, fanout, should_stream=None)` — a `should_stream: Callable[[], bool] | None`; when it returns False the pump still drains its queue but does not `sock.send` (brain suspended). `RobotSession.__init__` gains `broker=None`; `run()` builds `should_stream = lambda: self._broker is None or self._broker.allow_brain_motion()` and passes it to both pumps; `dispatch` gates `T_TTS` playback on the same. `RobotServer._on_connection` passes `broker=self._broker` into `RobotSession`.

- [ ] **Step 1: Read the existing streams/session tests + write the failing tests**

Locate the tests and match style. Add a pump-gating test (adapt the fake fanout/sock to the file's conventions):

```python
def test_pump_video_does_not_send_while_suspended():
    async def main():
        sent = []

        class _Sock:
            async def send(self, t, payload=None, **f): sent.append((t, payload))

        class _Fanout:
            def __init__(self, frames): self._frames = list(frames)
            def subscribe(self):
                q = asyncio.Queue()
                for f in self._frames:
                    q.put_nowait(f)
                return q
            def unsubscribe(self, q): pass

        from milo_bridge.net import streams
        active = {"on": False}
        task = asyncio.create_task(
            streams.pump_video(_Sock(), _Fanout([b"a", b"b"]), should_stream=lambda: active["on"])
        )
        await asyncio.sleep(0.02)   # drains both frames while suspended -> no sends
        suspended_sends = len(sent)
        active["on"] = True
        # a fresh frame while active DOES send: push via a new fanout is complex;
        # instead just assert nothing was sent while suspended.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return suspended_sends

    assert asyncio.run(main()) == 0
```

Add a session TTS-gating test if the session test file exercises `dispatch` (a `RobotSession` with a fake broker whose `allow_brain_motion()` returns False, dispatching a `T_TTS` message, asserts the fake audio's `play_pcm` was NOT called; True → called). Model it on the existing session/dispatch tests in that file.

- [ ] **Step 2: Run tests to verify they fail**

Run (from `bridge/`): `../.venv/Scripts/python.exe -m pytest tests/<streams-or-session-test> -k "suspend or should_stream" -v`
Expected: FAIL — `pump_video` doesn't accept `should_stream`; `RobotSession` has no `broker`.

- [ ] **Step 3: Write the implementation**

In `bridge/milo_bridge/net/streams.py`, add the gate to both pumps:

```python
async def pump_video(sock: MiloSocket, fanout, should_stream=None) -> None:
    """Send MJPEG frames from the hub until cancelled. While should_stream()
    is False (a web pilot holds control -> the brain is suspended) the queue
    is still drained so it doesn't back up, but nothing is forwarded."""
    q = fanout.subscribe()
    try:
        while True:
            frame = await q.get()
            if should_stream is None or should_stream():
                await sock.send(protocol.T_VIDEO, payload=frame, ts=time.time())
    finally:
        fanout.unsubscribe(q)


async def pump_audio(sock: MiloSocket, fanout, should_stream=None) -> None:
    """Send 20 ms stereo PCM frames from the hub until cancelled; gated by
    should_stream() the same way pump_video is."""
    q = fanout.subscribe()
    try:
        while True:
            chunk = await q.get()
            if should_stream is None or should_stream():
                await sock.send(protocol.T_AUDIO, payload=chunk, ts=time.time())
    finally:
        fanout.unsubscribe(q)
```

In `bridge/milo_bridge/net/session.py`, `RobotSession.__init__` — add `broker=None` (keyword, after `graph_api`) and store it:

```python
    def __init__(
        self,
        sock: MiloSocket,
        *,
        display,
        media_hub=None,
        audio=None,
        graph_api=None,
        broker=None,
    ):
        self._sock = sock
        self._display = display
        self._hub = media_hub
        self._audio = audio
        self._graph_api = graph_api
        self._broker = broker
```

Add a helper + use it in `run()` and `dispatch`. Add a method:

```python
    def _brain_active(self) -> bool:
        """The brain streams + speaks only when it holds motion rights -- i.e.
        no web pilot has taken control (see webapp/control.py's ControlBroker).
        A web pilot taking control suspends the brain: its media stops flowing
        and its speech is dropped until control is released."""
        return self._broker is None or self._broker.allow_brain_motion()
```

In `run()`, pass the gate to the pumps:

```python
        if self._hub is not None and self._hub.video is not None:
            pumps.append(asyncio.create_task(
                streams.pump_video(self._sock, self._hub.video, should_stream=self._brain_active)))
        if self._hub is not None and self._hub.audio is not None:
            pumps.append(asyncio.create_task(
                streams.pump_audio(self._sock, self._hub.audio, should_stream=self._brain_active)))
```

In `dispatch`, gate TTS playback:

```python
    async def dispatch(self, msg: protocol.Message) -> None:
        if msg.t == protocol.T_TTS:
            if self._audio is not None and msg.payload and self._brain_active():
                self._audio.play_pcm(msg.payload)
        elif msg.t == protocol.T_GRAPH:
            await self._handle_graph(msg)
        else:
            log.debug("ignoring message type %r", msg.t)
```

In `bridge/milo_bridge/net/server.py`, `_on_connection` — pass the broker into the session. Change the `RobotSession(...)` construction:

```python
            session = RobotSession(
                sock, display=self._display, media_hub=self._media_hub,
                audio=self._audio, graph_api=self._graph_api, broker=self._broker,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `../.venv/Scripts/python.exe -m pytest tests/<streams-and-session-tests> -v`
Expected: all pass (existing streams/session tests keep passing — `should_stream=None` defaults preserve old behavior, and `RobotSession` built without a `broker` streams normally).

- [ ] **Step 5: Run the full bridge suite and commit**

Run: `../.venv/Scripts/python.exe -m pytest` (from `bridge/`)
Expected: all pass.

```bash
git add bridge/milo_bridge/net/streams.py bridge/milo_bridge/net/session.py bridge/milo_bridge/net/server.py bridge/tests/
git commit -m "feat(bridge): suspend a connected brain's streams + speech while a web pilot holds control"
```

---

### Task 3: Draggable, pannable, compact Memory Graph

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/graph.js`
- (No JS unit tests in this project — verified by driving the panel; do not add a JS test framework.)

**Interfaces:**
- Produces: the graph canvas supports dragging a node (pointerdown on a node moves it, pinned during the drag), panning the view (pointerdown on empty space drags the whole graph via an offset), and a retuned force sim that settles into a compact circular cluster. Existing search-highlight, live-grow, select/detail, and the 5s poll are preserved.

- [ ] **Step 1: Rewrite `graph.js` with interaction + a compact layout**

Replace the body of `mount()` in `bridge/milo_bridge/webapp/static/js/panels/graph.js` so it adds a pan offset, pointer drag handling, and stronger clustering forces. Keep the existing `merge`/`loadAll`/`search`/poll structure; change the simulation constants, the `draw()` transform, the hit-test, and the pointer handlers. Concretely:

- Add view state: `let offsetX = 0, offsetY = 0, dragNode = null, panning = false, lastPX = 0, lastPY = 0, downX = 0, downY = 0, moved = false;`
- **Compact force sim** in `tick()`: strengthen centering and shorten repulsion so nodes ball up. Replace the per-node forces with:
  - centering pull toward canvas center: `a.vx += (W/2 - a.x) * 0.02; a.vy += (H/2 - a.y) * 0.02;` (was 0.001 — ~20x stronger, pulls into a circle)
  - repulsion capped at a short range so nodes stay close but don't overlap: keep the inverse-square push but lower the constant, e.g. `const d2 = Math.max(64, dx*dx + dy*dy); a.vx += (dx/d2) * 120; a.vy += (dy/d2) * 120;` (was 600)
  - edge springs slightly stronger: `a.vx += dx * 0.01; a.vy += dy * 0.01;` (was 0.003) so connected nodes pull tight.
  - a `dragNode`, while held, has its velocity zeroed and position pinned to the cursor (skip force integration for it).
- **draw()** applies the pan offset: translate by `(offsetX, offsetY)` — either `g.save(); g.translate(offsetX, offsetY); ...; g.restore();` around the node/edge drawing, or add the offset to every coordinate. Use `g.save()/translate/restore`.
- **hit-test** (shared by click + drag) subtracts the offset: `const x = ev.clientX - r.left - offsetX, y = ev.clientY - r.top - offsetY;`
- **pointer handlers** (replace the `cv.onclick`):
  - `cv.onpointerdown`: hit-test; if a node is hit → `dragNode = node; cv.setPointerCapture(ev.pointerId);` else → `panning = true`. Record `downX/downY`, `lastPX/lastPY`, `moved=false`.
  - `cv.onpointermove`: if `dragNode` → set its `x/y` to cursor-minus-offset, `vx=vy=0`, `moved=true`, and `if (!raf) tick()`; else if `panning` → `offsetX += ev.clientX - lastPX; offsetY += ev.clientY - lastPY; lastPX=ev.clientX; lastPY=ev.clientY; moved=true; draw();`.
  - `cv.onpointerup`: if `!moved` (a click, not a drag) → run the existing select/detail logic (set `selected` from the hit-test, update `#graph-detail`); then clear `dragNode=null; panning=false;` and `draw()`.
- Keep `merge`, `loadAll`, `search`, `#gsearch`/`#gclear`/`#gq` handlers, the 5s poll, and the cleanup return exactly as they are (the cleanup should also not need new listeners removed beyond what `mount` added, since pointer handlers are set as `cv.onpointer*` properties, not `addEventListener`).

Provide the full rewritten file. Preserve the top-of-file comment (update it to mention drag/pan). Everything not listed above stays byte-for-byte.

- [ ] **Step 2: Verify it loads without error**

There are no JS unit tests. Do a syntax/sanity check: `../.venv/Scripts/python.exe -c "import pathlib,ast; print('js file present:', pathlib.Path('milo_bridge/webapp/static/js/panels/graph.js').stat().st_size, 'bytes')"` (from `bridge/`) — confirm the file was written and is non-trivial. The real check is the manual step below.

- [ ] **Step 3: Run the full bridge suite (no regressions elsewhere)**

Run: `../.venv/Scripts/python.exe -m pytest` (from `bridge/`)
Expected: all pass — this task touches only a JS asset, so the Python suite is unaffected; run it to confirm nothing else broke and the static-asset integrity check (if any) still passes.

- [ ] **Step 4: Manual verification (after deploy, or against a running bridge)**

Open the webapp's Memory Graph panel and confirm: nodes cluster into a compact circle (not spread out); dragging a node moves it and it stays put while held, then eases back into the sim on release; dragging empty space pans the whole graph; a plain click still selects a node and shows its detail; search still highlights. If you can't drive a browser in this environment, say so honestly — note that the change is JS-only, the Python suite is green, and the behavior is verified once deployed.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/graph.js
git commit -m "feat(bridge): make the Memory Graph draggable, pannable, and compactly clustered"
```
