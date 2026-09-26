"""Run the full pipeline once and print the results. Usage: python main.py"""
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config
import pipeline


def build_brief():
    return {
        "brief": "Eco-friendly skincare brand, target women 18-34",
        "keywords": ["skincare", "clean beauty", "eco-friendly"],
        "competitors": ["BrandX"],
        "risk_topics": ["political", "controversial", "scandal"],
        "budget_cap": 5000,
        "target_k": 3,
    }


def load_creators(brief):
    if config.YOUTUBE_API_KEY:
        try:
            import youtube_api
            creators = youtube_api.get_creators(brief)
            return creators, "LIVE"
        except Exception as e:
            print(f"[warn] live API failed ({e}), falling back to sample data")
    import mock
    return mock.get_creators(), "DEMO"


def main():
    brief = build_brief()
    creators, mode = load_creators(brief)
    res = pipeline.run_pipeline(brief, creators, mode=mode)

    print("=" * 100)
    print("  Creator Shortlisting  |  auto-score + budget optimization")
    print("=" * 100)
    src = "YouTube Data API v3 (live data)" if res["mode"] == "LIVE" else "built-in sample data"
    print(f"Mode: {res['mode']}    Data source: {src}")
    print(f"Brief: {brief['brief']}")
    print(f"Discovered {len(creators)} candidates -> excluded {len(res['excluded'])} by cutoff -> {len(res['creators'])} kept")
    print()

    if res["excluded"]:
        print("-- Hard cutoffs (excluded) --")
        for e in res["excluded"]:
            print(f"  x {e['handle']:<28} {e['reason']}")
        print()

    if res["creators"]:
        print("-- Ranking (all scores 0-100) --")
        hdr = (f"{'#':<3}{'channel':<26}{'total':>7}{'content':>8}{'engage':>8}"
               f"{'growth':>8}{'stability':>9}{'audience':>9}{'consist':>8}"
               f"{'subs':>12}{'cost($)':>11}{'val/$':>7}")
        print(hdr)
        print("-" * len(hdr))
        for r in res["creators"][:15]:
            s = r["scores"]
            print(f"{r['rank']:<3}{r['handle'][:25]:<26}{r['total']:>7}"
                  f"{s['content_match']:>8}{s['engagement']:>8}{s['growth']:>8}"
                  f"{s['stability']:>9}{s['audience_fit']:>9}{s['consistency']:>8}"
                  f"{r['subscribers']:>12,}{r['estimated_cost']:>11,.2f}{r['value_per_dollar']:>7}")
        if len(res["creators"]) > 15:
            print(f"  ... (showing top 15 of {len(res['creators'])}, full list in the JSON below)")
        print()

    if res.get("portfolio"):
        p = res["portfolio"]
        print(f"-- Optimal portfolio within ${brief['budget_cap']} (0/1 knapsack, maximize total value) --")
        print(f"  Picks: {', '.join(p['picks'])}")
        print(f"  Total cost ${p['total_cost']} | expected views {p['expected_views']:,} | expected engagements {p['expected_engagements']:,}")
        print()

    print("-- Full results (the contract structure to hand to the frontend) --")
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
