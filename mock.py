"""Built-in sample data: fallback when there's no key or the API fails, so the full
pipeline still runs end-to-end."""
from datetime import datetime, timedelta


def _v(title, views, likes, comments, days_ago, tags=None, now=None, duration_seconds=None):
    now = now or datetime.now()
    return {"video_id": "v", "title": title, "description": title,
            "tags": tags or [], "view_count": int(views), "like_count": int(likes),
            "comment_count": int(comments),
            "published_at": (now - timedelta(days=days_ago)).isoformat(),
            "duration_seconds": duration_seconds,
            "is_short": (duration_seconds is not None and duration_seconds <= 60)}


def _make(handle, subs, base_views, er, cadence=3, n=20, tag=None, growth=1.0, words="video", duration=600):
    now = datetime.now()  # single reference so upload intervals are exact whole days
    videos = []
    for i in range(n):
        days_ago = i * cadence
        g = growth if days_ago < 30 else 1.0
        views = base_views * g * (1 + (n - i) * 0.01)
        likes = views * er
        videos.append(_v(f"{words} {i}", views, likes, likes * 0.08, days_ago, [tag],
                         now=now, duration_seconds=duration))
    return {"handle": handle, "subscriber_count": subs, "videos": videos}


def get_creators():
    return [
        _make("@cleanbeauty_lily", 128000, 52000, 0.09, 3, 20, "skincare", 1.4, "clean beauty skincare routine", 600),
        _make("@techreview_tom", 980000, 210000, 0.03, 7, 20, "tech", 0.8, "gadget review", 900),
        _make("@glowup_mia", 64000, 30000, 0.11, 2, 20, "makeup", 1.6, "makeup tutorial", 480),
        _make("@health_dan", 220000, 80000, 0.05, 14, 20, "fitness", 1.0, "fitness workout", 1200),
        _make("@competitor_fan", 150000, 60000, 0.06, 4, 20, "skincare", 1.2, "skincare featuring BrandX promo", 600),
        _make("@edgy_creator", 300000, 120000, 0.07, 5, 20, "skincare", 1.1, "controversial political skincare", 600),
        # Shorts-heavy creator to demo format filtering + ad-length matching.
        _make("@quickbeauty_emma", 89000, 41000, 0.12, 1, 20, "skincare", 2.0, "60 second skincare hacks", 45),
    ]


def get_comments(video_ids=None, max_total=50):
    """Fake comment sample (mixed positive/negative) so sentiment runs without a key."""
    pos = ["love this, so helpful!", "exactly what I needed, thanks", "your tips always work",
           "best channel for this, subscribed", "tried this and it actually works"]
    neg = ["boring and repetitive", "this is just an ad", "used to be good, now meh",
           "stopped watching, too salesy", "clickbait title, nothing new"]
    out = []
    i = 0
    while len(out) < max_total:
        # Deterministic 80/20 positive/negative mix so the demo positive_ratio is stable.
        out.append(pos[i % len(pos)] if i % 5 != 0 else neg[i % len(neg)])
        i += 1
    return out


def get_trending(region="US", max_results=10, keywords=None):
    """Fake "trending today" list so the trending panel demos without a key.

    When keywords are given, matching items are ranked first so the demo panel looks
    on-topic for the campaign.
    """
    items = [
        {"video_id": "t1", "title": "5-minute clean beauty routine for busy mornings", "channel": "GlowDaily"},
        {"video_id": "t2", "title": "We tried every viral skincare hack", "channel": "TryGuys"},
        {"video_id": "t3", "title": "honest review: this sunscreen changed my skin", "channel": "DermTalk"},
        {"video_id": "t4", "title": "budget vs luxury makeup challenge", "channel": "BeautyLab"},
        {"video_id": "t5", "title": "sustainable living: zero-waste swaps that work", "channel": "EcoHome"},
    ][:max_results]
    if keywords:
        kws = [k.lower() for k in keywords if k and len(k.strip()) >= 3]
        def _score(v):
            text = (v["title"] + " " + v["channel"]).lower()
            return sum(1 for k in kws if k in text)
        items = sorted(items, key=_score, reverse=True)
    return items
