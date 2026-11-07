"""Executive summary on the stock page (narrative.broker_summary, stock.broker_reading): pure, no database."""

from datetime import date, timedelta

from vg.web import narrative, stock
from vg.web.bpi_window import WindowScore
from vg.web.evidence import read
from vg.web.patterns import NetBuyer

W = WindowScore(5, 5, 0.253, 1, 4, "AK", 5)
NET = NetBuyer("AK", 5, 5, 329.1e9, 5, 0.33)
EDGE = {"AKUM-ASING": {"n": 1320, "win": "51%"}}


def _r(**over):
    r = {"win": W, "net": NET, "haka": 0.61, "overlap": 1, "wash": False, "crossing": None,
         "retail_div": "Retail selling, foreign institutions buying", "evidence": read(W, NET, haka=0.61)}
    r.update(over)
    return r


def _all_text(lines):
    return " ".join(lines).lower()


def test_a_full_reading_names_the_numbers_and_never_advises():
    lines = narrative.broker_summary(_r(), ["AKUM-ASING"], {"AKUM-ASING": "No evidence of an edge"}, EDGE)
    text = _all_text(lines)
    assert "+25.3 points" in text and "ak, was among the day's top 3 buyers on 5" in text
    assert "net buyer on 5 of 5 sessions" in text and "61%" in text
    assert "research on akum-asing" in text and "no evidence of an edge" in text and "51% of 1,320 cases" in text
    assert lines[-1].startswith("This describes data. It is not a recommendation")
    assert not any(w in text for w in narrative.FORBIDDEN)


def test_many_patterns_collapse_into_one_research_sentence():
    """ASII 7 Nov 2026 had 5 patterns: five 'Research on ...' lines buried the summary."""
    tags = ["A", "B", "C", "D", "E"]
    verdicts = {"A": "Real but too small to pay the costs", "B": "Real but too small to pay the costs",
                "C": "Real but too small to pay the costs", "D": "No evidence of an edge"}
    edge5 = {t: {"n": 100, "win": "46%", "win_raw": w} for t, w in zip(tags, (0.46, 0.47, 0.46, 0.46, 0.47), strict=True)}
    lines = narrative.broker_summary(_r(), tags, verdicts, edge5)
    research = [x for x in lines if x.startswith("Research")]
    assert len(research) == 1
    assert ("today's 5 patterns" in research[0] and "3 real but too small to pay the costs" in research[0]
            and "1 no evidence of an edge" in research[0] and "1 not researched yet" in research[0] and "46-47%" in research[0])


def test_without_stored_research_it_says_so_instead_of_failing():
    text = _all_text(narrative.broker_summary(_r(), ["AKUM-ASING"], None, {}))
    assert "not researched yet" in text


def test_no_broker_data_and_short_coverage_are_said_plainly():
    assert "no broker table" in _all_text(narrative.broker_summary(_r(win=None, net=None, haka=None, retail_div=None,
                                                                      evidence=read(None, None)), [], {}, {}))
    short = WindowScore(5, 2, None, None, 1, None, 0)
    assert "only 2 of the last 5 sessions" in _all_text(narrative.broker_summary(_r(win=short, net=None), [], {}, {}))


def test_selling_side_wash_and_crossing_are_described():
    sell = WindowScore(5, 5, -0.18, 0, 1, "CC", 1, n_crossing=1)
    text = _all_text(narrative.broker_summary(_r(win=sell, wash=True, crossing=0.99), [], {}, {}))
    assert "selling was the more concentrated side" in text and "wash flag" in text and "99% of today's value" in text
    assert "1 of those sessions was mostly negotiated-market" in text
    assert not any(w in text for w in narrative.FORBIDDEN)


def test_a_tiny_index_reads_as_balanced_not_as_minus_zero():
    """NATO 6 Nov 2026 printed 'selling was the more concentrated side (-0.0 points)'."""
    text = _all_text(narrative.broker_summary(_r(win=WindowScore(5, 5, -0.0001, 0, 2, "AP", 1)), [], {}, {}))
    assert "about equally concentrated" in text and "+0.0 points" in text and "-0.0" not in text


def test_broker_reading_builds_the_window_from_the_page_rows():
    days = [date(2026, 11, 2) + timedelta(days=i) for i in range(5)]
    levels = []
    for d in days:
        levels += [(d, "B", "YP", 40.0), (d, "B", "AA", 30.0), (d, "B", "BB", 20.0), (d, "B", "CC", 10.0)]
        levels += [(d, "S", f"S{i}", 10.0) for i in range(10)]
    raw = {"latest": days[-1], "chart_bars": [(d, 1, 1, 1, 1, 1, 1) for d in days],
           "bpi_levels": levels, "bpi_values": [(d, 100.0, 0.0) for d in days],
           "chart_brokers": {"flows": [(d, "YP", 40.0, 5.0) for d in days]}, "scoring": []}
    r = stock.broker_reading(raw, [])
    assert r["win"].days_ok == 5 and abs(r["win"].bpi3 - 0.6) < 1e-9 and r["win"].top_buyer == "YP"
    assert r["net"] is not None and r["net"].broker == "YP"            # 35 net of 100 a day = 35% of value, 5 of 5 days
    assert r["evidence"]["state"] == "agree"


def test_broker_reading_with_nothing_loaded_is_graceful():
    r = stock.broker_reading({"latest": date(2026, 11, 6)}, [])
    assert r["win"] is None and r["net"] is None and r["evidence"]["state"] == "unknown"
