"""Quantitative metrics: pure math over raw video data -> raw values.

Each function here returns a *raw* value, not a 0-100 score. Direction reversal
(e.g. "smaller CV = better") is handled later, in pipeline.py's normalize step,
not inside these functions.
"""
import numpy as np
from datetime import datetime

import config


def _parse(dt):
    """Normalize a publish timestamp (datetime or ISO string) to a naive datetime."""
    if isinstance(dt, datetime):
        return dt if dt.tzinfo is None else dt.replace(tzinfo=None)
    s = str(dt)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=None) if d.tzinfo is not None else d


def engagement_rate(videos):
    """Median engagement rate = (likes + comments) / views across recent videos."""
    vals = [(v["like_count"] + v["comment_count"]) / v["view_count"]
            for v in videos if v["view_count"] > 0]
    return float(np.median(vals)) if vals else 0.0


def engagement_stability(videos):
    """Coefficient of variation (CV) of engagement rate across recent videos.

    Smaller CV = more consistent engagement = more stable. Returns None when there
    are fewer than 2 videos with views, since the CV is undefined there.
    """
    ers = [(v["like_count"] + v["comment_count"]) / v["view_count"]
           for v in videos if v["view_count"] > 0]
    if len(ers) < 2:
        return None
    mean = float(np.mean(ers))
    if mean == 0:
        return None
    return float(np.std(ers) / mean)


def upload_consistency(videos):
    """Coefficient of variation (CV) of the gaps between uploads.

    Smaller CV = more regular upload cadence. Returns None with fewer than 3 upload
    dates (need at least 2 intervals to measure spread).
    """
    dates = sorted(_parse(v["published_at"]) for v in videos)
    if len(dates) < 3:
        return None
    intervals = [max((dates[i] - dates[i - 1]).total_seconds() / 86400.0, 0) for i in range(1, len(dates))]
    mean = float(np.mean(intervals))
    if mean == 0:
        return None
    return float(np.std(intervals) / mean)


def days_since_last_upload(videos, now=None):
    """Whole days since the creator's most recent upload (0 = posted today).

    Used to flag dormant channels. Returns None if there are no videos.
    """
    if not videos:
        return None
    now = now or datetime.now()
    newest = max(_parse(v["published_at"]) for v in videos)
    return (now - newest).days


def upload_interval_stats(videos):
    """Return (mean_days, std_days) between uploads - the "every X days +/- Y" display value.

    Distinct from upload_consistency (the CV): the CV is how *spread* the cadence is,
    whereas these are the actual cadence numbers the frontend shows. Returns
    (None, None) with fewer than 3 dates.
    """
    dates = sorted(_parse(v["published_at"]) for v in videos)
    if len(dates) < 3:
        return None, None
    intervals = [max((dates[i] - dates[i - 1]).total_seconds() / 86400.0, 0) for i in range(1, len(dates))]
    return float(np.mean(intervals)), float(np.std(intervals))


def view_growth(videos, now=None):
    """Recent growth = mean views last 30 days / mean views prior 30 days, clipped to [0, 5].

    Clipped so a single viral spike can't dominate the pool when we normalize.
    Used for scoring only - see view_growth_ratio for the raw number.
    """
    now = now or datetime.now()
    recent, prior = [], []
    for v in videos:
        age = (now - _parse(v["published_at"])).days
        if 0 <= age <= 30:
            recent.append(v["view_count"])
        elif 30 < age <= 60:
            prior.append(v["view_count"])
    if not recent or not prior:
        return 1.0
    r = float(np.mean(recent))
    p = float(np.mean(prior))
    if p == 0:
        return 1.0
    return float(min(max(r / p, 0.0), 5.0))


def view_growth_ratio(videos, now=None):
    """Unclipped view-growth ratio (same as view_growth but without the [0, 5] cap).

    This is the raw number exported for display ("+26%"), while view_growth (clipped)
    is what actually feeds the score.
    """
    now = now or datetime.now()
    recent, prior = [], []
    for v in videos:
        age = (now - _parse(v["published_at"])).days
        if 0 <= age <= 30:
            recent.append(v["view_count"])
        elif 30 < age <= 60:
            prior.append(v["view_count"])
    if not recent or not prior:
        return 1.0
    r = float(np.mean(recent))
    p = float(np.mean(prior))
    if p == 0:
        return 1.0
    return float(r / p)


def normalize_pool(values, low=0.0, high=100.0, reverse=False):
    """Min-max normalize a batch of values into [low, high].

    reverse=True flips the direction (smallest value maps to high), used for metrics
    where "smaller is better" (the CV-based stability/consistency). If all values are
    equal, everyone gets the midpoint.
    """
    arr = np.array(values, dtype=float)
    if arr.size == 0:
        return []
    if arr.max() - arr.min() < 1e-9:
        return [(low + high) / 2.0] * len(values)
    normed = [float(low + (x - arr.min()) / (arr.max() - arr.min()) * (high - low))
              for x in values]
    if reverse:
        return [low + high - x for x in normed]
    return normed


def median_duration_seconds(videos):
    """Median video length (seconds) across the videos that carry a duration.

    Feeds ad_length_match and the "format" label. Returns None when no durations
    are available (older/mock data without the field is simply skipped).
    """
    durs = [v["duration_seconds"] for v in videos if v.get("duration_seconds") is not None]
    if not durs:
        return None
    return float(np.median(durs))


def ad_length_match(videos, desired_seconds):
    """Closeness of the creator's median video length to the brand's desired ad length (0-100).

    100 = exact match; falls off linearly as the median drifts from the target.
    Returns None when desired is missing or no durations exist (-> neutral score 50).
    """
    if not desired_seconds:
        return None
    median = median_duration_seconds(videos)
    if median is None or median <= 0:
        return None
    gap = abs(median - desired_seconds) / desired_seconds * 100.0
    return float(max(0.0, 100.0 - min(100.0, gap)))


def filter_by_format(videos, fmt="long"):
    """Keep only the videos matching the campaign format.

    "long"  -> videos longer than SHORT_MAX_SECONDS (long-form, sponsor-friendly)
    "short" -> videos up to SHORT_MAX_SECONDS (Shorts)
    "any"   -> all videos (v1 behavior)
    Videos without a duration are kept (can't judge, don't drop) so old/mock data still works.
    """
    fmt = (fmt or config.DEFAULT_FORMAT).lower()
    if fmt == "any":
        return videos
    limit = config.SHORT_MAX_SECONDS
    out = []
    for v in videos:
        d = v.get("duration_seconds")
        if d is None:
            out.append(v)
        elif fmt == "short" and d <= limit:
            out.append(v)
        elif fmt == "long" and d > limit:
            out.append(v)
    return out


def shorts_ratio(videos):
    """Fraction of a creator's videos that are Shorts (0.0-1.0), for the UI label."""
    durs = [v["duration_seconds"] for v in videos if v.get("duration_seconds") is not None]
    if not durs:
        return 0.0
    return sum(1 for d in durs if d <= config.SHORT_MAX_SECONDS) / len(durs)


def reach_ratio(avg_views, subscribers):
    """avg_views / subscribers: how efficiently a channel turns its audience into views.

    Low ratio = big subscriber count but little actual reach = possible bought/dead channel.
    Returns None when subscribers is 0.
    """
    if not subscribers:
        return None
    return avg_views / subscribers
