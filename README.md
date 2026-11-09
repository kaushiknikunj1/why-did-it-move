# Why Did It Move

"Why did revenue drop?" is the Monday-morning question every analytics team I've been on gets, and most teams answer it the same way: six dashboards open, three people eyeballing filters, forty minutes later a confident sentence that nobody checked against the total. I did this for years. The embarrassing part is that the question has an arithmetic answer.

A metric tree - revenue = active users × conversion × order value, by segment - can be decomposed exactly. Every driver and every segment gets a contribution, the contributions sum to the observed change to the rupee, and the mix shift that always hides in "AOV moved" gets its own line. That should run before anyone opens a dashboard. The language model's only job is to say it in English, without touching the numbers.

```
── revenue, week 10 vs 9  [template (no API key)]
Revenue fell from INR 112.3 lakh to INR 103.9 lakh (-7.4%, -8.3 lakh) in week 10 (02 Nov to 08 Nov) vs week 9 (26 Oct to 01 Nov).
Biggest causes: Android · Tier-2 conversion 3.16% -> 1.82% (-9.9 lakh, 119% of the move); iOS · Tier-1 share of users (mix) 13.9% -> 12.1% (-4.5 lakh, 54.1% of the move).
Not a factor: iOS · Tier-2, iOS · Tier-3 (each under 1 pt of revenue) - stop looking there.
```

That is the deterministic template. With an API key the same evidence pack goes to an LLM, which writes the three lines in plainer English - and is then checked.

## How it works

1. **Data.** Ten weeks of daily consumer-payments data, synthetic, for six segments (Android/iOS × city tier 1-3). Into the final week I inject a story: conversion collapses in Android · Tier-2, order value rises about 2% everywhere, and users shift from high-value iOS · Tier-1 toward low-value Android · Tier-3. The engine has to recover it.
2. **Decompose.** Two cuts of the same change. Driver level: LMDI (log-mean Divisia) attribution of the week-over-week revenue change to users, conversion and order value - each driver's share is the log-mean of revenue times the log-ratio of the driver, so the three sum exactly to the change. Segment level: a Kitagawa-style split of each segment into *its own rate moved* vs *its share of users moved* (mix), with the rate half further split into conversion vs order value. A check confirms both cuts sum to the observed delta.
3. **Evidence pack.** Before/after totals, each driver's contribution in lakh and percentage points, each segment's rate and mix contribution, the top three contributors with their own before/after values and the share of the change they explain, and the list of things that did not move.
4. **Write + verify.** The pack goes to an LLM with a strict three-line brief. Every number in the reply is parsed and checked against the pack; if any was invented or re-rounded, the deterministic template is used instead and the output says so.

Step 4 matters more here than in most projects because decomposition numbers *look* like they can be re-derived. "-9.9 lakh is 119% of the move" invites a model to recompute it as "about 120%" or "roughly 10 lakh". Those are not the numbers the arithmetic produced, and the whole value of running the arithmetic is that the sentence can be audited against it.

The waterfall also shows the thing I most wanted the project to show: at the driver level "average order value" is *up* 3.2 lakh, so a dashboard reader would say AOV is fine. The segment cut says order value rose within every segment but users shifted toward cheaper ones - the mix shift is the second-largest cause of the drop and is invisible until you separate rate from mix.

## Run it

```bash
pip install -r requirements.txt
python why_did_it_move.py
```

Works with no API key - you get the template explanation. For an LLM-written one set one of:

```bash
export GEMINI_API_KEY=...        # preferred; uses gemini-2.5-flash via REST
export ANTHROPIC_API_KEY=...     # uses the official anthropic SDK, claude-opus-5-5
```

Outputs: `explanation.md` (the three lines + collapsible evidence pack) and `waterfall.png` (driver waterfall + per-segment rate vs mix).

## What I'd change for production

- Read the metric tree from config, not code. Every team's tree is different (GMV = merchants × transactions × ticket; retention = cohort × return rate), and the LMDI step does not care what the leaves are called.
- Compare against the same week last year and the trailing four-week average, not only the prior week. A single-week delta is noisy; a contribution that shows up in all three comparisons is the one to escalate.
- Add a significance screen before ranking. With six segments and three drivers you get eighteen contributions, and in a quiet week the top one is noise. A bootstrap over the daily rows tells you whether -9.9 lakh is a story or a Tuesday.

Companion reading: [North Star Metrics](https://nikunjkaushik.com/blog/product-north-star-metrics.html). Part of the [Building with AI](https://nikunjkaushik.com/projects.html) set on my site.
