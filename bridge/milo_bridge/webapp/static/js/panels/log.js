export default {
  id: "log", title: "Bridge Log",
  mount(el, { bus }) {
    el.innerHTML = `<pre id="loglines" style="margin:0;font-size:11px;white-space:pre-wrap"></pre>`;
    const pre = el.querySelector("#loglines");
    const push = (line) => {
      pre.textContent += line + "\n";
      const lines = pre.textContent.split("\n");
      if (lines.length > 300) pre.textContent = lines.slice(-300).join("\n");
      el.scrollTop = el.scrollHeight;
    };
    fetch("/api/logs?n=100").then((r) => r.json())
      .then((d) => d.lines.forEach(push)).catch(() => {});
    return bus.on("log", (m) => push(m.line));
  },
};
