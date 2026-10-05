"""M8 — A cart can be priced as of a date.

`Catalog.price_history` and `_PriceHistory.changes` expose the recorded
price changes; `Cart.priced_as_of` returns a dated view whose lines price
from them, `Cart.lines` honours a cart-level `pricing_date`,
`LineItem.line_total` gains a middle rung between an override and the
catalog price, and `RevenueReport.priced_as_of(dates)` reprices each order
at its own date.

Every price below is unround and no two are multiples of each other, and
every quantity is greater than one, so an implementation that ignores the
`as_of` date, reads the wrong recorded change, drops the quantity, or
confuses the override with the historical price produces a different number
rather than coinciding with the right one.
"""

from datetime import date

import pytest

from billing import api

# The catalog's current prices. 19.99 and 8.25 are not multiples of each
# other and neither is a multiple of any historical price below, so every
# subtotal in this file identifies exactly which price was used.
DESK_NOW = api.Money("19.99")
LAMP_NOW = api.Money("8.25")

# The recorded history. MARCH lowers the desk, JUNE raises it past its
# current price — so a dated view is not uniformly cheaper than today, and
# an implementation that "reprices" by applying a single discount rather
# than by reading the history cannot pass both.
MARCH = date(2026, 3, 1)
APRIL = date(2026, 4, 1)
JUNE = date(2026, 6, 15)
DESK_MARCH = api.Money("17.45")
DESK_JUNE = api.Money("23.07")
LAMP_APRIL = api.Money("7.40")

# A date strictly inside the March..April window for the desk and before
# April for the lamp, and one before every recorded change.
MAY = date(2026, 5, 20)
JANUARY = date(2026, 1, 10)

DESK_QTY = 3
LAMP_QTY = 2


def build_catalog():
    """A catalog with two skus, each carrying recorded price changes."""
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-1", "Desk", DESK_NOW, "furniture"))
    catalog.add(api.Product("SKU-2", "Lamp", LAMP_NOW, "lighting"))
    # Recorded out of chronological order on purpose: June before March.
    # `record` promises sorted insertion, and a reader of `changes()` must
    # not be able to tell the recording order from the result.
    catalog.record_price("SKU-1", JUNE, DESK_JUNE)
    catalog.record_price("SKU-1", MARCH, DESK_MARCH)
    catalog.record_price("SKU-2", APRIL, LAMP_APRIL)
    return catalog


def build_cart(catalog=None):
    catalog = catalog or build_catalog()
    customer = api.Customer("C-1", "Ada", "US-CA")
    cart = api.Cart(catalog, customer)
    cart.add("SKU-1", DESK_QTY)
    cart.add("SKU-2", LAMP_QTY)
    return cart


def build_order(order_id, cart):
    """An order with no discount and a registered 0% rate, so every figure
    this file asserts is the repriced line arithmetic and nothing else.

    The rate is registered rather than omitted because `TaxCalculator`
    raises `UnknownJurisdiction` for a jurisdiction it holds no rate for —
    a zero rate is a registered fact here, not a missing one.
    """
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "0"))
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )
    return api.Order(order_id, cart, pricing)


def test_price_history_for_a_sku_with_no_recorded_change_is_empty_not_none():
    """And it does not invent a base-price entry.

    Three wrong answers are killed here, not one: `None` (which forces a
    guard on every caller before iterating), a one-entry history holding
    `unit_price` (which claims an effective date nobody supplied), and a
    raise (which is reserved for a sku that does not exist).
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-9", "Stool", api.Money("31.15"), "furniture"))
    history = catalog.price_history("SKU-9")
    assert history is not None
    assert len(history) == 0
    assert list(history) == []
    assert api.Money("31.15") not in [price for _, price in history]


def test_price_history_raises_for_an_unknown_sku_rather_than_answering_empty():
    """"No price changes" and "no such product" are different facts.

    A caller pricing a line as of a date has to tell them apart: the first
    means price at `unit_price`, the second means the sku was a typo.
    `UnknownProduct` is the answer every other lookup on `Catalog` gives.
    """
    with pytest.raises(api.UnknownProduct):
        build_catalog().price_history("SKU-NOPE")


def test_changes_come_back_ascending_by_date_whatever_order_they_were_recorded():
    """June was recorded before March; the history reads March then June.

    Kills an implementation that returns the recorded order, and one that
    sorts by price (17.45 then 23.07 would coincide — so the assertion is
    on the dates as well as the prices, in the same pairs).
    """
    history = build_catalog().price_history("SKU-1")
    assert history == ((MARCH, DESK_MARCH), (JUNE, DESK_JUNE))
    assert [recorded for recorded, _ in history] == [MARCH, JUNE]


def test_two_changes_for_one_date_both_appear_and_the_last_recorded_prices_it():
    """The `C11` failure mode, stated: what counts as one entry, and which
    of two entries for the same date wins.

    Both are kept, because `changes()` audits what was recorded and
    dropping the correction would hide it. The *price* for that date is the
    last recorded, which is also what a dated cart charges — so three
    implementations die here: one that rejects the second recording, one
    that replaces the first (history of length 1), and one that resolves
    the date to the earlier entry.
    """
    catalog = build_catalog()
    corrected = api.Money("18.60")
    catalog.record_price("SKU-1", MARCH, corrected)
    history = catalog.price_history("SKU-1")
    assert len(history) == 3
    assert history == (
        (MARCH, DESK_MARCH),
        (MARCH, corrected),
        (JUNE, DESK_JUNE),
    )
    assert catalog.price_as_of("SKU-1", MARCH) == corrected
    # And the cart agrees with the catalog: 3 * 18.60 = 55.80, where the
    # superseded 17.45 would have given 52.35.
    view = build_cart(catalog).priced_as_of(MARCH)
    assert view.lines[0].line_total == api.Money("55.80")


def test_the_history_handed_back_cannot_be_appended_to():
    """A caller holding the catalog's own list could record a price change
    the catalog never validated — bypassing `record_price`'s currency
    check. A tuple refuses.
    """
    history = build_catalog().price_history("SKU-1")
    with pytest.raises(AttributeError):
        history.append((JANUARY, api.Money("1.00")))
    assert len(build_catalog().price_history("SKU-1")) == 2


def test_a_dated_cart_prices_every_line_from_the_history():
    """May: the desk at its March price, the lamp at its April price.

    3 * 17.45 + 2 * 7.40 = 52.35 + 14.80 = 67.15, where the current
    catalog gives 3 * 19.99 + 2 * 8.25 = 59.97 + 16.50 = 76.47. Both are
    asserted, so an implementation that reads the history for one line and
    the catalog for the other cannot land on either figure.
    """
    cart = build_cart()
    assert cart.subtotal == api.Money("76.47")
    view = cart.priced_as_of(MAY)
    desk, lamp = view.lines
    assert desk.as_of_price == DESK_MARCH
    assert lamp.as_of_price == LAMP_APRIL
    assert desk.line_total == api.Money("52.35")
    assert lamp.line_total == api.Money("14.80")
    assert view.subtotal == api.Money("67.15")


def test_the_effective_date_itself_is_already_in_effect():
    """The boundary has its own assertion, because off-by-one-day is the
    plausible wrong implementation and no other test in this file can see
    it: `bisect_left` instead of `bisect_right`, or `<` instead of `<=`.

    On June 15 the desk costs 23.07; on June 14 it still costs 17.45.
    3 * 23.07 = 69.21 against 3 * 17.45 = 52.35.
    """
    cart = build_cart()
    assert cart.priced_as_of(JUNE).lines[0].line_total == api.Money("69.21")
    assert cart.priced_as_of(date(2026, 6, 14)).lines[0].line_total == api.Money(
        "52.35"
    )


def test_a_date_before_every_recorded_change_prices_at_unit_price():
    """January precedes March and April, so `price_as_of`'s documented
    fallback applies: the product's own price, true from the moment the
    product existed — not the oldest recorded change, and not a raise.

    The subtotal therefore equals today's, 76.47, which on its own is also
    what an implementation ignoring `as_of` would produce. So this test
    also pins the resolved `as_of_price` per line and pins that the *same*
    cart dated in May answers differently — together they distinguish "the
    fallback was applied" from "the date was never consulted".
    """
    cart = build_cart()
    view = cart.priced_as_of(JANUARY)
    assert [line.as_of_price for line in view.lines] == [DESK_NOW, LAMP_NOW]
    assert view.subtotal == api.Money("76.47")
    assert cart.priced_as_of(MAY).subtotal == api.Money("67.15")


def test_a_manual_override_beats_the_history_and_the_history_beats_the_catalog():
    """All three rungs of `line_total`'s precedence in one claim.

    An override is a deliberate instruction with an audit reason attached,
    so a backdated price list does not discard it. The desk is overridden
    at 14.30 and the lamp is not: 3 * 14.30 + 2 * 7.40 = 42.90 + 14.80 =
    57.70. An implementation that let the historical price win would give
    52.35 + 14.80 = 67.15, and one that let the catalog price win for the
    un-overridden line would give 42.90 + 16.50 = 59.40. All three figures
    are distinct, which is why 14.30 is not a round number.
    """
    cart = build_cart()
    cart.override_price("SKU-1", api.Money("14.30"), "manager approved")
    view = cart.priced_as_of(MAY)
    desk, lamp = view.lines
    assert desk.line_total == api.Money("42.90")
    assert lamp.line_total == api.Money("14.80")
    assert view.subtotal == api.Money("57.70")
    # The history was still resolved for the overridden line; it simply did
    # not win. A view that dropped it would report the override as the only
    # price this line ever had.
    assert desk.as_of_price == DESK_MARCH


def test_a_note_and_an_override_survive_into_the_dated_view():
    """Every per-sku thing the cart carries crosses into the view.

    A view that rebuilt only quantities would lose the audit reason for a
    price the customer was actually charged, and would lose a note like
    "gift wrap requested" that someone still has to act on.
    """
    cart = build_cart()
    cart.set_note("SKU-2", "gift wrap requested")
    cart.override_price("SKU-1", api.Money("14.30"), "manager approved")
    view = cart.priced_as_of(MAY)
    desk, lamp = view.lines
    assert lamp.note == "gift wrap requested"
    assert view.note_for("SKU-2") == "gift wrap requested"
    assert desk.override_price == api.Money("14.30")
    assert desk.overridden_by == "manager approved"


def test_priced_as_of_does_not_mutate_the_cart_in_either_direction():
    """The original is untouched after the view is built and after the view
    is changed, and the original's own lines still carry no date.

    Asserting the subtotal before and after is not enough on its own: a
    view that shared the original's sku-keyed maps would pass that and then
    leak every later `add` back into the original. So the view gains a line
    and the original is re-read.
    """
    cart = build_cart()
    before = cart.subtotal
    view = cart.priced_as_of(MAY)
    assert cart.subtotal == before == api.Money("76.47")
    assert cart.pricing_date is None
    assert [line.as_of_price for line in cart.lines] == [None, None]
    view.add("SKU-1", 4)
    assert cart.lines[0].quantity == DESK_QTY
    assert cart.subtotal == api.Money("76.47")
    assert view.pricing_date == MAY


def test_lines_with_no_pricing_date_carry_exactly_what_they_always_did():
    """An undated cart is not a cart dated as early as possible.

    The lines compare equal to hand-built `LineItem`s with no historical
    price at all — state, not merely an equal total, because a view that
    stored `unit_price` in `as_of_price` for an undated cart would produce
    the same subtotal and a different line.
    """
    catalog = build_catalog()
    cart = build_cart(catalog)
    assert cart.lines == [
        api.LineItem(catalog.get("SKU-1"), DESK_QTY),
        api.LineItem(catalog.get("SKU-2"), LAMP_QTY),
    ]
    assert [line.as_of_price for line in cart.lines] == [None, None]
    assert cart.subtotal == api.Money("76.47")


def test_priced_as_of_on_an_empty_cart_yields_no_lines_rather_than_raising():
    """There is nothing to look a price up for, and that is not an error —
    a cart a caller has not filled yet is a legitimate cart.
    """
    cart = api.Cart(build_catalog(), api.Customer("C-1", "Ada", "US-CA"))
    view = cart.priced_as_of(MAY)
    assert view.lines == []
    assert view.snapshot() == ()
    assert view.subtotal == api.Money("0.00")
    assert view.pricing_date == MAY


def test_priced_as_of_requires_a_date():
    """`priced_as_of(None)` would be a view priced at the current catalog,
    which is the cart the caller already holds — so it is a mistake, not a
    shorthand, and it says so at the call site.
    """
    with pytest.raises(TypeError):
        build_cart().priced_as_of(None)


def test_the_report_reprices_each_order_at_its_own_date():
    """Two orders, two dates, one call — not one date for the whole report.

    O-1 is priced in May: 3 * 17.45 + 2 * 7.40 = 67.15. O-2 holds two desks
    in June: 2 * 23.07 = 46.14. Keys come back sorted by order id. An
    implementation that applied one order's date to both would give 67.15
    and 34.90, or 69.21 and 46.14 — neither pair matches.
    """
    catalog = build_catalog()
    first = build_order("O-1", build_cart(catalog))
    second_cart = api.Cart(catalog, api.Customer("C-2", "Grace", "US-CA"))
    second_cart.add("SKU-1", 2)
    second = build_order("O-2", second_cart)
    for order in (first, second):
        order.place()
    report = api.RevenueReport([first, second])
    repriced = report.priced_as_of({"O-1": MAY, "O-2": JUNE})
    assert list(repriced) == ["O-1", "O-2"]
    assert repriced == {
        "O-1": api.Money("67.15"),
        "O-2": api.Money("46.14"),
    }


def test_the_report_raises_key_error_naming_the_order_for_a_missing_date():
    """Matching `by_period`, which this method's mapping argument copies.

    The order id is in the exception, because a report of forty orders
    missing one date is otherwise a hunt. A silent skip is the wrong
    answer: the caller would read a complete-looking dict.
    """
    order = build_order("O-1", build_cart())
    order.place()
    report = api.RevenueReport([order])
    with pytest.raises(KeyError) as caught:
        report.priced_as_of({"O-OTHER": MAY})
    assert "O-1" in str(caught.value)


def test_a_cancelled_line_is_excluded_from_the_repriced_figure():
    """Repricing must not resurrect revenue a cancellation removed.

    The lamp line is cancelled, so O-1 reprices to the desk alone:
    3 * 17.45 = 52.35, not the 67.15 of the whole cart. `Cart.lines` still
    carries the cancelled line — `cancel_line` does not touch the cart —
    so an implementation that summed the repriced view's lines without
    filtering would report 67.15 here and look right everywhere else.
    """
    order = build_order("O-1", build_cart())
    order.place()
    order.cancel_line("SKU-2")
    assert len(order.cart.lines) == 2
    report = api.RevenueReport([order])
    assert report.priced_as_of({"O-1": MAY}) == {"O-1": api.Money("52.35")}


def test_repricing_a_report_never_changes_the_orders_it_was_built_from():
    """A counterfactual, not a write. The order is the authority on what
    was charged, and this module's standing rule is that reports never
    reprice orders — so the rule is kept by returning a separate value,
    and that is asserted rather than assumed.
    """
    order = build_order("O-1", build_cart())
    order.place()
    report = api.RevenueReport([order])
    assert report.gross == api.Money("76.47")
    repriced = report.priced_as_of({"O-1": MAY})
    assert repriced["O-1"] == api.Money("67.15")
    assert report.gross == api.Money("76.47")
    assert report.net == api.Money("76.47")
    assert order.subtotal == api.Money("76.47")
    assert order.cart.pricing_date is None
    assert report.to_rows() == (
        (
            "O-1",
            api.Money("76.47"),
            api.Money("0.00"),
            api.Money("0.00"),
            api.Money("76.47"),
        ),
    )
