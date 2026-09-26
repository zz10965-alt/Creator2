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
function fmtNum(n) {
  n = Number(n);
  if (!isFinite(n)) n = 0;
  const abs = Math.abs(n);
  if (abs >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
  return String(Math.round(n));
}
function fmtMoney(n) {
  n = Number(n);
  if (!isFinite(n)) n = 0;
  return "$" + Math.round(n).toLocaleString();
}
function srcTag(src) {
  return src === "data"
    ? '<span class="src src-youtube" title="From YouTube / data">●</span>'
    : '<span class="src src-ai" title="AI inferred">✦</span>';
}
const AVATAR_COLORS = ["#1A73E8", "#188038", "#D93025", "#B06000", "#7B1FA2", "#0D7D6E"];
function avatarTint(h) {
  let n = 0;
  const s = String(h || "");
  for (let i = 0; i < s.length; i++) n = (n * 31 + s.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[n % AVATAR_COLORS.length];
}
function avatar(h, size) {
  const color = avatarTint(h);
  const name = String(h || "").replace(/^@/, "").trim();
  const parts = name.split(/[\s_\-\.]+/).filter(Boolean);
  const ini = parts.length >= 2 ? (parts[0][0] + parts[1][0]) : name.slice(0, 2);
  return `<span class="avatar ${size || ""}" style="background:${color}1a;color:${color}">${esc((ini || "?").toUpperCase())}</span>`;
}

function showView(name) {
  $("view-chat").classList.toggle("hidden", name !== "chat");
  $("view-waiting").classList.toggle("hidden", name !== "waiting");
  $("view-results").classList.toggle("hidden", name !== "results");
}

function setMode(label, kind) {
  const el = $("mode-badge");
  el.textContent = label;
  el.className = "badge " + (kind === "live" ? "badge-live" : kind === "demo" ? "badge-demo" : "badge-muted");
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
  { key: "brief", label: "Brief", group: "Campaign" },
  { key: "keywords", label: "Keywords", group: "Campaign" },
  { key: "target_audience", label: "Target audience", group: "Audience" },
  { key: "target_market", label: "Target market", group: "Market" },
  { key: "budget_cap", label: "Budget", group: "Budget" },
  { key: "target_k", label: "Creators", group: "Budget" },
  { key: "format", label: "Format", group: "Format" },
  { key: "desired_ad_length", label: "Ad length", group: "Format" },
  { key: "competitors", label: "Competitors", group: "Guardrails" },
  { key: "risk_topics", label: "Risk topics", group: "Guardrails" },
];

function fmtBriefValue(f) {
  const v = state.brief ? state.brief[f.key] : undefined;
  if (v == null || v === "") return "";
  if (Array.isArray(v)) {
    const a = v.filter(Boolean);
    return a.length ? a.join(" · ") : "";
  }
  if (typeof v === "number" && v === 0) return "";
  if (f.key === "budget_cap") return "$" + Number(v).toLocaleString();
  if (f.key === "desired_ad_length") return v + " sec";
  if (f.key === "format") return ({ long: "Long-form", short: "Shorts", any: "Any" })[String(v).toLowerCase()] || String(v);
  return String(v).trim();
}

function renderBriefPanel(ci) {
  state.brief = ci || {};
  const el = $("brief-live");
  const groups = {};
  BRIEF_FIELDS.forEach((f) => {
    const v = fmtBriefValue(f);
    if (!v) return;
    (groups[f.group] = groups[f.group] || []).push({ f, v });
  });
  const names = Object.keys(groups);
  if (!names.length) {
    el.innerHTML = '<p class="hint" style="padding:10px 0">Tell me about your brand to start building the brief.</p>';
    return;
  }
  el.innerHTML = names.map((g) => {
    const items = groups[g].map(({ f, v }) =>
      `<div class="brief-item"><span class="brief-k">${esc(f.label)}</span><span class="brief-v">${esc(v)}</span></div>`
    ).join("");
    return `<div class="brief-group"><span class="brief-group-label">${esc(g)}</span>${items}</div>`;
  }).join("");
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

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

async function renderAll(results, brief) {
  setMode(results.mode === "LIVE" ? "Live data" : "Sample data", results.mode === "LIVE" ? "live" : "demo");

  const analyzed = (results.creators || []).length + (results.excluded || []).length;
  const qualified = (results.creators || []).length;
  let ctx = `${qualified} of ${analyzed} creators qualified`;
  if (brief.budget_cap) ctx += ` · ${fmtMoney(brief.budget_cap)} campaign`;
  $("results-context").textContent = ctx;

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
    renderOverview(ins, results, brief);
    renderInsightsTab(ins);
    renderBudgetAlts(ins);
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

// ---- Overview tab: hero + campaign summary + lineup ----

function renderOverview(ins, results, brief) {
  $("hero").innerHTML = ins.best_pick ? renderHero(ins.best_pick) : sectionDesc("The top creator recommendation will appear here.");
  $("campaign-summary").innerHTML = renderCampaignSummary(results, ins, brief);
  $("lineup").innerHTML = ins.lineup ? renderLineup(ins.lineup) : "";
}

function renderHero(bp) {
  return `<div class="hero-card">
    <div class="hero-top">
      ${avatar(bp.handle, "lg")}
      <div class="hero-id">
        <span class="hero-rank">Top recommendation</span>
        <div class="hero-name">${esc(bp.handle)}</div>
        ${bp.positioning ? `<p class="hero-pos">${esc(bp.positioning)}</p>` : ""}
      </div>
      <div class="hero-score"><b>${bp.total}</b><span>/100</span></div>
    </div>
    <div class="hero-stats">
      <div class="hero-stat"><b>${fmtNum(bp.subscribers)}</b><span>subscribers</span></div>
      <div class="hero-stat"><b>${fmtNum(bp.avg_views)}</b><span>avg views</span></div>
      <div class="hero-stat"><b>${fmtMoney(bp.estimated_cost)}</b><span>per video</span></div>
    </div>
    <div class="hero-block">
      <span class="hero-block-label">Why chosen</span>
      <p>${esc(bp.why)}</p>
    </div>
    <div class="hero-actions">
      <button class="btn btn-primary" onclick='showScriptFor("${esc(bp.handle)}")'>Generate script</button>
    </div>
  </div>`;
}

function renderCampaignSummary(results, ins, brief) {
  const at = (results.budget_scenarios && results.budget_scenarios.at) || {};
  const kpis = [
    [fmtMoney(at.total_cost), "Recommended spend"],
    [fmtNum(at.expected_views), "Expected views"],
    [fmtNum(at.expected_engagements), "Expected engagements"],
    [String((results.creators || []).length), "Creators shortlisted"],
  ];
  return `<h3>Campaign at a glance</h3>
    <div class="kpi-grid">${kpis.map(([b, s]) => `<div class="kpi"><b>${b}</b><span>${s}</span></div>`).join("")}</div>`;
}

function renderLineup(lineup) {
  const members = lineup.members.map(renderMember).join("");
  return `<section class="lineup">
    <div class="lineup-head">
      <h3>Recommended lineup</h3>
      <span class="sub">${fmtMoney(lineup.total_cost)} total · ${fmtNum(lineup.expected_views)} expected views</span>
    </div>
    ${members}
  </section>`;
}

function renderMember(m) {
  return `<div class="member-row">
    ${avatar(m.handle, "sm")}
    <div class="member-id">
      <div class="member-name">${esc(m.handle)}</div>
      ${m.positioning ? `<div class="member-pos">${esc(m.positioning)}</div>` : ""}
    </div>
    <span class="member-score">${m.total}<span style="font-size:11px;color:var(--text-subtle)">/100</span></span>
    <div class="member-meta"><b>${fmtNum(m.subscribers)}</b>subscribers</div>
    <div class="member-meta"><b>${fmtNum(m.avg_views)}</b>avg views</div>
    <div class="member-meta"><b>${fmtMoney(m.estimated_cost)}</b>est. cost</div>
  </div>`;
}

// ---- Creators tab: ranking with progressive disclosure ----

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
  el.innerHTML = creators.map((c) => renderRankCard(c, sources)).join("");
}

function metricBar(label, score, src) {
  const v = Math.max(0, Math.min(100, Math.round(score || 0)));
  return `<div class="mini-meter">
    <div class="mini-lbl"><span>${esc(label)} ${srcTag(src)}</span><span class="mini-val">${v}</span></div>
    <div class="mini-bar"><i style="width:${v}%"></i></div>
  </div>`;
}

function renderRankCard(c, sources) {
  const topKeys = ["content_match", "audience_fit", "engagement"];
  const topBars = topKeys.map((k) => {
    const v = c.scores[k] != null ? c.scores[k] : 0;
    const label = (METRICS.find((m) => m.key === k) || { label: k }).label;
    return metricBar(label, v, sources[k]);
  }).join("");
  const allBars = METRICS.map((m) => {
    const v = c.scores[m.key] != null ? c.scores[m.key] : 0;
    return metricBar(m.label, v, sources[m.key]);
  }).join("");

  const flags = [];
  if (c.flags && c.flags.low_reach_ratio) flags.push('<span class="tag">low reach ratio</span>');
  if (c.shorts_ratio > 0.5) flags.push(`<span class="tag">${Math.round(c.shorts_ratio * 100)}% Shorts</span>`);
  if (c.sentiment && c.sentiment.sample_size > 0) {
    flags.push(`<span class="tag">fans ${c.sentiment.fans_pct}% / haters ${c.sentiment.haters_pct}%</span>`);
  }

  return `<div class="rank-card">
    <div class="rank-head">
      <span class="rank-no">#${String(c.rank).padStart(2, "0")}</span>
      ${avatar(c.handle)}
      <div class="rank-id">
        <div class="rank-name">${esc(c.handle)}</div>
        ${c.positioning ? `<div class="rank-pos">${esc(c.positioning)}</div>` : ""}
      </div>
      <div class="rank-score"><b>${c.total}</b><span>/100</span></div>
    </div>
    <div class="rank-stats">${fmtNum(c.subscribers)} subscribers · ${fmtNum(c.avg_views)} avg views · ${fmtMoney(c.estimated_cost)} est. per video</div>
    <div class="rank-key">${topBars}</div>
    <details class="score-profile">
      <summary>Full score profile</summary>
      <div class="score-grid">${allBars}</div>
      <div class="rank-tags">${flags.join("")}
        <button class="btn btn-ghost-sm" onclick="showScoreChart(${c.rank - 1})">Score chart</button>
      </div>
    </details>
  </div>`;
}

// ---- Budget tab ----

function renderBudget(scenarios, budget) {
  const el = $("budget-cards");
  if (!scenarios || !scenarios.at) {
    el.innerHTML = sectionDesc("Budget scenarios at three levels will appear after a run.");
    return;
  }
  const labels = { under: "Lean", at: "Recommended", over: "Expanded" };
  const notes = { under: "70% of budget", at: "Your budget", over: "130% of budget" };
  const rows = [];
  const chartData = [["Scenario", "Cost", "Views"]];
  Object.keys(labels).forEach((key) => {
    const s = scenarios[key];
    if (!s) return;
    chartData.push([labels[key], s.total_cost, s.expected_views]);
    const featured = key === "at";
    rows.push(`<div class="bcard ${featured ? "featured" : ""}">
      ${featured ? '<span class="bcard-badge">Recommended</span>' : ""}
      <h3>${labels[key]}</h3>
      <div class="big">${fmtMoney(s.total_cost)}</div>
      <div class="sub">${notes[key]}<br>${s.picks.map(esc).join(", ")}<br>${fmtNum(s.expected_views)} expected views · ${fmtNum(s.expected_engagements)} engagements</div>
    </div>`);
  });
  el.innerHTML = rows.join("") + '<div id="budget-chart" style="height:220px;margin-top:8px"></div>';
  if (chartData.length > 1) drawBudgetChart(chartData);
}

function renderBudgetAlts(ins) {
  const el = $("budget-alts");
  if (!ins || (!ins.lineup_under && !ins.lineup_over)) { el.innerHTML = ""; return; }
  const alt = (label, l) => {
    if (!l) return `<div class="alt-card muted">${label}: no combo fits this budget.</div>`;
    const picks = l.members.map((m) => m.handle).join(", ");
    return `<div class="alt-card"><b>${label}</b> &mdash; ${esc(picks)}<br>
      <span class="alt-sub">${fmtMoney(l.total_cost)} / ${fmtNum(l.expected_views)} views / ${fmtNum(l.expected_engagements)} engagements</span></div>`;
  };
  el.innerHTML = `<section class="rec-section">
    <h3>Budget alternatives</h3>
    ${alt("Lean (70%)", ins.lineup_under)}
    ${alt("Expanded (130%)", ins.lineup_over)}
  </section>`;
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
      series: { 1: { type: "line", targetAxisIndex: 1, color: "#188038", lineWidth: 3, pointSize: 5 } },
      colors: ["#1A73E8"],
      legend: { position: "top", textStyle: { color: "#5F6368" } },
      hAxis: { textStyle: { color: "#5F6368" } },
      vAxes: {
        0: { title: "Cost (USD)", format: "$#,###", titleTextStyle: { color: "#1A73E8", italic: false }, textStyle: { color: "#5F6368" }, gridlines: { color: "#DADCE0" } },
        1: { title: "Views", format: "short", titleTextStyle: { color: "#188038", italic: false }, textStyle: { color: "#5F6368" }, gridlines: { color: "transparent" } },
      },
      chartArea: { width: "72%", height: "66%" },
    });
  });
}

// ---- Insights tab ----

function renderInsightsTab(data) {
  const el = $("insights");
  if (!data) {
    el.innerHTML = sectionDesc("AI observations, risks, and next steps will appear here.");
    return;
  }
  let html = "";
  if (data.ai_take) {
    html += `<div class="ai-take"><span class="tag">✦ AI take</span><p style="margin:0">${inline(esc(data.ai_take))}</p></div>`;
  }
  if (data.risks && data.risks.length) {
    html += `<section class="rec-section"><h3>Risks &amp; flags</h3>
      <ul class="risks">${data.risks.map((r) => `<li>${esc(r.handle)}: ${esc(r.risk)}</li>`).join("")}</ul></section>`;
  }
  if (!data.ai_take && !(data.risks && data.risks.length)) {
    html += sectionDesc("AI observations, risks, and next steps will appear here.");
  }
  if (data.next_step) html += `<p class="next-step">${esc(data.next_step)}</p>`;
  el.innerHTML = html;
}

// ---- Trending ----

function renderTrending(trending, matches) {
  const el = $("trending");
  if (!trending.length) { el.innerHTML = '<p class="hint" style="padding:12px 4px">Trending topics load after a run.</p>'; return; }
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
      colors: ["#1A73E8"],
      hAxis: { minValue: 0, maxValue: 100, textStyle: { color: "#5F6368" }, gridlines: { color: "#DADCE0" } },
      vAxis: { textStyle: { color: "#202124" } },
      chartArea: { width: "68%", height: "82%" },
      bar: { groupWidth: "62%" },
      annotations: { alwaysOutside: true, textStyle: { color: "#5F6368", fontSize: 11 } },
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
  setMode("Ready", "muted");
  $("results-context").textContent = "";
  $("hero").innerHTML = sectionDesc("The top creator recommendation will appear here.");
  $("campaign-summary").innerHTML = sectionDesc("Campaign KPIs will appear here after a run.");
  $("lineup").innerHTML = "";
  $("budget-cards").innerHTML = sectionDesc("Budget scenarios at three levels will appear here.");
  $("budget-alts").innerHTML = "";
  $("ranking").innerHTML = sectionDesc("Every shortlisted creator, scored and ranked.");
  $("excluded").innerHTML = "";
  $("insights").innerHTML = sectionDesc("AI observations, risks, and next steps.");
  $("trending").innerHTML = sectionDesc("What's trending and which creators can ride it.");
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

// ---------------------------------------------------------------------------
// Event wiring + init
// ---------------------------------------------------------------------------

$("btn-sample-top").addEventListener("click", runDemo);
$("btn-reset").addEventListener("click", reset);
$("btn-continue").addEventListener("click", () => startGenerating(false, state.useGemini));
$("chat-send").addEventListener("click", sendChat);
$("chat-msg").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
$("modal-close").addEventListener("click", closeModal);
$("modal-print").addEventListener("click", () => window.print());
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });

// Results tabs (presentation only — no new requests)
document.querySelectorAll("#results-tabs .tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll("#results-tabs .tab").forEach((x) => x.classList.toggle("active", x === t));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + t.dataset.tab));
  });
});

// Sample prompt chips (fill the composer, no request)
document.querySelectorAll("#suggestions .sug").forEach((s) => {
  s.addEventListener("click", () => {
    const input = $("chat-msg");
    input.value = s.dataset.prompt;
    input.focus();
  });
});

buildWeights();
showEmptyState();
renderBriefPanel({});
showView("chat");
pushMsg("Hi! Tell me about your brand or campaign and I'll build the brief, then generate your creator shortlist.", "bot");
