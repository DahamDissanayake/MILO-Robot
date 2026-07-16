export default {
  id: "camera", title: "Camera",
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;height:100%">
        <img id="cam" src="/stream/camera" alt="camera offline"
             onerror="this.dataset.err=1">
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <button class="btn" id="snap">Snapshot</button>
          <div style="display:flex;gap:2px;margin-left:auto" id="res-row">
            <button class="btn" data-res="sd">SD</button>
            <button class="btn" data-res="hd">HD</button>
          </div>
        </div>
      </div>`;
    const img = el.querySelector("#cam");
    el.querySelector("#snap").onclick = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth || 640; c.height = img.naturalHeight || 480;
      c.getContext("2d").drawImage(img, 0, 0);
      const a = document.createElement("a");
      a.href = c.toDataURL("image/jpeg");
      a.download = `milo-${Date.now()}.jpg`;
      a.click();
    };

    const resRow = el.querySelector("#res-row");
    function setResButtons(name) {
      resRow.querySelectorAll("[data-res]").forEach((b) => b.classList.toggle("active", b.dataset.res === name));
    }
    setResButtons("sd");
    resRow.querySelectorAll("[data-res]").forEach((b) => {
      b.onclick = () => bus.send({ t: "camera_resolution", value: b.dataset.res });
    });
    const offTelemetry = bus.on("telemetry", (m) => { if (m.camera_resolution) setResButtons(m.camera_resolution); });

    return () => {
      offTelemetry();
    };
  },
};
