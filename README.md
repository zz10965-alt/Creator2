# Creator Shortlisting - conversational AI recommendation tool

NYU SPS x Google Hackathon | Track 2 (Product & Engineering)

A brand enters a brief (via a form or a Gemini chat interviewer) and the tool runs the
whole flow automatically - **discover -> fetch data -> score -> rank -> budget optimization ->
AI recommendation** - compressing hours of manual shortlisting into minutes.

Built end-to-end on Google products: **Gemini** (qualitative scoring + narrative),
**YouTube Data API v3** (discovery + data), **Google Charts** (frontend charts).

## Quick start

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

Then open **http://localhost:8000**. The web app has:
- a **chat assistant** (embedded above the brief) that collects the brief conversationally
  and then explains the results,
- a **ranking table** with per-metric scores and **data / AI inferred** badges,
- **budget options** (under / at / over) with a comparison chart,
- a **structured recommendation** (best single match / recommended lineup / risks),
- a **trending today** strip matched to your shortlist,
- per-creator **score chart** and **"generate script"** buttons.

**CLI fallback (still works):** `python main.py`

## Environment variables (`.env`, committed to this private repo)

```bash
YOUTUBE_API_KEY=your_YouTube_Data_API_v3_key
GEMINI_API_KEY=            # optional; fill in for qualitative scoring + chat + insights
```

- `YOUTUBE_API_KEY` set -> **LIVE** mode (real data); missing -> **DEMO** mode (built-in samples).
- `GEMINI_API_KEY` set -> qualitative scoring, chat interviewer, and insight narrative use
  Gemini; missing -> keyword fallback + deterministic summaries (the app still runs).

## Metric model

**Core weighted score** - every sub-score and the total are on **0-100**. The weighted total
is normalized by the sum of the weights, so the sliders don't have to add up to 100.

| Dimension | Metric | How it's computed | Weight (default) | Source |
|---|---|---|---|---|
| Content fit | Content Match | Gemini: tone / topic / audience / format vs. brief | 20 | AI |
| Engagement | Engagement Rate | Median `(likes + comments) / views` | 18 | data |
| Growth | View Momentum | Mean views last 30d / prior 30d | 12 | data |
| Stability | Engagement Stability | CV of engagement rate (smaller = better) | 12 | data |
| Audience | Audience Fit | Gemini: audience vs. target | 12 | AI |
| Reliability | Upload Consistency | CV of upload interval (smaller = better) | 8 | data |
| Quality | Content Quality | Gemini: packaging/hook/title from metadata only | 8 | AI |
| Length | Ad-length Match | closeness of median video length to desired ad length | 5 | data |
| Fans | Audience Sentiment | `fans / (fans + haters)` from comment classification | 5 | AI |

The default weights sum to 100 and every dimension is non-zero; slide any of them to retune.
The weighted total is still normalized by the sum, so the sliders don't have to add up to 100.
"Source" drives the **data / AI inferred** badge on each metric.

Quantitative metrics (engagement, growth, stability, consistency) are min-max normalized
across the candidate pool; the CV-based ones are **reversed** (smaller CV -> higher score).
`content_match`, `audience_fit`, `content_quality` come straight from Gemini (0-100);
`ad_length_match` is absolute 0-100; `audience_sentiment` is the positive ratio (0-100).

**Hard cutoffs (exclusion, not weighted)** - each excluded creator appears in
`results["excluded"]` with a reason. All thresholds are configurable in `config.py`.

| Cutoff | Rule | Default threshold |
|---|---|---|
| Brand safety | LLM risk score too high | `risk_score >= 70` |
| Competitor conflict | Creator mentions a competitor | on |
| Inactive | No recent upload | `> 60` days since last upload |
| Low reach | Average views too low | `avg_views < 1000` |
| Off-topic | Content doesn't match the brief | `content_match < 20` |
| Wrong audience | Audience doesn't match the target | `audience_fit < 20` |
| Wrong format | No videos match the requested format | new in v2 |

## Format & ad length (v2)

`campaign_input.format` (`long` / `short` / `any`, default `long`) filters which videos count
toward scoring and pricing. `desired_ad_length` (seconds) drives the ad-length match metric.
`shorts_ratio` (fraction of a channel's videos <= 60s) and `reach_ratio`
(`avg_views / subscribers`, flags possible dead/bought channels) are also exported.

## Budget / value layer

| Metric | Formula | Meaning |
|---|---|---|
| Estimated cost | `(avg_views / 1000) x CPM x premium` | price per sponsored video. CPM is a niche benchmark; premium = 1.15 if engagement beats the benchmark, 0.85 if under half, else 1.0 |
| Effective value | `avg_views x (ER / benchmark) x (audience_fit / 100)` | expected campaign value |
| Value-per-dollar | `effective_value / estimated_cost` | efficiency |
| Portfolio | 0/1 knapsack: maximize `sum(total_score x effective_value)` within budget, up to k creators | the optimal set for a budget |

`budget_scenarios` returns three knapsack runs - **under (70%) / at / over (130%)** - each with
`picks / total_cost / expected_views / expected_engagements`. The "why over-budget is worth it"
explanation is written by Gemini in the recommendation panel.

Niche CPM benchmarks ($ per 1,000 views), tunable in `config.py`: beauty 12, tech 28,
finance 30, gaming 8, fashion 15, food 10, fitness 12, default 15.

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | serves `static/index.html` |
| `/api/campaign` | POST | run discover -> score -> rank -> budget, returns `results` |
| `/api/chat` | POST | one chat turn; returns `{reply, campaign_input}` |
| `/api/insights` | POST | structured recommendation (best match + lineup + risks) |
| `/api/trending` | GET | today's trending videos |
| `/api/trending/match` | POST | match trending videos to shortlisted creators |
| `/api/script` | POST | generate a collaboration script for one creator |

## Files

| File | Purpose |
|---|---|
| `app.py` | FastAPI entry point (serves frontend + JSON API) |
| `static/` | frontend: `index.html` + `style.css` + `app.js` (vanilla JS + Google Charts) |
| `main.py` | CLI entry point (fallback) |
| `pipeline.py` | Orchestration: score -> cutoff -> rank -> budget -> sentiment |
| `youtube_api.py` | YouTube Data API v3: discover / data / comments / trending |
| `scoring.py` | Quantitative metrics + format / ad-length / reach helpers |
| `llm.py` | Gemini qualitative scoring + keyword fallback |
| `pricing.py` | Pricing + value-per-dollar + 0/1 knapsack + budget scenarios |
| `sentiment.py` | Comment sentiment -> fans/haters/positive ratio |
| `chat.py` | Gemini chat interviewer that collects the brief |
| `insights.py` | Recommendation narrative + script + trending match |
| `mock.py` | Sample data (DEMO mode) |
| `config.py` | Weights / thresholds / CPM benchmarks / keys |

## Data vs AI inferred

Every metric has a known origin, and the frontend renders a badge per metric (and the
`results["sources"]` map exposes it):

- **data** - from the YouTube API or plain math: engagement, growth, stability, consistency,
  ad-length match, views, subscribers, cost, value, budget numbers.
- **AI inferred** - from Gemini: content match, audience fit, content quality, risk score,
  competitor conflict, comment sentiment, and all narrative text.

## Positioning / differentiation

We don't go head-to-head with Google's YouTube Creator Partnerships (BrandConnect, a
black-box with enterprise private signals). Three differentiators:
1. **Transparent & explainable** - every score and price assumption can be broken down, and
   we label what is real data vs AI inference.
2. **Conversational** - a Gemini interviewer collects the brief, and a Gemini panel explains
   *why*, not just *what*.
3. **For SMBs / indie agencies** - they lack Google's enterprise signals; public data +
   benchmark estimates serve exactly that market.
