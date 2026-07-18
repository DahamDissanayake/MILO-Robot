// Shared piloting control logic: continuous-gait hold (forward/backward),
// scripted turn hold, and manual-mode look-pose hold. Used by both the
// Move panel and the camera fullscreen overlay so on-screen buttons in
// either place drive the exact same hold-state machinery and bus messages
// instead of two independently maintained copies of it.
const SEND_MS = 100;

// -- Auto Standby: one flag shared by every pilot instance on the page
// (module-level, not per-controller) so the Move panel's toggle and the
// fullscreen overlay's toggle are always showing/controlling the same
// setting -- fullscreen is an overlay on this same page, not a separate
// tab, so this needs no server round-trip to stay in sync. Flipping the
// toggle itself only ever updates this flag and notifies listeners for
// the button's visual state -- it never sends a bus message, so switching
// it on/off never moves the robot by itself; it only changes what happens
// the *next* time a piloted movement is released. --
let autoStandby = false;
const autoStandbyListeners = new Set();

export function getAutoStandby() {
  return autoStandby;
}
export function setAutoStandby(on) {
  autoStandby = on;
  autoStandbyListeners.forEach((fn) => fn(on));
}
export function onAutoStandbyChange(fn) {
  autoStandbyListeners.add(fn);
  return () => autoStandbyListeners.delete(fn);
}

export function createPilotController(bus, getSpeed) {
  // -- continuous gait: forward/backward only. `gaitState` maps an
  // arbitrary caller-chosen token (a raw keyboard key, or a button id) to
  // its direction sign, so multiple tokens mapped to the same direction
  // (e.g. the "w" key and a d-pad button both meaning forward) can be held
  // together and only fully release once every token holding that
  // direction has released -- this matches keyboard semantics where
  // holding both W and ArrowUp and releasing only one must keep moving. --
  let vec = { vx: 0 }, timer = null;
  const gaitState = new Map(); // token -> sign (1 forward, -1 backward)

  function scaled() {
    return { vx: vec.vx * (getSpeed() / 100), vy: 0, yaw: 0 };
  }
  function sending(active) {
    if (active && !timer) timer = setInterval(() => bus.send({ t: "gait", ...scaled() }), SEND_MS);
    if (!active && timer) {
      clearInterval(timer); timer = null;
      bus.send({ t: "gait", vx: 0, vy: 0, yaw: 0 });
      // Balanced/angled mode already auto-standbys on its own once the
      // command zeroes; raw mode does not (see GaitEngine.set_velocity_command).
      // Auto Standby makes the return-to-standby explicit and unconditional
      // regardless of mode, once the toggle is on.
      if (autoStandby) bus.send({ t: "standby" });
    }
  }
  function gaitSync() {
    let vx = 0;
    gaitState.forEach((sign) => { vx += sign; });
    vec = { vx: Math.sign(vx) };
    sending(gaitState.size > 0);
  }
  function gaitPress(token, sign) { gaitState.set(token, sign); gaitSync(); }
  function gaitRelease(token) { gaitState.delete(token); gaitSync(); }

  // -- turn: scripted gait, held via a large cycle count on the server and
  // stopped with the universal {t:"stop"} message. --
  function turnPress(dir) { bus.send({ t: "turn", dir }); }
  function turnRelease() {
    bus.send({ t: "stop" });
    // turn_left/turn_right already recover to stand on abort (see
    // PoseRunner.run: any pose with a cycle does), but send it explicitly
    // too when Auto Standby is on so the guarantee doesn't depend on that.
    if (autoStandby) bus.send({ t: "standby" });
  }

  // -- look up/down: held, not toggled. manual:true (sent first, so its
  // own abort() doesn't cut the pose off mid-flight) freezes the gait
  // engine's writes for as long as the button/key stays down; release
  // returns to standby and un-freezes. --
  function lookPress(dir) {
    bus.send({ t: "manual", on: true });
    bus.send({ t: "pose", name: `look_${dir}` });
  }
  function lookRelease() {
    bus.send({ t: "standby" });
    bus.send({ t: "manual", on: false });
  }

  function bindPointerHold(el, press, release) {
    el.addEventListener("pointerdown", press);
    el.addEventListener("pointerup", release);
    el.addEventListener("pointerleave", release);
    el.addEventListener("pointercancel", release);
    return () => {
      el.removeEventListener("pointerdown", press);
      el.removeEventListener("pointerup", release);
      el.removeEventListener("pointerleave", release);
      el.removeEventListener("pointercancel", release);
    };
  }

  function bindGaitButton(el, token, sign) {
    return bindPointerHold(el, (e) => { e.preventDefault(); gaitPress(token, sign); }, () => gaitRelease(token));
  }
  function bindTurnButton(el, dir) {
    return bindPointerHold(el, (e) => { e.preventDefault(); turnPress(dir); }, turnRelease);
  }
  function bindLookButton(el, dir) {
    return bindPointerHold(el, (e) => { e.preventDefault(); lookPress(dir); }, lookRelease);
  }

  function stop() {
    sending(false);
    gaitState.clear();
  }

  return {
    bindGaitButton, bindTurnButton, bindLookButton,
    gaitPress, gaitRelease, turnPress, turnRelease, lookPress, lookRelease,
    stop,
  };
}
