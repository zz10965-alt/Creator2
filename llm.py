"""Qualitative scoring via Gemini (when a key is set), with a keyword fallback.

Returns one fixed structure (4 LLM metrics + rationale), all scores on 0-100:
{content_match, risk_score, competitor_conflict, audience_fit, rationale}
"""
import json
import re

from config import GEMINI_API_KEY, GEMINI_MODEL


def make_client():
    """A Gemini client that fails fast (no retry backoff) on quota/availability errors.

    Without this, the SDK retries a 429 "quota exceeded" for ~10s before raising,
    which makes even DEMO mode hang. Failing fast lets every caller fall back to its
    deterministic path instantly instead.
    """
    from google import genai
    from google.genai import types
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )


def score_creator(creator, brief, use_gemini=True):
    """Score one creator's qualitative fit. Uses Gemini when a key is available,
    otherwise a keyword/synonym overlap heuristic.

    use_gemini=False forces the deterministic fallback (used by instant demo/sample
    runs so they cost no quota and never wait on the API).
    """
    if use_gemini and GEMINI_API_KEY:
        try:
            return _gemini_score(creator, brief)
        except Exception:
            return _keyword_fallback(creator, brief)
    return _keyword_fallback(creator, brief)


def _text(creator):
    """Concatenate the creator's recent video titles/descriptions/tags into one text blob."""
    parts = [creator.get("handle", "")]
    for v in creator["videos"][:20]:
        parts.append(v["title"] + " " + (v.get("description") or "") + " " +
                     " ".join(v.get("tags") or []))
    return "\n".join(parts)[:8000]


def _gemini_score(creator, brief):
    client = make_client()
    prompt = (
        "You are scoring a YouTube creator for a brand campaign. "
        "Campaign brief: " + brief.get("brief", "") + "\n"
        "Keywords: " + ", ".join(brief.get("keywords", [])) + "\n"
        "Competitors: " + ", ".join(brief.get("competitors", [])) + "\n"
        "Risk topics: " + ", ".join(brief.get("risk_topics", [])) + "\n\n"
        "Creator: " + creator["handle"] + "\n"
        "Recent video titles/descriptions/tags:\n" + _text(creator) + "\n\n"
        "Return ONLY a JSON object (no markdown fences) with: content_match (0-100), "
        "risk_score (0-100, 0 = safe), competitor_conflict (bool), "
        "audience_fit (0-100), content_quality (0-100), rationale (string), "
        "positioning (string - one line describing who this creator is, their content "
        "style/format, and who they reach). "
        "content_quality estimates packaging/hook/title quality from the metadata and "
        "engagement signals only (you have NOT watched the video - infer, do not claim "
        "visual quality)."
    )
    resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return _clamp(_parse_json(resp.text))


def _parse_json(text):
    """Robustly turn the model's reply into a dict (strip ``` fences, grab first {...})."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


def _clamp(data):
    """Clamp the numeric scores to [0, 100] and coerce types defensively."""
    out = dict(data)
    for k in ("content_match", "risk_score", "audience_fit", "content_quality"):
        if k in out and out[k] is not None:
            out[k] = max(0, min(100, round(float(out[k]), 2)))
    out["competitor_conflict"] = bool(out.get("competitor_conflict", False))
    out["rationale"] = str(out.get("rationale", ""))
    out["positioning"] = str(out.get("positioning", ""))
    return out


# Keyword -> synonym/stem expansion. In the fallback, each brief keyword maps to a
# set of signal words; hitting any one of them counts as matching that aspect, so
# "eco-friendly" still recognizes sustainable / earth / zero waste.
_ASPECT_SYNONYMS = {
    "skincare": ["skincare", "skin care", "skin", "cleanser", "serum", "moisturizer", "derm", "glow", "acne"],
    "beauty": ["beauty", "makeup", "cosmetic", "glam", "grooming"],
    "clean": ["clean", "natural", "organic", "non-toxic", "nontoxic", "toxin", "green", "pure"],
    "eco": ["eco", "sustainable", "sustainability", "zero waste", "zerowaste", "environment", "environmental", "planet", "earth", "recycle", "reusable", "plastic-free"],
    "friendly": ["friendly"],
    "vegan": ["vegan", "cruelty-free", "cruelty free", "cruelty"],
    "fashion": ["fashion", "style", "outfit", "clothing", "wear"],
    "food": ["food", "recipe", "cook", "meal", "eat"],
    "fitness": ["fitness", "workout", "exercise", "gym", "training"],
}


def _aspects(keywords):
    """Split the brief keywords into aspects, each carrying its synonym set.

    Known words (e.g. from _ASPECT_SYNONYMS) are kept regardless of length, so
    short-but-meaningful keywords like "AI" or "EV" still count. The length
    filter only applies to *unmapped* words, to keep meaningless fragments
    ("a", "of") out of the substring match.
    """
    aspects = []
    for k in keywords:
        for w in str(k).lower().replace("-", " ").split():
            if w in _ASPECT_SYNONYMS:
                aspects.append(_ASPECT_SYNONYMS[w])
            elif len(w) >= 3:
                aspects.append([w])
    return aspects


def _keyword_fallback(creator, brief):
    """No-Gemini fallback: score by keyword/synonym overlap in the creator's text."""
    text = _text(creator).lower()
    aspects = _aspects(brief.get("keywords", []))
    hits = sum(1 for syns in aspects if any(s in text for s in syns))
    ratio = hits / len(aspects) if aspects else 0.0
    content_match = round(min(100.0, ratio * 100.0), 1)
    audience_fit = round(min(100.0, ratio * 100.0), 1)

    risk_topics = [str(r).lower() for r in brief.get("risk_topics", [])]
    risk_hits = sum(1 for r in risk_topics if r in text)
    risk_score = min(100, risk_hits * 50)

    comps = [str(c).lower() for c in brief.get("competitors", [])]
    comp = any(c in text for c in comps)

    rationale = (f"[keyword-fallback] matched {hits}/{len(aspects)} aspects, "
                 f"{risk_hits} risk topics, competitor mention: {comp}")
    kws = ", ".join(brief.get("keywords", [])[:2]) or "topic"
    positioning = (f"Creates {kws}-focused content for a "
                   f"{brief.get('target_audience', 'broad')} audience.")
    return {"content_match": content_match, "risk_score": risk_score,
            "competitor_conflict": comp, "audience_fit": audience_fit,
            "content_quality": 50,  # neutral default: no real signal without Gemini
            "rationale": rationale, "positioning": positioning}
