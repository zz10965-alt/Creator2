"""Pipeline orchestration: brief -> score -> cutoff -> rank -> budget -> results."""
from datetime import datetime

import config
import scoring
import llm
import pricing
import sentiment

# Which metric is backed by real data (YouTube API / math) vs AI inference (Gemini).
# The frontend renders a "data" / "AI inferred" badge per metric from this map.
SOURCES = {
    "engagement": "data",
    "growth": "data",
    "stability": "data",
    "consistency": "data",
    "ad_length_match": "data",
    "content_match": "AI",
    "audience_fit": "AI",
    "content_quality": "AI",
    "audience_sentiment": "AI",
    "risk_score": "AI",
    "competitor_conflict": "AI",
}


def _assign_scores(kept, score_key, values, reverse=False):
    """Normalize a metric across the candidate pool to 0-100 and write it into each creator.

    None values (insufficient data, e.g. a CV with <2 samples) get the neutral score;
    the rest are min-max normalized together. reverse=True flips the direction for
    "smaller is better" metrics (CV-based stability/consistency).
    """
    indexed = [(i, v) for i, v in enumerate(values) if v is not None]
    if not indexed:
        out = [float(config.NEUTRAL_SCORE)] * len(values)
    else:
        normed = scoring.normalize_pool([v for _, v in indexed], 0.0, 100.0, reverse=reverse)
        out = [float(config.NEUTRAL_SCORE)] * len(values)
        for (i, _), n in zip(indexed, normed):
            out[i] = n
    for r, n in zip(kept, out):
        r.setdefault("scores", {})[score_key] = round(n, 2)


def _enrich_sentiment(creators, mode, use_gemini=True):
    """Fetch + classify comment sentiment for the given creators (in place).

    Sets r["sentiment"] (full aggregate) and r["audience_sentiment"] (0-100 score).
    DEMO uses mock comments; LIVE samples a few real comments via the YouTube API.
    """
    if not creators:
        return
    yt = None
    for r in creators:
        vids = r.get("_video_ids") or []
        comments = []
        if mode == "LIVE":
            try:
                import youtube_api
                if yt is None:
                    yt = youtube_api._youtube()
                comments = youtube_api.get_comments(yt, vids)
            except Exception:
                comments = []
        else:
            import mock
            comments = mock.get_comments(vids)
        r["sentiment"] = sentiment.score(comments, use_gemini=use_gemini)
        r["audience_sentiment"] = r["sentiment"]["sentiment_score"]


def run_pipeline(campaign_input, creators, mode="DEMO", use_gemini=None):
    if use_gemini is None:
        use_gemini = bool(config.GEMINI_API_KEY)
    now = datetime.now()
    kept, excluded = [], []

    # Quota-saver: only Gemini-score the biggest channels (by subscribers), so the
    # input order doesn't matter; the free data cutoffs still run on everyone first.
    if config.QUOTA_SAVER:
        creators = sorted(creators, key=lambda c: c.get("subscriber_count", 0), reverse=True)
    scored = 0

    # Per-request overrides: the frontend can pass "weights" / "thresholds" in
    # campaign_input (see README "Interface contract"). Anything not overridden
    # falls back to the config.py defaults.
    weights = {**config.WEIGHTS, **(campaign_input.get("weights") or {})}
    thresholds = {
        "risk_cutoff": config.RISK_CUTOFF,
        "exclude_competitor": config.EXCLUDE_COMPETITOR,
        "inactive_days": config.INACTIVE_DAYS,
        "min_avg_views": config.MIN_AVG_VIEWS,
        "min_content_match": config.MIN_CONTENT_MATCH,
        "min_audience_fit": config.MIN_AUDIENCE_FIT,
        **(campaign_input.get("thresholds") or {}),
    }

    # Format + ad length: which videos count toward scoring/price, and how close the
    # creator's median length is to the brand's desired ad length.
    fmt = (campaign_input.get("format") or config.DEFAULT_FORMAT).lower()
    desired = campaign_input.get("desired_ad_length")

    for c in creators:
        vids = scoring.filter_by_format(c["videos"], fmt)
        vids_full = c["videos"]

        # Raw quantitative metrics (the CV-based ones may be None on insufficient data).
        er = scoring.engagement_rate(vids)
        stab_cv = scoring.engagement_stability(vids)
        interval_mean, interval_std = scoring.upload_interval_stats(vids)
        interval_cv = scoring.upload_consistency(vids)
        growth = scoring.view_growth(vids, now)               # clipped, feeds the score
        growth_ratio = scoring.view_growth_ratio(vids, now)   # unclipped, for display
        avg_views = pricing.avg_recent_views({"videos": vids})
        days_since = scoring.days_since_last_upload(vids, now)
        median_dur = scoring.median_duration_seconds(vids)
        ad_match = scoring.ad_length_match(vids, desired)
        shorts_ratio = scoring.shorts_ratio(vids_full)
        reach = scoring.reach_ratio(avg_views, c["subscriber_count"])

        # --- Hard cutoffs, stage 1: data-driven (free, no LLM call) ---
        if not vids:
            excluded.append({"handle": c["handle"],
                             "reason": f"no {fmt}-form videos"})
            continue
        if days_since is not None and days_since > thresholds["inactive_days"]:
            excluded.append({"handle": c["handle"],
                             "reason": f"inactive ({days_since}d since last upload)"})
            continue
        if avg_views < thresholds["min_avg_views"]:
            excluded.append({"handle": c["handle"],
                             "reason": f"low_reach (avg {avg_views:,.0f} views)"})
            continue

        # --- Hard cutoffs, stage 2: LLM-based (need the qualitative score) ---
        # The LLM sees only the format-matched subset, so a long-form brand doesn't
        # get recommended a creator whose only matching content is Shorts.
        if config.QUOTA_SAVER and use_gemini and scored >= config.QUOTA_SAVER_CREATOR_MAX:
            excluded.append({"handle": c["handle"],
                             "reason": f"quota-saver (capped at {config.QUOTA_SAVER_CREATOR_MAX} creators)"})
            continue
        scored += 1
        llm_res = llm.score_creator({**c, "videos": vids}, campaign_input, use_gemini=use_gemini)
        if llm_res["risk_score"] >= thresholds["risk_cutoff"]:
            excluded.append({"handle": c["handle"],
                             "reason": f"brand_safety (risk={llm_res['risk_score']})"})
            continue
        if thresholds["exclude_competitor"] and llm_res["competitor_conflict"]:
            excluded.append({"handle": c["handle"], "reason": "competitor_conflict"})
            continue
        if llm_res["content_match"] < thresholds["min_content_match"]:
            excluded.append({"handle": c["handle"],
                             "reason": f"content_mismatch (content_match={llm_res['content_match']})"})
            continue
        if llm_res["audience_fit"] < thresholds["min_audience_fit"]:
            excluded.append({"handle": c["handle"],
                             "reason": f"audience_mismatch (audience_fit={llm_res['audience_fit']})"})
            continue

        kept.append({
            "handle": c["handle"],
            "subscribers": c["subscriber_count"],
            "avg_views": round(avg_views, 1),
            "avg_er": round(er, 4),
            # Only the unclipped ratio is exported; the clipped one stays internal for scoring.
            "growth_clipped": growth,
            "raw": {
                "engagement_rate": er,
                "stability_cv": stab_cv,
                "upload_interval_days": interval_mean,
                "upload_interval_std_days": interval_std,
                "upload_interval_cv": interval_cv,
                "growth_ratio": growth_ratio,
            },
            "content_match": llm_res["content_match"],
            "audience_fit": llm_res["audience_fit"],
            "content_quality": llm_res["content_quality"],
            "risk_score": llm_res["risk_score"],
            "competitor_conflict": llm_res["competitor_conflict"],
            "rationale": llm_res["rationale"],
            "positioning": llm_res.get("positioning") or llm_res.get("rationale", ""),
            "duration_median": median_dur,
            "ad_length_match": ad_match,
            "shorts_ratio": shorts_ratio,
            "reach_ratio": reach,
            # internal (not exported): video ids for comment sampling
            "_video_ids": [v["video_id"] for v in vids_full],
        })

    if not kept:
        return {"mode": mode, "creators": [], "excluded": excluded,
                "portfolio": None, "budget_scenarios": None,
                "brief": campaign_input, "sources": SOURCES}

    # Sentiment: compute for ALL kept only when it actually moves the score (weight > 0).
    # Otherwise it is enriched post-ranking below (top-N only) as a display badge.
    # Quota-saver forces the top-N-only path to save Gemini calls.
    sentiment_top = config.QUOTA_SAVER_SENTIMENT_TOP if config.QUOTA_SAVER else config.SENTIMENT_TOP_N
    if weights.get("audience_sentiment", 0) > 0 and not config.QUOTA_SAVER:
        _enrich_sentiment(kept, mode, use_gemini=use_gemini)

    # LLM metrics are already 0-100; ad-length is absolute 0-100; the quantitative ones
    # need pool normalization.
    for r in kept:
        r["scores"] = {
            "content_match": round(r["content_match"], 2),
            "audience_fit": round(r["audience_fit"], 2),
            "content_quality": round(r["content_quality"], 2),
            "ad_length_match": (round(r["ad_length_match"], 2)
                                if r["ad_length_match"] is not None
                                else float(config.NEUTRAL_SCORE)),
            "audience_sentiment": round(r.get("audience_sentiment", config.NEUTRAL_SCORE), 2),
        }
    _assign_scores(kept, "engagement", [r["raw"]["engagement_rate"] for r in kept], reverse=False)
    _assign_scores(kept, "growth", [r["growth_clipped"] for r in kept], reverse=False)
    _assign_scores(kept, "stability", [r["raw"]["stability_cv"] for r in kept], reverse=True)
    _assign_scores(kept, "consistency", [r["raw"]["upload_interval_cv"] for r in kept], reverse=True)

    # Weighted total (0-100), normalized by the sum of weights so the sliders don't
    # have to add up to exactly 100.
    denom = sum(weights.values()) or 1
    for r in kept:
        r["total"] = round(sum(weights[k] * r["scores"][k] for k in weights) / denom, 2)

    # Budget / value layer.
    cpm = config.campaign_cpm(campaign_input.get("keywords", []))
    for r in kept:
        er = r["raw"]["engagement_rate"]
        r["estimated_cost"] = pricing.estimate_cost_from_views(r["avg_views"], cpm, config.ER_BENCHMARK, er)
        r["effective_value"] = pricing.effective_value_from_views(r["avg_views"], er, config.ER_BENCHMARK, r["audience_fit"])
        r["value_per_dollar"] = round(r["effective_value"] / r["estimated_cost"], 2) if r["estimated_cost"] > 0 else 0.0

    # Rank by total.
    kept.sort(key=lambda r: r["total"], reverse=True)
    for i, r in enumerate(kept):
        r["rank"] = i + 1
        r["flags"] = {
            "competitor_conflict": r["competitor_conflict"],
            "risk": r["risk_score"],
            "low_reach_ratio": bool(r["reach_ratio"] is not None
                                    and r["reach_ratio"] < config.LOW_REACH_RATIO),
        }

    # Default path (weight 0), and quota-saver: sentiment is a display badge on the
    # top-N only, after ranking is fixed, so it never changes the ranking.
    if weights.get("audience_sentiment", 0) <= 0 or config.QUOTA_SAVER:
        _enrich_sentiment(kept[:sentiment_top], mode, use_gemini=use_gemini)
        for r in kept[:sentiment_top]:
            r["scores"]["audience_sentiment"] = round(r["sentiment"]["sentiment_score"], 2)

    portfolio = pricing.portfolio(kept, campaign_input.get("budget_cap"),
                                  campaign_input.get("target_k", 3))
    budget_scenarios = pricing.budget_scenarios(kept, campaign_input.get("budget_cap"),
                                                campaign_input.get("target_k", 3))

    creators_out = []
    for r in kept:
        creators_out.append({
            "handle": r["handle"], "subscribers": r["subscribers"], "avg_views": r["avg_views"],
            "avg_er": r["avg_er"], "raw": r["raw"], "effective_value": r["effective_value"],
            "scores": r["scores"], "total": r["total"], "rank": r["rank"],
            "rationale": r["rationale"], "positioning": r.get("positioning", ""), "flags": r["flags"],
            "estimated_cost": r["estimated_cost"], "value_per_dollar": r["value_per_dollar"],
            "content_quality": r["content_quality"],
            "ad_length_match": r["ad_length_match"],
            "duration_median": r["duration_median"],
            "shorts_ratio": r["shorts_ratio"],
            "reach_ratio": r["reach_ratio"],
            "sentiment": r.get("sentiment"),
        })

    return {"mode": mode, "creators": creators_out, "excluded": excluded,
            "portfolio": portfolio, "budget_scenarios": budget_scenarios,
            "cpm_assumption": cpm, "brief": campaign_input, "sources": SOURCES}
