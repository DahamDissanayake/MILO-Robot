// Brain card: which brain(s) are connected right now (the robot accepts
// several at once, but only one -- "active" -- may actually move it),
// which ones this robot already knows, and a button to make the robot
// discoverable/pairable on the LAN. The robot has no discovery role of
// its own in this architecture -- it can only report what it truthfully
// knows (currently connected, or previously paired), never a fabricated
// "scanning" list.
export default {
  id: "brain", title: "Brain", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div id="brain-status" class="muted">Loading…</div>
      <div id="brain-ip" class="muted"></div>
      <ul id="connected-list" style="list-style:none;padding:0;margin:8px 0"></ul>
      <ul id="paired-list" style="list-style:none;padding:0;margin:8px 0"></ul>
      <button class="btn" id="pair-btn">Enter Pairing Mode</button>`;
    const statusEl = el.querySelector("#brain-status");
    const ipEl = el.querySelector("#brain-ip");
    const connectedEl = el.querySelector("#connected-list");
    const listEl = el.querySelector("#paired-list");
    const btn = el.querySelector("#pair-btn");

    function setButton(on) {
      btn.textContent = on ? "Pairing Mode: ON (tap to cancel)" : "Enter Pairing Mode";
      btn.classList.toggle("active", on);
    }

    async function refresh() {
      const r = await fetch("/api/brains").then((res) => res.json()).catch(() => null);
      if (!r) return;
      const connected = r.connected || [];
      statusEl.textContent = connected.length
        ? `Connected: ${connected.length} brain${connected.length > 1 ? "s" : ""}`
        : "No brain connected";
      // Shown regardless of pairing state -- also useful for manually
      // reconnecting an already-paired brain when mDNS discovery doesn't
      // reach it (some routers don't forward multicast between WiFi
      // clients), not just for a fresh pairing.
      ipEl.innerHTML = r.ip
        ? (r.pairing ? `<b>Connect to: ${r.ip}:${r.port}</b>` : `IP: ${r.ip}:${r.port}`)
        : "";
      const connectedIds = new Set(connected.map((b) => b.id));
      connectedEl.innerHTML = connected.length
        ? connected.map((b) => `
            <li>
              ${b.name}${b.active ? " <b>(active)</b>" : ""}
              ${b.active ? "" : `<button class="btn switch-brain-btn" data-id="${b.id}">Make Active</button>`}
              <button class="btn disconnect-brain-btn" data-id="${b.id}">Disconnect</button>
            </li>`).join("")
        : "";
      listEl.innerHTML = r.paired.length
        ? r.paired.map((b) => `<li>${b.name}${connectedIds.has(b.id) ? " (online)" : ""}</li>`).join("")
        : `<li class="muted">No paired brains yet</li>`;
      setButton(r.pairing);
    }

    // Flipping the switch only ever sends the toggle -- no other side
    // effects, and its own visual state (button label/active class) is the
    // only thing that changes immediately; the robot doesn't move.
    btn.onclick = () => bus.send({ t: "enter_pairing_mode", on: !btn.classList.contains("active") });
    connectedEl.onclick = (ev) => {
      const sw = ev.target.closest(".switch-brain-btn");
      if (sw) { bus.send({ t: "switch_active_brain", id: sw.dataset.id }); return; }
      const dc = ev.target.closest(".disconnect-brain-btn");
      if (dc) bus.send({ t: "disconnect_brain", id: dc.dataset.id });
    };
    const offPairing = bus.on("pairing", (m) => { setButton(m.on); refresh(); });

    refresh();
    const iv = setInterval(refresh, 5000); // connected/paired state has no other push channel

    return () => { offPairing(); clearInterval(iv); };
  },
};
