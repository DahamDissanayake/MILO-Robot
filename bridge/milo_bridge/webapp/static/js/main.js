import { createBus } from "./bus.js";
import { initGrid } from "./grid.js";
import { cards } from "./registry.js";

// theme
const saved = localStorage.getItem("milo.theme");
if (saved) document.documentElement.dataset.theme = saved;
else if (matchMedia("(prefers-color-scheme: dark)").matches)
  document.documentElement.dataset.theme = "dark";
document.getElementById("btn-theme").onclick = () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("milo.theme", next);
};

const bus = createBus();

// connection dot + owner label + control button
const dot = document.getElementById("conn-dot");
const owner = document.getElementById("owner-label");
const btnControl = document.getElementById("btn-control");
bus.on("_open", () => dot.classList.add("live"));
bus.on("_close", () => { dot.classList.remove("live"); owner.textContent = "owner: —"; });
bus.on("control", (m) => {
  owner.textContent = `owner: ${m.owner}`;
  btnControl.textContent = m.you ? "Release Control" : "Take Control";
  btnControl.classList.toggle("active", m.you);
});
btnControl.onclick = () => bus.send({ t: "control", take: !bus.controlled });
document.getElementById("btn-stop").onclick = () => bus.send({ t: "stop" });

initGrid(document.getElementById("grid"), cards, bus);
