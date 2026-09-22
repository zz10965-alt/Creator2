"""YouTube Data API v3: auto-discover candidates + fetch their data (quota-aware path)."""
import re

import config
from googleapiclient.discovery import build


def _youtube():
    return build("youtube", "v3", developerKey=config.YOUTUBE_API_KEY)


def parse_duration_seconds(iso):
    """Parse an ISO 8601 duration ("PT12M30S" -> 750) into whole seconds.

    Returns None for an empty/unsupported value so callers can fall back to
    "no duration known" rather than crashing on an odd format.
    """
    if not iso:
        return None
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


def _resolve_handle(yt, handle):
    """Resolve an explicit @handle (from campaign_input["handles"]) to a channel id.

    Returns None (rather than raising) on any lookup failure, so one bad handle
    in the list doesn't take down the whole discovery run.
    """
    h = handle if str(handle).startswith("@") else "@" + str(handle).lstrip("@")
    try:
        resp = yt.channels().list(part="id", forHandle=h).execute()
        items = resp.get("items", [])
        return items[0]["id"] if items else None
    except Exception:
        return None


def get_creators(brief, max_candidates=None):
    yt = _youtube()
    max_candidates = max_candidates or config.DISCOVER_MAX_RESULTS
    videos_per_creator = brief.get("videos_per_creator") or config.MAX_VIDEOS_PER_CREATOR
    q = " ".join(brief.get("keywords", [])[:3])

    # 1a. Explicit handles (campaign_input["handles"]) always get checked first,
    #     regardless of keyword discovery below.
    channel_ids = []
    for h in brief.get("handles") or []:
        cid = _resolve_handle(yt, h)
        if cid and cid not in channel_ids:
            channel_ids.append(cid)

    # 1b. Keyword-based discovery: paginate through video search results, collecting
    #    the unique channels behind them until we have enough candidates or run out
    #    of pages. Skipped once the explicit handles alone already fill the quota.
    page_token = None
    for _ in range(config.DISCOVER_MAX_PAGES):
        if len(channel_ids) >= max_candidates:
            break
        params = {"part": "snippet", "q": q, "type": "video",
                  "maxResults": 50, "relevanceLanguage": "en"}
        if page_token:
            params["pageToken"] = page_token
        resp = yt.search().list(**params).execute()
        for item in resp.get("items", []):
            cid = item["snippet"]["channelId"]
            if cid not in channel_ids:
                channel_ids.append(cid)
            if len(channel_ids) >= max_candidates:
                break
        if len(channel_ids) >= max_candidates:
            break
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    channel_ids = channel_ids[:max_candidates]
    if not channel_ids:
        return []

    # 2. Channel info + uploads playlist (the API caps `id` at 50 per call, so chunk it).
    channels = {}
    for i in range(0, len(channel_ids), 50):
        chunk = channel_ids[i:i + 50]
        ch = yt.channels().list(part="snippet,statistics,contentDetails",
                                id=",".join(chunk)).execute()
        for c in ch.get("items", []):
            channels[c["id"]] = c

    creators = []
    for cid in channel_ids:
        if cid not in channels:
            continue
        item = channels[cid]
        handle = item["snippet"].get("customUrl", "") or item["snippet"]["title"]
        subs = int(item["statistics"].get("subscriberCount", 0))
        if subs < config.MIN_SUBSCRIBERS:
            continue
        uploads = (item["contentDetails"]["relatedPlaylists"].get("uploads", "")
                   if "contentDetails" in item else "")
        vids = _get_videos(yt, uploads, max_videos=videos_per_creator)
        if len(vids) < 3:
            continue  # too little data to compute stability/consistency
        creators.append({"handle": handle, "subscriber_count": subs, "videos": vids})
    return creators


def _get_videos(yt, uploads_playlist, max_videos=None):
    max_videos = max_videos or config.MAX_VIDEOS_PER_CREATOR
    if not uploads_playlist:
        return []
    ids = []
    resp = yt.playlistItems().list(part="contentDetails", playlistId=uploads_playlist,
                                   maxResults=max_videos).execute()
    for it in resp.get("items", []):
        vid = it["contentDetails"].get("videoId")
        if vid:
            ids.append(vid)
    if not ids:
        return []
    v = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(ids)).execute()
    out = []
    for it in v.get("items", []):
        st = it.get("statistics", {})
        sn = it.get("snippet", {})
        cd = it.get("contentDetails", {})
        dur = parse_duration_seconds(cd.get("duration", ""))
        out.append({
            "video_id": it["id"],
            "title": sn.get("title", ""),
            "description": sn.get("description", ""),
            "tags": sn.get("tags", []),
            "view_count": int(st.get("viewCount", 0)),
            "like_count": int(st.get("likeCount", 0)),
            "comment_count": int(st.get("commentCount", 0)),
            "published_at": sn.get("publishedAt", ""),
            "duration_seconds": dur,
            "is_short": (dur is not None and dur <= config.SHORT_MAX_SECONDS),
        })
    return out


def get_comments(yt, video_ids, max_total=None):
    """Sample top-level comments from a creator's top videos for sentiment analysis.

    One commentThreads.list call per video (cheap on quota). Only the first few
    videos are sampled so the total cost stays bounded. Returns a flat list of
    comment text strings, capped at max_total. A video with comments disabled
    simply raises and is skipped.
    """
    max_total = max_total or config.MAX_COMMENTS_PER_CREATOR
    out = []
    for vid in (video_ids or [])[:5]:
        try:
            resp = yt.commentThreads().list(part="snippet", videoId=vid,
                                            maxResults=100, textFormat="plainText").execute()
        except Exception:
            continue
        for it in resp.get("items", []):
            txt = (it.get("snippet", {}).get("topLevelComment", {})
                   .get("snippet", {}).get("textDisplay", ""))
            if txt:
                out.append(txt)
            if len(out) >= max_total:
                return out
    return out


def get_trending(yt, region=None, category_id=None, max_results=20, keywords=None):
    """Fetch trending videos, optionally narrowed to the campaign's topic.

    With no keywords this is the regional mostPopular chart. With keywords it searches
    for popular on-topic videos (ordered by view count) so the panel stays relevant to
    the brief instead of showing a generic regional chart.
    """
    if keywords:
        q = " ".join(keywords[:3])
        out = []
        page_token = None
        while len(out) < max_results:
            params = {"part": "snippet", "q": q, "type": "video", "order": "viewCount",
                      "maxResults": 50, "relevanceLanguage": "en"}
            if region:
                params["regionCode"] = region
            if page_token:
                params["pageToken"] = page_token
            resp = yt.search().list(**params).execute()
            for it in resp.get("items", []):
                sn = it.get("snippet", {})
                out.append({"video_id": (it.get("id") or {}).get("videoId", ""),
                            "title": sn.get("title", ""),
                            "channel": sn.get("channelTitle", ""),
                            "published_at": sn.get("publishedAt", "")})
                if len(out) >= max_results:
                    break
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out[:max_results]

    region = region or config.TRENDING_REGION
    params = {"part": "snippet", "chart": "mostPopular", "regionCode": region,
              "maxResults": max_results}
    if category_id:
        params["videoCategoryId"] = category_id
    resp = yt.videos().list(**params).execute()
    out = []
    for it in resp.get("items", []):
        sn = it.get("snippet", {})
        out.append({"video_id": it["id"], "title": sn.get("title", ""),
                    "channel": sn.get("channelTitle", ""),
                    "published_at": sn.get("publishedAt", "")})
    return out
