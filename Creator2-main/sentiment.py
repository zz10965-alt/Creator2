"""Comment sentiment: classify a creator's recent comments into fans / neutral / haters.

The result is AI-inferred (Gemini classification, or a deterministic keyword heuristic
when no key is set). It is used for display + risk narrative only - never a hard cutoff.
"""
import config
import llm


def score(comments, use_gemini=True):
    """Aggregate sentiment for a creator's comment sample.

    Returns {"fans_pct", "haters_pct", "neutral_pct", "positive_ratio",
             "sentiment_score", "sample_size"}. All percentages are 0-100.

    use_gemini=False forces the deterministic keyword heuristic (instant demo).
    """
    comments = [c for c in (comments or []) if str(c).strip()]
    if not comments:
        return _aggregate(0, 0, 0)
    if use_gemini and config.GEMINI_API_KEY:
        try:
            fans, neutral, haters = _gemini_classify(comments)
            return _aggregate(fans, neutral, haters)
        except Exception:
            return _heuristic(comments)
    return _heuristic(comments)


def _aggregate(fans, neutral, haters):
    total = fans + neutral + haters
    if total == 0:
        return {"fans_pct": 0.0, "haters_pct": 0.0, "neutral_pct": 0.0,
                "positive_ratio": 50.0, "sentiment_score": 50.0, "sample_size": 0}
    denom = fans + haters
    positive_ratio = round(fans / denom * 100.0, 1) if denom else 50.0
    return {
        "fans_pct": round(fans / total * 100.0, 1),
        "haters_pct": round(haters / total * 100.0, 1),
        "neutral_pct": round(neutral / total * 100.0, 1),
        "positive_ratio": positive_ratio,
        "sentiment_score": positive_ratio,
        "sample_size": total,
    }


_POS_WORDS = ["love", "great", "best", "amazing", "helpful", "thanks", "awesome",
              "works", "subscribed", "perfect", "nice", "wow", "good"]
_NEG_WORDS = ["boring", "hate", "terrible", "bad", "clickbait", "salesy", "ad",
              "stopped", "repetitive", "waste", "overrated", "awful", "disappointed"]


def _heuristic(comments):
    """Deterministic keyword heuristic fallback (no Gemini)."""
    fans = neutral = haters = 0
    for c in comments:
        t = str(c).lower()
        pos = any(w in t for w in _POS_WORDS)
        neg = any(w in t for w in _NEG_WORDS)
        if pos and not neg:
            fans += 1
        elif neg and not pos:
            haters += 1
        else:
            neutral += 1
    return _aggregate(fans, neutral, haters)


def _gemini_classify(comments):
    client = llm.make_client()
    text = "\n".join(f"{i + 1}. {c}" for i, c in
                     enumerate(comments[:config.MAX_COMMENTS_PER_CREATOR]))
    prompt = (
        "Classify each YouTube comment as POSITIVE, NEUTRAL, or NEGATIVE, from the "
        "perspective of a brand deciding whether to sponsor this creator. "
        "POSITIVE = a fan/supporter, NEGATIVE = a hater/hostile, NEUTRAL = neither.\n\n"
        "Comments:\n" + text + "\n\n"
        'Return ONLY JSON: {"fans": int, "neutral": int, "haters": int}'
    )
    resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
    data = llm._parse_json(resp.text)
    return (int(data.get("fans", 0)), int(data.get("neutral", 0)),
            int(data.get("haters", 0)))
