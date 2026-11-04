// NCSA Training front door (Phase 1 prototype).
// Reads only the public API. config.json says where it lives:
//   GitHub Pages: {"apiBase": "api/v1", "suffix": ".json"}
//   Harness API:  {"apiBase": "http://localhost:8000/api/v1", "suffix": ""}
// All text from the API is inserted with textContent, never as HTML.
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const TYPE_LABEL = { course: "Course", program: "Program", live: "Live", recording: "Recording", tool: "Tool" };
  let config = { apiBase: "api/v1", suffix: ".json" };
  let tilesById = new Map();

  const api = async (path) => {
    const res = await fetch(`${config.apiBase}${path}${config.suffix}`, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    return res.json();
  };

  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("data-")) node.setAttribute(k, v);
      else node[k] = v;
    }
    for (const c of children) if (c) node.append(c);
    return node;
  };

  const tileCard = (tile, startHere) => {
    const btn = el("button", { class: "tile", type: "button", "data-tile": tile.id });
    btn.setAttribute("aria-haspopup", "dialog");
    const art = el("span", { class: "tile-art" });
    art.setAttribute("aria-hidden", "true");
    const letter = () => {
      art.replaceChildren(tile.title.trim().charAt(0).toUpperCase());
      art.dataset.hue = String([...tile.id].reduce((a, c) => a + c.charCodeAt(0), 0) % 6);
    };
    if (tile.image) {
      const img = el("img", { src: tile.image, alt: "", loading: "lazy" });
      img.addEventListener("error", letter, { once: true });
      art.append(img);
    } else {
      letter();
    }
    const badges = el("span", { class: "tile-badges" },
      el("span", { class: "badge", text: TYPE_LABEL[tile.type] || tile.type }),
      tile.guestAccess ? el("span", { class: "badge open", text: "Open to all" }) : null,
      startHere ? el("span", { class: "badge start", text: "Start here" }) : null);
    btn.append(art, el("span", { class: "tile-title", text: tile.title }), badges);
    btn.addEventListener("click", () => openSheet(tile));
    return el("li", {}, btn);
  };

  const openSheet = (tile) => {
    $("sheet-type").textContent = TYPE_LABEL[tile.type] || tile.type;
    $("sheet-title").textContent = tile.title;
    const bits = [];
    if (tile.category) bits.push(tile.category.name);
    if (tile.level) bits.push(tile.level);
    if (tile.minutes) bits.push(`about ${tile.minutes} min`);
    bits.push(tile.guestAccess ? "open to everyone, no sign-in needed" : "sign in to Learn@NCSA to enroll");
    $("sheet-meta").textContent = bits.join(" · ");
    $("sheet-summary").textContent = tile.summary || "No description yet.";
    const list = $("sheet-children-list");
    list.replaceChildren();
    const kids = tile.children || [];
    $("sheet-children").hidden = kids.length < 2;
    for (const k of kids) {
      list.append(el("li", {}, el("a", { href: k.url, text: k.title, target: "_blank", rel: "noopener" })));
    }
    const launch = $("sheet-launch");
    launch.href = tile.launch.url;
    launch.textContent = `${tile.launch.label} on Learn@NCSA`;
    $("sheet").showModal();
  };

  const renderShelves = (shelves) => {
    const root = $("shelves");
    root.replaceChildren();
    for (const shelf of shelves) {
      const headingId = `shelf-${shelf.id}`;
      const list = el("ul", { class: "tiles" });
      for (const id of shelf.tileIds) {
        const tile = tilesById.get(id);
        if (tile) list.append(tileCard(tile, id === shelf.startHere));
      }
      const section = el("section", { class: "shelf" },
        el("h2", { id: headingId, text: shelf.title }),
        el("p", { class: "purpose", text: shelf.purpose }),
        list);
      section.setAttribute("aria-labelledby", headingId);
      root.append(section);
    }
  };

  const search = (q) => {
    const results = $("results");
    const list = $("results-list");
    const term = q.trim().toLowerCase();
    if (!term) {
      results.hidden = true;
      $("shelves").hidden = false;
      return;
    }
    const hits = [...tilesById.values()].filter((t) =>
      `${t.title} ${t.summary} ${t.category ? t.category.name : ""}`.toLowerCase().includes(term));
    list.replaceChildren(...hits.map((t) => tileCard(t, false)));
    $("results-title").textContent = hits.length ? `${hits.length} result${hits.length === 1 ? "" : "s"}` : "No results";
    results.hidden = false;
    $("shelves").hidden = true;
  };

  // Arrow keys move between tiles within a row; Home/End jump to the ends.
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && document.activeElement !== $("q")) {
      e.preventDefault();
      $("q").focus();
      return;
    }
    const tile = e.target.closest && e.target.closest(".tile");
    if (!tile) return;
    const row = [...tile.closest(".tiles").querySelectorAll(".tile")];
    const i = row.indexOf(tile);
    const next = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: row.length - 1 }[e.key];
    if (next !== undefined && row[next]) {
      e.preventDefault();
      row[next].focus();
    }
  });

  const start = async () => {
    try {
      const cfg = await fetch("config.json").then((r) => (r.ok ? r.json() : {}));
      config = { ...config, ...cfg };
      const [meta, shelves, tiles] = await Promise.all([api("/catalog/meta"), api("/shelves"), api("/tiles")]);
      tilesById = new Map(tiles.items.map((t) => [t.id, t]));
      renderShelves(shelves.items);
      $("status").textContent = "";
      $("status").hidden = true;
      const when = new Date(meta.generatedAt).toLocaleString();
      $("footer-meta").textContent =
        `${meta.counts.courses} courses from ${meta.source.siteName} (Moodle ${meta.source.release.split(" ")[0]}), updated ${when}.`;
      $("q").addEventListener("input", (e) => search(e.target.value));
    } catch (err) {
      $("status").textContent = "The catalog could not be loaded. Please try again later.";
      console.error(err);
    }
  };
  start();
})();
