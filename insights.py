"""Insights: structured recommendation, script generation, and trending match.

The recommendation is returned as structured JSON (rendered as cards by the frontend),
not a markdown blob. The deterministic part cites raw data (engagement %, growth,
upload cadence, median duration, reach ratio); Gemini (when a key is set) adds a short
human "take" on top. All narrative text is flagged "AI inferred" via the sources map.
"""
import config
import llm


# ---------------------------------------------------------------------------
# Recommendation panel (structured JSON)
# ---------------------------------------------------------------------------

def generate(results, campaign_input, use_gemini=True):
    """Return a structured recommendation object for the frontend to render as cards.

    Two answers, kept clearly separate:
      * best_pick  -- "Best single match": the #1 by pure score, ignoring budget.
      * lineup     -- "Recommended lineup": the budget-optimized knapsack portfolio.
      * lineup_under / lineup_over -- the same portfolio at 70% / 130% of budget.
      * risks      -- flags on any shortlisted creator.
    """
    data = _structured(results, campaign_input)
    data["has_gemini"] = bool(use_gemini and config.GEMINI_API_KEY and not config.QUOTA_SAVER)
    if use_gemini and config.GEMINI_API_KEY and not config.QUOTA_SAVER:
        try:
            take = _gemini_take(results, campaign_input)
            if take:
                data["ai_take"] = take
        except Exception:
            pass
    return data


def _structured(results, campaign_input):
    creators = results.get("creators", [])
    scenarios = results.get("budget_scenarios") or {}

    out = {
        "best_pick": None,
        "lineup": None,
        "lineup_under": None,
        "lineup_over": None,
        "risks": [],
        "ai_take": None,
        "next_step": "Pick a creator and generate a collaboration script, "
                     "or check the Trending panel to ride today's topic.",
    }

    if not creators:
        out["next_step"] = ("No creators survived the cutoffs for this brief. "
                            "Try relaxing a threshold or broadening the keywords.")
        return out

    top = creators[0]
    desired = campaign_input.get("desired_ad_length")
    out["best_pick"] = {
        "handle": top["handle"],
        "rank": top["rank"],
        "total": top["total"],
        "avg_views": top["avg_views"],
        "estimated_cost": top["estimated_cost"],
        "subscribers": top["subscribers"],
        "positioning": top.get("positioning") or top.get("rationale", ""),
        "why": _why_single(top, desired),
    }

    out["lineup"] = _lineup(scenarios.get("at"), creators, desired)
    out["lineup_under"] = _lineup(scenarios.get("under"), creators, desired)
    out["lineup_over"] = _lineup(scenarios.get("over"), creators, desired)

    out["risks"] = _risks(creators)
    return out


def _lineup(scenario, creators, desired=None):
    """Turn a portfolio scenario into {total_cost, expected_views, members[]} or None."""
    if not scenario or not scenario.get("picks"):
        return None
    by_handle = {c["handle"]: c for c in creators}
    members = []
    for h in scenario["picks"]:
        r = by_handle.get(h)
        if not r:
            continue
        members.append(_member(r, desired))
    if not members:
        return None
    return {
        "total_cost": scenario["total_cost"],
        "expected_views": scenario["expected_views"],
        "expected_engagements": scenario["expected_engagements"],
        "members": members,
    }


def _member(r, desired=None):
    """Card data for one lineup member, with data-grounded facts and a detailed reason."""
    raw = r.get("raw", {})
    drivers = sorted(r["scores"].items(), key=lambda kv: kv[1], reverse=True)[:3]
    drivers = [k.replace("_", " ") for k, _ in drivers]

    facts = []
    er = raw.get("engagement_rate")
    if er is not None:
        facts.append(f"engagement {er * 100:.1f}%")
    growth = raw.get("growth_ratio")
    if growth is not None:
        facts.append(f"growth {growth:.1f}x")
    interval = raw.get("upload_interval_days")
    if interval:
        facts.append(f"uploads ~every {interval:.0f}d")
    dur = r.get("duration_median")
    if dur:
        facts.append(f"median video {dur:.0f}s")
    reach = r.get("reach_ratio")
    if reach is not None:
        facts.append(f"reach {reach:.1%} of subs")

    return {
        "handle": r["handle"],
        "total": r["total"],
        "estimated_cost": r["estimated_cost"],
        "avg_views": r["avg_views"],
        "subscribers": r["subscribers"],
        "duration_median": r.get("duration_median"),
        "drivers": drivers,
        "facts": facts,
        "positioning": r.get("positioning") or r.get("rationale", "") or ", ".join(drivers[:2]),
        "match": [
            {"label": "Content match", "score": r["scores"].get("content_match")},
            {"label": "Audience fit", "score": r["scores"].get("audience_fit")},
            {"label": "Content quality", "score": r["scores"].get("content_quality")},
            {"label": "Ad-length match", "score": r["scores"].get("ad_length_match")},
        ],
        "why": _why_member(r, drivers, desired),
    }


def _why_single(r, desired=None):
    """Detailed, data-grounded reason the #1 is the best pure match."""
    raw = r.get("raw", {})
    drivers = sorted(r["scores"].items(), key=lambda kv: kv[1], reverse=True)[:2]
    drv = " and ".join(f"{k.replace('_', ' ')} ({v:.0f}/100)" for k, v in drivers)
    s = f"Best overall fit: strongest on {drv}."
    er = raw.get("engagement_rate")
    if er is not None:
        s += f" Engagement {er * 100:.1f}% (benchmark {config.ER_BENCHMARK * 100:.0f}%)."
    growth = raw.get("growth_ratio")
    if growth is not None:
        s += f" Views up {growth:.1f}x month-over-month."
    interval = raw.get("upload_interval_days")
    if interval:
        s += f" Uploads ~every {interval:.0f} days."
    return s


def _why_member(r, drivers, desired=None):
    """A few sentences of data-grounded rationale for why this creator made the lineup."""
    raw = r.get("raw", {})
    sentences = []
    vpd = r.get("value_per_dollar", 0) or 0
    sentences.append(
        f"~${r['estimated_cost']:,.0f} per video for ~{r['avg_views']:,.0f} expected views "
        f"({vpd:.1f} effective value per dollar)."
    )
    bits = []
    er = raw.get("engagement_rate")
    if er is not None:
        bits.append(f"{er * 100:.1f}% engagement (benchmark {config.ER_BENCHMARK * 100:.0f}%)")
    growth = raw.get("growth_ratio")
    if growth is not None:
        bits.append(f"{growth:.1f}x month-over-month views")
    if bits:
        sentences.append("Performance: " + "; ".join(bits) + ".")
    cad = []
    interval = raw.get("upload_interval_days")
    if interval:
        cad.append(f"uploads ~every {interval:.0f} days")
    dur = r.get("duration_median")
    if dur:
        if desired:
            cad.append(f"median video {dur:.0f}s vs your {desired}s ad")
        else:
            cad.append(f"median video {dur:.0f}s")
    if cad:
        sentences.append("Cadence & format: " + ", ".join(cad) + ".")
    sentences.append("Strongest on " + ", ".join(drivers[:2]) + ".")
    return " ".join(sentences)


def _risks(creators):
    out = []
    for r in creators:
        bits = []
        if r["flags"].get("risk", 0) >= 60:
            bits.append(f"risk {r['flags']['risk']}/100")
        if r["flags"].get("low_reach_ratio"):
            bits.append("low reach ratio (possible dead/bought audience)")
        if bits:
            out.append({"handle": r["handle"], "risk": ", ".join(bits)})
    return out


def _gemini_take(results, campaign_input):
    """Gemini writes a 2-3 sentence executive take on the shortlist."""
    client = llm.make_client()
    compact = {
        "top": [{"handle": r["handle"], "total": r["total"],
                 "avg_views": r["avg_views"], "estimated_cost": r["estimated_cost"],
                 "risk": r["flags"].get("risk")}
                for r in results.get("creators", [])[:3]],
        "budget_cap": campaign_input.get("budget_cap"),
    }
    prompt = ("Here is a YouTube creator shortlist for a brand campaign. "
              "Write a 2-3 sentence executive take: who to pick and why, in plain English. "
              "Do not restate every number. Data:\n" + _json_compact(compact))
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
    return resp.text.strip()


# ---------------------------------------------------------------------------
# Script / brief generation
# ---------------------------------------------------------------------------

def generate_script(creator, campaign_input, concept=None, use_gemini=True):
    """Return {"script": <structured obj>, "has_gemini": bool} -- a collab script.

    The script is structured (title options / hook / storyboard table / cta / offer /
    compliance / alternatives) so the frontend can render a clean document instead of
    a markdown blob. On Gemini failure it falls back to a deterministic version and
    includes a "note" so the reason is visible instead of silently swallowed.
    """
    if use_gemini and config.GEMINI_API_KEY:
        try:
            return {"script": _normalize_script(_gemini_script(creator, campaign_input, concept)),
                    "has_gemini": True}
        except Exception as e:
            return {"script": _fallback_script(creator, campaign_input),
                    "has_gemini": False,
                    "note": "Gemini unavailable: " + " ".join(str(e).split())[:120]}
    return {"script": _fallback_script(creator, campaign_input), "has_gemini": False}


def _fallback_script(creator, campaign_input):
    """Deterministic structured script (same shape as _gemini_script) for no-key/error runs."""
    handle = str(creator.get("handle", "@creator"))
    brand = campaign_input.get("brief", "your brand")
    dur = campaign_input.get("desired_ad_length")
    dur_txt = f"{dur}s" if dur else "60s"
    code = handle.strip("@").upper()[:8]
    return {
        "title_options": [
            f"Why this {brand} routine actually stuck with me",
            f"I tried {brand} for two weeks - honest review",
            f"The one {brand} product I kept in my routine",
        ],
        "hook": f"Here's the one {brand} routine I actually keep - and why.",
        "storyboard": [
            {"time": "0:00-0:05", "shot": "Talking head, direct to camera",
             "dialogue": f"Here's the one {brand} routine I actually keep."},
            {"time": f"0:05-0:30", "shot": "B-roll: product in a real routine",
             "dialogue": "Show the product in use; focus on the result, not the ingredient list."},
            {"time": "0:30-0:45", "shot": "Close-up: product + honest take",
             "dialogue": "Name the brand once, naturally, and what changed for you."},
            {"time": f"0:45-{dur_txt}", "shot": "Talking head + on-screen link",
             "dialogue": f"Try it via the link below - my code is {code}."},
        ],
        "cta": f"Try it via the link below - my code is {code}.",
        "offer": "Mention the discount/code the brand provides.",
        "compliance": "This video is sponsored (#ad). Opinions are my own.",
        "alternatives": [
            "Angle B: a pure 'label read-through' explainer.",
            "Angle C: a blind test - don't name the brand until the end.",
        ],
    }


def _gemini_script(creator, campaign_input, concept=None):
    client = llm.make_client()
    dur = campaign_input.get("desired_ad_length")
    prompt = (
        "Write a complete YouTube sponsorship / collaboration script for this creator "
        "and brand. Return ONLY JSON (no markdown) with this exact structure:\n"
        '{"title_options": [3 short title ideas], "hook": "opening hook (0-5s)", '
        '"storyboard": [{"time": "0:00-0:05", "shot": "shot/camera note", "dialogue": "spoken line"}], '
        '"cta": "call to action", "offer": "promo / offer / code", '
        '"compliance": "sponsorship disclosure (#ad)", "alternatives": [2 alternate angles]}.\n'
        "Make the storyboard a real shot-by-shot table with time ranges, shot/camera "
        "notes, and the spoken dialogue. Match the requested length if given.\n"
        f"Creator: {creator.get('handle', '')} (avg views {creator.get('avg_views', 0):,.0f}).\n"
        f"Brand brief: {campaign_input.get('brief', '')}\n"
        f"Target audience: {campaign_input.get('target_audience', '')}\n"
        f"Keywords: {', '.join(campaign_input.get('keywords', []))}\n"
        f"Desired ad length: {dur if dur else 'not specified'} seconds."
    )
    if concept:
        ang = concept.get("angle", "") if isinstance(concept, dict) else str(concept)
        if ang:
            prompt += f"\nCreative angle (write the script around this): {ang}."
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
    return llm._parse_json(resp.text)


def _normalize_script(data):
    """Coerce Gemini's JSON into the expected script shape, with safe defaults."""
    if not isinstance(data, dict):
        data = {}
    def _str_list(v):
        return [str(x) for x in v] if isinstance(v, list) else []
    storyboard = []
    for row in (data.get("storyboard") if isinstance(data.get("storyboard"), list) else []):
        if isinstance(row, dict):
            storyboard.append({"time": str(row.get("time", "")),
                               "shot": str(row.get("shot", "")),
                               "dialogue": str(row.get("dialogue", ""))})
    return {
        "title_options": _str_list(data.get("title_options")),
        "hook": str(data.get("hook", "")),
        "storyboard": storyboard,
        "cta": str(data.get("cta", "")),
        "offer": str(data.get("offer", "")),
        "compliance": str(data.get("compliance", "")),
        "alternatives": _str_list(data.get("alternatives")),
    }


# ---------------------------------------------------------------------------
# Trending match
# ---------------------------------------------------------------------------

def match_trending(trending_videos, creators, campaign_input, use_gemini=True):
    """Return {"matches": [...], "has_gemini": bool} -- trending topics matched to found creators."""
    if use_gemini and config.GEMINI_API_KEY and not config.QUOTA_SAVER:
        try:
            return {"matches": _gemini_trending(trending_videos, creators), "has_gemini": True}
        except Exception:
            pass
    return {"matches": _fallback_trending(trending_videos, creators), "has_gemini": False}


def search_trends(campaign_input, use_gemini=True):
    """Gemini + Google Search grounding: identify current trends for the brief.

    Returns {"trends": [str], "has_gemini": bool, "note": str}. Gated by
    QUOTA_SAVER like the other automatic Gemini features; when off, or on any
    failure, it degrades to an empty list + a note instead of raising.
    """
    if use_gemini and config.GEMINI_API_KEY and not config.QUOTA_SAVER:
        try:
            client = llm.make_client()
            prompt = (
                "Using Google Search, identify 3-5 current trends or cultural moments "
                "relevant to this brand campaign. Be specific, recent, and actionable "
                "for a YouTube creator collab. Return ONLY JSON (no markdown): "
                '{"trends": ["trend one", "trend two"]}\n'
                f"Brief: {campaign_input.get('brief', '')}\n"
                f"Keywords: {', '.join(campaign_input.get('keywords', []))}\n"
                f"Target audience: {campaign_input.get('target_audience', '')}\n"
                f"Target market: {campaign_input.get('target_market', '')}"
            )
            from google.genai import types
            cfg = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())])
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL, contents=prompt, config=cfg)
            data = llm._parse_json(resp.text)
            trends = ([str(t) for t in data.get("trends", [])]
                      if isinstance(data, dict) else [])
            if trends:
                return {"trends": trends[:5], "has_gemini": True, "note": ""}
        except Exception as e:
            return {"trends": [], "has_gemini": False,
                    "note": "search unavailable: " + " ".join(str(e).split())[:120]}
    return {"trends": [], "has_gemini": False,
            "note": "Trend search is paused."}


def _fallback_trending(trending_videos, creators):
    """Keyword overlap between trending titles and each creator's handle/rationale."""
    creator_text = {c["handle"]: (c["handle"] + " " + c.get("rationale", "")).lower()
                    for c in creators}
    matches = []
    for t in trending_videos:
        title = t["title"].lower()
        for handle, text in creator_text.items():
            # overlap on any 4+ char word in the title
            if any(w in text for w in title.split() if len(w) >= 4):
                matches.append({"trending": t["title"], "creator": handle,
                                "reason": "keyword overlap"})
    return matches[:6]


def _gemini_trending(trending_videos, creators):
    client = llm.make_client()
    creator_list = [{"handle": c["handle"], "rationale": c.get("rationale", "")}
                    for c in creators[:10]]
    prompt = ("Match these trending YouTube videos to the creators in our shortlist. "
              "For each trending video, name ONE creator from the list who could make "
              "a similar video, and a one-line reason.\n"
              "Trending:\n" + _json_compact(trending_videos) + "\n"
              "Creators:\n" + _json_compact(creator_list) + "\n"
              'Return ONLY JSON: [{"trending": "...", "creator": "...", "reason": "..."}]')
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
    data = llm._parse_json(resp.text)
    return data if isinstance(data, list) else []


def _json_compact(obj):
    import json
    return json.dumps(obj, ensure_ascii=True, default=str)


# ---------------------------------------------------------------------------
# Concepts (creative angles) - generated on demand, before the script
# ---------------------------------------------------------------------------

def generate_concepts(creator, campaign_input, use_gemini=True):
    """Return {"concepts": [{angle, reason, title}], "has_gemini": bool} -- 2-3 angles.

    On-demand like generate_script: gated only by whether a key is set, not QUOTA_SAVER.
    Falls back to a deterministic template when Gemini is unavailable.
    """
    if use_gemini and config.GEMINI_API_KEY:
        try:
            return {"concepts": _normalize_concepts(_gemini_concepts(creator, campaign_input)),
                    "has_gemini": True}
        except Exception as e:
            return {"concepts": _fallback_concepts(creator, campaign_input),
                    "has_gemini": False,
                    "note": "Gemini unavailable: " + " ".join(str(e).split())[:120]}
    return {"concepts": _fallback_concepts(creator, campaign_input), "has_gemini": False}


def _gemini_concepts(creator, campaign_input):
    client = llm.make_client()
    prompt = (
        "Propose 2-3 creative collaboration angles (concepts) for this YouTube creator "
        "and brand. Each angle is the big idea BEFORE writing a script: the format / hook "
        "direction and why it fits this creator's style.\n"
        'Return ONLY JSON (no markdown): {"concepts": [{"angle": "...", "reason": "...", "title": "..."}]}.\n'
        f"Creator: {creator.get('handle', '')}\n"
        f"Brand brief: {campaign_input.get('brief', '')}\n"
        f"Target audience: {campaign_input.get('target_audience', '')}\n"
        f"Keywords: {', '.join(campaign_input.get('keywords', []))}"
    )
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
    return llm._parse_json(resp.text)


def _normalize_concepts(data):
    if not isinstance(data, dict):
        data = {}
    out = []
    for row in (data.get("concepts") if isinstance(data.get("concepts"), list) else []):
        if isinstance(row, dict):
            out.append({"angle": str(row.get("angle", "")),
                        "reason": str(row.get("reason", "")),
                        "title": str(row.get("title", ""))})
    return out[:3]


def _fallback_concepts(creator, campaign_input):
    brand = campaign_input.get("brief", "your brand")
    handle = creator.get("handle", "this creator")
    return [
        {"angle": "30-day real test",
         "reason": f"Honest, real-use angle that fits {handle}'s authentic style",
         "title": f"I tried {brand} for 30 days - honest results"},
        {"angle": "Ingredient / deep dive",
         "reason": "Educational, detail-heavy angle for a credibility play",
         "title": f"What's actually in {brand} - the label read-through"},
        {"angle": "Blind test",
         "reason": "High-contrast, shareable angle that hooks viewers",
         "title": f"Blind test: can I guess {brand} without seeing the label?"},
    ]
