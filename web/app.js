// NCSA Training front door (Phase 2 prototype): shelves of tiles, side sheet, search and filters.
// Routes (hash): #/            home (My desktop + every shelf)
//                #/shelf/<id>  one shelf as a grid
//                #/tile/<id>   opens the side sheet over the current view (shareable link)
"use strict";

(() => {
  const { loadConfig, api, el, tileArt, TYPE_LABEL, store, footer } = window.NCSA;
  const $ = (id) => document.getElementById(id);
  const PIN_KEY = "ncsa-training:pins";

  const state = {
    tiles: new Map(),
    shelves: [],
    shelfIndex: new Map(),
    query: "",
    filters: new Set(),
    pins: [],
    baseHash: "#/",
    sheetPushed: false,
    currentTile: null,
  };

  // ---------- helpers ----------
  const hueFor = (tile) => {
    const first = tile.shelfIds && tile.shelfIds[0];
    return state.shelfIndex.has(first) ? state.shelfIndex.get(first) : 7;
  };
  const shelfTitle = (id) => (state.shelves.find((s) => s.id === id) || {}).title || id;
  // Status messages are for screen readers once the catalog has loaded (role=status, polite).
  const announce = (text) => { $("status").textContent = text; };

  // ---------- tiles ----------
  const tileCard = (tile, { startHere = false } = {}) => {
    const btn = el("button", { class: "tile", type: "button", "data-tile": tile.id, tabindex: "-1", "aria-haspopup": "dialog" });
    const badges = el("span", { class: "tile-badges" },
      el("span", { class: "badge", text: TYPE_LABEL[tile.type] || tile.type }),
      tile.guestAccess ? el("span", { class: "badge open", text: "Open to all" }) : null,
      startHere ? el("span", { class: "badge start", text: "Start here" }) : null,
      state.pins.includes(tile.id) ? el("span", { class: "badge pinned", text: "Pinned" }) : null);
    btn.append(
      tileArt(tile, hueFor(tile)),
      el("span", { class: "tile-title", text: tile.title }),
      tile.category ? el("span", { class: "tile-cat", text: tile.category.name }) : null,
      badges);
    btn.addEventListener("click", () => openTile(tile.id, true));
    return el("li", {}, btn);
  };

  const tileList = (tiles, { grid = false, label, startHere = null } = {}) => {
    const ul = el("ul", { class: grid ? "tiles grid" : "tiles", role: "list", "aria-label": label });
    for (const t of tiles) ul.append(tileCard(t, { startHere: t.id === startHere }));
    const first = ul.querySelector(".tile");
    if (first) first.tabIndex = 0; // one tab stop per row; arrow keys move within it
    return ul;
  };

  const section = (id, title, purpose, list, extra) => {
    const hid = `h-${id}`;
    const s = el("section", { class: "shelf", "aria-labelledby": hid },
      el("div", { class: "shelf-head" },
        el("div", {},
          el("h2", { id: hid, text: title }),
          purpose ? el("p", { class: "purpose", text: purpose }) : null),
        extra || null),
      list);
    return s;
  };

  // ---------- views ----------
  const renderHome = () => {
    const view = $("view");
    view.replaceChildren();
    const pinned = state.pins.map((id) => state.tiles.get(id)).filter(Boolean);
    if (pinned.length) {
      view.append(section("mydesktop", "My desktop", "Your pinned courses, saved in this browser.",
        tileList(pinned, { label: "My desktop" })));
    }
    for (const shelf of state.shelves) {
      const tiles = shelf.tileIds.map((id) => state.tiles.get(id)).filter(Boolean);
      if (!tiles.length) continue;
      const more = el("a", { class: "see-all", href: `#/shelf/${shelf.id}`, text: `See all ${tiles.length}` });
      more.setAttribute("aria-label", `See all ${tiles.length} in ${shelf.title}`);
      view.append(section(shelf.id, shelf.title, shelf.purpose, tileList(tiles, { label: shelf.title, startHere: shelf.startHere }), more));
    }
  };

  const renderShelf = (id) => {
    const shelf = state.shelves.find((s) => s.id === id);
    if (!shelf) {
      announce("That track was not found. Showing all tracks.");
      return renderHome();
    }
    const tiles = shelf.tileIds.map((t) => state.tiles.get(t)).filter(Boolean);
    const view = $("view");
    view.replaceChildren(
      el("p", { class: "crumb" }, el("a", { href: "#/", text: "← All tracks" })),
      section(shelf.id, shelf.title, shelf.purpose, tileList(tiles, { grid: true, label: shelf.title, startHere: shelf.startHere })));
  };

  const matches = (t) => {
    if (state.filters.has("open") && !t.guestAccess) return false;
    const types = ["course", "program"].filter((f) => state.filters.has(f));
    if (types.length && !types.includes(t.type)) return false;
    if (!state.query) return true;
    const hay = [t.title, t.summary, t.category && t.category.name, ...(t.children || []).map((c) => c.title),
      ...(t.shelfIds || []).map(shelfTitle)].join(" ").toLowerCase();
    return state.query.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
  };

  const renderResults = () => {
    const hits = [...state.tiles.values()].filter(matches);
    const bits = [];
    if (state.query) bits.push(`“${state.query}”`);
    for (const f of state.filters) bits.push({ open: "open to all", course: "courses", program: "programs" }[f]);
    const title = `${hits.length} result${hits.length === 1 ? "" : "s"}`;
    const clear = el("button", { type: "button", class: "secondary small", text: "Clear search and filters" });
    clear.addEventListener("click", clearSearch);
    const view = $("view");
    view.replaceChildren(section("results", title, bits.length ? `For ${bits.join(", ")}` : "",
      hits.length ? tileList(hits, { grid: true, label: "Results" })
        : el("p", { class: "empty", text: "Nothing matches. Try fewer words or clear the filters." }), clear));
    announce(title);
  };

  const renderView = () => {
    if (state.query || state.filters.size) return renderResults();
    const r = parseRoute(state.baseHash);
    if (r.shelf) renderShelf(r.shelf);
    else renderHome();
  };

  const clearSearch = () => {
    state.query = "";
    $("q").value = "";
    state.filters.clear();
    document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
    renderView();
    announce("Search cleared.");
    $("q").focus();
  };

  // ---------- side sheet ----------
  const sheet = $("sheet");

  const fillSheet = (tile) => {
    state.currentTile = tile.id;
    $("sheet-type").textContent = TYPE_LABEL[tile.type] || tile.type;
    $("sheet-title").textContent = tile.title;
    $("sheet-art").replaceChildren(tileArt(tile, hueFor(tile), "tile-art large", 64));
    const bits = [];
    if (tile.category) bits.push(tile.category.name);
    if (tile.level) bits.push(tile.level);
    if (tile.minutes) bits.push(`about ${tile.minutes} min`);
    bits.push(tile.guestAccess ? "Open to everyone, no sign-in needed" : "Sign in to Learn@NCSA to enroll");
    $("sheet-meta").textContent = bits.join(" · ");
    $("sheet-summary").textContent = tile.summary || "No description yet.";
    const kids = tile.children || [];
    $("sheet-children").hidden = kids.length < 2;
    $("sheet-children-title").textContent = tile.type === "program" ? "Inside this program" : "Sessions";
    $("sheet-children-list").replaceChildren(...kids.map((k) =>
      el("li", {}, el("a", { href: k.url, text: k.title, target: "_blank", rel: "noopener" }),
        k.guestAccess ? el("span", { class: "badge open", text: "Open to all" }) : null)));
    const tracks = $("sheet-tracks");
    tracks.replaceChildren();
    if (tile.shelfIds && tile.shelfIds.length) {
      tracks.append("Tracks: ");
      tile.shelfIds.forEach((sid, i) => {
        if (i) tracks.append(", ");
        tracks.append(el("a", { href: `#/shelf/${sid}`, text: shelfTitle(sid) }));
      });
    }
    const launch = $("sheet-launch");
    launch.href = tile.launch.url;
    launch.textContent = `${tile.launch.label} on Learn@NCSA`;
    if (tile.type === "tool") launch.textContent = tile.launch.label;
    updatePinButton();
  };

  const openTile = (id, fromApp) => {
    const tile = state.tiles.get(id);
    if (!tile) {
      if (sheet.open) sheet.close();
      state.sheetPushed = false;
      announce("That course is not in the catalog.");
      history.replaceState(null, "", state.baseHash);
      return;
    }
    if (fromApp) {
      state.sheetPushed = true;
      location.hash = `#/tile/${id}`; // hashchange will call showSheet
      return;
    }
    showSheet(tile);
  };

  const showSheet = (tile) => {
    fillSheet(tile);
    if (!sheet.open) sheet.showModal();
    $("sheet-title").focus();
  };

  const closeSheet = () => {
    if (!sheet.open) return;
    if (state.sheetPushed) {
      state.sheetPushed = false;
      history.back(); // hashchange closes the dialog
    } else {
      history.replaceState(null, "", state.baseHash);
      hideSheet();
    }
  };

  const hideSheet = () => {
    if (!sheet.open) return;
    const id = state.currentTile;
    sheet.close();
    const back = id && document.querySelector(`.tile[data-tile="${CSS.escape(id)}"]`);
    if (back) back.focus();
  };

  sheet.addEventListener("cancel", (e) => { e.preventDefault(); closeSheet(); });
  sheet.addEventListener("click", (e) => { if (e.target === sheet) closeSheet(); });
  $("sheet-close").addEventListener("click", closeSheet);

  // ---------- pins ----------
  const updatePinButton = () => {
    const pinned = state.pins.includes(state.currentTile);
    const b = $("sheet-pin");
    b.setAttribute("aria-pressed", String(pinned));
    b.textContent = pinned ? "Unpin from My desktop" : "Pin to My desktop";
  };
  $("sheet-pin").addEventListener("click", () => {
    const id = state.currentTile;
    state.pins = state.pins.includes(id) ? state.pins.filter((p) => p !== id) : [...state.pins, id];
    store.set(PIN_KEY, state.pins);
    updatePinButton();
    announce(state.pins.includes(id) ? "Pinned to My desktop." : "Removed from My desktop.");
    renderView();
  });

  // ---------- routing ----------
  function parseRoute(hash) {
    const m = /^#\/(tile|shelf)\/([a-z0-9-]+)$/.exec(hash || "");
    if (!m) return {};
    return m[1] === "tile" ? { tile: m[2] } : { shelf: m[2] };
  }

  const onRoute = () => {
    const r = parseRoute(location.hash);
    if (r.tile) {
      openTile(r.tile, false);
      return;
    }
    state.sheetPushed = false;
    const changed = state.baseHash !== (location.hash || "#/");
    if (!changed) {
      hideSheet(); // closing the sheet: keep the view and return focus to the tile
      return;
    }
    if (sheet.open) sheet.close();
    state.baseHash = location.hash || "#/";
    if (changed && (state.query || state.filters.size)) {
      state.query = "";
      $("q").value = "";
      state.filters.clear();
      document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", "false"));
    }
    $("tracks").open = false;
    renderView();
    if (changed) {
      window.scrollTo(0, 0);
      $("main").focus({ preventScroll: true });
    }
  };
  window.addEventListener("hashchange", onRoute);

  // ---------- search + filters ----------
  let searchTimer;
  $("q").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = e.target.value.trim();
      renderView();
    }, 120);
  });
  $("search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const first = document.querySelector("#view .tile");
    if (first) first.focus();
  });
  document.querySelectorAll(".chip").forEach((chip) => chip.addEventListener("click", () => {
    const f = chip.dataset.filter;
    if (state.filters.has(f)) state.filters.delete(f);
    else state.filters.add(f);
    chip.setAttribute("aria-pressed", String(state.filters.has(f)));
    renderView();
  }));

  // ---------- keyboard ----------
  const visibleTiles = () => [...document.querySelectorAll("#view .tile")];
  const focusTile = (t) => {
    if (!t) return;
    const list = t.closest(".tiles");
    list.querySelectorAll(".tile").forEach((x) => { x.tabIndex = -1; });
    t.tabIndex = 0;
    t.focus();
  };
  const vertical = (cur, dir) => {
    const c = cur.getBoundingClientRect();
    const cx = c.left + c.width / 2;
    let best = null;
    let bestScore = Infinity;
    for (const t of visibleTiles()) {
      if (t === cur) continue;
      const r = t.getBoundingClientRect();
      const dy = dir > 0 ? r.top - c.bottom : c.top - r.bottom;
      if (dy < -2) continue;
      const score = Math.round(dy / 20) * 100000 + Math.abs(r.left + r.width / 2 - cx);
      if (score < bestScore) { bestScore = score; best = t; }
    }
    return best;
  };

  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || e.target.isContentEditable;
    if (e.key === "/" && !typing && !sheet.open) {
      e.preventDefault();
      $("q").focus();
      $("q").select();
      return;
    }
    if (e.key === "Escape" && e.target === $("q") && $("q").value) {
      e.preventDefault();
      clearSearch();
      return;
    }
    const tile = e.target.closest && e.target.closest("#view .tile");
    if (!tile || e.altKey || e.ctrlKey || e.metaKey) return;
    const row = [...tile.closest(".tiles").querySelectorAll(".tile")];
    const i = row.indexOf(tile);
    let next = null;
    if (e.key === "ArrowRight") next = row[i + 1];
    else if (e.key === "ArrowLeft") next = row[i - 1];
    else if (e.key === "Home") next = row[0];
    else if (e.key === "End") next = row[row.length - 1];
    else if (e.key === "ArrowDown") next = vertical(tile, 1);
    else if (e.key === "ArrowUp") next = vertical(tile, -1);
    else return;
    e.preventDefault();
    focusTile(next);
  });
  document.addEventListener("focusin", (e) => {
    const t = e.target.closest && e.target.closest(".tiles .tile");
    if (t) {
      t.closest(".tiles").querySelectorAll(".tile").forEach((x) => { x.tabIndex = x === t ? 0 : -1; });
    }
  });

  // ---------- start ----------
  const start = async () => {
    try {
      await loadConfig();
      const [meta, shelves, tiles] = await Promise.all([api("/catalog/meta"), api("/shelves"), api("/tiles")]);
      state.tiles = new Map(tiles.items.map((t) => [t.id, t]));
      state.shelves = shelves.items;
      state.shelves.forEach((s, i) => state.shelfIndex.set(s.id, i));
      state.pins = (store.get(PIN_KEY, []) || []).filter((id) => state.tiles.has(id));
      $("tracks-list").replaceChildren(...state.shelves.map((s) =>
        el("li", {}, el("a", { href: `#/shelf/${s.id}`, text: s.title }))));
      $("footer-meta").textContent = footer(meta);
      $("status").textContent = "";
      $("status").classList.add("visually-hidden");
      const r = parseRoute(location.hash);
      state.baseHash = r.tile ? "#/" : location.hash || "#/";
      renderView();
      if (r.tile) openTile(r.tile, false);
    } catch (err) {
      $("status").classList.remove("visually-hidden");
      announce("The catalog could not be loaded. Please try again later.");
      console.error(err);
    }
  };
  start();
})();
