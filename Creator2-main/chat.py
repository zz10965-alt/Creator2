"""Chat: a Gemini interviewer that collects the campaign brief conversationally.

Each turn the model returns JSON: {"reply": str, "campaign_input": dict | null}.
campaign_input is non-null only once the key fields are collected; the frontend
auto-fills the form from it and fires the analysis.
"""
import config
import llm

SYSTEM_PROMPT = (
    "You are a friendly, natural assistant interviewing a brand to build a YouTube "
    "creator shortlist. Converse like a real person, not a form: open with a warm "
    "question, then follow up based on what they say. If they give extra detail, "
    "ask about the gaps instead of re-asking. Never list several questions at once - "
    "ask one natural question at a time and react to their answer.\n"
    "You are collecting these campaign-brief fields: brief (one line), keywords "
    "(3-6), target_audience, budget_cap (USD), target_market (the country/region "
    "the campaign is aimed at), and optionally competitors, risk_topics, "
    "desired_ad_length (seconds), format (long/short/any), target_k.\n"
    "Ask where the campaign is targeting geographically (which market/region). If "
    "they answer with only a country, follow up and ask which specific city or "
    "region — a broad market changes which creators are relevant.\n"
    "On EVERY reply return ONLY JSON (no markdown fences) with:\n"
    '  "reply": your natural message to the user,\n'
    '  "campaign_input": the PARTIAL brief with whatever fields you have collected '
    "so far (omit fields you don't know yet; use \"\" or [] for empty),\n"
    '  "complete": true ONLY when brief + keywords + target_audience + budget_cap + '
    "target_market are all collected, else false.\n"
    "campaign_input shape: {brief: str, keywords: [str], target_audience: str, "
    "budget_cap: int, target_market: str, competitors: [str], risk_topics: [str], "
    "desired_ad_length: int, format: str, target_k: int}."
)


RESULTS_PROMPT = (
    "The user has already run an analysis and is now asking about the results. "
    "Use the shortlist summary below to answer their question in plain English. "
    "Explain *why* a creator ranks where they do using the numbers (engagement %, "
    "growth, cost, views), not just what the number is. Keep it to a few sentences. "
    "Do not invent data that is not in the summary.\n\n"
    "Shortlist summary:\n"
)


def chat(message, history=None, results=None):
    """One turn. Two modes:
       * results is None  -> interview mode: collect the brief, return campaign_input.
       * results is set   -> explain mode: answer questions about a finished run.
    Returns {"reply": ..., "campaign_input": ... or None}.
    """
    if not config.GEMINI_API_KEY:
        return {"reply": "Please set GEMINI_API_KEY in .env to enable the AI assistant.",
                "campaign_input": None}
    try:
        client = llm.make_client()

        if results:
            system = RESULTS_PROMPT + _summarize(results)
            ack = "Understood. I will explain the results using only the summary."
        else:
            system = SYSTEM_PROMPT
            ack = "Understood. I will ask one question at a time."

        contents = [
            {"role": "user", "parts": [{"text": system}]},
            {"role": "model", "parts": [{"text": ack}]},
        ]
        for h in (history or []):
            role = "user" if h.get("role") == "user" else "model"
            contents.append({"role": role, "parts": [{"text": str(h.get("text", ""))}]})
        contents.append({"role": "user", "parts": [{"text": str(message)}]})
        resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=contents)
        text = (resp.text or "").strip()

        # Explain mode: the model answers in plain English (no JSON contract).
        if results:
            return {"reply": text, "campaign_input": None}

        # Interview mode: the model returns {"reply", "campaign_input", "complete"} JSON.
        data = llm._parse_json(text)
        return {"reply": str(data.get("reply", "")),
                "campaign_input": _coerce(data.get("campaign_input")),
                "complete": bool(data.get("complete"))}
    except Exception as e:
        return {"reply": f"[chat] could not reach Gemini: {e}", "campaign_input": None}


def _summarize(results):
    """Compact, text-only digest of a finished run for the explain-mode system prompt."""
    import json
    creators = results.get("creators", [])
    rows = []
    for r in creators[:8]:
        raw = r.get("raw", {})
        er = raw.get("engagement_rate")
        rows.append({
            "handle": r["handle"], "rank": r["rank"], "score": r["total"],
            "avg_views": r["avg_views"], "estimated_cost": r["estimated_cost"],
            "subscribers": r["subscribers"],
            "engagement_rate": f"{er*100:.1f}%" if er is not None else None,
            "growth_ratio": raw.get("growth_ratio"),
            "upload_interval_days": raw.get("upload_interval_days"),
            "rationale": r.get("rationale", ""),
            "risk": r.get("flags", {}).get("risk"),
        })
    budget = results.get("budget_scenarios", {}).get("at")
    return json.dumps({
        "shortlisted": rows,
        "budget_cap": results.get("brief", {}).get("budget_cap"),
        "recommended_lineup": budget,
    }, ensure_ascii=True, default=str)


def _coerce(ci):
    """Light validation of the model's campaign_input so the frontend/form gets sane types."""
    if not isinstance(ci, dict):
        return None
    out = dict(ci)
    if not isinstance(out.get("keywords"), list):
        out["keywords"] = [str(out.get("keywords", ""))] if out.get("keywords") else []
    out["keywords"] = [str(k) for k in out["keywords"]]
    try:
        out["budget_cap"] = int(float(out.get("budget_cap", 0)))
    except Exception:
        out["budget_cap"] = 0
    fmt = str(out.get("format", "")).lower()
    out["format"] = fmt if fmt in ("long", "short", "any") else None
    for list_key in ("competitors", "risk_topics"):
        if not isinstance(out.get(list_key), list):
            out[list_key] = []
    return out
