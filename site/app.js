/* Pulse reader. No framework, no build step: it reads ./data/index.json and
   one JSON file per day, written each morning by the pipeline. Everything
   from the data is inserted as text, never as HTML. */

(() => {
  "use strict";

  const DATA = "./data/";
  const REFRESH_AFTER_MS = 15 * 60 * 1000;
  const main = document.getElementById("main");
  const notice = document.getElementById("notice");
  const state = { index: null, days: new Map(), fetchedAt: 0, shownLatest: null, scroll: new Map() };

  // ---------------------------------------------------------------- helpers

  function el(tag, props, ...children) {
    const node = document.createElementNS(
      props && props.svg ? "http://www.w3.org/2000/svg" : "http://www.w3.org/1999/xhtml", tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false || key === "svg") continue;
      if (key === "class") node.setAttribute("class", value);
      else if (key === "text") node.textContent = value;
      else if (key === "href") { const safe = safeHref(value); if (safe) node.setAttribute("href", safe); }
      else if (key === "style") for (const [prop, v] of Object.entries(value)) node.style.setProperty(prop, v);
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function safeHref(url) {
    if (typeof url !== "string") return null;
    if (/^#[A-Za-z0-9-]*$/.test(url)) return url;
    try {
      const parsed = new URL(url);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
    } catch { return null; }
  }

  function external(url, props, ...children) {
    const href = safeHref(url);
    if (!href) return el("span", { class: props && props.class }, ...children);
    return el("a", { ...props, href, target: "_blank", rel: "noopener noreferrer" }, ...children);
  }

  const dateOnly = (iso) => new Date(`${iso}T12:00:00Z`);
  const fmt = (iso, options) => new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", ...options }).format(dateOnly(iso));
  const longDay = (iso) => fmt(iso, { weekday: "long", day: "numeric", month: "long" });
  const shortDay = (iso) => fmt(iso, { day: "numeric", month: "short" });

  function timeIn(isoInstant, tz) {
    try {
      return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: tz || undefined })
        .format(new Date(isoInstant));
    } catch { return ""; }
  }

  function todayIn(tz) {
    try {
      return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" })
        .format(new Date());
    } catch { return new Date().toISOString().slice(0, 10); }
  }

  function plural(n, one, many) { return `${n} ${n === 1 ? one : many}`; }

  function listWords(items) {
    if (items.length <= 1) return items.join("");
    return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
  }

  // ---------------------------------------------------------------- heat

  function ramp() {
    const css = getComputedStyle(document.documentElement);
    return [0, 1, 2, 3, 4].map((i) => css.getPropertyValue(`--heat-${i}`).trim());
  }

  function hexToRgb(hex) {
    const n = parseInt(hex.replace("#", ""), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function heatColor(value) {
    const stops = ramp().map(hexToRgb);
    const v = Math.min(1, Math.max(0, Number(value) || 0)) * (stops.length - 1);
    const i = Math.min(stops.length - 2, Math.floor(v));
    const t = v - i;
    const c = stops[i].map((a, k) => Math.round(a + (stops[i + 1][k] - a) * t));
    return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
  }

  function heatWord(value) {
    if (value >= 0.8) return "very loud";
    if (value >= 0.6) return "loud";
    if (value >= 0.35) return "steady";
    return "quiet";
  }

  function bar(value) {
    return el("span", { class: "bar", style: { "--heat": heatColor(value) }, "aria-hidden": "true" });
  }

  // ---------------------------------------------------------------- pulse line

  let gradientId = 0;

  // Monotone cubic through the points, so the line never dips below zero.
  function smoothPath(points) {
    const n = points.length;
    if (n < 2) return "";
    const dx = [], dy = [], m = [];
    for (let i = 0; i < n - 1; i++) {
      dx.push(points[i + 1][0] - points[i][0]);
      dy.push(points[i + 1][1] - points[i][1]);
      m.push(dy[i] / dx[i]);
    }
    const t = [m[0]];
    for (let i = 1; i < n - 1; i++) t.push(m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2);
    t.push(m[n - 2]);
    for (let i = 0; i < n - 1; i++) {
      if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
      const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
      if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
    }
    let d = `M${points[0][0].toFixed(1)},${points[0][1].toFixed(1)}`;
    for (let i = 0; i < n - 1; i++) {
      const [x0, y0] = points[i], [x1, y1] = points[i + 1], h = dx[i] / 3;
      d += `C${(x0 + h).toFixed(1)},${(y0 + t[i] * h).toFixed(1)} ${(x1 - h).toFixed(1)},${(y1 - t[i + 1] * h).toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
    }
    return d;
  }

  function pulseLine(values, { height = 44, delay = 0, animate = true, label = "" } = {}) {
    const W = 320, H = height, top = 3, bottom = 1;
    const svg = el("svg", {
      svg: true, class: `pulse${animate ? " draw" : ""}`, viewBox: `0 0 ${W} ${H}`,
      preserveAspectRatio: "none", height: String(H), role: "img", "aria-label": label,
      style: { "--delay": `${delay}ms` },
    });
    const data = Array.isArray(values) ? values.map(Number) : [];
    svg.append(el("line", { svg: true, class: "base", x1: 0, y1: H - 0.5, x2: W, y2: H - 0.5 }));
    if (!data.length || Math.max(...data) <= 0) {
      svg.classList.remove("draw");
      svg.append(el("line", { svg: true, class: "flat", x1: 0, y1: H - 3, x2: W, y2: H - 3 }));
      return svg;
    }
    const id = `heat-${++gradientId}`;
    const colors = ramp();
    const gradient = el("linearGradient", { svg: true, id, gradientUnits: "userSpaceOnUse", x1: 0, y1: H, x2: 0, y2: top });
    [0, 0.3, 0.55, 0.8, 1].forEach((offset, i) =>
      gradient.append(el("stop", { svg: true, offset: String(offset), "stop-color": colors[i] })));
    const clip = el("clipPath", { svg: true, id: `${id}-clip` },
      el("rect", { svg: true, class: "reveal", x: 0, y: -4, width: W, height: H + 8 }));
    svg.append(el("defs", { svg: true }, gradient, clip));

    const pts = data.map((v, i) => [(i / (data.length - 1)) * W, H - bottom - v * (H - top - bottom)]);
    const line = smoothPath(pts);
    const group = el("g", { svg: true, "clip-path": `url(#${id}-clip)` },
      el("path", { svg: true, class: "area", d: `${line}L${W},${H}L0,${H}Z`, fill: `url(#${id})` }),
      el("path", { svg: true, class: "line", d: line, stroke: `url(#${id})` }));
    svg.append(group);
    return svg;
  }

  function pulseBlock(topic, day, options) {
    const days = day.window && day.window.days ? day.window.days : 30;
    const end = day.window && day.window.end ? day.window.end : day.date;
    const start = day.window && day.window.start ? day.window.start : end;
    const hasLine = (topic.pulse || []).some((v) => v > 0);
    const showPeak = hasLine && topic.pulse_peak && topic.pulse_peak !== end && topic.pulse_peak !== start;
    const label = hasLine
      ? `Activity over the last ${days} days${topic.pulse_peak ? `, busiest on ${shortDay(topic.pulse_peak)}` : ""}`
      : `No dated activity in the last ${days} days`;
    return el("div", { class: "pulse-wrap" },
      pulseLine(topic.pulse, { ...options, label }),
      el("div", { class: "pulse-axis", "aria-hidden": "true" },
        el("span", { text: shortDay(start) }),
        showPeak ? el("span", { text: `busiest ${shortDay(topic.pulse_peak)}` }) : null,
        el("span", { text: shortDay(end) })));
  }

  // ---------------------------------------------------------------- data

  async function getJSON(path, fresh) {
    const response = await fetch(DATA + path, { cache: fresh ? "no-cache" : "default" });
    if (!response.ok) throw new Error(`${path}: ${response.status}`);
    return response.json();
  }

  async function loadIndex() {
    state.index = await getJSON("index.json", true);
    state.fetchedAt = Date.now();
    return state.index;
  }

  async function loadDay(date) {
    if (!state.days.has(date)) {
      const fresh = state.index && date === state.index.latest;
      state.days.set(date, await getJSON(`days/${date}.json`, fresh));
    }
    return state.days.get(date);
  }

  // ---------------------------------------------------------------- routing
  // Plain hash names (letters, digits, hyphens) so links survive anywhere:
  //   #today  #rising  #archive  #topic-ai-evals  #topic-ai-evals-story-2
  //   #day-2026-11-25  #day-2026-11-25-topic-ai-evals

  function route() {
    let rest = location.hash.replace(/^#/, "");
    const r = { view: "today", date: null, topic: null, story: null };
    const day = rest.match(/^day-(\d{4}-\d{2}-\d{2})(?:-(.*))?$/);
    if (day) { r.date = day[1]; rest = day[2] || ""; }
    const topic = rest.match(/^topic-(.+?)(?:-story-(\d+))?$/);
    if (topic) {
      r.view = "topic"; r.topic = topic[1];
      if (topic[2] !== undefined) r.story = Number(topic[2]);
    } else if (rest === "rising") r.view = "rising";
    else if (rest === "archive") r.view = "archive";
    return r;
  }

  const isPast = (date) => Boolean(date && state.index && date !== state.index.latest);
  const dayHref = (date) => (isPast(date) ? `#day-${date}` : "#today");
  const topicHref = (date, id, story) =>
    `#${isPast(date) ? `day-${date}-` : ""}topic-${id}${story !== undefined ? `-story-${story}` : ""}`;

  function setTabs(view) {
    const active = view === "topic" ? "today" : view;
    document.querySelectorAll(".tabs a").forEach((a) => {
      if (a.dataset.tab === active) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
  }

  // ---------------------------------------------------------------- views

  function writerNote(day) {
    if (day.written_by === "claude") return null;
    return el("p", { class: "note", text: day.written_by === "mixed"
      ? "Some topics are written straight from the ranked results because the Claude step failed for them today."
      : "This brief is built straight from the ranked results. Add an Anthropic API key to get it written up properly." });
  }

  function storyHeads(day, topic) {
    const shown = topic.stories.slice(0, 3);
    return el("ul", { class: "heads" }, shown.map((story, i) =>
      el("li", null, el("a", { href: topicHref(day.date, topic.id, i) },
        bar(story.heat),
        el("span", { class: "text" }, story.headline,
          el("span", { class: "visually-hidden", text: `, ${heatWord(story.heat)}` }))))));
  }

  function topicStatusNote(topic) {
    if (topic.status === "failed")
      return el("p", { class: "note", text: `This topic didn't come through today (${topic.error || "the research step failed"}). It runs again tomorrow morning.` });
    if (topic.status === "thin")
      return el("p", { class: "note", text: "Nothing solid turned up for this topic in the last month." });
    return null;
  }

  function renderToday(day) {
    const isLatest = day.date === state.index.latest;
    const isToday = day.date === todayIn(day.timezone);
    const updated = timeIn(day.generated_at, day.timezone);
    let meta;
    if (!isLatest) meta = `From the archive: ${longDay(day.date)}`;
    else if (isToday) meta = `${longDay(day.date)}, updated at ${updated}`;
    else meta = `Latest brief, from ${longDay(day.date)}`;

    const ov = day.overview || {};
    const sections = [];
    day.topics.forEach((topic, index) => {
      const count = topic.stories.length;
      sections.push(el("section", { class: "topic", "aria-labelledby": `t-${topic.id}` },
        el("div", { class: "topic-head" },
          el("h2", { class: "topic-name", id: `t-${topic.id}` },
            el("a", { href: topicHref(day.date, topic.id) }, topic.name)),
          count ? el("span", { class: "topic-time", text: `${topic.reading_minutes} min read` }) : null),
        pulseBlock(topic, day, { delay: 150 + index * 140 }),
        topic.summary ? el("p", { class: "prose", text: topic.summary }) : null,
        topicStatusNote(topic),
        count ? storyHeads(day, topic) : null,
        count ? el("a", { class: "more", href: topicHref(day.date, topic.id),
          text: count > 3 ? `Read all ${count} stories` : count === 1 ? "Read the story" : "Read the full brief" }) : null));
    });

    const sourceNames = new Set();
    day.topics.forEach((t) => (t.coverage || []).forEach((c) => c.state === "ok" && sourceNames.add(c.label)));
    const writer = day.written_by === "claude" ? `, written by Claude (${day.model})` : "";
    const colophon = day.preview
      ? `Preview built on ${longDay(day.date)}: Claude wrote it from news coverage found with web search, so there are no vote counts and bar colour shows how many outlets covered each story. ` +
        "Your daily brief replaces this with last30days evidence from Reddit, Hacker News, X, YouTube and more, where bar colour shows how hard people engaged."
      : `Researched by last30days ${day.engine || ""} across ${sourceNames.size ? listWords([...sourceNames].slice(0, 8)) : "public sources"}${writer}. ` +
        `It covers ${shortDay(day.window.start)} to ${shortDay(day.window.end)}. Bar colour shows how hard people engaged with a story compared with everything else that platform turned up.`;
    main.replaceChildren(
      el("header", { class: "hero" },
        el("p", { class: "meta", text: meta }),
        el("h1", { class: "page-title", text: ov.headline || "Today's brief" }),
        ov.bullets && ov.bullets.length ? el("ul", { class: "lede" }, ov.bullets.map((b) => el("li", { text: b }))) : null,
        day.preview ? el("p", { class: "note", text: "This is a preview with real news from the past month. Daily briefs start once the GitHub workflow is running." }) : null,
        writerNote(day)),
      ...sections,
      el("p", { class: "colophon", text: colophon }));
  }

  function renderStory(day, topic, story, i) {
    const q = story.quote;
    return el("article", { class: "story", id: `s-${i}` },
      bar(story.heat),
      el("div", { class: "story-body" },
        el("h3", null, story.headline,
          el("span", { class: "visually-hidden", text: `, ${heatWord(story.heat)}` })),
        story.body ? el("p", { text: story.body }) : null,
        q ? el("figure", { class: "quote" },
          el("blockquote", { text: q.text }),
          el("figcaption", null, external(q.url, null, q.who || "Source"), q.count ? `, ${q.count}` : "")) : null,
        story.odds ? el("p", { class: "odds", text: story.odds }) : null,
        story.sources.length ? el("ul", { class: "chips", "aria-label": "Sources" }, story.sources.map((s) =>
          el("li", null, external(s.url, { class: "chip", title: s.title },
            el("b", { text: s.label }), s.count ? el("span", { text: s.count }) : null)))) : null));
  }

  function renderTopic(day, id, storyIndex) {
    const topic = day.topics.find((t) => t.id === id);
    if (!topic) return renderMissing("That topic isn't in this brief.", dayHref(day.date));
    const sourcesUsed = (topic.coverage || []).filter((c) => c.state === "ok").length;
    const meta = topic.counts && topic.counts.items
      ? `${shortDay(day.window.start)} to ${shortDay(day.window.end)}, ${plural(topic.counts.items, "post", "posts")} from ${plural(sourcesUsed || topic.counts.sources, "source", "sources")}`
      : `${shortDay(day.window.start)} to ${shortDay(day.window.end)}`;

    const parts = [
      el("a", { class: "back", href: dayHref(day.date) },
        el("svg", { svg: true, viewBox: "0 0 24 24", "aria-hidden": "true" }, el("path", { svg: true, d: "M15 5l-7 7 7 7" })),
        day.date === state.index.latest ? "Today" : shortDay(day.date)),
      el("h1", { class: "page-title", text: topic.name }),
      el("p", { class: "meta", text: meta }),
      pulseBlock(topic, day, { height: 72, delay: 100 }),
      topic.summary ? el("p", { class: "prose", text: topic.summary }) : null,
      topicStatusNote(topic),
      ...topic.stories.map((s, i) => renderStory(day, topic, s, i)),
    ];
    if (topic.patterns && topic.patterns.length) {
      parts.push(el("h2", { class: "section-title", text: "What keeps coming up" }),
        el("ul", { class: "patterns" }, topic.patterns.map((p) => el("li", { text: p }))));
    }
    if (topic.watch) parts.push(el("p", { class: "watch" }, el("strong", { text: "Worth watching" }), topic.watch));
    if (topic.coverage && topic.coverage.length) {
      parts.push(el("h2", { class: "section-title", text: "Where this came from" }),
        el("ul", { class: "coverage" }, topic.coverage.map((c) =>
          el("li", { class: c.state }, el("span", { text: c.label }), el("span", { text: c.detail || "" })))));
    }
    main.replaceChildren(el("div", { class: "topic-detail" }, ...parts));
    if (storyIndex !== null) {
      const target = document.getElementById(`s-${storyIndex}`);
      if (target) requestAnimationFrame(() => target.scrollIntoView({ block: "start" }));
    }
  }

  function renderRising(day) {
    const r = day.rising;
    const where = r && r.domain ? `in ${r.domain}` : "everywhere";
    const head = [el("h1", { class: "page-title", text: `Rising ${where}` }),
      el("p", { class: "meta", text: "Topics picking up speed this week on Reddit, Hacker News and other feeds, ranked by momentum." })];
    if (!r) return main.replaceChildren(...head, el("p", { class: "empty", text: "The rising sweep didn't run for this brief." }));
    if (!r.items.length || r.outcome === "nothing-solid") {
      return main.replaceChildren(...head, el("p", { class: "empty", text:
        `Nothing is clearly rising this week.${r.weak_signal ? ` The closest candidate was ${r.weak_signal}.` : ""}` }));
    }
    const momentumWord = { "new-this-week": "New this week", building: "Building" };
    main.replaceChildren(...head, el("ol", { class: "rising-list" }, r.items.map((item, i) =>
      el("li", { class: "rise" },
        el("span", { class: "rise-rank", "aria-hidden": "true", text: String(item.rank || i + 1) }),
        el("div", null,
          el("h3", { text: item.topic }),
          item.momentum ? el("span", { class: "momentum", style: { "--heat": heatColor(item.momentum === "new-this-week" ? 0.95 : 0.6) },
            text: momentumWord[item.momentum] || item.momentum }) : null,
          item.comment ? el("figure", { class: "quote" }, el("blockquote", { text: item.comment })) : null,
          item.why ? el("p", { class: "why", text: item.why }) : null,
          item.counts.length ? el("p", { class: "why", text: item.counts.map((c) => `${c.label} ${c.count}`).join(", ") }) : null,
          item.evidence.length ? el("p", { class: "links" }, "Read more: ",
            item.evidence.slice(0, 3).map((url, k) => [k ? ", " : "",
              external(url, null, new URL(url).hostname.replace(/^www\./, ""))])) : null)))));
  }

  function renderArchive(currentDate) {
    const days = (state.index && state.index.days) || [];
    if (!days.length) return main.replaceChildren(el("h1", { class: "page-title", text: "Archive" }),
      el("p", { class: "empty", text: "Past briefs will collect here, one per day." }));
    const groups = new Map();
    for (const d of days) {
      const month = fmt(d.date, { month: "long", year: "numeric" });
      if (!groups.has(month)) groups.set(month, []);
      groups.get(month).push(d);
    }
    main.replaceChildren(
      el("h1", { class: "page-title", text: "Archive" }),
      el("p", { class: "meta", text: `${plural(days.length, "brief", "briefs")} so far.` }),
      el("div", { class: "months" }, [...groups.entries()].map(([month, list]) => [
        el("h2", { class: "month-name", text: month }),
        el("ul", { class: "days" }, list.map((d) => el("li", { class: d.date === currentDate ? "current" : null },
          el("a", { href: dayHref(d.date) },
            el("span", { class: "d", text: fmt(d.date, { weekday: "short", day: "numeric" }) }),
            el("span", { class: "h", text: d.headline || listWords(d.topics || []) }))))),
      ])));
  }

  function renderMissing(message, href) {
    main.replaceChildren(el("p", { class: "empty", text: message }), el("a", { class: "more", href, text: "Go to today's brief" }));
  }

  function renderNoData(offline) {
    main.replaceChildren(
      el("h1", { class: "page-title", text: offline ? "You're offline" : "No brief yet" }),
      el("p", { class: "prose", text: offline
        ? "Pulse couldn't reach the internet and has no saved brief to show. Open it again once you're back online."
        : "The first brief appears after the morning run. To make one now, open the repository on GitHub, go to Actions, pick “Daily brief” and choose “Run workflow”." }));
  }

  // ---------------------------------------------------------------- render loop

  let lastKey = "";

  async function render() {
    const r = route();
    setTabs(r.view);
    if (!state.index) {
      try { await loadIndex(); } catch { return renderNoData(!navigator.onLine); }
    }
    if (!state.index.days || !state.index.days.length) return renderNoData(false);
    const date = r.date || state.index.latest;
    state.shownLatest = state.index.latest;

    const key = location.hash;
    state.scroll.set(lastKey, window.scrollY);
    if (r.view === "archive") renderArchive(date);
    else {
      let day;
      try { day = await loadDay(date); } catch {
        return renderMissing(navigator.onLine ? "That brief couldn't be loaded." : "That brief isn't saved for offline reading.", "#today");
      }
      if (r.view === "topic") renderTopic(day, r.topic, r.story);
      else if (r.view === "rising") renderRising(day);
      else renderToday(day);
    }
    if (r.story === null) window.scrollTo(0, state.scroll.get(key) || 0);
    if (lastKey) main.focus({ preventScroll: true });
    lastKey = key;
  }

  function showNotice(text, actionText, action) {
    notice.replaceChildren(el("span", { text }), actionText ? el("button", { type: "button", onclick: action, text: actionText }) : null);
    notice.hidden = false;
  }

  async function checkForNewBrief() {
    if (document.visibilityState !== "visible" || Date.now() - state.fetchedAt < REFRESH_AFTER_MS) return;
    try {
      const before = state.shownLatest;
      await loadIndex();
      if (before && state.index.latest !== before) {
        showNotice("A new brief is ready.", "Show it", () => {
          notice.hidden = true;
          state.scroll.clear();
          location.hash = "#today";
          render();
        });
      }
    } catch { /* offline: keep what we have */ }
  }

  window.addEventListener("hashchange", render);
  document.addEventListener("visibilitychange", checkForNewBrief);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render());
  new MutationObserver(() => render())
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  if ("scrollRestoration" in history) history.scrollRestoration = "manual";

  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    window.addEventListener("load", () => navigator.serviceWorker.register("./sw.js").catch(() => {}));
  }
  render();
})();
