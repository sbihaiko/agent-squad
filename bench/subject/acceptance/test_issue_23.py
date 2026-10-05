"""K5 — Tax follows the shipment.

`Shipment.destination`, `Shipment.destination_scope`, `Order.dispatch`'s
fourth argument, `Order.tax_total`'s new per-line scope and
`Order.tax_by_shipment` in `sales/orders.py`; `core.tax.taxing_scope` and
`TaxCalculator.tax_for_shipment` in `core/tax.py`;
`RevenueReport.tax_by_destination` in `reporting/reports.py`.

K5 is the middle link of Chain B. It is cut from K4's Reference
Implementation, calls `Shipment` and `Order.shipments()`, and K6 is
authored against `destination_scope` and `tax_by_shipment` — so both are
pinned here by name as well as by behaviour.

Three things shape every fixture below.

First, **the no-destination path must not move at all**. The shared order is
K4's: three lines at 200.00, 70.00 and 50.00 under a 10% discount, one line
tax-exempt, giving 320.00 / 32.00 / 24.30 / 312.30 with allocated taxable
shares of 180.00 and 63.00. Those four figures are pinned again here
whenever no destination is named, because "tax follows the shipment" is a
change to one code path and an implementation that reached the customer's
jurisdiction through the new machinery and got a different answer would be
a regression K4's own suite would also catch — but only after this one had
passed, which is the wrong order to find out.

Second, **the destination rate is deliberately different in both
directions**: US-CA at 10%, US-NY at 5%, NY-NYC at 1%. A destination rate
equal to the customer's would make "taxed where it went" and "taxed where
the customer is" the same number, and half the assertions here would hold
for an implementation that ignored the destination entirely.

Third, **rounding is asserted where it drifts**, not only where it
reconciles. `tax_total` rounds per line and `tax_by_shipment` rounds per
shipment, so the two are the same figure only up to a minor unit. A suite
that asserted the reconciliation on clean tenths alone would pass an
implementation that rounded per line in both places — and that
implementation would be reporting a destination authority a figure nobody
owes. One fixture therefore prices two lines whose per-line tax is exactly
a half-cent, where the two policies differ by one cent, and pins both.

Every test here fails on `wip/k4-v5`: each reaches `destination`,
`destination_scope`, `dispatch`'s fourth argument, `tax_by_shipment`,
`tax_for_shipment` or `tax_by_destination`, none of which K4's tree
defines, and the `pytest.raises` assertions name exceptions that a
`TypeError` about an unexpected argument does not satisfy.
"""

import pytest

from billing import api

# Opaque date strings, exactly as in K4: the package still reads no clock
# and still compares no dates. K6 is where dates acquire arithmetic.
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


def build_cart(catalog=None, customer=None):
    catalog = catalog or build_catalog()
    cart = api.Cart(catalog, customer or api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-1")
    cart.add("SKU-2", 2)
    cart.add("SKU-3")
    return cart


def build_table():
    """US-CA 10%, US-NY 5%, NY-NYC 1%, and no US-TX at all.

    The three rates are all different, so a figure computed at the wrong
    one of them is a different number rather than a coincidence, and the
    missing jurisdiction is what the unregistered-destination refusal is
    asserted against.
    """
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


#: K4's figures, which the no-destination path owes unchanged.
PINNED_BREAKDOWN = {
    "subtotal": api.Money("320.00"),
    "discount": api.Money("32.00"),
    "tax": api.Money("24.30"),
    "total": api.Money("312.30"),
}


def test_a_shipment_carries_an_optional_destination_and_keeps_k4s_constructor():
    """The fourth field is defaulted, not required and not keyword-only.

    K4's suite constructs a `Shipment` and calls `dispatch` with three
    positional arguments throughout, and that has to go on working: a
    required destination would break every existing caller, and a
    keyword-only one would buy nothing a default does not. So all three
    spellings are pinned — absent, positional, keyword — and a shipment
    with no destination reports `destination_scope` as `None` rather than
    guessing a jurisdiction it cannot know.
    """
    plain = api.Shipment("S-1", ["SKU-1"], DAY_ONE)
    assert plain.destination is None
    assert plain.destination_scope is None
    positional = api.Shipment("S-2", ["SKU-2"], DAY_ONE, "US-NY")
    assert positional.destination == "US-NY"
    assert positional.destination_scope == "US-NY"
    keyword = api.Shipment("S-3", ["SKU-3"], DAY_ONE, destination="NY-NYC")
    assert keyword.destination == "NY-NYC"
    # Still frozen: a record of something that already happened cannot be
    # relabelled with a different destination after the fact.
    with pytest.raises(AttributeError):
        keyword.destination = "US-NY"
    order = placed_order()
    assert order.dispatch("S-1", ["SKU-1"], DAY_ONE).destination is None
    assert order.dispatch("S-2", ["SKU-2"], DAY_ONE, "US-NY").destination == "US-NY"
    assert (
        order.dispatch("S-3", ["SKU-3"], DAY_TWO, destination="NY-NYC").destination
        == "NY-NYC"
    )


def test_a_destination_is_validated_at_construction_not_at_first_pricing():
    """Matching `Product` and matching `skus`: a shipment that could never
    be priced must not be recordable.

    An empty string is refused rather than read as "no destination", since
    `None` already means that and a falsy-string fall-back would make a
    data-entry slip indistinguishable from a deliberate omission. A scope
    named twice is refused here rather than by `rate_for` later, because
    `rate_for` would only raise once somebody priced the order.
    """
    with pytest.raises(ValueError):
        api.Shipment("S-1", ["SKU-1"], DAY_ONE, "")
    with pytest.raises(ValueError):
        api.Shipment("S-1", ["SKU-1"], DAY_ONE, [])
    with pytest.raises(ValueError):
        api.Shipment("S-1", ["SKU-1"], DAY_ONE, ["US-NY", "US-NY"])
    with pytest.raises(TypeError):
        api.Shipment("S-1", ["SKU-1"], DAY_ONE, 94110)
    with pytest.raises(TypeError):
        api.Shipment("S-1", ["SKU-1"], DAY_ONE, ["US-NY", 94110])
    # And `dispatch` records nothing when the destination is the problem.
    order = placed_order()
    with pytest.raises(ValueError):
        order.dispatch("S-1", ["SKU-1"], DAY_ONE, "")
    assert order.shipments() == ()


def test_a_compound_destination_of_one_scope_resolves_to_that_bare_scope():
    """`["US-NY"]` and `"US-NY"` are the same place and must group as one.

    A compound destination is normalized to a tuple, like `skus`, so a
    frozen record never holds a caller's list. Two or more scopes stay a
    tuple, because that is a genuinely compound place whose rates stack;
    exactly one collapses, because otherwise a caller who wrapped a string
    in a list would split one destination into two report rows with no
    cause a reader could find.
    """
    single = api.Shipment("S-1", ["SKU-1"], DAY_ONE, ["US-NY"])
    assert single.destination == ("US-NY",)
    assert single.destination_scope == "US-NY"
    compound = api.Shipment("S-2", ["SKU-2"], DAY_ONE, ["US-NY", "NY-NYC"])
    assert compound.destination == ("US-NY", "NY-NYC")
    assert compound.destination_scope == ("US-NY", "NY-NYC")
    assert isinstance(compound.destination, tuple)


def test_a_shipment_with_no_destination_leaves_every_k4_figure_identical():
    """The whole no-destination path is unchanged — asserted against the
    figures K4's own suite pins, not against a fresh computation.

    A 10% rate over an allocated discount and one tax-exempt line makes
    every one of the four figures move if the new scope machinery reached a
    different jurisdiction, and `tax_by_shipment()` is read in the same
    test so this cannot pass on a tree that has no destinations at all.
    """
    order = placed_order()
    report = api.RevenueReport([order])
    order.dispatch("S-1", ["SKU-1", "SKU-2"], DAY_ONE)
    order.dispatch("S-2", ["SKU-3"], DAY_TWO)
    assert order.breakdown() == PINNED_BREAKDOWN
    assert report.tax_by_jurisdiction() == {"US-CA": api.Money("24.30")}
    assert order.tax_by_shipment() == {
        "S-1": api.Money("24.30"),
        "S-2": api.Money("0.00"),
    }


def test_a_shipment_with_a_destination_taxes_its_lines_at_that_rate():
    """The rate of the place the goods went to, not of the customer.

    SKU-1's allocated taxable share is 180.00, so US-NY's 5% is 9.00 where
    US-CA's 10% would be 18.00 — and the order's own tax drops from 24.30
    to 15.30 and its total from 312.30 to 303.30. `subtotal` and
    `discount_total` are pinned unchanged: a destination is a tax fact and
    must not reach the pre-tax pipeline.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-NY")
    assert order.tax_by_shipment() == {
        "S-1": api.Money("9.00"),
        None: api.Money("6.30"),
    }
    assert order.subtotal == api.Money("320.00")
    assert order.discount_total == api.Money("32.00")
    assert order.tax_total == api.Money("15.30")
    assert order.total == api.Money("303.30")
    assert order.taxable_base == api.Money("243.00")


def test_an_unregistered_destination_raises_rather_than_taxing_at_zero():
    """`UnknownJurisdiction`, which `rate_for` already guarantees, and this
    path must not weaken it into a silent zero.

    The refusal is at pricing, not at dispatch: goods that demonstrably
    left the warehouse must be recordable even when a rate row is missing,
    and the missing row surfaces the moment anyone asks what the order
    owes. So the dispatch succeeds, and all three readings raise — a
    destination-blind `tax_total` would return 24.30 instead.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-TX")
    assert order.shipments()[0].destination_scope == "US-TX"
    with pytest.raises(api.UnknownJurisdiction):
        order.tax_by_shipment()
    with pytest.raises(api.UnknownJurisdiction):
        order.tax_total
    with pytest.raises(api.UnknownJurisdiction):
        order.total
    # A registered destination on the same order prices normally, so the
    # refusal is about the missing rate and not about destinations.
    other = placed_order()
    other.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-NY")
    assert other.tax_total == api.Money("15.30")


def test_tax_by_shipment_is_keyed_by_shipment_id_in_dispatch_order():
    """Dispatch order, matching `shipments()` — not sorted by id and not
    sorted by date, which is why the second dispatch below carries the
    earlier date and the lower id.

    The mapping is keyed by `shipment_id` rather than by the `Shipment`
    itself: a report groups these figures and an id is the field a caller
    already holds. K4 refuses a duplicate id per order, which is what makes
    the key unambiguous.
    """
    order = placed_order()
    order.dispatch("S-9", ["SKU-2"], DAY_TWO)
    order.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-NY")
    by_shipment = order.tax_by_shipment()
    assert list(by_shipment) == ["S-9", "S-1", None]
    assert by_shipment["S-9"] == api.Money("6.30")
    assert by_shipment["S-1"] == api.Money("9.00")
    assert by_shipment[None] == api.Money("0.00")


def test_the_per_shipment_figures_sum_to_tax_total_for_a_fully_dispatched_order():
    """The property that makes the breakdown trustworthy.

    Two shipments to two different jurisdictions, every line in one of
    them, so there is no undispatched remainder to carry the difference:
    9.00 owed to US-NY plus 6.30 owed to US-CA is the 15.30 the order
    reports. The `None` key is absent rather than zero, matching
    `undispatched_orders()`, which drops a fully dispatched order entirely.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-NY")
    order.dispatch("S-2", ["SKU-2", "SKU-3"], DAY_TWO)
    by_shipment = order.tax_by_shipment()
    assert set(by_shipment) == {"S-1", "S-2"}
    assert None not in by_shipment
    assert by_shipment == {"S-1": api.Money("9.00"), "S-2": api.Money("6.30")}
    total = api.Money("0.00")
    for amount in by_shipment.values():
        total = total + amount
    assert total == order.tax_total == api.Money("15.30")


def test_undispatched_lines_are_reported_under_the_none_key_rather_than_dropped():
    """So the sum still reconciles while an order is part-shipped.

    `None` rather than a string: a `shipment_id` is a non-empty string, so
    `None` is the one key that cannot collide with a shipment a caller
    named "undispatched". The key is last, after every real shipment, and
    disappears when the last line ships.
    """
    order = placed_order()
    assert order.tax_by_shipment() == {None: api.Money("24.30")}
    order.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-NY")
    part = order.tax_by_shipment()
    assert list(part) == ["S-1", None]
    assert part[None] == api.Money("6.30")
    assert part["S-1"] + part[None] == order.tax_total
    order.dispatch("S-2", ["SKU-2", "SKU-3"], DAY_TWO, destination="US-NY")
    rest = order.tax_by_shipment()
    assert list(rest) == ["S-1", "S-2"]
    # 63.00 of taxable share at US-NY's 5% is 3.15, and the exempt line
    # contributes nothing wherever it goes.
    assert rest["S-2"] == api.Money("3.15")
    assert order.tax_total == api.Money("12.15")


def test_a_tax_exempt_customer_owes_zero_on_every_shipment():
    """Exemption is answered first, as `_lines_for_tax` already answers it.

    Every shipment reports zero in the order's own currency rather than
    being left out, because a shipment that happened is a row in this
    breakdown whatever it owes. And an unregistered destination on an
    exempt order raises nothing at all: with no taxable line there is no
    rate to look up, which is `tax_for`'s own order of operations one level
    up.
    """
    exempt = api.Customer("C-2", "Bo", "US-CA", tax_exempt=True)
    order = placed_order(cart=build_cart(customer=exempt))
    order.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-NY")
    order.dispatch("S-2", ["SKU-2"], DAY_TWO, destination="US-TX")
    by_shipment = order.tax_by_shipment()
    assert list(by_shipment) == ["S-1", "S-2", None]
    assert by_shipment == {
        "S-1": api.Money("0.00"),
        "S-2": api.Money("0.00"),
        None: api.Money("0.00"),
    }
    assert order.tax_total == api.Money("0.00")
    assert order.total == api.Money("288.00")


def test_a_cancelled_line_contributes_nothing_to_any_shipments_tax():
    """A cancelled line is gone from every figure, including this one.

    K4 allows cancelling a line that has already shipped, so a shipment can
    legitimately hold a cancelled sku — and it still appears, at zero,
    because the dispatch happened. Cancelling SKU-1 leaves 70.00 and 50.00
    active, a 12.00 discount allocating to 7.00 and 5.00, so US-CA's 10% on
    the surviving taxable share of 63.00 is 6.30 and the US-NY shipment
    owes nothing at all.
    """
    order = placed_order()
    order.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-NY")
    order.dispatch("S-2", ["SKU-2"], DAY_TWO)
    order.cancel_line("SKU-1")
    by_shipment = order.tax_by_shipment()
    assert list(by_shipment) == ["S-1", "S-2", None]
    assert by_shipment["S-1"] == api.Money("0.00")
    assert by_shipment["S-2"] == api.Money("6.30")
    assert by_shipment[None] == api.Money("0.00")
    assert order.tax_total == api.Money("6.30")
    assert order.subtotal == api.Money("120.00")
    assert order.shipments()[0].skus == ("SKU-1",)


def test_tax_for_shipment_rejects_a_non_money_amount_and_a_non_bool_taxable():
    """Matching `_raw_tax`, which is the function that does the rejecting.

    The non-`Money` amount is pinned both with and without an explicit
    `currency`, because without one the implementation has to read the
    currency off the first amount — and reading it first would report a
    missing attribute instead of naming the real mistake. A non-bool
    `taxable` is refused even when truthy, for the reason `_raw_tax` gives:
    a caller who passes `"yes"` deserves an error rather than a coincidence
    that happens to work.
    """
    calculator = api.TaxCalculator(build_table())
    assert calculator.tax_for_shipment(
        [(api.Money("180.00"), "furniture")], "US-NY"
    ) == api.Money("9.00")
    assert calculator.tax_for_shipment(
        [(api.Money("180.00"), "vouchers", False)], "US-NY"
    ) == api.Money("0.00")
    with pytest.raises(TypeError):
        calculator.tax_for_shipment([("180.00", "furniture")], "US-NY")
    with pytest.raises(TypeError):
        calculator.tax_for_shipment([("180.00", "furniture")], "US-NY",
                                    currency="USD")
    with pytest.raises(TypeError):
        calculator.tax_for_shipment(
            [(api.Money("180.00"), "furniture", "yes")], "US-NY")
    with pytest.raises(ValueError):
        calculator.tax_for_shipment([(api.Money("180.00"),)], "US-NY")
    with pytest.raises(api.UnknownJurisdiction):
        calculator.tax_for_shipment([(api.Money("180.00"), "furniture")], "US-TX")


def test_a_shipment_with_no_taxable_line_needs_its_currency_named():
    """Zero in the stated currency, never `Money.ZERO`'s USD by accident.

    A shipment with no taxable line is a real case, not a degenerate one —
    a tax-exempt customer's every shipment is one — and there is no amount
    to read a currency from, so the caller names it. Refusing to guess is
    the point: a report in EUR must not acquire a USD zero, which compares
    unequal to every EUR figure it would be summed with.
    """
    calculator = api.TaxCalculator(build_table())
    assert calculator.tax_for_shipment([], "US-NY", currency="EUR") == api.Money(
        "0.00", "EUR"
    )
    assert calculator.tax_for_shipment([], "US-TX", currency="USD") == api.Money("0.00")
    with pytest.raises(ValueError):
        calculator.tax_for_shipment([], "US-NY")


def test_rounding_is_bankers_per_shipment_and_tax_total_keeps_its_per_line_rounding():
    """The two policies differ by a cent here, and both figures are pinned.

    Two lines of 10.05 at 10% owe 1.005 each. Rounded per line, bankers
    takes each to 1.00 and `tax_total` is 2.00; accumulated unrounded and
    rounded once, the shipment owes 2.01. `tax_total` is the figure that
    does not move — an order's total must not depend on how the goods were
    packed — and the per-shipment figure is what a destination's authority
    is owed as one payment.

    Without this fixture an implementation that rounded per line inside
    `tax_by_shipment` would pass every other test in this file, and would
    report a destination a figure nobody owes.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-A", "Cable", api.Money("10.05"), "parts"))
    catalog.add(api.Product("SKU-B", "Clip", api.Money("10.05"), "parts"))
    cart = api.Cart(catalog, api.Customer("C-1", "Ada", "US-CA"))
    cart.add("SKU-A")
    cart.add("SKU-B")
    order = placed_order(cart=cart, pricing=build_pricing(discounts=[]))
    assert order.tax_total == api.Money("2.00")
    order.dispatch("S-1", ["SKU-A", "SKU-B"], DAY_ONE)
    assert order.tax_by_shipment() == {"S-1": api.Money("2.01")}
    assert order.tax_total == api.Money("2.00")
    assert order.total == api.Money("22.10")
    # Split across two shipments, each line rounds on its own again, so the
    # breakdown agrees with `tax_total`: the drift is a property of
    # grouping, not a second rate.
    split = placed_order(cart=cart, pricing=build_pricing(discounts=[]))
    split.dispatch("S-1", ["SKU-A"], DAY_ONE)
    split.dispatch("S-2", ["SKU-B"], DAY_TWO)
    assert split.tax_by_shipment() == {
        "S-1": api.Money("1.00"),
        "S-2": api.Money("1.00"),
    }


def test_tax_by_destination_groups_across_orders_sorted_by_destination():
    """Keyed by the authority owed, across every order, sorted by scope.

    Two orders sent to overlapping destinations, handed to the report in
    descending id order so an implementation that grouped per order or
    returned insertion order fails. US-NY collects from both orders and
    appears once, which is the whole point of grouping across them, and a
    wholly cancelled order contributes nothing, excluded at construction
    like every other figure this class reports.
    """
    first = placed_order("O-2")
    first.dispatch("S-1", ["SKU-1"], DAY_ONE, destination="US-NY")
    first.dispatch("S-2", ["SKU-2", "SKU-3"], DAY_TWO)
    second = placed_order("O-1")
    second.dispatch("S-3", ["SKU-1", "SKU-2"], DAY_ONE, destination="US-NY")
    dead = placed_order("O-3")
    dead.dispatch("S-4", ["SKU-1"], DAY_ONE, destination="NY-NYC")
    dead.cancel()
    report = api.RevenueReport([first, second, dead])
    by_destination = report.tax_by_destination()
    assert list(by_destination) == ["US-CA", "US-NY"]
    # US-NY: 9.00 from the first order plus 5% of (180.00 + 63.00) = 12.15
    # from the second, rounded once for that shipment.
    assert by_destination == {
        "US-CA": api.Money("6.30"),
        "US-NY": api.Money("21.15"),
    }


def test_tax_by_destination_without_destinations_agrees_with_tax_by_jurisdiction():
    """The two figures coincide exactly while nothing is sent elsewhere.

    One report, two customers in two jurisdictions, no destination named
    anywhere — including an order that has dispatched nothing at all, whose
    tax lands under its customer's jurisdiction rather than being dropped
    as undispatched. An implementation that keyed the undispatched
    remainder under `None` here would produce a `None` row in a filing
    report.
    """
    californian = placed_order("O-1")
    californian.dispatch("S-1", ["SKU-1", "SKU-2", "SKU-3"], DAY_ONE)
    new_yorker = placed_order(
        "O-2", cart=build_cart(customer=api.Customer("C-2", "Bo", "US-NY"))
    )
    untouched = placed_order("O-3")
    report = api.RevenueReport([californian, new_yorker, untouched])
    assert report.tax_by_destination() == report.tax_by_jurisdiction()
    assert report.tax_by_destination() == {
        "US-CA": api.Money("48.60"),
        "US-NY": api.Money("12.15"),
    }


def test_a_compound_destination_stacks_its_scopes_and_groups_as_one_row():
    """A city rate on top of a state rate, which is what `rate_for`'s
    compound jurisdiction exists for — a shipment to a city is that case.

    6% of the 180.00 taxable share is 10.80, so the compound scope is
    neither US-NY's 5% nor NY-NYC's 1% alone. The compound key sorts
    immediately after the bare scope it starts with, and the one-scope
    compound on the second order groups with plain `"US-NY"` rather than
    opening a row of its own.
    """
    order = placed_order("O-1")
    order.dispatch("S-1", ["SKU-1"], DAY_ONE, destination=["US-NY", "NY-NYC"])
    assert order.tax_by_shipment()["S-1"] == api.Money("10.80")
    assert order.tax_total == api.Money("17.10")
    wrapped = placed_order("O-2")
    wrapped.dispatch("S-2", ["SKU-1"], DAY_ONE, destination=["US-NY"])
    plain = placed_order("O-3")
    plain.dispatch("S-3", ["SKU-1"], DAY_ONE, destination="US-NY")
    report = api.RevenueReport([order, wrapped, plain])
    by_destination = report.tax_by_destination()
    assert list(by_destination) == ["US-CA", "US-NY", ("US-NY", "NY-NYC")]
    assert by_destination["US-NY"] == api.Money("18.00")
    assert by_destination[("US-NY", "NY-NYC")] == api.Money("10.80")
