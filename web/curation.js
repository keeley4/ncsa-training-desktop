// Curator view: what the last catalog build found. Reads the same public API as the front door.
"use strict";

(async () => {
  const { loadConfig, api, el, TYPE_LABEL, footer } = window.NCSA;
  const $ = (id) => document.getElementById(id);
  try {
    const config = await loadConfig();
    const [meta, tiles, shelves] = await Promise.all([api("/catalog/meta"), api("/tiles"), api("/shelves")]);
    const byId = new Map(tiles.items.map((t) => [t.id, t]));
    const site = meta.source.siteUrl.replace(/\/$/, "");

    $("stats").replaceChildren(...[
      ["Public courses", meta.counts.courses], ["Tiles", meta.counts.tiles], ["Tracks", meta.counts.shelves],
      ["No description", meta.health.noSummary.length], ["No image", meta.health.noImage.length],
      ["On no track", meta.health.unshelved.length],
    ].map(([label, n]) => el("li", {}, el("strong", { text: String(n) }), label)));

    if (config.editShelvesUrl) {
      $("edit").replaceChildren("Arrange tracks by editing ",
        el("a", { href: config.editShelvesUrl, text: "catalog/shelves.yaml" }),
        " (opens a pull request; the site rebuilds after merge).");
    }

    const editLinks = (t) => {
      const cell = el("td");
      (t.source.ids || []).forEach((id, i) => {
        if (i) cell.append(" · ");
        cell.append(el("a", { href: `${site}/course/edit.php?id=${id}`, text: `Edit course ${id}` }));
      });
      return cell;
    };
    const table = (title, ids, note) => {
      const rows = ids.map((id) => byId.get(id)).filter(Boolean);
      const sec = el("section", {}, el("h2", { text: `${title} (${rows.length})` }), note ? el("p", { class: "meta", text: note }) : null);
      if (!rows.length) {
        sec.append(el("p", { text: "None." }));
        return sec;
      }
      const tbody = el("tbody");
      for (const t of rows) {
        tbody.append(el("tr", {},
          el("td", {}, el("a", { href: `./#/tile/${t.id}`, text: t.title })),
          el("td", { text: TYPE_LABEL[t.type] || t.type }),
          el("td", { text: (t.shelfIds || []).map((s) => (shelves.items.find((x) => x.id === s) || {}).title || s).join(", ") || "—" }),
          editLinks(t)));
      }
      sec.append(el("table", {},
        el("thead", {}, el("tr", {}, ...["Tile", "Type", "Tracks", "In Learn@NCSA"].map((h) => el("th", { scope: "col", text: h })))),
        tbody));
      return sec;
    };

    const warnings = el("section", {}, el("h2", { text: `Build warnings (${meta.warnings.length})` }),
      meta.warnings.length ? el("ul", {}, ...meta.warnings.map((w) => el("li", { text: w }))) : el("p", { text: "None." }));

    $("sections").replaceChildren(
      warnings,
      table("On no track", meta.health.unshelved, "Public in Learn@NCSA but not placed on any shelf."),
      table("No description", meta.health.noSummary, "Add a course summary in Learn@NCSA (Course settings → Description)."),
      table("No course image", meta.health.noImage, "Add a course image in Learn@NCSA (Course settings → Course image). Tiles without one show the track colour."));
    $("status").textContent = "";
    $("footer-meta").textContent = footer(meta);
  } catch (err) {
    $("status").textContent = "The catalog could not be loaded.";
    console.error(err);
  }
})();
