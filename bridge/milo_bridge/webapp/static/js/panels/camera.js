import { createPilotController } from "../pilot.js";
import { mountEmotePopover } from "./poses.js";
import { ICON_RECORD } from "../icons.js";

export default {
  id: "camera", title: "Camera",
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;height:100%">
        <div id="cam-wrap" class="cam-wrap">
          <img id="cam" src="/stream/camera" alt="camera offline" onerror="this.dataset.err=1">
          <div id="cam-overlay" class="cam-overlay">
            <button class="btn" id="ov-control">Take Control</button>
            <div class="cam-overlay-row">
              <button class="btn danger" id="ov-stop">STOP</button>
              <button class="btn ghost" id="ov-exit">Exit Fullscreen</button>
            </div>
            <div class="cam-overlay-row seg-row" id="ov-res-row">
              <button class="btn" data-res="sd">SD</button>
              <button class="btn" data-res="hd">HD</button>
            </div>
            <div class="cam-dpad">
              <div></div><button class="btn" data-dpad="up" style="font-size:20px">↑</button><div></div>
              <button class="btn" data-dpad="left" style="font-size:20px">←</button><div></div><button class="btn" data-dpad="right" style="font-size:20px">→</button>
              <div></div><button class="btn" data-dpad="down" style="font-size:20px">↓</button><div></div>
            </div>
            <div class="cam-overlay-row">
              <button class="btn" data-dpad="lookup">Look Up</button>
              <button class="btn" data-dpad="lookdown">Look Down</button>
            </div>
            <div id="ov-emote-mount"></div>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <button class="btn" id="snap">Snapshot</button>
          <button class="btn" id="rec">Record</button>
          <button class="btn" id="fullscreen">Fullscreen</button>
          <div class="seg-row" style="margin-left:auto" id="res-row">
            <button class="btn" data-res="sd">SD</button>
            <button class="btn" data-res="hd">HD</button>
          </div>
        </div>
      </div>`;
    const img = el.querySelector("#cam");
    const camWrap = el.querySelector("#cam-wrap");
    const overlay = el.querySelector("#cam-overlay");

    const pilot = createPilotController(bus, () => 70);
    pilot.bindGaitButton(overlay.querySelector('[data-dpad="up"]'), "ov-up", 1);
    pilot.bindGaitButton(overlay.querySelector('[data-dpad="down"]'), "ov-down", -1);
    pilot.bindTurnButton(overlay.querySelector('[data-dpad="left"]'), "left");
    pilot.bindTurnButton(overlay.querySelector('[data-dpad="right"]'), "right");
    pilot.bindLookButton(overlay.querySelector('[data-dpad="lookup"]'), "up");
    pilot.bindLookButton(overlay.querySelector('[data-dpad="lookdown"]'), "down");

    const ovControl = overlay.querySelector("#ov-control");
    ovControl.onclick = () => bus.send({ t: "control", take: !bus.controlled });
    const offControl = bus.on("control", (m) => {
      ovControl.textContent = m.you ? "Release Control" : "Take Control";
      ovControl.classList.toggle("active", m.you);
      overlay.classList.toggle("locked", !m.you);
    });
    overlay.classList.toggle("locked", !bus.controlled);
    overlay.querySelector("#ov-stop").onclick = () => bus.send({ t: "stop" });
    overlay.querySelector("#ov-exit").onclick = () => document.exitFullscreen();

    mountEmotePopover(overlay.querySelector("#ov-emote-mount"), { bus });

    el.querySelector("#snap").onclick = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth || 640; c.height = img.naturalHeight || 480;
      c.getContext("2d").drawImage(img, 0, 0);
      const a = document.createElement("a");
      a.href = c.toDataURL("image/jpeg");
      a.download = `milo-${Date.now()}.jpg`;
      a.click();
    };

    const recBtn = el.querySelector("#rec");
    let recorder = null, recChunks = [], recTimer = null;
    function startRecording() {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || 640;
      canvas.height = img.naturalHeight || 480;
      const ctx = canvas.getContext("2d");
      recTimer = setInterval(() => ctx.drawImage(img, 0, 0, canvas.width, canvas.height), 66);
      recChunks = [];
      recorder = new MediaRecorder(canvas.captureStream(15), { mimeType: "video/webm" });
      recorder.ondataavailable = (e) => { if (e.data.size > 0) recChunks.push(e.data); };
      recorder.onstop = () => {
        clearInterval(recTimer);
        const blob = new Blob(recChunks, { type: "video/webm" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `milo-${Date.now()}.webm`;
        a.click();
        URL.revokeObjectURL(a.href);
        recBtn.disabled = false;
      };
      recorder.start();
      recBtn.innerHTML = `${ICON_RECORD}Stop & Save`;
      recBtn.classList.add("active");
    }
    function stopRecording() {
      recorder?.stop();
      recBtn.textContent = "Record";
      recBtn.classList.remove("active");
      recBtn.disabled = true;
    }
    recBtn.onclick = () => (recorder && recorder.state === "recording" ? stopRecording() : startRecording());

    // -- resolution: one shared toggle wired across every copy on the page
    // (the normal panel's #res-row and the fullscreen overlay's #ov-res-row)
    // -- clicking either updates both, since they reflect one piece of
    // server state (see camera.py: resolution is a shared-device setting).
    // Fullscreen does NOT auto-switch resolution -- whatever is selected
    // (even SD) just fills the screen, upscaled by the browser; the buttons
    // stay available in both modes so the choice is always the viewer's. --
    function setResButtons(name) {
      el.querySelectorAll("[data-res]").forEach((b) => b.classList.toggle("active", b.dataset.res === name));
    }
    setResButtons("sd");
    el.querySelectorAll("[data-res]").forEach((b) => {
      b.onclick = () => bus.send({ t: "camera_resolution", value: b.dataset.res });
    });
    const offTelemetry = bus.on("telemetry", (m) => { if (m.camera_resolution) setResButtons(m.camera_resolution); });

    el.querySelector("#fullscreen").onclick = () => camWrap.requestFullscreen();

    return () => {
      offTelemetry();
      offControl();
      pilot.stop();
      if (recorder && recorder.state === "recording") stopRecording();
    };
  },
};
