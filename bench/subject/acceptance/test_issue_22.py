"""K4 — An order ships in parts.

`Shipment(shipment_id, skus, dispatched_on)`, `Order.dispatch`,
`Order.shipments` in `sales/orders.py`; `Cart.dispatchable_skus` in
`sales/cart.py`; `RevenueReport.undispatched_orders` in
`reporting/reports.py`.

K4 is the head of Chain B, so the names above are a contract K5 and K6 are
authored against, and this file pins them by name as well as by behaviour.

Two things shape every fixture below.

First, **dispatch must change no figure**, and the only way to see that is
to price the order so that *any* leakage of dispatch into the pipeline moves
a number. The shared order carries three lines at 200.00, 70.00 and 50.00
under a 10% discount, one line tax-exempt: subtotal 320.00, discount 32.00,
tax 24.30 (10% of the taxable lines' discounted shares, 180.00 + 63.00),
total 312.30. An implementation that dropped dispatched lines out of
`_active_lines` would report 250.00/25.00/22.50/247.50 after the first
shipment, and every one of those four digits differs from the pinned figure.
A single-line cart at a round price would have left the leak and the
contract indistinguishable on three of the four figures.

Second, the three refusals `dispatch` owes are *three different exceptions*
(`InvalidTransition`, `UnknownLine`, `ValueError`), so each refusal is
asserted on its own type, and the cancelled-versus-unknown pair is asserted
on the message too. A suite that only checked "it raised" would pass an
implementation that collapsed all three into one, which is precisely the
distinction `cancel_line`/`UnknownLine` already draws and that K5's and
K6's callers inherit.

Every test here fails on `subject-v5`: each reaches a name the base tree
does not define, and the `pytest.raises` assertions name exceptions an
`AttributeError` does not satisfy.
"""

import pytest

from billing import api

# A date is an opaque string throughout this file. The package never reads a
# clock and never compares two dates, so a string is enough to prove a date
# is carried rather than invented -- and it is deliberately *not* a
# `datetime.date`, so an implementation that tried to compute with it fails
# here rather than in K6, where dates first acquire arithmetic.
DAY_ONE = "2026-09-03"
DAY_TWO = "2026-09-04"


def build_catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", api.Money("200.00"), "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", api.Money("35.00"), "furniture"))
    catalog.add(
        api.Product("SKU-3", "Gift card", api.Money("50.00"), "vouchers", taxable=False)
    )
    return catalog


def build_cart(catalog=None):
    catalog = catalog or build_catalog()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1")
    # Quantity 2 so a line's total (70.00) is not its unit price: an
    # implementation that shipped "a line" by unit price would not be caught
    # by a cart of single units.
    cart.add("SKU-2", 2)
    cart.add("SKU-3")
    return cart


def build_pricing():
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    return api.PricingContext(
        discount_engine=api.DiscountEngine([api.PercentageDiscount("loyal", "10")]),
        tax_calculator=api.TaxCalculator(table),
    )


def placed_order(order_id="O-1", cart=None):
    order = api.Order(order_id, cart or build_cart(), build_pricing())
    order.place()
    return order


PINNED_BREAKDOWN = {
    "subtotal": api.Money("320.00"),
    "discount": api.Money("32.00"),
    "tax": api.Money("24.30"),
    "total": api.Money("312.30"),
}


def test_the_shipment_type_is_exported_through_the_facade():
    """The suite sees the package only through `api`, and K5 gives a
    `Shipment` a destination, so the type has to be nameable from outside.
    """
    assert api.Shipment is not None
    assert "Shipment" in api.__all__


def test_a_shipment_is_frozen_and_normalizes_its_skus_to_a_tuple():
    """A record of something that already happened cannot be edited.

    The skus are normalized to a `tuple` in the caller's own order, not
    sorted and not a `frozenset`: a packing list's order is information, and
    the reverse-order construction below is what makes "preserved, not
    sorted" falsifiable.
    """
    shipment = api.Shipment("S-1", ["SKU-3", "SKU-1"], DAY_ONE)
    assert shipment.skus == ("SKU-3", "SKU-1")
    assert isinstance(shipment.skus, tuple)
    assert shipment.dispatched_on == DAY_ONE
    with pytest.raises(AttributeError):
        shipment.skus = ("SKU-2",)
    with pytest.raises(AttributeError):
        shipment.dispatched_on = DAY_TWO
    # A generator is accepted and consumed once, which a tuple-normalizing
    # constructor handles and a field that stored the iterable raw does not.
    from_generator = api.Shipment("S-2", (s for s in ("SKU-1", "SKU-2")), DAY_ONE)
    assert from_generator.skus == ("SKU-1", "SKU-2")


def test_a_shipment_of_nothing_is_rejected_at_construction():
    """And a bare string is not an iterable of skus.

    `skus="SKU-1"` would otherwise normalize to five single-character skus
    and be refused much later as five unknown lines, which names the wrong
    mistake. Validation is at construction, matching `Product`, so no order
    ever holds a malformed shipment.
    """
    with pytest.raises(ValueError):
        api.Shipment("S-1", [], DAY_ONE)
    with pytest.raises(ValueError):
        api.Shipment("S-1", (), DAY_ONE)
    with pytest.raises(TypeError):
        api.Shipment("S-1", "SKU-1", DAY_ONE)
    with pytest.raises(ValueError):
        api.Shipment("", ["SKU-1"], DAY_ONE)


def test_a_sku_named_twice_in_one_shipment_is_rejected_rather_than_collapsed():
    """Deduplicating would ship one unit while the caller believed two had
    gone, and the caller would never find out. The same "a unit cannot ship
    twice" rule the two-shipment case asserts, applied inside one shipment.
    """
    with pytest.raises(ValueError):
        api.Shipment("S-1", ["SKU-1", "SKU-1"], DAY_ONE)


def test_dispatched_on_is_required_and_is_never_read_from_a_clock():
    """No default, and `None` refused.

    `Catalog.record_price` and `Coupon.redeem` already take their date from
    the caller, and a shipment defaulting to today would stamp a backfilled
    record with the date of the backfill. `None` is pinned separately from
    the missing argument because a defaulted-to-`None` field would satisfy
    the `TypeError` assertion alone while still recording a dateless
    shipment.
    """
    with pytest.raises(TypeError):
        api.Shipment("S-1", ["SKU-1"])
    with pytest.raises(ValueError):
        api.Shipment("S-1", ["SKU-1"], None)
    order = placed_order()
    with pytest.raises(TypeError):
        order.dispatch("S-1", ["SKU-1"])
    with pytest.raises(ValueError):
        order.dispatch("S-1", ["SKU-1"], None)


def test_a_draft_order_cannot_dispatch():
    """Nothing ships before the order is placed, matching `refund_line`'s
    refusal on a draft: a draft has charged nobody and promised nothing.

    The order is left untouched by the refusal, and placing it afterwards
    makes the same call succeed -- without that second half, a `dispatch`
    that raised unconditionally would pass this test.
    """
    order = api.Order("O-1", build_cart(), build_pricing())
    with pytest.raises(api.InvalidTransition):
        order.dispatch("S-1", ["SKU-1"], DAY_ONE)
    assert order.shipments() == ()
    order.place()
    order.dispatch("S-1", ["SKU-1"], DAY_ONE)
    assert len(order.shipments()) == 1


def test_a_cancelled_order_cannot_dispatch():
    """An order nobody owes anything on has nothing to send, the same
    refusal `cancel_line` gives on a cancelled order.
    """
    order = placed_order()
    order.cancel()
    with pytest.raises(api.InvalidTransition):
        order.dispatch("S-1", ["SKU-1"], DAY_ONE)
    assert order.shipments() == ()


def test_dispatching_a_sku_with_no_active_line_raises_unknown_line():
    """Matching `cancel_line`: the sku was never on this order, or belongs
    to a different order's cart. A sku the catalog knows but this cart does
    not is the interesting half -- an implementation that validated against
    the catalog rather than the order's lines would pass on `SKU-9` alone.
    """
    catalog = build_catalog()
    catalog.add(api.Product("SKU-4", "Rug", api.Money("80.00"), "furniture"))
    order = placed_order(cart=build_cart(catalog))
    with pytest.raises(api.UnknownLine):
        order.dispatch("S-1", ["SKU-4"], DAY_ONE)
    with pytest.raises(api.UnknownLine):
        order.dispatch("S-1", ["SKU-9"], DAY_ONE)
    # And a shipment that mixes a good sku with a bad one records nothing:
    # there is no half-applied dispatch to reason about.
    with pytest.raises(api.UnknownLine):
        order.dispatch("S-1", ["SKU-1", "SKU-4"], DAY_ONE)
    assert order.shipments() == ()


def test_a_cancelled_line_is_refused_as_cancelled_not_as_unknown():
    """"No such line" and "that line is in the wrong state" are different
    facts, and `cancel_line`/`UnknownLine` already draw exactly this line.

    A cancelled sku is filtered out of the order's active lines, so the
    lazy implementation reports it as unknown. Both the exception type and
    the message are pinned, because `InvalidTransition` is also what an
    already-dispatched sku raises and only the message separates the two
    for a caller deciding whether a data fix is worth retrying.
    """
    order = placed_order()
    order.cancel_line("SKU-2")
    with pytest.raises(api.InvalidTransition) as cancelled:
        order.dispatch("S-1", ["SKU-2"], DAY_ONE)
    assert "cancel" in str(cancelled.value).lower()
    assert "SKU-2" in str(cancelled.value)
    with pytest.raises(api.UnknownLine) as unknown:
        order.dispatch("S-1", ["SKU-9"], DAY_ONE)
    assert "cancel" not in str(unknown.value).lower()
    # The surviving lines still ship.
    order.dispatch("S-1", ["SKU-1"], DAY_ONE)
    assert order.shipments()[0].skus == ("SKU-1",)


def test_a_sku_cannot_ship_twice_across_two_shipments():
    """A partial dispatch partitions the order's lines; it is not a
    multiset. The second shipment is refused and the first is left intact,
    so a refusal cannot be mistaken for a rollback of the whole order.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1", "SKU-2"], DAY_ONE)
    with pytest.raises(api.InvalidTransition):
        order.dispatch("S-2", ["SKU-2"], DAY_TWO)
    with pytest.raises(api.InvalidTransition):
        order.dispatch("S-2", ["SKU-3", "SKU-1"], DAY_TWO)
    assert len(order.shipments()) == 1
    assert order.shipments()[0].skus == ("SKU-1", "SKU-2")
    # SKU-3 was never in a shipment, so it still ships -- the refusal above
    # was about SKU-1, not about the attempt.
    order.dispatch("S-2", ["SKU-3"], DAY_TWO)
    assert order.shipments()[1].skus == ("SKU-3",)


def test_a_duplicate_shipment_id_is_refused_and_the_first_shipment_stands():
    """Two shipments sharing an id make `shipments()` ambiguous as a lookup
    for every later reader -- K5 keys `tax_by_shipment()` by exactly this
    field. `ValueError` rather than `InvalidTransition`: this is about the
    identifier the caller chose, not about the order's state.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_ONE)
    with pytest.raises(ValueError):
        order.dispatch("S-1", ["SKU-2"], DAY_TWO)
    assert len(order.shipments()) == 1
    assert order.shipments()[0].skus == ("SKU-1",)
    assert order.shipments()[0].dispatched_on == DAY_ONE
    # The id is unique per order, not globally: a second order may reuse it.
    other = placed_order("O-2")
    other.dispatch("S-1", ["SKU-1"], DAY_ONE)
    assert other.shipments()[0].shipment_id == "S-1"


def test_shipments_is_an_immutable_tuple_in_dispatch_order():
    """Dispatch order, matching `transitions()` -- not sorted by id and not
    sorted by date. A back-dated shipment must not rewrite the sequence of
    events that actually happened, which is why the second dispatch below
    carries the *earlier* date and the *lower* id.
    """
    order = placed_order()
    first = order.dispatch("S-9", ["SKU-2"], DAY_TWO)
    second = order.dispatch("S-1", ["SKU-1"], DAY_ONE)
    assert order.shipments() == (first, second)
    assert [s.shipment_id for s in order.shipments()] == ["S-9", "S-1"]
    assert isinstance(order.shipments(), tuple)
    # The caller cannot extend the order's history through what it was
    # handed, and a second read is unaffected by what it did with the first.
    handed = order.shipments()
    with pytest.raises(AttributeError):
        handed.append(api.Shipment("S-3", ["SKU-3"], DAY_ONE))
    assert len(order.shipments()) == 2


def test_dispatch_returns_the_shipment_it_recorded():
    """The same courtesy `refund_line` extends with its `CreditNote`: the
    caller does not have to search `shipments()` for the record it just
    created.
    """
    order = placed_order()
    shipment = order.dispatch("S-1", ["SKU-1", "SKU-3"], DAY_ONE)
    assert isinstance(shipment, api.Shipment)
    assert shipment.shipment_id == "S-1"
    assert shipment.skus == ("SKU-1", "SKU-3")
    assert shipment.dispatched_on == DAY_ONE
    assert order.shipments() == (shipment,)


def test_dispatch_appends_one_transition_and_leaves_the_existing_trail_alone():
    """One entry per dispatch, under its own event name, pinned field by
    field -- including the fields it does *not* set.

    A dispatch names a shipment, not a line, so `sku` stays `None` and the
    id travels in its own field: a reader filtering the trail by sku must
    not pick up dispatches as if they were lines. `as_of` is the shipment's
    own `dispatched_on` rather than `None`, because unlike `place()` and
    `cancel()` a dispatch is never dateless. The `placed` entry is pinned
    too, so an implementation that rewrote the trail's existing shape to
    make room fails here rather than silently.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1", "SKU-2"], DAY_ONE)
    order.dispatch("S-2", ["SKU-3"], DAY_TWO)
    trail = order.transitions()
    assert [t.event for t in trail] == ["placed", "dispatched", "dispatched"]
    assert trail[0].status == "placed"
    assert trail[0].as_of is None
    assert trail[0].sku is None
    assert trail[0].shipment_id is None
    assert trail[1].status == "placed"
    assert trail[1].as_of == DAY_ONE
    assert trail[1].sku is None
    assert trail[1].shipment_id == "S-1"
    assert trail[2].as_of == DAY_TWO
    assert trail[2].shipment_id == "S-2"
    # A refused dispatch records nothing, so the trail is not an attempt log.
    with pytest.raises(api.InvalidTransition):
        order.dispatch("S-3", ["SKU-1"], DAY_TWO)
    assert len(order.transitions()) == 3


def test_dispatch_changes_no_pipeline_figure():
    """Shipping an order does not reprice it.

    All four figures are pinned before and after, and with a 10% discount
    allocated across three lines one of which is tax-exempt, so an
    implementation that excluded dispatched lines from the active set would
    report 250.00 / 25.00 / 22.50 / 247.50 -- four different digits, not a
    rounding difference. `cancelled_skus()` is pinned empty too: dispatch is
    not a quiet cancellation.
    """
    order = placed_order()
    assert order.breakdown() == PINNED_BREAKDOWN
    order.dispatch("S-1", ["SKU-1", "SKU-2"], DAY_ONE)
    assert order.breakdown() == PINNED_BREAKDOWN
    order.dispatch("S-2", ["SKU-3"], DAY_TWO)
    assert order.breakdown() == PINNED_BREAKDOWN
    assert order.subtotal == api.Money("320.00")
    assert order.taxable_base == api.Money("243.00")
    assert order.cancelled_skus() == frozenset()
    assert order.status == "placed"


def test_dispatch_does_not_disturb_the_cancellation_and_refund_figures():
    """The two axes are independent in both directions.

    A line cancelled after a dispatch still refunds the exact drop in
    `total` that cancelling it caused, which is the property
    `CreditNote.amount` is built on. Under the allocated 10% discount that
    drop is 69.30 for SKU-2 (70.00 of line total, less 7.00 of discount
    share, plus 6.30 of tax), not the line's 70.00 catalog total -- a figure
    only the real pipeline produces.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_ONE)
    order.cancel_line("SKU-2")
    note = order.refund_line("SKU-2", reason="damaged")
    assert note.amount == api.Money("69.30")
    assert order.total == api.Money("243.00")
    # The shipment is untouched by the cancellation of an unrelated line.
    assert order.shipments()[0].skus == ("SKU-1",)


def test_dispatchable_skus_excludes_nothing_on_a_plain_cart():
    """Every line a cart carries is a line a warehouse could pick.

    Sorted ascending and a tuple, so two carts built in different orders
    compare equal -- the skus are added below in descending order to make
    that falsifiable. A sku that is only noted or only price-overridden has
    no line yet and so is absent rather than excluded: `set_note` and
    `override_price` accept a sku before it is added, and a cart cannot ship
    a line it does not have.
    """
    catalog = build_catalog()
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    assert cart.dispatchable_skus() == ()
    cart.add("SKU-3")
    cart.add("SKU-1")
    cart.set_note("SKU-2", "gift wrap requested")
    assert cart.dispatchable_skus() == ("SKU-1", "SKU-3")
    assert isinstance(cart.dispatchable_skus(), tuple)
    cart.add("SKU-2", 2)
    assert cart.dispatchable_skus() == ("SKU-1", "SKU-2", "SKU-3")
    cart.remove("SKU-1")
    assert cart.dispatchable_skus() == ("SKU-2", "SKU-3")


def test_dispatchable_skus_is_not_order_aware():
    """The cart reports what exists; the order reports what may still be
    sent, and `Order.dispatch` is the authority on the second.

    A cart can back several orders that cancelled and dispatched different
    lines -- which is why `cancel_line` is an `Order`-level fact at all --
    so a cart filtering by any one order's state would have to pick one
    arbitrarily. Two orders over the same cart are built here for exactly
    that reason: whichever one a cart-level filter chose, this assertion
    would catch it.
    """
    cart = build_cart()
    first = api.Order("O-1", cart, build_pricing())
    second = api.Order("O-2", cart, build_pricing())
    first.place()
    second.place()
    first.cancel_line("SKU-1")
    second.dispatch("S-1", ["SKU-3"], DAY_ONE)
    assert cart.dispatchable_skus() == ("SKU-1", "SKU-2", "SKU-3")
    # And the order disagrees, which is the point.
    with pytest.raises(api.InvalidTransition):
        first.dispatch("S-2", ["SKU-1"], DAY_ONE)
    with pytest.raises(api.InvalidTransition):
        second.dispatch("S-2", ["SKU-3"], DAY_TWO)


def test_undispatched_orders_names_every_order_with_an_outstanding_line():
    """At least one active line in no shipment, sorted by order id.

    Sorted rather than in construction order: the orders are handed to the
    report in descending id order below, so an implementation that returned
    them as given fails. A partially dispatched order is still outstanding,
    which is the reading that makes the method useful as a work list.
    """
    untouched = placed_order("O-3")
    partial = placed_order("O-1")
    partial.dispatch("S-1", ["SKU-1"], DAY_ONE)
    complete = placed_order("O-2")
    complete.dispatch("S-2", ["SKU-1", "SKU-2", "SKU-3"], DAY_ONE)
    report = api.RevenueReport([untouched, partial, complete])
    assert report.undispatched_orders() == ("O-1", "O-3")
    assert isinstance(report.undispatched_orders(), tuple)


def test_a_fully_dispatched_order_drops_off_the_work_list():
    """Across several shipments, not only in one.

    The report holds no snapshot, so the same report object reports the
    order gone once the last line ships -- the property `RevenueReport`'s
    own docstring claims for every figure, asserted here for this one.
    """
    order = placed_order()
    report = api.RevenueReport([order])
    assert report.undispatched_orders() == ("O-1",)
    order.dispatch("S-1", ["SKU-2"], DAY_ONE)
    assert report.undispatched_orders() == ("O-1",)
    order.dispatch("S-2", ["SKU-1", "SKU-3"], DAY_TWO)
    assert report.undispatched_orders() == ()


def test_a_cancelled_line_is_not_an_outstanding_shipment():
    """A cancelled line is gone, not awaiting dispatch.

    Reading it as undispatched would keep the order on the work list
    forever, waiting on a line nobody will ever send. So an order whose only
    un-shipped line was cancelled is complete, and an order with a cancelled
    line plus a real outstanding one is still listed -- the second half
    rules out an implementation that dropped any order with a cancellation.
    """
    tidy = placed_order("O-1")
    tidy.cancel_line("SKU-3")
    tidy.dispatch("S-1", ["SKU-1", "SKU-2"], DAY_ONE)
    outstanding = placed_order("O-2")
    outstanding.cancel_line("SKU-3")
    outstanding.dispatch("S-2", ["SKU-1"], DAY_ONE)
    report = api.RevenueReport([tidy, outstanding])
    assert report.undispatched_orders() == ("O-2",)


def test_a_cancelled_order_and_an_empty_order_are_both_absent():
    """A cancelled order is finished, excluded at construction like every
    other figure this class reports. An empty order has no line awaiting
    dispatch either -- stated because "nothing has shipped" is a tempting
    reading of the same order, and it would put an empty order on a work
    list forever.
    """
    cancelled = placed_order("O-1")
    cancelled.cancel()
    empty_cart = api.Cart(build_catalog(), api.Customer("C-1", "Ada", "US-CA"))
    empty = placed_order("O-2", cart=empty_cart)
    live = placed_order("O-3")
    report = api.RevenueReport([cancelled, empty, live])
    assert report.undispatched_orders() == ("O-3",)


def test_dispatch_leaves_every_other_report_figure_alone():
    """The report's existing figures are pinned across a full dispatch.

    `undispatched_orders()` is a new question, not a new filter: if dispatch
    had leaked into `_active_lines`, `by_category`, `top_products` and
    `tax_by_jurisdiction` would all move, and they are the three shapes that
    read lines rather than order-level totals.
    """
    order = placed_order()
    report = api.RevenueReport([order])
    before = (
        report.gross,
        report.net,
        report.tax_collected,
        report.by_category(),
        report.top_products(),
        report.tax_by_jurisdiction(),
        report.to_rows(),
    )
    order.dispatch("S-1", ["SKU-1", "SKU-2", "SKU-3"], DAY_ONE)
    assert report.undispatched_orders() == ()
    after = (
        report.gross,
        report.net,
        report.tax_collected,
        report.by_category(),
        report.top_products(),
        report.tax_by_jurisdiction(),
        report.to_rows(),
    )
    assert after == before
    assert before[3] == {
        "furniture": api.Money("270.00"),
        "vouchers": api.Money("50.00"),
    }
    assert before[5] == {"US-CA": api.Money("24.30")}
