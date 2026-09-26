"""Budget / value layer: cost estimation + value-per-dollar + portfolio optimization (knapsack)."""
import numpy as np


def avg_recent_views(creator):
    """Typical recent views = mean of the fetched videos (ignores the date window; more robust)."""
    views = [v["view_count"] for v in creator["videos"] if v["view_count"] > 0]
    return float(np.mean(views)) if views else 0.0


def estimate_cost_from_views(avg_views, cpm, er_benchmark, er):
    """Estimated cost per video = (avg_views / 1000) * CPM * engagement premium multiplier."""
    if er > er_benchmark:
        mult = 1.15
    elif er < er_benchmark * 0.5:
        mult = 0.85
    else:
        mult = 1.0
    return round((avg_views / 1000.0) * cpm * mult, 2)


def effective_value_from_views(avg_views, er, er_benchmark, audience_fit):
    """Effective value = avg_views * (er / benchmark) * (audience_fit / 100).

    Non-degenerate on purpose: folding engagement + fit into the numerator keeps
    value-per-dollar from collapsing to ~1/CPM for everyone.
    """
    return round(avg_views * (er / er_benchmark) * (audience_fit / 100.0), 2)


def portfolio(ranked, budget, k):
    """0/1 knapsack: pick up to k creators within budget, maximizing total score * value.

    Unlike a greedy "best value-per-dollar" pass, the knapsack optimizes total value
    directly, so it actually spends the budget and picks influential creators rather
    than the cheapest high-efficiency micro channels.
    """
    if not budget or budget <= 0:
        return None
    k = k or 3
    cands = [r for r in ranked if r.get("estimated_cost", 0) > 0]
    if not cands:
        return None

    B = int(round(budget))
    NEG = float("-inf")
    # dp[count][b] = (max_value, [handles]) -- 0/1 knapsack with a headcount cap
    dp = [[(NEG, []) for _ in range(B + 1)] for _ in range(k + 1)]
    dp[0][0] = (0.0, [])

    for r in cands:
        cost = int(round(r["estimated_cost"]))
        if cost <= 0:
            cost = 1
        value = r["total"] * r.get("effective_value", 0.0)
        if value <= 0:
            continue
        for c in range(k, 0, -1):
            for b in range(B, cost - 1, -1):
                if dp[c - 1][b - cost][0] == NEG:
                    continue
                cand_val = dp[c - 1][b - cost][0] + value
                if cand_val > dp[c][b][0]:
                    dp[c][b] = (cand_val, dp[c - 1][b - cost][1] + [r["handle"]])

    best_val, best_picks, best_budget = NEG, [], 0
    for c in range(1, k + 1):
        for b in range(B + 1):
            if dp[c][b][0] > best_val:
                best_val, best_picks, best_budget = dp[c][b][0], dp[c][b][1], b

    if not best_picks:
        return None
    by_handle = {r["handle"]: r for r in ranked}
    total_cost = sum(by_handle[h]["estimated_cost"] for h in best_picks if h in by_handle)
    total_views = sum(by_handle[h]["avg_views"] for h in best_picks if h in by_handle)
    total_eng = sum(by_handle[h]["avg_views"] * by_handle[h].get("avg_er", 0)
                    for h in best_picks if h in by_handle)
    return {"picks": best_picks, "total_cost": round(total_cost, 2),
            "expected_views": round(total_views, 1),
            "expected_engagements": round(total_eng, 1),
            "note": "0/1 knapsack: maximize sum(total_score * effective_value) within budget, up to k creators"}


def budget_scenarios(ranked, budget_cap, target_k):
    """Three portfolio options: under / at / over budget.

    under = knapsack at 70% of budget (cheapest efficient combo)
    at    = knapsack at the stated budget (the default recommendation)
    over  = knapsack at 130% of budget (stretch - shows whether the extra spend is worth it)

    Each entry is a portfolio dict (or None if no combo fits that budget). The human
    "why over-budget is worth it" explanation is generated separately in insights.py.
    """
    if not budget_cap or budget_cap <= 0:
        return {"under": None, "at": None, "over": None}
    return {
        "under": portfolio(ranked, budget_cap * 0.7, target_k),
        "at": portfolio(ranked, budget_cap, target_k),
        "over": portfolio(ranked, budget_cap * 1.3, target_k),
    }
