export default {
  id: "poses", title: "Poses & Emotes", w: 4, h: 3, needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `<div class="muted">Poses</div><div id="pose-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px"></div>
      <div class="muted">Faces</div><div id="face-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px"></div>`;
    const fill = (sel, names, type) => {
      const box = el.querySelector(sel);
      names.forEach((name) => {
        const b = document.createElement("button");
        b.className = "btn"; b.textContent = name;
        b.onclick = () => bus.send({ t: type, name });
        box.appendChild(b);
      });
    };
    fetch("/api/poses").then((r) => r.json()).then((d) => fill("#pose-btns", d.poses, "pose"));
    fetch("/api/faces").then((r) => r.json()).then((d) => fill("#face-btns", d.faces, "face"));
  },
};
