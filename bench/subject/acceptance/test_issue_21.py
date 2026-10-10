"""K3 - Restock lead times are measurable.

`Catalog.lead_time_days(sku)` for one sku's mean, `Catalog.slowest_restocks(n)`
ranking the skus by it, `RevenueReport.restock_lead_times(catalog)` reporting
them, and `Cart.expected_fill(sku)` projecting when a backorder will be
filled.

Every assertion below is one the issue text (21-*.md, with 20-*.md and
19-*.md) states. The text names no ledger-row attribute for a
remainder, a closed flag or a lead time, no exception class for a refused
projection, no anchor date for the projection, and no shape for a ranking
entry. So the test reads a ledger row as the pair the text names (a request
and its receipt or `None`), asks only that a refusal raises, reads a ranking
entry as a sku or a (sku, mean) pair, and checks the projection by how it
moves with the mean. Every date arrives from this file: the package reads no
clock.

The fixture gives each decision a distinguishing case. SKU-2 has one request
closed in 3 days and a second, partially received one still open, so its mean
is 3, not 1.5. SKU-1 took 4 and 8 days (mean 6); SKU-4 took 2 and 3 (mean
2.5). SKU-2 and SKU-5 tie at 3, and SKU-2 sorts first. SKU-3 was requested
and never received, and SKU-6 was never requested.
"""

from datetime import date
from decimal import Decimal

import pytest

from billing import api

JAN_1 = date(2026, 1, 1)
JAN_2 = date(2026, 1, 2)
JAN_4 = date(2026, 1, 4)
JAN_5 = date(2026, 1, 5)
JAN_10 = date(2026, 1, 10)
JAN_13 = date(2026, 1, 13)
JAN_20 = date(2026, 1, 20)
FEB_1 = date(2026, 2, 1)
FEB_2 = date(2026, 2, 2)
FEB_5 = date(2026, 2, 5)
FEB_8 = date(2026, 2, 8)
FEB_9 = date(2026, 2, 9)
FEB_20 = date(2026, 2, 20)
MAR_1 = date(2026, 3, 1)
MAR_4 = date(2026, 3, 4)


def build_catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", api.Money("40.00"), "furniture"))
    catalog.add(api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers"))
    catalog.add(api.Product("SKU-4", "Chair", api.Money("90.00"), "furniture"))
    catalog.add(api.Product("SKU-5", "Rug", api.Money("70.00"), "furniture"))
    catalog.add(api.Product("SKU-6", "Pen", api.Money("3.00"), "stationery"))
    return catalog


def restocked_catalog():
    """Eight requests and six receipts, giving five distinguishable skus.

    | sku   | closed lead times | still open            | mean    |
    |-------|-------------------|----------------------|---------|
    | SKU-1 | 4 and 8 days      | —                    | 6       |
    | SKU-2 | 3 days            | 4 of 6 units awaited | 3       |
    | SKU-3 | —                 | 2 units awaited      | `None`  |
    | SKU-4 | 2 and 3 days      | —                    | 2.5     |
    | SKU-5 | 3 days            | —                    | 3       |

    SKU-6 is in the catalog and was never restocked at all, so "never
    requested" and "requested and never received" are separable.

    The receipts are recorded after every request, each one closing the
    oldest open request for its sku under `K2`'s first-in-first-out rule.
    """
    catalog = build_catalog()
    catalog.request_restock("SKU-1", 5, JAN_1)
    catalog.request_restock("SKU-1", 3, FEB_1)
    catalog.request_restock("SKU-2", 4, JAN_10)
    catalog.request_restock("SKU-2", 6, FEB_2)
    catalog.request_restock("SKU-3", 2, JAN_20)
    catalog.request_restock("SKU-4", 7, JAN_2)
    catalog.request_restock("SKU-4", 5, MAR_1)
    catalog.request_restock("SKU-5", 3, FEB_5)

    catalog.receive_restock("SKU-1", 5, JAN_5)        # 4 days
    catalog.receive_restock("SKU-1", 3, FEB_9)        # 8 days
    catalog.receive_restock("SKU-2", 4, JAN_13)       # 3 days
    catalog.receive_restock("SKU-2", 2, FEB_20)       # partial, still open
    catalog.receive_restock("SKU-4", 7, JAN_4)        # 2 days
    catalog.receive_restock("SKU-4", 5, MAR_4)        # 3 days
    catalog.receive_restock("SKU-5", 3, FEB_8)        # 3 days
    return catalog


def build_cart(catalog):
    return api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))


def pair(row):
    """A ledger row read as the text names it: a request and its receipt or
    `None`. The row may be an object carrying both or a two-element pair."""
    if hasattr(row, "request") and hasattr(row, "receipt"):
        return row.request, row.receipt
    request, receipt = row
    return request, receipt


def ledger_pairs(catalog):
    return [pair(row) for row in catalog.restock_ledger()]


def ranked_skus(ranking):
    """A ranking entry is a sku or a (sku, mean) pair; read the sku."""
    return [entry if isinstance(entry, str) else entry[0] for entry in ranking]


def test_a_lead_time_is_the_days_between_the_request_and_its_receipt():
    """SKU-5 has exactly one closed restock, requested 2026-02-05 and
    received 2026-02-08, so its lead time is the 3 days between them and
    nothing else — not the 3 units received, and not a figure read off a
    clock."""
    catalog = restocked_catalog()

    assert catalog.lead_time_days("SKU-5") == 3


def test_the_mean_is_exact_where_the_day_counts_divide_evenly():
    """SKU-1's two closed restocks took 4 and 8 days. The mean is 6 days
    exactly, and it is not a binary float pretending to be one."""
    catalog = restocked_catalog()

    mean = catalog.lead_time_days("SKU-1")

    assert mean == 6
    assert not isinstance(mean, float)


def test_a_mean_that_does_not_divide_evenly_keeps_the_half_day():
    """SKU-4's closed restocks took 2 and 3 days, so its mean is 2.5 — not
    truncated to 2, not rounded to 3, and exact rather than floating."""
    catalog = restocked_catalog()

    mean = catalog.lead_time_days("SKU-4")

    assert mean == Decimal("2.5")
    assert not isinstance(mean, float)


def test_an_open_request_contributes_no_lead_time_and_is_not_a_zero():
    """A request with nothing received against it has no lead time at all.

    Averaged in as zero, this sku's mean would fall from 3 days to 1.5 the
    moment a second request was raised — a supplier that has delivered once
    in three days would look twice as fast for having been asked again.
    """
    catalog = build_catalog()
    catalog.request_restock("SKU-5", 3, FEB_5)
    catalog.receive_restock("SKU-5", 3, FEB_8)
    assert catalog.lead_time_days("SKU-5") == 3

    catalog.request_restock("SKU-5", 9, MAR_1)

    assert catalog.lead_time_days("SKU-5") == 3


def test_a_partially_received_request_is_open_and_contributes_no_lead_time():
    """20-*.md: a partial receipt leaves the request open. SKU-2's second
    request received 2 of its 6 units, so it is still an open row and its
    days from request to part delivery are not a lead time: SKU-2's mean
    stays at the 3 days of the request that closed."""
    catalog = restocked_catalog()
    report = api.RevenueReport([])
    open_rows = [pair(row)[0] for row in report.open_restocks(catalog)]

    assert ("SKU-2", 6) in [(r.sku, r.quantity) for r in open_rows]
    assert catalog.lead_time_days("SKU-2") == 3


def test_a_sku_with_no_closed_restock_reports_none_rather_than_zero():
    """SKU-3 was requested and never received, and SKU-6 was never
    requested. Both report `None`: a lead time nobody has measured is
    absent, and zero would make either of them the fastest restock in the
    catalog."""
    catalog = restocked_catalog()

    assert catalog.lead_time_days("SKU-3") is None
    assert catalog.lead_time_days("SKU-6") is None


def test_lead_time_days_for_an_unknown_sku_raises_unknown_product():
    """A sku the catalog never carried is a caller error, not a sku with no
    deliveries — the distinction `None` would lose."""
    catalog = restocked_catalog()

    with pytest.raises(api.UnknownProduct):
        catalog.lead_time_days("SKU-404")


def test_the_report_is_keyed_by_sku_and_sorted_by_sku():
    """`restock_lead_times` reports every sku the restock records mention,
    in ascending sku order, which is the key the report's other mappings
    sort by."""
    catalog = restocked_catalog()
    report = api.RevenueReport([])

    lead_times = report.restock_lead_times(catalog)

    assert list(lead_times) == sorted(lead_times)
    assert {"SKU-1", "SKU-2", "SKU-4", "SKU-5"} <= set(lead_times)
    assert lead_times["SKU-1"] == 6
    assert lead_times["SKU-2"] == 3
    assert lead_times["SKU-4"] == Decimal("2.5")
    assert lead_times["SKU-5"] == 3
    assert lead_times.get("SKU-3") is None


def test_the_report_agrees_with_the_catalog_sku_by_sku():
    """The report is a reading of the catalog's own figure, not a second
    opinion computed off the same ledger: the two cannot disagree about
    what the mean of two deliveries is."""
    catalog = restocked_catalog()
    report = api.RevenueReport([])

    lead_times = report.restock_lead_times(catalog)

    for sku, mean in lead_times.items():
        assert mean == catalog.lead_time_days(sku) or (
            mean is None and catalog.lead_time_days(sku) is None)


def test_the_report_is_empty_for_a_catalog_with_nothing_restocked():
    """No restocks is an empty mapping, not a raise: a report of no lead
    times is a perfectly good answer, the reading `unfilled_restocks`
    already gives for a catalog with no requests."""
    report = api.RevenueReport([])

    assert report.restock_lead_times(build_catalog()) == {}


def test_slowest_restocks_ranks_by_mean_lead_time_descending():
    """Slowest first, with the sku's mean beside it: SKU-1 at 6 days, then
    SKU-2 and SKU-5 at 3, then SKU-4 at 2.5."""
    catalog = restocked_catalog()

    assert ranked_skus(catalog.slowest_restocks(None)) == [
        "SKU-1", "SKU-2", "SKU-5", "SKU-4"]


def test_slowest_restocks_breaks_a_tie_on_sku_ascending():
    """SKU-2 and SKU-5 both mean 3 days, and SKU-2 comes first however the
    requests happened to be registered — the two-pass convention
    `top_products` uses, not whichever order the aggregation produced."""
    catalog = restocked_catalog()

    ranked = ranked_skus(catalog.slowest_restocks(None))

    assert ranked.index("SKU-2") < ranked.index("SKU-5")

    # And again with the tie registered the other way round, because in the
    # fixture above SKU-2's request is the older one and sku order and
    # aggregation order happen to agree. Here SKU-5 is requested first, so
    # an implementation whose first pass sorts by anything other than the
    # sku reports the pair in the order the ledger produced it.
    reversed_tie = build_catalog()
    reversed_tie.request_restock("SKU-5", 3, JAN_1)
    reversed_tie.request_restock("SKU-2", 4, JAN_10)
    reversed_tie.receive_restock("SKU-5", 3, JAN_4)       # 3 days
    reversed_tie.receive_restock("SKU-2", 4, JAN_13)      # 3 days
    assert ranked_skus(reversed_tie.slowest_restocks(None)) == ["SKU-2", "SKU-5"]


def test_slowest_restocks_omits_a_sku_with_no_closed_row():
    """A ranking by slowness cannot place a sku whose lead time does not
    exist, so SKU-3 is absent here even though `restock_lead_times` names
    it."""
    catalog = restocked_catalog()

    ranked = ranked_skus(catalog.slowest_restocks(None))
    assert "SKU-3" not in ranked
    assert "SKU-6" not in ranked


def test_slowest_restocks_with_no_limit_returns_every_ranked_sku():
    """`n=None` means all of them in order, and an `n` past the end returns
    what exists without padding — `top_products(n=None)`'s reading."""
    catalog = restocked_catalog()

    everything = ranked_skus(catalog.slowest_restocks(None))
    assert everything == ["SKU-1", "SKU-2", "SKU-5", "SKU-4"]
    assert ranked_skus(catalog.slowest_restocks(99)) == everything


def test_slowest_restocks_truncates_to_n_from_the_slow_end():
    """A limit keeps the slowest, which is the end the ranking is named
    for."""
    catalog = restocked_catalog()

    assert ranked_skus(catalog.slowest_restocks(2)) == ["SKU-1", "SKU-2"]
    assert ranked_skus(catalog.slowest_restocks(0)) == []


def test_a_negative_n_raises():
    """Matching `top_products`: a negative row count has no meaning, and
    Python's own slicing would quietly return the *fastest* restocks
    instead."""
    catalog = restocked_catalog()

    with pytest.raises(Exception):
        catalog.slowest_restocks(-1)


def sku_2_projection(closed_days):
    """SKU-2 with one request closed in `closed_days` days and a second one
    still open, and a backorder of 5 units; the projected date."""
    catalog = build_catalog()
    catalog.request_restock("SKU-2", 4, JAN_10)
    catalog.request_restock("SKU-2", 6, FEB_2)
    catalog.receive_restock("SKU-2", 4, date(2026, 1, 10 + closed_days))
    cart = build_cart(catalog)
    cart.backorder("SKU-2", 5)
    return cart.expected_fill("SKU-2")


def test_expected_fill_projects_a_backorder_from_the_skus_own_mean():
    """The projection is a date that moves day for day with the sku's own
    mean: a mean one day longer projects one day later. No clock is read,
    and no default lead time is invented."""
    three = sku_2_projection(3)
    four = sku_2_projection(4)

    assert isinstance(three, date)
    assert (four - three).days == 1


def test_expected_fill_refuses_a_sku_with_no_measured_lead_time():
    """SKU-3 has a request and a backorder but no delivery has ever been
    received against it, so there is nothing to project from. Refused
    rather than answered with a default lead time or with today, either of
    which would put a fabricated date in front of a customer."""
    catalog = restocked_catalog()
    cart = build_cart(catalog)
    cart.backorder("SKU-3", 2)

    with pytest.raises(Exception) as refused:
        cart.expected_fill("SKU-3")

    # The text names no class, so any raise passes, but a missing method is
    # not an answer.
    assert not isinstance(refused.value, AttributeError)


def test_expected_fill_refuses_a_sku_this_cart_never_backordered():
    """Matching `fillable`: a sku this cart never backordered raises, an
    unknown sku included."""
    catalog = restocked_catalog()
    cart = build_cart(catalog)
    cart.backorder("SKU-2", 5)

    with pytest.raises(Exception) as never:
        cart.expected_fill("SKU-3")
    assert not isinstance(never.value, AttributeError)
    with pytest.raises(Exception):
        cart.expected_fill("SKU-404")


def test_reading_the_lead_times_leaves_the_k1_and_k2_figures_untouched():
    """Every figure here is derived on read. The requests, the ledger and
    both report readings are identical before and after the lead times are
    computed, so nothing in this Task consumed or rewrote the records its
    prerequisites hold."""
    catalog = restocked_catalog()
    report = api.RevenueReport([])
    before = ledger_pairs(catalog)

    report.restock_lead_times(catalog)
    catalog.slowest_restocks(None)

    assert ledger_pairs(catalog) == before
    assert len(report.unfilled_restocks(catalog)) == 8
    assert sorted(pair(row)[0].sku for row in report.open_restocks(catalog)) == [
        "SKU-2", "SKU-3"]
