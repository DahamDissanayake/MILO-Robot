// Brain card: which brain (if any) is connected, which ones this robot
// already knows, and a button to make the robot discoverable/pairable on
// the LAN. The robot has no discovery role of its own in this
// architecture -- it can only report what it truthfully knows (currently
// connected, or previously paired), never a fabricated "scanning" list.
export default {
  id: "brain", title: "Brain",
  mount(el, { bus }) {
    el.innerHTML = `
      <div id="brain-status" class="muted">Loading…</div>
      <ul id="paired-list" style="list-style:none;padding:0;margin:8px 0"></ul>
      <button class="btn" id="pair-btn">Enter Pairing Mode</button>`;
    const statusEl = el.querySelector("#brain-status");
    const listEl = el.querySelector("#paired-list");
    const btn = el.querySelector("#pair-btn");

    function setButton(on) {
      btn.textContent = on ? "Pairing Mode: ON (tap to cancel)" : "Enter Pairing Mode";
      btn.classList.toggle("active", on);
    }

    async function refresh() {
      const r = await fetch("/api/brains").then((res) => res.json()).catch(() => null);
      if (!r) return;
      statusEl.textContent = r.connected ? `Connected: ${r.connected.name}` : "No brain connected";
      listEl.innerHTML = r.paired.length
        ? r.paired.map((b) => `<li>${b.name}${r.connected && r.connected.id === b.id ? " (online)" : ""}</li>`).join("")
        : `<li class="muted">No paired brains yet</li>`;
      setButton(r.pairing);
    }

    // Flipping the switch only ever sends the toggle -- no other side
    // effects, and its own visual state (button label/active class) is the
    // only thing that changes immediately; the robot doesn't move.
    btn.onclick = () => bus.send({ t: "enter_pairing_mode", on: !btn.classList.contains("active") });
    const offPairing = bus.on("pairing", (m) => { setButton(m.on); refresh(); });

    refresh();
    const iv = setInterval(refresh, 5000); // connected/paired state has no other push channel

    return () => { offPairing(); clearInterval(iv); };
  },
};
