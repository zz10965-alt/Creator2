"""Central config: weights, thresholds, CPM benchmarks, and API keys (from env / .env)."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Core scoring weights (sum to 100 by default; the UI can override per-request and the
# pipeline normalizes by the sum, so sliders don't have to add up to exactly 100).
WEIGHTS = {
    "content_match": 20,
    "engagement": 18,
    "growth": 12,
    "stability": 12,
    "audience_fit": 12,
    "consistency": 8,
    "content_quality": 8,     # AI-inferred (metadata only), not a true "content quality" score
    "ad_length_match": 5,     # how close the creator's video length is to the brand's desired ad length
    "audience_sentiment": 5,  # comment sentiment (positive ratio), AI-inferred
}

# ---------------------------------------------------------------------------
# Hard cutoffs (exclusion, NOT part of the weighted score).
#
# These are binary gates: a creator who trips ANY one of them is dropped before
# ranking and reported in results["excluded"] with a short "reason" string. They
# are intentionally NOT folded into the 0-100 score - that keeps the score
# explainable: a low score means "weak fit", a cutoff means "we would never sign
# them, regardless of score".
#
# Evaluation order (implemented in pipeline.py):
#   1. Data-driven cutoffs first (inactive, low reach) - computed from video
#      stats for free, so they run BEFORE any LLM call to save API quota.
#   2. LLM-based cutoffs second (risk, competitor, content/audience mismatch) -
#      these need the qualitative score, so they run after score_creator().
# ---------------------------------------------------------------------------
RISK_CUTOFF = 70            # brand-safety risk score (0-100, LLM) >= this -> excluded
EXCLUDE_COMPETITOR = True   # creator mentions a competitor -> excluded (conflict of interest)
INACTIVE_DAYS = 60          # days since last upload > this -> excluded as dormant/dead channel
MIN_AVG_VIEWS = 1000        # mean views per recent video < this -> too small to matter for a brand
MIN_CONTENT_MATCH = 20      # content_match (0-100, LLM) < this -> off-topic for the brief
MIN_AUDIENCE_FIT = 20       # audience_fit (0-100, LLM) < this -> wrong audience for the brand

# ---------------------------------------------------------------------------
# Data-fetch limits - how many candidates/videos we pull, and why.
#
# The YouTube API charges "quota units" per call, so every extra page or video
# costs real budget. These caps keep a run cheap while still pulling enough data
# (a stability/consistency CV needs >= 2-3 upload points to mean anything).
#
# The candidate count shrinks through this funnel:
#   discovered (<= DISCOVER_MAX_RESULTS)  ->  sub < MIN_SUBSCRIBERS dropped
#   ->  < 3 videos dropped  ->  6 hard cutoffs  ->  ranked list
# ---------------------------------------------------------------------------
MAX_VIDEOS_PER_CREATOR = 20    # recent videos fetched + scored per creator (enough for a stable CV, cheap on quota)
DISCOVER_MAX_RESULTS = 80      # candidate channels we aim to discover (actual total can be fewer)
DISCOVER_MAX_PAGES = 3         # search pages to paginate (50 videos/page -> up to 150 videos scanned)
MIN_SUBSCRIBERS = 1000         # skip channels below this: too small to be worth a brand pitch

# Gemini model (change here to switch models).
GEMINI_MODEL = "gemini-3.6-flash"

# Score assigned when a metric has insufficient data (e.g. CV needs >= 2 samples).
NEUTRAL_SCORE = 50

# ---- v2: format / ad length / sentiment / transparency ----
DEFAULT_FORMAT = "long"        # "long" | "short" | "any" - which videos count toward scoring
SHORT_MAX_SECONDS = 60         # videos <= this are treated as Shorts (approximation)
LOW_REACH_RATIO = 0.01         # avg_views / subscribers below this -> flagged "possible dead/bought channel"
MAX_COMMENTS_PER_CREATOR = 50  # top-level comments sampled per creator for sentiment
SENTIMENT_TOP_N = 10           # enrich only the top-N ranked creators with comment sentiment (saves quota)
TRENDING_REGION = "US"         # region for the "trending today" panel

# ---------------------------------------------------------------------------
# Quota-saver mode (hackathon demo): cut Gemini calls per "Run analysis" from
# ~2N+2 (N = creators scored) down to ~8, leaving quota for chat + script.
#
#   * QUOTA_SAVER = False -> normal behavior (default).
#   * QUOTA_SAVER = True  -> Gemini-score only the QUOTA_SAVER_CREATOR_MAX biggest
#     channels (by subscribers) that pass the free data cutoffs; run real Gemini
#     sentiment only on the top QUOTA_SAVER_SENTIMENT_TOP; skip Gemini for the AI
#     take + trending match (deterministic fallbacks instead). Accuracy loss is small.
#   * To go back to normal: set QUOTA_SAVER = False and restart the server.
# ---------------------------------------------------------------------------
QUOTA_SAVER = True
QUOTA_SAVER_CREATOR_MAX = 5    # cap Gemini scoring at this many creators
QUOTA_SAVER_SENTIMENT_TOP = 3  # real Gemini sentiment for the top-N only; rest neutral

# ---- Budget / value layer: industry benchmark CPM (USD per 1,000 views; frontend can override) ----
ER_BENCHMARK = 0.05            # baseline engagement rate used for the premium/discount multiplier

CPM_BENCHMARK = {
    "beauty": 12, "tech": 28, "finance": 30, "gaming": 8,
    "fashion": 15, "food": 10, "fitness": 12, "default": 15,
}

NICHE_MAP = {
    "skincare": "beauty", "beauty": "beauty", "makeup": "beauty", "cosmetic": "beauty",
    "tech": "tech", "gadget": "tech", "software": "tech", "ai": "tech",
    "finance": "finance", "invest": "finance", "money": "finance",
    "gaming": "gaming", "game": "gaming",
    "fashion": "fashion", "food": "food", "fitness": "fitness", "workout": "fitness",
}


def campaign_cpm(keywords):
    """Guess the niche from the brief keywords and return the benchmark CPM."""
    text = " ".join(keywords).lower()
    for kw, niche in NICHE_MAP.items():
        if kw in text:
            return CPM_BENCHMARK.get(niche, CPM_BENCHMARK["default"])
    return CPM_BENCHMARK["default"]
