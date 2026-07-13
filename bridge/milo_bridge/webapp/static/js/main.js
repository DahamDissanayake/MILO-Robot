import { createBus } from "./bus.js";
import { initStatusBar } from "./statusbar.js";
import { initLayout } from "./layout.js";
import { registry } from "./registry.js";

// theme (set before first paint to avoid a flash of the wrong theme)
const saved = localStorage.getItem("milo.theme");
if (saved) document.documentElement.dataset.theme = saved;
else if (matchMedia("(prefers-color-scheme: dark)").matches)
  document.documentElement.dataset.theme = "dark";

const bus = createBus();
const layout = initLayout(registry, bus);
initStatusBar(document.getElementById("statusbar"), bus, {
  onToolsToggle: () => layout.toggleTools(),
});
