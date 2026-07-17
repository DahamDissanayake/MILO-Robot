# Milo Brain TUI Design

**Status:** Approved for planning
**Package:** `brain/` only (robot-side `bridge/` static-address discovery fallback is a separate, independent piece of work — see Non-Goals)

## Goal

Replace `brain/`'s PyQt6 system-tray UI with a full Textual TUI: a clean interactive dashboard (identity, connection/pairing status, live LLM token throughput, model selection) that works identically on Linux and Windows, in a terminal, with no hidden tray-icon UX. Branded **MILO** / **by DAMA**.

## Why (context)

Today's session spent hours debugging the tray's architecture: `Advertiser.start()`/`.stop()` call zeroconf's synchronous API from `serve_forever()`, which itself runs on a separate asyncio loop bridged to the Qt thread via `call_soon_threadsafe`/`QTimer` — a genuinely fragile setup that caused a silent deadlock (fixed today with `asyncio.to_thread`, but the underlying two-loops-two-threads architecture remains a standing risk). Textual apps are themselves asyncio-native, so folding the server into the *same* loop as the UI removes that whole class of bug at the architecture level, not just patches a symptom. It also matches the project's existing convention — `iot-testing` and `milo-dashboard` are already Textual TUIs (`textual>=0.60`) — so this brings `brain` in line with the rest of the repo instead of being the odd one out with a GUI toolkit.

## Non-Goals

- The `bridge/`-side static brain-address fallback (for routers that don't forward mDNS multicast reliably) is a separate package and a separate piece of work. Not included here.
- No change to the wire protocol, pairing/auth handshake (`milo_common`), or the cognition pipelines (`pipelines/`, `mcp_client.py`) beyond what's needed to expose live token-rate stats.

## Architecture

`MiloBrainApp` (Textual `App`) owns a `BrainServer` instance and starts `serve_forever()` as a background `asyncio` task in `on_mount()` — both run on Textual's own event loop. This replaces the tray's separate thread + manual loop-bridging entirely:

- **Pairing PIN request:** today, `brain_handshake()` calls `request_pin(robot_name)` (an async callable), and the tray marshals that across threads via `QTimer.singleShot` + `loop.call_soon_threadsafe`. In the TUI, `request_pin` becomes a plain coroutine on the app: it pushes `PairingPinScreen` and awaits the screen's dismissal result directly — no cross-thread handoff needed, because there's only one thread and one loop.
- **zeroconf sync/async boundary:** `Advertiser.start()`/`.stop()` still go through `asyncio.to_thread(...)` exactly as fixed today. That fix is orthogonal to tray-vs-TUI (zeroconf's sync API deadlocks on *any* thread that already has a running loop, Qt or Textual) and stays unchanged.
- **Live dashboard refresh:** a periodic Textual `Timer` (matching `milo-dashboard`'s `FAST_INTERVAL_S`/`SLOW_INTERVAL_S` pattern) re-reads `BrainServer`/`Advertiser` state (connected robot, pairing flag) and the token-rate tracker, pushing updates into the dashboard's reactive attributes — no polling thread needed, it's a Textual-native timer on the same loop.

## Components

```
brain/milo_brain/tui/
  app.py            MiloBrainApp(App) -- owns BrainServer, starts serve_forever()
                     as a background task on mount, holds reactive state
                     (connected_robot, pairing_enabled, tokens_per_sec_in/out,
                     tier, gpu, port), wires request_pin to PairingPinScreen.
  dashboard.py       DashboardScreen -- the main (and default) screen:
                       - Identity panel: name, brain_id, tier, GPU
                       - Connection panel: listening port, advertised IP,
                         connected robot name + last-seen, mDNS status
                       - Model panel: current llm_model/whisper_model/piper_voice,
                         live token throughput (see below), "Change model" action
                       - Pairing panel: on/off toggle (mirrors --pairing / tray's
                         "Enable pairing mode")
  pairing.py         PairingPinScreen(ModalScreen) -- shows robot name + PIN
                     Input; submit dismisses with the PIN string, cancel/escape
                     dismisses with None. Replaces the tray's QInputDialog.
  model_picker.py    ModelPickerScreen(ModalScreen) -- lists installed Ollama
                     models (GET {ollama_url}/api/tags, not a subprocess call to
                     the `ollama` CLI -- consistent with how agent.py already
                     talks to Ollama over HTTP, and doesn't depend on `ollama`
                     being on PATH). Picking one updates cfg.llm_model and
                     calls cfg.save().
  token_rate.py      TokenRateTracker -- rolling tokens/sec calculation fed by
                     agent.py during streaming (see Token Throughput below).
```

Modified:
- `brain/milo_brain/__main__.py` — default path becomes `MiloBrainApp().run()`. `--headless` keeps today's `asyncio.run(server.serve_forever())` path, unchanged in behavior. `--pairing` still pre-arms `server.advertiser.pairing = True` before the app starts.
- `brain/milo_brain/llm/agent.py` — Ollama call switches from `"stream": False` to `"stream": True`, iterating the NDJSON response and feeding `TokenRateTracker` per chunk, instead of one blocking POST.

Removed:
- `brain/milo_brain/ui/tray.py` (deleted entirely)
- `PyQt6`, `PyQt6-Qt6`, `PyQt6_sip` from dependencies

## Token Throughput (tokens/sec in / out)

Ollama's `/api/chat` response (streaming or not) carries `prompt_eval_count`/`prompt_eval_duration` and `eval_count`/`eval_duration` (nanoseconds). Two different update behaviors, stated plainly so this isn't oversold as more "live" than it physically can be:

- **Down (generation, tokens/sec out):** genuinely live — each streamed chunk's arrival is timestamped by `TokenRateTracker`, giving a real rolling rate that updates continuously while the model is producing its reply.
- **Up (prompt eval, tokens/sec in):** Ollama evaluates the prompt synchronously *before* emitting the first token — it doesn't stream progress during that phase. So this number is the measured rate from `prompt_eval_count / (prompt_eval_duration / 1e9)`, updated once per exchange (when that exchange's stats become available), not continuously. Shown as an "up" rate for symmetry with "down," but it won't move between exchanges.

Both values live on `MiloBrainApp`'s reactive state; the dashboard's model panel binds to them directly.

## Keybindings

Matching `iot-testing`/`milo-dashboard`'s existing convention (`q` quit, `r` refresh):

| Key | Action |
|---|---|
| `p` | Toggle pairing mode |
| `m` | Open model picker |
| `q` | Quit |
| `Escape` | Dismiss the active modal (pairing/model picker), no-op on the dashboard |

## Dependencies (`brain/pyproject.toml`)

- `textual>=0.60` moves into **base** `dependencies` (not `[full]`) — the TUI is the primary interactive experience even for a light/pairing-only install, so it shouldn't be gated behind the full AI stack.
- Remove `PyQt6>=6.6` from `[full]` entirely.
- No other dependency changes; streaming from Ollama uses the same `httpx` client already in `dependencies`.

## Branding

- App `TITLE = "MILO"`, `SUB_TITLE = "Brain"` (Textual's built-in header fields).
- A small "by DAMA" credit in the footer/corner, styled the same way `milo-dashboard`'s `TopBar` shows its hostname/uptime line — consistent look across the project's TUIs, not a separate visual language.

## Testing

Textual's `Pilot`/`App.run_test()` harness (same pattern as `iot-testing/tests/test_app_integration.py`) covers, headlessly (no real terminal, no real robot/Ollama):
- Dashboard renders identity/connection/model panels from a fake `BrainServer`/`BrainConfig`.
- `PairingPinScreen` submit/cancel paths resolve the awaited coroutine with the right value.
- `ModelPickerScreen` against a fake `/api/tags` response, selecting a model persists to a fake config.
- `TokenRateTracker` against synthetic chunk-timing sequences (unit test, no Textual needed).
- `--headless` path: unchanged behavior, existing `brain/tests/test_server_integration.py`-style coverage still applies.

## Cross-platform

Textual already renders correctly on Windows Terminal/ConHost and Linux terminals — no platform-specific UI code needed. Everything else (zeroconf `asyncio.to_thread`, `pick_port`, `_local_ip()`) is unchanged from today's fixes and already platform-neutral.
