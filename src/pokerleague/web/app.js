/* Hold'em League site: vanilla JS, no build step.
 * Data comes from data/league.json, data/season-<year>.json and data/t/<date>.json.
 * All names are inserted with textContent (never innerHTML).
 */
(() => {
  "use strict";

  const app = document.getElementById("app");
  const tooltip = document.getElementById("tooltip");
  const seasonSelect = document.getElementById("season");
  const cache = new Map();
  const state = { league: null, season: null, highlight: [] };
  const SLOTS = ["--s1", "--s2", "--s3"];

  // ---------- helpers ----------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style") el.setAttribute("style", v);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const SVGNS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, v);
    for (const c of children.flat(Infinity)) if (c !== null && c !== undefined) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return el;
  }
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const money = (v) => (v === null || v === undefined ? "–" : "$" + (Number.isInteger(v) ? v : v.toFixed(2)));
  const pct = (a, b) => (b ? Math.round((100 * a) / b) + "%" : "–");
  const ordinal = (n) => {
    const suffix = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (suffix[(v - 20) % 10] || suffix[v] || suffix[0]);
  };
  function fmtDate(iso, opts) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString(undefined, opts || { month: "short", day: "numeric" });
  }
  const nameOf = (id) => (state.league.players[id] || (id && id.startsWith("?") ? "Unknown player" : id) || "–");
  const playerLink = (id) => (id ? h("a", { href: `#/p/${encodeURIComponent(id)}` }, nameOf(id)) : "–");

  function cardsEl(cards) {
    return h("span", { class: "cards" }, (cards || []).map((c, i) => [
      i ? " " : "",
      h("span", { class: /[♥♦]/.test(c) ? "card-red" : null }, c),
    ]));
  }

  async function load(path) {
    if (!cache.has(path)) {
      cache.set(path, fetch(path, { cache: "no-cache" }).then((r) => {
        if (!r.ok) throw new Error(`${path}: ${r.status}`);
        return r.json();
      }));
    }
    return cache.get(path);
  }
  const seasonData = () => load(`data/season-${state.season}.json`);

  function tile(label, value, sub) {
    return h("div", { class: "tile" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), sub ? h("div", { class: "sub" }, sub) : null);
  }

  // ---------- tooltip ----------
  function showTooltip(x, y, title, rows) {
    tooltip.replaceChildren(
      h("div", { class: "tt-title" }, title),
      ...rows.map((r) => h("div", { class: "tt-row" },
        r.color ? h("span", { class: r.dot ? "dot-key" : "line-key", style: `background:${r.color}` }) : null,
        h("strong", {}, r.value),
        h("span", { class: "name" }, r.name))),
    );
    tooltip.hidden = false;
    const pad = 14;
    const rect = tooltip.getBoundingClientRect();
    let left = x + pad, top = y + pad;
    if (left + rect.width > window.innerWidth - 8) left = x - rect.width - pad;
    if (top + rect.height > window.innerHeight - 8) top = y - rect.height - pad;
    tooltip.style.left = Math.max(8, left) + "px";
    tooltip.style.top = Math.max(8, top) + "px";
  }
  const hideTooltip = () => { tooltip.hidden = true; };

  // ---------- table ----------
  function sortableTable(columns, rows, { initial, rowAttrs } = {}) {
    let sortKey = initial ? initial.key : null;
    let dir = initial ? initial.dir : "descending";
    const tbody = h("tbody");
    const heads = columns.map((c) => {
      const th = h("th", { class: c.num ? "num" : null, scope: "col" },
        c.sortValue ? h("button", { class: "sort", type: "button", onclick: () => sortBy(c.key) }, c.label) : c.label);
      th.dataset.key = c.key;
      return th;
    });
    function render() {
      const col = columns.find((c) => c.key === sortKey);
      const sorted = col ? [...rows].sort((a, b) => {
        const va = col.sortValue(a), vb = col.sortValue(b);
        const cmp = typeof va === "string" ? va.localeCompare(vb) : va - vb;
        return dir === "ascending" ? cmp : -cmp;
      }) : rows;
      heads.forEach((th) => (th.dataset.key === sortKey ? th.setAttribute("aria-sort", dir) : th.removeAttribute("aria-sort")));
      tbody.replaceChildren(...sorted.map((r) => h("tr", rowAttrs ? rowAttrs(r) : {},
        columns.map((c) => h("td", { class: [c.num ? "num" : "", c.cls || ""].join(" ").trim() || null }, c.render(r))))));
    }
    function sortBy(key) {
      if (sortKey === key) dir = dir === "descending" ? "ascending" : "descending";
      else { sortKey = key; dir = columns.find((c) => c.key === key).defaultDir || "descending"; }
      render();
    }
    render();
    return h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, heads)), tbody));
  }

  // ---------- line / dot chart ----------
  // series: [{id, name, values:[number|null], color: css var name or null (muted)}]
  function lineChart(opts) {
    const { x, xLabel, series, yLabelFormat = (v) => v, invert = false, height = 300, markers = false,
      lines = true, tooltipTitle, tooltipValue = (v) => v, maxTooltipRows = 8, ariaLabel, yMin: yMinOpt, yMax: yMaxOpt, step = false } = opts;
    const wrap = h("div", { class: "chart" });
    const legendSeries = series.filter((sr) => sr.color).sort((a, b) => SLOTS.indexOf(a.color) - SLOTS.indexOf(b.color));
    if (legendSeries.length > 1 || opts.legendOthers) {
      wrap.append(h("div", { class: "legend" },
        legendSeries.map((sr) => h("span", { class: "key" }, h("span", { class: markers && !lines ? "dot-key" : "line-key", style: `background:var(${sr.color})` }), sr.name)),
        opts.legendOthers ? h("span", { class: "key" }, h("span", { class: "line-key", style: "background:var(--other-line)" }), opts.legendOthers) : null));
    }
    const holder = h("div");
    wrap.append(holder);

    function draw() {
      const width = Math.max(300, holder.clientWidth || 600);
      const m = { top: 12, right: lines && legendSeries.length ? 84 : 16, bottom: 28, left: 40 };
      const iw = width - m.left - m.right, ih = height - m.top - m.bottom;
      const all = series.flatMap((sr) => sr.values).filter((v) => v !== null && v !== undefined);
      let yMin = yMinOpt ?? Math.min(0, ...all), yMax = yMaxOpt ?? Math.max(1, ...all);
      const ticks = niceTicks(yMin, yMax, 5);
      yMin = Math.min(yMin, ticks[0]); yMax = Math.max(yMax, ticks[ticks.length - 1]);
      const xMin = x[0], xMax = x[x.length - 1] === x[0] ? x[0] + 1 : x[x.length - 1];
      const sx = (v) => m.left + ((v - xMin) / (xMax - xMin)) * iw;
      const sy = (v) => invert ? m.top + ((v - yMin) / (yMax - yMin)) * ih : m.top + ih - ((v - yMin) / (yMax - yMin)) * ih;

      const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": ariaLabel || "" });
      const grid = s("g", { class: "grid" });
      for (const t of ticks) {
        grid.append(s("line", { x1: m.left, x2: m.left + iw, y1: sy(t), y2: sy(t) }));
        svg.append(s("text", { x: m.left - 8, y: sy(t) + 4, "text-anchor": "end" }, yLabelFormat(t)));
      }
      svg.prepend(grid);
      svg.append(s("line", { class: "baseline", x1: m.left, x2: m.left + iw, y1: m.top + ih, y2: m.top + ih }));
      const xTickCount = Math.min(x.length, Math.max(2, Math.floor(iw / 90)));
      if (opts.continuousX) {
        for (const t of niceTicks(xMin, xMax, xTickCount).filter((v) => v >= xMin && v <= xMax)) {
          svg.append(s("text", { x: sx(t), y: height - 8, "text-anchor": t === xMin ? "start" : "middle" }, xLabel(t)));
        }
      } else for (let i = 0; i < xTickCount; i++) {
        const idx = Math.round((i * (x.length - 1)) / Math.max(1, xTickCount - 1));
        svg.append(s("text", { x: sx(x[idx]), y: height - 8, "text-anchor": i === 0 ? "start" : i === xTickCount - 1 ? "end" : "middle" }, xLabel(x[idx], idx)));
      }

      const ordered = [...series.filter((sr) => !sr.color), ...series.filter((sr) => sr.color)];
      for (const sr of ordered) {
        const color = sr.color ? `var(${sr.color})` : "var(--other-line)";
        if (lines) {
          let d = "", prev = null;
          sr.values.forEach((v, i) => {
            if (v === null || v === undefined) { prev = null; return; }
            const px = sx(x[i]), py = sy(v);
            if (prev === null) d += `M${px},${py}`;
            else d += step ? `H${px}V${py}` : `L${px},${py}`;
            prev = v;
          });
          svg.append(s("path", { d, fill: "none", stroke: color, "stroke-width": sr.color ? 2 : 1.25, "stroke-linejoin": "round", "stroke-linecap": "round" }));
        }
        if (markers && sr.color) {
          sr.values.forEach((v, i) => {
            if (v === null || v === undefined) return;
            svg.append(s("circle", { cx: sx(x[i]), cy: sy(v), r: 4.5, fill: color, stroke: "var(--surface)", "stroke-width": 2 }));
          });
        }
      }
      // End labels for highlighted series; skipped where they would collide (the legend still names them).
      if (lines && legendSeries.length) {
        const ends = legendSeries.map((sr) => {
          let i = sr.values.length - 1;
          while (i >= 0 && (sr.values[i] === null || sr.values[i] === undefined)) i--;
          return i >= 0 ? { sr, x: sx(x[i]), y: sy(sr.values[i]) } : null;
        }).filter(Boolean).sort((a, b) => a.y - b.y);
        ends.forEach((e, i) => {
          const clash = ends.some((o, j) => j !== i && Math.abs(o.y - e.y) < 14);
          svg.append(s("circle", { cx: e.x, cy: e.y, r: 4.5, fill: `var(${e.sr.color})`, stroke: "var(--surface)", "stroke-width": 2 }));
          if (!clash) svg.append(s("text", { class: "end-label", x: e.x + 8, y: e.y + 4 }, e.sr.name));
        });
      }

      // Hover layer: crosshair snaps to the nearest x; one tooltip lists the series.
      const cross = s("line", { class: "crosshair", y1: m.top, y2: m.top + ih, visibility: "hidden" });
      const hoverDots = s("g");
      svg.append(cross, hoverDots);
      const overlay = s("rect", { class: "overlay", x: m.left, y: m.top, width: iw, height: ih, tabindex: 0, "aria-label": `${ariaLabel || "Chart"}: use arrow keys to step through points` });
      svg.append(overlay);
      let current = -1;
      function show(i, clientX, clientY) {
        current = Math.max(0, Math.min(x.length - 1, i));
        const px = sx(x[current]);
        cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
        hoverDots.replaceChildren(...legendSeries.filter((sr) => sr.values[current] !== null && sr.values[current] !== undefined)
          .map((sr) => s("circle", { cx: px, cy: sy(sr.values[current]), r: 4.5, fill: `var(${sr.color})`, stroke: "var(--surface)", "stroke-width": 2 })));
        const rows = series.filter((sr) => sr.values[current] !== null && sr.values[current] !== undefined)
          .map((sr) => ({ sr, v: sr.values[current] }))
          .sort((a, b) => (invert ? a.v - b.v : b.v - a.v));
        const shown = [...rows.filter((r) => r.sr.color), ...rows.filter((r) => !r.sr.color)].slice(0, maxTooltipRows)
          .sort((a, b) => (invert ? a.v - b.v : b.v - a.v));
        if (clientX === undefined) {
          const box = svg.getBoundingClientRect();
          clientX = box.left + (px / width) * box.width; clientY = box.top + box.height / 3;
        }
        showTooltip(clientX, clientY, tooltipTitle(x[current], current),
          shown.map((r) => ({ value: tooltipValue(r.v), name: r.sr.name, color: r.sr.color ? `var(${r.sr.color})` : "var(--other-line)", dot: markers && !lines })));
      }
      function nearest(clientX) {
        const box = svg.getBoundingClientRect();
        const vx = ((clientX - box.left) / box.width) * width;
        let best = 0, bestD = Infinity;
        x.forEach((xv, i) => { const d = Math.abs(sx(xv) - vx); if (d < bestD) { bestD = d; best = i; } });
        return best;
      }
      overlay.addEventListener("pointermove", (e) => show(nearest(e.clientX), e.clientX, e.clientY));
      overlay.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hoverDots.replaceChildren(); hideTooltip(); });
      overlay.addEventListener("focus", () => show(current < 0 ? x.length - 1 : current));
      overlay.addEventListener("blur", () => { cross.setAttribute("visibility", "hidden"); hoverDots.replaceChildren(); hideTooltip(); });
      overlay.addEventListener("keydown", (e) => {
        if (e.key === "ArrowLeft") { show(current - 1); e.preventDefault(); }
        if (e.key === "ArrowRight") { show(current + 1); e.preventDefault(); }
      });
      holder.replaceChildren(svg);
    }
    requestAnimationFrame(draw);
    new ResizeObserver(() => draw()).observe(holder);
    return wrap;
  }

  function niceTicks(min, max, count) {
    if (min === max) max = min + 1;
    const span = max - min;
    const step0 = Math.pow(10, Math.floor(Math.log10(span / count)));
    const step = [1, 2, 2.5, 5, 10].map((f) => f * step0).find((st) => span / st <= count) || step0 * 10;
    const out = [];
    for (let v = Math.floor(min / step) * step; v <= max + 1e-9; v += step) out.push(+v.toFixed(6));
    if (out[out.length - 1] < max) out.push(+(out[out.length - 1] + step).toFixed(6));
    return out;
  }

  // ---------- heatmap ----------
  function heatmap(matrix, ariaLabel) {
    const hunters = Object.keys(matrix);
    const victimsSet = new Set(hunters.flatMap((hn) => Object.keys(matrix[hn])));
    const total = (obj) => Object.values(obj).reduce((a, b) => a + b, 0);
    hunters.sort((a, b) => total(matrix[b]) - total(matrix[a]) || nameOf(a).localeCompare(nameOf(b)));
    const victimTotals = {};
    hunters.forEach((hn) => Object.entries(matrix[hn]).forEach(([v, n]) => (victimTotals[v] = (victimTotals[v] || 0) + n)));
    const victims = [...victimsSet].sort((a, b) => victimTotals[b] - victimTotals[a] || nameOf(a).localeCompare(nameOf(b)));
    // Anchor the scale at 4+ so a lone knockout reads as light, not maximal.
    const max = Math.max(4, ...hunters.flatMap((hn) => Object.values(matrix[hn])));
    const ramp = css("--heat").split(",").map((c) => c.trim());
    const dark = document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
    const steps = dark ? [...ramp].reverse().slice(1) : ramp.slice(1);
    const grid = h("div", { class: "heatmap", role: "table", "aria-label": ariaLabel,
      style: `grid-template-columns: max-content repeat(${victims.length}, minmax(28px, 1fr))` });
    grid.append(h("div", { role: "columnheader" }, h("span", { class: "muted" }, "Hunter ↓ / Victim →")));
    victims.forEach((v) => grid.append(h("div", { class: "hm-col-label", role: "columnheader" }, nameOf(v))));
    hunters.forEach((hn) => {
      grid.append(h("div", { class: "hm-row-label", role: "rowheader" }, h("a", { href: `#/p/${encodeURIComponent(hn)}` }, nameOf(hn))));
      victims.forEach((v) => {
        const n = matrix[hn][v] || 0;
        if (!n) { grid.append(h("div", { class: "hm-cell empty", role: "cell", "aria-label": `${nameOf(hn)} knocked out ${nameOf(v)} 0 times` })); return; }
        const idx = Math.min(steps.length - 1, Math.floor(((n - 0.0001) / max) * steps.length));
        const fill = steps[idx];
        const textDark = luminance(fill) > 0.45;
        const label = `${nameOf(hn)} knocked out ${nameOf(v)} ${n} time${n === 1 ? "" : "s"}`;
        const cell = h("div", { class: "hm-cell", role: "cell", tabindex: 0, "aria-label": label,
          style: `background:${fill};color:${textDark ? "#0b0b0b" : "#ffffff"}` }, Number.isInteger(n) ? n : n.toFixed(1));
        const tip = (e) => {
          const r = cell.getBoundingClientRect();
          showTooltip(e && e.clientX ? e.clientX : r.right, e && e.clientY ? e.clientY : r.bottom, `${nameOf(hn)} → ${nameOf(v)}`, [{ value: String(n), name: n === 1 ? "knockout" : "knockouts" }]);
        };
        cell.addEventListener("pointermove", tip);
        cell.addEventListener("focus", () => tip());
        cell.addEventListener("pointerleave", hideTooltip);
        cell.addEventListener("blur", hideTooltip);
        grid.append(cell);
      });
    });
    return h("div", { class: "table-wrap" }, grid);
  }
  function luminance(hex) {
    const n = parseInt(hex.replace("#", ""), 16);
    const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  }

  // ---------- views ----------
  async function viewStandings() {
    const data = await seasonData();
    const rows = data.standings;
    if (!state.highlight.length) state.highlight = rows.slice(0, 3).map((r) => r.player);
    const leader = rows[0];
    const nb = data.next_bounty;
    const tiles = h("div", { class: "tiles" },
      tile("Next week's bounty", money(nb.worth), nb.target ? `on ${nameOf(nb.target)}, last week's winner` : null),
      tile("Points leader", leader ? nameOf(leader.player) : "–", leader ? `${leader.points} points` : null),
      tile("Tournaments", String(data.tournaments.length), `latest ${fmtDate(data.tournaments[data.tournaments.length - 1].date)}`),
      tile("Players this season", String(rows.length)));

    const raceCard = h("section", { class: "card" }, h("h2", {}, "Points race"));
    const tableCard = h("section", { class: "card" }, h("h2", {}, "Standings"),
      h("p", { class: "secondary" }, "4 points for a win, 3 for second, 2 for third, 1 for fourth (fourth pays with 11+ players). Use ● to compare players in the chart."));

    function drawRace() {
      const race = data.points_race;
      const x = race.dates.map((_, i) => i);
      const series = Object.entries(race.series).map(([pid, values]) => {
        const slot = state.highlight.indexOf(pid);
        return { id: pid, name: nameOf(pid), values, color: slot >= 0 ? SLOTS[slot] : null };
      });
      raceCard.replaceChildren(h("h2", {}, "Points race"), lineChart({
        x, series, height: 320, step: true, legendOthers: "Everyone else",
        xLabel: (_, i) => fmtDate(race.dates[i]),
        tooltipTitle: (_, i) => `After ${fmtDate(race.dates[i], { month: "short", day: "numeric", year: "numeric" })}`,
        tooltipValue: (v) => `${v} pts`,
        ariaLabel: "Cumulative points by player over the season",
      }), h("p", { class: "caption" }, "Cumulative points after each tournament. The standings table below has every value."));
    }
    function toggle(pid) {
      const i = state.highlight.indexOf(pid);
      if (i >= 0) state.highlight.splice(i, 1);
      else { if (state.highlight.length >= 3) state.highlight.shift(); state.highlight.push(pid); }
      drawRace();
      tableCard.querySelectorAll("button.hl").forEach((b) => {
        const slot = state.highlight.indexOf(b.dataset.pid);
        b.setAttribute("aria-pressed", slot >= 0 ? "true" : "false");
        b.style.setProperty("--hl-color", slot >= 0 ? `var(${SLOTS[slot]})` : "transparent");
      });
    }
    const hlButton = (pid) => {
      const slot = state.highlight.indexOf(pid);
      const b = h("button", { class: "hl", type: "button", "aria-pressed": slot >= 0 ? "true" : "false", "aria-label": `Highlight ${nameOf(pid)} in the points race`, onclick: () => toggle(pid) });
      b.dataset.pid = pid;
      b.style.setProperty("--hl-color", slot >= 0 ? `var(${SLOTS[slot]})` : "transparent");
      return b;
    };
    tableCard.append(sortableTable([
      { key: "rank", label: "#", num: true, render: (r) => r.rank, sortValue: (r) => r.rank, defaultDir: "ascending" },
      { key: "player", label: "Player", cls: "player", render: (r) => [hlButton(r.player), playerLink(r.player)], sortValue: (r) => nameOf(r.player), defaultDir: "ascending" },
      { key: "points", label: "Points", num: true, render: (r) => r.points, sortValue: (r) => r.points },
      { key: "tourneys", label: "Played", num: true, render: (r) => r.tourneys, sortValue: (r) => r.tourneys },
      { key: "ppt", label: "Pts/game", num: true, render: (r) => r.points_per_tourney.toFixed(2), sortValue: (r) => r.points_per_tourney },
      { key: "wins", label: "1st", num: true, render: (r) => r.wins, sortValue: (r) => r.wins },
      { key: "seconds", label: "2nd", num: true, render: (r) => r.seconds, sortValue: (r) => r.seconds },
      { key: "thirds", label: "3rd", num: true, render: (r) => r.thirds, sortValue: (r) => r.thirds },
      { key: "fourths", label: "4th", num: true, render: (r) => r.fourths, sortValue: (r) => r.fourths },
      { key: "itm", label: "In money", num: true, render: (r) => pct(r.in_money, r.tourneys), sortValue: (r) => r.in_money / Math.max(1, r.tourneys) },
      { key: "bubbles", label: "Bubbles", num: true, render: (r) => r.bubbles, sortValue: (r) => r.bubbles },
      { key: "first_outs", label: "First out", num: true, render: (r) => r.first_outs, sortValue: (r) => r.first_outs },
      { key: "bounties", label: "Bounties", num: true, render: (r) => r.bounties, sortValue: (r) => r.bounties },
    ], rows, { initial: { key: "rank", dir: "ascending" } }));

    drawRace();
    const recent = data.tournaments.slice(-5).reverse();
    app.replaceChildren(
      h("div", { class: "page-head" }, h("h1", {}, `${state.season} season`)),
      tiles, raceCard, tableCard,
      h("section", { class: "card" }, h("h2", {}, "Recent tournaments"), tournamentList(recent)),
    );
  }

  function tournamentList(list) {
    return sortableTable([
      { key: "date", label: "Date", render: (t) => h("a", { href: `#/t/${t.date}` }, fmtDate(t.date, { weekday: "short", month: "short", day: "numeric" })), sortValue: (t) => t.date },
      { key: "players", label: "Players", num: true, render: (t) => t.players, sortValue: (t) => t.players },
      { key: "first", label: "Winner", cls: "player", render: (t) => playerLink(t.first) },
      { key: "second", label: "2nd", render: (t) => playerLink(t.second) },
      { key: "third", label: "3rd", render: (t) => playerLink(t.third) },
      { key: "bounty", label: "Bounty", render: (t) => (t.bounty_winners.length ? t.bounty_winners.map(nameOf).join(", ") : "–") },
      { key: "detail", label: "", render: (t) => (t.has_logs ? h("span", { class: "badge" }, "hand history") : "") },
    ], list, { initial: { key: "date", dir: "descending" } });
  }

  async function viewTournaments() {
    const data = await seasonData();
    app.replaceChildren(
      h("div", { class: "page-head" }, h("h1", {}, `${state.season} tournaments`)),
      h("section", { class: "card" }, tournamentList(data.tournaments)),
    );
  }

  async function viewTournament(date) {
    const t = await load(`data/t/${date}.json`);
    const winnings = t.winnings || {};
    const head = h("div", { class: "page-head" },
      h("h1", {}, fmtDate(date, { weekday: "long", month: "long", day: "numeric", year: "numeric" })),
      h("p", { class: "secondary" }, [
        `${t.players || t.results.length} players`,
        t.pool ? `${money(t.pool)} pool` : null,
        t.hands_played ? `${t.hands_played} hands` : null,
        t.duration_minutes ? `${Math.round(t.duration_minutes)} minutes` : null,
        t.host ? `hosted by ${t.host}` : null,
      ].filter(Boolean).join(" · ")));

    const p = t.placings || {};
    const b = t.bounty || {};
    const tiles = h("div", { class: "tiles" },
      tile("Winner", nameOf(p.first), winnings[p.first] ? money(winnings[p.first]) : null),
      tile("Bounty", b.winners && b.winners.length ? b.winners.map(nameOf).join(" & ") : "Rolled over",
        b.target ? `on ${nameOf(b.target)}${b.pot ? ` · ${money(b.pot)}` : ""}` : "no target this week"),
      tile("Bubble", nameOf(p.bubble)),
      tile("First out", nameOf(p.first_out)));

    const sections = [head, tiles];
    if (t.recap) sections.push(h("section", { class: "card" }, h("h2", {}, "Recap"), h("div", { class: "recap" }, t.recap)));

    const hasDetail = t.source === "logs";
    const resultRows = t.results.filter((r) => r.place || !hasDetail);
    sections.push(h("section", { class: "card" }, h("h2", {}, "Results"),
      hasDetail ? null : h("p", { class: "secondary" }, "Imported from the 2026 spreadsheet: only the top finishers, bubble and first out were recorded."),
      sortableTable([
        { key: "place", label: "Place", num: true, render: (r) => (r.place ? ordinal(r.place) : "–"), sortValue: (r) => r.place || 999, defaultDir: "ascending" },
        { key: "player", label: "Player", cls: "player", render: (r) => playerLink(r.player), sortValue: (r) => nameOf(r.player), defaultDir: "ascending" },
        { key: "won", label: "Won", num: true, render: (r) => (winnings[r.player] ? money(winnings[r.player]) : ""), sortValue: (r) => winnings[r.player] || 0 },
        ...(hasDetail ? [
          { key: "by", label: "Knocked out by", render: (r) => (r.busted_by && r.busted_by.length ? r.busted_by.map(nameOf).join(" & ") : "") },
          { key: "minute", label: "Out at", num: true, render: (r) => (r.busted_minute !== null && r.busted_minute !== undefined ? `${Math.round(r.busted_minute)} min` : ""), sortValue: (r) => r.busted_minute ?? 9999 },
        ] : []),
      ], resultRows, { initial: { key: "place", dir: "ascending" } })));

    if (hasDetail && t.chip_series) {
      const top = t.results.slice(0, 3).map((r) => r.player);
      const minutes = [...new Set(Object.values(t.chip_series).flatMap((pts) => pts.map((pt) => pt[0])))].sort((a, b) => a - b);
      const series = Object.entries(t.chip_series).map(([pid, pts]) => {
        let j = 0, last = null, out = false;
        const values = minutes.map((mn) => {
          while (j < pts.length && pts[j][0] <= mn) { last = pts[j][1]; j++; }
          if (out) return null;
          if (last === 0) { out = true; return 0; }
          return last;
        });
        const slot = top.indexOf(pid);
        return { id: pid, name: nameOf(pid), values, color: slot >= 0 ? SLOTS[slot] : null };
      });
      sections.push(h("section", { class: "card" }, h("h2", {}, "Chip stacks"),
        lineChart({ x: minutes, series, height: 300, step: true, continuousX: true, legendOthers: "Everyone else",
          xLabel: (v) => `${Math.round(v)} min`, tooltipTitle: (v) => `${Math.round(v)} minutes in`,
          tooltipValue: (v) => v.toFixed(0), ariaLabel: "Chip stacks over the tournament" }),
        h("p", { class: "caption" }, "Top three finishers highlighted. Stacks are sampled at the start of each hand.")));
    }

    if (hasDetail && t.allin_showdowns && t.allin_showdowns.length) {
      const list = [...t.allin_showdowns].sort((a, c) => (c.suckout - a.suckout) || (c.pot - a.pot));
      const item = (sd) => h("li", {},
          h("div", {}, sd.players.map((pl, i) => [i ? " vs " : "",
            h("strong", {}, nameOf(pl.pid)), " ", cardsEl(pl.cards), ` ${Math.round(pl.equity * 100)}%`, pl.won ? " ✓" : ""]),
            sd.suckout ? h("span", { class: "badge" }, "suckout") : null),
          h("div", { class: "muted" }, `All-in ${sd.street} · pot ${sd.pot.toFixed(0)} · board `, cardsEl(sd.board)));
      const shown = 6;
      sections.push(h("section", { class: "card" }, h("h2", {}, "All-ins"),
        h("p", { class: "secondary" }, "Each player's chance of winning at the moment the chips went in. Suckouts first."),
        h("ul", { class: "hand-list" }, list.slice(0, shown).map(item)),
        list.length > shown ? h("details", {}, h("summary", { class: "secondary" }, `Show ${list.length - shown} more`),
          h("ul", { class: "hand-list" }, list.slice(shown).map(item))) : null));
    }

    if (hasDetail && t.player_stats) {
      const rows = Object.entries(t.player_stats).map(([pid, st]) => ({ pid, ...st }));
      sections.push(h("section", { class: "card" }, h("h2", {}, "How everyone played"),
        h("p", { class: "secondary" }, "Played = share of hands where they put money in voluntarily before the flop. Raised = share where they raised before the flop."),
        sortableTable([
          { key: "player", label: "Player", cls: "player", render: (r) => playerLink(r.pid), sortValue: (r) => nameOf(r.pid), defaultDir: "ascending" },
          { key: "hands", label: "Hands", num: true, render: (r) => r.hands, sortValue: (r) => r.hands },
          { key: "vpip", label: "Played", num: true, render: (r) => pct(r.vpip, r.hands), sortValue: (r) => r.vpip / Math.max(1, r.hands) },
          { key: "pfr", label: "Raised", num: true, render: (r) => pct(r.pfr, r.hands), sortValue: (r) => r.pfr / Math.max(1, r.hands) },
          { key: "sd", label: "Showdowns won", num: true, render: (r) => `${r.showdown_wins}/${r.showdowns}`, sortValue: (r) => r.showdown_wins },
          { key: "allin", label: "All-ins survived", num: true, render: (r) => `${r.all_ins_survived}/${r.all_ins}`, sortValue: (r) => r.all_ins_survived },
          { key: "kos", label: "Knockouts", num: true, render: (r) => r.knockouts, sortValue: (r) => r.knockouts },
          { key: "best", label: "Best hand", render: (r) => (r.best_hand ? r.best_hand.name : "") },
        ], rows, { initial: { key: "kos", dir: "descending" } })));
    }
    app.replaceChildren(...sections);
  }

  async function viewPlayer(pid) {
    const data = await seasonData();
    const row = data.standings.find((r) => r.player === pid);
    const head = h("div", { class: "page-head" }, h("h1", {}, nameOf(pid)), h("p", { class: "secondary" }, `${state.season} season`));
    if (!row) { app.replaceChildren(head, h("p", {}, "No tournaments this season.")); return; }
    const finishes = row.finishes;
    const avg = finishes.length ? (finishes.reduce((a, f) => a + f.place, 0) / finishes.length).toFixed(1) : "–";
    const st = row.stats;
    const sections = [head, h("div", { class: "tiles" },
      tile("Rank", `#${row.rank}`, `${row.points} points`),
      tile("Wins", String(row.wins), `${row.in_money} times in the money`),
      tile("In the money", pct(row.in_money, row.tourneys), `${row.tourneys} played`),
      tile("Bounties", String(row.bounties), `${row.bubbles} bubbles · ${row.first_outs} first out`),
      tile("Average finish", avg, finishes.length ? `over ${finishes.length} recorded finishes` : "no recorded finishes"))];

    if (finishes.length) {
      const x = finishes.map((_, i) => i);
      const maxPlace = Math.max(...finishes.map((f) => f.of));
      sections.push(h("section", { class: "card" }, h("h2", {}, "Finishes"),
        lineChart({ x, series: [{ id: pid, name: nameOf(pid), values: finishes.map((f) => f.place), color: "--s1" }],
          invert: true, yMin: 1, yMax: maxPlace, markers: true, lines: false, height: 240,
          xLabel: (_, i) => fmtDate(finishes[i].date), yLabelFormat: (v) => (v === 0 ? "" : ordinal(v)),
          tooltipTitle: (_, i) => fmtDate(finishes[i].date, { weekday: "short", month: "short", day: "numeric" }),
          tooltipValue: (v) => ordinal(v), ariaLabel: `Finishing place by tournament for ${nameOf(pid)}` }),
        h("p", { class: "caption" }, "1st is at the top. Only recorded finishes are shown (2026 spreadsheet weeks recorded the top places, the bubble and first out)."),
        sortableTable([
          { key: "date", label: "Date", render: (f) => h("a", { href: `#/t/${f.date}` }, fmtDate(f.date)), sortValue: (f) => f.date },
          { key: "place", label: "Place", num: true, render: (f) => ordinal(f.place), sortValue: (f) => f.place, defaultDir: "ascending" },
          { key: "of", label: "Players", num: true, render: (f) => f.of, sortValue: (f) => f.of },
        ], finishes, { initial: { key: "date", dir: "descending" } })));
    }

    if (st && st.hands) {
      sections.push(h("section", { class: "card" }, h("h2", {}, "Playing style"),
        h("p", { class: "secondary" }, `From ${st.hands} hands in tournaments with full hand histories.`),
        h("div", { class: "tiles" },
          tile("Hands played", pct(st.vpip, st.hands), "put money in before the flop"),
          tile("Raised preflop", pct(st.pfr, st.hands)),
          tile("Showdowns won", pct(st.showdown_wins, st.showdowns), `${st.showdown_wins} of ${st.showdowns}`),
          tile("All-ins survived", pct(st.all_ins_survived, st.all_ins), `${st.all_ins_survived} of ${st.all_ins}`),
          tile("Knockouts", String(st.knockouts)))));
    }

    const ko = data.knockouts || {};
    const victims = Object.entries(ko[pid] || {}).sort((a, b) => b[1] - a[1]);
    const hunters = Object.entries(ko).map(([hn, vs]) => [hn, vs[pid] || 0]).filter(([, n]) => n).sort((a, b) => b[1] - a[1]);
    if (victims.length || hunters.length) {
      const list = (items) => (items.length ? h("ul", { class: "hand-list" }, items.map(([p2, n]) => h("li", {}, playerLink(p2), h("span", { class: "muted" }, ` · ${n}`)))) : h("p", { class: "muted" }, "None yet"));
      sections.push(h("div", { class: "grid-2" },
        h("section", { class: "card" }, h("h2", {}, "Knocked out"), list(victims)),
        h("section", { class: "card" }, h("h2", {}, "Knocked out by"), list(hunters))));
    }
    app.replaceChildren(...sections);
  }

  async function viewKnockouts() {
    const data = await seasonData();
    const matrix = data.knockouts || {};
    const head = h("div", { class: "page-head" }, h("h1", {}, "Who busts whom"),
      h("p", { class: "secondary" }, "Knockouts from tournaments with full hand histories. Rows are the player who took the chips; columns are the player knocked out. A split pot counts half."));
    if (!Object.keys(matrix).length) { app.replaceChildren(head, h("p", { class: "muted" }, "No hand histories yet this season.")); return; }
    app.replaceChildren(head, h("section", { class: "card" }, heatmap(matrix, "Knockout counts by hunter and victim")));
  }

  // ---------- routing ----------
  async function route() {
    hideTooltip();
    const hash = location.hash.replace(/^#\/?/, "");
    const [section, arg] = hash.split("/");
    document.querySelectorAll("[data-nav]").forEach((a) => {
      const active = (a.dataset.nav === "standings" && !section) || a.dataset.nav === section ||
        (a.dataset.nav === "tournaments" && section === "t");
      active ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
    });
    try {
      if (section === "t" && arg) await viewTournament(decodeURIComponent(arg));
      else if (section === "p" && arg) await viewPlayer(decodeURIComponent(arg));
      else if (section === "tournaments") await viewTournaments();
      else if (section === "knockouts") await viewKnockouts();
      else await viewStandings();
    } catch (err) {
      app.replaceChildren(h("p", {}, "Couldn't load this page. "), h("p", { class: "muted" }, String(err.message || err)));
    }
    window.scrollTo(0, 0);
  }

  async function init() {
    state.league = await load("data/league.json");
    const seasons = state.league.seasons.map((s2) => s2.season);
    let saved = null;
    try { saved = Number(localStorage.getItem("season")); } catch (e) { /* storage unavailable */ }
    state.season = seasons.includes(saved) ? saved : seasons[0];
    seasonSelect.replaceChildren(...seasons.map((yr) => h("option", { value: yr, selected: yr === state.season }, yr)));
    seasonSelect.addEventListener("change", () => {
      state.season = Number(seasonSelect.value);
      state.highlight = [];
      try { localStorage.setItem("season", state.season); } catch (e) { /* ignore */ }
      route();
    });
    document.querySelector(".theme-toggle").addEventListener("click", () => {
      const root = document.documentElement;
      const isDark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
      root.dataset.theme = isDark ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (e) { /* ignore */ }
      route();
    });
    window.addEventListener("hashchange", route);
    route();
  }
  init().catch((err) => app.replaceChildren(h("p", {}, "Couldn't load league data."), h("p", { class: "muted" }, String(err))));
})();
