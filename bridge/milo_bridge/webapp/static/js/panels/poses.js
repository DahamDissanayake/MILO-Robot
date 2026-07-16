function fillButtons(box, names, type, bus) {
  names.forEach((name) => {
    const b = document.createElement("button");
    b.className = "btn"; b.textContent = name;
    b.onclick = () => bus.send({ t: type, name });
    box.appendChild(b);
  });
}

// Self-contained toggle icon + popover: fetches /api/poses and /api/faces
// and renders them behind a collapsed icon button instead of an
// always-visible grid. Exported so both the normal cockpit layout (default
// export below) and the camera panel's fullscreen overlay can mount the
// exact same implementation into their own container, rather than each
// keeping its own copy of this fetch-and-render logic.
export function mountEmotePopover(el, { bus }) {
  el.innerHTML = `
    <button class="btn emote-toggle">🎭 Emotes</button>
    <div class="emote-popover">
      <div class="muted">Poses</div><div class="pose-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px"></div>
      <div class="muted">Faces</div><div class="face-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px"></div>
    </div>`;
  const popover = el.querySelector(".emote-popover");
  popover.style.display = "none";
  el.querySelector(".emote-toggle").onclick = () => {
    popover.style.display = popover.style.display === "none" ? "block" : "none";
  };
  fetch("/api/poses").then((r) => r.json()).then((d) => fillButtons(el.querySelector(".pose-btns"), d.poses, "pose", bus));
  fetch("/api/faces").then((r) => r.json()).then((d) => fillButtons(el.querySelector(".face-btns"), d.faces, "face", bus));
}

export default {
  id: "poses", title: "Poses & Emotes", needsControl: true,
  mount(el, { bus }) {
    mountEmotePopover(el, { bus });
  },
};
