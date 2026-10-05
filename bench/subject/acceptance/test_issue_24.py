"""K6 — Dispatch performance is reportable.

`RevenueReport.dispatch_latency(dates)`,
`RevenueReport.slowest_destinations(dates, n=None)` and the module-level
`_dispatch_rows` fold in `reporting/reports.py`; `Order.dispatch_spans()` and
`Order._outstanding_skus()` in `sales/orders.py`;
`CustomerBook.dispatch_exposure(orders, currency=None)` and
`CustomerBook.over_exposed(orders, currency=None)` in `sales/customers.py`.

K6 closes Chain B. It is cut from K5's Reference Implementation, and its own
code calls K5's `Order.tax_by_shipment`, `Shipment.destination_scope` and
`core.tax.taxing_scope` as well as K4's `Order.shipments`. Those calls are
what the chain is measured by, so they are pinned here behaviourally rather
than structurally: every latency figure below is keyed by the scope
`taxing_scope` resolves, and every `slowest_destinations` row carries the tax
`tax_by_shipment` reported for the parcels behind it. An implementation that
re-derived either would have to reproduce K5's rounding and K5's fall-back
exactly to pass.

Four things shape the fixtures.

First, **dates acquire arithmetic here**, which they did not have in K4 or
K5. Those two suites used opaque strings deliberately: the package compares
no dates, and `Order.dispatch_spans` still does not. Latency is a *report*
figure, computed in the one module that already takes its dates from its
caller (`by_period`), so this file supplies real `datetime.date` objects and
the Subject still imports no `datetime` and names no date type.

Second, **the no-destination path must still not move**. The shared order is
K4's and K5's: 320.00 / 32.00 / 24.30 / 312.30, with allocated taxable
shares of 180.00 and 63.00. Those figures are pinned again after every new
reading here, because a report that repriced an order to measure it would be
a defect no other suite in the chain would catch.

Third, **the three rates stay different in both directions** — US-CA 10%,
US-NY 5%, NY-NYC 1% — so a tax figure computed for the wrong destination is
a different number rather than a coincidence, and **the latencies differ per
destination**, so a mean computed over the wrong grouping is a different
number too.

Fourth, **`dispatch_spans` is asserted against `dispatch_latency` on the same
order**, because the two deliberately disagree: a shipment that named no
destination has a span scope of `None` (nothing was written on the label) and
a latency key of the customer's own jurisdiction (that is where it was
taxed). An implementation that resolved the fall-back in both places, or in
neither, fails exactly one of those two assertions.

Every test here fails on `wip/k5-v5`: each reaches `dispatch_latency`,
`slowest_destinations`, `dispatch_spans`, `dispatch_exposure` or
`over_exposed`, none of which K5's tree defines, and the `pytest.raises`
assertions name `KeyError`, `ValueError` and `UnknownCustomer`, which the
`AttributeError` a missing method raises does not satisfy.
"""

import datetime

import pytest

from billing import api

#: The order's own date, and the dispatch dates measured against it. Real
#: dates rather than K4's opaque strings: a latency is a subtraction, and
#: `datetime.date` is the obvious type whose difference carries `.days`. The
#: Subject never names it — `_dispatch_rows` subtracts whatever it is handed.
ORDERED_ON = datetime.date(2026, 9, 1)
SAME_DAY = datetime.date(2026, 9, 1)
DAY_PLUS_ONE = datetime.date(2026, 9, 2)
DAY_PLUS_TWO = datetime.date(2026, 9, 3)
DAY_PLUS_FOUR = datetime.date(2026, 9, 5)
DAY_BEFORE = datetime.date(2026, 8, 31)


def build_catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", api.Money("35.00"), "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers", taxable=False)
    )
    return catalog


def build_cart(catalog=None, customer=None):
    catalog = catalog or build_catalog()
    cart = api.Cart(catalog, customer or api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1")
    cart.add("SKU-2", 2)
    cart.add("SKU-3")
    return cart


def build_table():
    """US-CA 10%, US-NY 5%, NY-NYC 1%, exactly as K5's suite registers them."""
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    table.add(api.TaxRate("US-NY", "5"))
    table.add(api.TaxRate("NY-NYC", "1"))
    return table


def build_pricing(table=None, discounts=None):
    return api.PricingContext(
        discount_engine=api.DiscountEngine(
            [api.PercentageDiscount("loyal", "10")] if discounts is None else discounts
        ),
        tax_calculator=api.TaxCalculator(table or build_table()),
    )


def placed_order(order_id="O-1", cart=None, pricing=None):
    order = api.Order(order_id, cart or build_cart(), pricing or build_pricing())
    order.place()
    return order


def book(*customers):
    registry = api.CustomerBook()
    for customer in customers:
        registry.add(customer)
    return registry


#: K4's and K5's figures, which nothing in this Task may move.
PINNED_BREAKDOWN = {
    "subtotal": api.Money("320.00"),
    "discount": api.Money("32.00"),
    "tax": api.Money("24.30"),
    "total": api.Money("312.30"),
}


def test_latency_is_dispatched_on_minus_the_orders_own_date_in_days():
    """Two parcels to one destination, at two and four days, mean 3.0.

    The date comes from the caller-supplied mapping, never from a clock: the
    same contract `by_period` has, and the reason this figure lives in
    `reporting/` rather than on `Order`, which holds no date of its own to
    subtract from. The mean is a plain division rather than a rounded
    figure, because a report that rounded it could not tell a 2.5-day
    destination from a 3-day one while claiming to rank them.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_PLUS_TWO, destination="US-NY")
    order.dispatch("S-2", ["SKU-2"], DAY_PLUS_FOUR, destination="US-NY")
    report = api.RevenueReport([order])
    assert report.dispatch_latency({"O-1": ORDERED_ON}) == {"US-NY": 3.0}
    # Same-day dispatch is a real event and reads zero, not an error.
    prompt = placed_order("O-2")
    prompt.dispatch("S-3", ["SKU-1"], SAME_DAY, destination="US-NY")
    assert api.RevenueReport([prompt]).dispatch_latency({"O-2": ORDERED_ON}) == {
        "US-NY": 0.0
    }


def test_a_missing_date_raises_key_error_naming_the_order():
    """Matching `by_period`, which raises the same way for the same reason.

    A report that silently skipped an order it could not date would
    under-report without saying so, and the order id is the only thing in
    the message that lets a caller fix the mapping. Both readings raise, so
    the rule is the fold's rather than one method's.
    """
    order = placed_order("O-7")
    order.dispatch("S-1", ["SKU-1"], DAY_PLUS_TWO, destination="US-NY")
    report = api.RevenueReport([order])
    with pytest.raises(KeyError) as missing:
        report.dispatch_latency({})
    assert missing.value.args[0] == "O-7"
    with pytest.raises(KeyError) as also_missing:
        report.slowest_destinations({})
    assert also_missing.value.args[0] == "O-7"
    # A mapping carrying some other order's date is still a missing entry.
    with pytest.raises(KeyError):
        report.dispatch_latency({"O-1": ORDERED_ON})


def test_an_undispatched_order_contributes_no_latency_and_needs_no_date():
    """Not counted as zero, and not required in `dates` either.

    An order that has not shipped is not an order that shipped instantly, so
    averaging it in as zero would flatter every destination it touched — the
    mean below stays 4.0 rather than halving to 2.0. And since the fold
    ignores such an order entirely, requiring a date for it would make a
    report over a mostly unshipped book unanswerable for no gain, which is
    why only `O-1` appears in the mapping.
    """
    dispatched = placed_order("O-1")
    dispatched.dispatch("S-1", ["SKU-1"], DAY_PLUS_FOUR, destination="US-NY")
    quiet = placed_order("O-2")
    report = api.RevenueReport([dispatched, quiet])
    assert report.dispatch_latency({"O-1": ORDERED_ON}) == {"US-NY": 4.0}
    assert report.slowest_destinations({"O-1": ORDERED_ON}) == (
        ("US-NY", 4.0, 1, api.Money("9.00")),
    )
    # The quiet order is still in the report for every other figure.
    assert report.undispatched_orders() == ("O-1", "O-2")


def test_a_negative_latency_raises_rather_than_being_averaged_in():
    """A parcel dispatched before its order's date is a data error.

    Reporting it would quietly improve the mean of the destination it went
    to, which is the opposite of what this figure is for, so the refusal
    names both the order and the shipment — the two things a caller needs to
    find the bad row. Zero is not negative and is allowed, which the
    same-day assertion above pins.
    """
    order = placed_order("O-1")
    order.dispatch("S-1", ["SKU-1"], DAY_BEFORE, destination="US-NY")
    report = api.RevenueReport([order])
    with pytest.raises(ValueError) as refused:
        report.dispatch_latency({"O-1": ORDERED_ON})
    assert "S-1" in str(refused.value)
    assert "O-1" in str(refused.value)
    with pytest.raises(ValueError):
        report.slowest_destinations({"O-1": ORDERED_ON})


def test_dispatch_latency_is_keyed_by_scope_with_the_customers_jurisdiction_as_fallback():
    """K5's fall-back, reached through the same `taxing_scope` rule.

    One parcel to US-NY and one with no destination at all, on an order
    whose customer is in US-CA: two keys, not one, and the undirected parcel
    lands under US-CA because that is where its tax was in fact collected —
    the same grouping `tax_by_destination` makes. Keys come back sorted, as
    every mapping this report returns does.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-2"], DAY_PLUS_ONE)
    order.dispatch("S-2", ["SKU-1"], DAY_PLUS_FOUR, destination="US-NY")
    report = api.RevenueReport([order])
    latency = report.dispatch_latency({"O-1": ORDERED_ON})
    assert list(latency) == ["US-CA", "US-NY"]
    assert latency == {"US-CA": 1.0, "US-NY": 4.0}


def test_a_compound_destination_is_one_key_and_sorts_after_the_scope_it_starts_with():
    """A city stacked on a state is one place, and groups as one row.

    `destination_scope` collapses a one-scope compound to a bare string, so
    `["US-NY"]` groups with plain `"US-NY"` rather than opening a row of its
    own; two or more scopes stay a tuple and sort immediately after the bare
    scope they start with, which is `_scope_key`'s ordering and the one a
    reader scanning a report wants.
    """
    compound = placed_order("O-1")
    compound.dispatch("S-1", ["SKU-1"], DAY_PLUS_FOUR, destination=["US-NY", "NY-NYC"])
    wrapped = placed_order("O-2")
    wrapped.dispatch("S-2", ["SKU-1"], DAY_PLUS_TWO, destination=["US-NY"])
    plain = placed_order("O-3")
    plain.dispatch("S-3", ["SKU-1"], DAY_PLUS_FOUR, destination="US-NY")
    report = api.RevenueReport([compound, wrapped, plain])
    dates = {"O-1": ORDERED_ON, "O-2": ORDERED_ON, "O-3": ORDERED_ON}
    latency = report.dispatch_latency(dates)
    assert list(latency) == ["US-NY", ("US-NY", "NY-NYC")]
    # The wrapped one-scope compound averages with the plain string: 2 and 4.
    assert latency == {"US-NY": 3.0, ("US-NY", "NY-NYC"): 4.0}


def test_dispatch_latency_is_ordered_by_scope_and_not_by_the_figure():
    """A mapping a person reads, keyed the way the module's others are.

    The two orders are built so the two orderings disagree: `US-CA` is the
    slower destination and sorts first, `US-NY` the faster and sorts second.
    An implementation sorting on the latency would report them the other way
    round, and `slowest_destinations` -- which ranks by the figure on purpose
    -- reads this mapping, so a scope order here is also what gives that
    method a stable tie-break to fall back on.
    """
    slow = placed_order("O-1")
    slow.dispatch("S-1", ["SKU-1"], DAY_PLUS_FOUR)
    fast = placed_order("O-2")
    fast.dispatch("S-2", ["SKU-1"], DAY_PLUS_ONE, destination="US-NY")
    report = api.RevenueReport([slow, fast])
    dates = {"O-1": ORDERED_ON, "O-2": ORDERED_ON}
    assert report.dispatch_latency(dates) == {"US-CA": 4.0, "US-NY": 1.0}
    assert list(report.dispatch_latency(dates)) == ["US-CA", "US-NY"]


def test_slowest_destinations_ranks_by_mean_latency_with_an_ascending_scope_tie_break():
    """Slowest first, and a tie broken by scope ascending — `top_products`'
    own two-pass convention, so a tie never comes back in a different order
    on a different run.

    Three destinations on one order: US-NY and US-CA both at four days, and
    NY-NYC at one. The tie puts `"US-CA"` before `"US-NY"` because the
    tie-break is ascending while the ranking is descending, and an
    implementation that sorted on a single compound key would have to get
    both directions right.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_PLUS_FOUR, destination="US-NY")
    order.dispatch("S-2", ["SKU-2"], DAY_PLUS_FOUR)
    order.dispatch("S-3", ["SKU-3"], DAY_PLUS_ONE, destination="NY-NYC")
    report = api.RevenueReport([order])
    ranked = report.slowest_destinations({"O-1": ORDERED_ON})
    assert [row[0] for row in ranked] == ["US-CA", "US-NY", "NY-NYC"]
    assert [row[1] for row in ranked] == [4.0, 4.0, 1.0]


def test_slowest_destinations_returns_all_for_none_and_refuses_a_negative_n():
    """`n=None` means all of them in order, matching `top_products(n=None)`;
    a negative `n` raises `ValueError`, also matching it; an `n` past the end
    returns what exists without padding.

    `dates` is the first argument and `n` the second, the shape
    `by_period(dates, bucket=None)` already has: a latency cannot be
    computed from what a `RevenueReport` holds without the caller's dates,
    so a one-argument `slowest_destinations(n)` would have nothing to rank.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_PLUS_FOUR, destination="US-NY")
    order.dispatch("S-2", ["SKU-2"], DAY_PLUS_ONE)
    report = api.RevenueReport([order])
    dates = {"O-1": ORDERED_ON}
    assert len(report.slowest_destinations(dates)) == 2
    assert len(report.slowest_destinations(dates, None)) == 2
    assert [row[0] for row in report.slowest_destinations(dates, 1)] == ["US-NY"]
    assert len(report.slowest_destinations(dates, 99)) == 2
    assert report.slowest_destinations(dates, 0) == ()
    with pytest.raises(ValueError):
        report.slowest_destinations(dates, -1)
    # And an empty report ranks nothing rather than raising.
    assert api.RevenueReport([]).slowest_destinations({}) == ()


def test_each_ranked_row_carries_the_shipment_count_and_the_tax_behind_its_mean():
    """The row is `(scope, mean_days, shipments, tax)`, and the tax is
    `Order.tax_by_shipment`'s figure rather than a second computation.

    A nine-day mean over one parcel and over three are different findings,
    so the count travels with the mean; and the tax is what makes a slow
    destination worth acting on. Reading it off `tax_by_shipment` is what
    keeps it from being a second rounding of the same amounts — the total
    below is asserted against that mapping directly, so an implementation
    that re-derived tax per destination would have to reproduce K5's
    per-shipment bankers rounding to agree.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_PLUS_TWO, destination="US-NY")
    order.dispatch("S-2", ["SKU-2"], DAY_PLUS_FOUR, destination="US-NY")
    order.dispatch("S-3", ["SKU-3"], DAY_PLUS_ONE)
    by_shipment = order.tax_by_shipment()
    report = api.RevenueReport([order])
    ranked = report.slowest_destinations({"O-1": ORDERED_ON})
    assert ranked == (
        ("US-NY", 3.0, 2, by_shipment["S-1"] + by_shipment["S-2"]),
        ("US-CA", 1.0, 1, by_shipment["S-3"]),
    )
    # Spelled out: 5% of the 180.00 and 63.00 taxable shares, and nothing at
    # all for the exempt gift card wherever it went.
    assert ranked[0][3] == api.Money("12.15")
    assert ranked[1][3] == api.Money("0.00")


def test_dispatch_spans_reports_one_span_per_shipment_in_dispatch_order():
    """`(shipment_id, dispatched_on, destination_scope, skus)`, in dispatch
    order — not sorted by id and not sorted by date, matching `shipments()`
    and `tax_by_shipment()`, which is why the second dispatch below carries
    the earlier date and the lower id.

    An immutable tuple of rows, like `shipments()` and `transitions()`: a
    caller holding the result cannot append to the order's history by
    mutating what it was handed. `skus` comes back as the shipment's own
    tuple, in the order it was packed.
    """
    order = placed_order()
    order.dispatch("S-9", ["SKU-2", "SKU-3"], DAY_PLUS_FOUR)
    order.dispatch("S-1", ["SKU-1"], DAY_PLUS_ONE, destination="US-NY")
    spans = order.dispatch_spans()
    assert isinstance(spans, tuple)
    assert spans == (
        ("S-9", DAY_PLUS_FOUR, None, ("SKU-2", "SKU-3")),
        ("S-1", DAY_PLUS_ONE, "US-NY", ("SKU-1",)),
    )
    assert [row[0] for row in spans] == list(order.tax_by_shipment())


def test_a_partially_dispatched_order_names_what_did_not_ship():
    """A final `(None, None, None, skus)` row, matching `tax_by_shipment`'s
    `None` key and placed after every real shipment.

    Omitting the remainder would let a caller sum the spans and conclude the
    order was fully sent. The row disappears when the last line ships —
    absent rather than empty, again matching `tax_by_shipment`, which drops
    the key entirely for a fully dispatched order — and a cancelled line is
    outstanding nowhere, because it is gone rather than unshipped.
    """
    order = placed_order()
    assert order.dispatch_spans() == ((None, None, None, ("SKU-1", "SKU-2", "SKU-3")),)
    order.dispatch("S-1", ["SKU-1"], DAY_PLUS_ONE, destination="US-NY")
    part = order.dispatch_spans()
    assert part[-1] == (None, None, None, ("SKU-2", "SKU-3"))
    assert list(order.tax_by_shipment()) == ["S-1", None]
    order.cancel_line("SKU-3")
    assert order.dispatch_spans()[-1] == (None, None, None, ("SKU-2",))
    order.dispatch("S-2", ["SKU-2"], DAY_PLUS_TWO)
    assert order.dispatch_spans() == (
        ("S-1", DAY_PLUS_ONE, "US-NY", ("SKU-1",)),
        ("S-2", DAY_PLUS_TWO, None, ("SKU-2",)),
    )
    assert None not in order.tax_by_shipment()


def test_a_span_records_the_label_while_the_latency_key_resolves_the_fallback():
    """The one place the two deliberately disagree.

    A shipment that named no destination has `None` on its span — nothing
    was written on the label, and `Order` knows nothing about a customer to
    invent one from — while the report keys its latency under the customer's
    own jurisdiction, because that is where its tax was collected. An
    implementation that resolved the fall-back in `dispatch_spans` would
    fail the first assertion; one that dropped it in the report would fail
    the second. A compound destination appears on the span already collapsed
    by `destination_scope`, so the two agree on what one place is.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-2"], DAY_PLUS_TWO)
    order.dispatch("S-2", ["SKU-1"], DAY_PLUS_TWO, destination=["US-NY"])
    spans = order.dispatch_spans()
    assert [row[2] for row in spans if row[0] is not None] == [None, "US-NY"]
    # The unshipped remainder carries no scope either: nothing has a label.
    assert spans[-1] == (None, None, None, ("SKU-3",))
    latency = api.RevenueReport([order]).dispatch_latency({"O-1": ORDERED_ON})
    assert sorted(latency) == ["US-CA", "US-NY"]


def test_dispatch_exposure_sums_dispatched_lines_per_customer_over_every_order():
    """Dispatched and unsettled, summed per customer, keyed by customer id.

    This package records no payment of any kind, so "not yet paid" is every
    dispatched line of an order that is still placed. SKU-1's line is 200.00
    and SKU-2's two units are 70.00, at catalog value rather than at a share
    of the discounted total: exposure is what left the warehouse, and an
    allocated share would make one customer's exposure move when an
    unrelated line of the same order was cancelled.

    Two orders for the same customer sum, and a customer registered in the
    book with nothing on its way appears at zero rather than being absent —
    a missing key would be indistinguishable from a customer the book has
    never heard of.
    """
    ada = api.Customer("C-1", "Ada", "US-CA")
    bo = api.Customer("C-2", "Bo", "US-NY")
    cy = api.Customer("C-3", "Cy", "US-CA")
    first = placed_order("O-1", cart=build_cart(customer=ada))
    first.dispatch("S-1", ["SKU-1"], DAY_PLUS_ONE)
    second = placed_order("O-2", cart=build_cart(customer=ada))
    second.dispatch("S-2", ["SKU-2"], DAY_PLUS_ONE, destination="US-NY")
    third = placed_order("O-3", cart=build_cart(customer=bo))
    third.dispatch("S-3", ["SKU-1", "SKU-3"], DAY_PLUS_TWO)
    registry = book(ada, bo, cy)
    assert registry.dispatch_exposure([first, second, third]) == {
        "C-1": api.Money("270.00"),
        "C-2": api.Money("250.00"),
        "C-3": api.Money("0.00"),
    }
    assert list(registry.dispatch_exposure([first])) == ["C-1", "C-2", "C-3"]


def test_dispatch_exposure_skips_a_cancelled_order_and_a_cancelled_line():
    """A cancelled order is not exposure, matching `RevenueReport`, which
    excludes one from every figure it reports; and a cancelled line is
    excluded even when it already shipped, which `Order` allows.

    Both exclusions matter for the same reason: exposure is what the
    business still expects to be paid for, and neither a cancelled order nor
    a cancelled line is owed. The surviving figure is 70.00 — SKU-2's two
    units — so an implementation that kept either would report 270.00.
    """
    ada = api.Customer("C-1", "Ada", "US-CA")
    live = placed_order("O-1", cart=build_cart(customer=ada))
    live.dispatch("S-1", ["SKU-1", "SKU-2"], DAY_PLUS_ONE)
    live.cancel_line("SKU-1")
    dead = placed_order("O-2", cart=build_cart(customer=ada))
    dead.dispatch("S-2", ["SKU-3"], DAY_PLUS_ONE)
    dead.cancel()
    registry = book(ada)
    assert registry.dispatch_exposure([live, dead]) == {"C-1": api.Money("70.00")}
    # The cancelled line is still on its shipment: the dispatch happened.
    assert live.shipments()[0].skus == ("SKU-1", "SKU-2")


def test_dispatch_exposure_over_an_empty_set_is_zero_in_the_stated_currency():
    """Never `Money.ZERO`'s USD by accident — the rule `R7` generalizes, and
    `customers.py` is not even on the structure gate's `Money.ZERO`
    allowlist.

    With nothing dispatched there is no amount to read a currency off, so
    the caller states one and gets zero in it. Stating none raises
    `ValueError` rather than guessing, exactly as
    `TaxCalculator.tax_for_shipment` refuses the same case. An empty book
    answers an empty mapping and needs no currency at all, because it has no
    entry to fill.
    """
    ada = api.Customer("C-1", "Ada", "US-CA")
    registry = book(ada)
    assert registry.dispatch_exposure([], currency="EUR") == {
        "C-1": api.Money("0.00", "EUR")
    }
    assert registry.dispatch_exposure([], currency="USD") == {"C-1": api.Money("0.00")}
    with pytest.raises(ValueError):
        registry.dispatch_exposure([])
    assert api.CustomerBook().dispatch_exposure([]) == {}
    # An order that has dispatched nothing is the same case as no orders.
    with pytest.raises(ValueError):
        registry.dispatch_exposure([placed_order(cart=build_cart(customer=ada))])


def test_dispatch_exposure_refuses_an_order_for_an_unregistered_customer():
    """`UnknownCustomer`, matching `get`.

    A book asked what its customers owe cannot answer for one it has never
    heard of, and silently dropping the order would under-report the total
    while looking like a complete answer. The refusal holds even when that
    order has dispatched nothing, because the book is asked about the
    customer before the order is valued.
    """
    ada = api.Customer("C-1", "Ada", "US-CA")
    stranger = api.Customer("C-9", "Zed", "US-CA")
    order = placed_order("O-1", cart=build_cart(customer=stranger))
    order.dispatch("S-1", ["SKU-1"], DAY_PLUS_ONE)
    registry = book(ada)
    with pytest.raises(api.UnknownCustomer):
        registry.dispatch_exposure([order])
    quiet = placed_order("O-2", cart=build_cart(customer=stranger))
    with pytest.raises(api.UnknownCustomer):
        registry.dispatch_exposure([quiet], currency="USD")


def test_over_exposed_names_only_customers_past_their_own_credit_limit():
    """`Customer.within_credit_limit`, inverted — one definition of "within
    the limit", so the two cannot come to disagree.

    Three customers, all exposed for 200.00: one with a 150.00 limit is
    over, one with a limit of exactly 200.00 is not (the `<=` is
    `within_credit_limit`'s own, not a second decision here), and one with no
    limit never appears, because unlimited credit cannot be exceeded. That
    last case is the distinction `credit_limit=None` exists to keep from a
    limit of zero, which can be exceeded — and is, below.
    """
    over = api.Customer("C-1", "Ada", "US-CA", credit_limit=api.Money("150.00"))
    exact = api.Customer("C-2", "Bo", "US-CA", credit_limit=api.Money("200.00"))
    free = api.Customer("C-3", "Cy", "US-CA")
    zero = api.Customer("C-4", "Di", "US-CA", credit_limit=api.Money("0.00"))
    # `Order.place` refuses an order whose total is above the customer's
    # limit, so each order is settled to a zero total, which every limit
    # admits. Exposure reads the catalog value of what shipped, which a
    # discount does not touch: each customer is exposed for 200.00 whatever
    # the total, and the limits — not the totals — decide who is over.
    settled = build_pricing(discounts=[api.PercentageDiscount("settled", "100")])
    orders = []
    for index, customer in enumerate((over, exact, free, zero), start=1):
        order = placed_order(
            f"O-{index}", cart=build_cart(customer=customer), pricing=settled
        )
        order.dispatch("S-1", ["SKU-1"], DAY_PLUS_ONE)
        orders.append(order)
    registry = book(over, exact, free, zero)
    assert registry.over_exposed(orders) == ("C-1", "C-4")
    # With nothing dispatched nobody is over, and no currency need be stated:
    # the comparison happens in the limits' own currency.
    assert registry.over_exposed([]) == ()
    assert book(free).over_exposed([]) == ()
    # One limited customer and nothing dispatched is the smallest book that
    # has a currency to seed from, and the seeding has to reach it: an
    # implementation that read the second limit would have nothing to read.
    assert book(over).over_exposed([]) == ()
    assert book(over).over_exposed(orders[:1]) == ("C-1",)


def test_every_reading_here_leaves_k4s_and_k5s_figures_exactly_as_they_were():
    """The whole chain's pinned numbers, asserted after all five new
    readings have run against the same order.

    A report that repriced an order in order to measure it, or an exposure
    figure that reached into the pricing pipeline and cached something,
    would show up here and nowhere else in this file. `tax_total` still
    rounds per line and `tax_by_shipment` still rounds per shipment, and
    K6 touches neither policy.
    """
    ada = api.Customer("C-1", "Ada", "US-CA")
    order = placed_order(cart=build_cart(customer=ada))
    order.dispatch("S-1", ["SKU-1", "SKU-2"], DAY_PLUS_TWO)
    order.dispatch("S-2", ["SKU-3"], DAY_PLUS_FOUR)
    report = api.RevenueReport([order])
    dates = {"O-1": ORDERED_ON}
    assert report.dispatch_latency(dates) == {"US-CA": 3.0}
    assert report.slowest_destinations(dates) == (
        ("US-CA", 3.0, 2, api.Money("24.30")),
    )
    assert len(order.dispatch_spans()) == 2
    assert book(ada).dispatch_exposure([order]) == {"C-1": api.Money("320.00")}
    assert book(ada).over_exposed([order]) == ()
    assert order.breakdown() == PINNED_BREAKDOWN
    assert order.tax_by_shipment() == {
        "S-1": api.Money("24.30"),
        "S-2": api.Money("0.00"),
    }
    assert report.tax_by_jurisdiction() == {"US-CA": api.Money("24.30")}
    assert report.tax_by_destination() == {"US-CA": api.Money("24.30")}
    assert report.undispatched_orders() == ()
