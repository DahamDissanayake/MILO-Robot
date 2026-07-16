import { createPilotController } from "../pilot.js";
import { mountEmotePopover } from "./poses.js";
import { mountImuPlate } from "./sensors.js";
import { mountCommunication, getAudioSession } from "./comm.js";
import { ICON_RECORD } from "../icons.js";

const MODES = ["raw", "balanced", "angled"];
const MODE_LABEL = { raw: "Raw", balanced: "Balanced", angled: "Angled" };

export default {
  id: "camera", title: "Camera",
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;height:100%">
        <div id="cam-wrap" class="cam-wrap">
          <img id="cam" src="/stream/camera" alt="camera offline" onerror="this.dataset.err=1">
          <div id="cam-overlay-left" class="cam-overlay cam-overlay-left">
            <div class="cam-overlay-row seg-row cam-pilot-control" id="ov-mode-row">
              ${MODES.map((m) => `<button class="btn" data-mode="${m}">${MODE_LABEL[m]}</button>`).join("")}
            </div>
            <label class="muted cam-pilot-control">Speed <input id="ov-speed" type="range" min="10" max="100" value="60"></label>
            <div class="cam-dpad cam-pilot-control">
              <div></div><button class="btn" data-dpad="up" style="font-size:20px">↑</button><div></div>
              <button class="btn" data-dpad="left" style="font-size:20px">←</button><div></div><button class="btn" data-dpad="right" style="font-size:20px">→</button>
              <div></div><button class="btn" data-dpad="down" style="font-size:20px">↓</button><div></div>
            </div>
            <div class="cam-overlay-row cam-pilot-control">
              <button class="btn" data-dpad="lookup">Look Up</button>
              <button class="btn" data-dpad="lookdown">Look Down</button>
            </div>
            <div class="cam-overlay-divider"></div>
            <div class="cam-overlay-row seg-row">
              <button class="btn" data-res="sd">SD</button>
              <button class="btn" data-res="hd">HD</button>
            </div>
            <div class="cam-overlay-row seg-row">
              <button class="btn snap-btn">Snapshot</button>
              <button class="btn rec-btn">Record</button>
            </div>
          </div>
          <div id="cam-overlay-right" class="cam-overlay cam-overlay-right">
            <button class="btn" id="ov-control">Take Control</button>
            <div class="cam-overlay-row">
              <button class="btn danger" id="ov-stop">STOP</button>
              <button class="btn ghost" id="ov-exit">Exit Fullscreen</button>
            </div>
            <div class="cam-overlay-divider"></div>
            <div id="ov-comm-mount"></div>
            <div class="cam-overlay-divider"></div>
            <div id="ov-emote-mount"></div>
            <div class="cam-overlay-divider"></div>
            <div class="sensor-tile imu-tile" id="ov-imu-mount"></div>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <button class="btn snap-btn">Snapshot</button>
          <button class="btn rec-btn">Record</button>
          <button class="btn ghost" id="fullscreen">Fullscreen</button>
          <div class="seg-row" style="margin-left:auto" id="res-row">
            <button class="btn" data-res="sd">SD</button>
            <button class="btn" data-res="hd">HD</button>
          </div>
        </div>
      </div>`;
    const img = el.querySelector("#cam");
    const camWrap = el.querySelector("#cam-wrap");
    const overlayLeft = el.querySelector("#cam-overlay-left");
    const overlayRight = el.querySelector("#cam-overlay-right");
    const commSession = getAudioSession(bus);

    // -- mode buttons + speed slider (mirrors the Move panel's own controls,
    // so piloting from fullscreen has a real adjustable speed instead of a
    // fixed fallback, and mode is switchable without leaving fullscreen) --
    const ovSpeed = overlayLeft.querySelector("#ov-speed");
    function setOvModeButtons(name) {
      overlayLeft.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === name));
    }
    setOvModeButtons("balanced");
    overlayLeft.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = () => bus.send({ t: "mode", name: b.dataset.mode });
    });
    const offMode = bus.on("mode", (m) => setOvModeButtons(m.name));

    const pilot = createPilotController(bus, () => ovSpeed.value);
    pilot.bindGaitButton(overlayLeft.querySelector('[data-dpad="up"]'), "ov-up", 1);
    pilot.bindGaitButton(overlayLeft.querySelector('[data-dpad="down"]'), "ov-down", -1);
    pilot.bindTurnButton(overlayLeft.querySelector('[data-dpad="left"]'), "left");
    pilot.bindTurnButton(overlayLeft.querySelector('[data-dpad="right"]'), "right");
    pilot.bindLookButton(overlayLeft.querySelector('[data-dpad="lookup"]'), "up");
    pilot.bindLookButton(overlayLeft.querySelector('[data-dpad="lookdown"]'), "down");

    const ovControl = overlayRight.querySelector("#ov-control");
    ovControl.onclick = () => bus.send({ t: "control", take: !bus.controlled });
    const offControl = bus.on("control", (m) => {
      ovControl.textContent = m.you ? "Release Control" : "Take Control";
      ovControl.classList.toggle("active", m.you);
      overlayLeft.classList.toggle("locked", !m.you);
    });
    overlayLeft.classList.toggle("locked", !bus.controlled);
    overlayRight.querySelector("#ov-stop").onclick = () => bus.send({ t: "stop" });
    overlayRight.querySelector("#ov-exit").onclick = () => document.exitFullscreen();

    const offComm = mountCommunication(overlayRight.querySelector("#ov-comm-mount"), { bus });
    mountEmotePopover(overlayRight.querySelector("#ov-emote-mount"), { bus });
    const offImu = mountImuPlate(overlayRight.querySelector("#ov-imu-mount"), { bus });

    el.querySelector("#fullscreen").onclick = () => camWrap.requestFullscreen();

    // -- snapshot: shared handler, bound to every copy on the page (normal
    // row + fullscreen left panel) -- pure client-side canvas grab, no state
    // to keep in sync beyond the click handler itself --
    el.querySelectorAll(".snap-btn").forEach((b) => {
      b.onclick = () => {
        const c = document.createElement("canvas");
        c.width = img.naturalWidth || 640; c.height = img.naturalHeight || 480;
        c.getContext("2d").drawImage(img, 0, 0);
        const a = document.createElement("a");
        a.href = c.toDataURL("image/jpeg");
        a.download = `milo-${Date.now()}.jpg`;
        a.click();
      };
    });

    // -- recording: captures both the canvas video AND the robot's incoming
    // audio, by tapping the same Web Audio graph the Communication panel's
    // Listen feature schedules into (see comm.js's getAudioTap) -- combines
    // that audio track with the canvas's video track into one MediaStream.
    // Audio only flows while the shared session is "listening", so a
    // recording started while Listen is off turns it on for the duration
    // and back off afterward -- but never turns off a listen session the
    // user already had running themselves before recording started.
    // One shared recorder/session, reflected on every button copy on the
    // page (normal row + fullscreen left panel), same reasoning as the
    // audio session -- two independent MediaRecorder instances recording
    // the same canvas at once would just waste CPU and confuse "which
    // recording am I stopping". --
    const recBtns = Array.from(el.querySelectorAll(".rec-btn"));
    let recorder = null, recChunks = [], recTimer = null;
    function setRecButtons(recording) {
      recBtns.forEach((b) => {
        b.innerHTML = recording ? `${ICON_RECORD}Stop & Save` : "Record";
        b.classList.toggle("active", recording);
      });
    }
    function startRecording() {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || 640;
      canvas.height = img.naturalHeight || 480;
      const ctx = canvas.getContext("2d");
      recTimer = setInterval(() => ctx.drawImage(img, 0, 0, canvas.width, canvas.height), 66);
      recChunks = [];

      const turnedOnListening = !commSession.listening;
      if (turnedOnListening) commSession.setListening(true);

      const videoTrack = canvas.captureStream(15).getVideoTracks()[0];
      const audioTrack = commSession.getAudioTap().getAudioTracks()[0];
      recorder = new MediaRecorder(new MediaStream([videoTrack, audioTrack]), { mimeType: "video/webm" });
      recorder.ondataavailable = (e) => { if (e.data.size > 0) recChunks.push(e.data); };
      recorder.onstop = () => {
        clearInterval(recTimer);
        if (turnedOnListening) commSession.setListening(false);
        const blob = new Blob(recChunks, { type: "video/webm" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `milo-${Date.now()}.webm`;
        a.click();
        URL.revokeObjectURL(a.href);
        recBtns.forEach((b) => { b.disabled = false; });
      };
      recorder.start();
      setRecButtons(true);
    }
    function stopRecording() {
      recorder?.stop();
      setRecButtons(false);
      recBtns.forEach((b) => { b.disabled = true; });
    }
    recBtns.forEach((b) => {
      b.onclick = () => (recorder && recorder.state === "recording" ? stopRecording() : startRecording());
    });

    // -- resolution: one shared toggle wired across every copy on the page
    // (the normal panel's #res-row and the fullscreen left panel's res row)
    // -- clicking either updates both, since they reflect one piece of
    // server state (see camera.py: resolution is a shared-device setting).
    // Fullscreen does NOT auto-switch resolution -- whatever is selected
    // (even SD) just fills the screen, scaled up; the buttons stay
    // available in both modes so the choice is always the viewer's. --
    function setResButtons(name) {
      el.querySelectorAll("[data-res]").forEach((b) => b.classList.toggle("active", b.dataset.res === name));
    }
    setResButtons("sd");
    el.querySelectorAll("[data-res]").forEach((b) => {
      b.onclick = () => bus.send({ t: "camera_resolution", value: b.dataset.res });
    });
    const offTelemetry = bus.on("telemetry", (m) => {
      if (m.camera_resolution) setResButtons(m.camera_resolution);
      if (m.gait_mode) setOvModeButtons(m.gait_mode);
    });

    return () => {
      offTelemetry();
      offControl();
      offMode();
      offComm();
      offImu();
      pilot.stop();
      if (recorder && recorder.state === "recording") stopRecording();
    };
  },
};
