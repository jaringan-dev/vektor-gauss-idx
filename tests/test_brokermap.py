"""Broker map of one session (vg.web.brokermap): pure, no database."""

from vg.web import brokermap as M

F, R = frozenset({"AK", "ZP"}), frozenset({"YP", "XL"})
B = 1e9


def rows():
    # ASII-like 6 Nov 2026 shape: AK foreign net buyer, KZ a clean buyer, CC and XL net sellers, MG a round-tripper, small noise
    return [
        ("AK", 106 * B, 26 * B, 21.4e6, 5.2e6),
        ("KZ", 24 * B, 0, 4.85e6, 0),
        ("CC", 4 * B, 24 * B, 0.8e6, 4.9e6),
        ("XL", 3 * B, 26 * B, 0.6e6, 5.3e6),
        ("YP", 1 * B, 9 * B, 0.2e6, 1.8e6),
        ("MG", 10 * B, 9 * B, 2e6, 1.8e6),
        ("ZZ", 0.1 * B, 0, 0.02e6, 0),          # below 1% of the value: in no list
        ("PD", 0, 4.1 * B, 0, 0.8e6),
    ]


def test_group_shares_add_up_and_use_the_broker_lists():
    bm = M.build(rows(), F, R)
    sh = {s["key"]: s for s in bm["shares"]}
    assert abs(sum(s["buy"] for s in bm["shares"]) - 1) < 1e-9 and abs(sum(s["sell"] for s in bm["shares"]) - 1) < 1e-9
    assert abs(sh["foreign"]["buy"] - 106 / 148.1) < 1e-9          # AK (ZP absent)
    assert abs(sh["retail"]["sell"] - (26 + 9) / 98.1) < 1e-9       # XL + YP


def test_top_lists_are_by_net_with_average_prices():
    bm = M.build(rows(), F, R)
    assert [i["code"] for i in bm["top_buy"]] == ["AK", "KZ", "MG", "ZZ"]
    assert [i["code"] for i in bm["top_sell"]][:3] == ["XL", "CC", "YP"]
    assert abs(bm["top_buy"][0]["buy_avg"] - 106 * B / 21.4e6) < 1e-6


def test_clean_buyers_sellers_and_round_trips():
    bm = M.build(rows(), F, R)
    assert [i["code"] for i in bm["clean_buy"]] == ["KZ"]           # ZZ is clean but immaterial
    assert [i["code"] for i in bm["clean_sell"]] == ["PD"]          # YP bought 11% of its selling: not clean
    assert [i["code"] for i in bm["roundtrip"]] == ["MG"]           # 9 of 10 both ways; AK is a net buyer, not a round-tripper


def test_nothing_traded_gives_none_and_view_formats():
    assert M.build([], F, R) is None and M.view(None) is None
    v = M.view(M.build(rows(), F, R))
    assert v["top_buy"][0]["net_t"] == "+80.0B" and v["top_buy"][0]["g"] == "F" and v["shares"][0]["buy_t"].endswith("%")
