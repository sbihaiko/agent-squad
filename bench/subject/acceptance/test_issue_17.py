"""R7 — An empty collection stops being USD by accident.

`Money.zero`'s defaulted currency is gone, replaced by
`Money.seeded_from(source)`; `Cart`, `Order` and `RevenueReport` each report a
currency of their own; and an empty collection that cannot find out what
currency it is in raises instead of answering USD. The issue text (17-*.md) names no exception
class for that refusal, so the test asks only that it raises.

**Why the fixtures are EUR.** A restructuring states a shape, and a suite can
only see behavior, so every assertion below is pinned against a non-USD
fixture: that is the one place the old and the new implementation disagree
about a *value* rather than about a shape. An empty cart over a EUR catalog
reports `Money("0.00", "EUR")` here and `Money("0.00", "USD")` on the base
tree, and `Money.__eq__` compares the currency, so the two are
distinguishable. A USD fixture would have passed on both trees and asserted
nothing — which is exactly how `C12` came to pin a USD fixture as a
workaround, and is the workaround this Task retires.

Scale is deliberately never asserted. `Decimal("0.00") == Decimal("0")`, so a
zero's number of decimal places is invisible to every comparison in this file
and pinning it would constrain an implementation detail rather than a
behavior. What is asserted everywhere is the currency *label*.

Three tests below pin behavior this Task leaves unchanged (a filled cart's
subtotal, a coupon minimum's gating, `Money.ZERO` itself). Each of them would
pass on the base tree if it were written on its own, so each is entangled with
a name this Task introduces, in the same test, on purpose: the base suite
staying green is necessary but not sufficient, because the base suite is not
this suite, and an unchanged figure is only pinned here if this file can see
it.
"""

import pytest

from billing import api


def eur_catalog():
    """A catalog whose whole price list is quoted in EUR.

    Two products rather than one, so a filled cart's subtotal is a figure an
    implementation has to fold rather than copy: 250.00 + 2 x 125.50 = 501.00,
    which is the same arithmetic the Subject's own `test_subtotal_sums_line_
    totals` pins in USD.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-E1", "Bureau", api.Money("250.00", "EUR"), "furniture"))
    catalog.add(api.Product("SKU-E2", "Chaise", api.Money("125.50", "EUR"), "furniture"))
    return catalog


def usd_catalog():
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-U1", "Desk", api.Money("250.00"), "furniture"))
    return catalog


def mixed_catalog():
    """A price list quoting two currencies at once — the shape
    `Catalog.record_price` already refuses to create by price change, and
    which therefore has no single answer to "what currency is this catalog in".
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-E1", "Bureau", api.Money("250.00", "EUR"), "furniture"))
    catalog.add(api.Product("SKU-U1", "Desk", api.Money("250.00", "USD"), "furniture"))
    return catalog


def customer(customer_id="C-1"):
    return api.Customer(customer_id, "Ada", "US-CA")


def pricing():
    """A 10% US-CA rate and no discounts.

    Tax is left on rather than switched off: `tax_collected` is one of the
    three whole-order figures the entry names, and a rate of zero would make
    it indistinguishable from a figure that was never computed.
    """
    table = api.TaxTable()
    table.add(api.TaxRate("US-CA", "10"))
    return api.PricingContext(
        discount_engine=api.DiscountEngine([]),
        tax_calculator=api.TaxCalculator(table),
    )


def eur_order(order_id="O-1", sku="SKU-E1", place=True):
    cart = api.Cart(eur_catalog(), customer())
    cart.add(sku)
    order = api.Order(order_id, cart, pricing())
    if place:
        order.place()
    return order


def usd_order(order_id="O-9", place=True):
    cart = api.Cart(usd_catalog(), customer("C-2"))
    cart.add("SKU-U1")
    order = api.Order(order_id, cart, pricing())
    if place:
        order.place()
    return order


# -- The replacement for the defaulting zero ---------------------------------


def test_seeded_from_reads_the_currency_off_an_amount():
    """The fold-seeding case: a caller with an amount in hand.

    Asserted against EUR and not USD, because a `seeded_from` that ignored
    its source and returned the USD constant would satisfy a USD assertion.
    """
    assert api.Money.seeded_from(api.Money("501.00", "EUR")) == api.Money("0.00", "EUR")
    assert api.Money.seeded_from(api.Money("501.00", "EUR")) != api.Money.ZERO
    assert api.Money.seeded_from(api.Money("501.00", "EUR")).currency == "EUR"
    assert api.Money.seeded_from(api.Money("501.00", "EUR")).amount == 0


def test_seeded_from_also_takes_a_bare_currency_code_because_an_empty_fold_has_no_amount():
    """`Cart.subtotal` is both callers in turn.

    With lines it has an amount to copy a currency from; with none it has
    only the collection's own currency, and that is a code. A `seeded_from`
    that accepted amounts only would force the empty case back to a
    hand-written `Money("0.00", code)` — which is the duplication this Task
    removes, not a variant of it.
    """
    assert api.Money.seeded_from("EUR") == api.Money("0.00", "EUR")
    assert api.Money.seeded_from("EUR") == api.Money.seeded_from(api.Money("1.00", "EUR"))
    assert api.Money.seeded_from("USD") == api.Money.ZERO


def test_seeded_from_serves_a_currency_whose_minor_unit_is_not_two_places():
    """JPY has no minor unit and BHD prices in thousandths.

    The round trip is what is asserted rather than the zero's scale: adding a
    real amount to the seed must return that amount exactly, which is the only
    thing a fold's seed is for.
    """
    yen = api.Money("150", "JPY")
    dinar = api.Money("1.234", "BHD")
    assert api.Money.seeded_from("JPY") + yen == yen
    assert api.Money.seeded_from(dinar) + dinar == dinar
    assert api.Money.seeded_from("JPY").currency == "JPY"


def test_seeded_from_refuses_a_source_that_names_no_currency():
    """There is no default to fall back on, and that is the design.

    A seed built from `None` would be the defaulting zero again, one call
    deeper. `TypeError` rather than a named exception because this is a
    caller passing the wrong kind of thing, not a currency that cannot be
    determined.
    """
    for bad in (None, 0, 501, ("EUR",), api.Money):
        with pytest.raises(TypeError):
            api.Money.seeded_from(bad)


def test_the_zero_that_defaulted_to_usd_is_gone_and_seeded_from_does_its_work():
    """The removal, asserted through the replacement and through the default.

    A test that only asserted an absence (`not hasattr(Money, "zero")`) would
    pass on an empty module, so neither half of this test is an absence. The
    first half is the behavioral consequence of the removal: the defaulted
    call that used to answer a USD zero for anybody who asked for "a zero"
    now refuses, because there is no longer a definition with a default
    behind that name. The second half is that every call which did name a
    currency is served by `seeded_from`, which is the only zero factory left
    — the Subject's own suite reaches `Money.zero` by name, so the name
    survives as an alias to `seeded_from` and not as a second definition.
    """
    with pytest.raises(TypeError):
        api.Money.zero()
    assert api.Money.zero("EUR") == api.Money.seeded_from("EUR")
    assert api.Money.zero.__func__ is api.Money.seeded_from.__func__


# -- The cart ---------------------------------------------------------------


def test_an_empty_cart_over_a_eur_catalog_reports_a_eur_subtotal():
    """The headline behavioral consequence of the whole Task.

    Today the answer is `Money("0.00", "USD")` — a currency nobody asked
    about, for a cart that could only ever have charged in EUR. The second
    assertion is not redundant with the first: `Money.__eq__` compares the
    currency, so spelling out the inequality states which wrong answer is
    being refused.
    """
    cart = api.Cart(eur_catalog(), customer())
    assert cart.lines == []
    assert cart.currency == "EUR"
    assert cart.subtotal == api.Money("0.00", "EUR")
    assert cart.subtotal != api.Money.ZERO
    assert cart.subtotal.currency == "EUR"


def test_an_empty_cart_with_nothing_to_read_a_currency_off_raises():
    """The case the entry names: no lines, and no explicit currency.

    An empty catalog quotes no prices, so there is no third place to look.
    Both the currency and the subtotal raise, because a subtotal that
    answered while `currency` refused would be the same guess one level down.
    """
    cart = api.Cart(api.Catalog(), customer())
    with pytest.raises(Exception):
        cart.currency
    with pytest.raises(Exception):
        cart.subtotal


def test_a_stated_currency_settles_an_empty_cart_whose_catalog_cannot():
    """And it is the caller's statement that is honored, not a guess.

    JPY is chosen over EUR here because JPY's registered exponent is 0: an
    implementation that stated the currency but quantized the zero to two
    places still passes (scale is not a behavior), while one that ignored the
    statement and answered USD does not.
    """
    cart = api.Cart(api.Catalog(), customer(), currency="JPY")
    assert cart.currency == "JPY"
    assert cart.subtotal == api.Money("0", "JPY")
    assert cart.subtotal != api.Money.ZERO


def test_a_filled_carts_subtotal_is_unchanged_and_its_currency_is_its_lines():
    """The unchanged figure, pinned here and not only in the base suite.

    Entangled with `cart.currency` on purpose: the subtotal assertions alone
    would pass on the base tree, so on their own they would assert nothing
    about this Task. 501.00 is the same fold the Subject's own cart test
    pins, moved into EUR.
    """
    cart = api.Cart(eur_catalog(), customer())
    cart.add("SKU-E1")
    cart.add("SKU-E2", 2)
    assert cart.currency == "EUR"
    assert cart.subtotal == api.Money("501.00", "EUR")
    usd = api.Cart(usd_catalog(), customer())
    usd.add("SKU-U1", 2)
    assert usd.currency == "USD"
    assert usd.subtotal == api.Money("500.00")


def test_a_stated_currency_that_contradicts_the_lines_raises_rather_than_winning():
    """The lines are what `subtotal` adds, so they cannot be overruled.

    Silently honoring the statement would label a EUR figure USD; silently
    dropping it would leave a caller believing a statement that had no
    effect. Both are worse than telling the caller which two currencies
    disagree, and the subtotal refuses for the same reason the currency does.
    """
    cart = api.Cart(eur_catalog(), customer(), currency="USD")
    cart.add("SKU-E1")
    with pytest.raises(api.CurrencyMismatch):
        cart.currency
    with pytest.raises(api.CurrencyMismatch):
        cart.subtotal


def test_a_mixed_currency_catalog_is_answerable_only_once_the_cart_has_lines():
    """Two carts over the same unanswerable price list, one of which can
    answer for itself.

    The pair is the discrimination: an implementation that resolved the
    currency from the catalog *first* and the lines second would fail the
    second half, and one that never consulted the catalog would fail the
    first. `CurrencyMismatch` for the empty cart, because the catalog gave two
    answers rather than none.
    """
    empty = api.Cart(mixed_catalog(), customer())
    with pytest.raises(api.CurrencyMismatch):
        empty.currency
    filled = api.Cart(mixed_catalog(), customer())
    filled.add("SKU-E1")
    assert filled.currency == "EUR"
    assert filled.subtotal == api.Money("250.00", "EUR")


def test_a_cart_holding_two_currencies_raises_from_currency_naming_both():
    """The criterion's "here rather than one addition later".

    A cart over the mixed catalog with a line from each side cannot name a
    currency, and `currency` is where that is reported: it is the one place
    holding both codes, so the message names them, where the `Money`
    addition inside `subtotal` would name only the pair it happened to
    reach. An implementation that left this to `subtotal` passes every
    other criterion in this file and reports the failure in the wrong
    place, with no way for a caller to ask the question first.
    """
    cart = api.Cart(mixed_catalog(), customer())
    cart.add("SKU-E1")
    cart.add("SKU-U1")
    with pytest.raises(api.CurrencyMismatch) as raised:
        cart.currency
    assert "EUR" in str(raised.value)
    assert "USD" in str(raised.value)
    # And `subtotal` still refuses it, through the reader rather than
    # instead of it.
    with pytest.raises(api.CurrencyMismatch):
        cart.subtotal


def test_the_catalog_reports_the_one_currency_its_price_list_quotes():
    """The reader an empty cart asks, asserted on its own.

    `Catalog.record_price` already refuses a price in any currency but the
    product's own, on the stated grounds that this package has no notion of a
    multi-currency price list. This is that position as a question a caller
    can ask, and its two refusals are the two ways a price list can fail to
    answer: no prices at all, and prices that disagree.
    """
    assert eur_catalog().currency == "EUR"
    assert usd_catalog().currency == "USD"
    with pytest.raises(Exception):
        api.Catalog().currency
    with pytest.raises(api.CurrencyMismatch):
        mixed_catalog().currency


# -- The order --------------------------------------------------------------


def test_an_orders_currency_is_its_carts_and_every_pipeline_figure_agrees():
    """One EUR line at 250.00 under a 10% rate: 250.00 + 25.00 = 275.00 EUR.

    The figures are pinned alongside `currency` so that a `currency` reading
    EUR while the pipeline still computed in something else could not pass.
    """
    order = eur_order()
    assert order.currency == "EUR"
    assert order.subtotal == api.Money("250.00", "EUR")
    assert order.tax_total == api.Money("25.00", "EUR")
    assert order.total == api.Money("275.00", "EUR")
    assert order.currency == order.total.currency


def test_an_order_with_every_line_cancelled_still_reports_its_own_currency():
    """A zero order is still a EUR order.

    This is the order-level version of the empty cart: nothing is active, so
    there is no amount left to read a currency off, and the answer still must
    not be USD.
    """
    order = eur_order()
    order.cancel_line("SKU-E1")
    assert order.currency == "EUR"
    assert order.subtotal == api.Money("0.00", "EUR")
    assert order.subtotal != api.Money.ZERO


# -- The report -------------------------------------------------------------


def test_a_reports_figures_are_denominated_in_its_orders_currency():
    """Two EUR orders, no discounts: gross 500.00, net 500.00, tax 50.00.

    `net` equals `gross` here because this fixture has no discount, and the
    entry names all three figures, so all three are asserted rather than one
    standing in for the others — `C2`'s lesson: a figure with no reader is
    not a contract.
    """
    report = api.RevenueReport([eur_order("O-1"), eur_order("O-2", "SKU-E2")])
    assert report.currency == "EUR"
    assert report.gross == api.Money("375.50", "EUR")
    assert report.net == api.Money("375.50", "EUR")
    assert report.tax_collected == api.Money("37.55", "EUR")
    assert report.gross != api.Money("375.50", "USD")


def test_an_empty_report_reads_every_figure_in_the_currency_it_was_given():
    """The report's own version of a stated cart currency.

    All three whole-order figures, because all three route through the same
    fold and an implementation that fixed one by hand would pass a
    single-figure test.
    """
    report = api.RevenueReport([], currency="EUR")
    assert report.currency == "EUR"
    assert report.gross == api.Money("0.00", "EUR")
    assert report.net == api.Money("0.00", "EUR")
    assert report.tax_collected == api.Money("0.00", "EUR")
    assert report.gross != api.Money.ZERO


def test_an_empty_report_that_knows_nothing_refuses_to_name_a_currency():
    """And its figures keep the default the Subject's own suite pins.

    This is the one place the guess survives, and the asymmetry is
    deliberate rather than an oversight: `tests/acceptance/test_reporting.py`
    pins `RevenueReport([]).gross == Money("0.00")`, so a restructuring that
    made the fold raise there would be deleting behavior rather than moving
    it. `currency` is the new reader, bound by no such pin, and it refuses —
    so a caller who needs to know whether the figure is a real USD zero or a
    zero standing in for no answer at all has one place to find out.
    """
    report = api.RevenueReport([])
    with pytest.raises(Exception):
        report.currency
    assert report.gross == api.Money("0.00")
    assert report.net == api.Money("0.00")
    assert report.tax_collected == api.Money("0.00")


def test_a_report_whose_orders_disagree_on_currency_raises():
    """The second behavioral consequence the entry names.

    The disagreement is reported by the reader that can name the orders,
    rather than by whichever addition inside a fold happened to meet it
    first, and `currency` refuses before any figure is computed.
    """
    report = api.RevenueReport([eur_order("O-1"), usd_order("O-2")])
    with pytest.raises(api.CurrencyMismatch):
        report.currency
    with pytest.raises(api.CurrencyMismatch):
        report.gross


def test_a_stated_report_currency_that_contradicts_its_orders_raises():
    """Same rule as the cart's: the summands win, and the caller is told.

    Worth its own test because a report resolves its currency from a
    different place than a cart does — its orders, not its lines — so an
    implementation could easily honor the statement in one class and not the
    other.
    """
    report = api.RevenueReport([eur_order()], currency="USD")
    with pytest.raises(api.CurrencyMismatch):
        report.currency
    with pytest.raises(api.CurrencyMismatch):
        report.gross


def test_a_cancelled_order_does_not_vote_on_the_reports_currency():
    """It contributes to no figure, so it cannot veto one.

    A cancelled order is excluded at construction from everything this class
    reports. Letting it decide the report's currency — or force a mismatch —
    would let an order that contributes nothing reject a report it is not
    part of.
    """
    stray = usd_order("O-3")
    stray.cancel()
    report = api.RevenueReport([eur_order("O-1"), stray])
    assert report.currency == "EUR"
    assert report.gross == api.Money("250.00", "EUR")


# -- What this Task leaves alone --------------------------------------------


def test_a_coupon_minimum_still_gates_in_its_own_currency():
    """Unchanged behavior, pinned through the new seeding function.

    `Coupon.minimum_subtotal` still defaults to a USD `Money.ZERO` that gates
    nothing — `_minimum_rule` builds no rule for a zero minimum at all, so
    the constant's currency never gets compared to anything. A real minimum
    still gates in its own currency, including the fallback for a context
    carrying no `subtotal` key at all, which has to be a zero in the
    *minimum's* currency or a missing context key would surface as a currency
    bug. Written with `seeded_from` so the test is an invariant of this Task
    rather than of the base tree, which would otherwise pass it unchanged.
    """
    free = api.Coupon("FREE", "fixed", api.Money("10.00", "EUR"))
    assert free.minimum_subtotal == api.Money.ZERO
    assert free.to_discount().rule is None
    gated = api.Coupon(
        "SAVE10", "fixed", api.Money("10.00", "EUR"),
        minimum_subtotal=api.Money("50.00", "EUR"),
    )
    discount = gated.to_discount()
    assert discount.rule is not None
    sixty = api.Money("60.00", "EUR")
    assert discount.apply(sixty, {"subtotal": sixty}) == api.Money("10.00", "EUR")
    forty = api.Money("40.00", "EUR")
    assert discount.apply(forty, {"subtotal": forty}) == api.Money.seeded_from("EUR")
    assert discount.apply(sixty, {}) == api.Money.seeded_from(sixty)


def test_no_collection_consults_the_money_zero_constant_any_more():
    """`Money.ZERO` stays, stays allowlisted, and stops being read.

    The constant is the structure gate's business and not this Task's to
    move, so it is still here and still a USD zero. What changed is that no
    collection reads it: replacing it with an amount no figure could
    legitimately equal leaves every figure below intact, including the
    default an empty report with no currency at all still answers — that
    default names `DEFAULT_CURRENCY` now rather than inheriting a constant.
    On the base tree the empty cart and the empty report both hand the
    sentinel straight back, which is what makes this assertion a measurement
    rather than a restatement.
    """
    assert api.Money.ZERO == api.Money("0.00", "USD")
    sentinel = api.Money("999.99", "XXX")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(api.Money, "ZERO", sentinel)
        assert api.Cart(eur_catalog(), customer()).subtotal == api.Money("0.00", "EUR")
        assert api.RevenueReport([], currency="EUR").gross == api.Money("0.00", "EUR")
        assert api.RevenueReport([]).gross == api.Money("0.00", "USD")
        assert api.Money.seeded_from("USD") == api.Money("0.00", "USD")
    assert api.Money.ZERO == api.Money("0.00", "USD")
