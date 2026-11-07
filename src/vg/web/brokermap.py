"""Broker map of one session on the stock page: who bought and who sold, by group and by house.

    Group shares   foreign houses / domestic retail houses / local institutions & others, as a share of all buying and of all selling
    Top 5          the five biggest NET buyers and NET sellers, with their average price
    Clean buyers   houses that bought and (almost) did not sell: sell at most CLEAN_TOL of buy
    Clean sellers  the mirror image
    Round-trip     houses on both sides in similar size ("tektokan"): the smaller side at least ROUNDTRIP_MIN of the bigger one, so they
                   moved a lot of stock but changed their position little (day trading, market making, or crossing between accounts)

COMPLETE TABLES ONLY: it reads `broker_flows_full` (every broker that traded, from `vg collect-broker-full`). `broker_flows` keeps the 20
biggest net buyers only and the ladders stop at rank 25, so on those a house that "did not sell" may simply have sold below the cut: a
clean-buyer list built on them would be a guess. Groups are by BROKER HOUSE (the house a trade went through), not by the end client.

Thresholds are written here so they can be argued with. `build` is pure; `load` is SELECT only.
"""

from __future__ import annotations

from datetime import date

CLEAN_TOL = 0.05          # a clean buyer sold at most 5% of what it bought (and the mirror for sellers)
ROUNDTRIP_MIN = 0.80      # round-trip: the smaller side is at least 80% of the bigger side
MATERIAL = 0.01           # every list needs at least 1% of the session's traded value on the side that counts
TOP_N = 5
LIST_MAX = 6
GROUPS = (("foreign", "Foreign houses"), ("retail", "Domestic retail houses"), ("other", "Local institutions & others"))


def _avg(val: float, vol: float | None) -> float | None:
    return val / vol if vol and vol > 0 and val > 0 else None


def build(rows: list[tuple], foreign: frozenset | set, retail: frozenset | set) -> dict | None:
    """`rows`: (broker, buy value, sell value, buy volume, sell volume) for ONE stock and session, every broker that traded.
    None when there is nothing traded."""
    rs = [(c, float(b or 0), float(s or 0), float(bv or 0), float(sv or 0)) for c, b, s, bv, sv in rows]
    tot_b, tot_s = sum(r[1] for r in rs), sum(r[2] for r in rs)
    total = max(tot_b, tot_s)
    if total <= 0:
        return None

    def group(code: str) -> str:
        return "foreign" if code in foreign else "retail" if code in retail else "other"

    shares = []
    for key, label in GROUPS:
        b = sum(r[1] for r in rs if group(r[0]) == key)
        s = sum(r[2] for r in rs if group(r[0]) == key)
        shares.append({"key": key, "label": label, "buy": b / tot_b if tot_b else 0.0, "sell": s / tot_s if tot_s else 0.0,
                       "net": b - s})

    def item(r, value: float) -> dict:
        c, b, s, bv, sv = r
        return {"code": c, "group": group(c), "buy": b, "sell": s, "net": b - s, "value": value,
                "buy_avg": _avg(b, bv), "sell_avg": _avg(s, sv)}

    floor = MATERIAL * total
    nets = sorted(rs, key=lambda r: r[1] - r[2])
    top_buy = [item(r, r[1] - r[2]) for r in reversed(nets) if r[1] - r[2] > 0][:TOP_N]
    top_sell = [item(r, r[2] - r[1]) for r in nets if r[2] - r[1] > 0][:TOP_N]
    clean_buy = sorted((item(r, r[1]) for r in rs if r[1] >= floor and r[2] <= CLEAN_TOL * r[1]), key=lambda x: -x["value"])
    clean_sell = sorted((item(r, r[2]) for r in rs if r[2] >= floor and r[1] <= CLEAN_TOL * r[2]), key=lambda x: -x["value"])
    rt = sorted((item(r, r[1] + r[2]) for r in rs
                 if min(r[1], r[2]) >= floor and min(r[1], r[2]) >= ROUNDTRIP_MIN * max(r[1], r[2])), key=lambda x: -x["value"])
    return {"total": total, "n_brokers": len(rs), "shares": shares, "top_buy": top_buy, "top_sell": top_sell,
            "clean_buy": clean_buy[:LIST_MAX], "clean_sell": clean_sell[:LIST_MAX], "roundtrip": rt[:LIST_MAX],
            "n_clean_buy": len(clean_buy), "n_clean_sell": len(clean_sell), "n_roundtrip": len(rt)}


GROUP_SHORT = {"foreign": "F", "retail": "R", "other": "L"}


def view(bm: dict | None) -> dict | None:
    """`build` output with the text the template prints (rupiah as 24.2B, prices, percentages). Pure."""
    if not bm:
        return None
    from . import fmt

    def money(x: float) -> str:
        return fmt.idr(x).lstrip("+")

    def fmt_item(it: dict) -> dict:
        return {**it, "g": GROUP_SHORT[it["group"]], "net_t": fmt.idr(it["net"]), "value_t": money(it["value"]),
                "buy_t": money(it["buy"]), "sell_t": money(it["sell"]),
                "avg_t": fmt.price(it["buy_avg"] if it["net"] >= 0 else it["sell_avg"]) if (it["buy_avg"] or it["sell_avg"]) else "—"}

    out = {**bm, "total_t": money(bm["total"])}
    out["shares"] = [{**s, "buy_t": f"{s['buy'] * 100:.0f}%", "sell_t": f"{s['sell'] * 100:.0f}%", "net_t": fmt.idr(s["net"]),
                      "net_c": fmt.signcls(s["net"]), "buy_w": round(s["buy"] * 100), "sell_w": round(s["sell"] * 100)}
                     for s in bm["shares"]]
    for k in ("top_buy", "top_sell", "clean_buy", "clean_sell", "roundtrip"):
        out[k] = [fmt_item(i) for i in bm[k]]
    return out


def load(cur, ticker: str) -> tuple[date | None, list[tuple]]:
    """(the newest session with a complete broker table for `ticker`, its rows). SELECT only."""
    cur.execute("SELECT max(session_date) FROM broker_flows_full WHERE ticker = %s", (ticker,))
    day = cur.fetchone()[0]
    if day is None:
        return None, []
    cur.execute("SELECT broker_code, buy_val, sell_val, buy_vol, sell_vol FROM broker_flows_full WHERE ticker = %s AND session_date = %s",
                (ticker, day))
    return day, cur.fetchall()
