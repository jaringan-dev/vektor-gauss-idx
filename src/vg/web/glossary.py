"""Plain-English glossary: the single source of truth for every term the dashboard shows.

RENCANA-ROMBAK-UI-v3 §2/§4. The dashboard was written for its author and used research jargon (span, edge H5, uHit,
CR3 …) with no explanation. Every column header, quadrant, status and pattern (tag) is defined HERE, once, and the
templates only reference keys (`{{ term('cr3', 'CR3') }}`), so a new term cannot reach the screen unexplained — a test
fails when a template uses a key that is missing here or when the engine emits a tag that has no entry in `TAGS`.

Writing rules: ordinary words first, the original term second; one honest caveat where it matters; never a buy/sell
instruction. Thresholds quoted in `TAGS[...].rule` mirror the constants in `engine.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Term:
    key: str
    name: str          # plain name shown in the popover title
    short: str         # 1–2 sentences, plain
    group: str
    how: str = ""      # how it is computed, one line
    caveat: str = ""   # what it does NOT tell you


@dataclass(frozen=True)
class TagInfo:
    tag: str
    name: str          # plain-language name
    what: str          # one sentence: what the pattern says
    rule: str          # the exact trigger, in words
    family: str
    caveat: str = ""


GROUPS = (
    ("reading", "Reading the stock lists"),
    ("quadrant", "Quadrants: price direction and foreign flow"),
    ("concepts", "Core concepts"),
    ("report", "Track record (forward test)"),
    ("data", "Data health"),
    ("risk", "Risk plan"),
    ("research", "Is the edge real?"),
)

# Engine quadrant value (database, Indonesian) -> glossary key + English display word.
QUADRANTS = {
    "AKUMULASI": ("q_accumulation", "Accumulation"),
    "MARKUP": ("q_markup", "Markup"),
    "DISTRIBUSI": ("q_distribution", "Distribution"),
    "MARKDOWN": ("q_markdown", "Markdown"),
    "NETRAL": ("q_neutral", "Neutral"),
}

_T = [
    # ---- reading the stock lists ---------------------------------------------------------------------------
    Term("change_today", "Change today", "How much the closing price moved today compared with yesterday's close.", "reading",
         how="(today's close / yesterday's close) − 1"),
    Term("foreign_today", "Foreign flow today", "Foreign investors' buying minus their selling in this stock today. "
         "Positive means foreigners bought more than they sold.", "reading",
         how="(foreign buy volume − foreign sell volume) × the day's average price, from IDX's stock summary.",
         caveat="Based on the investor's domicile as reported by IDX. It is not a per-broker figure, so it can differ from the "
                "broker-based 'foreign' number."),
    Term("foreign_1w", "Foreign flow, 1 week", "Net foreign flow summed over the last five trading days. Shows whether foreigners "
         "have been moving in or out over the week, not just today.", "reading",
         how="Sum of daily net foreign flow over the last 5 trading days."),
    Term("broker_net", "Foreign broker net", "Net buying by the brokers classed as foreign in this stock today, from per-broker trade data.",
         "reading", how="Sum of net buying across brokers classified as foreign.",
         caveat="This older measure only adds up net buyers, so it can miss the true figure. For a figure that is guaranteed "
                "correct see 'certain bounds' on the stock page."),
    Term("cr3", "Top-3 broker share (CR3)", "The share of net buying taken by the three biggest buying brokers. "
         "The higher it is, the more concentrated the buying is in a few brokers.", "reading",
         how="Net buying of the top 3 brokers ÷ total net buying of all buying brokers.",
         caveat="High concentration does not by itself mean a 'big player' is involved; thinly traded stocks are often dominated by few brokers."),
    Term("consistency", "Consistent days (out of 10)", "Out of the last 10 trading days, how many had net foreign buying. "
         "The more days point the same way, the less likely it is a one-day accident.", "reading",
         how="Count of days with positive net foreign flow in the last 10 days."),
    Term("ksei", "Foreign ownership trend (KSEI)", "Direction of the foreign ownership share over the last three months according to KSEI "
         "data (↑ up, ↓ down, → flat).", "reading",
         how="Change in the foreign percentage of the scripless base, latest month vs three months earlier.",
         caveat="KSEI data is monthly and late, so it is a cross-check, not a daily signal."),
    Term("pattern", "Pattern", "A feature detected in today's data by a fixed rule (for example, foreigners buying while the price falls). "
         "Every pattern has its own track record on the Track record page.", "reading",
         caveat="A pattern describes data. It is not an instruction to trade."),
    Term("quadrant", "Quadrant", "A one-word summary of two things: the 5-day price direction and the 1-week foreign flow direction.", "quadrant",
         how="Price up/down/flat uses a ±2% band over 5 days; foreign flow uses the sign of the 1-week net flow."),
    # ---- quadrants ---------------------------------------------------------------------------------------------
    Term("q_accumulation", "Accumulation", "Price is down or flat, yet foreigners were net buyers over the week. Big buyers appear to be "
         "absorbing the selling.", "quadrant", caveat="Only a description of two numbers; the price can keep falling."),
    Term("q_markup", "Markup", "Price is up and foreigners were net buyers over the week: the rise is accompanied by foreign buying.", "quadrant"),
    Term("q_distribution", "Distribution", "Price is up, but foreigners were net sellers over the week. The rise looks like it is being used "
         "to sell down holdings.", "quadrant", caveat="Only a description of two numbers, not a forecast of a decline."),
    Term("q_markdown", "Markdown", "Price is down or flat and foreigners were net sellers over the week.", "quadrant"),
    Term("q_neutral", "Neutral", "Not in any of the four quadrants above (for example, zero net foreign flow).", "quadrant"),
    # ---- concepts -----------------------------------------------------------------------------------------------
    Term("net_flow", "Net buying / net selling", "Net buying means total purchases exceeded total sales over the period; net selling is the "
         "reverse. It describes flow, not a recommendation.", "concepts"),
    Term("liquid", "Liquid stock", "A stock that traded more than Rp5 billion on the day. It is easier to buy and sell without moving the "
         "price, so its numbers are more reliable.", "concepts"),
    Term("float", "Free float", "The part of a company's shares that the public can actually trade, excluding controlling holders.", "concepts"),
    Term("broker", "Broker", "A securities firm that executes trades. Per-broker data shows who bought or sold the most, but one broker "
         "serves many clients, so it is not the identity of a single party.", "concepts"),
    Term("certain_bounds", "Certain bounds", "The sell side we cannot see in full is represented by a range that is guaranteed to contain the "
         "truth, rather than a single guess. Example: 'between −6.3% and −0.1% of traded value' means the real value is somewhere in that range.",
         "concepts", how="Per broker: the visible side is exact; the hidden side is bounded between 0 and the smallest visible value."),
    Term("group_flow", "Net flow by group", "Brokers split into three groups (foreign houses, domestic retail brokers, all others) and each group's net "
         "buying or selling shown per day as a range that is guaranteed to contain the truth. The three groups always add up to zero.", "concepts",
         how="Foreign and retail groups use the certain bounds of their listed brokers; 'all others' is minus the sum of the other two.",
         caveat="Groups come from the broker's code, not from who its client is: one broker serves many kinds of clients, so 'retail' and "
                "'institution' are estimates. 'All others' mixes local institutions, bank-backed houses and small brokers."),
    Term("roundtrip", "Round-trip trading", "Value that the same broker both bought and sold on the day. Shown as a share of the day's traded value, "
         "as a floor: at least this much.", "concepts",
         how="For each broker whose buy and sell are both visible: the smaller of the two, summed over brokers.",
         caveat="It counts a broker's own clients trading against each other as well as market-making or intraday trading, and the data cannot "
                "tell which, so a high value is not proof of a market maker."),
    Term("gross", "Gross buy / gross sell", "A broker's total bought (or sold) value for the day, without netting the two.", "concepts"),
    Term("ihsg", "IHSG (Jakarta Composite Index)", "The overall index of the Indonesian stock market.", "concepts"),
    # ---- track record ---------------------------------------------------------------------------------------------
    Term("forward_test", "Forward test", "Patterns are recorded as they appear and their results measured days later, with no rule changes. "
         "It is the most honest evidence because it cannot be polished after the outcome is known.", "report"),
    Term("in_sample", "Full history (in-sample)", "Pattern results over all past data. Tends to be too optimistic because rules were chosen while "
         "looking at that data, so it is context only and never used to judge a pass.", "report"),
    Term("n", "Number of occurrences (n)", "How many times the pattern has appeared. The fewer, the more likely the result is luck.", "report",
         caveat="Occurrences on the same day are related, so the number of truly independent observations is smaller than n."),
    Term("span", "Time span", "Calendar days between the pattern's first and last occurrence. A pattern needs at least 60 days so it is not "
         "tested in only one market mood.", "report"),
    Term("edge", "Edge", "The average result of stocks that triggered the pattern minus the average result of all stocks on the same days. "
         "Positive means the pattern did better than the average stock.", "report",
         caveat="Not yet reduced by trading costs; see 'edge after costs'."),
    Term("edge_h1", "Edge after 1 day", "Edge measured 1 trading day after the pattern appeared.", "report"),
    Term("edge_h5", "Edge after 5 days", "Edge measured 5 trading days after the pattern appeared. This is the number that decides pass or fail.",
         "report"),
    Term("edge_trend", "Monthly trend of the 5-day edge",
         "One dot per calendar month: how far the pattern's 5-day edge sat above or below the market that month. Above the dashed line means it beat "
         "the market in that month. Every line in a table uses the same scale, so you can compare them.",
         "report",
         how="The same edge as the 5-day column (pattern return minus the market return on the same day), averaged within each month. "
             "Only occurrences whose 5-day result is known are counted, so the newest month is usually thin.",
         caveat="A hollow dot is a month with fewer than 30 occurrences: it is shown but never joined by a line, because a thin month can look "
                "like a trend by luck. A wiggle from month to month is normal noise; it is not a reason to trust or distrust a pattern."),
    Term("edge_h10", "Edge after 10 days", "Edge measured 10 trading days after the pattern appeared.", "report"),
    Term("hit", "Hit rate", "The percentage of occurrences that ended higher after 5 days.", "report"),
    Term("uhit", "Market hit rate", "The percentage of all stocks that ended higher after 5 days over the same period. The yardstick for the hit rate.",
         "report"),
    Term("n_liquid", "Occurrences in liquid stocks", "How many times the pattern appeared in stocks trading more than Rp5 billion a day.", "report"),
    Term("edge_h5_liquid", "Edge after 5 days, liquid stocks", "5-day edge using only liquid stocks, compared with the average liquid stock.",
         "report", caveat="The yardstick differs from the all-stock 5-day edge, so the two columns cannot be compared directly."),
    Term("edge_after_cost", "Edge after costs", "5-day edge minus the round-trip trading cost of about 1.7% (commission, tax, bid-ask spread). "
         "A pattern only passes if this stays positive.", "report"),
    Term("status_pass", "Pass", "Enough data (n ≥ 50 and span ≥ 60 days) and a positive edge after costs. No pattern has reached this yet.", "report"),
    Term("status_fail", "Fail", "Enough data to judge, but the edge after costs is not positive. Shown just as prominently as a pass.", "report"),
    Term("status_probation", "Probation", "Not enough data yet to call it a pass or a fail. Neutral; not a proven signal.", "report"),
    Term("small_n", "Small n", "Fewer than 50 occurrences, too few to judge. A good-looking number from a sample this small is easy luck, "
         "so these patterns are sorted to the bottom.", "report"),
    Term("cost", "Trading cost", "Buy and sell commission, tax and the bid-ask spread. About 1.7% round trip is assumed when judging patterns.", "report"),
    # ---- data health ----------------------------------------------------------------------------------------------
    Term("eod", "EOD (end of day)", "The closing data of a trading day. 'EOD 3 Nov' means data up to the close of 3 Nov has been collected.", "data"),
    Term("data_price", "Price data", "How many stocks have a price for the day, compared with the normal count (about 960).", "data"),
    Term("data_broker", "Broker data", "How many stocks have per-broker trade data. Coverage is deliberately narrower (about 200 liquid stocks) "
         "than price data.", "data", caveat="Not a fault as long as it is at least 150 stocks."),
    Term("data_ksei", "KSEI data", "Monthly ownership data from KSEI. It is more than a month behind because it is published monthly.", "data"),
    Term("normal", "Normal count", "The median of the previous 60 trading days, used as the yardstick for healthy data.", "data"),
    Term("partial", "Partial", "The day's data is 50–90% of normal: some of it is missing.", "data"),
    Term("gap", "Gap", "The day's data is under 50% of normal: most of it is missing, so calculations for that day cannot be trusted.", "data"),
    # ---- risk plan -----------------------------------------------------------------------------------------------
    Term("atr", "ATR (average daily range)", "How far the price typically moves in a day, averaged over 14 days. More volatile stocks have a larger ATR.",
         "risk", how="14-day average of the true range: the largest of (high − low) and the gap from yesterday's close.",
         caveat="Empty when a split or reverse split distorts the recent range."),
    Term("mech_stop", "Mechanical stop level", "Reference price minus 2 × ATR, rounded to the IDX tick size. A calculation from volatility, "
         "not a price prediction and not a guarantee.", "risk",
         caveat="Price can gap through this level, and a stop that is too close is often hit by ordinary movement."),
    Term("r_target", "Risk-multiple target (R)", "1R, 2R and 3R mean a gain of 1, 2 or 3 times the distance between the reference price and "
         "the stop level.", "risk", caveat="Targets are arithmetic only, not a forecast of where the price will go."),
    Term("position_size", "Position size (lots)", "The number of lots such that, if the stop level is hit, the loss does not exceed the share of "
         "capital you chose. 1 lot = 100 shares.", "risk",
         how="lots = (capital × risk%) ÷ ((price − stop + fee per share) × 100)"),
    Term("hold_10", "Hold 10 days", "Buying at the reference price and simply selling 10 trading days later, with no stop and no target. "
         "It is the yardstick the stop-and-target rule has to beat.", "risk",
         how="(close 10 trading days later ÷ reference price) − 1 − trading costs"),
    Term("stop_hit", "Stopped out", "The share of trades where price touched the stop level within 10 trading days. A gap down through the stop exits "
         "at the open, which is worse than planned.", "risk"),
    Term("target_hit", "Target reached", "The share of trades where price reached the target within 10 trading days before touching the stop. "
         "If both fall inside one day's range, the test assumes the stop was hit first.", "risk"),
    Term("time_exit", "Time exit", "Neither the stop nor the target was touched in 10 trading days, so the trade is closed at that day's close.", "risk"),
    Term("avg_net", "Average result after costs", "The average return of all the trades in the test after subtracting the round-trip trading cost "
         "(about 1.7%). Below zero means the typical trade lost money.", "risk"),
    Term("win_rate", "Winning trades", "The share of trades that ended above the reference price after costs.", "risk"),
    Term("ci95", "95% interval", "The range the true edge most likely lies in, given the data. If the whole range is above zero the edge is probably real; "
         "if it includes zero the data cannot tell it apart from luck.", "research",
         how="Resamples whole blocks of 5 trading days 2,000 times and takes the middle 95% of the results.",
         caveat="Wide intervals mean little data, not a weak pattern."),
    Term("eff_blocks", "Independent observations", "Signals on the same day, and 5-day returns on neighbouring days, move together, so 1,000 signals are not 1,000 pieces of evidence. "
         "This is roughly how many independent observations they amount to.", "research",
         how="Days that had at least one signal ÷ 5."),
    Term("neutral_edge", "Size- and liquidity-matched edge", "The edge when each flagged stock is compared with stocks of the same size and the same liquidity on the same day, "
         "instead of with the whole market. It removes the effect of simply flagging small, jumpy stocks.", "research",
         caveat="Beta and sector are not removed (sectors need a map we do not have yet)."),
    Term("net_edge", "Edge after modelled costs", "The matched edge minus a cost estimated for each signal: commission, the tick-size spread, and market impact that grows when the stock "
         "is thin or volatile (assuming an order of Rp100 million).", "research",
         caveat="The impact term is an assumption, not a measurement."),
    Term("fdr_q", "False-discovery q-value", "Testing 26 patterns means a few will look good by pure luck. The q-value corrects for that: a pattern is only taken seriously if its q-value is "
         "below 0.10.", "research", how="Benjamini-Hochberg adjustment of the one-sided p-values across all patterns tested."),
    Term("folds_pos", "Folds with a positive edge", "The data is cut into half-year periods. This is the share of those periods in which the pattern's edge after costs was positive. "
         "A real edge should not depend on one good period.", "research",
         how="The last 10 trading days of each period are dropped so neighbouring periods do not share return windows."),
    Term("regime", "Market regime", "Whether the market was rising, falling or flat over the previous 60 trading days (the Composite index moving more than 3% up or down). "
         "A pattern that only works in one regime is a bet on that regime.", "research"),
    Term("ref_price", "Reference price", "The latest closing price, used as the calculation point. Not the price you will get when you trade.", "risk"),
]

TERMS: dict[str, Term] = {t.key: t for t in _T}

TAG_FAMILIES = {
    "flow": "Foreign flow and price",
    "ownership": "Ownership (KSEI and filings)",
    "broker": "Broker activity",
    "trades": "Trade patterns",
}

_TAGS = [
    TagInfo("AKUM-ASING", "Foreigners buying into weakness",
            "Price is flat or falling, yet foreigners keep buying on balance.",
            "5-day price flat/down; net foreign flow positive this week and last week; at least 6 of the last 10 days net foreign buying.", "flow"),
    TagInfo("DISTRIBUSI-ASING", "Foreigners selling into strength",
            "Price is rising, yet foreigners are net sellers and rarely buy.",
            "5-day price up more than 2%; 1-week net foreign flow negative; at most 3 of the last 10 days net foreign buying.", "flow"),
    TagInfo("MARKUP", "Price up with foreign buying", "Price is rising and foreigners were net buyers over the week.",
            "5-day price up more than 2% and 1-week net foreign flow positive.", "flow"),
    TagInfo("MARKDOWN", "Price down with foreign selling", "Price is falling and foreigners were net sellers over the week.",
            "5-day price down more than 2% and 1-week net foreign flow negative.", "flow"),
    TagInfo("KSEI-KONFIRM", "KSEI data agrees with foreign flow",
            "The monthly foreign ownership trend (KSEI) points the same way as the daily foreign flow that triggered the pattern.",
            "Appears with AKUM-ASING when the 3-month KSEI foreign share is rising, or with DISTRIBUSI-ASING when it is falling.", "ownership"),
    TagInfo("KSEI-DIVERGEN", "KSEI data contradicts foreign flow",
            "The monthly foreign ownership trend (KSEI) points the opposite way from the daily foreign flow: worth caution.",
            "Appears with AKUM-ASING when the 3-month KSEI foreign share is falling, or with DISTRIBUSI-ASING when it is rising.", "ownership"),
    TagInfo("KSEI-INST-AKUM", "Local institutions raising their share", "The local institutional share rose over three months (KSEI data).",
            "Local institutional share up by more than 0.5 percentage points in 3 months.", "ownership", "Monthly and late data."),
    TagInfo("KSEI-RITEL-EXIT", "Local retail holders shrinking", "The local individual investor share fell over three months (KSEI data).",
            "Local individual share down by more than 0.5 percentage points in 3 months.", "ownership", "Monthly and late data."),
    TagInfo("FLOAT-ASING-PADAT", "Foreigners taking a big slice of the free float",
            "A week of net foreign buying equals a meaningful part of the shares that trade freely.",
            "5-day net foreign flow at least 1% of the free float.", "ownership", "Rare; small sample."),
    TagInfo("LAPOR-KEPEMILIKAN-RAMAI", "Sudden burst of ownership filings",
            "Many ownership-change filings arrived in a short time; the direction is not known from this pattern alone.",
            "At least 3 filings in 5 trading days and at least 2× the 20-day average rate.", "ownership"),
    TagInfo("INSIDER-AKUM", "Insiders adding shares", "Today's filings show directors or commissioners adding shares.",
            "From the ownership filings parsed today.", "ownership", "Rare; small sample."),
    TagInfo("INSIDER-DIST", "Insiders reducing shares", "Today's filings show directors or commissioners reducing shares.",
            "From the ownership filings parsed today.", "ownership", "Rare; small sample."),
    TagInfo("SUBSTANTIAL-AKUM", "5%+ holder adding", "Today's filings show a holder of more than 5% adding to their stake.",
            "From the ownership filings parsed today.", "ownership", "Rare; small sample."),
    TagInfo("SUBSTANTIAL-DIST", "5%+ holder reducing", "Today's filings show a holder of more than 5% reducing their stake.",
            "From the ownership filings parsed today.", "ownership", "Rare; small sample."),
    TagInfo("CROSSING-TINGGI", "Lots of negotiated trades",
            "A large part of the volume came from negotiated/crossing trades rather than the regular market, so regular price moves can mislead.",
            "Negotiated-board value above 20% of the day's total value.", "trades"),
    TagInfo("TIKET-BESAR-AKUM", "Unusually large trades while price is weak",
            "Average value per trade is far above this stock's habit while the price is flat or falling (a footprint of large buyers).",
            "Z-score of average value per trade at least 1.5 and 5-day price flat/down.", "trades"),
    TagInfo("TIKET-KECIL-MARKUP", "Many small trades while price rises",
            "The rise is driven by trades much smaller than usual (a footprint of retail crowding).",
            "Z-score of average value per trade at most −1 and 5-day price up.", "trades"),
    TagInfo("BROKER-ASING-AKUM", "Foreign brokers net buying into weakness (old measure)",
            "Foreign brokers are buying heavily on balance while the price is flat or falling.",
            "Foreign broker net buying at least 5% of the day's traded value and 5-day price flat/down.", "broker",
            "Uses a one-sided measure (net buyers only), so it can miss reality. See BROKER2-ASING-AKUM."),
    TagInfo("BROKER-ASING-DIST", "Foreign brokers net selling into strength (old measure)",
            "Foreign brokers are selling heavily on balance while the price rises.",
            "Foreign broker net selling at least 5% of the day's traded value and 5-day price up.", "broker",
            "One-sided measure; almost never fires because net sellers are not fully recorded. See BROKER2-ASING-DIST."),
    TagInfo("BROKER2-ASING-AKUM", "Foreign brokers certainly net buying into weakness",
            "Same as the old version but with certain bounds: the whole range of possibilities clears the 5% threshold.",
            "Lower bound of foreign broker net buying at least 5% of traded value and 5-day price flat/down.", "broker",
            "New pattern (2 Nov 2026); past results shown were computed backwards, not forward-tested."),
    TagInfo("BROKER2-ASING-DIST", "Foreign brokers certainly net selling into strength",
            "Same as the old version but with certain bounds: the whole range of possibilities is below −5%.",
            "Upper bound of foreign broker net flow at most −5% of traded value and 5-day price up.", "broker",
            "New pattern (2 Nov 2026); its direction is not yet proven and past results were computed backwards."),
    TagInfo("BROKER-DOMISILI-KONFIRM", "Two foreign measures agree",
            "Foreign flow by broker and foreign flow by IDX account domicile move the same way.",
            "Both measures at least 1% of traded value with the same sign.", "broker"),
    TagInfo("BROKER-DOMISILI-DIVERGEN", "Two foreign measures disagree",
            "Foreign flow by broker and by IDX account domicile contradict each other: the data sources do not agree.",
            "Both measures at least 1% of traded value with opposite signs.", "broker"),
    TagInfo("FOREIGN-INST", "Foreign institutional brokers buying heavily",
            "Brokers typical of foreign institutions are net buying a large amount.",
            "Net buying of foreign institutional brokers at least 5% of the day's traded value.", "broker",
            "An estimate: one broker serves many clients."),
    TagInfo("DOM-RETAIL", "Domestic retail brokers buying heavily",
            "Brokers that mostly serve domestic retail clients are net buying a large amount; usually a sign of retail crowding.",
            "Net buying of domestic retail brokers at least 5% of the day's traded value.", "broker",
            "An estimate, not a certain identity."),
    TagInfo("BANDAR-CANDIDATE", "One broker dominating the buying",
            "One broker holds a large share of net buying with institution-sized tickets. A guess, not a fact.",
            "One broker at least 40% of net buying, average ticket at least Rp50 million, and its cumulative position not falling.", "broker",
            "Only an inference from data patterns."),
]

TAGS: dict[str, TagInfo] = {t.tag: t for t in _TAGS}


def term(key: str) -> Term | None:
    return TERMS.get(key)


def tag_info(tag: str) -> TagInfo | None:
    return TAGS.get(tag)


def plain_tag(tag: str) -> str:
    """Plain-language name for a tag, falling back to the raw code so an unknown tag never disappears."""
    t = TAGS.get(tag)
    return t.name if t else tag


def quad_key(quadrant: str | None) -> str | None:
    q = QUADRANTS.get(quadrant or "")
    return q[0] if q else None


def quad_word(quadrant: str | None) -> str:
    q = QUADRANTS.get(quadrant or "")
    return q[1] if q else "—"


def popover_data() -> dict:
    """What the browser needs to render a hint card: key -> {name, short, how, caveat}. Tags use the key `tag:<CODE>`."""
    out: dict[str, dict] = {k: {"name": t.name, "short": t.short, "how": t.how, "caveat": t.caveat} for k, t in TERMS.items()}
    for code, t in TAGS.items():
        out[f"tag:{code}"] = {"name": t.name, "short": t.what, "how": t.rule, "caveat": t.caveat, "code": code}
    return out


def split_tags(tag_string: str | None) -> list[str]:
    return [x.strip() for x in (tag_string or "").split(",") if x.strip()]


def install(env) -> None:
    """Expose the glossary helpers to a Jinja environment (the app's and the template tests')."""
    env.globals.update(plain_tag=plain_tag, split_tags=split_tags, quad_key=quad_key, quad_word=quad_word)
