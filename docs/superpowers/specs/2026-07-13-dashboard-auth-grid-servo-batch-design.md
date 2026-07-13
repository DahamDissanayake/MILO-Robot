# Dashboard Auth, Masonry Grid, and Servo Batch — Design

**Date:** 2026-07-13
**Status:** Approved

## Purpose

Three fixes to the Milo web dashboard (`bridge/milo_bridge/webapp/`), requested
together because they're all "the dashboard doesn't feel finished yet" issues
surfaced by actually using it:

1. **Authentication** — the dashboard was deliberately open on the LAN; that
   decision is superseded. It now requires a login.
2. **Grid packing** — cards resize but neighbors don't reflow, leaving gaps
   and overlaps. Replace with a real auto-packing (masonry) layout.
3. **Servo "Center All" timing** — the button visibly moves servos one after
   another instead of together. Root-caused to per-channel WebSocket
   round-trips, not a driver bug; fixed with a real batch command.

## Scope

In scope: login page + session-cookie auth gating the entire dashboard;
config-driven credentials with hashed password storage; a JS masonry
compaction algorithm replacing the static CSS grid; a `servo_batch` WS
command and driver-level batched write for "Center All" (and reusable by
any future multi-servo UI). Out of scope: TLS/HTTPS (LAN-only, documented
caveat), multi-user accounts (single shared login), changing any other
card's behavior.

## 1. Authentication

### Credential storage

`BridgeConfig` (`bridge/milo_bridge/config.py`) gains two fields:

```python
web_username: str = "dama"
web_password_hash: str = ""   # "<salt_hex>$<hash_hex>", scrypt, empty = unset
```

A new `bridge/milo_bridge/webapp/auth.py` provides:

- `hash_password(password: str) -> str` — `hashlib.scrypt(password.encode(), salt=os.urandom(16), n=2**14, r=8, p=1)`, returned as `"<salt_hex>$<hash_hex>"`. Stdlib only, no new dependency.
- `verify_password(password: str, stored: str) -> bool` — re-derives with the stored salt and compares with `hmac.compare_digest`.

On `BridgeConfig.load()`, if `web_password_hash` is empty, seed it once:
`web_username="dama"`, `web_password_hash=hash_password("MILO@gate")`, then
save — matching the existing "seed `robot_id` on first run" pattern already
in `load()`. Changing the password later means editing `~/.milo/config.json`
by hand and running a one-line helper (documented in the module docstring
and `docs/WEB-DASHBOARD.md`):

```bash
python -c "from milo_bridge.webapp.auth import hash_password; print(hash_password('new pw'))"
```

then pasting the result into `web_password_hash` and restarting the service.

### Session mechanism

New `bridge/milo_bridge/webapp/session_auth.py`:

- `SessionStore` — in-memory (not persisted; a bridge restart requires
  re-login, which is fine and simpler). `create(username) -> token` issues
  `secrets.token_urlsafe(32)`; `is_valid(token) -> bool`; `revoke(token)`.
- `LoginThrottle` — per-source-IP failed-attempt tracker: after 5 failures
  within 60s, refuses further attempts from that IP for 30s (in-memory
  dict of `ip -> (fail_count, first_fail_ts, locked_until)`), independent
  of `SessionStore`.

`POST /api/login` (`webapp/api/auth.py`, new): body
`{"username": str, "password": str}`. Checks `LoginThrottle` first (429 if
locked), then `username == cfg.web_username and verify_password(...)`. On
success: `SessionStore.create()`, sets cookie
`Set-Cookie: milo_session=<token>; HttpOnly; SameSite=Strict; Path=/`
(no `Max-Age`/`Expires` — a true session cookie, cleared when the browser
closes, and **no `Secure` flag** since the dashboard is plain HTTP; this is
a documented, accepted LAN-only trade-off). On failure: record the attempt
in `LoginThrottle`, return `{"error": "invalid credentials"}` with a
generic message (not "wrong password" vs "wrong username", to avoid
username enumeration).

`POST /api/logout`: revokes the session token (if present) and clears the
cookie (`Set-Cookie: milo_session=; Max-Age=0`).

### Gating

New `@web.middleware _auth_middleware` in `webapp/__init__.py`, installed
**before** the existing `_json_error_middleware`. Allow-list (no auth
required): `/login`, `/api/login`, and any `/static/*` path (the login
page needs its own JS/CSS). Everything else — `/`, `/ws`, `/stream/camera`,
every other `/api/*` route — requires `request.cookies.get("milo_session")`
to be a token `SessionStore.is_valid()` accepts.

- Unauthenticated request to `/` or any non-`/api/`, non-`/ws`, non-`/stream`
  path → `303 See Other` redirect to `/login`.
- Unauthenticated request to any `/api/*` path (other than the allow-list)
  or `/stream/camera` → `401 {"error": "unauthorized"}` JSON (handled by
  the existing `_json_error_middleware` pattern, extended to also catch a
  raised `web.HTTPUnauthorized` from the auth middleware — no change needed
  there since `_json_error_middleware` already converts any
  `web.HTTPException` on `/api/*` paths to JSON).
- Unauthenticated `/ws` connection attempt: the auth middleware runs before
  the WS upgrade, so it can reject with `401` before `ws.prepare()` is ever
  called — the browser's `WebSocket` constructor will fail to connect,
  which `bus.js`'s existing reconnect loop will retry harmlessly forever
  at low cost (acceptable: a logged-out tab left open just keeps failing
  quietly until the user logs in again in another tab and reloads).

### Frontend

`static/login.html` — a small standalone page (not a card, not part of the
dashboard shell): centered form, username + password fields, error text
area, using the same `theme.css` custom properties for light/dark
consistency. `static/js/login.js`: `POST /api/login` with the form values;
on success `location.href = "/"`; on failure show the returned error
inline and clear the password field.

`index.html`/`main.js` gain a **Logout** button in the header (next to
Take Control / STOP): `onclick` → `POST /api/logout` → `location.href =
"/login"`.

## 2. Masonry grid packing

Replace `grid.js`'s CSS-grid-`span` placement with a JS-driven compaction
layout, keeping the same public shape (`initGrid(container, cards, bus)`,
same `localStorage["milo.layout.v1"]` schema: `{order, sizes, hidden}` —
no migration needed, the packer derives positions from the same fields).

### Layout model

`#grid` becomes `position: relative` (no more `display: grid`); each
`.card` becomes `position: absolute` with inline `left/top/width/height`
in pixels, computed from a logical `{x, y, w, h}` in grid units (12
logical columns, 80px row unit — unchanged from today) via a pure
`compact(order, sizes) -> Map<id, {x,y,w,h}>` function:

- Process cards in `order` sequence (drag-reorder still controls priority).
- For each card, scan candidate `(x, y)` positions — `x` from 0 to
  `12 - w`, `y` from 0 upward — in row-major order (top row first, left to
  right within a row), and place the card at the first position where its
  `w × h` footprint doesn't overlap any already-placed card's footprint.
  This is the same "gravity" bin-packing pass gridstack.js-style libraries
  use: no gaps are left above/left of where a card could legally sit.
- Container height = `(max over all cards of y + h) × rowHeight`, applied
  to `#grid`'s inline `height` so the page scrolls correctly.

`compact()` re-runs after every mutation: on load, after a drag-reorder
drop, after `hide`/`show`, after `Reset layout`, and **live** on every
`pointermove` tick while resizing (not just on release) — so dragging a
corner handle bigger visibly shoves later cards into their next free slot
in real time, not just when you let go.

### Responsiveness

Column width = `container.clientWidth / columns`, recomputed on a
`window.resize` listener (re-running `compact()` is unnecessary on resize
since `x/y/w/h` units don't change — only the pixel conversion does).
Below a `700px` container width, `columns` drops from 12 to 2 and each
card's effective `w` is clamped to `min(w, 2)` for that pass only (not
persisted), reproducing today's single/double-column mobile fallback
without a separate CSS media-query code path.

### Motion

`.card` gets `transition: left 0.15s ease, top 0.15s ease, width 0.15s
ease, height 0.15s ease` in `grid.css` so a reflow animates smoothly
instead of snapping — this is what makes "others adjust" read as clean
rather than jarring. The transition is suspended (`transition: none`)
while the card being dragged/resized is the one moving, so direct
manipulation still feels immediate.

### Drag-to-reorder

Unchanged interaction (drag by header, drop on a target card inserts the
dragged id at that position in `order`) — only the post-drop re-layout
changes, from "just re-render the CSS grid" to "re-run `compact()`."

## 3. Servo batch command

`bridge/milo_bridge/webapp/motion.py` gains:

```python
async def servo_batch(self, client_id: str, angles: dict[str, float]) -> dict:
    if err := self._denied(client_id):
        return err
    bad = [name for name in angles if name not in SERVO_CHANNELS]
    if bad:
        return {"error": f"unknown servo(s) {bad!r}"}
    clamped = {name: _clamp(deg, DEG_MIN, DEG_MAX) for name, deg in angles.items()}
    try:
        await self._deps.servos.set_pose(clamped, stagger=True)
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True}
```

Rejects the whole batch (no partial writes) if any channel name is
unrecognized. Reuses `ServoDriver.set_pose()` — already staggers writes by
`stagger_ms` (config default 20ms, the value tuned against real brownout
behavior) — so this is the same safe multi-servo path poses already use,
just exposed directly.

`webapp/ws.py`'s dispatch table gains `"servo_batch"` →
`motion.servo_batch(client_id, data.get("angles", {}))`.

`static/js/cards/servos.js`'s **Center All (90°)** handler changes from 8
individual `bus.send({t:"servo",...})` calls to one:

```js
el.querySelector("#center").onclick = () => {
  const angles = {};
  SERVOS.forEach((s) => {
    const sl = el.querySelector(`[data-servo="${s}"]`);
    sl.value = 90;
    el.querySelector(`[data-val="${s}"]`).textContent = "90°";
    angles[s] = 90;
  });
  bus.send({ t: "servo_batch", angles });
};
```

Per-slider dragging is unchanged (still single-channel `{t:"servo"}` —
correct, since a single slider drag only ever moves one channel).

## Error handling

- Wrong password / unknown username: generic `{"error": "invalid
  credentials"}`, throttled per-IP.
- Session cookie present but expired/revoked: same as no cookie — redirect
  or 401, no special-case error message (avoids distinguishing "expired"
  from "never logged in" to a potential attacker, though the practical
  benefit on a LAN is minor).
- `servo_batch` with an empty `angles` dict: succeeds trivially (`set_pose`
  with an empty mapping is a no-op) — no special-case needed.
- Grid `compact()` never throws: any card with `w`/`h` outside
  `[2,12]`/`[2,10]` is clamped at read-time, same as today's resize clamp.

## Testing

- `webapp/auth.py`: `hash_password`/`verify_password` round-trip, wrong
  password rejected, two hashes of the same password differ (random salt).
- `session_auth.py`: `SessionStore` create/validate/revoke;
  `LoginThrottle` allows under the limit, blocks at 5 failures, unblocks
  after the cooldown window (inject a fake clock, don't sleep in tests).
- `api/auth.py` (login/logout endpoints): success sets a cookie and
  returns ok; wrong password fails with the generic message; lockout
  after 5 failures returns 429; logout clears the session so a subsequent
  authenticated request fails.
- Auth middleware: unauthenticated `/` redirects to `/login`;
  unauthenticated `/api/status` returns 401 JSON; unauthenticated `/ws`
  handshake is rejected; `/login`, `/api/login`, `/static/*` work without
  a cookie; an authenticated request with a valid cookie passes through
  to the real handler.
- `MotionService.servo_batch`: control-gated like other motion ops;
  rejects an unknown channel with no partial write (assert the fake
  driver's `set_pose` was never called when validation fails); clamps
  every angle; calls the fake driver's `set_pose` exactly once with all
  provided channels.
- Grid `compact()` (pure function, testable outside the browser via a
  small Node-free unit test is not practical for browser JS in this
  repo's Python-test-only convention — instead this logic gets thorough
  manual verification per the existing project convention of no JS test
  harness, documented in the manual smoke checklist addition below).

## Manual smoke checklist additions (docs/WEB-DASHBOARD.md)

- Open `http://milo.local` logged out → redirected to `/login`; wrong
  password shows an error and doesn't log in; correct password (`dama` /
  `MILO@gate`) logs in and lands on the dashboard.
- Close and reopen the browser (not just the tab) → logged out again
  (session cookie, no persistence).
- **Logout** button in the header returns to `/login` and a subsequent
  page load stays on `/login`.
- Resize a card bigger via its corner handle: watch cards below/right
  smoothly reflow into the next free slot *while dragging*, not just on
  release; no overlaps, no stray gaps after release.
- Center All (90°) on the Servo Test card: all 8 sliders visibly move
  together, not in a visible sequence.
