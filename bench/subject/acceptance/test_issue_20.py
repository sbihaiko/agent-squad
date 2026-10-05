"""K2 - A receipt closes a restock request.

`RestockReceipt(sku, quantity, received_on)`, `Catalog.receive_restock`
closing the oldest open `RestockOrder` for a sku, `Catalog.restock_ledger`
pairing each request with its receipt or `None`, `Cart.fillable` answering
whether a backorder can now be filled, and `RevenueReport.open_restocks`
reporting the ledger's unclosed rows.

Every assertion below is one the issue text (20-*.md) states (ADR-0119). The
text names no exception class, no row attribute and no remainder reader, so
the test asks only that a refusal raises, reads a ledger row as the pair the
text names (a request and its receipt or `None`), and reads a remainder by
what it does: a receipt for more than the remainder raises, and one for
exactly the remainder closes the request. Every date arrives from the test
because the package reads no clock.

The fixture is built so the decisions this Task has to make each have a
distinguishing case: SKU-1 carries two requests, 7 units dated January and 5
units dated March, registered later-dated first, so a receipt of 7 closes the
January request under first-in-first-out and raises under last-in-first-out.
SKU-2 asks for 2 units and receives 1, a partial receipt.
"""

from datetime import date

import pytest

from billing import api

JANUARY = date(2026, 1, 9)
FEBRUARY = date(2026, 2, 2)
MARCH = date(2026, 3, 5)
APRIL = date(2026, 4, 1)
MAY = date(2026, 5, 1)


def build_catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", api.Money("40.00"), "furniture"))
    catalog.add(api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers"))
    return catalog


def stocked_catalog():
    """Four requests, registered in neither date nor sku nor size order.

    Read back by `restock_requests()` they are
    (SKU-1, 7, JANUARY), (SKU-2, 2, FEBRUARY), (SKU-1, 5, MARCH),
    (SKU-3, 4, MARCH).
    """
    catalog = build_catalog()
    catalog.request_restock("SKU-1", 5, MARCH)
    catalog.request_restock("SKU-1", 7, JANUARY)
    catalog.request_restock("SKU-2", 2, FEBRUARY)
    catalog.request_restock("SKU-3", 4, MARCH)
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


def ledger(catalog):
    """Each ledger row as (sku, requested quantity, receipt or None)."""
    out = []
    for row in catalog.restock_ledger():
        request, receipt = pair(row)
        out.append((request.sku, request.quantity, receipt))
    return out


def received(catalog):
    """Per ledger row: the receipt's (sku, quantity, received_on), or None."""
    return [
        None if receipt is None else (receipt.sku, receipt.quantity, receipt.received_on)
        for _, _, receipt in ledger(catalog)
    ]


def test_the_receipt_record_is_exported_and_carries_its_three_fields():
    receipt = api.RestockReceipt("SKU-1", 7, APRIL)
    assert receipt.sku == "SKU-1"
    assert receipt.quantity == 7
    assert receipt.received_on == APRIL


def test_a_receipt_with_nothing_to_close_is_refused_and_names_the_sku():
    """A delivery nobody ordered is a data error, not a no-op. Both ways of
    having nothing to close are asserted: a sku with no request, and one
    whose only request is already fully received."""
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-3", 4, APRIL)
    with pytest.raises(Exception) as already_closed:
        catalog.receive_restock("SKU-3", 1, MAY)
    assert "SKU-3" in str(already_closed.value)

    fresh = build_catalog()
    with pytest.raises(Exception) as unordered:
        fresh.receive_restock("SKU-2", 2, APRIL)
    assert "SKU-2" in str(unordered.value)
    assert len(list(fresh.restock_ledger())) == 0


def test_a_receipt_closes_the_oldest_open_request_for_that_sku():
    """First-in-first-out: SKU-1's oldest request is for 7 units and its
    newest for 5, so a receipt of 7 closes the January request."""
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-1", 7, APRIL)
    assert [(sku, quantity) for sku, quantity, _ in ledger(catalog)] == [
        ("SKU-1", 7), ("SKU-2", 2), ("SKU-1", 5), ("SKU-3", 4),
    ]
    assert received(catalog) == [("SKU-1", 7, APRIL), None, None, None]


def test_a_short_receipt_leaves_the_request_open_with_the_remainder_reduced():
    """Two units were asked for and one arrived, so one is still owed: two
    more raises, and one more closes the request."""
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-2", 1, MARCH)
    assert [(sku, quantity) for sku, quantity, _ in ledger(catalog)] == [
        ("SKU-1", 7), ("SKU-2", 2), ("SKU-1", 5), ("SKU-3", 4),
    ]
    with pytest.raises(Exception):
        catalog.receive_restock("SKU-2", 2, APRIL)
    rest = stocked_catalog()
    rest.receive_restock("SKU-2", 1, MARCH)
    rest.receive_restock("SKU-2", 1, APRIL)
    sku_2 = [row for row in ledger(rest) if row[0] == "SKU-2"]
    assert len(sku_2) == 1
    assert sku_2[0][2] is not None
    # The other requests are untouched.
    assert [r for (s, _, r) in ledger(catalog) if s != "SKU-2"] == [None, None, None]


def test_a_receipt_larger_than_what_is_owed_is_refused_outright():
    """The surplus is not credited to a later request for the same sku, and
    nothing is recorded."""
    catalog = stocked_catalog()
    with pytest.raises(Exception):
        catalog.receive_restock("SKU-3", 5, APRIL)
    with pytest.raises(Exception):
        catalog.receive_restock("SKU-1", 8, APRIL)  # more than the oldest owes
    assert received(catalog) == [None, None, None, None]
    catalog.receive_restock("SKU-2", 1, MARCH)
    with pytest.raises(Exception):
        catalog.receive_restock("SKU-2", 2, APRIL)  # more than the remainder
    assert received(catalog)[0] is None
    assert received(catalog)[2:] == [None, None]


def test_a_receipt_cannot_predate_the_request_it_closes():
    """SKU-2's request is dated February: a January receipt raises, and a
    February one (the same day) is accepted."""
    catalog = stocked_catalog()
    with pytest.raises(Exception):
        catalog.receive_restock("SKU-2", 1, JANUARY)
    assert received(catalog) == [None, None, None, None]
    catalog.receive_restock("SKU-2", 2, FEBRUARY)
    assert received(catalog)[1] == ("SKU-2", 2, FEBRUARY)


def test_the_receipt_date_is_required_and_never_read_from_a_clock():
    catalog = stocked_catalog()
    with pytest.raises(Exception):
        catalog.receive_restock("SKU-2", 2)
    with pytest.raises(Exception):
        catalog.receive_restock("SKU-2", 2, None)
    assert received(catalog) == [None, None, None, None]


def test_the_ledger_pairs_every_request_with_its_receipt_or_none_in_request_order():
    """One row per request, ordered as `restock_requests()` is."""
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-3", 4, MAY)
    rows = list(catalog.restock_ledger())
    assert [pair(row)[0] for row in rows] == list(catalog.restock_requests())
    assert received(catalog) == [None, None, None, ("SKU-3", 4, MAY)]


def test_a_sku_with_two_requests_and_one_receipt_shows_one_closed_and_one_open_row():
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-1", 7, APRIL)
    sku_1 = [row for row in ledger(catalog) if row[0] == "SKU-1"]
    assert len(sku_1) == 2
    (_, closed_quantity, closed_receipt), (_, open_quantity, open_receipt) = sku_1
    assert (closed_quantity, open_quantity) == (7, 5)
    assert closed_receipt is not None
    assert closed_receipt.quantity == 7
    assert open_receipt is None


def test_a_cart_backorder_is_unfillable_until_the_units_arrive():
    """`False` before a receipt and `True` after one that fully covers the
    backorder."""
    catalog = stocked_catalog()
    cart = build_cart(catalog)
    cart.backorder("SKU-2", 2)
    assert cart.fillable("SKU-2") is False
    catalog.receive_restock("SKU-2", 2, APRIL)
    assert cart.fillable("SKU-2") is True


def test_a_backorder_smaller_than_the_delivery_is_fillable():
    catalog = stocked_catalog()
    cart = build_cart(catalog)
    cart.backorder("SKU-1", 5)
    catalog.receive_restock("SKU-1", 7, APRIL)
    assert cart.fillable("SKU-1") is True


def test_fillable_refuses_a_sku_this_cart_never_backordered():
    """"No such backorder" and "not yet fillable" are different answers: the
    question raises rather than returning `False`, even when units for the
    sku have arrived."""
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-3", 4, MAY)
    cart = build_cart(catalog)
    cart.add("SKU-1", 1)
    cart.backorder("SKU-2", 2)
    with pytest.raises(Exception):
        cart.fillable("SKU-404")
    with pytest.raises(Exception):
        cart.fillable("SKU-3")
    with pytest.raises(Exception):
        cart.fillable("SKU-1")


def open_skus(report, catalog):
    return [(pair(row)[0].sku, pair(row)[0].quantity) for row in report.open_restocks(catalog)]


def test_open_restocks_drops_a_received_request_and_keeps_a_partly_received_one():
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-1", 7, APRIL)
    catalog.receive_restock("SKU-2", 1, MARCH)
    report = api.RevenueReport([])
    assert open_skus(report, catalog) == [("SKU-2", 2), ("SKU-1", 5), ("SKU-3", 4)]


def test_open_restocks_is_empty_when_every_request_is_received_and_for_none_at_all():
    """Both empties are answers, not errors. The figure reads the catalog it
    is handed and not the report's orders."""
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-1", 7, APRIL)
    catalog.receive_restock("SKU-1", 5, APRIL)
    catalog.receive_restock("SKU-2", 2, MARCH)
    catalog.receive_restock("SKU-3", 4, MAY)
    report = api.RevenueReport([])
    assert len(list(report.open_restocks(catalog))) == 0
    assert len(list(report.open_restocks(build_catalog()))) == 0


def test_unfilled_restocks_keeps_its_own_contract_across_a_receipt():
    """`K1`'s figure is a demand history: every request the catalog holds,
    sorted by sku. A receipt closes a request; it does not unmake it."""
    catalog = stocked_catalog()
    catalog.receive_restock("SKU-1", 7, APRIL)
    report = api.RevenueReport([])
    assert [(r.sku, r.quantity) for r in report.unfilled_restocks(catalog)] == [
        ("SKU-1", 7),
        ("SKU-1", 5),
        ("SKU-2", 2),
        ("SKU-3", 4),
    ]
    assert len(list(report.open_restocks(catalog))) == 3
