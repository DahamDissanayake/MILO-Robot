// Fixed cockpit layout: mounts zone-grouped panels into fixed slots and
// manages the Tools drawer. Replaces the old drag/resize grid.js.
export function initLayout(registry, bus) {
  const center = document.getElementById("cockpit-center");
  const side = document.getElementById("cockpit-side");
  const graphZone = document.getElementById("memory-graph");
  const drawer = document.getElementById("tools-drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  const drawerClose = document.getElementById("drawer-close");

  function mountInto(container, panels) {
    for (const panel of panels) {
      const section = document.createElement("section");
      section.className = "panel";
      section.dataset.id = panel.id;
      section.innerHTML = `<h2 class="panel-title">${panel.title}</h2><div class="panel-body"></div>`;
      container.appendChild(section);
      panel.mount(section.querySelector(".panel-body"), { bus });
      if (panel.needsControl) {
        const applyLock = () => section.classList.toggle("locked", !bus.controlled);
        applyLock();
        bus.on("control", applyLock);
        bus.on("_close", applyLock);
      }
    }
  }

  mountInto(center, registry.cockpitCenter);
  mountInto(side, registry.cockpitSide);
  mountInto(graphZone, registry.graph);
  mountInto(drawer, registry.tools);

  let open = false;
  function setOpen(next) {
    open = next;
    drawer.classList.toggle("open", open);
    backdrop.classList.toggle("open", open);
  }
  backdrop.onclick = () => setOpen(false);
  drawerClose.onclick = () => setOpen(false);

  return { toggleTools: () => setOpen(!open) };
}
