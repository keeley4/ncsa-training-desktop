// NCSA Training front door (Phase 2 prototype): shelves of tiles, side sheet, search and filters.
// Routes (hash): #/            home (My desktop + every shelf)
//                #/workshops   live workshops (upcoming and past)
//                #/shelf/<id>  one shelf as a grid
//                #/tile/<id>   opens the side sheet over the current view (shareable link)
//                #/path?work=&scale=&fit=&ex=&par=   learning path from From Laptop to Cluster (harness only);
//                #/path        the signed-in learner's saved path, else the general journey
//                #/path?view=all  the general journey (every stage, no answers)
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
    me: null,          // /me when served by the harness and signed in
    progress: null,    // /me/progress: { status, items }
    byCourse: new Map(), // Moodle course id -> progress item
    workshops: [],     // /events?when=all (harness only)
    myPath: null,      // /me/path: { saved, answers, path }
    journey: null,     // /paths with no answers: the general laptop-to-cluster journey (stage of each tile)
  };

  // ---------- helpers ----------
  const hueFor = (tile) => {
    const first = tile.shelfIds && tile.shelfIds[0];
    return state.shelfIndex.has(first) ? state.shelfIndex.get(first) : 7;
  };
  const shelfTitle = (id) => (state.shelves.find((s) => s.id === id) || {}).title || id;
  // Status messages are for screen readers once the catalog has loaded (role=status, polite).
  const announce = (text) => { $("status").textContent = text; };

  // ---------- usage analytics (harness only; aggregate: random per-session id, no user id) ----------
  const usage = (() => {
    let queue = [];
    let sid = null;
    const id = () => {
      if (sid) return sid;
      try { sid = sessionStorage.getItem("ncsa-training:sid"); } catch (_) { /* private mode */ }
      if (!sid || !/^[a-f0-9]{16,32}$/.test(sid)) {
        sid = [...crypto.getRandomValues(new Uint8Array(12))].map((b) => b.toString(16).padStart(2, "0")).join("");
        try { sessionStorage.setItem("ncsa-training:sid", sid); } catch (_) { /* fine */ }
      }
      return sid;
    };
    const flush = () => {
      if (!queue.length || !window.NCSA.config.signIn) { queue = []; return; }
      const body = JSON.stringify({ sid: id(), events: queue.splice(0, 25) });
      const blob = new Blob([body], { type: "application/json" });
      if (!(navigator.sendBeacon && navigator.sendBeacon("/api/v1/usage", blob))) {
        fetch("/api/v1/usage", { method: "POST", body, headers: { "Content-Type": "application/json" }, keepalive: true }).catch(() => {});
      }
      if (queue.length) flush();
    };
    setInterval(flush, 10000);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });
    window.addEventListener("pagehide", flush);
    return (kind, target = "", detail = "", n = null) => {
      queue.push({ kind, target: String(target), detail: String(detail), n });
      if (queue.length >= 20) flush();
    };
  })();

  // ---------- live workshops (harness only) ----------
  const wsTime = (w) => {
    const s = w.sessions[0];
    if (!s) return "";
    const tz = w.timezone || "America/Chicago";
    const d = new Date(s.start);
    const day = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: tz }).format(d);
    const t = (x, zone) => new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone: tz,
      ...(zone ? { timeZoneName: "short" } : {}) }).format(new Date(x));
    const more = w.sessions.length > 1 ? ` · ${w.sessions.length} sessions` : "";
    return `${day} · ${t(s.start)}–${t(s.end, true)}${more}`;
  };
  const wsEnded = (w) => w.sessions.length && new Date(w.sessions[w.sessions.length - 1].end) < new Date();
  const upcomingWorkshops = () => state.workshops.filter((w) => !wsEnded(w));
  const icsHref = (id) => `${window.NCSA.config.apiBase}/events/${id}.ics`;

  const workshopList = (items, { past = false } = {}) => {
    const ul = el("ul", { class: "ws-list", role: "list" });
    for (const w of items) {
      const d = new Date(w.sessions[0].start);
      const tz = w.timezone || "America/Chicago";
      const fmt = (o) => new Intl.DateTimeFormat("en-US", { ...o, timeZone: tz }).format(d);
      const cancelled = w.status === "cancelled";
      const actions = el("div", { class: "ws-actions" });
      if (!past && !cancelled) {
        if (w.registerUrl) {
          const reg = el("a", { class: "primary small", href: w.registerUrl, target: "_blank", rel: "noopener",
            text: "Register", "aria-label": `Register for ${w.title}` });
          reg.addEventListener("click", () => usage("register_click", w.id));
          actions.append(reg);
        }
        const cal = el("a", { class: "secondary small ws-cal", href: icsHref(w.id), download: "",
          text: "Add to calendar", "aria-label": `Add ${w.title} to your calendar` });
        cal.addEventListener("click", () => usage("ics_download", w.id));
        actions.append(cal);
      }
      if (past && w.recordingUrl) {
        actions.append(el("a", { class: "secondary small ws-cal", href: w.recordingUrl, target: "_blank", rel: "noopener",
          text: "Watch the recording" }));
      }
      if (w.moodleUrl) {
        actions.append(el("a", { class: "ws-link", href: w.moodleUrl, target: "_blank", rel: "noopener", text: "Self-paced course" }));
      }
      const meta = [FORMAT_LABEL[w.format] || "Webinar", w.speakers].filter(Boolean).join(" · ");
      ul.append(el("li", { class: `ws-item${cancelled ? " cancelled" : ""}${past ? " past" : ""}` },
        el("div", { class: "ws-date", "aria-hidden": "true" },
          el("span", { class: "ws-mon", text: fmt({ month: "short" }) }),
          el("span", { class: "ws-day", text: fmt({ day: "numeric" }) })),
        el("div", { class: "ws-body" },
          el("h3", { class: "ws-title", text: w.title }),
          el("p", { class: "ws-when" }, cancelled ? el("span", { class: "badge cancel", text: "Cancelled" }) : null,
            ` ${wsTime(w)}`),
          meta ? el("p", { class: "ws-meta", text: meta }) : null,
          w.summary ? el("p", { class: "ws-summary", text: w.summary }) : null,
          actions)));
    }
    return ul;
  };
  const FORMAT_LABEL = { webinar: "Webinar", series: "Workshop series", "hands-on": "Hands-on workshop" };

  const workshopsSection = () => {
    const next = upcomingWorkshops().slice(0, 3);
    if (!next.length) return null;
    const more = el("a", { class: "see-all", href: "#/workshops", text: `All workshops` });
    return section("workshops", "Live workshops", "Upcoming online workshops. Register on the workshop's form.",
      workshopList(next), more);
  };

  const renderWorkshops = () => {
    const up = upcomingWorkshops();
    const past = state.workshops.filter(wsEnded).reverse();
    const feed = el("a", { class: "see-all", href: `${window.NCSA.config.apiBase}/events.ics`,
      text: "Subscribe to the calendar feed" });
    feed.addEventListener("click", () => usage("ics_download", "feed"));
    $("view").replaceChildren(
      el("p", { class: "crumb" }, el("a", { href: "#/", text: "← All tracks" })),
      section("workshops", "Live workshops", "Online workshops led by NCSA staff. Registration opens the workshop's form.",
        up.length ? workshopList(up) : el("p", { class: "empty", text: "No upcoming workshops are scheduled right now." }), feed),
      past.length ? section("past-workshops", "Past workshops", "Recordings are linked when available.",
        workshopList(past.slice(0, 12), { past: true })) : "");
    usage("workshops_view");
  };

  // ---------- learner progress (harness only) ----------
  const courseIdOf = (url) => {
    const m = /\/course\/view\.php\?id=(\d+)/.exec(url || "");
    return m ? Number(m[1]) : null;
  };
  const progressOf = (tile) => (tile.type === "tool" ? null : state.byCourse.get(courseIdOf(tile.launch.url)) || null);
  const progressText = (p) => (p.completed ? "Completed" : p.progress == null ? "Enrolled" : `${p.progress}% complete`);
  const linked = () => !!(state.me && state.me.user.moodle && state.me.user.moodle.status === "linked");

  const loadProgress = async () => {
    if (!linked()) return;
    try {
      state.progress = await api("/me/progress");
      state.byCourse = new Map((state.progress.items || []).map((i) => [i.courseId, i]));
    } catch (_) { state.progress = null; state.byCourse = new Map(); }
  };

  const continueSection = () => {
    const items = (state.progress && state.progress.items) || [];
    if (!items.length) return null;
    const shown = items.slice(0, 6);
    const ul = el("ul", { class: "continue", role: "list", "aria-label": "Continue learning" });
    for (const p of shown) {
      const bar = el("progress", { max: "100", value: String(p.completed ? 100 : p.progress || 0),
        "aria-label": `${p.title}: ${progressText(p)}` });
      if (p.progress == null && !p.completed) bar.removeAttribute("value");
      const title = p.tileId && state.tiles.has(p.tileId)
        ? el("a", { href: `#/tile/${p.tileId}`, class: "continue-title", text: p.title })
        : el("span", { class: "continue-title", text: p.title });
      ul.append(el("li", { class: "continue-item" },
        title,
        el("span", { class: "continue-status" }, p.progress == null && !p.completed ? null : bar,
          el("span", { text: progressText(p) })),
        (() => {
          const a = el("a", { class: "primary small", href: p.url, target: "_blank", rel: "noopener",
            text: p.completed ? "Review" : "Continue", "aria-label": `${p.completed ? "Review" : "Continue"} ${p.title} on Learn@NCSA` });
          a.addEventListener("click", () => usage("continue", p.courseId));
          return a;
        })()));
    }
    const extra = items.length > shown.length
      ? el("span", { class: "purpose", text: `Showing your ${shown.length} most recent of ${items.length}.` }) : null;
    return section("continue", "Continue learning", "Your Learn@NCSA courses, most recent first.", ul, extra);
  };


  // ---------- learning paths from From Laptop to Cluster (harness only) ----------
  const PATH_KEYS = ["work", "scale", "fit", "ex", "par"];
  const L2C_DEFAULT = "https://keeley4.github.io/intro_hpc/";
  const sameAnswers = (a, b) => !!a && !!b && PATH_KEYS.every((k) => (a[k] || "") === (b[k] || ""));

  const loadMyPath = async () => {
    if (!state.me) { state.myPath = null; return; }
    try { state.myPath = await api("/me/path"); } catch (_) { state.myPath = null; }
  };

  const pathWorkshops = (items) => (items && items.length ? workshopList(items) : null);

  const stageOfTile = (id) => {
    if (!state.journey) return null;
    const st = state.journey.stages.findIndex((x) => x.tileIds.includes(id));
    return st >= 0 ? { no: st + 1, stage: state.journey.stages[st] } : null;
  };

  const journeyBanner = () => {
    if (!state.journey) return null;
    const steps = el("ol", { class: "journey-strip", "aria-label": "Stages from laptop to cluster" },
      ...state.journey.stages.map((st, i) => el("li", {},
        el("a", { href: "#/path?view=all", text: `${i + 1}. ${st.title.split(":")[0]}` }))));
    const l2c = el("a", { class: "primary journey-cta", href: state.journey.l2cUrl || L2C_DEFAULT, target: "_blank",
      rel: "noopener", "aria-label": "Find your starting point: a 5-minute interactive, opens in a new tab" },
      el("span", { class: "journey-cta-main", text: "Find your starting point →" }),
      el("span", { class: "journey-cta-sub", text: "5-minute interactive · answer a few questions about your research" }));
    l2c.addEventListener("click", () => usage("path_view", "banner_l2c"));
    return el("section", { class: "shelf journey-banner", "aria-labelledby": "h-journey" },
      el("div", { class: "shelf-head" }, el("div", {},
        el("h2", { id: "h-journey", text: "Not sure where to start?" }),
        el("p", { class: "purpose", text: "Pick a stage of the learning path below, or get courses picked for your research." }))),
      steps,
      el("p", { class: "journey-actions" }, l2c));
  };

  const yourPathSection = () => {
    const mp = state.myPath;
    if (!mp || !mp.saved || !mp.path.here) return journeyBanner();
    const tiles = mp.path.focus.map((id) => state.tiles.get(id)).filter(Boolean);
    if (!tiles.length) return null;
    const stage = mp.path.stages.find((x) => x.position === "here");
    const more = el("a", { class: "see-all", href: "#/path", text: "See your full path" });
    return section("yourpath", "Your learning path",
      `${stage ? `You are at: ${stage.title}. ` : ""}Do these next, based on your From Laptop to Cluster answers.`,
      tileList(tiles, { label: "Your learning path" }), more);
  };

  const savePathControls = (answers) => {
    const box = el("div", { class: "path-save" });
    const status = el("p", { class: "purpose", role: "status" });
    const saved = state.myPath && state.myPath.saved && sameAnswers(state.myPath.answers, answers);
    if (!window.NCSA.config.signIn) return null;
    if (!state.me) {
      const next = "/" + (location.hash || "#/path");
      box.append(el("a", { class: "primary small", href: `/auth/login?next=${encodeURIComponent(next)}`,
        text: "Sign in to save this path" }),
      el("p", { class: "purpose", text: "Saving keeps this path on My Desktop and shows your progress in each course." }));
      return box;
    }
    const send = async (method) => {
      const opts = { method, credentials: "same-origin", headers: { "Content-Type": "application/json" } };
      if (method === "PUT") opts.body = JSON.stringify(answers);
      const res = await fetch("/api/v1/me/path", opts);
      if (!res.ok) throw new Error(String(res.status));
      state.myPath = await res.json();
    };
    if (saved) {
      const rm = el("button", { type: "button", class: "secondary small", text: "Remove saved path" });
      rm.addEventListener("click", async () => {
        try { await send("DELETE"); announce("Saved path removed."); renderView(); }
        catch (_) { status.textContent = "Could not remove the path. Please try again."; }
      });
      box.append(el("span", { class: "badge mine", text: "Saved to My Desktop" }), " ", rm);
    } else {
      const save = el("button", { type: "button", class: "primary small",
        text: state.myPath && state.myPath.saved ? "Replace my saved path with this one" : "Save to My Desktop" });
      save.addEventListener("click", async () => {
        try { await send("PUT"); announce("Path saved to My Desktop."); renderView(); }
        catch (_) { status.textContent = "Could not save the path. Please try again."; }
      });
      box.append(save, el("p", { class: "purpose",
        text: "Saves your five answers (kind of work, how much, best-fit resource, research example, parallel code) to your account. Nothing else. You can remove it any time." }));
    }
    box.append(status);
    return box;
  };

  const drawPath = (path, { fromL2C = false } = {}) => {
    const view = $("view");
    const l2c = path.l2cUrl || L2C_DEFAULT;
    const general = !!path.general;
    const saved = state.myPath && state.myPath.saved;
    const links = el("p", { class: "path-links" });
    const addLink = (a) => { if (links.childNodes.length) links.append(" · "); links.append(a); };
    if (general && saved) addLink(el("a", { href: "#/path", text: "Your saved path" }));
    if (!general) addLink(el("a", { href: "#/path?view=all", text: "See the whole path without your answers" }));
    addLink(el("a", { href: l2c, target: "_blank", rel: "noopener",
      text: general ? "Find where you are: From Laptop to Cluster (5 minutes)" : "Change your answers in From Laptop to Cluster" }));
    const head = el("section", { class: "shelf path-head", "aria-labelledby": "h-path" },
      el("h2", { id: "h-path", text: general ? "Learning path: from laptop to cluster" : "Your learning path" }),
      el("p", { class: "purpose", text: general
        ? "Four stages, in order. Start at the first one that is new to you, or answer a few questions in From Laptop to Cluster to get courses picked for your research."
        : fromL2C
          ? "From your answers in From Laptop to Cluster: what to learn next, then the rest of the journey."
          : "What to learn next, then the rest of the journey from laptop to cluster." }),
      general ? null : el("p", { class: "path-answers" },
        ...Object.entries(path.labels).map(([k, v]) => el("span", { class: "badge", text: v, title: k }))),
      general ? null : savePathControls(path.answers),
      links);
    view.replaceChildren(el("p", { class: "crumb" }, el("a", { href: "#/", text: "← Home" })), head);
    if (general) {
      view.append(stagesList(path, new Set()));
      return;
    }
    if (!path.here) {
      view.append(el("p", { class: "empty", text: path.note || "No course path for these answers yet." }));
      return;
    }
    const focus = path.focus.map((id) => state.tiles.get(id)).filter(Boolean);
    if (focus.length) view.append(section("path-next", "Do these next", "Chosen for your kind of work and best-fit resource.",
      tileList(focus, { label: "Do these next" })));
    const fw = pathWorkshops(path.focusWorkshops);
    if (fw) view.append(section("path-live", "Live workshops for you", "Upcoming workshops on the same topics.", fw));
    view.append(el("h2", { class: "path-journey", text: "The whole journey" }),
      stagesList(path, new Set((path.focusWorkshops || []).map((w) => w.id))));
  };

  const stagesList = (path, shownIds) => {
    const ol = el("ol", { class: "path-stages", "aria-label": "Stages from laptop to cluster" });
    path.stages.forEach((st, i) => {
      const tiles = st.tileIds.map((id) => state.tiles.get(id)).filter(Boolean);
      const steps = st.l2cSteps.length ? `From Laptop to Cluster step${st.l2cSteps.length > 1 ? "s" : ""} ${st.l2cSteps.join(", ")}. ` : "";
      const hid = `h-stage-${st.id}`;
      ol.append(el("li", { class: `path-stage ${st.position}` },
        el("section", { class: "shelf", "aria-labelledby": hid },
          el("div", { class: "shelf-head" }, el("div", {},
            el("h3", { id: hid }, el("span", { class: "stage-no", text: `${i + 1}` }), ` ${st.title} `,
              st.position === "here" ? el("span", { class: "badge start", text: "You are here" }) : null),
            el("p", { class: "purpose", text: steps + st.summary }))),
          tiles.length ? tileList(tiles, { label: st.title }) : el("p", { class: "empty", text: "No courses on this stage yet." }),
          pathWorkshops(st.workshops.filter((w) => !shownIds.has(w.id))))));
    });
    return ol;
  };

  const renderPath = async (params) => {
    const view = $("view");
    if (!window.NCSA.config.signIn) {
      view.replaceChildren(el("p", { class: "empty", text: "Learning paths are available on trainingdesktop.web.illinois.edu." }));
      return;
    }
    const answers = Object.fromEntries(PATH_KEYS.filter((k) => params.get(k)).map((k) => [k, params.get(k)]));
    const fromL2C = params.get("from") === "l2c";
    if (answers.work) {
      view.replaceChildren(el("p", { class: "empty", text: "Loading your path…" }));
      try {
        const q = new URLSearchParams(answers).toString();
        const res = await fetch(`/api/v1/paths?${q}`, { headers: { Accept: "application/json" } });
        if (!res.ok) throw new Error(String(res.status));
        const path = await res.json();
        drawPath(path, { fromL2C });
        usage("path_view", fromL2C ? "l2c" : "", `${path.answers.work}:${path.answers.scale}:${path.answers.fit}`);
      } catch (_) {
        view.replaceChildren(el("p", { class: "empty", text: "That path link is not valid. Try From Laptop to Cluster again." }));
      }
      return;
    }
    if (params.get("view") !== "all" && state.myPath && state.myPath.saved) {
      drawPath(state.myPath.path);
      const a = state.myPath.answers;
      usage("path_view", "saved", `${a.work}:${a.scale}:${a.fit}`);
      return;
    }
    if (state.journey) {
      drawPath(state.journey);
      usage("path_view", "general");
      return;
    }
    view.replaceChildren(el("p", { class: "crumb" }, el("a", { href: "#/", text: "← Home" })),
      section("path-empty", "Learning path", "Answer a few questions in From Laptop to Cluster to get a path of courses.",
        el("p", {}, el("a", { class: "primary small", href: L2C_DEFAULT, target: "_blank", rel: "noopener",
          text: "Open From Laptop to Cluster" }))));
  };

  // ---------- tiles ----------
  const tileCard = (tile, { startHere = false } = {}) => {
    const btn = el("button", { class: "tile", type: "button", "data-tile": tile.id, tabindex: "-1", "aria-haspopup": "dialog" });
    const badges = el("span", { class: "tile-badges" },
      el("span", { class: "badge", text: TYPE_LABEL[tile.type] || tile.type }),
      tile.guestAccess ? el("span", { class: "badge open", text: "Open to all" }) : null,
      startHere ? el("span", { class: "badge start", text: "Start here" }) : null,
      progressOf(tile) ? el("span", { class: "badge mine", text: progressText(progressOf(tile)) }) : null,
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
    const yp = yourPathSection();
    if (yp) view.append(yp);
    const cont = continueSection();
    if (cont) view.append(cont);
    const ws = workshopsSection();
    if (ws) view.append(ws);
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
    clearTimeout(state.searchLog);
    state.searchLog = setTimeout(() => {
      if (state.query) usage("search", "", state.query, hits.length);
    }, 1500);
  };

  const renderView = () => {
    if (state.query || state.filters.size) return renderResults();
    const r = parseRoute(state.baseHash);
    if (r.shelf) renderShelf(r.shelf);
    else if (r.workshops) renderWorkshops();
    else if (r.path) renderPath(r.path);
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
    const mine = progressOf(tile);
    if (mine) bits.push(`You're enrolled: ${progressText(mine).toLowerCase()}`);
    else if (linked() && tile.type !== "tool") bits.push(tile.guestAccess ? "Open to everyone" : "Start enrolls you if the course is open to everyone");
    else bits.push(tile.guestAccess ? "Open to everyone, no sign-in needed" : "Sign in to Learn@NCSA to enroll");
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
    const onPath = stageOfTile(tile.id);
    if (onPath) {
      if (tracks.childNodes.length) tracks.append(el("br"));
      tracks.append("Learning path: ", el("a", { href: "#/path?view=all",
        text: `stage ${onPath.no}, ${onPath.stage.title}` }));
    }
    const launch = $("sheet-launch");
    launch.href = tile.launch.url;
    launch.textContent = `${tile.launch.label} on Learn@NCSA`;
    if (mine) launch.textContent = mine.completed ? "Review on Learn@NCSA" : "Continue on Learn@NCSA";
    else if (linked() && tile.type !== "tool") launch.textContent = "Start on Learn@NCSA";
    if (tile.type === "tool") launch.textContent = tile.launch.label;
    updatePinButton();
  };

  // Enroll-on-launch: for a signed-in, linked learner who is not yet enrolled, ask the harness to enroll,
  // then open the course either way (Learn@NCSA handles anything the harness declines).
  $("sheet-launch").addEventListener("click", async (e) => {
    const tile = state.tiles.get(state.currentTile);
    if (tile) usage("launch", tile.id, progressOf(tile) ? "continue" : linked() ? "start" : "open");
    if (!tile || tile.type === "tool" || !linked() || progressOf(tile)) return;
    const courseId = courseIdOf(tile.launch.url);
    if (!courseId) return;
    e.preventDefault();
    const win = window.open("about:blank", "_blank");
    if (win) win.opener = null;
    let url = tile.launch.url;
    let enrolled = false;
    try {
      const res = await fetch("/api/v1/enrollments", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ courseId }) });
      const body = await res.json();
      if (body.url) url = body.url;
      enrolled = !!body.enrolled;
    } catch (_) { /* open the course anyway */ }
    if (win) win.location.href = url;
    else location.href = url;
    if (enrolled) {
      announce("You're enrolled. The course opened in a new tab.");
      await loadProgress();
      fillSheet(tile);
      renderView();
    }
  });

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
    usage("tile_open", tile.id);
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
    if (hash === "#/workshops") return { workshops: true };
    const pm = /^#\/path(?:\?(.*))?$/.exec(hash || "");
    if (pm) return { path: new URLSearchParams(pm[1] || "") };
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
    if (state.filters.has(f)) usage("filter", f);
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

  // ---------- account (only when served by the harness, which sets config.signIn) ----------
  const renderAccount = async () => {
    const box = $("account");
    if (!box || !window.NCSA.config.signIn) return;
    try {
      const me = await api("/me");
      if (me.signedIn) {
        state.me = me;
        const form = el("form", { method: "post", action: "/auth/logout", class: "account-form" },
          el("span", { class: "account-name", text: me.user.name || me.user.username || "Signed in" }),
          el("button", { type: "submit", class: "chip", text: "Sign out" }));
        box.replaceChildren(form);
      } else {
        const next = "/" + (location.hash || "");
        box.replaceChildren(el("a", { class: "chip signin", href: `/auth/login?next=${encodeURIComponent(next)}`,
          text: "Sign in" }));
      }
      box.hidden = false;
    } catch (_) { /* signed-out view still works */ }
  };

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
      await renderAccount();
      await loadProgress();
      await loadMyPath();
      if (window.NCSA.config.signIn) {
        try { state.workshops = (await api("/events?when=all")).items || []; } catch (_) { state.workshops = []; }
        const nav = $("nav-workshops"); // absent if the browser still has an older index.html cached
        if (nav) nav.hidden = !state.workshops.length;
        try { state.journey = await api("/paths"); } catch (_) { state.journey = null; }
        const navPath = $("nav-path");
        if (navPath) navPath.hidden = !state.journey;
        if (state.journey) {
          $("tracks-list").prepend(el("li", { class: "tracks-path" },
            el("a", { href: "#/path?view=all", text: "Learning path: laptop to cluster" })));
        }
        usage("visit", ((location.hash || "#/").split("/")[1] || "home").split("?")[0]);
      }
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
