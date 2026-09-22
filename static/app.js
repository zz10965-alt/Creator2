"use strict";

const METRICS = [
  { key: "content_match", label: "Content match" },
  { key: "engagement", label: "Engagement" },
  { key: "growth", label: "Growth" },
  { key: "stability", label: "Stability" },
  { key: "audience_fit", label: "Audience fit" },
  { key: "consistency", label: "Consistency" },
  { key: "content_quality", label: "Content quality" },
  { key: "ad_length_match", label: "Ad-length match" },
  { key: "audience_sentiment", label: "Sentiment" },
];

const DEFAULT_WEIGHTS = {
  content_match: 20, engagement: 18, growth: 12, stability: 12,
  audience_fit: 12, consistency: 8, content_quality: 8,
  ad_length_match: 5, audience_sentiment: 5,
};

const state = {
  results: null,
  chatHistory: [],
  brief: {},        // partial campaign_input accumulated from the chat
  weights: { ...DEFAULT_WEIGHTS },
  forceDemo: false,
  useGemini: true,
  awaitingContinue: false,
};

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function esc(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function inline(s) {
  return s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`(.+?)`/g, "<code>$1</code>");
}
function splitList(v) {
  return String(v || "").split(",").map((s) => s.trim()).filter(Boolean);
}
function truncate(s, n) {
  s = String(s == null ? "" : s);
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function showView(name) {
  $("view-chat").classList.toggle("hidden", name !== "chat");
  $("view-waiting").classList.toggle("hidden", name !== "waiting");
  $("view-results").classList.toggle("hidden", name !== "results");
}

// ---------------------------------------------------------------------------
// Brief (source of truth is state.brief, filled by the chat or the sample)
// ---------------------------------------------------------------------------

function collectBrief() {
  const b = state.brief || {};
  return {
    brief: b.brief || "",
    keywords: Array.isArray(b.keywords) ? b.keywords : [],
    target_audience: b.target_audience || "",
    target_market: b.target_market || "",
    budget_cap: b.budget_cap || 0,
    target_k: b.target_k || 3,
    desired_ad_length: b.desired_ad_length != null ? b.desired_ad_length : null,
    format: b.format || "long",
    competitors: Array.isArray(b.competitors) ? b.competitors : [],
    risk_topics: Array.isArray(b.risk_topics) ? b.risk_topics : [],
    weights: { ...state.weights },
  };
}

const BRIEF_FIELDS = [
  { key: "brief", label: "Brand / campaign" },
  { key: "keywords", label: "Keywords" },
  { key: "target_audience", label: "Target audience" },
  { key: "target_market", label: "Target market" },
  { key: "budget_cap", label: "Budget (USD)" },
  { key: "target_k", label: "Creators to pick" },
  { key: "desired_ad_length", label: "Ad length (sec)" },
  { key: "format", label: "Format" },
  { key: "competitors", label: "Competitors" },
  { key: "risk_topics", label: "Risk topics" },
];

function fmtBriefValue(f) {
  const v = state.brief ? state.brief[f.key] : undefined;
  if (v == null || v === "") return "";
  if (Array.isArray(v)) {
    const a = v.filter(Boolean);
    return a.length ? a.join(", ") : "";
  }
  if (typeof v === "number" && v === 0) return "";
  return String(v).trim();
}

function renderBriefPanel(ci) {
  state.brief = ci || {};
  const el = $("brief-live");
  const rows = [];
  BRIEF_FIELDS.forEach((f) => {
    const v = fmtBriefValue(f);
    if (v) rows.push(
      `<div class="brief-row"><span class="brief-label">${esc(f.label)}</span><span class="brief-val">${esc(v)}</span></div>`
    );
  });
  el.innerHTML = rows.length
    ? rows.join("")
    : '<p class="hint">Tell me about your brand to start building the brief.</p>';
}

function renderTrends(trends, note) {
  const section = $("trends-section");
  const el = $("trends-live");
  if (trends && trends.length) {
    section.hidden = false;
    el.innerHTML = trends.map((t) => `<div class="trend-line">${esc(t)}</div>`).join("");
    revealTrends();
  } else if (note) {
    section.hidden = false;
    el.innerHTML = `<p class="hint">${esc(note)}</p>`;
    revealTrends();
  } else {
    section.hidden = true;
    el.innerHTML = "";
  }
}

// The brief panel is a sticky, scrollable column; when the trends card and the
// Continue button appear at the bottom, scroll them into view so the presenter
// actually sees them pop in instead of them sitting below the fold.
function revealTrends() {
  const panel = document.querySelector(".brief-panel");
  if (panel) {
    setTimeout(() => panel.scrollTo({ top: panel.scrollHeight, behavior: "smooth" }), 80);
  }
}

async function runSearchTrends(first) {
  if (first) {
    pushMsg("Info collected — let me identify the current trends for you.", "bot");
  }
  let trends = [], note = "";
  try {
    const r = await fetch("/api/trending/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ campaign_input: collectBrief(), force_demo: !!state.forceDemo }),
    });
    const data = await r.json();
    trends = data.trends || [];
    note = data.note || "";
  } catch (e) {
    note = "Search failed: " + e.message;
  }
  renderTrends(trends, note);
  $("btn-continue").hidden = false;
}

// ---------------------------------------------------------------------------
// Weight sliders
// ---------------------------------------------------------------------------

function buildWeights() {
  const wrap = $("weights");
  wrap.innerHTML = "";
  METRICS.forEach((m) => {
    const row = document.createElement("div");
    row.className = "weight-row";
    const label = document.createElement("span");
    label.textContent = m.label;
    const input = document.createElement("input");
    input.type = "range"; input.min = 0; input.max = 30; input.step = 1;
    input.value = state.weights[m.key];
    const val = document.createElement("em");
    val.textContent = state.weights[m.key];
    input.addEventListener("input", () => {
      state.weights[m.key] = parseInt(input.value, 10);
      val.textContent = input.value;
    });
    row.append(label, input, val);
    wrap.appendChild(row);
  });
}

// ---------------------------------------------------------------------------
// Run analysis + waiting state
// ---------------------------------------------------------------------------

async function runAnalysis(forceDemo, useGemini) {
  const brief = collectBrief();
  state.forceDemo = !!forceDemo;
  state.useGemini = useGemini !== false;
  if (!brief.brief && brief.keywords.length === 0) {
    return false;
  }
  try {
    const resp = await fetch("/api/campaign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ campaign_input: brief, force_demo: !!forceDemo, use_gemini: state.useGemini }),
    });
    const results = await resp.json();
    state.results = results;
    renderAll(results, brief);
    return true;
  } catch (e) {
    return false;
  }
}

async function startGenerating(forceDemo, useGemini) {
  showView("waiting");
  $("waiting-title").textContent = "Discovering & scoring creators…";
  $("waiting-sub").textContent = "Finding candidate channels, scoring their fit, and ranking them by your budget.";
  const ok = await runAnalysis(forceDemo, useGemini);
  showView(ok ? "results" : "chat");
  if (!ok) {
    pushMsg("Could not run the analysis. Try a sample, or check that the server is running.", "bot");
  }
}

// ---- "Try a sample": a hands-free, zero-Gemini replay of the whole flow ----
// Plays a scripted brief interview, shows preset trends, then runs a REAL YouTube
// pull (live creators from the brief) with keyword-fallback scoring (no Gemini).
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

const DEMO_PAUSE = 950;   // ms between scripted chat turns

const DEMO_STEPS = [
  { who: "bot",  text: "What's your brand, and what are you trying to promote?" },
  { who: "user", text: "It's an eco-friendly skincare brand for women 18-34.",
    brief: { brief: "Eco-friendly skincare brand, women 18-34" } },
  { who: "bot",  text: "Nice. What keywords or topics should we target?" },
  { who: "user", text: "Clean beauty, vegan, zero-waste skincare.",
    brief: { keywords: ["skincare", "clean beauty", "eco-friendly", "vegan"] } },
  { who: "bot",  text: "What's your budget, and how many creators do you want?" },
  { who: "user", text: "About $5,000 total, 3 creators, 60-second ads.",
    brief: { budget_cap: 5000, target_k: 3, desired_ad_length: 60, format: "long",
             target_audience: "women 18-34" } },
  { who: "bot",  text: "Which market or region are you targeting?" },
  { who: "user", text: "The US.",
    brief: { target_market: "US" } },
  { who: "bot",  text: "Got it — any specific region or city you want to focus on?" },
  { who: "user", text: "The Northeast, mainly New York.",
    brief: { target_market: "US — Northeast, mainly New York" } },
  { who: "bot",  text: "Any competitors to avoid, or topics that are off-limits?" },
  { who: "user", text: "Avoid BrandX. No political or controversial stuff.",
    brief: { competitors: ["BrandX"], risk_topics: ["political", "controversial", "scandal"] } },
  { who: "bot",  text: "Got it — the brief is complete. Let me identify the current trends for you." },
];

const DEMO_TRENDS = [
  "Skin-barrier repair is the top clean-beauty angle right now — creators lead with ceramides and \"skinimalism\" (fewer, gentler products)",
  "De-influencing is outperforming hauls: \"what I wouldn't buy\" and honest ingredient breakdowns pull the highest watch time in beauty",
  "Refillable / plastic-neutral packaging has become a purchase trigger — zero-waste routines are a key differentiator for DTC skincare",
  "Preventive SPF-in-skincare content is spiking with women 18-34 as \"start anti-aging in your 20s\" goes mainstream",
];

async function runDemo() {
  // Hard reset without the greeting - the script provides its own open.
  state.results = null;
  state.chatHistory = [];
  state.brief = {};
  state.awaitingContinue = false;
  state.forceDemo = false;
  state.useGemini = false;
  $("chat-log").innerHTML = "";
  renderBriefPanel({});
  renderTrends([], "");
  $("btn-continue").hidden = true;
  showEmptyState();
  showView("chat");

  for (const s of DEMO_STEPS) {
    if (s.brief) { Object.assign(state.brief, s.brief); renderBriefPanel(state.brief); }
    pushMsg(s.text, s.who);
    await sleep(DEMO_PAUSE);
  }

  renderTrends(DEMO_TRENDS, "");
  pushMsg("Here's what's trending in your space right now.", "bot");
  $("btn-continue").hidden = false;
  // Stop here: the presenter clicks Continue to run the shortlist.
}

async function renderAll(results, brief) {
  const mode = $("mode-badge");
  mode.textContent = results.mode;
  mode.className = "badge " + (results.mode === "LIVE" ? "badge-data" : "badge-warn");

  renderExcluded(results.excluded || []);
  renderRanking(results.creators || [], results.sources || {});
  renderBudget(results.budget_scenarios || {}, brief.budget_cap);

  // Recommendation (structured JSON) - async
  try {
    const ir = await fetch("/api/insights", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ results, campaign_input: brief, force_demo: !!state.forceDemo, use_gemini: !!state.useGemini }),
    });
    const ins = await ir.json();
    renderInsights(ins);
  } catch (e) { /* panel stays on empty state */ }

  // Trending + match - async (trending is keyword-filtered on the server)
  try {
    const qs = "?keywords=" + encodeURIComponent(brief.keywords.join(","));
    const tr = await fetch("/api/trending" + qs);
    const tdata = await tr.json();
    let matches = [];
    try {
      const mr = await fetch("/api/trending/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trending: tdata.trending, creators: results.creators, campaign_input: brief, force_demo: !!state.forceDemo, use_gemini: !!state.useGemini }),
      });
      const mdata = await mr.json();
      matches = mdata.matches || [];
    } catch (e) {}
    renderTrending(tdata.trending || [], matches);
  } catch (e) {}
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function srcBadge(src) {
  return src === "data"
    ? '<span class="badge badge-data">data</span>'
    : '<span class="badge badge-ai">AI</span>';
}

function renderExcluded(excluded) {
  const el = $("excluded");
  if (!excluded.length) { el.innerHTML = ""; return; }
  el.innerHTML = excluded.map((e) =>
    `<span class="chip" title="${esc(e.reason)}">&#10005; ${esc(e.handle)} <small>(${esc(e.reason)})</small></span>`
  ).join("");
}

function renderRanking(creators, sources) {
  const el = $("ranking");
  if (!creators.length) {
    el.innerHTML = '<div class="empty-state"><p>No creators passed the cutoffs for this brief.</p></div>';
    return;
  }
  el.innerHTML = creators.map((c) => {
    const flags = [];
    if (c.flags && c.flags.low_reach_ratio) flags.push('<span class="tag" style="color:var(--warn)">low reach ratio</span>');
    if (c.shorts_ratio > 0.5) flags.push(`<span class="tag">${Math.round(c.shorts_ratio * 100)}% Shorts</span>`);
    if (c.sentiment && c.sentiment.sample_size > 0) {
      flags.push(`<span class="tag">fans ${c.sentiment.fans_pct}% / haters ${c.sentiment.haters_pct}%</span>`);
    }
    const bars = METRICS.map((m) => {
      const v = c.scores[m.key] != null ? c.scores[m.key] : 0;
      return `<div class="m"><div class="lbl">${m.label} <span class="src">${srcBadge(sources[m.key])}</span></div>
        <div class="bar"><i style="width:${Math.round(v)}%"></i></div></div>`;
    }).join("");
    return `<div class="rank-row">
      <div class="head">
        <span class="name">#${c.rank} ${esc(c.handle)}</span>
        <span class="score">${c.total}</span>
        <span class="subs">${c.subscribers.toLocaleString()} subs &middot; ${c.avg_views.toLocaleString()} avg views &middot; ~$${Math.round(c.estimated_cost).toLocaleString()}</span>
      </div>
      <div class="meter">${bars}</div>
      <div class="rank-actions">${flags.join("")}
        <button class="btn btn-ghost" onclick="showScoreChart(${c.rank - 1})">score chart</button>
      </div>
    </div>`;
  }).join("");
}

function renderBudget(scenarios, budget) {
  const el = $("budget-cards");
  if (!scenarios || !scenarios.at) {
    el.innerHTML = '<p class="hint empty">Budget options will appear after a run.</p>';
    return;
  }
  const labels = { under: "Under budget (70%)", at: "At budget", over: "Over budget (130%)" };
  const rows = [];
  const chartData = [["Scenario", "Cost", "Views"]];
  Object.keys(labels).forEach((key) => {
    const s = scenarios[key];
    if (!s) return;
    chartData.push([labels[key], s.total_cost, s.expected_views]);
    rows.push(`<div class="bcard ${key === "at" ? "pick" : ""}">
      <h3>${labels[key]}</h3>
      <div class="big">$${s.total_cost.toLocaleString()}</div>
      <div class="sub">${s.picks.map(esc).join(", ")}<br>${s.expected_views.toLocaleString()} expected views &middot; ${s.expected_engagements.toLocaleString()} engagements</div>
    </div>`);
  });
  el.innerHTML = rows.join("") + '<div id="budget-chart" style="height:200px;margin-top:8px"></div>';
  if (chartData.length > 1) drawBudgetChart(chartData);
}

function drawBudgetChart(rows) {
  if (typeof google === "undefined") return;
  google.charts.load("current", { packages: ["corechart"] });
  google.charts.setOnLoadCallback(() => {
    const data = google.visualization.arrayToDataTable(rows);
    const chart = new google.visualization.ComboChart($("budget-chart"));
    chart.draw(data, {
      backgroundColor: "transparent",
      seriesType: "bars",
      series: { 1: { type: "line", targetAxisIndex: 1, color: "#0d9488", lineWidth: 3, pointSize: 5 } },
      colors: ["#2563eb"],
      legend: { position: "top", textStyle: { color: "#667085" } },
      hAxis: { textStyle: { color: "#667085" } },
      vAxes: {
        0: { title: "Cost (USD)", format: "$#,###", titleTextStyle: { color: "#2563eb", italic: false }, textStyle: { color: "#667085" }, gridlines: { color: "#e3e7ee" } },
        1: { title: "Views", format: "short", titleTextStyle: { color: "#0d9488", italic: false }, textStyle: { color: "#667085" }, gridlines: { color: "transparent" } },
      },
      chartArea: { width: "72%", height: "66%" },
    });
  });
}

function renderInsights(data) {
  const el = $("insights");
  if (!data) {
    el.innerHTML = '<div class="empty-state"><p>Your recommendation summary (best match + budget lineup) will show up here first.</p></div>';
    return;
  }
  let html = "";

  if (data.ai_take) {
    html += `<div class="ai-take"><span class="badge badge-ai">AI take</span><p>${inline(esc(data.ai_take))}</p></div>`;
  }

  if (data.best_pick) html += renderBestPick(data.best_pick);

  if (data.lineup) html += renderLineup("Recommended lineup", data.lineup);

  if (data.lineup_under || data.lineup_over) html += renderAlternatives(data);

  if (data.risks && data.risks.length) {
    html += `<section class="rec-section"><h3>Risks &amp; flags</h3>
      <ul class="risks">${data.risks.map((r) => `<li>${esc(r.handle)}: ${esc(r.risk)}</li>`).join("")}</ul></section>`;
  }

  html += `<p class="next-step">${esc(data.next_step || "")}</p>`;

  el.innerHTML = html;
}

function renderBestPick(bp) {
  return `<section class="rec-section">
    <h3>Best single match</h3>
    <div class="hero-card">
      <div class="hero-name">#${bp.rank} ${esc(bp.handle)}</div>
      <div class="hero-score">${bp.total}<span>/100</span></div>
      <div class="hero-stats">
        <div><b>${bp.avg_views.toLocaleString()}</b>avg views</div>
        <div><b>${bp.subscribers.toLocaleString()}</b>subscribers</div>
        <div><b>~$${Math.round(bp.estimated_cost).toLocaleString()}</b>per video</div>
      </div>
      ${bp.positioning ? `<div class="mblock"><span class="mblock-label">Positioning</span><p>${esc(bp.positioning)}</p></div>` : ""}
      <div class="mblock"><span class="mblock-label">Why chosen</span><p>${esc(bp.why)}</p></div>
      <div class="member-actions">
        <button class="btn" onclick='showScriptFor("${esc(bp.handle)}")'>Generate script</button>
      </div>
    </div>
  </section>`;
}

function renderLineup(title, lineup) {
  const members = lineup.members.map(renderMember).join("");
  return `<section class="rec-section">
    <h3>${title}</h3>
    <div class="lineup-summary">
      <span><b>$${Math.round(lineup.total_cost).toLocaleString()}</b>total cost</span>
      <span><b>${lineup.expected_views.toLocaleString()}</b>expected views</span>
      <span><b>${lineup.expected_engagements.toLocaleString()}</b>engagements</span>
    </div>
    <div class="member-list">${members}</div>
  </section>`;
}

function renderMember(m) {
  const facts = (m.facts || []).join(" · ");
  const matchBars = (m.match || []).map((mm) => {
    const v = mm.score != null ? Math.round(mm.score) : 0;
    return `<div class="m"><div class="lbl">${esc(mm.label)}</div>
      <div class="bar"><i style="width:${v}%"></i></div></div>`;
  }).join("");
  const why = m.why || "";
  return `<div class="member-card">
    <div class="member-head">
      <span class="member-name">${esc(m.handle)}</span>
      <span class="member-score">${m.total}<span class="member-score-denom">/100</span></span>
    </div>
    <div class="member-sub">~$${Math.round(m.estimated_cost).toLocaleString()} &middot; ${m.avg_views.toLocaleString()} avg views &middot; ${m.subscribers.toLocaleString()} subs</div>
    ${m.positioning ? `<div class="mblock"><span class="mblock-label">Positioning</span><p>${esc(m.positioning)}</p></div>` : ""}
    ${facts ? `<div class="mblock"><span class="mblock-label">Recent data</span><p>${esc(facts)}</p></div>` : ""}
    ${matchBars ? `<div class="mblock"><span class="mblock-label">Match</span><div class="meter">${matchBars}</div></div>` : ""}
    ${why ? `<div class="mblock"><span class="mblock-label">Why chosen</span><p>${esc(why)}</p></div>` : ""}
    <div class="member-actions">
      <button class="btn" onclick='showScriptFor("${esc(m.handle)}")'>Generate script</button>
    </div>
  </div>`;
}

function renderAlternatives(data) {
  const alt = (label, l) => {
    if (!l) return `<div class="alt-card muted">${label}: no combo fits this budget.</div>`;
    const picks = l.members.map((m) => m.handle).join(", ");
    return `<div class="alt-card"><b>${label}</b> &mdash; ${esc(picks)}<br>
      <span class="alt-sub">$${Math.round(l.total_cost).toLocaleString()} / ${l.expected_views.toLocaleString()} views / ${l.expected_engagements.toLocaleString()} engagements</span></div>`;
  };
  return `<section class="rec-section">
    <h3>Budget alternatives</h3>
    ${alt("Under budget (70%)", data.lineup_under)}
    ${alt("Over budget (130%)", data.lineup_over)}
  </section>`;
}

function renderTrending(trending, matches) {
  const el = $("trending");
  if (!trending.length) { el.innerHTML = '<p class="hint empty">Trending topics load after a run.</p>'; return; }
  const matchMap = {};
  (matches || []).forEach((m) => { matchMap[m.trending] = m; });
  el.innerHTML = trending.map((t) => {
    const m = matchMap[t.title];
    const url = t.video_id
      ? "https://www.youtube.com/watch?v=" + encodeURIComponent(t.video_id)
      : null;
    const title = url
      ? `<a class="trend-link" href="${url}" target="_blank" rel="noopener">${esc(t.title)}</a>`
      : esc(t.title);
    return `<div class="trend-item">${title}
      <div class="ch">${esc(t.channel)}${m ? ` &middot; <span class="trend-match">match: ${esc(m.creator)}</span>` : ""}</div>
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// Score chart + script modal
// ---------------------------------------------------------------------------

function showScoreChart(idx) {
  const c = state.results && state.results.creators[idx];
  if (!c) return;
  const rows = [["Metric", "Score", { role: "annotation" }]];
  METRICS.forEach((m) => rows.push([m.label, c.scores[m.key] || 0, String(Math.round(c.scores[m.key] || 0))]));
  openModal("Score profile: " + c.handle);
  $("modal-doc").style.display = "none";
  $("modal-chart").style.display = "block";
  if (typeof google === "undefined") { $("modal-chart").innerHTML = "Charts unavailable."; return; }
  google.charts.load("current", { packages: ["corechart"] });
  google.charts.setOnLoadCallback(() => {
    const data = google.visualization.arrayToDataTable(rows);
    const chart = new google.visualization.BarChart($("modal-chart"));
    chart.draw(data, {
      backgroundColor: "transparent",
      legend: { position: "none" },
      colors: ["#2563eb"],
      hAxis: { minValue: 0, maxValue: 100, textStyle: { color: "#667085" }, gridlines: { color: "#e3e7ee" } },
      vAxis: { textStyle: { color: "#1f2328" } },
      chartArea: { width: "68%", height: "82%" },
      bar: { groupWidth: "62%" },
      annotations: { alwaysOutside: true, textStyle: { color: "#667085", fontSize: 11 } },
    });
  });
}

function showScriptFor(handle) {
  const c = (state.results && state.results.creators || []).find((x) => x.handle === handle);
  if (c) showScript(c);
}

async function showScript(c) {
  if (!c) return;
  openModal("Collaboration script: " + c.handle);
  $("modal-doc").style.display = "block";
  $("modal-chart").style.display = "none";
  $("modal-doc").innerHTML = '<p class="hint">Generating concepts…</p>';
  let concepts = [];
  try {
    const r = await fetch("/api/concepts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ creator: c, campaign_input: collectBrief(), use_gemini: !!state.useGemini }),
    });
    const data = await r.json();
    concepts = data.concepts || [];
  } catch (e) {
    concepts = [];
  }
  if (concepts.length) {
    renderConceptPicker(c, concepts);
  } else {
    $("modal-doc").innerHTML = '<p class="hint">Generating script…</p>';
    fetchScript(c, null);
  }
}

function renderConceptPicker(c, concepts) {
  const el = $("modal-doc");
  const opts = concepts.map((k, i) =>
    `<label class="concept-opt">
      <input type="radio" name="concept" value="${i}" ${i === 0 ? "checked" : ""}>
      <span class="concept-body"><b>${esc(k.angle)}</b>
      <span class="concept-reason">${esc(k.reason)}</span>
      <span class="concept-title">${esc(k.title)}</span></span>
    </label>`
  ).join("");
  el.innerHTML = `
    <p class="hint">Pick an angle, then I'll write the full script around it.</p>
    <div class="concept-list">${opts}</div>
    <div class="concept-actions">
      <button id="concept-write" class="btn btn-primary">Write full script &rarr;</button>
    </div>`;
  $("concept-write").addEventListener("click", () => {
    const sel = el.querySelector('input[name="concept"]:checked');
    const idx = sel ? parseInt(sel.value, 10) : 0;
    const concept = concepts[idx];
    el.innerHTML = '<p class="hint">Generating script…</p>';
    fetchScript(c, concept);
  });
}

async function fetchScript(c, concept) {
  try {
    const r = await fetch("/api/script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ creator: c, campaign_input: collectBrief(), concept, use_gemini: !!state.useGemini }),
    });
    const data = await r.json();
    renderScript(data.script, data.note);
  } catch (e) {
    $("modal-doc").innerHTML = '<p class="hint">Error: ' + esc(e.message) + '</p>';
  }
}

function renderScript(s, note) {
  const el = $("modal-doc");
  if (!s || typeof s !== "object") {
    el.innerHTML = '<p class="hint">No script available.</p>';
    return;
  }
  const titles = (s.title_options || []).map((t) => `<li>${esc(t)}</li>`).join("");
  const board = (s.storyboard || []).map((r) =>
    `<tr><td class="sb-time">${esc(r.time)}</td><td class="sb-shot">${esc(r.shot)}</td><td class="sb-dialogue">${esc(r.dialogue)}</td></tr>`
  ).join("");
  const alts = (s.alternatives || []).map((a) => `<li>${esc(a)}</li>`).join("");
  el.innerHTML = `
    ${note ? `<div class="script-note">${esc(note)}</div>` : ""}
    <section class="script-section">
      <h3>Title options</h3>
      <ul class="script-titles">${titles || "<li>—</li>"}</ul>
    </section>
    <section class="script-section">
      <h3>Hook</h3>
      <p class="script-hook">${esc(s.hook || "")}</p>
    </section>
    <section class="script-section">
      <h3>Storyboard</h3>
      <table class="storyboard">
        <thead><tr><th>Time</th><th>Shot</th><th>Dialogue</th></tr></thead>
        <tbody>${board}</tbody>
      </table>
    </section>
    <section class="script-section">
      <h3>Call to action</h3>
      <p>${esc(s.cta || "")}</p>
    </section>
    <section class="script-section">
      <h3>Offer</h3>
      <p>${esc(s.offer || "")}</p>
    </section>
    <section class="script-section">
      <h3>Compliance</h3>
      <p>${esc(s.compliance || "")}</p>
    </section>
    <section class="script-section">
      <h3>Alternate angles</h3>
      <ul class="script-alts">${alts || "<li>—</li>"}</ul>
    </section>
  `;
}

function openModal(title) { $("modal-title").textContent = title; $("modal").classList.remove("hidden"); }
function closeModal() { $("modal").classList.add("hidden"); }

// ---------------------------------------------------------------------------
// Chat (full-page assistant, interview mode)
// ---------------------------------------------------------------------------

function pushMsg(text, who) {
  const log = $("chat-log");
  const m = document.createElement("div");
  m.className = "msg " + who;
  m.textContent = text;
  log.appendChild(m);
  log.scrollTop = log.scrollHeight;
}

async function sendChat() {
  const input = $("chat-msg");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  pushMsg(text, "user");
  state.chatHistory.push({ role: "user", text });
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        history: state.chatHistory.slice(0, -1),
        results: state.results || null,
      }),
    });
    const data = await r.json();
    pushMsg(data.reply || "(no reply)", "bot");
    state.chatHistory.push({ role: "assistant", text: data.reply || "" });
    if (data.campaign_input) {
      renderBriefPanel(data.campaign_input);
    }
    if (data.complete) {
      const first = !state.awaitingContinue;
      state.awaitingContinue = true;
      await runSearchTrends(first);
    }
  } catch (e) {
    pushMsg("Error: " + e.message, "bot");
  }
}

// ---------------------------------------------------------------------------
// Empty state + init
// ---------------------------------------------------------------------------

function sectionDesc(text) {
  return '<div class="empty-state"><p>' + text + '</p></div>';
}

function showEmptyState() {
  $("mode-badge").textContent = "idle";
  $("mode-badge").className = "badge badge-muted";

  $("budget-cards").innerHTML = sectionDesc("The best creator combo at 3 budget levels (70% / 100% / 130%).");
  $("insights").innerHTML = sectionDesc("Your best single match + the budget-optimized lineup, with reasons.");
  $("ranking").innerHTML = sectionDesc("Every shortlisted creator scored on 9 metrics, tagged data vs AI.");
  $("trending").innerHTML = sectionDesc("What's hot right now + which of your creators could ride it.");
  $("excluded").innerHTML = "";
}

function reset() {
  state.results = null;
  state.chatHistory = [];
  state.brief = {};
  state.awaitingContinue = false;
  state.useGemini = true;
  $("chat-log").innerHTML = "";
  renderBriefPanel({});
  renderTrends([], "");
  $("btn-continue").hidden = true;
  showEmptyState();
  showView("chat");
  pushMsg("Hi! Tell me about your brand or campaign and I'll build the brief, then generate your creator shortlist.", "bot");
}

$("btn-sample-top").addEventListener("click", runDemo);
$("btn-reset").addEventListener("click", reset);
$("btn-continue").addEventListener("click", () => startGenerating(false, state.useGemini));
$("chat-send").addEventListener("click", sendChat);
$("chat-msg").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
$("modal-close").addEventListener("click", closeModal);
$("modal-print").addEventListener("click", () => window.print());
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });

buildWeights();
showEmptyState();
renderBriefPanel({});
showView("chat");
pushMsg("Hi! Tell me about your brand or campaign and I'll build the brief, then generate your creator shortlist.", "bot");
