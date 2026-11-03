# Vektor Gauss roadmap

**As of 3 Nov 2026.** One page that replaces reading `ARSITEKTUR-DATA-v1.md` §6, `CHECKLIST-UI-UX.md` and `RENCANA-ROMBAK-UI-v3.md` side
by side. Item codes (K#, F#, G#, I#) point into those documents. `CHECKLIST-UI-UX.md` is out of date: several of its open items
(glossary A5, stock/pattern pages F3/F4, clickable tickers F10, mobile D1/D2, authentication I3) were finished after it was written.

## The one question everything serves

After about 3.5 years of free IDX data, **no pattern has been shown to beat trading costs** (about 1.7% round trip). The stop/target
replay did not change that (average −1.25% versus −0.86% for just holding). So the roadmap has three jobs, in this order:
1. find out honestly whether an edge exists (more data, better statistics);
2. make the tool useful and understandable even if the answer is "no edge": risk sizing, flow reading, honest reporting;
3. keep the data safe and complete.

Nothing commercial (public report card, paid tiers) is built until job 1 has an answer.

## Done

| Area | What |
|---|---|
| Data foundation | Immutable raw archive, sell-side broker ladders, split/dividend adjustment, EOD quality gate, bad-price repair (K1–K5); full 105-day sell-side refill (K6); backups to GitHub Releases |
| Research | BROKER2 patterns (certain bounds), data audit, stop/target backtest on 1.16M stock-days |
| Dashboard | English UI, glossary and explanation cards, `/guide`, `/search`, stock pages with plain summaries and charts, pattern pages, `/risk-test`, mobile layout |
| Access | Password gate, lockout, safe startup, `scripts/serve.sh`, `PHONE-ACCESS.md` (Windows/phone steps are yours and untested) |

## Now (next 1 to 2 weeks)

| Item | Why now | Size |
|---|---|---|
| Forward-test gate | Time-based: the first pattern can be judged once its span reaches 60 days, around mid-November. Read the verdict then | wait |
| Weekly refresh of the stop/target snapshot | It is manual today; add it to the Sunday block of `eod.sh` | S |
| Align digest and dashboard risk parameters | Digest uses 2.5R and 0.3% fee, dashboard uses 1R–3R and 0.4%; two answers to the same question | S |
| K10: effective sample size | Signals on the same day are related, so `n` overstates the evidence. Needed before any verdict is trusted | S |
| Phone access setup (yours) | Follow `PHONE-ACCESS.md`; I can help debug | S |
| Prove G3 and G11 | G3: five EOD days with at least 150 broker names (2 of 5 so far: 186, 202). G11: release `backup-2026-11-08` after Sunday | wait |
| Look at the BBCA outlier days | Foreign-broker flow of ±40–56% of traded value on some days: real events or data quirks? | S |

## Next (weeks 2 to 6)

| Item | Why | Size |
|---|---|---|
| K7: deep broker history (2023 then 2020) | Gives the broker patterns a multi-year test instead of five months. About 15 days of API quota, runs in the background | long, low effort |
| K11: factor neutralisation and false-discovery control | Separates "the pattern works" from "small, illiquid stocks went up" | M |
| K12: walk-forward testing | Honest out-of-sample test per market regime | M |
| K13: cost and capacity model | Lot size, tick size, auto-rejection limits, average daily volume | M |
| K9: sector map and KSEI coverage | Fixes sector filters and the KSEI blind spots | M |
| Dashboard health home page (F2) and collector run log (G1) | A single "is everything healthy?" screen; needs `collector_runs` | M |
| Screener speed (B2) | The page is large; show the top 100 first | M |

## Later

K8 security master (delistings, historical sectors; depends on finding a free source), K14 fundamentals, K15 macro and short-sell data,
sector rotation (F6), dividend calendar (F7), insiders per stock (F8), Broker Stalker (F9), per-broker inventory chart (needs K7 and
a two-sided broker measure), tablet checks and screen-reader testing (C5, D4).

## Gated: do not start until a pattern passes (or you decide the product is a reading/risk tool)

Public report card (I1), landing page, sign-up, user watchlists, paid tier (I2). If no pattern ever passes, the honest product is a
transaction-reading and risk-sizing tool for you, not something to sell as "signals".

## Decisions waiting for you

1. **Product direction if nothing passes:** personal reading and risk tool, or keep hunting for an edge with deeper data (K7, K11–K13)?
2. **K7 depth:** 2023 onward first (about 8 days of quota) or all the way to 2020 (about 15)?
3. **Fundamentals (K14):** worth it, or stay focused on flow and price?
4. **Phone access:** are you doing the Tailscale setup, or prefer a different route?
