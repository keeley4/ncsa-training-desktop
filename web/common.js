// Shared helpers for the front door and the curation page.
// The page reads only the public API. config.json says where it lives:
//   GitHub Pages: {"apiBase": "api/v1", "suffix": ".json"}
//   Harness API:  {"apiBase": "http://localhost:8000/api/v1", "suffix": ""}
// All text from the API is inserted with textContent, never as HTML.
"use strict";

window.NCSA = (() => {
  let config = { apiBase: "api/v1", suffix: ".json" };

  const loadConfig = async () => {
    try {
      const r = await fetch("config.json", { cache: "no-cache" });
      if (r.ok) config = { ...config, ...(await r.json()) };
    } catch (_) { /* defaults */ }
    return config;
  };

  const api = async (path) => {
    const res = await fetch(`${config.apiBase}${path}${config.suffix}`, { headers: { Accept: "application/json" } });
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    return res.json();
  };

  const el = (tag, props = {}, ...children) => {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("data-") || k.startsWith("aria-") || k === "role" || k === "tabindex") node.setAttribute(k, String(v));
      else node[k] = v;
    }
    for (const c of children) if (c !== null && c !== undefined && c !== false) node.append(c);
    return node;
  };

  // Simple line icons (24x24) drawn as SVG paths. Decorative only.
  const ICONS = {
    course: "M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2V5zm2 14h12M8 7h7M8 10h7",
    program: "M4 6h16v13H4zM4 10h16M8 3v4M16 3v4M8 14h3M13 14h3",
    live: "M12 13a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM7.8 7.8a6 6 0 0 0 0 8.4M16.2 16.2a6 6 0 0 0 0-8.4M5 5a10 10 0 0 0 0 14M19 19a10 10 0 0 0 0-14",
    recording: "M4 5h16v14H4zM10 9v6l5-3z",
    tool: "M14.7 6.3a4 4 0 0 0-5.4 5.1L4 16.7 7.3 20l5.3-5.3a4 4 0 0 0 5.1-5.4l-2.5 2.5-2.4-.6-.6-2.4z",
  };
  const SVGNS = "http://www.w3.org/2000/svg";
  const icon = (type, size = 40) => {
    const svg = document.createElementNS(SVGNS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", String(size));
    svg.setAttribute("height", String(size));
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS(SVGNS, "path");
    path.setAttribute("d", ICONS[type] || ICONS.course);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "1.6");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.append(path);
    return svg;
  };

  const HUES = 8;
  // Tile art: the course image when there is one, otherwise the shelf colour with a type icon.
  const tileArt = (tile, hueIndex, cls = "tile-art", iconSize = 40) => {
    const art = el("span", { class: cls, "aria-hidden": "true" });
    const generated = () => {
      art.replaceChildren(icon(tile.type, iconSize));
      art.dataset.hue = String(((hueIndex % HUES) + HUES) % HUES);
      art.classList.add("generated");
    };
    if (tile.image) {
      const img = el("img", { src: tile.image, alt: "", loading: "lazy", decoding: "async" });
      img.addEventListener("error", generated, { once: true });
      art.append(img);
    } else {
      generated();
    }
    return art;
  };

  const TYPE_LABEL = { course: "Course", program: "Program", live: "Live", recording: "Recording", tool: "Tool" };

  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); } catch (_) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* private mode etc. */ }
    },
  };

  const footer = (meta) => {
    const when = new Date(meta.generatedAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    const release = String(meta.source.release || "").split(" ")[0];
    return `${meta.counts.courses} courses from ${meta.source.siteName}${release ? ` (Moodle ${release})` : ""}, updated ${when}.`;
  };

  return { loadConfig, api, el, icon, tileArt, TYPE_LABEL, store, footer, get config() { return config; } };
})();
