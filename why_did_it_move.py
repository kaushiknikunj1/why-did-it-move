#!/usr/bin/env python3
"""Why Did It Move - decompose a week-over-week metric change before anyone opens a dashboard.

1. Synthesize ten weeks of daily consumer-payments data for six segments
   (platform x city tier). Metric tree: revenue = active users x conversion x AOV.
   Inject a known story into the latest week: conversion collapses in one segment,
   AOV rises a little everywhere, and users shift toward low-value segments.
2. Decompose: (a) LMDI (log-mean Divisia) attribution of the revenue change to the
   three drivers - contributions sum exactly to the observed change; (b) a
   Kitagawa-style split per segment into "its own rate moved" vs "its share of
   users moved" (mix). Rank the contributors and check that everything adds up.
3. Build an evidence pack: before/after, each driver's and segment's contribution in
   INR lakh and percentage points, the top three contributors with their own
   before/after values, and the drivers that did not move.
4. Narrate: with GEMINI_API_KEY or ANTHROPIC_API_KEY set, an LLM writes the three
   lines; every number it writes is checked against the evidence pack and the
   deterministic template takes over if it invents one. Without a key, the template.
   The arithmetic decides what happened; the LLM only says it in English.

Run:  python why_did_it_move.py
Out:  explanation.md, waterfall.png
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import llm  # noqa: E402

RNG = np.random.default_rng(11)
LAKH = 1e5
QUIET_PTS = 1.0          # a driver or segment under this many % points of revenue "did not move"
DRIVERS = {"active_users": "active users", "conversion_rate": "conversion rate",
           "avg_order_value": "average order value"}
# segment -> (weekly active users, conversion, AOV in INR)
SEGMENTS = {
    "Android · Tier-1": (120_000, 0.038, 620),
    "Android · Tier-2": (150_000, 0.032, 480),
    "Android · Tier-3": (110_000, 0.026, 390),
    "iOS · Tier-1":     (70_000, 0.050, 980),
    "iOS · Tier-2":     (35_000, 0.042, 760),
    "iOS · Tier-3":     (15_000, 0.035, 610),
}
# The story injected into the final week (week 10). The engine must recover it.
STORY = {"conv_mult": {"Android · Tier-2": 0.58},
         "user_mult": {"Android · Tier-3": 1.10, "iOS · Tier-1": 0.86},
         "aov_mult_all": 1.02}


# --------------------------------------------------------------------------- data
def make_data(weeks: int = 10) -> pd.DataFrame:
    days = pd.date_range("2026-08-31", periods=weeks * 7, freq="D")
    weekday = np.where(days.dayofweek >= 5, 1.12, 0.96)          # busier weekends
    rows = []
    for seg, (users_wk, conv, aov) in SEGMENTS.items():
        trend = np.linspace(0.97, 1.03, len(days))                 # gentle growth
        users = users_wk / 7 * weekday * trend * (1 + 0.03 * RNG.standard_normal(len(days)))
        conv_d = np.full(len(days), conv) * (1 + 0.04 * RNG.standard_normal(len(days)))
        aov_d = np.full(len(days), aov, dtype=float) * (1 + 0.02 * RNG.standard_normal(len(days)))
        last = days >= days[-7]
        conv_d[last] *= STORY["conv_mult"].get(seg, 1.0)
        users[last] *= STORY["user_mult"].get(seg, 1.0)
        aov_d[last] *= STORY["aov_mult_all"]
        users = users.round().astype(int)
        orders = RNG.binomial(users, np.clip(conv_d, 0.001, 0.5))
        rows.append(pd.DataFrame({"date": days, "segment": seg, "active_users": users,
                                  "orders": orders, "revenue": (orders * aov_d).round(2)}))
    d = pd.concat(rows, ignore_index=True)
    d["week"] = ((d.date - days[0]).dt.days // 7 + 1).astype(int)
    return d


def weekly(d: pd.DataFrame, week: int) -> pd.DataFrame:
    """One row per segment for the given week, with derived rates."""
    w = d[d.week == week].groupby("segment")[["active_users", "orders", "revenue"]].sum()
    w["conversion_rate"] = w.orders / w.active_users
    w["avg_order_value"] = w.revenue / w.orders
    w["rpu"] = w.revenue / w.active_users          # revenue per active user
    w["share"] = w.active_users / w.active_users.sum()
    return w


def totals(w: pd.DataFrame) -> dict[str, float]:
    u, o, r = float(w.active_users.sum()), float(w.orders.sum()), float(w.revenue.sum())
    return {"active_users": u, "conversion_rate": o / u, "avg_order_value": r / o, "revenue": r}


# --------------------------------------------------------------------------- engine
def log_mean(a: float, b: float) -> float:
    return a if a == b else (a - b) / (np.log(a) - np.log(b))


def lmdi(before: dict[str, float], after: dict[str, float], drivers: list[str],
         target: str) -> dict[str, float]:
    """Multiplicative decomposition: target = product of drivers.
    Each driver's contribution is L(V1, V0) * ln(x1/x0); they sum exactly to V1 - V0."""
    weight = log_mean(after[target], before[target])
    return {k: weight * float(np.log(after[k] / before[k])) for k in drivers}


def kitagawa(w0: pd.DataFrame, w1: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    """revenue = U * sum_s(share_s * rpu_s). Split the change into a volume term (U moved)
    and, per segment, a rate term (its rpu moved) and a mix term (its share moved).
    Midpoint weights make the three sum exactly to the observed change."""
    u0, u1 = w0.active_users.sum(), w1.active_users.sum()
    rpu0, rpu1 = w0.revenue.sum() / u0, w1.revenue.sum() / u1
    volume = float((u1 - u0) * (rpu0 + rpu1) / 2)
    u_bar = (u0 + u1) / 2
    seg = pd.DataFrame(index=w0.index)
    seg["rate"] = u_bar * (w0.share + w1.share) / 2 * (w1.rpu - w0.rpu)
    seg["mix"] = u_bar * (w1.share - w0.share) * (w0.rpu + w1.rpu) / 2
    # split the rate term into its conversion and AOV halves (LMDI on rpu = conv x aov)
    for s in seg.index:
        parts = lmdi(w0.loc[s].to_dict(), w1.loc[s].to_dict(),
                     ["conversion_rate", "avg_order_value"], "rpu")
        scale = seg.loc[s, "rate"] / (w1.loc[s, "rpu"] - w0.loc[s, "rpu"])
        seg.loc[s, "rate_conversion"] = parts["conversion_rate"] * scale
        seg.loc[s, "rate_aov"] = parts["avg_order_value"] * scale
    return volume, seg


def lk(x: float) -> float:
    return round(x / LAKH, 1) + 0.0          # + 0.0 turns -0.0 into 0.0


def pct(x: float, base: float) -> float:
    return round(100 * x / base, 1) + 0.0


def build_evidence(d: pd.DataFrame, week: int) -> dict:
    w0, w1 = weekly(d, week - 1), weekly(d, week)
    t0, t1 = totals(w0), totals(w1)
    delta = t1["revenue"] - t0["revenue"]
    contrib = lmdi(t0, t1, list(DRIVERS), "revenue")
    volume, seg = kitagawa(w0, w1)
    dates = lambda wk: d[d.week == wk].date.agg(["min", "max"]).dt.strftime("%d %b").tolist()

    drivers = [{"driver": k, "label": DRIVERS[k],
                "before": _fmt_driver(k, t0[k]), "after": _fmt_driver(k, t1[k]),
                "change_pct": pct(t1[k] - t0[k], t0[k]),
                "contribution_lakh": lk(v), "contribution_pct_pts": pct(v, t0["revenue"])}
               for k, v in contrib.items()]
    segments = [{"segment": s, "users_before": int(w0.loc[s, "active_users"]),
                 "users_after": int(w1.loc[s, "active_users"]),
                 "share_before_pct": round(100 * w0.loc[s, "share"], 1),
                 "share_after_pct": round(100 * w1.loc[s, "share"], 1),
                 "conversion_before_pct": round(100 * w0.loc[s, "conversion_rate"], 2),
                 "conversion_after_pct": round(100 * w1.loc[s, "conversion_rate"], 2),
                 "aov_before": round(w0.loc[s, "avg_order_value"]),
                 "aov_after": round(w1.loc[s, "avg_order_value"]),
                 "rate_contribution_lakh": lk(seg.loc[s, "rate"]),
                 "mix_contribution_lakh": lk(seg.loc[s, "mix"]),
                 "rate_from_conversion_lakh": lk(seg.loc[s, "rate_conversion"]),
                 "rate_from_aov_lakh": lk(seg.loc[s, "rate_aov"])}
                for s in seg.index]
    top = rank_contributors(seg, volume, w0, w1, delta)
    quiet = [x["label"] for x in drivers if abs(x["contribution_pct_pts"]) < QUIET_PTS]
    quiet += [s for s in seg.index if abs(seg.loc[s, ["rate", "mix"]]).sum() < QUIET_PTS / 100 * t0["revenue"]]
    return {
        "metric": "revenue", "unit": "INR lakh",
        "before_period": f"week {week - 1} ({' to '.join(dates(week - 1))})",
        "after_period": f"week {week} ({' to '.join(dates(week))})",
        "before_lakh": lk(t0["revenue"]), "after_lakh": lk(t1["revenue"]),
        "change_lakh": lk(delta), "change_pct": pct(delta, t0["revenue"]),
        "drivers": drivers,
        "user_volume_contribution_lakh": lk(volume),
        "segments": segments,
        "top_contributors": top,
        "did_not_move": quiet, "did_not_move_threshold_pct_pts": QUIET_PTS,
        "checks": {"driver_sum_gap_lakh": lk(sum(contrib.values()) - delta),
                   "segment_sum_gap_lakh": lk(volume + seg.rate.sum() + seg.mix.sum() - delta)},
    }


def _fmt_driver(k: str, v: float):
    return int(round(v)) if k == "active_users" else round(100 * v, 2) if k == "conversion_rate" else round(v)


def rank_contributors(seg: pd.DataFrame, volume: float, w0: pd.DataFrame, w1: pd.DataFrame,
                      delta: float, k: int = 3) -> list[dict]:
    items = [{"name": "total active users", "contribution_lakh": lk(volume),
              "before": int(w0.active_users.sum()), "after": int(w1.active_users.sum())}]
    for s in seg.index:
        items.append({"name": f"{s} conversion", "contribution_lakh": lk(seg.loc[s, "rate_conversion"]),
                      "before": f"{100 * w0.loc[s, 'conversion_rate']:.2f}%",
                      "after": f"{100 * w1.loc[s, 'conversion_rate']:.2f}%"})
        items.append({"name": f"{s} AOV", "contribution_lakh": lk(seg.loc[s, "rate_aov"]),
                      "before": f"INR {w0.loc[s, 'avg_order_value']:.0f}",
                      "after": f"INR {w1.loc[s, 'avg_order_value']:.0f}"})
        items.append({"name": f"{s} share of users (mix)", "contribution_lakh": lk(seg.loc[s, "mix"]),
                      "before": f"{100 * w0.loc[s, 'share']:.1f}%",
                      "after": f"{100 * w1.loc[s, 'share']:.1f}%"})
    items.sort(key=lambda x: -abs(x["contribution_lakh"]))
    for it in items[:k]:
        it["share_of_change_pct"] = pct(it["contribution_lakh"] * LAKH, delta)
    return items[:k]


# --------------------------------------------------------------------------- narrative
def template_explanation(ev: dict) -> str:
    verb = "fell" if ev["change_lakh"] < 0 else "rose"
    line1 = (f"Revenue {verb} from INR {ev['before_lakh']} lakh to INR {ev['after_lakh']} lakh "
             f"({ev['change_pct']:+g}%, {ev['change_lakh']:+g} lakh) in {ev['after_period']} vs "
             f"{ev['before_period']}.")
    causes = [f"{t['name']} {t['before']} -> {t['after']} ({t['contribution_lakh']:+g} lakh, "
              f"{t['share_of_change_pct']:g}% of the move)" for t in ev["top_contributors"][:2]]
    line2 = "Biggest causes: " + "; ".join(causes) + "."
    line3 = "Not a factor: " + ", ".join(ev["did_not_move"]) + f" (each under {ev['did_not_move_threshold_pct_pts']:g} pt of revenue) - stop looking there." \
        if ev["did_not_move"] else "Every driver moved - nothing to rule out."
    return "\n".join([line1, line2, line3])


SYSTEM = """You explain a week-over-week revenue change to a product and growth team.
Format: exactly three short lines, plain English.
1) What moved and by how much: before -> after, % change.
2) "Biggest causes:" the top two contributors from top_contributors, each with its own before -> after and its contribution in lakh.
3) "Not a factor:" the items in did_not_move, so the reader can stop looking there.
Use ONLY numbers present in the evidence. Never compute, re-round, or add new numbers. No jargon about methods."""


def narrate(ev: dict) -> tuple[str, str]:
    text = llm.complete(SYSTEM, json.dumps(ev, indent=2), max_tokens=300)
    if text is None:
        return template_explanation(ev), "template (no API key)"
    bad = llm.unpinned(text, ev)
    if bad:
        return template_explanation(ev), f"template (LLM invented numbers: {sorted(bad)})"
    return text, f"llm ({llm.provider()})"


# --------------------------------------------------------------------------- chart
def plot(ev: dict, path: str = "waterfall.png") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw={"width_ratios": [3, 2]})
    blue, red, grey = "#4A6FA5", "#d9534f", "#b8b8b8"

    labels = [ev["before_period"].split(" (")[0]] + [x["label"] for x in ev["drivers"]] + \
             [ev["after_period"].split(" (")[0]]
    steps = [x["contribution_lakh"] for x in ev["drivers"]]
    level, bottoms, heights, colors = ev["before_lakh"], [0.0], [ev["before_lakh"]], [blue]
    for s in steps:
        bottoms.append(level if s >= 0 else level + s)
        heights.append(abs(s))
        colors.append(red if s < 0 else grey)
        level += s
    bottoms.append(0.0); heights.append(ev["after_lakh"]); colors.append(blue)
    ax.bar(range(len(labels)), heights, bottom=bottoms, color=colors, width=0.6)
    for i, (b, h, c) in enumerate(zip(bottoms, heights, colors)):
        txt = f"{h:,.1f}" if c == blue else f"{steps[i - 1]:+,.1f}"
        ax.text(i, b + h + 0.6, txt, ha="center", va="bottom", fontsize=9, color="#333")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels([l.replace(" ", "\n", 1) for l in labels], fontsize=9)
    lo = min(ev["before_lakh"], ev["after_lakh"])
    ax.set_ylim(lo * 0.85, max(bottoms[i] + heights[i] for i in range(len(heights))) * 1.04)
    ax.set_ylabel("revenue, INR lakh")
    ax.set_title(f"Revenue {ev['change_pct']:+g}% week over week - driver contributions (LMDI)", fontsize=10)
    ax.grid(axis="y", alpha=0.2); ax.spines[["top", "right"]].set_visible(False)

    segs = ev["segments"]
    y = np.arange(len(segs))
    rate = [s["rate_contribution_lakh"] for s in segs]
    mix = [s["mix_contribution_lakh"] for s in segs]
    ax2.barh(y + 0.18, rate, height=0.34, color=[red if v < 0 else blue for v in rate], label="own rate moved")
    ax2.barh(y - 0.18, mix, height=0.34, color=grey, label="share of users moved (mix)")
    ax2.axvline(0, color="#666", lw=0.8)
    ax2.set_yticks(y); ax2.set_yticklabels([s["segment"] for s in segs], fontsize=9)
    ax2.invert_yaxis()
    ax2.set_xlabel("contribution to the change, INR lakh")
    ax2.set_title("Per segment: rate vs mix (Kitagawa)", fontsize=10)
    ax2.legend(fontsize=8, frameon=False, loc="lower left")
    ax2.grid(axis="x", alpha=0.2); ax2.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.savefig(path, dpi=130)


# --------------------------------------------------------------------------- main
def main() -> None:
    d = make_data()
    week = int(d.week.max())
    ev = build_evidence(d, week)
    print(f"{d.date.nunique()} days · {len(SEGMENTS)} segments · comparing week {week} with week {week - 1}"
          f" · LLM provider: {llm.provider()}\n")
    text, src = narrate(ev)
    print(f"── revenue, week {week} vs {week - 1}  [{src}]")
    print(text, "\n")
    print(f"checks: drivers sum to the change within {ev['checks']['driver_sum_gap_lakh']} lakh; "
          f"segments within {ev['checks']['segment_sum_gap_lakh']} lakh")

    out = ["# Why did it move", "",
           "LMDI driver attribution + Kitagawa rate/mix split on weekly revenue. "
           f"Explanation written by: {llm.provider()}; every number verified against the evidence pack.", "",
           f"## Revenue, week {week} vs week {week - 1}", "", text, "", f"*source: {src}*", "",
           "![waterfall](waterfall.png)", "",
           "<details><summary>evidence</summary>", "", "```json", json.dumps(ev, indent=2), "```",
           "</details>", ""]
    Path("explanation.md").write_text("\n".join(out), encoding="utf-8")
    plot(ev)
    print("wrote explanation.md and waterfall.png")


if __name__ == "__main__":
    main()
