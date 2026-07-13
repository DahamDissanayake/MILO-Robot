// Single WebSocket to the robot: JSON topics + binary audio, auto-reconnect.
export function createBus() {
  const listeners = new Map();   // topic -> Set<fn>
  const binHandlers = new Set();
  let ws = null, backoff = 1000, hbTimer = null;
  const bus = { clientId: null, controlled: false, connected: false };

  function emit(topic, data) {
    (listeners.get(topic) || []).forEach((fn) => fn(data));
  }

  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      bus.connected = true; backoff = 1000; emit("_open", {});
      hbTimer = setInterval(() => bus.send({ t: "hb" }), 5000);
    };
    ws.onclose = () => {
      bus.connected = false; bus.controlled = false;
      clearInterval(hbTimer); emit("_close", {});
      setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 10000);
    };
    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) { binHandlers.forEach((fn) => fn(new Uint8Array(ev.data))); return; }
      let msg; try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.t === "hello") bus.clientId = msg.id;
      if (msg.t === "control") { bus.controlled = !!msg.you; }
      emit(msg.t, msg);
    };
  }

  bus.send = (obj) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); };
  bus.sendBytes = (u8) => { if (ws && ws.readyState === 1) ws.send(u8); };
  bus.on = (topic, fn) => {
    if (!listeners.has(topic)) listeners.set(topic, new Set());
    listeners.get(topic).add(fn);
    return () => listeners.get(topic).delete(fn);
  };
  bus.onBinary = (fn) => { binHandlers.add(fn); return () => binHandlers.delete(fn); };
  connect();
  return bus;
}
