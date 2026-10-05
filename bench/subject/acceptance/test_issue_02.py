"""M2 — Tax-inclusive rates through the pipeline.

`TaxRate.inclusive` in `core/tax.py`, honoured by `_raw_tax`, carried through
`TaxTable._parse_row`, read back by `TaxTable.inclusive_for` and
`TaxCalculator.is_inclusive`, split by `TaxCalculator.split_inclusive`, then
threaded up into `Order.taxable_base`/`Order.tax_total`/
`Order.gross_inclusive` and summed by `RevenueReport.gross_inclusive_total`.

The discriminating arithmetic throughout is that an inclusive rate's tax is a
gross-side remainder and not a multiplication. At 23% on 119.99 those are
22.44 and 27.60, so an implementation that stores the flag and then
multiplies anyway fails here rather than coinciding. The prices (119.99,
49.95) and the additive rate (8.5%) are not round for the same reason: an
implementation that ignores the amount, the category or the rate lands on a
different figure instead of the same one.
"""

from decimal import Decimal

import pytest

from billing import api

# Portugal quotes shelf prices VAT-inclusive at 23%, and bills a fitting
# service ex-tax at 8.5% on top. One jurisdiction, two quoting conventions,
# separated by category — which is what makes a single order mixed, since an
# order reads exactly one jurisdiction off its customer.
PT_INCLUSIVE = "23"
PT_SERVICES_ADDITIVE = "8.5"


def table():
    registered = api.TaxTable()
    registered.add(api.TaxRate("PT", PT_INCLUSIVE, inclusive=True))
    registered.add(api.TaxRate("PT", PT_SERVICES_ADDITIVE, category="services"))
    return registered


def calculator():
    return api.TaxCalculator(table())


def build_order(order_id="O-1", discounts=(), exempt=False, with_gift_card=True):
    """A mixed order in PT: an inclusive-quoted desk, an additive-quoted
    fitting service, and (by default) a non-taxable gift card.

    Three lines rather than two so "what counts as an inclusive line" has
    something to exclude in both directions: the fitting is taxable but
    additive, the gift card sits in the inclusive-quoted default rate's
    scope but is not taxable at all.
    """
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-DESK", "Desk", api.Money("119.99"), "furniture"))
    catalog.add(api.Product("SKU-FIT", "Fitting", api.Money("49.95"), "services"))
    catalog.add(
        api.Product("SKU-GIFT", "Gift card", api.Money("25.00"), "vouchers", taxable=False)
    )
    customer = api.Customer("C-1", "Ada", "PT", tax_exempt=exempt)
    cart = api.Cart(catalog, customer)
    cart.add("SKU-DESK")
    cart.add("SKU-FIT")
    if with_gift_card:
        cart.add("SKU-GIFT")
    pricing = api.PricingContext(
        discount_engine=api.DiscountEngine(list(discounts)),
        tax_calculator=api.TaxCalculator(table()),
    )
    return api.Order(order_id, cart, pricing)


def test_a_rate_is_additive_unless_it_says_so_and_the_flag_is_read_back():
    """The flag defaults False and `inclusive_for` is what reads it.

    Asserted on the reader, not on the field alone: a rate that recorded
    `inclusive` and exposed nothing would be indistinguishable from one that
    validated the argument and threw it away, which is the failure this
    assertion exists to kill.
    """
    assert api.TaxRate("US-CA", "8.25").inclusive is False
    assert api.TaxRate("PT", PT_INCLUSIVE, inclusive=True).inclusive is True
    registered = api.TaxTable()
    registered.add(api.TaxRate("US-CA", "8.25"))
    registered.add(api.TaxRate("PT", PT_INCLUSIVE, inclusive=True))
    assert registered.inclusive_for("US-CA", "furniture") is False
    assert registered.inclusive_for("PT", "furniture") is True
    # And the percentage still resolves by the category-then-default rule,
    # so storing the flag did not disturb what the table answers for a rate.
    assert registered.rate_for("PT", "furniture") == Decimal(PT_INCLUSIVE)


def test_an_additive_rates_figures_are_exactly_what_they_were():
    """Pinned, because the whole Task rests on this not moving.

    Every figure here is computed by the base tree with no flag in sight:
    a plain rate, a zero-rated category beating the jurisdiction default, a
    compound jurisdiction summing two scopes, and a non-taxable line owing
    nothing. An implementation that routed the additive path through the
    inclusive remainder would change all four.
    """
    registered = api.TaxTable()
    registered.add(api.TaxRate("US-CA", "7.25"))
    registered.add(api.TaxRate("US-CA", "0", category="groceries"))
    registered.add(api.TaxRate("US-CA-SF", "1.25"))
    calc = api.TaxCalculator(registered)
    amount = api.Money("119.99")
    # 119.99 * 7.25% = 8.699275, bankers to 8.70.
    assert calc.tax_for(amount, "US-CA", "furniture") == api.Money("8.70")
    assert calc.tax_for(amount, "US-CA", "groceries") == api.Money("0.00")
    # 7.25 + 1.25 = 8.50%, and 119.99 * 8.5% = 10.19915, bankers to 10.20.
    assert calc.tax_for(amount, ("US-CA", "US-CA-SF"), "furniture") == api.Money("10.20")
    assert calc.tax_for(amount, "US-CA", "furniture", taxable=False) == api.Money("0.00")


def test_an_inclusive_rates_tax_is_the_gross_side_remainder_not_a_multiplication():
    """The amount is the gross, so the tax is already inside it.

    119.99 at 23% inclusive contains 22.44 of tax: 119.99 / 1.23 is
    97.5528..., and 119.99 - 97.55 is 22.44. Multiplying instead gives
    119.99 * 0.23 = 27.5977 -> 27.60, which is tax on the tax. The second
    assertion is what kills an implementation that stores the flag and then
    multiplies anyway.
    """
    calc = calculator()
    assert calc.tax_for(api.Money("119.99"), "PT", "furniture") == api.Money("22.44")
    assert calc.tax_for(api.Money("119.99"), "PT", "furniture") != api.Money("27.60")
    # The additive category of the same jurisdiction is untouched by it:
    # 49.95 * 8.5% = 4.24575, bankers to 4.25.
    assert calc.tax_for(api.Money("49.95"), "PT", "services") == api.Money("4.25")


def test_split_inclusive_conserves_the_gross_exactly():
    """`net + tax == amount`, and the tax is `tax_for`'s own figure.

    The tax is rounded and the net is the remainder of an exact `Money`
    subtraction, so conservation is a construction rather than a property of
    the inputs. MEASURED, so the claim is not overstated: at 23% the other
    plausible construction — rounding the net half up and taking the tax as
    the remainder, which is what `net_from_gross` does — conserves too and
    agrees to the cent on every amount from 0.01 to 2000.00, so this
    assertion does not discriminate between the two and is not offered as if
    it did. The second assertion is the one that bites: the split's tax is
    the same figure `tax_for` reports for that amount, so an implementation
    whose two readers disagree about one line's tax fails here.
    """
    calc = calculator()
    assert calc.split_inclusive(api.Money("119.99"), "PT", "furniture") == (
        api.Money("97.55"),
        api.Money("22.44"),
    )
    for cents in ("0.01", "0.07", "12.34", "119.99", "999.99", "1234.56"):
        amount = api.Money(cents)
        net, tax = calc.split_inclusive(amount, "PT", "furniture")
        assert net + tax == amount
        assert tax == calc.tax_for(amount, "PT", "furniture")


def test_split_inclusive_refuses_an_additive_rate_rather_than_guessing():
    """An additive quote is a different computation, asked for by name.

    `net_from_gross` is that name and it still answers for the same rate,
    which is what makes the refusal a routing decision rather than a missing
    feature: 119.99 backed out of an 8.5% additive quote is 110.59 net and
    9.40 tax.
    """
    calc = calculator()
    with pytest.raises(ValueError):
        calc.split_inclusive(api.Money("119.99"), "PT", "services")
    assert calc.net_from_gross(api.Money("119.99"), "PT", "services") == (
        api.Money("110.59"),
        api.Money("9.40"),
    )


def test_net_from_gross_keeps_its_own_meaning_under_an_inclusive_rate():
    """The caller names the rate; the flag does not redirect the method.

    `net_from_gross` divides by `1 + rate/100` and rounds the net half up,
    whatever the registered rate says about how it is quoted. Both ways of
    reaching a net stay available and neither became an alias for the other:
    the additive rate above is one `split_inclusive` refuses, and this
    inclusive rate is one `net_from_gross` still answers for.
    """
    calc = calculator()
    assert calc.net_from_gross(api.Money("119.99"), "PT", "furniture") == (
        api.Money("97.55"),
        api.Money("22.44"),
    )
    # Exemption is still answered before the table is consulted, so the flag
    # did not move that check either.
    assert calc.net_from_gross(api.Money("119.99"), "PT", "furniture", taxable=False) == (
        api.Money("119.99"),
        api.Money("0.00"),
    )


def test_a_loaded_mapping_row_carries_inclusive_and_omitting_rows_default_false():
    """The keyword of a mapping row, and the False default when omitted.

    The carry is asserted through `inclusive_for` *and* through the tax the
    rate then produces, because a parser that accepted the keyword and
    dropped it would satisfy a lookup that only read the percentage back.
    The rows here that carry no `inclusive` are a positional and a mapping
    shape, so the default is exercised on both; only the mapping shape
    carries the flag, a positional row keeping its 2 or 3 elements.
    """
    registered = api.TaxTable()
    registered.load(
        [
            {"jurisdiction": "PT", "rate": PT_INCLUSIVE, "inclusive": True},
            {"jurisdiction": "IE", "rate": "13.5", "inclusive": True},
            ("US-CA", "8.25", "furniture"),
            {"jurisdiction": "US-OR", "rate": "0"},
        ]
    )
    assert registered.inclusive_for("PT", "furniture") is True
    assert registered.inclusive_for("IE", "furniture") is True
    # The rows that omit it default False, which is what keeps every row
    # the base tree could load meaning what it meant.
    assert registered.inclusive_for("US-CA", "furniture") is False
    assert registered.inclusive_for("US-OR", "furniture") is False
    calc = api.TaxCalculator(registered)
    assert calc.tax_for(api.Money("119.99"), "PT", "furniture") == api.Money("22.44")
    # 119.99 * 8.25% = 9.899175, bankers to 9.90 — the additive figure, from
    # a row that omitted the keyword rather than carrying False.
    assert calc.tax_for(api.Money("119.99"), "US-CA", "furniture") == api.Money("9.90")


def test_an_inclusive_that_is_not_a_bool_is_refused():
    """`"yes"` and `1` are both truthy and neither is a bool.

    The same reasoning `taxable` already gets: a string that happens to work
    today is a coincidence, and one row of a loaded table carrying it would
    change a tax figure with nothing else in the system reading differently.
    The refusal lives in `TaxRate` itself, so it fires from a mapping row's
    keyword exactly as it does from the constructor. A five-element row is a
    malformed row, not a row with an extra flag.
    """
    registered = api.TaxTable()
    with pytest.raises(TypeError):
        registered.load([{"jurisdiction": "PT", "rate": PT_INCLUSIVE, "inclusive": "yes"}])
    with pytest.raises(TypeError):
        registered.load([{"jurisdiction": "PT", "rate": PT_INCLUSIVE, "inclusive": 1}])
    with pytest.raises(TypeError):
        api.TaxRate("PT", PT_INCLUSIVE, inclusive="yes")
    with pytest.raises(ValueError):
        registered.load([("PT", PT_INCLUSIVE, None, True, "extra")])


def test_a_compound_jurisdiction_must_agree_on_inclusive_before_it_sums():
    """Summing an inclusive rate with an additive one answers for neither.

    Part of the result would already be inside the amount and part of it
    would not, and no single percentage expresses that, so the disagreement
    is refused at every reader rather than resolved by picking a side.
    Scopes that agree still sum: 23 + 2.5 = 25.5% inclusive on 119.99
    contains 24.38, where multiplying would have said 30.60.
    """
    mixed = api.TaxTable()
    mixed.add(api.TaxRate("PT", PT_INCLUSIVE, inclusive=True))
    mixed.add(api.TaxRate("US-CA", "8.25"))
    with pytest.raises(ValueError):
        mixed.rate_for(("PT", "US-CA"), None)
    with pytest.raises(ValueError):
        mixed.inclusive_for(("PT", "US-CA"), None)
    with pytest.raises(ValueError):
        api.TaxCalculator(mixed).tax_for(api.Money("119.99"), ("PT", "US-CA"), None)

    agreeing = api.TaxTable()
    agreeing.add(api.TaxRate("PT", PT_INCLUSIVE, inclusive=True))
    agreeing.add(api.TaxRate("PT-LIS", "2.5", inclusive=True))
    assert agreeing.rate_for(("PT", "PT-LIS"), None) == Decimal("25.5")
    assert agreeing.inclusive_for(("PT", "PT-LIS"), None) is True
    stacked = api.TaxCalculator(agreeing).tax_for(api.Money("119.99"), ("PT", "PT-LIS"), None)
    assert stacked == api.Money("24.38")
    assert stacked != api.Money("30.60")


def test_an_unregistered_jurisdiction_raises_rather_than_reading_as_additive():
    """A missing rate is a missing configuration, not an additive one.

    `inclusive_for` answering False for a jurisdiction nobody registered
    would report the absence as a quoting convention, and the amount would
    then be taxed on top of a price that may well have contained the tax
    already. A missing scope of a compound jurisdiction raises for the same
    reason it does when only the percentage is wanted.
    """
    registered = api.TaxTable()
    registered.add(api.TaxRate("PT", PT_INCLUSIVE, inclusive=True))
    with pytest.raises(api.UnknownJurisdiction):
        registered.inclusive_for("US-ZZ", "furniture")
    with pytest.raises(api.UnknownJurisdiction):
        registered.inclusive_for(("PT", "US-ZZ"), "furniture")


def test_a_non_taxable_line_is_not_inclusive_quoted_and_consults_no_rate():
    """Exemption is answered before the table, here as everywhere else.

    An exempt line's price contains no tax to back out, so there is nothing
    for a quoting convention to say about it — and an unregistered
    jurisdiction must not turn an exempt line into an error in this reader
    any more than it does in `tax_for`. The empty table is what makes that
    falsifiable.
    """
    calc = api.TaxCalculator(api.TaxTable())
    assert calc.is_inclusive("US-ZZ", "furniture", False) is False
    inclusive_everywhere = calculator()
    assert inclusive_everywhere.is_inclusive("PT", "furniture") is True
    assert inclusive_everywhere.is_inclusive("PT", "furniture", False) is False
    assert inclusive_everywhere.is_inclusive("PT", "services") is False


def test_tax_total_on_an_all_inclusive_order_does_not_add_tax_on_top():
    """The order's tax is the tax inside its prices, not a second charge.

    One inclusive line of 119.99 owes the 22.44 it already contains. An
    implementation that kept multiplying would report 27.60, and one that
    ignored the flag in the pipeline while honouring it in `tax_for` would
    report 27.60 here and 22.44 there.
    """
    order = build_order(with_gift_card=False)
    order.cancel_line("SKU-FIT")
    assert order.subtotal == api.Money("119.99")
    assert order.tax_total == api.Money("22.44")
    assert order.tax_total != api.Money("27.60")
    assert order.gross_inclusive == api.Money("119.99")


def test_taxable_base_excludes_an_inclusive_lines_tax():
    """A base that had already been taxed is not a base.

    The desk's 119.99 contributes its net 97.55; the fitting's 49.95 is
    additive and contributes unchanged, which is how the same assertion
    pins both directions. 97.55 + 49.95 = 147.50, and an implementation
    that summed the shares as before would say 169.94.
    """
    order = build_order()
    assert order.subtotal == api.Money("194.94")    # 119.99 + 49.95 + 25.00
    assert order.taxable_base == api.Money("147.50")
    assert order.taxable_base != api.Money("169.94")
    # The gift card is excluded because it is not taxable, exactly as before.
    assert order.taxable_base + api.Money("25.00") + api.Money("22.44") == order.subtotal


def test_a_mixed_order_reports_each_line_under_its_own_convention():
    """One order, one jurisdiction, two quoting conventions by category.

    22.44 contained in the desk plus 4.25 charged on the fitting is 26.69,
    and `gross_inclusive` names the 119.99 the first of those was quoted
    inside. `total` keeps its formula — subtotal - discount + tax — which
    this asserts because the change to `taxable_base` must not leak into it.
    """
    order = build_order()
    assert order.tax_total == api.Money("26.69")        # 22.44 + 4.25
    assert order.gross_inclusive == api.Money("119.99")
    assert order.total == api.Money("221.63")           # 194.94 - 0.00 + 26.69
    assert order.total == order.subtotal - order.discount_total + order.tax_total
    assert order.breakdown()["tax"] == api.Money("26.69")
    assert order.breakdown()["total"] == order.total


def test_the_discount_is_allocated_before_an_inclusive_line_is_split():
    """The split runs on the discounted share, not on the catalog price.

    Under 50% off, the desk's share is 59.99 and contains 11.22 of tax
    (59.99 / 1.23 = 48.7723...), while the fitting's 24.98 share is charged
    2.12 on top. An implementation that split the undiscounted line total
    would report the desk's tax as 22.44 and the order's as 24.56.
    """
    order = build_order(discounts=[api.PercentageDiscount("half", "50")])
    assert order.discount_total == api.Money("97.47")   # half of 194.94
    assert order.tax_total == api.Money("13.34")        # 11.22 + 2.12
    assert order.tax_total != api.Money("24.56")
    assert order.taxable_base == api.Money("73.75")     # 48.77 + 24.98
    assert order.gross_inclusive == api.Money("59.99")  # the desk's share


def test_gross_inclusive_is_zero_in_the_orders_own_currency():
    """Zero, and not `Money.ZERO`'s US dollars.

    An order priced in euros reporting a USD zero is a `CurrencyMismatch`
    waiting one addition downstream, which is why the assertion is on the
    currency and not only on the amount.
    """
    registered = api.TaxTable()
    registered.add(api.TaxRate("DE", "19"))
    catalog = api.Catalog()
    catalog.add(api.Product("SKU-LAMP", "Lamp", api.Money("39.95", "EUR"), "lighting"))
    cart = api.Cart(catalog, api.Customer("C-2", "Bo", "DE"))
    cart.add("SKU-LAMP", 3)
    order = api.Order(
        "O-2",
        cart,
        api.PricingContext(
            discount_engine=api.DiscountEngine([]),
            tax_calculator=api.TaxCalculator(registered),
        ),
    )
    assert order.subtotal == api.Money("119.85", "EUR")  # 39.95 * 3
    assert order.gross_inclusive == api.Money("0.00", "EUR")
    assert order.gross_inclusive.currency == "EUR"
    assert order.gross_inclusive != api.Money.ZERO
    # An all-additive order is otherwise untouched: 119.85 * 19% = 22.7715.
    assert order.tax_total == api.Money("22.77", "EUR")
    assert order.taxable_base == api.Money("119.85", "EUR")


def test_a_non_taxable_line_is_not_part_of_the_inclusive_portion():
    """What counts as one inclusive line is decided line by line.

    The gift card sits squarely inside the inclusive default rate's scope
    and contributes nothing, because no tax was included in its price. An
    implementation that counted every line in an inclusive jurisdiction
    would report 144.99 rather than 119.99.
    """
    order = build_order()
    assert order.gross_inclusive == api.Money("119.99")
    assert order.gross_inclusive != api.Money("144.99")   # + the 25.00 card
    assert order.gross_inclusive != api.Money("169.94")   # + the fitting too


def test_a_tax_exempt_customer_has_no_inclusive_portion():
    """Exemption is answered once, and this figure inherits it.

    A customer who owes no tax has no tax contained in anything, so the
    inclusive-quoted portion of the order is zero and so is the base — both
    through `_lines_for_tax`, the same single place every other figure here
    reads exemption from.
    """
    order = build_order(exempt=True)
    assert order.tax_total == api.Money("0.00")
    assert order.gross_inclusive == api.Money("0.00")
    assert order.taxable_base == api.Money("0.00")
    assert order.total == api.Money("194.94")


def test_a_cancelled_line_leaves_the_inclusive_portion_with_it():
    """A cancelled line is gone from this figure as from every other.

    Cancelling the desk removes the only inclusive-quoted line, so the
    portion drops to zero while the additive fitting keeps owing its 4.25.
    """
    order = build_order()
    order.place()
    order.cancel_line("SKU-DESK")
    assert order.gross_inclusive == api.Money("0.00")
    assert order.tax_total == api.Money("4.25")
    assert order.taxable_base == api.Money("49.95")


def test_the_report_sums_the_inclusive_portion_and_excludes_a_cancelled_order():
    """Summed off the orders, not recomputed from their lines.

    Two live orders of 119.99 inclusive-quoted each is 239.98; the third
    order is cancelled and contributes nothing, exactly as it contributes
    nothing to `tax_collected`. An empty report answers `Money.ZERO` like
    every other whole-order aggregate in that module.
    """
    live_one = build_order("O-1", with_gift_card=False)
    live_one.place()
    live_two = build_order("O-2", with_gift_card=False)
    live_two.place()
    cancelled = build_order("O-3", with_gift_card=False)
    cancelled.place()
    cancelled.cancel()
    report = api.RevenueReport([live_one, live_two, cancelled])
    assert report.gross_inclusive_total() == api.Money("239.98")   # 119.99 * 2
    assert report.tax_collected == api.Money("53.38")              # 26.69 * 2
    assert api.RevenueReport([]).gross_inclusive_total() == api.Money.ZERO


def test_tax_by_jurisdiction_groups_an_inclusive_lines_tax_like_any_other():
    """The grouping does not learn about the flag, and must not have to.

    `tax_by_jurisdiction` sums `order.tax_total` by the customer's own
    jurisdiction, so an inclusive line's contained tax lands under the same
    key an additive line's would — one entry for PT carrying both, and the
    report's total agreeing with `tax_collected` rather than being a second
    derivation of it.
    """
    order = build_order("O-1")
    order.place()
    report = api.RevenueReport([order])
    assert report.tax_by_jurisdiction() == {"PT": api.Money("26.69")}
    assert sum(
        (amount for amount in report.tax_by_jurisdiction().values()),
        api.Money("0.00"),
    ) == report.tax_collected


def test_net_and_tax_is_the_pipeline_primitive_and_charges_by_default():
    """The one call `Order` reads both figures from.

    Its `taxable` defaults to True, like `tax_for`'s. A default of False
    would make every line the pipeline never flags exempt come back
    untaxed, and the order totals would still be self-consistent — which is
    why this is asserted on the primitive and not only on the order.

    The two quoting conventions are the discrimination: for the additive
    fitting the net *is* the amount and the tax is on top, for the
    inclusive desk the amount is the gross and the net is what remains.
    """
    calc = calculator()
    net, tax = calc.net_and_tax(api.Money("49.95"), "PT", "services")
    assert net == api.Money("49.95")
    assert tax == api.Money("4.25")
    net, tax = calc.net_and_tax(api.Money("119.99"), "PT", "furniture")
    assert net == api.Money("97.55")
    assert tax == api.Money("22.44")
    assert net + tax == api.Money("119.99")


def test_net_and_tax_on_a_non_taxable_line_charges_nothing_either_way():
    """An exempt line owes nothing and keeps its whole amount as the net,
    whichever convention its jurisdiction quotes in — so the inclusive
    branch must not run for it and back a tax out of a price that contains
    none.
    """
    calc = calculator()
    assert calc.net_and_tax(api.Money("119.99"), "PT", "furniture",
                            False) == (api.Money("119.99"), api.Money.ZERO)
    assert calc.net_and_tax(api.Money("49.95"), "PT", "services",
                            False) == (api.Money("49.95"), api.Money.ZERO)
